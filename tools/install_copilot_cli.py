"""Developer entry point for the same verified helper used by dashboard sign-in."""
import os
from pathlib import Path

from research_intern.copilot.install import install

if __name__ == "__main__":
    workspace = Path(__file__).resolve().parents[1]
    bundle = workspace / ".runtime/trusted-ca-bundle.pem"
    if bundle.is_file():
        os.environ.setdefault("SSL_CERT_FILE", str(bundle))
    print(install(workspace))
