"""Browser sign-in orchestration with fake providers; no cloud/model operations."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, create_autospec, patch

from copilot import CopilotClient, GetAuthStatusResponse
from research_intern import signin
from research_intern.copilot.auth import prefer_browser_credentials, runtime_environment


class SignInTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.draft = self.root / '.runtime/live-settings.draft.json'
        self.draft.parent.mkdir()
        self.settings = {'azure': {'subscription_id': '11111111-2222-3333-4444-555555555555'},
                         'copilot': {'runtime_path': 'fake-runtime', 'model': ''}, 'scoring': {}}
        self.draft.write_text(json.dumps(self.settings))
        (self.root / 'fake-runtime').write_text('SDK is mocked')
        cli = self.root / '.runtime/copilot-cli' / f'{signin.CLI_VERSION}-linux-x64' / 'package'
        cli.mkdir(parents=True)
        (cli / ('copilot.exe' if os.name == 'nt' else 'copilot')).write_text('CLI is mocked')
        platform = patch.object(signin, 'get_runtime_platform', return_value='linux-x64')
        platform.start()
        self.addCleanup(platform.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_azure_reuses_valid_login_and_selects_target_subscription(self):
        with patch.object(signin.shutil, 'which', return_value='/fake/az'), \
             patch.object(signin.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            signin.azure_sign_in(self.settings['azure']['subscription_id'])
        self.assertEqual(len(run.call_args_list), 2)
        self.assertIn('get-access-token', run.call_args_list[0].args[0])
        self.assertIs(run.call_args_list[0].kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(run.call_args_list[1].args[0][1:3], ['account', 'set'])
        self.assertFalse(any('login' in call.args[0] for call in run.call_args_list))

    def test_azure_missing_login_uses_browser_and_rechecks_access(self):
        statuses = [1, 0, 0, 0]
        with patch.object(signin.shutil, 'which', return_value='/fake/az'), \
             patch.object(signin.subprocess, 'run', side_effect=[SimpleNamespace(returncode=c) for c in statuses]) as run:
            signin.azure_sign_in(self.settings['azure']['subscription_id'], device_code=True)
        self.assertIn('--use-device-code', run.call_args_list[1].args[0])
        self.assertEqual(run.call_args_list[1].kwargs['env']['AZURE_CORE_LOGIN_EXPERIENCE_V2'], 'off')
        self.assertIn('get-access-token', run.call_args_list[2].args[0])

    def test_azure_failed_login_stops_without_selection(self):
        with patch.object(signin.shutil, 'which', return_value='/fake/az'), \
             patch.object(signin.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run, \
             self.assertRaises(signin.SignInError):
            signin.azure_sign_in(self.settings['azure']['subscription_id'])
        self.assertEqual(len(run.call_args_list), 2)

    def test_copilot_login_uses_browser_and_shared_home_without_secrets(self):
        with patch.dict(os.environ, {'AZURE_CLIENT_SECRET': 'azure-secret', 'GH_TOKEN': 'token-override',
                        'NODE_EXTRA_CA_CERTS': '/trusted.pem', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/keyring'}):
            command, environment = signin.copilot_login_command(self.root, host='example.ghe.com')
        self.assertEqual(command[1:], ['login', '--web-flow', '--host', 'https://example.ghe.com'])
        self.assertEqual(environment['COPILOT_HOME'], str(self.root / '.runtime/copilot-state'))
        self.assertEqual(environment['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/keyring')
        self.assertEqual(environment['NODE_EXTRA_CA_CERTS'], '/trusted.pem')
        self.assertNotIn('GH_TOKEN', environment)
        self.assertNotIn('AZURE_CLIENT_SECRET', environment)

    def test_device_code_and_invalid_host(self):
        command, _ = signin.copilot_login_command(self.root, device_code=True)
        self.assertIn('--device-code', command)
        for host in ('https://github.com', 'github.com --allow-all', 'unrelated.example'):
            with self.subTest(host=host), self.assertRaises(signin.SignInError):
                signin.copilot_login_command(self.root, host=host)

    def test_runtime_inherits_keychain_connection_but_not_azure_credentials(self):
        with patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'keyring', 'XDG_RUNTIME_DIR': '/run/user/1000',
                                    'AZURE_CLIENT_SECRET': 'private', 'NODE_EXTRA_CA_CERTS': '/trusted.pem'}):
            environment = runtime_environment()
        self.assertEqual(environment['DBUS_SESSION_BUS_ADDRESS'], 'keyring')
        self.assertEqual(environment['XDG_RUNTIME_DIR'], '/run/user/1000')
        self.assertNotIn('AZURE_CLIENT_SECRET', environment)

    def test_login_then_model_selection_updates_only_draft_model(self):
        with patch.dict(os.environ, {'COPILOT_GITHUB_TOKEN': 'must-not-override-browser'}), \
             patch.object(signin, 'azure_sign_in') as azure, \
             patch.object(signin, 'copilot_models', AsyncMock(side_effect=[None, ['model-a', 'model-b']])), \
             patch.object(signin.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
             patch('builtins.input', side_effect=['invalid', '2']):
            signin.sign_in(self.root)
            self.assertNotIn('COPILOT_GITHUB_TOKEN', os.environ)
        expected = json.loads(json.dumps(self.settings))
        expected['copilot']['model'] = 'model-b'
        self.assertEqual(json.loads(self.draft.read_text()), expected)
        azure.assert_called_once()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:], ['login', '--web-flow'])
        self.assertFalse((self.root / '.runtime/research-project/live.json').exists())
        self.assertFalse((self.root / '.runtime/research-project/ledger.sqlite3').exists())

    def test_valid_copilot_login_and_model_do_not_prompt_or_rewrite(self):
        self.settings['copilot']['model'] = 'model-a'
        self.draft.write_text(json.dumps(self.settings))
        before = self.draft.read_bytes()
        with patch.object(signin, 'azure_sign_in'), \
             patch.object(signin, 'copilot_models', AsyncMock(return_value=['model-a'])), \
             patch.object(signin.subprocess, 'run') as run, patch('builtins.input') as ask:
            signin.sign_in(self.root)
        run.assert_not_called()
        ask.assert_not_called()
        self.assertEqual(self.draft.read_bytes(), before)

    def test_stale_copilot_auth_can_be_repaired_with_one_browser_login(self):
        with patch.object(signin, 'azure_sign_in'), \
             patch.object(signin, 'copilot_models', AsyncMock(side_effect=[signin.SignInError('expired'), ['model-a']])), \
             patch.object(signin.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
             patch('builtins.input', return_value='1'):
            signin.sign_in(self.root)
        run.assert_called_once()
        self.assertEqual(json.loads(self.draft.read_text())['copilot']['model'], 'model-a')

    def test_browser_preference_removes_only_copilot_token_overrides(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'old', 'COPILOT_GITHUB_TOKEN': 'old', 'GITHUB_TOKEN': 'old',
                                    'REQUESTS_CA_BUNDLE': 'bundle', 'AZURE_CONFIG_DIR': 'azure-cache'}):
            prefer_browser_credentials()
            for key in ('GH_TOKEN', 'COPILOT_GITHUB_TOKEN', 'GITHUB_TOKEN'):
                self.assertNotIn(key, os.environ)
            self.assertEqual(os.environ['REQUESTS_CA_BUNDLE'], 'bundle')
            self.assertEqual(os.environ['AZURE_CONFIG_DIR'], 'azure-cache')

    def test_cancelled_copilot_login_leaves_settings_unchanged(self):
        before = self.draft.read_bytes()
        with patch.object(signin, 'azure_sign_in'), \
             patch.object(signin, 'copilot_models', AsyncMock(return_value=None)), \
             patch.object(signin.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
             self.assertRaises(signin.SignInError):
            signin.sign_in(self.root)
        self.assertEqual(self.draft.read_bytes(), before)

    def test_unavailable_model_never_changes_a_live_run(self):
        live = self.root / '.runtime/research-project/live.json'
        live.parent.mkdir()
        self.settings['copilot']['model'] = 'fixed-model'
        live.write_text(json.dumps({'settings': self.settings}))
        before = live.read_bytes()
        with patch.object(signin, 'azure_sign_in'), \
             patch.object(signin, 'copilot_models', AsyncMock(return_value=['other-model'])), \
             self.assertRaisesRegex(signin.SignInError, 'fixed for the live run'):
            signin.sign_in(self.root)
        self.assertEqual(live.read_bytes(), before)


class SDKAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_denial_is_safe_and_distinct_from_failed_sign_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'runtime').write_text('SDK is mocked')
            client = create_autospec(CopilotClient, instance=True)
            client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=True)
            client.list_models.side_effect = RuntimeError('403 Forbidden: private-provider-response')
            with patch.object(signin, 'CopilotClient', return_value=client):
                with self.assertRaises(signin.SignInError) as error:
                    await signin.copilot_models(root, 'runtime')
            self.assertEqual(error.exception.code, 'access_denied')
            self.assertNotIn('private-provider-response', str(error.exception))
            client.create_session.assert_not_called()
            client.stop.assert_awaited_once()

    async def test_authentication_and_models_never_create_a_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'runtime').write_text('SDK is mocked')
            client = create_autospec(CopilotClient, instance=True)
            client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=True)
            client.list_models.return_value = [SimpleNamespace(id='model-b'), SimpleNamespace(id='model-a')]
            with patch.object(signin, 'CopilotClient', return_value=client) as constructor:
                self.assertEqual(await signin.copilot_models(root, 'runtime'), ['model-a', 'model-b'])
            self.assertTrue(constructor.call_args.kwargs['use_logged_in_user'])
            self.assertEqual(constructor.call_args.kwargs['mode'], 'empty')
            self.assertEqual(constructor.call_args.kwargs['base_directory'], str(root / '.runtime/copilot-state'))
            client.create_session.assert_not_called()
            client.stop.assert_awaited_once()

    async def test_missing_authentication_does_not_query_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'runtime').write_text('SDK is mocked')
            client = create_autospec(CopilotClient, instance=True)
            client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=False)
            with patch.object(signin, 'CopilotClient', return_value=client):
                self.assertIsNone(await signin.copilot_models(root, 'runtime'))
            client.list_models.assert_not_called()
            client.create_session.assert_not_called()
            client.stop.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
