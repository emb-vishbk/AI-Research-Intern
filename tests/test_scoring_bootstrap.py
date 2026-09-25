"""Offline bootstrap tests with no installed pip/ensurepip and no network calls."""
import hashlib
import io
import json
import os
import ssl
import stat
import zipfile
from unittest.mock import patch
from urllib.error import URLError

from packaging.tags import Tag

from research_intern.domain.experiments import SliceError
from research_intern.evaluation.bootstrap import install_uv, select_wheel, UV_VERSION
from research_intern.evaluation.runtime import run_command
from test_execution_slice import WorkspaceTest


class BootstrapTests(WorkspaceTest):
    def release(self, *, symlink=False):
        executable = "uv.exe" if os.name == "nt" else "uv"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as wheel:
            info = zipfile.ZipInfo(f"uv-{UV_VERSION}.data/scripts/{executable}")
            info.external_attr = (stat.S_IFLNK if symlink else stat.S_IFREG | 0o755) << 16
            wheel.writestr(info, b"fixture executable")
            wheel.writestr("../../do-not-extract.txt", b"must not be published")
        payload = archive.getvalue()
        tag = Tag("py3", "none", "manylinux_2_17_x86_64")
        entry = {"filename": f"uv-{UV_VERSION}-{tag}.whl", "packagetype": "bdist_wheel", "yanked": False,
                 "url": "https://files.pythonhosted.org/packages/uv.whl", "size": len(payload),
                 "digests": {"sha256": hashlib.sha256(payload).hexdigest()}}
        return tag, entry, payload

    def test_installs_binary_without_python_installer_and_reuses_offline(self):
        tag, entry, wheel = self.release()
        # The failed old installer environment must not prevent retry.
        (self.root / "tools/bin").mkdir(parents=True)
        (self.root / "tools/bin/python").write_text("old incomplete environment")
        with patch("research_intern.evaluation.bootstrap.sys_tags", return_value=[tag]), patch(
                "research_intern.evaluation.bootstrap.urlopen", side_effect=[io.BytesIO(json.dumps({"urls": [entry]}).encode()), io.BytesIO(wheel)]) as request, patch("subprocess.run") as command:
            binary = install_uv(self.root, self.root / "setup.log", {})
            self.assertEqual(binary.read_bytes(), b"fixture executable")
            self.assertEqual(install_uv(self.root, self.root / "setup.log", {}), binary)
            self.assertEqual(request.call_count, 2)
            command.assert_not_called()
        self.assertFalse((self.root.parent / "do-not-extract.txt").exists())
        receipt = json.loads(binary.with_name("installation.json").read_text())
        self.assertEqual(receipt["wheel_sha256"], entry["digests"]["sha256"])
        self.assertTrue((self.root / "tools/bin/python").exists())

    def test_hash_mismatch_never_publishes_executable(self):
        tag, entry, wheel = self.release()
        entry["digests"]["sha256"] = "0" * 64
        with patch("research_intern.evaluation.bootstrap.sys_tags", return_value=[tag]), patch(
                "research_intern.evaluation.bootstrap.urlopen", side_effect=[io.BytesIO(json.dumps({"urls": [entry]}).encode()), io.BytesIO(wheel)]):
            with self.assertRaisesRegex(SliceError, "checksum verification"):
                install_uv(self.root, self.root / "setup.log", {})
        self.assertFalse(list((self.root / "bootstrap").rglob("installation.json")))

    def test_platform_selection_and_official_https_source(self):
        tag, entry, _ = self.release()
        with self.assertRaisesRegex(SliceError, "no binary"):
            select_wheel({"urls": [entry]}, [Tag("py3", "none", "win_amd64")])
        entry["url"] = "http://files.pythonhosted.org/uv.whl"
        with self.assertRaisesRegex(SliceError, "metadata is invalid"):
            select_wheel({"urls": [entry]}, [tag])

    def test_failed_download_can_be_retried_without_cleanup(self):
        tag, entry, wheel = self.release()
        with patch("research_intern.evaluation.bootstrap.urlopen", side_effect=URLError("connection interrupted")):
            with self.assertRaisesRegex(SliceError, "Could not download"):
                install_uv(self.root, self.root / "setup.log", {})
        with patch("research_intern.evaluation.bootstrap.sys_tags", return_value=[tag]), patch(
                "research_intern.evaluation.bootstrap.urlopen", side_effect=[io.BytesIO(json.dumps({"urls": [entry]}).encode()), io.BytesIO(wheel)]):
            self.assertTrue(install_uv(self.root, self.root / "setup.log", {}).is_file())

    def test_uses_configured_corporate_ca_without_disabling_tls(self):
        with patch("research_intern.evaluation.bootstrap.ssl.create_default_context", side_effect=OSError("bad certificate file")) as context:
            with self.assertRaises(SliceError):
                install_uv(self.root, self.root / "setup.log", {"SSL_CERT_FILE": "/trusted/ca.pem"})
            context.assert_called_once_with(cafile="/trusted/ca.pem")

    def test_symlink_member_is_rejected(self):
        tag, entry, wheel = self.release(symlink=True)
        with patch("research_intern.evaluation.bootstrap.sys_tags", return_value=[tag]), patch(
                "research_intern.evaluation.bootstrap.urlopen", side_effect=[io.BytesIO(json.dumps({"urls": [entry]}).encode()), io.BytesIO(wheel)]):
            with self.assertRaisesRegex(SliceError, "regular file"):
                install_uv(self.root, self.root / "setup.log", {})

    def test_creation_error_reports_stage_instead_of_assuming_network(self):
        import subprocess
        with patch("research_intern.evaluation.runtime.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["uv"])):
            with self.assertRaisesRegex(SliceError, "compatible Python environment creation"):
                run_command(["uv", "venv"], self.root / "setup.log", {}, self.root,
                            phase="compatible Python environment creation")
