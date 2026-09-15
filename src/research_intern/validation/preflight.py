"""Check syntax and data configuration without executing researcher code."""

from pathlib import Path

from research_intern.contracts.loader import read_yaml
from research_intern.domain.experiments import SliceError
from research_intern.execution.outputs import read_json
from research_intern.workspace.paths import child_path


class PreflightError(SliceError):
    """An inexpensive candidate check failed before execution."""


def validate_files(repository: Path, changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    checks = []
    for name in changed_paths:
        path = child_path(repository, name)
        if not path.is_file():
            continue
        try:
            if path.suffix == ".py":
                compile(path.read_bytes(), name, "exec")
                checks.append(f"Python syntax: {name}")
            elif path.suffix in (".yaml", ".yml"):
                read_yaml(path)
                checks.append(f"YAML syntax: {name}")
            elif path.suffix == ".json":
                read_json(path)
                checks.append(f"JSON object: {name}")
        except (SyntaxError, ValueError, OSError, SliceError) as exc:
            raise PreflightError(f"Preflight failed for {name}: {exc}") from exc
    return tuple(checks)
