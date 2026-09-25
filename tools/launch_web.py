"""Launch the local UI with an optional project-local corporate CA bundle."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def prepare_import_path(workspace: Path) -> None:
    source = workspace / 'src'
    required = ('__init__.py', 'main.py', 'copilot/auth.py', 'api/app.py')
    if any(not (source / 'research_intern' / name).is_file() for name in required):
        raise SystemExit(
            f'Research Intern application files are missing from {source / "research_intern"}.\n'
            'Restore this folder from your application checkout, then run start-web.cmd again.\n'
            'Uploading an experiment creates a research workspace; it cannot recreate the application.'
        )
    sys.path.insert(0, str(source))
    # The server is a new Python process and must use this checkout too.
    os.environ['PYTHONPATH'] = str(source)


def main():
    workspace = Path(__file__).resolve().parents[1]
    os.chdir(workspace)
    args = sys.argv[1:]
    if '--help' in args or '-h' in args:
        print('Usage: start-web.cmd [--port PORT] [--check]')
        print('Connect Azure and Copilot using the sign-in buttons in the dashboard.')
        print('--check verifies local application imports without starting the server or signing in.')
        print('--sign-in remains accepted for compatibility; sign-in happens in the dashboard.')
        return
    prepare_import_path(workspace)
    bundle = workspace / '.runtime/trusted-ca-bundle.pem'
    extra = workspace / '.runtime/zscaler-root.pem'
    if bundle.is_file():
        for key in ('REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'PIP_CERT'):
            os.environ.setdefault(key, str(bundle))
    if extra.is_file():
        os.environ.setdefault('NODE_EXTRA_CA_CERTS', str(extra))
    from research_intern.copilot.auth import prefer_browser_credentials
    prefer_browser_credentials()
    if args == ['--check']:
        from research_intern.api.app import create_app
        print(f'Application imports OK. Python: {sys.executable}')
        return
    sign_in_requested = '--sign-in' in args or '--copilot-token' in args
    if sign_in_requested:
        args = [value for value in args if value not in ('--sign-in', '--copilot-token')]
        args = [value for value in args if value != '--device-code']
        if '--copilot-host' in args:
            index = args.index('--copilot-host')
            if index + 1 >= len(args):
                raise SystemExit('--copilot-host needs your GitHub Enterprise Cloud hostname.')
            os.environ['RESEARCH_INTERN_COPILOT_HOST'] = args[index + 1]
            del args[index:index + 2]
        print('Use Sign in to Azure and Sign in to Copilot in the dashboard.', flush=True)
    elif '--device-code' in args or '--copilot-host' in args:
        raise SystemExit('Use --sign-in with browser authentication options.')
    os.execv(sys.executable, [sys.executable, '-B', '-m', 'research_intern.main', 'serve', *args])


if __name__ == '__main__':
    main()
