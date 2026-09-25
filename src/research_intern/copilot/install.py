"""Explicitly install the official, checksum-verified Copilot CLI beside the SDK.

The SDK's hostless runtime cannot run `copilot login`. This separate CLI is used
only for provider-managed browser sign-in, never to edit researcher code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import zipfile
from urllib.request import urlopen

from copilot._cli_version import CLI_VERSION, get_runtime_platform
from research_intern.workspace.paths import child_path


def install(workspace: Path, *, cancelled=None) -> Path:
    def check_cancelled():
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Sign-in cancelled")
    check_cancelled()
    if not CLI_VERSION:
        raise RuntimeError("Install a released, pinned Copilot SDK first")
    platform = get_runtime_platform()
    name = f"copilot-{platform}.zip" if os.name == "nt" else f"copilot-{platform}.tar.gz"
    base = f"https://github.com/github/copilot-cli/releases/download/v{CLI_VERSION}"
    root = child_path(workspace, ".runtime/copilot-cli")
    root.mkdir(parents=True, exist_ok=True)
    destination = child_path(root, f"{CLI_VERSION}-{platform}")
    executable = "copilot.exe" if os.name == "nt" else "copilot"
    binary = destination / "package" / executable
    receipt = destination / "installation.json"
    if receipt.is_file() and binary.is_file():
        installed = json.loads(receipt.read_text())
        with binary.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if installed["binary_sha256"] != actual:
            raise RuntimeError("Installed Copilot CLI changed; inspect it before using it")
        return binary
    if destination.exists():
        raise RuntimeError("An incomplete CLI installation exists; inspect it before replacing it")
    with urlopen(base + "/SHA256SUMS.txt", timeout=15) as response:
        sums = response.read(1024 * 1024).decode()
    expected = next((line.split()[0] for line in sums.splitlines()
                     if len(line.split()) == 2 and line.split()[1].lstrip("*") == name), None)
    if expected is None or len(expected) != 64:
        raise RuntimeError("Official release checksum is missing")
    print(f"Downloading official Copilot CLI {CLI_VERSION} for browser sign-in...", flush=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="install-") as temporary:
        stage = Path(temporary)
        archive_path = stage / "cli.tgz"
        digest, total = hashlib.sha256(), 0
        with urlopen(base + "/" + name, timeout=15) as response, archive_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                check_cancelled()
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    raise RuntimeError("Release archive exceeds the size limit")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("Copilot CLI archive checksum mismatch")
        extracted = stage / "extracted"
        extracted.mkdir()
        package = extracted / "package"
        package.mkdir()
        expanded = 0
        def extract_entry(name, size, directory, mode, opener):
            nonlocal expanded
            check_cancelled()
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                    or ":" in name or not relative.parts):
                raise RuntimeError("Unsupported release archive entry")
            target = package.joinpath(*relative.parts)
            if directory:
                target.mkdir(parents=True, exist_ok=True)
                return
            expanded += size
            if expanded > 3 * 1024 * 1024 * 1024:
                raise RuntimeError("Expanded release archive exceeds the size limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            with opener() as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            target.chmod(0o755 if mode & 0o111 else 0o644)
        if os.name == "nt":
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    extract_entry(member.filename, member.file_size, member.is_dir(), 0o644,
                                  lambda: archive.open(member))
        else:
            with tarfile.open(archive_path, "r:gz") as archive:
                for member in archive:
                    if not (member.isdir() or member.isfile()):
                        raise RuntimeError("Unsupported release archive entry")
                    if member.name in (".", "./") and member.isdir():
                        continue
                    extract_entry(member.name, member.size, member.isdir(), member.mode,
                                  lambda: archive.extractfile(member))
        staged_binary = extracted / "package" / executable
        with staged_binary.open("rb") as stream:
            binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (extracted / "installation.json").write_text(json.dumps({
            "version": CLI_VERSION, "platform": platform, "archive": name,
            "archive_sha256": expected, "binary_sha256": binary_digest,
        }, indent=2))
        extracted.rename(destination)
    return binary
