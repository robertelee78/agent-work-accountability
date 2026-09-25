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
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_sim as sim  # noqa: E402
from scenario import PRIORITIES, REPO, World, evidence, legacy_board, v3_manifest  # noqa: E402


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
        self.assertEqual(views, ["Lifecycle", "By section"])

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

        # Running the same manifest again changes nothing.
        again = world.apply(draft, "--attach-parents")
        self.assertTrue(again["verified"])
        self.assertEqual(again["applied_mutations"], [])


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
        self.assertEqual([v["name"] for v in world.project(self.board)["views"]], ["Lifecycle", "By section", "My triage"])
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
            self.assertEqual(names, ["Lifecycle", "By section"])
            lifecycle = next(v for v in world.project(old["number"])["views"] if v["name"] == "Lifecycle")
            self.assertEqual(lifecycle["filter"], 'has:"Work phase"')
            self.assertIn(f"Project: {receipt['lifecycle_url']}", world.issue(one)["body"])
            self.assertTrue(any("replace v3 Lifecycle view" in note for note in receipt["notes"]))
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


if __name__ == "__main__":
    unittest.main()
