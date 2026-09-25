"""Provision the pinned uv executable without pip, venv or ensurepip.

Read an official PyPI wheel as a ZIP and publish only its executable. No wheel
installer, uploaded project code, shell script or system package manager runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename, InvalidWheelFilename

from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes

UV_VERSION = "0.8.22"
MAX_WHEEL = 64 * 1024**2
MAX_BINARY = 128 * 1024**2


def select_wheel(release, tags):
    ranks = {tag: i for i, tag in enumerate(tags)}
    matches = []
    for entry in release.get("urls", []):
        if entry.get("packagetype") != "bdist_wheel" or entry.get("yanked"):
            continue
        try:
            name, version, _, wheel_tags = parse_wheel_filename(entry["filename"])
        except (InvalidWheelFilename, KeyError, TypeError):
            continue
        supported = wheel_tags & ranks.keys()
        if name == "uv" and str(version) == UV_VERSION and supported:
            matches.append((min(ranks[tag] for tag in supported), entry))
    if not matches:
        raise SliceError("The evaluation installer has no binary for this operating system and CPU architecture")
    entry = min(matches, key=lambda pair: pair[0])[1]
    url = urlsplit(entry.get("url", ""))
    checksum = entry.get("digests", {}).get("sha256", "")
    if (url.scheme != "https" or url.hostname != "files.pythonhosted.org" or url.username or url.password
            or url.port not in (None, 443) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or type(entry.get("size")) is not int or not 0 < entry["size"] <= MAX_WHEEL):
        raise SliceError("The official evaluation installer metadata is invalid")
    return entry


def install_uv(cache: Path, log: Path, environment: dict) -> Path:
    destination = child_path(cache, "bootstrap", "uv-" + UV_VERSION)
    destination.mkdir(parents=True, exist_ok=True)
    binary = child_path(destination, "uv.exe" if os.name == "nt" else "uv")
    receipt = child_path(destination, "installation.json")
    if binary.is_file() and receipt.is_file():
        try:
            installed = json.loads(receipt.read_text())
            with binary.open("rb") as source:
                checksum = hashlib.file_digest(source, "sha256").hexdigest()
            if installed.get("version") == UV_VERSION and installed.get("binary_sha256") == checksum:
                return binary
        except (ValueError, OSError):
            pass  # Retry an interrupted installation with a verified fresh copy.
    try:
        certificate = environment.get("SSL_CERT_FILE") or environment.get("REQUESTS_CA_BUNDLE")
        context = ssl.create_default_context(cafile=certificate)
        with urlopen(f"https://pypi.org/pypi/uv/{UV_VERSION}/json", timeout=30, context=context) as response:
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise SliceError("Evaluation installer release metadata exceeds its size limit")
        entry = select_wheel(json.loads(raw), sys_tags())
        with log.open("a", encoding="utf-8") as output:
            output.write(f"\nDownloading standalone evaluation installer: {entry['filename']}\n")
        with tempfile.TemporaryDirectory(dir=cache, prefix="installer-") as temporary:
            wheel = Path(temporary) / "uv.whl"
            checksum, size = hashlib.sha256(), 0
            with urlopen(entry["url"], timeout=30, context=context) as response, wheel.open("wb") as output:
                while chunk := response.read(1024**2):
                    size += len(chunk)
                    if size > MAX_WHEEL:
                        raise SliceError("Evaluation installer download exceeds its size limit")
                    checksum.update(chunk)
                    output.write(chunk)
            if size != entry["size"] or checksum.hexdigest() != entry["digests"]["sha256"]:
                raise SliceError("Evaluation installer checksum verification failed; retry preparation")
            with zipfile.ZipFile(wheel) as archive:
                candidates = [m for m in archive.infolist() if not m.is_dir() and PurePosixPath(m.filename).name == binary.name]
                if len(candidates) != 1:
                    raise SliceError("The evaluation installer archive does not contain one expected executable")
                member = candidates[0]
                if (member.file_size > MAX_BINARY or member.flag_bits & 1
                        or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise SliceError("The evaluation installer executable is not a bounded regular file")
                payload = archive.read(member)
            atomic_bytes(binary, payload, permissions=0o755)
            atomic_bytes(receipt, json.dumps({"version": UV_VERSION, "wheel": entry["filename"],
                         "wheel_sha256": entry["digests"]["sha256"],
                         "binary_sha256": hashlib.sha256(payload).hexdigest()}, indent=2).encode())
            return binary
    except (OSError, ValueError, zipfile.BadZipFile, SliceError) as exc:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"Installer bootstrap failed: {type(exc).__name__}: {exc}\n")
        if isinstance(exc, SliceError):
            raise
        raise SliceError("Could not download or verify the evaluation installer. "
                         "See scoring-environments/setup.log for the connection or certificate error, then retry preparation.") from exc
