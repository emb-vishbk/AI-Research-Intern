"""Windows Edge smoke test against the explicitly started fake-provider fixture.

Uses only the Python standard library and the browser's local debugging protocol.
Does not click provider links, authenticate an account or submit experiments.
"""
import base64
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
HTTP = build_opener(ProxyHandler({}))


class CDP:
    def __init__(self, port, path):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        headers = b""
        while not headers.endswith(b"\r\n\r\n"):
            headers += self.sock.recv(1)
        assert b"101 Switching Protocols" in headers, headers
        self.sequence = 0
        self.errors = []

    def read(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Browser debugging connection closed")
            data += chunk
        return data

    def call(self, method, params=None):
        self.sequence += 1
        data = json.dumps({"id": self.sequence, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        length = len(data)
        header = bytes([0x81, 0x80 | length]) if length < 126 else bytes([0x81, 0xFE]) + struct.pack("!H", length)
        self.sock.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(data)))
        message = b""
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            assert not second & 128
            message += self.read(size)
            if not first & 128:
                continue
            response = json.loads(message)
            message = b""
            if response.get("method") == "Runtime.exceptionThrown":
                self.errors.append(response["params"])
            if response.get("id") == self.sequence:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def wait(self, expression):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(.15)
        raise AssertionError("Browser condition timed out: " + expression)


if __name__ == "__main__":
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Edge") as policy:
            if winreg.QueryValueEx(policy, "RemoteDebuggingAllowed")[0] == 0:
                raise SystemExit("SKIPPED: Edge remote debugging is disabled by organization policy.")
    except FileNotFoundError:
        pass
    url = sys.argv[1]
    assert url.startswith("http://127.0.0.1:")
    edge = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe"
    artifact_root = ROOT / ".runtime/test-results"
    artifact_root.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="signin-browser-", dir=artifact_root)).resolve()
    assert profile.is_relative_to(artifact_root.resolve())
    launch_log = (artifact_root / "browser-launch.log").open("w")
    process = subprocess.Popen([str(edge), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=launch_log, creationflags=subprocess.CREATE_NO_WINDOW)
    cdp = None
    try:
        deadline = time.monotonic() + 20
        active_port = profile / "DevToolsActivePort"
        while not active_port.is_file() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.1)
        if not active_port.is_file():
            raise RuntimeError(f"Edge debugging did not start (exit {process.poll()}); see {artifact_root / 'browser-launch.log'}")
        port = int(active_port.read_text().splitlines()[0])
        targets = json.loads(HTTP.open(f"http://127.0.0.1:{port}/json/list", timeout=10).read())
        target = next(item for item in targets if item["type"] == "page")
        path = "/" + target["webSocketDebuggerUrl"].split("/", 3)[3]
        cdp = CDP(port, path)
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": url})
        cdp.wait("document.getElementById('azure-signin') && !document.getElementById('azure-signin').disabled")
        click = lambda selector: cdp.evaluate(f"document.getElementById({json.dumps(selector)}).click()")
        state = lambda provider, status: cdp.wait(f"document.getElementById('{provider}-status').textContent === '{status}'")
        complete = lambda: cdp.evaluate("fetch('/__test__/complete', {method:'POST',headers:{'X-Research-Intern':'1'}}).then(r=>r.json())")
        click("azure-signin")
        state("azure", "Awaiting sign-in")
        assert cdp.evaluate("document.getElementById('azure-code').textContent") == "TEST-1234"
        assert cdp.evaluate("document.getElementById('copilot-signin').disabled")
        click("azure-cancel")
        state("azure", "Cancelled")
        click("azure-signin")
        state("azure", "Awaiting sign-in")
        complete()
        state("azure", "Connected")
        click("copilot-signin")
        state("copilot", "Awaiting sign-in")
        complete()
        state("copilot", "Access required")
        assert "Copilot license" in cdp.evaluate("document.getElementById('copilot-message').textContent")
        click("copilot-signin")
        state("copilot", "Awaiting sign-in")
        complete()
        state("copilot", "Connected")
        cdp.evaluate("document.getElementById('copilot-model').value='model-b'; document.getElementById('copilot-model').dispatchEvent(new Event('change'))")
        click("copilot-save-model")
        cdp.wait("document.getElementById('copilot-message').textContent.includes('model saved')")
        assert cdp.evaluate("document.getElementById('start').disabled")
        shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (artifact_root / "signin-dashboard.png").write_bytes(base64.b64decode(shot["data"]))
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        assert cdp.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not cdp.errors, cdp.errors
        (artifact_root / "signin-browser.json").write_text(json.dumps({"passed": True, "checks": ["initial controls", "Microsoft challenge", "cancel and retry", "independent connections", "Copilot access denial", "account retry", "model selection", "research remains stopped", "mobile layout", "no JavaScript exceptions"]}, indent=2))
        print("Browser sign-in checks passed; screenshot: " + str(artifact_root / "signin-dashboard.png"))
    finally:
        if cdp:
            try:
                cdp.call("Browser.close")
            except (OSError, RuntimeError):
                pass
            cdp.sock.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        launch_log.close()
