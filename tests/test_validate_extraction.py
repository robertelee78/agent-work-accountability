#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "skills/github-work-accountability/scripts/validate_extraction.py"


def run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


class ExtractionValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.assertEqual(run("git", "init", "-b", "main", cwd=self.repo).returncode, 0)
        self.assertEqual(run("git", "config", "user.name", "Fixture", cwd=self.repo).returncode, 0)
        self.assertEqual(
            run("git", "config", "user.email", "fixture@example.invalid", cwd=self.repo).returncode,
            0,
        )
        plan = self.repo / "docs/plans/PLAN-001.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(
            "# PLAN-001: Example\n\n"
            "1. Produce the first observable outcome.\n"
            "2. Prove the outcome against the acceptance boundary.\n",
            encoding="utf-8",
        )
        self.assertEqual(run("git", "add", ".", cwd=self.repo).returncode, 0)
        self.assertEqual(run("git", "commit", "-m", "fixture", cwd=self.repo).returncode, 0)
        self.commit = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.blob = run(
            "git", "rev-parse", f"{self.commit}:docs/plans/PLAN-001.md", cwd=self.repo
        ).stdout.strip()
        base = "example/portable:PLAN-001"
        self.manifest = {
            "schema": "github-work-accountability/extraction-v1",
            "source": {
                "kind": "plan",
                "path": "docs/plans/PLAN-001.md",
                "commit": self.commit,
                "blob": self.blob,
            },
            "epic": {
                "key": base,
                "title": "Example outcome",
                "source_quotes": ["# PLAN-001: Example"],
            },
            "stories": [
                {
                    "key": f"{base}:produce",
                    "title": "Produce outcome",
                    "source_quotes": ["1. Produce the first observable outcome."],
                    "outcome": "The outcome exists.",
                    "acceptance": ["The outcome can be observed."],
                    "dependencies": [],
                },
                {
                    "key": f"{base}:prove",
                    "title": "Prove outcome",
                    "source_quotes": [
                        "2. Prove the outcome against the acceptance boundary."
                    ],
                    "outcome": "The outcome is accepted.",
                    "acceptance": ["The acceptance proof passes."],
                    "dependencies": [f"{base}:produce"],
                },
            ],
            "coverage": [
                {
                    "source_quote": "1. Produce the first observable outcome.",
                    "stories": [f"{base}:produce"],
                },
                {
                    "source_quote": "2. Prove the outcome against the acceptance boundary.",
                    "stories": [f"{base}:prove"],
                },
            ],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self, name: str = "manifest.json") -> Path:
        path = self.repo / name
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        return path

    def validate(self, manifest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return run("python", str(VALIDATOR), str(manifest), "--repo", str(self.repo), *extra, cwd=ROOT)

    def test_valid_source_bound_graph(self) -> None:
        result = self.validate(self.write_manifest(), "--against", self.commit)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_changed_source_requires_reconciliation(self) -> None:
        manifest = self.write_manifest()
        plan = self.repo / "docs/plans/PLAN-001.md"
        plan.write_text(plan.read_text() + "\nChanged requirement.\n", encoding="utf-8")
        self.assertEqual(run("git", "add", ".", cwd=self.repo).returncode, 0)
        self.assertEqual(run("git", "commit", "-m", "change", cwd=self.repo).returncode, 0)
        result = self.validate(manifest, "--against", "HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("source changed at HEAD", result.stdout)

    def test_dependency_cycle_is_rejected(self) -> None:
        self.manifest["stories"][0]["dependencies"] = [self.manifest["stories"][1]["key"]]
        result = self.validate(self.write_manifest())
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependency cycle:", result.stdout)


if __name__ == "__main__":
    unittest.main()
