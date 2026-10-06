import shutil
import subprocess
import unittest
from pathlib import Path


class WebRegressionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for UI regression tests")
    def test_ui_workflows(self):
        result = subprocess.run([shutil.which("node"), "--test", "tests/web_app.test.cjs"],
                                cwd=Path(__file__).resolve().parent.parent,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
