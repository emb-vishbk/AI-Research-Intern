"""Explicit local browser fixture: fake identities, no provider or GPU calls."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading

import uvicorn
from research_intern.api.app import create_app
from research_intern.connections import Connections
from research_intern.controller.mission import MissionControl
from research_intern.signin import SignInError


class Providers:
    def __init__(self):
        self.release = threading.Event()
        self.copilot_attempt = 0

    def run(self, name, update, cancelled, host):
        self.release.clear()
        update(status="waiting", message="Complete the simulated browser sign-in.",
               authorization_url="https://microsoft.com/devicelogin" if name == "azure" else "https://github.com/login/device",
               user_code="TEST-1234")
        while not self.release.wait(.05):
            if cancelled.is_set():
                return
        if name == "copilot":
            self.copilot_attempt += 1
            if self.copilot_attempt == 1:
                raise SignInError("GitHub sign-in succeeded, but Copilot access was denied. Check the account's Copilot license and your organization's Copilot CLI policy.", "access_denied")
        update(status="connected", message="Connected to simulated provider.", storage="session",
               models=["model-a", "model-b"] if name == "copilot" else [], authorization_url=None, user_code=None)

    def azure(self, *args):
        self.run("azure", *args)

    def copilot(self, *args):
        self.run("copilot", *args)

    def bind(self, settings):
        pass

    def close(self):
        pass


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="research-intern-browser-test-") as temporary:
        root = Path(temporary)
        (root / ".runtime").mkdir()
        (root / ".runtime/live-settings.draft.json").write_text(json.dumps({"copilot": {"model": ""}}))
        mission = MissionControl(root)
        providers = Providers()
        connections = Connections(root, mission, providers=providers)
        app = create_app(root, mission=mission, connections=connections)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error"))

        @app.post("/__test__/complete")
        def complete():
            providers.release.set()
            return {"ok": True}

        @app.post("/__test__/shutdown")
        def shutdown():
            server.should_exit = True
            return {"ok": True}

        server.run()
