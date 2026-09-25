"""Replace the active project only after validation; preserve the full old project."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from research_intern.domain.experiments import SliceError
from research_intern.execution.outputs import read_json
from research_intern.workspace.discovery import ProjectImporter
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


def project_identity(root: Path) -> str:
    metadata = child_path(root, 'project.json') if root.exists() else root / 'project.json'
    return hashlib.sha256(metadata.read_bytes()).hexdigest() if metadata.is_file() else ''


def require_settled(root: Path) -> None:
    if child_path(root, 'preparation.pending.json').exists():
        raise SliceError('Finish the interrupted project preparation before choosing another project')
    database = child_path(root, 'ledger.sqlite3')
    if not database.exists():
        return
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        pending = connection.execute("SELECT 1 FROM experiments WHERE state NOT IN ('RECORDED', 'FAILED') LIMIT 1").fetchone()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unfinished = 'preparations' in tables and connection.execute(
            "SELECT 1 FROM preparations WHERE stage NOT IN ('RESERVED', 'RECOVERED') LIMIT 1").fetchone()
    if pending or unfinished:
        raise SliceError('This project has an unfinished experiment or recovery. Resume collection or resolve it before choosing another project')


def resume_switch(workspace: Path) -> bool:
    """Caller owns the active project lock and excludes the driver and job watcher."""
    runtime = child_path(workspace, '.runtime')
    journal = child_path(runtime, 'project-switch.pending.json')
    if not journal.exists():
        return False
    record = read_json(journal)
    if not re.fullmatch(r'[0-9a-f]{32}', record.get('id', '')):
        raise SliceError('Invalid project switch recovery record')
    current = child_path(runtime, 'research-project')
    transfer = child_path(runtime, 'project-switches', record['id'])
    new = child_path(transfer, 'next')
    archived = child_path(runtime, 'project-history', record['id'])
    for key in ('old_entries', 'new_entries'):
        names = record[key]
        if (not isinstance(names, list) or len(set(names)) != len(names)
                or any(not isinstance(n, str) or n in {'.', '..', 'operation.lock'} or '/' in n or '\\' in n for n in names)):
            raise SliceError('Invalid project switch entries')
    if record['phase'] == 'archive':
        for name in record['old_entries']:
            source, target = child_path(current, name), child_path(archived, name)
            if source.exists() and not target.exists():
                source.rename(target)
            elif source.exists() or not target.exists():
                raise SliceError('Project changed during switching. Retained copies need inspection')
        record['phase'] = 'publish'
        atomic_bytes(journal, json.dumps(record).encode())
    if record['phase'] != 'publish':
        raise SliceError('Unknown project switch recovery phase')
    for name in record['new_entries']:
        source, target = child_path(new, name), child_path(current, name)
        if source.exists() and not target.exists():
            source.rename(target)
        elif source.exists() or not target.exists():
            raise SliceError('New project changed during switching. Retained copies need inspection')
    if project_identity(current) != record['new_identity']:
        raise SliceError('The selected project differs from its import record')
    journal.rename(child_path(transfer, 'completed.json'))
    return True


def replace_project(workspace: Path, files, *, archive: bool, expected_project: str) -> None:
    runtime = child_path(workspace, '.runtime')
    root = child_path(runtime, 'research-project')
    with RunLock(root):
        if child_path(runtime, 'project-switch.pending.json').exists():
            raise SliceError('Restart the app to finish the interrupted project switch before uploading again')
        if not expected_project or project_identity(root) != expected_project:
            raise SliceError('The loaded project changed. Refresh the page and choose the folder again')
        require_settled(root)
        # Import completely before changing the active slot. A bad ZIP or an
        # exceeded size limit leaves source, settings and results untouched.
        with tempfile.TemporaryDirectory(dir=runtime, prefix='project-import-') as temporary:
            importer = ProjectImporter(Path(temporary))
            importer.import_files(files, archive=archive)
            revision = uuid.uuid4().hex
            metadata_path = importer.root / 'project.json'
            metadata = read_json(metadata_path)
            metadata['project_id'] = revision
            atomic_bytes(metadata_path, json.dumps(metadata, indent=2).encode())
            previous = f'.runtime/project-history/{revision}'
            initial = {'goal': '', 'target': {}, 'job_config': '', 'existing_run': None,
                       'use_legacy_hints': False, 'previous_project_archive': previous}
            atomic_bytes(importer.root / 'onboarding.json', json.dumps(initial).encode())
            transfer = child_path(runtime, 'project-switches', revision)
            archived = child_path(runtime, 'project-history', revision)
            transfer.mkdir(parents=True)
            archived.mkdir(parents=True)
            next_root = child_path(transfer, 'next')
            # All rename targets are validated inside the application runtime.
            importer.root.rename(next_root)
            record = {'id': revision, 'phase': 'archive',
                      'old_entries': sorted(p.name for p in root.iterdir() if p.name != 'operation.lock'),
                      'new_entries': sorted(p.name for p in next_root.iterdir() if p.name != 'operation.lock'),
                      'new_identity': project_identity(next_root)}
            for name in record['old_entries']:
                child_path(root, name)
            atomic_bytes(runtime / 'project-switch.pending.json', json.dumps(record).encode())
            resume_switch(workspace)
