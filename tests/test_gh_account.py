#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills/github-work-accountability/scripts/gh_account.py"


class GitHubAccountHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "gh-command.json"
        stub = self.bin / "gh"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['auth', 'token']:\n"
            "    if os.environ.get('STUB_EMPTY_TOKEN'):\n"
            "        raise SystemExit(0)\n"
            "    print('fixture-token')\n"
            "elif args[:2] == ['api', 'user']:\n"
            "    print(os.environ.get('STUB_LOGIN', 'ExpectedUser'))\n"
            "else:\n"
            "    Path = __import__('pathlib').Path\n"
            "    Path(os.environ['STUB_LOG']).write_text(json.dumps({\n"
            "        'args': args, 'host': os.environ.get('GH_HOST'),\n"
            "        'token': os.environ.get('GH_TOKEN')\n"
            "    }))\n"
            "    raise SystemExit(int(os.environ.get('STUB_COMMAND_EXIT', '0')))\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_helper(self, *arguments: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(extra_env)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["STUB_LOG"] = str(self.log)
        return subprocess.run(
            [sys.executable, str(HELPER), *arguments],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runs_command_with_named_identity_and_exact_arguments(self) -> None:
        result = self.run_helper(
            "--user", "ExpectedUser", "--host", "example.test", "--", "issue", "list", "--json", "title"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        invocation = json.loads(self.log.read_text())
        self.assertEqual(invocation["args"], ["issue", "list", "--json", "title"])
        self.assertEqual(invocation["host"], "example.test")
        self.assertEqual(invocation["token"], "fixture-token")

    def test_refuses_identity_mismatch_before_command(self) -> None:
        result = self.run_helper("--user", "ExpectedUser", "--", "issue", "list", STUB_LOGIN="OtherUser")
        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing command", result.stderr)
        self.assertFalse(self.log.exists())

    def test_refuses_empty_stored_token(self) -> None:
        result = self.run_helper(
            "--user", "ExpectedUser", "--", "issue", "list", STUB_EMPTY_TOKEN="1"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("returned no stored token", result.stderr)
        self.assertFalse(self.log.exists())

    def test_propagates_gh_command_exit_status(self) -> None:
        result = self.run_helper(
            "--user", "ExpectedUser", "--", "issue", "list", STUB_COMMAND_EXIT="7"
        )
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
