"""The launcher finds app source independently of shell or editable-install state."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_launcher_from_another_directory_without_pythonpath(self):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, '-B', str(ROOT / 'tools/launch_web.py'), '--check'],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Application imports OK', result.stdout)

    def test_missing_source_is_explained_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            shutil.copyfile(ROOT / 'tools/launch_web.py', root / 'tools/launch_web.py')
            result = subprocess.run([sys.executable, '-B', str(root / 'tools/launch_web.py'), '--check'],
                                    capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('application files are missing', result.stderr)
        self.assertIn('cannot recreate the application', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
