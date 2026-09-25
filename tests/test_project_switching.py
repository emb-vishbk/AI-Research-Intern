"""Project replacement preserves evidence and never abandons controller work."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from research_intern.api.app import create_app
from research_intern.connections import Connections
from research_intern.controller.mission import MissionControl, MissionBusy
from research_intern.domain.experiments import SliceError
from research_intern.onboarding import Onboarding
from research_intern.workspace.project import SourceFile
from research_intern.workspace.switching import project_identity
from research_intern.workspace.importing import contract_digest, prepared_workspace, prepare_source
from test_execution_slice import WorkspaceTest
from test_onboarding import Cloud, TARGET, source_files


class ProjectSwitchTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.mission = MissionControl(self.root)
        self.connections = Connections(self.root, self.mission)
        self.cloud = Cloud()
        self.service = Onboarding(self.root, self.mission, self.connections, cloud=self.cloud)
        self.addCleanup(self.service.close)
        self.service.upload(source_files())
        self.project = self.service.root
        self.original_id = project_identity(self.project)
        self.service.validate_target(TARGET)
        state = self.service._read()
        state.update(goal='Old project goal', budget={'max_experiments': 3, 'max_gpu_hours': 2, 'max_ai_credits': 100})
        self.service._write(state)

    def replacement(self, *, archive=False):
        files = [SourceFile(f'next-project/{Path(item.path).relative_to("ordinary").as_posix()}', item.stream)
                 for item in source_files({'train.py': b'RATE = 0.3\n'})]
        if not archive:
            return files
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as bundle:
            for item in files:
                bundle.writestr(item.path, item.stream.read())
        stream.seek(0)
        return [SourceFile('next.zip', stream)]

    def switch(self, *, archive=False):
        return self.service.upload(self.replacement(archive=archive), archive=archive,
                                   replace=True, expected_project=self.original_id)

    def test_folder_switch_preserves_project_and_connections_and_clears_choices(self):
        (self.project / 'saved-result.txt').write_text('keep this evidence')
        self.connections._state['azure']['status'] = 'connected'
        self.connections._state['copilot'].update(status='connected', model_locked=True, selected_model='model-a')
        previous_source = (self.project / 'repository/train.py').read_bytes()
        result = self.switch()
        previous = self.root / result['previous_project_archive']
        self.assertEqual((previous / 'repository/train.py').read_bytes(), previous_source)
        self.assertEqual((previous / 'saved-result.txt').read_text(), 'keep this evidence')
        self.assertEqual((previous / 'assets/weights/model.pt').read_bytes(), b'fixture-checkpoint')
        self.assertEqual(result['import']['name'], 'next-project')
        self.assertNotEqual(result['project_id'], self.original_id)
        self.assertEqual(result['goal'], '')
        self.assertNotIn('budget', result)
        self.assertFalse(result.get('target_validated'))
        self.assertIsNone(result['existing_run'])
        self.assertEqual(self.connections.snapshot()['azure']['status'], 'connected')
        self.assertEqual(self.connections.snapshot()['copilot']['selected_model'], 'model-a')
        self.assertFalse(self.connections.snapshot()['copilot']['model_locked'])
        self.assertEqual((self.project / 'repository/train.py').read_bytes(), b'RATE = 0.3\n')

    def test_rejected_replacement_leaves_project_and_state_untouched(self):
        before = (self.project / 'onboarding.json').read_bytes()
        with self.assertRaises(SliceError):
            self.service.upload([SourceFile('bad/../escape.py', io.BytesIO(b'pass'))],
                                replace=True, expected_project=self.original_id)
        self.assertEqual(project_identity(self.project), self.original_id)
        self.assertEqual((self.project / 'onboarding.json').read_bytes(), before)
        self.assertFalse((self.root / '.runtime/project-history').exists())
        with self.assertRaisesRegex(SliceError, 'loaded project changed'):
            self.service.upload(self.replacement(), replace=True, expected_project='stale')

    def test_complete_git_preparation_is_preserved_in_history(self):
        repository = self.project / 'repository'
        (repository / '.gitignore').write_text('')
        (repository / '.research_intern').mkdir()
        contract = {'version': '1.0', 'objective': {'metric': 'f1', 'direction': 'maximize'},
                    'execution': {'backend': 'azure_ml', 'job_config': 'job.yaml'},
                    'outputs': {'root': 'experiment_outputs/'}, 'constraints': {}, 'budget': {'max_experiments': 0},
                    'scope': {'editable': ['train.py'], 'protected': ['evaluate.py', 'split.json']}}
        # JSON is also valid YAML; no research is initialized by source preparation.
        (repository / '.research_intern/contract.yaml').write_text(json.dumps(contract))
        saved = prepare_source(self.project, repository, contract_digest(repository))
        result = self.switch()
        previous = self.root / result['previous_project_archive']
        self.assertEqual(prepared_workspace(previous, previous / 'repository'), saved)
        self.assertFalse((self.project / 'workspace.json').exists())

    def test_job_search_from_previous_project_cannot_publish_to_replacement(self):
        def search(target, experiment_name, job_name):
            self.switch()
            # Even selecting the same Azure target must not adopt the old search.
            self.service.validate_target(TARGET)
            return {'items': [{'name': 'old-project-run'}], 'truncated': False}
        self.cloud.jobs = search
        with self.assertRaisesRegex(SliceError, 'Search again'):
            self.service.find_jobs()
        self.assertFalse(self.service.snapshot().get('job_search'))

    def test_driver_and_pending_azure_job_block_switching(self):
        with self.mission.project_operation():
            with self.assertRaises(MissionBusy):
                self.switch()
        database = self.project / 'ledger.sqlite3'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute('CREATE TABLE experiments (state TEXT)')
            connection.execute("INSERT INTO experiments VALUES ('SUBMITTED')")
        with self.assertRaisesRegex(SliceError, 'unfinished experiment'):
            self.switch()
        self.assertEqual(project_identity(self.project), self.original_id)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE experiments SET state='RECORDED'")
        result = self.switch()
        self.assertTrue((self.root / result['previous_project_archive'] / 'ledger.sqlite3').is_file())

    def test_interrupted_switch_recovers_without_overwriting_the_old_project(self):
        rename = Path.rename
        def fail_publish(path, target):
            if path.parent.name == 'next' and path.name == 'repository':
                raise OSError('interrupted switch')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_publish):
            with self.assertRaisesRegex(OSError, 'interrupted switch'):
                self.switch()
        self.assertTrue((self.root / '.runtime/project-switch.pending.json').exists())
        self.service.recover_project_switch()
        self.assertFalse((self.root / '.runtime/project-switch.pending.json').exists())
        self.assertEqual((self.project / 'repository/train.py').read_bytes(), b'RATE = 0.3\n')
        self.assertEqual(len(list((self.root / '.runtime/project-history').iterdir())), 1)
        self.assertFalse(self.service.snapshot()['configured'])

    def test_polling_finishes_before_project_switch_and_cannot_write_into_new_project(self):
        with patch.object(self.service, 'start'):
            self.service.choose_job('old-run')
        entered, release = threading.Event(), threading.Event()
        original = self.cloud.job
        def delayed(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        self.cloud.job = delayed
        poll = threading.Thread(target=self.service.poll_once)
        poll.start()
        self.assertTrue(entered.wait(2))
        result, errors = [], []
        def replace():
            try: result.append(self.switch())
            except BaseException as exc: errors.append(exc)
        replacement = threading.Thread(target=replace)
        replacement.start()
        release.set()
        poll.join(5); replacement.join(10)
        self.assertFalse(poll.is_alive() or replacement.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNone(result[0]['existing_run'])
        old = self.root / result[0]['previous_project_archive'] / 'onboarding.json'
        self.assertEqual(json.loads(old.read_text())['existing_run']['phase'], 'waiting')
        self.assertEqual(self.cloud.downloads, 0)

    def test_zip_replacement_api_and_stale_tab_protection(self):
        with TestClient(create_app(self.root, mission=self.mission, connections=self.connections,
                                  onboarding=self.service), base_url='http://127.0.0.1') as client:
            url = f'/api/onboarding/upload?archive=true&replace=true&expected_project={self.original_id}'
            bundle = self.replacement(archive=True)[0].stream.read()
            self.assertEqual(client.post(url, files=[('files', ('new.zip', bundle))]).status_code, 403)
            response = client.post(url, files=[('files', ('new.zip', bundle))], headers={'X-Research-Intern': '1'})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()['import']['name'], 'next-project')
            stale = client.post(url, files=[('files', ('new.zip', bundle))], headers={'X-Research-Intern': '1'})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(len(list((self.root / '.runtime/project-history').iterdir())), 1)
            page = client.get('/').text
            for control in ('change-project', 'choose-project-folder', 'choose-project-zip'):
                self.assertIn(f'id="{control}"', page)
