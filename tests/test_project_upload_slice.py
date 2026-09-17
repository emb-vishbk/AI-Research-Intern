"""Local folder handoff, budget persistence and single-loop dashboard boundaries."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    import python_multipart
except ImportError as exc:
    raise unittest.SkipTest("Install optional web and web-test dependencies") from exc

from research_intern.api.app import create_app
from research_intern.controller.mission import MissionControl
from research_intern.domain.experiments import SliceError
from research_intern.workspace.project import PROJECT_PATH, ProjectStore, SourceFile, source_paths
from test_execution_slice import WorkspaceTest

HEADERS = {"X-Research-Intern": "1"}
CONTRACT = {
    "version": "1.0", "objective": {"metric": "val_map_50_95", "direction": "maximize"},
    "execution": {"backend": "azure_ml", "job_config": "execution/azure_job.yaml"},
    "outputs": {"root": "experiment_outputs/"},
    "scope": {"editable": ["train.py"], "protected": ["evaluate.py"]},
    "budget": {"max_experiments": 0},
}


def project_files():
    return {
        ".research_intern/contract.yaml": json.dumps(CONTRACT).encode(),
        "execution/azure_job.yaml": b"type: command\ncommand: python train.py\n",
        "train.py": b"raise RuntimeError('This source must never execute during upload')\n",
        "evaluate.py": b"# Researcher-owned evaluator\n",
        "configs/learning rate.txt": b"0.001\n",
        "README.md": "Independent pedestrian experiment.\n".encode(),
    }


class ProjectUploadTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.mission = MissionControl(self.root)
        self.client = TestClient(create_app(self.root, mission=self.mission), base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.target = self.root / PROJECT_PATH

    def upload(self, files=None, headers=HEADERS):
        items = files if files is not None else [(f"Penn Fudan/{name}", raw) for name, raw in project_files().items()]
        return self.client.post("/api/project/upload", files=[("files", (name, raw, "application/octet-stream")) for name, raw in items], headers=headers)

    def budget(self, **changes):
        return {"max_experiments": 5, "max_gpu_hours": 2.5, "max_ai_credits": 120.0,
                "contract_sha256": self.client.get("/api/project").json()["contract_sha256"], **changes}

    def test_upload_preserves_contents_and_validates_without_execution(self):
        self.target.mkdir(parents=True)
        self.assertEqual(self.client.get("/api/project").json()["status"], "missing")
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        project = response.json()
        self.assertEqual(project["status"], "contract_valid")
        self.assertEqual(project["name"], "Penn Fudan")
        self.assertEqual(project["objective"]["metric"], "val_map_50_95")
        self.assertFalse(project["execution_available"])
        self.assertFalse(project["upload_available"])
        for name, raw in project_files().items():
            self.assertEqual((self.target / name).read_bytes(), raw)
        self.assertFalse((self.target / "Penn Fudan").exists())
        self.assertFalse((self.target / ".git").exists())
        self.assertEqual(list(self.target.parent.glob("upload-*")), [])
        self.assertEqual(self.client.post("/api/project/start", json={}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.get("/api/runs").json()["runs"], [])

    def test_existing_project_is_never_overwritten(self):
        self.assertEqual(self.upload().status_code, 201)
        marker = self.target / "researcher_notes.txt"
        marker.write_text("keep my work")
        response = self.upload()
        self.assertEqual(response.status_code, 409)
        self.assertIn("already loaded", response.json()["detail"])
        self.assertEqual(marker.read_text(), "keep my work")
        self.assertEqual((self.target / "train.py").read_bytes(), project_files()["train.py"])

    def test_bad_contract_and_missing_protected_file_leave_slot_empty(self):
        self.target.mkdir(parents=True)
        for remove in ("evaluate.py", "execution/azure_job.yaml"):
            files = [(f"Project/{name}", raw) for name, raw in project_files().items() if name != remove]
            with self.subTest(remove=remove):
                self.assertEqual(self.upload(files).status_code, 409)
                self.assertEqual(list(self.target.iterdir()), [])
                self.assertEqual(list(self.target.parent.glob("upload-*")), [])

    def test_source_without_contract_can_be_uploaded_for_researcher_review(self):
        files = [(f"Project/{name}", raw) for name, raw in project_files().items()
                 if name != ".research_intern/contract.yaml"]
        response = self.upload(files)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "needs_contract")
        self.assertFalse(response.json()["prepare_available"])
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse(self.client.get("/api/workspace").json()["can_start"])

    def test_unsafe_and_conflicting_paths_cannot_escape_source_slot(self):
        examples = [
            [("Project/../../outside.txt", b"bad")], [("/absolute.txt", b"bad")],
            [("C:/outside.txt", b"bad")], [("Project/dir\\outside.txt", b"bad")],
            [("Project/CON.txt", b"bad")], [("Project/space /x.py", b"bad")],
            [("Project/file.py", b"a"), ("Project/file.py", b"b")],
            [("Project/a/x.py", b"a"), ("Project/A/y.py", b"b")],
            [("Project/a", b"a"), ("Project/a/x.py", b"b")],
            [("First/a.py", b"a"), ("Second/b.py", b"b")],
            [("Project/.git/config", b"[include]\npath=/private")],
        ]
        for files in examples:
            with self.subTest(files=files):
                self.assertEqual(self.upload(files).status_code, 409)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "outside.txt").exists())

    def test_symlink_destination_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.target.parent.mkdir(parents=True)
        self.create_symlink(self.target, outside, target_is_directory=True)
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(list(outside.iterdir()), [])

    def test_payload_limits_and_interrupted_upload_do_not_publish_files(self):
        with patch("research_intern.api.uploads.MAX_REQUEST_BYTES", 100):
            self.assertEqual(self.upload().status_code, 413)
        with patch("research_intern.api.uploads.MAX_UPLOAD_FILES", 1):
            self.assertEqual(self.upload().status_code, 400)
        with patch("research_intern.workspace.project.MAX_UPLOAD_BYTES", 10):
            self.assertEqual(self.upload().status_code, 409)
        request = self.client.build_request("POST", "/api/project/upload", headers=HEADERS,
                                           files=[("files", (f"Project/{name}", raw)) for name, raw in project_files().items()])
        truncated = request.read()[:-12]
        response = self.client.post("/api/project/upload", content=truncated,
                                    headers={**HEADERS, "Content-Type": request.headers["Content-Type"]})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob("upload-*")), [])

    def test_browser_origin_checks_apply_to_upload_and_budget(self):
        for headers in ({}, {**HEADERS, "Origin": "https://foreign.example"}):
            self.assertEqual(self.upload(headers=headers).status_code, 403)
            self.assertEqual(self.client.post("/api/project/budget", json={}, headers=headers).status_code, 403)
        self.assertFalse(self.target.exists())

    def test_budget_is_persisted_separately_and_bound_to_contract(self):
        self.assertEqual(self.upload().status_code, 201)
        original = (self.target / ".research_intern/contract.yaml").read_bytes()
        budget = self.budget()
        response = self.client.post("/api/project/budget", json=budget, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["budget_saved"])
        fresh = ProjectStore(self.root).inspect()
        self.assertEqual(fresh["budget"], {k: v for k, v in budget.items() if k != "contract_sha256"})
        self.assertEqual((self.target / ".research_intern/contract.yaml").read_bytes(), original)
        (self.target / ".research_intern/contract.yaml").write_bytes(original + b"\n")
        self.assertFalse(ProjectStore(self.root).inspect()["budget_saved"])
        self.assertEqual(self.client.post("/api/project/budget", json=budget, headers=HEADERS).status_code, 409)

    def test_budget_rejects_invalid_values_and_missing_project(self):
        empty = {"max_experiments": 3, "max_gpu_hours": 2.0, "max_ai_credits": 50.0, "contract_sha256": "a" * 64}
        self.assertEqual(self.client.post("/api/project/budget", json=empty, headers=HEADERS).status_code, 409)
        self.assertEqual(self.upload().status_code, 201)
        for changes in ({"max_experiments": True}, {"max_experiments": 1.5}, {"max_experiments": 51},
                        {"max_gpu_hours": -1}, {"max_ai_credits": "50"}, {"max_ai_credits": True},
                        {"scenario": "mixed"}):
            with self.subTest(changes=changes):
                response = self.client.post("/api/project/budget", json=self.budget(**changes), headers=HEADERS)
                self.assertEqual(response.status_code, 422, response.text)
        raw = json.dumps(self.budget()).replace('"max_gpu_hours": 2.5', '"max_gpu_hours": 1e999')
        self.assertEqual(self.client.post("/api/project/budget", content=raw, headers={**HEADERS, "Content-Type": "application/json"}).status_code, 422)

    def test_upload_settings_and_run_creation_are_mutually_exclusive(self):
        with self.mission.project_operation():
            self.assertEqual(self.upload().status_code, 409)
            self.assertEqual(self.client.post("/api/runs", json={}, headers=HEADERS).status_code, 409)
            budget = {"max_experiments": 1, "max_gpu_hours": 1.0, "max_ai_credits": 10.0, "contract_sha256": "a" * 64}
            self.assertEqual(self.client.post("/api/project/budget", json=budget, headers=HEADERS).status_code, 409)
        self.mission._active = "loop-test"
        try:
            self.assertEqual(self.upload().status_code, 409)
        finally:
            self.mission._active = None
        self.assertEqual(self.upload().status_code, 201)

    def test_saved_simulation_does_not_become_uploaded_project_history(self):
        run = self.mission.create(max_experiments=0)
        old = self.client.get("/api/workspace").json()
        self.assertEqual(old["run"]["id"], run["id"])
        self.assertEqual(old["run"]["mode"], "simulated")
        self.assertEqual(self.upload().status_code, 201)
        current = self.client.get("/api/workspace").json()
        self.assertIsNone(current["run"])
        self.assertFalse(current["can_start"])
        self.assertEqual(current["project"]["objective"]["metric"], "val_map_50_95")
        self.assertEqual(len(self.client.get("/api/runs").json()["runs"]), 1)

    def test_prepare_and_confirm_evaluation_without_enabling_live_start(self):
        from test_project_setup_slice import sources
        files = [(f"Research/{name}", raw) for name, raw in sources().items()]
        project = self.upload(files).json()
        for headers in ({}, {**HEADERS, "Origin": "https://foreign.example"}):
            self.assertEqual(self.client.post("/api/project/prepare", json={"contract_sha256": project["contract_sha256"]}, headers=headers).status_code, 403)
            self.assertEqual(self.client.post("/api/project/evaluation/confirm", json={}, headers=headers).status_code, 403)
        with self.mission.project_operation():
            self.assertEqual(self.client.post("/api/project/prepare", json={"contract_sha256": project["contract_sha256"]}, headers=HEADERS).status_code, 409)
        prepared = self.client.post("/api/project/prepare", json={"contract_sha256": project["contract_sha256"]}, headers=HEADERS)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        project = prepared.json()
        self.assertTrue(project["workspace_prepared"])
        confirmation = {name: project[name] for name in ("source_commit", "contract_sha256", "evaluation_fingerprint")}
        self.assertEqual(self.client.post("/api/project/evaluation/confirm", json=confirmation, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.post("/api/project/evaluation/confirm", json={**confirmation, "confirmed": False}, headers=HEADERS).status_code, 422)
        response = self.client.post("/api/project/evaluation/confirm", json={**confirmation, "confirmed": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evaluation_status"], "confirmed")
        self.assertFalse(self.client.get("/api/workspace").json()["can_start"])
        self.assertEqual(self.client.post("/api/project/start", json={}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.get("/api/runs").json()["runs"], [])


class UploadPathTests(unittest.TestCase):
    def test_raw_control_character_paths_are_rejected(self):
        for path in ("Project/bad\x00.py", "Project/bad\n.py", "Project/bad\x7f.py", "Project/../bad.py"):
            with self.subTest(path=path), self.assertRaises(SliceError):
                source_paths([SourceFile(path, io.BytesIO(b""))])
