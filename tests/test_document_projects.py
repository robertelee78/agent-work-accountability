#!/usr/bin/env python3
"""User-level scenarios for one GitHub Project per planning document.

Every test runs the real reconciler CLI against github_sim and checks what a
person or agent actually sees afterwards: which cards sit in which board
column, how the section table groups, where issue links point, which old
boards are closed, and what the command printed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_sim as sim  # noqa: E402
from scenario import PRIORITIES, REPO, World, evidence, legacy_board, release_document, release_evidence, v3_manifest  # noqa: E402


KEY = f"{REPO}:PRD-001"


def prd_with_section_boards(world: World) -> dict[str, int]:
    """PRD-001 as the 0.7.3 skill left it: three section epics, no root, one board each."""
    state = world.state
    n: dict[str, int] = {}
    n["s1"] = sim.add_issue(state, REPO, "PRD-001 §3.1: Direct mode", work_key=f"{KEY}:S3.1")
    n["a"] = sim.add_issue(state, REPO, "Direct handshake", work_key=f"{KEY}:S3.1:handshake", parent=n["s1"])
    n["b"] = sim.add_issue(state, REPO, "Direct rekey", work_key=f"{KEY}:S3.1:rekey", parent=n["s1"])
    n["s2"] = sim.add_issue(state, REPO, "PRD-001 §3.2: Relay mode", work_key=f"{KEY}:S3.2")
    n["c"] = sim.add_issue(state, REPO, "Relay fallback", work_key=f"{KEY}:S3.2:fallback", parent=n["s2"],
                           labels=["phase/acceptance", "area/relay"])
    n["s3"] = sim.add_issue(state, REPO, "PRD-001 §3.3: Anchors", work_key=f"{KEY}:S3.3")
    n["d"] = sim.add_issue(state, REPO, "Anchor store", work_key=f"{KEY}:S3.3:store", parent=n["s3"])
    n["e"] = sim.add_issue(state, REPO, "Anchor quota removal", work_key=f"{KEY}:S3.3:quota", parent=n["s3"])
    n["other"] = sim.add_issue(state, REPO, "ADR-9 unrelated epic", work_key=f"{REPO}:ADR-9")
    boards = {
        "s1": legacy_board(world, "vox — §3.1", n["s1"], f"{KEY}:S3.1", {
            n["a"]: {"Work phase": "Ready", "Health": "On track", "Source freshness": "Current", "Priority": "High", "Rank": 1},
            n["b"]: {"Work phase": "Executing", "Health": "At risk", "Source freshness": "Current", "Priority": "High", "Rank": 2},
        }),
        "s2": legacy_board(world, "vox — §3.2", n["s2"], f"{KEY}:S3.2", {
            n["c"]: {"Work phase": "Acceptance", "Health": "On track", "Source freshness": "Current", "Priority": "Medium", "Rank": 1},
        }),
        "s3": legacy_board(world, "vox — §3.3", n["s3"], f"{KEY}:S3.3", {
            n["d"]: {"Work phase": "Done", "Health": "On track", "Source freshness": "Current", "Priority": "Low", "Rank": 1},
            n["e"]: {"Work phase": "Ready", "Health": "Blocked", "Source freshness": "Current", "Priority": "Low", "Rank": 2},
        }),
    }
    for name, board in boards.items():
        n[f"board_{name}"] = board["number"]
    world.bases = []  # type: ignore[attr-defined]
    for section, stories in (
        ("s1", {n["a"]: (f"{KEY}:S3.1:handshake", "Ready"), n["b"]: (f"{KEY}:S3.1:rekey", "Executing")}),
        ("s2", {n["c"]: (f"{KEY}:S3.2:fallback", "Acceptance")}),
        ("s3", {n["d"]: (f"{KEY}:S3.3:store", "Done"), n["e"]: (f"{KEY}:S3.3:quota", "Ready")}),
    ):
        key = {"s1": f"{KEY}:S3.1", "s2": f"{KEY}:S3.2", "s3": f"{KEY}:S3.3"}[section]
        path = world.write_manifest(v3_manifest(key, n[section], stories), f"v3-{section}.json")
        world.bases.append(str(path))  # type: ignore[attr-defined]
    world.save()
    return n


def create_root(world: World) -> int:
    """What the vox agent does first: create the PRD-001 root epic issue."""
    number = sim.add_issue(world.state, REPO, "PRD-001: Legitimate transport modes", work_key=KEY)
    world.save()
    return number


def base_args(world: World) -> list[str]:
    args: list[str] = []
    for path in world.bases:  # type: ignore[attr-defined]
        args += ["--base", path]
    return args


def migrate(world: World, n: dict[str, int]) -> tuple[dict, dict]:
    root = create_root(world)
    n["root"] = root
    draft = world.draft(root, "--include", f"{n['s1']},{n['s2']},{n['s3']}", *base_args(world))
    receipt = world.apply(draft, "--attach-parents")
    return draft, receipt


class PrdMigrationTest(unittest.TestCase):
    """The vox PRD-001 case: merge per-section boards into one document board."""

    def setUp(self) -> None:
        self.world = World()
        self.n = prd_with_section_boards(self.world)

    def tearDown(self) -> None:
        self.world.close()

    def test_one_board_shows_every_story_by_phase_and_old_boards_close(self) -> None:
        world, n = self.world, self.n
        draft, receipt = migrate(world, n)
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(sorted(draft["supersedes"]), sorted([n["board_s1"], n["board_s2"], n["board_s3"]]))

        board = receipt["project_number"]
        # A person opening the new board sees every story in its phase column, and no epics.
        self.assertEqual(
            world.board(board, "Lifecycle"),
            {
                "Ready": [n["a"], n["e"]],
                "Executing": [n["b"]],
                "Acceptance": [n["c"]],
                "Done": [n["d"]],
            },
        )
        # The section table groups the whole document, sections carrying their progress.
        groups = world.board(board, "By section")
        self.assertEqual(groups["§3.1: Direct mode"], [n["s1"], n["a"], n["b"]])
        self.assertEqual(groups["§3.2: Relay mode"], [n["s2"], n["c"]])
        self.assertEqual(groups["§3.3: Anchors"], [n["s3"], n["d"], n["e"]])
        self.assertEqual(groups["PRD-001 (general)"], [n["root"]])
        self.assertEqual(world.value(board, n["s3"], "Progress"), "1/2 Done · 1 blocked")
        self.assertEqual(world.value(board, n["s3"], "Health"), "Blocked")
        self.assertEqual(world.value(board, n["root"], "Progress"), "1/5 Done · 1 blocked · 1 at risk")
        # Lifecycle is the first tab; GitHub's empty starter table is gone.
        views = [v["name"] for v in world.project(board)["views"]]
        self.assertEqual(views, ["Lifecycle", "By section", "By release"])

        # The sections now sit under the root on GitHub.
        for section in ("s1", "s2", "s3"):
            self.assertEqual(world.issue(n[section])["parent"], n["root"])
        # Every issue links to the new board; human prose and unrelated labels survive.
        for key in ("root", "s1", "a", "b", "s2", "c", "s3", "d", "e"):
            self.assertIn(f"Project: {receipt['lifecycle_url']}", world.issue(n[key])["body"])
        self.assertIn("Human description of Relay fallback.", world.issue(n["c"])["body"])
        self.assertEqual(world.issue(n["c"])["labels"], ["area/relay"])

        # Old boards are closed, not deleted, and say where the work went.
        for section in ("s1", "s2", "s3"):
            old = world.project(n[f"board_{section}"])
            self.assertTrue(old["closed"])
            self.assertIn(f"Superseded by {receipt['project_url']}", old["readme"])
            self.assertTrue(old["items"], "old board items must be untouched")
        self.assertEqual(len(receipt["snapshots"]), 3)
        for snapshot in receipt["snapshots"]:
            self.assertTrue(Path(snapshot["path"]).exists())

        # Running the same manifest again changes nothing, and a dry run says so.
        again = world.apply(draft, "--attach-parents")
        self.assertTrue(again["verified"])
        self.assertEqual(again["applied_mutations"], [])
        dry = world.reconcile("--manifest", str(world.write_manifest(draft)))
        self.assertEqual(json.loads(dry.stdout)["planned_mutations"], [])


    def test_disagreeing_old_board_stops_before_any_write(self) -> None:
        world, n = self.world, self.n
        root = create_root(world)
        draft = world.draft(root, "--include", f"{n['s1']},{n['s2']},{n['s3']}", *base_args(world))
        story = next(i for i in draft["items"] if i["number"] == n["c"])
        story["work_phase"] = "Executing"  # the old board says Acceptance
        story["evidence"]["attempt"]["state"] = "active"
        del story["evidence"]["candidate"]
        before = world.mutations()
        result = world.reconcile("--manifest", str(world.write_manifest(draft)), "--apply", "--attach-parents")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"Project #{n['board_s2']} #{n['c']} Work phase: old board shows 'Acceptance', manifest wants 'Executing'", result.stderr)
        self.assertIn("nothing was written", result.stderr)
        self.assertEqual(world.mutations(), before)
        self.assertFalse(any(world.project(n[f"board_{s}"])["closed"] for s in ("s1", "s2", "s3")))
        self.assertIsNone(world.issue(n["s1"])["parent"])

    def test_acknowledged_change_moves_the_story_on_the_new_board(self) -> None:
        world, n = self.world, self.n
        root = create_root(world)
        draft = world.draft(root, "--include", f"{n['s1']},{n['s2']},{n['s3']}", *base_args(world))
        story = next(i for i in draft["items"] if i["number"] == n["b"])
        story["work_phase"] = "Ready"  # the recorded attempt was abandoned
        story["evidence"] = {"design_approval": story["evidence"]["design_approval"]}
        draft["acknowledged_changes"] = [{
            "project": n["board_s1"], "number": n["b"], "field": "Work phase",
            "from": "Executing", "to": "Ready", "reason": "attempt abandoned; no durable attempt event",
        }]
        receipt = world.apply(draft, "--attach-parents")
        self.assertEqual(world.board(receipt["project_number"])["Ready"], [n["a"], n["b"], n["e"]])

    def test_a_crash_after_any_write_resumes_to_the_same_board(self) -> None:
        baseline_world = World()
        try:
            baseline_n = prd_with_section_boards(baseline_world)
            _draft, receipt = migrate(baseline_world, baseline_n)
            expected = {
                "lifecycle": baseline_world.board(receipt["project_number"], "Lifecycle"),
                "section": baseline_world.board(receipt["project_number"], "By section"),
            }
            total = baseline_world.state["write_calls"]
        finally:
            baseline_world.close()

        def crash_then_resume(crash_at: int) -> tuple[int, str | None]:
            world = World()
            try:
                n = prd_with_section_boards(world)
                root = create_root(world)
                draft = world.draft(root, "--include", f"{n['s1']},{n['s2']},{n['s3']}", *base_args(world))
                world.state["write_calls"] = 0
                world.state["crash_at"] = crash_at
                path = world.write_manifest(draft)
                first = world.reconcile("--manifest", str(path), "--apply", "--attach-parents")
                if first.returncode == 0:
                    return crash_at, "the injected crash did not surface as a failure"
                second = world.reconcile("--manifest", str(path), "--apply", "--attach-parents")
                if second.returncode != 0:
                    return crash_at, second.stderr
                project = json.loads(second.stdout)["project_number"]
                seen = {"lifecycle": world.board(project, "Lifecycle"), "section": world.board(project, "By section")}
                if seen != expected:
                    return crash_at, f"board differs: {seen}"
                open_boards = [p["number"] for p in world.state["projects"] if not p["closed"]]
                if open_boards != [project]:
                    return crash_at, f"open boards after resume: {open_boards}"
                return crash_at, None
            finally:
                world.close()

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            results = list(pool.map(crash_then_resume, range(1, total + 1)))
        failures = [f"write {n}: {problem}" for n, problem in results if problem]
        self.assertEqual(failures, [], "\n".join(failures))

    def test_old_board_edited_mid_migration_keeps_old_boards_open(self) -> None:
        world, n = self.world, self.n
        root = create_root(world)
        draft = world.draft(root, "--include", f"{n['s1']},{n['s2']},{n['s3']}", *base_args(world))
        world.state["write_calls"] = 0
        world.state["crash_at"] = 1  # interrupted right after the first write
        path = world.write_manifest(draft)
        self.assertNotEqual(world.reconcile("--manifest", str(path), "--apply", "--attach-parents").returncode, 0)
        board = world.project(n["board_s1"])  # someone reorders the old board meanwhile
        item = next(i for i in board["items"] if i["number"] == n["a"])
        sim.set_value(board, item, "Rank", 9)
        result = world.reconcile("--manifest", str(path), "--apply", "--attach-parents")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"old boards changed after they were checked: Project #{n['board_s1']} #{n['a']}", result.stderr)
        self.assertFalse(any(world.project(n[f"board_{s}"])["closed"] for s in ("s1", "s2", "s3")))

    def test_stale_agent_board_for_a_section_is_caught(self) -> None:
        world, n = self.world, self.n
        _draft, receipt = migrate(world, n)
        # A session still running 0.7.3 makes a new per-section board.
        stray = legacy_board(world, "vox — §3.1 again", n["s1"], f"{KEY}:S3.1", {})
        world.save()
        again = world.draft(n["root"])
        result = world.reconcile("--manifest", str(world.write_manifest(again)), "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)  # the draft lists it in supersedes
        self.assertIn(stray["number"], again["supersedes"])
        self.assertTrue(world.project(stray["number"])["closed"])
        # Without listing it, the run refuses and names the stray board.
        stray2 = legacy_board(world, "vox — §3.2 again", n["s2"], f"{KEY}:S3.2", {})
        world.save()
        again["supersedes"] = []
        result = world.reconcile("--manifest", str(world.write_manifest(again)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{stray2['number']} ({KEY}:S3.2)", result.stderr)
        self.assertEqual(receipt["project_number"], json.loads(world.reconcile(
            "--manifest", str(world.write_manifest(world.draft(n["root"]))), "--apply").stdout)["project_number"])

    def test_a_section_cannot_get_its_own_board_again(self) -> None:
        world, n = self.world, self.n
        migrate(world, n)
        section_manifest = {
            "schema": "github-work-accountability/project-v4",
            "repository": REPO,
            "scope": {"root_number": n["s1"], "root_work_key": f"{KEY}:S3.1"},
            "project": {"owner": "acme", "priority_options": PRIORITIES},
            "observed": {str(n["s1"]): {}, str(n["a"]): {}, str(n["b"]): {}},
            "items": [
                {"number": n["s1"], "work_key": f"{KEY}:S3.1", "kind": "epic"},
                {"number": n["a"], "work_key": f"{KEY}:S3.1:handshake", "parent": f"{KEY}:S3.1", "work_phase": "Backlog"},
                {"number": n["b"], "work_key": f"{KEY}:S3.1:rekey", "parent": f"{KEY}:S3.1", "work_phase": "Backlog"},
            ],
        }
        before = world.mutations()
        result = world.reconcile("--manifest", str(world.write_manifest(section_manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['s1']} belongs to managed epic #{n['root']}", result.stderr)
        self.assertEqual(world.mutations(), before)


class TwoAgentsTest(unittest.TestCase):
    """The lost-update guard, seen from two agents sharing one document board."""

    def setUp(self) -> None:
        self.world = World()
        self.n = prd_with_section_boards(self.world)
        _draft, self.receipt = migrate(self.world, self.n)
        self.board = self.receipt["project_number"]

    def tearDown(self) -> None:
        self.world.close()

    def test_older_manifest_cannot_undo_newer_progress(self) -> None:
        world, n = self.world, self.n
        agent_a = world.draft(n["root"], *base_args(world))
        agent_b = world.draft(n["root"], *base_args(world))
        # Agent B starts story a.
        story = next(i for i in agent_b["items"] if i["number"] == n["a"])
        story["work_phase"] = "Executing"
        story["evidence"] = evidence(story["work_key"], "Executing", attempt="attempt-b-1")
        world.apply(agent_b)
        self.assertIn(n["a"], world.board(self.board)["Executing"])
        # Agent A, working from its older read, marks story e as no longer blocked.
        story = next(i for i in agent_a["items"] if i["number"] == n["e"])
        story["health"] = "On track"
        receipt = world.apply(agent_a)
        self.assertIn(n["a"], world.board(self.board)["Executing"], "A must not move a back to Ready")
        self.assertEqual(world.value(self.board, n["e"], "Health"), "On track")
        self.assertTrue(any(f"#{n['a']} Work phase stays 'Executing'" in note for note in receipt["kept_live_values"]))
        # Agent A now tries to change story a itself from its stale read: refused, nothing written.
        story = next(i for i in agent_a["items"] if i["number"] == n["a"])
        story["priority"] = "Low"
        story["work_phase"] = "Designing"
        story["evidence"] = {}
        before = world.mutations()
        result = world.reconcile("--manifest", str(world.write_manifest(agent_a)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['a']} Work phase: you read 'Ready', GitHub now shows 'Executing', you want 'Designing'", result.stderr)
        self.assertIn("Re-run --draft", result.stderr)
        self.assertEqual(world.mutations(), before)

    def test_people_keep_their_own_views_and_renamed_sections_keep_their_cards(self) -> None:
        world, n = self.world, self.n
        board = world.project(self.board)
        sim.add_view(world.state, board, "My triage", "TABLE_LAYOUT")
        world.save()
        manifest = world.draft(n["root"], *base_args(world))
        section = next(i for i in manifest["items"] if i["number"] == n["s2"])
        section["section_label"] = "§3.2: Relay and fallback"
        world.apply(manifest)
        self.assertEqual([v["name"] for v in world.project(self.board)["views"]], ["Lifecycle", "By section", "By release", "My triage"])
        groups = world.board(self.board, "By section")
        self.assertEqual(groups["§3.2: Relay and fallback"], [n["s2"], n["c"]])
        self.assertNotIn("§3.2: Relay mode", groups)


class NewDocumentTest(unittest.TestCase):
    """A planning document tracked from scratch, with a subsection and a general story."""

    def setUp(self) -> None:
        world = self.world = World()
        state = world.state
        n = self.n = {}
        n["root"] = sim.add_issue(state, REPO, "ADR-12: Release hardening", work_key=f"{REPO}:ADR-12")
        n["general"] = sim.add_issue(state, REPO, "Write the threat model", work_key=f"{REPO}:ADR-12:threats", parent=n["root"])
        n["sec"] = sim.add_issue(state, REPO, "ADR-12 §2: Signing", work_key=f"{REPO}:ADR-12:S2", parent=n["root"])
        n["sub"] = sim.add_issue(state, REPO, "§2.1 Key custody", work_key=f"{REPO}:ADR-12:S2.1", parent=n["sec"])
        n["deep"] = sim.add_issue(state, REPO, "Rotate keys", work_key=f"{REPO}:ADR-12:S2.1:rotate", parent=n["sub"])
        n["sign"] = sim.add_issue(state, REPO, "Sign releases", work_key=f"{REPO}:ADR-12:S2:sign", parent=n["sec"])
        n["drafted"] = sim.add_issue(state, REPO, "Unmanaged note", work_key=None)
        world.save()

    def tearDown(self) -> None:
        self.world.close()

    def test_dry_run_writes_nothing_then_apply_builds_the_board(self) -> None:
        world, n = self.world, self.n
        manifest = world.draft(n["root"])
        before = world.mutations()
        dry = world.reconcile("--manifest", str(world.write_manifest(manifest)))
        self.assertEqual(dry.returncode, 0, dry.stderr)
        plan = json.loads(dry.stdout)
        self.assertIn("create document Project linked to repository", plan["planned_mutations"])
        self.assertEqual(world.mutations(), before)
        self.assertEqual(world.state["projects"], [])

        for item in manifest["items"]:
            if item["number"] == n["deep"]:
                item["work_phase"] = "Ready"
                item["evidence"] = evidence(item["work_key"], "Ready")
        receipt = world.apply(manifest)
        board = receipt["project_number"]
        self.assertEqual(world.board(board), {"Backlog": [n["general"], n["sign"]], "Ready": [n["deep"]]})
        groups = world.board(board, "By section")
        self.assertEqual(groups["ADR-12 (general)"], [n["root"], n["general"]])
        self.assertEqual(groups["§2: Signing"], [n["sec"], n["sub"], n["deep"], n["sign"]])
        self.assertEqual(world.value(board, n["sub"], "Progress"), "0/1 Done")
        self.assertEqual(world.value(board, n["root"], "Progress"), "0/3 Done")

    def test_a_dry_run_after_apply_previews_nothing_until_something_drifts(self) -> None:
        world, n = self.world, self.n
        manifest = world.draft(n["root"])
        receipt = world.apply(manifest)
        self.assertTrue(receipt["verified"])

        def preview() -> list[str]:
            result = world.reconcile("--manifest", str(world.write_manifest(manifest)))
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["planned_mutations"]

        before = world.mutations()
        self.assertEqual(preview(), [])
        # Someone breaks one issue's Lifecycle link.
        record = world.issue(n["sign"])
        record["body"] = record["body"].replace(receipt["lifecycle_url"], receipt["project_url"] + "/views/99")
        world.save()
        self.assertEqual(preview(), [f"bind issue #{n['sign']} to Project fields"])
        self.assertEqual(world.mutations(), before, "a dry run never writes")
        again = world.apply(manifest)
        self.assertTrue(again["verified"])
        self.assertEqual(again["applied_mutations"], [f"bind issue #{n['sign']} to Project fields"])
        self.assertIn(f"Project: {receipt['lifecycle_url']}", world.issue(n["sign"])["body"])
        self.assertEqual(preview(), [])

    def test_a_person_adding_an_unmanaged_card_does_not_break_the_board(self) -> None:
        world, n = self.world, self.n
        receipt = world.apply(world.draft(n["root"]))
        board = world.project(receipt["project_number"])
        sim.add_item(world.state, board, REPO, n["drafted"])
        board["items"].append({"id": "PVTI_draft", "draft": "Call the lawyer", "repository": None, "number": None, "archived": False, "values": {}})
        world.save()
        again = world.apply(world.draft(n["root"]))
        self.assertTrue(again["verified"])
        self.assertIn("DraftIssue Call the lawyer", again["unmanaged_items"])

    def test_tree_deeper_than_three_levels_is_refused(self) -> None:
        world, n = self.world, self.n
        n["too_deep"] = sim.add_issue(world.state, REPO, "Too deep", work_key=f"{REPO}:ADR-12:S2.1:rotate:x", parent=n["deep"])
        world.save()
        manifest = world.draft(n["root"])
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['too_deep']} sits 4 levels below the root", result.stderr)

    def test_manifest_must_match_the_github_tree(self) -> None:
        world, n = self.world, self.n
        manifest = world.draft(n["root"])
        item = next(i for i in manifest["items"] if i["number"] == n["sign"])
        item["parent"] = f"{REPO}:ADR-12:S2.1"
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['sign']} is under #{n['sec']} on GitHub, manifest says #{n['sub']}", result.stderr)
        manifest = world.draft(n["root"])
        manifest["items"] = [i for i in manifest["items"] if i["number"] != n["sign"]]
        del manifest["observed"][str(n["sign"])]
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"omits native sub-issues of its epics: #{n['sign']} (under #{n['sec']})", result.stderr)

    def test_low_budget_stops_before_writing(self) -> None:
        world, n = self.world, self.n
        world.state["rate"]["graphql"] = 60
        result = world.reconcile("--manifest", str(world.write_manifest(world.draft(n["root"]))), "--apply")
        self.assertEqual(result.returncode, 75)
        self.assertIn("rerun after reset", result.stderr)
        self.assertEqual(world.state["projects"], [])


class LegacyEpicBoardTest(unittest.TestCase):
    """An ADR tracked by 0.7.3 as one epic with direct stories upgrades in place."""

    def test_upgrade_keeps_values_and_replaces_the_lifecycle_filter(self) -> None:
        world = World()
        try:
            state = world.state
            root = sim.add_issue(state, REPO, "ADR-59: Sync", work_key=f"{REPO}:ADR-59")
            one = sim.add_issue(state, REPO, "Sync engine", work_key=f"{REPO}:ADR-59:engine", parent=root)
            two = sim.add_issue(state, REPO, "Sync UI", work_key=f"{REPO}:ADR-59:ui", parent=root)
            old = legacy_board(world, "vox — ADR-59", root, f"{REPO}:ADR-59", {
                one: {"Work phase": "Executing", "Health": "On track", "Source freshness": "Current", "Priority": "High", "Rank": 1},
                two: {"Work phase": "Backlog", "Health": "On track", "Source freshness": "Current", "Priority": "Low", "Rank": 2},
            })
            base = world.write_manifest(v3_manifest(f"{REPO}:ADR-59", root, {
                one: (f"{REPO}:ADR-59:engine", "Executing"), two: (f"{REPO}:ADR-59:ui", "Backlog")}), "v3.json")
            world.save()
            stale = world.reconcile("--manifest", str(base), "--apply")
            self.assertEqual(stale.returncode, 2)
            self.assertIn("is no longer accepted; regenerate it as 'github-work-accountability/project-v4' with --draft", stale.stderr)

            manifest = world.draft(root, "--base", str(base))
            self.assertEqual(manifest["supersedes"], [])
            receipt = world.apply(manifest)
            self.assertEqual(receipt["project_number"], old["number"])
            self.assertEqual(world.board(old["number"]), {"Executing": [one], "Backlog": [two]})
            names = [v["name"] for v in world.project(old["number"])["views"]]
            self.assertEqual(names, ["Lifecycle", "By section", "By release"])
            lifecycle = next(v for v in world.project(old["number"])["views"] if v["name"] == "Lifecycle")
            self.assertEqual(lifecycle["filter"], "has:work-phase")
            self.assertIn(f"Project: {receipt['lifecycle_url']}", world.issue(one)["body"])
            self.assertTrue(any("outdated filter 'parent-issue:" in note for note in receipt["notes"]))
        finally:
            world.close()


def legacy_adr(world: World) -> tuple[int, int, int, dict]:
    """An ADR board as 0.7.3 left it: its only view is the direct-child Lifecycle board."""
    state = world.state
    root = sim.add_issue(state, REPO, "ADR-59: Sync", work_key=f"{REPO}:ADR-59")
    one = sim.add_issue(state, REPO, "Sync engine", work_key=f"{REPO}:ADR-59:engine", parent=root)
    two = sim.add_issue(state, REPO, "Sync UI", work_key=f"{REPO}:ADR-59:ui", parent=root)
    old = legacy_board(world, "vox — ADR-59", root, f"{REPO}:ADR-59", {
        one: {"Work phase": "Executing", "Health": "On track", "Source freshness": "Current", "Priority": "High", "Rank": 1},
        two: {"Work phase": "Backlog", "Health": "On track", "Source freshness": "Current", "Priority": "Low", "Rank": 2},
    })
    base = world.write_manifest(v3_manifest(f"{REPO}:ADR-59", root, {
        one: (f"{REPO}:ADR-59:engine", "Executing"), two: (f"{REPO}:ADR-59:ui", "Backlog")}), "v3.json")
    world.save()
    return root, one, two, {"project": old, "base": str(base)}


class LegacyUpgradeTest(unittest.TestCase):
    """Upgrading a board whose only view is the old Lifecycle (GitHub keeps at least one view)."""

    def test_the_upgrade_survives_a_crash_after_any_write(self) -> None:
        baseline = World()
        try:
            root, one, two, legacy = legacy_adr(baseline)
            baseline.apply(baseline.draft(root, "--base", legacy["base"]))
            total = baseline.state["write_calls"]
        finally:
            baseline.close()

        def crash_then_resume(crash_at: int) -> str | None:
            world = World()
            try:
                root, one, two, legacy = legacy_adr(world)
                path = world.write_manifest(world.draft(root, "--base", legacy["base"]))
                world.state["write_calls"] = 0
                world.state["crash_at"] = crash_at
                if world.reconcile("--manifest", str(path), "--apply").returncode == 0:
                    return f"write {crash_at}: the injected crash did not surface"
                second = world.reconcile("--manifest", str(path), "--apply")
                if second.returncode != 0:
                    return f"write {crash_at}: {second.stderr}"
                number = legacy["project"]["number"]
                names = [v["name"] for v in world.project(number)["views"]]
                if names != ["Lifecycle", "By section", "By release"]:
                    return f"write {crash_at}: views {names}"
                if world.board(number) != {"Executing": [one], "Backlog": [two]}:
                    return f"write {crash_at}: board {world.board(number)}"
                return None
            finally:
                world.close()

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            failures = [problem for problem in pool.map(crash_then_resume, range(1, total + 1)) if problem]
        self.assertEqual(failures, [], "\n".join(failures))

    def test_a_board_left_by_0_8_1_gets_a_filter_the_web_page_accepts(self) -> None:
        world = World()
        try:
            root, one, two, legacy = legacy_adr(world)
            world.apply(world.draft(root, "--base", legacy["base"]))
            number = legacy["project"]["number"]
            # 0.8.0-0.8.1 wrote a quoted filter the web page rejects (blank page in Safari).
            lifecycle = next(v for v in world.project(number)["views"] if v["name"] == "Lifecycle")
            lifecycle["filter"] = 'has:"Work phase"'
            world.save()
            with self.assertRaises(sim.WebFilterRejected):
                world.board(number)
            receipt = world.apply(world.draft(root, "--base", legacy["base"]))
            self.assertTrue(any("outdated filter 'has:\"Work phase\"'" in note for note in receipt["notes"]))
            self.assertEqual(world.board(number), {"Executing": [one], "Backlog": [two]})
            views = world.project(number)["views"]
            self.assertEqual([v["name"] for v in views], ["Lifecycle", "By section", "By release"])
            self.assertEqual(views[0]["filter"], "has:work-phase")
            self.assertIn(f"Project: {receipt['lifecycle_url']}", world.issue(one)["body"])
        finally:
            world.close()

    def test_a_guard_view_someone_added_is_left_alone(self) -> None:
        world = World()
        try:
            root, one, two, legacy = legacy_adr(world)
            project = world.project(legacy["project"]["number"])
            sim.add_view(world.state, project, "Temporary guard", "TABLE_LAYOUT")
            world.save()
            receipt = world.apply(world.draft(root, "--base", legacy["base"]))
            self.assertTrue(receipt["verified"])
            names = [v["name"] for v in world.project(legacy["project"]["number"])["views"]]
            self.assertEqual(names, ["Temporary guard", "Lifecycle", "By section", "By release"])
            again = world.apply(world.draft(root, "--base", legacy["base"]))
            self.assertEqual(again["applied_mutations"], [])
        finally:
            world.close()


class EvidenceGateTest(unittest.TestCase):
    """A manifest that claims progress without evidence is refused before GitHub is touched."""

    def setUp(self) -> None:
        self.world = World()
        state = self.world.state
        self.root = sim.add_issue(state, REPO, "ADR-1: Widget", work_key=f"{REPO}:ADR-1")
        self.one = sim.add_issue(state, REPO, "One", work_key=f"{REPO}:ADR-1:one", parent=self.root)
        self.two = sim.add_issue(state, REPO, "Two", work_key=f"{REPO}:ADR-1:two", parent=self.root)
        self.world.save()
        self.manifest = self.world.draft(self.root)

    def tearDown(self) -> None:
        self.world.close()

    def story(self, manifest: dict, number: int) -> dict:
        return next(i for i in manifest["items"] if i["number"] == number)

    def refused(self, manifest: dict, message: str) -> None:
        calls = len(self.world.state["calls"])
        result = self.world.reconcile("--manifest", str(self.world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertEqual(len(self.world.state["calls"]), calls, "no GitHub call may happen")

    def test_progress_claims_need_their_evidence(self) -> None:
        m = copy.deepcopy(self.manifest)
        self.story(m, self.one)["work_phase"] = "Executing"
        self.story(m, self.one)["evidence"] = evidence(f"{REPO}:ADR-1:one", "Ready")
        self.refused(m, "Executing requires evidence: attempt")

        m = copy.deepcopy(self.manifest)
        story = self.story(m, self.one)
        story["work_phase"] = "Executing"
        story["evidence"] = evidence(story["work_key"], "Executing")
        story["evidence"]["attempt"]["ref"] = f"https://github.com/{REPO}/commit/{'b' * 40}"
        self.refused(m, "must name the attempt-start event, not an implementation commit")

        m = copy.deepcopy(self.manifest)
        for number in (self.one, self.two):
            story = self.story(m, number)
            story["work_phase"] = "Executing"
            story["evidence"] = evidence(story["work_key"], "Executing", attempt="shared")
        self.refused(m, "attempt_id is reused across stories")

        m = copy.deepcopy(self.manifest)
        story = self.story(m, self.one)
        story["work_phase"] = "Executing"
        story["evidence"] = evidence(story["work_key"], "Executing")
        story["evidence"]["attempt"].update(ref_kind="issue_comment", ref=f"https://github.com/{REPO}/issues/{self.two}#issuecomment-1")
        self.refused(m, f"must name an issue comment on #{self.one}")

        m = copy.deepcopy(self.manifest)
        story = self.story(m, self.one)
        story["work_phase"] = "Release ready"
        story["evidence"] = evidence(story["work_key"], "Release ready")
        story["evidence"]["verdict"]["author"] = "builder"
        self.refused(m, "verdict is not independent")

        m = copy.deepcopy(self.manifest)
        self.story(m, self.root)["work_phase"] = "Ready"
        self.refused(m, "epic Work phase must be omitted")

        m = copy.deepcopy(self.manifest)
        self.story(m, self.root)["health"] = "At risk"
        self.refused(m, f"epic #{self.root} declares Health 'At risk', but its stories make it 'On track'")

        m = copy.deepcopy(self.manifest)
        del m["observed"]
        self.refused(m, "run --draft to produce it")


class SafetyTest(unittest.TestCase):
    """Situations where the safe answer is to refuse or to leave people's things alone."""

    def setUp(self) -> None:
        self.world = World()
        state = self.world.state
        self.root = sim.add_issue(state, REPO, "ADR-3: Search", work_key=f"{REPO}:ADR-3")
        self.story = sim.add_issue(state, REPO, "Index", work_key=f"{REPO}:ADR-3:index", parent=self.root)
        self.world.save()

    def tearDown(self) -> None:
        self.world.close()

    def run_draft(self, *extra: str):
        manifest = self.world.draft(self.root)
        return self.world.reconcile("--manifest", str(self.world.write_manifest(manifest)), "--apply", *extra)

    def test_a_field_of_the_wrong_type_is_reported_not_replaced(self) -> None:
        board = sim.add_project(self.world.state, "search", readme=legacy_readme_for(self.world, f"{REPO}:ADR-3", self.root), repositories=[REPO])
        sim.add_field(self.world.state, board, "Rank", "TEXT")
        before = self.world.mutations()
        result = self.run_draft()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Project field 'Rank' has type TEXT, expected NUMBER", result.stderr)
        self.assertEqual(self.world.mutations(), before)
        self.assertEqual([f["name"] for f in self.world.project(board["number"])["fields"]].count("Rank"), 1)

    def test_two_boards_claiming_one_document_stop_the_run(self) -> None:
        for title in ("first", "second"):
            sim.add_project(self.world.state, title, readme=legacy_readme_for(self.world, f"{REPO}:ADR-3", self.root), repositories=[REPO])
        result = self.run_draft()
        self.assertEqual(result.returncode, 2)
        self.assertIn("multiple canonical Projects found", result.stderr)

    def test_a_persons_board_is_only_used_when_named(self) -> None:
        mine = sim.add_project(self.world.state, "my search board", repositories=[REPO])
        receipt = json.loads(self.run_draft().stdout)
        self.assertNotEqual(receipt["project_number"], mine["number"])
        self.assertEqual(self.world.project(mine["number"])["items"], [])
        other = sim.add_project(self.world.state, "team board", repositories=[REPO])
        self.world.save()
        # Adoption applies only while the document has no board of its own.
        result = self.run_draft("--adopt-project", str(other["number"]))
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"--adopt-project {other['number']} conflicts with marked Project {receipt['project_number']}", result.stderr)

    def test_another_documents_issue_leaves_this_board(self) -> None:
        receipt = json.loads(self.run_draft().stdout)
        stray = sim.add_issue(self.world.state, REPO, "ADR-4 story", work_key=f"{REPO}:ADR-4:x")
        board = self.world.project(receipt["project_number"])
        sim.add_item(self.world.state, board, REPO, stray)
        self.world.save()
        again = self.run_draft()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(f"remove out-of-scope managed issue #{stray}", json.loads(again.stdout)["applied_mutations"])
        self.assertNotIn(stray, [i["number"] for i in self.world.project(receipt["project_number"])["items"]])

    def test_duplicate_work_keys_stop_the_run(self) -> None:
        sim.add_issue(self.world.state, REPO, "Copy of Index", work_key=f"{REPO}:ADR-3:index")
        self.world.save()
        result = self.world.reconcile("--draft", "--repo", REPO, "--root", str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"work key {REPO}:ADR-3:index occurs in issues #{self.story} and #", result.stderr)


def legacy_readme_for(world: World, key: str, root: int) -> str:
    from scenario import legacy_readme

    return legacy_readme(world.state, key, root)


class VisibilityTest(unittest.TestCase):
    """Boards are private unless someone deliberately makes one public."""

    def setUp(self) -> None:
        self.world = World()
        state = self.world.state
        self.root = sim.add_issue(state, REPO, "ADR-7: Sync", work_key=f"{REPO}:ADR-7")
        self.story = sim.add_issue(state, REPO, "Sync engine", work_key=f"{REPO}:ADR-7:engine", parent=self.root)
        self.world.save()
        self.receipt = self.world.apply(self.world.draft(self.root))
        self.number = self.receipt["project_number"]

    def tearDown(self) -> None:
        self.world.close()

    def board(self) -> dict:
        return self.world.project(self.number)

    def test_a_new_board_is_private_even_though_github_would_make_it_public(self) -> None:
        self.assertFalse(self.board()["public"])
        self.assertIsNone(sim.render_anonymous(self.world.state, self.number))
        self.assertIn("Visibility: private", self.board()["readme"])
        self.assertEqual(self.receipt["visibility"], "private")

    def test_going_public_previews_and_needs_confirmation(self) -> None:
        world = self.world
        board = self.board()
        board["items"].append({"id": "PVTI_d", "draft": "Call the lawyer", "repository": None, "number": None, "archived": False, "values": {}})
        world.save()
        before = world.mutations()
        refused = world.awa("project", "visibility", str(self.root), "public", "--repo", REPO)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("anyone on the internet will see", refused.stderr)
        self.assertIn("1 draft issues, fully visible: Call the lawyer", refused.stderr)
        self.assertIn("rerun with --yes", refused.stderr)
        self.assertEqual(world.mutations(), before)
        self.assertFalse(self.board()["public"])

        done = world.awa("project", "visibility", str(self.root), "public", "--repo", REPO, "--yes")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["verified"])
        outsider = sim.render_anonymous(world.state, self.number)
        self.assertIn(f"{REPO}#{self.story}", outsider["cards"])
        self.assertIn("Visibility: public", self.board()["readme"])

    def test_an_older_manifest_keeps_a_board_awa_made_public(self) -> None:
        world = self.world
        older = world.draft(self.root)
        world.awa("project", "visibility", str(self.root), "public", "--repo", REPO, "--yes")
        receipt = world.apply(older)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["visibility"], "public")
        self.assertTrue(self.board()["public"])
        fresh = world.draft(self.root)
        dry = world.reconcile("--manifest", str(world.write_manifest(fresh)))
        self.assertEqual(json.loads(dry.stdout)["planned_mutations"], [])
        back = world.awa("project", "visibility", str(self.root), "private", "--repo", REPO)
        self.assertEqual(back.returncode, 0, back.stderr)
        self.assertIsNone(sim.render_anonymous(world.state, self.number))

    def test_a_board_flipped_in_github_stops_the_next_run(self) -> None:
        world = self.world
        self.board()["public"] = True  # someone used GitHub's settings page
        world.save()
        manifest = world.draft(self.root)  # a routine draft must not legitimize it
        before = world.mutations()
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"Project #{self.number} is public on GitHub, but awa last set it private; it was changed outside awa", result.stderr)
        self.assertEqual(world.mutations(), before)
        self.assertTrue(self.board()["public"])
        accepted = world.awa("project", "visibility", str(self.root), "public", "--repo", REPO, "--yes")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(world.apply(manifest)["verified"])

    def test_a_manifest_cannot_publish_a_board_by_itself(self) -> None:
        world = self.world
        manifest = world.draft(self.root)
        manifest["project"]["visibility"] = "public"
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("visibility is not a manifest setting", result.stderr)
        self.assertFalse(self.board()["public"])

    def test_organization_policy_refusal_leaves_the_board_private(self) -> None:
        world = self.world
        world.state["forbid_visibility_change"] = True
        result = world.awa("project", "visibility", str(self.root), "public", "--repo", REPO, "--yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("organization policy or missing Project admin rights", result.stderr)
        self.assertIn("Only organization owners can change the visibility", result.stderr)
        self.assertFalse(self.board()["public"])

    def test_a_private_repository_shows_outsiders_only_hidden_cards(self) -> None:
        world = self.world
        world.state["repositories"][REPO]["private"] = True
        url = f"https://github.com/users/acme/projects/{self.number}"
        preview = world.awa("project", "visibility", url, "public")
        self.assertIn("all hidden from outsiders because acme/vox is private", preview.stderr)
        world.awa("project", "visibility", url, "public", "--yes")
        self.assertEqual(sim.render_anonymous(world.state, self.number)["cards"], ["hidden item", "hidden item"])

    def test_a_board_github_creates_public_never_shows_the_document(self) -> None:
        world = World()
        try:
            root = sim.add_issue(world.state, REPO, "ADR-8: Secret plan", work_key=f"{REPO}:ADR-8")
            sim.add_issue(world.state, REPO, "Step", work_key=f"{REPO}:ADR-8:step", parent=root)
            path = world.write_manifest(world.draft(root))
            world.state["write_calls"] = 0
            world.state["crash_at"] = 1  # the response to creating the board is lost
            self.assertNotEqual(world.reconcile("--manifest", str(path), "--apply").returncode, 0)
            created = world.state["projects"][-1]
            self.assertTrue(created["public"])
            self.assertTrue(created["title"].startswith("work-accountability setup "))
            self.assertNotIn("Secret", created["title"])
            self.assertEqual(created["items"], [])
            resumed = world.reconcile("--manifest", str(path), "--apply")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            board = sim.project_by(world.state, number=created["number"])
            self.assertFalse(board["public"])
            self.assertEqual(json.loads(resumed.stdout)["project_number"], created["number"])
            self.assertNotEqual(board["title"], created["title"])
        finally:
            world.close()

    def test_doctor_points_out_private_boards_on_a_public_repository(self) -> None:
        world = self.world
        checkout = world.path / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"], cwd=checkout, check=True)
        result = world.awa("doctor", cwd=checkout)
        self.assertIn(f"NOTE: {REPO} is public but its document Projects #{self.number} are private", result.stdout)


class ReleaseMilestoneTest(unittest.TestCase):
    """Release milestones, seen the way a release manager sees them."""

    def setUp(self) -> None:
        self.world = World()
        self.n = release_document(self.world)
        plan = self.world.awa("release", "plan", "v1.1.0", "--repo", REPO, "--description", "Sync engine")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.manifest = self.world.draft(self.n["root"])
        self.set_story("a", "Release ready", {"kind": "release", "release": "v1.1.0"})
        self.set_story("b", "Release ready", {"kind": "release", "release": "v1.1.0"})
        self.set_story("c", "Executing", {"kind": "release", "release": "v1.1.0"})
        self.set_story("d", "Ready", {"kind": "merge"})
        self.set_story("e", "Release ready", {"kind": "release", "release": "next"})
        self.receipt = self.world.apply(self.manifest)
        self.board = self.receipt["project_number"]

    def tearDown(self) -> None:
        self.world.close()

    def item(self, name: str, manifest: dict | None = None) -> dict:
        number = self.n[name]
        return next(i for i in (manifest or self.manifest)["items"] if i["number"] == number)

    def set_story(self, name: str, phase: str, delivery: dict, manifest: dict | None = None) -> dict:
        item = self.item(name, manifest)
        item["work_phase"] = phase
        item["delivery"] = delivery
        item["evidence"] = release_evidence(item["work_key"], phase, str(self.n[f"sha_{name}"]), attempt=f"att-{name}")
        return item

    def publish(self, tag: str = "v1.1.0", **extra) -> None:
        sim.add_tag(self.world.state, REPO, tag)
        sim.add_release(self.world.state, REPO, tag, published_at="2026-09-20T12:00:00Z", **extra)
        self.world.save()

    def close(self, *extra: str):
        return self.world.awa("release", "close", "v1.1.0", "--repo", REPO, *extra)

    def test_planned_stories_land_in_the_release_milestone(self) -> None:
        page = sim.render_milestone(self.world.state, REPO, "v1.1.0")
        self.assertEqual(page["open"], sorted([self.n["a"], self.n["b"], self.n["c"]]))
        self.assertEqual(page["description"], "Sync engine")
        groups = self.world.board(self.board, "By release")
        self.assertEqual(groups["v1.1.0"], [self.n["a"], self.n["b"], self.n["c"]])
        body = self.world.issue(self.n["a"])["body"]
        self.assertIn("Release: v1.1.0", body)
        self.assertIn("Delivery: release v1.1.0", body)
        self.assertIn(f"Integration: {self.n['sha_a']}", body)
        dry = self.world.reconcile("--manifest", str(self.world.write_manifest(self.world.draft(self.n["root"]))))
        self.assertEqual(json.loads(dry.stdout)["planned_mutations"], [])

    def test_a_release_move_needs_a_reason_and_a_ui_move_stops_the_run(self) -> None:
        world, n = self.world, self.n
        moved = world.draft(n["root"])
        story = self.item("c", moved)
        story["milestone"] = "v1.2.0"
        story["delivery"]["release"] = "v1.2.0"
        result = world.reconcile("--manifest", str(world.write_manifest(moved)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['c']} moves from release v1.1.0 to 'v1.2.0'; a release move needs milestone_change_reason", result.stderr)
        story["milestone_change_reason"] = "scope cut by the decider"
        world.apply(moved)
        self.assertEqual(sim.render_milestone(world.state, REPO, "v1.2.0")["open"], [n["c"]])
        comments = [c["body"] for c in world.issue(n["c"])["comments"]]
        self.assertEqual(sum("Release target moved from v1.1.0 to v1.2.0: scope cut by the decider" in c for c in comments), 1)
        world.apply(moved)  # a rerun posts no second comment
        self.assertEqual(len(world.issue(n["c"])["comments"]), len(comments))
        # Someone drags #b to another milestone in GitHub.
        repo = world.state["repositories"][REPO]
        world.issue(n["b"])["milestone"] = next(m["number"] for m in repo["milestones"] if m["title"] == "v1.2.0")
        world.save()
        before = world.mutations()
        stale = world.reconcile("--manifest", str(world.write_manifest(world.draft(n["root"]))), "--apply")
        self.assertEqual(stale.returncode, 2)
        self.assertIn(f"#{n['b']} is in milestone 'v1.2.0' on GitHub, but awa last set 'v1.1.0'", stale.stderr)
        self.assertEqual(world.mutations(), before)

    def test_closing_a_release_delivers_its_stories(self) -> None:
        world, n = self.world, self.n
        refused = self.close()
        self.assertEqual(refused.returncode, 2)
        self.assertIn("v1.1.0 has no published GitHub Release yet", refused.stderr)
        self.publish(body="Highlights written by a person.")
        before = world.mutations()
        unfinished = self.close()
        self.assertEqual(unfinished.returncode, 2)
        self.assertIn(f"#{n['c']} is Executing, not Release ready or Done", unfinished.stderr)
        self.assertEqual(world.mutations(), before, "a refused close writes nothing")

        done = self.close("--move-open-to", "v1.2.0")
        self.assertEqual(done.returncode, 0, done.stderr)
        receipt = json.loads(done.stdout)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["delivered"], sorted([n["a"], n["b"], n["e"]]))
        self.assertEqual(receipt["moved"], [n["c"]])
        page = sim.render_milestone(world.state, REPO, "v1.1.0")
        self.assertEqual(page["state"], "closed")
        self.assertEqual(page["closed"], sorted([n["a"], n["b"], n["e"]]))
        self.assertEqual(page["open"], [])
        self.assertEqual(sim.render_milestone(world.state, REPO, "v1.2.0")["open"], [n["c"]])
        for name in ("a", "b", "e"):
            issue = world.issue(n[name])
            self.assertEqual((issue["state"], issue["state_reason"]), ("closed", "completed"))
            self.assertIn("Delivered: https://github.com/acme/vox/releases/tag/v1.1.0", issue["body"])
        notes = world.state["repositories"][REPO]["releases"][-1]["body"]
        self.assertTrue(notes.startswith("Highlights written by a person."))
        self.assertIn(f"- #{n['a']} Sync core", notes)
        self.assertIn(f"- #{n['e']} Sync retry", notes)
        self.assertEqual(world.board(self.board)["Done"], sorted([n["a"], n["b"], n["e"]]))
        again = self.close("--move-open-to", "v1.2.0")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["applied_mutations"], [])
        dry = world.reconcile("--manifest", str(world.write_manifest(world.draft(n["root"]))))
        self.assertEqual(json.loads(dry.stdout)["planned_mutations"], [])

    def test_next_stories_join_the_first_full_release_and_unknown_ones_block(self) -> None:
        world, n = self.world, self.n
        attribute = world.awa("release", "attribute", str(n["e"]), "--repo", REPO)
        self.assertEqual(json.loads(attribute.stdout)["status"], "pending")  # v1.0.1 is a tag without a Release
        self.publish()
        attribute = world.awa("release", "attribute", str(n["e"]), "--repo", REPO)
        self.assertEqual((json.loads(attribute.stdout)["status"], json.loads(attribute.stdout)["tag"]), ("released", "v1.1.0"))
        # A next story with no recorded landing commit cannot be attributed.
        record = world.issue(n["d"])
        record["body"] = record["body"].replace("Delivery: merge", "Delivery: release next")
        world.save()
        blocked = self.close("--move-open-to", "v1.2.0")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn(f"#{n['d']} (delivery: next) cannot be attributed: no Integration record", blocked.stderr)

    def test_a_merge_story_reaches_done_only_once_its_commit_is_on_main(self) -> None:
        world, n = self.world, self.n
        manifest = world.draft(n["root"])
        story = self.set_story("d", "Done", {"kind": "merge"}, manifest)
        side = sim.add_commit(world.state, REPO, "unmerged work", branch="feature")
        world.save()
        story["evidence"]["integration"]["commit"] = side
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"#{n['d']}: integration commit {side[:12]} is not on main", result.stderr)
        story["evidence"]["integration"]["commit"] = str(n["sha_d"])
        world.apply(manifest)
        issue = world.issue(n["d"])
        self.assertEqual(issue["state"], "closed")
        self.assertIn(f"Delivered: https://github.com/{REPO}/commit/{n['sha_d']}", issue["body"])

    def test_integration_must_name_the_accepted_candidate(self) -> None:
        world, n = self.world, self.n
        manifest = world.draft(n["root"])
        self.item("a", manifest)["evidence"]["integration"]["candidate"] = "https://example.invalid/other"
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("integration.candidate must equal the accepted candidate", result.stderr)

    def test_github_dropping_a_milestone_change_is_reported(self) -> None:
        world, n = self.world, self.n
        world.state["no_push"] = True
        manifest = world.draft(n["root"])
        story = self.item("c", manifest)
        story["milestone"] = "v1.2.0"
        story["delivery"]["release"] = "v1.2.0"
        story["milestone_change_reason"] = "deferred"
        result = world.reconcile("--manifest", str(world.write_manifest(manifest)), "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("drops milestone changes silently when the account lacks push access", result.stderr)

    def test_close_needs_evidence_from_the_machine_that_applied_it(self) -> None:
        world = self.world
        self.publish()
        shutil.rmtree(world.path / "state" / "agent-work-accountability" / "pending")
        before = world.mutations()
        result = self.close("--move-open-to", "v1.2.0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("this machine has no candidate/integration evidence", result.stderr)
        self.assertEqual(world.mutations(), before)

    def test_a_release_is_not_a_planning_document(self) -> None:
        world = World()
        try:
            root = sim.add_issue(world.state, REPO, "v0.2.10 release", work_key=f"{REPO}:REL-0.2.10")
            sim.add_issue(world.state, REPO, "Fix", work_key=f"{REPO}:REL-0.2.10:fix", parent=root)
            world.save()
            result = world.reconcile("--manifest", str(world.write_manifest(world.draft(root))), "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertIn("looks like a release, not a planning document", result.stderr)
            self.assertEqual(world.state["projects"], [])
        finally:
            world.close()

    def test_a_crash_during_close_resumes_to_the_same_result(self) -> None:
        self.publish()
        total_world = World()
        try:
            pass
        finally:
            total_world.close()

        def run(crash_at: int | None) -> tuple[dict, str | None]:
            world = World()
            try:
                case = ReleaseMilestoneTest("test_planned_stories_land_in_the_release_milestone")
                case.world = world
                case.n = release_document(world)
                world.awa("release", "plan", "v1.1.0", "--repo", REPO)
                case.manifest = world.draft(case.n["root"])
                case.set_story("a", "Release ready", {"kind": "release", "release": "v1.1.0"})
                case.set_story("b", "Release ready", {"kind": "release", "release": "v1.1.0"})
                case.set_story("e", "Release ready", {"kind": "release", "release": "next"})
                world.apply(case.manifest)
                case.publish()
                world.state["write_calls"] = 0
                world.state["crash_at"] = crash_at
                first = world.awa("release", "close", "v1.1.0", "--repo", REPO)
                if crash_at is not None:
                    if first.returncode == 0 and world.state.get("crash_at") is not None:
                        return {}, None  # fewer writes than crash_at
                    second = world.awa("release", "close", "v1.1.0", "--repo", REPO)
                    if second.returncode != 0:
                        return {}, f"write {crash_at}: {second.stderr}"
                outcome = {
                    "milestone": sim.render_milestone(world.state, REPO, "v1.1.0"),
                    "issues": {k: world.issue(case.n[k])["state"] for k in ("a", "b", "e")},
                    "notes": world.state["repositories"][REPO]["releases"][-1]["body"],
                    "comments": {k: len(world.issue(case.n[k])["comments"]) for k in ("a", "b", "e")},
                }
                return outcome, None
            finally:
                world.close()

        expected, problem = run(None)
        self.assertIsNone(problem)
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            results = list(pool.map(run, range(1, 40)))
        failures = [problem for outcome, problem in results if problem]
        failures += [f"crash {i + 1}: {outcome}" for i, (outcome, problem) in enumerate(results) if outcome and outcome != expected]
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
