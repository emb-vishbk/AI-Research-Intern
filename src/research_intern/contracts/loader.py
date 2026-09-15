"""Bounded, strict YAML loading for the prepared experiment contract."""

from __future__ import annotations

from pathlib import Path

import yaml

from research_intern.contracts.models import CONTRACT_PATH, ContractError, ExperimentContract
from research_intern.workspace.paths import child_path

MAX_YAML_BYTES = 256 * 1024


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ContractError("YAML aliases are not supported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ContractError("YAML keys must be unique strings; merge keys are not supported")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def read_yaml(path: Path) -> object:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ContractError("YAML inputs must be regular files without links")
        with path.open("rb") as stream:
            raw = stream.read(MAX_YAML_BYTES + 1)
        if len(raw) > MAX_YAML_BYTES:
            raise ContractError("YAML inputs exceed the 256 KiB limit")
        return yaml.load(raw.decode("utf-8"), Loader=StrictLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError, ValueError) as exc:
        raise ContractError(f"Cannot load valid YAML from {path.name}") from exc


def load_contract(repository: Path) -> ExperimentContract:
    contract = ExperimentContract.from_dict(read_yaml(child_path(repository, CONTRACT_PATH)))
    for scope in (*contract.scope.editable, *contract.scope.protected):
        path = child_path(repository, scope)
        if ((scope in contract.scope.protected and not path.exists())
                or (path.exists() and path.is_dir() != scope.endswith("/"))):
            raise ContractError(f"Scope path is missing or has the wrong file/directory type: {scope}")
    job = child_path(repository, contract.execution.job_config)
    if not isinstance(read_yaml(job), dict):
        raise ContractError("The Azure job configuration must be a YAML object")
    # Full Azure service validation belongs to the future live executor.
    child_path(repository, contract.outputs.root)
    return contract
