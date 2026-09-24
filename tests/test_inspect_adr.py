#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "skills/github-work-accountability/scripts/inspect_adr.py"


def run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


class AdrInspectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.assertEqual(run("git", "init", "-b", "main", cwd=self.repo).returncode, 0)
        self.assertEqual(run("git", "config", "user.name", "Fixture", cwd=self.repo).returncode, 0)
        self.assertEqual(
            run("git", "config", "user.email", "fixture@example.invalid", cwd=self.repo).returncode,
            0,
        )
        (self.repo / "docs/adr").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, message: str = "fixture") -> str:
        self.assertEqual(run("git", "add", ".", cwd=self.repo).returncode, 0)
        self.assertEqual(run("git", "commit", "-m", message, cwd=self.repo).returncode, 0)
        return run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()

    def inspect(self, path: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return run(sys.executable, str(INSPECTOR), path, "--repo", str(self.repo), *extra, cwd=ROOT)

    def test_frontmatter_and_prose_keep_decision_and_execution_separate(self) -> None:
        path = self.repo / "docs/adr/ADR-059-example.md"
        path.write_text(
            "---\n"
            "id: ADR-059\n"
            "title: \"Example\"\n"
            "status: accepted\n"
            "date: 2026-08-26\n"
            "updated: 2026-09-04\n"
            "depends-on: [ADR-018]\n"
            "---\n"
            "# ADR-059: Example\n\n"
            "**Decision status:** Accepted by the product owner.\n\n"
            "**Execution status:** Required/open. Nothing is built.\n",
            encoding="utf-8",
        )
        commit = self.commit()
        result = self.inspect("docs/adr/ADR-059-example.md", "--ref", commit, "--require-status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["metadata"]["decision_status"]["normalized"], "accepted")
        self.assertEqual(report["metadata"]["execution_status"]["normalized"], "required/open")
        self.assertEqual(report["metadata"]["relations"]["depends_on"], ["ADR-018"])
        self.assertEqual(report["source"]["commit"], commit)

    def test_markdown_metadata_is_supported_without_frontmatter(self) -> None:
        path = self.repo / "docs/adr/ADR-021-example.md"
        path.write_text(
            "# ADR-021: Example\n\n"
            "**Status**: **partly implemented** — remaining work is listed below.\n"
            "**Date**: 2026-09-23\n"
            "**Updated**: 2026-09-24\n",
            encoding="utf-8",
        )
        self.commit()
        result = self.inspect("docs/adr/ADR-021-example.md", "--require-status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["metadata"]["format"], "markdown")
        self.assertEqual(report["metadata"]["decision_status"]["normalized"], "partly implemented")
        self.assertIn("no byte-zero frontmatter", " ".join(report["warnings"]))

    def test_conflicting_status_declarations_fail_closed(self) -> None:
        path = self.repo / "docs/adr/ADR-007-conflict.md"
        path.write_text(
            "---\nid: ADR-007\nstatus: accepted\n---\n"
            "# ADR-007: Conflict\n\n**Status**: proposed\n",
            encoding="utf-8",
        )
        self.commit()
        result = self.inspect("docs/adr/ADR-007-conflict.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflicting decision status declarations", result.stdout)

    def test_working_tree_change_is_detected_before_commit(self) -> None:
        path = self.repo / "docs/adr/ADR-001-drift.md"
        path.write_text("# ADR-001: Drift\n\n**Status**: proposed\n", encoding="utf-8")
        self.commit()
        path.write_text("# ADR-001: Drift\n\n**Status**: accepted\n", encoding="utf-8")
        result = self.inspect(
            "docs/adr/ADR-001-drift.md", "--working-tree", "--against", "HEAD"
        )
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertTrue(report["source"]["changed"])
        self.assertIn("ADR source changed", result.stdout)

    def test_committed_change_is_detected_against_recorded_revision(self) -> None:
        path = self.repo / "docs/adr/ADR-002-drift.md"
        path.write_text("# ADR-002: Drift\n\n**Status**: proposed\n", encoding="utf-8")
        original = self.commit("original")
        path.write_text("# ADR-002: Drift\n\n**Status**: accepted\n", encoding="utf-8")
        self.commit("changed")
        result = self.inspect("docs/adr/ADR-002-drift.md", "--ref", original, "--against", "HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(json.loads(result.stdout)["source"]["changed"])

    def test_working_tree_hash_uses_repository_text_filters(self) -> None:
        (self.repo / ".gitattributes").write_text("*.md text eol=lf\n", encoding="utf-8")
        path = self.repo / "docs/adr/ADR-003-line-endings.md"
        text = "# ADR-003: Line endings\n\n**Status**: proposed\n"
        path.write_text(text, encoding="utf-8")
        self.commit()
        path.write_bytes(text.replace("\n", "\r\n").encode())
        result = self.inspect(
            "docs/adr/ADR-003-line-endings.md", "--working-tree", "--against", "HEAD"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["source"]["changed"])

    def test_underscores_and_lower_heading_levels_are_preserved(self) -> None:
        path = self.repo / "docs/adr/ADR_059-example.md"
        path.write_text(
            "---\nid: ADR_059\nstatus: in_progress\nextra:\n  owner: team_one\n---\n"
            "## ADR_059: Example_name\n",
            encoding="utf-8",
        )
        self.commit()
        result = self.inspect("docs/adr/ADR_059-example.md", "--require-status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["metadata"]["id"], "ADR_059")
        self.assertEqual(report["metadata"]["title"], "Example_name")
        self.assertEqual(report["metadata"]["decision_status"]["raw"], "in_progress")
        self.assertTrue(any("ignored nested" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
