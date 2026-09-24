#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/github-work-accountability/scripts/reconcile_project.py"
SPEC = importlib.util.spec_from_file_location("reconcile_project", SCRIPT)
assert SPEC and SPEC.loader
rp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rp
SPEC.loader.exec_module(rp)


def evidence(key: str, name: str, **extra: str) -> dict[str, str]:
    return {
        "ref": f"https://example.invalid/{name}",
        "work_key": key,
        "requirement": "abc123:requirement-v1",
        **extra,
    }


def manifest_data(phase: str | None = "Executing", kind: str = "story") -> dict:
    key = "Acme/widget:ADR-001:story"
    item = {
        "number": 7,
        "work_key": key,
        "kind": kind,
        "health": "On track",
        "source_freshness": "Current",
        "priority": "High",
        "rank": 1,
        "evidence": {},
    }
    if phase is not None:
        item["work_phase"] = phase
    for name in rp.REQUIRED_EVIDENCE.get(phase, ()):
        item["evidence"][name] = evidence(key, name)
    if phase in {"Release ready", "Done"}:
        candidate = item["evidence"]["candidate"]["ref"]
        item["evidence"]["verdict"].update(
            candidate=candidate, author="reviewer", implementer="builder"
        )
    if phase == "Done":
        item["evidence"]["delivery"]["candidate"] = item["evidence"]["candidate"]["ref"]
    return {
        "schema": rp.SCHEMA,
        "repository": "Acme/widget",
        "mode": "repository",
        "project": {
            "owner": "Acme",
            "title": "widget — Delivery",
            "priority_options": ["High", "Medium", "Low"],
        },
        "items": [item],
    }


class ManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "manifest.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def load(self, value: dict):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return rp.load_manifest(self.path)

    def test_valid_evidence_bound_story(self) -> None:
        manifest = self.load(manifest_data("Done"))
        self.assertEqual(manifest.items[0].work_phase, "Done")
        self.assertEqual(manifest.items[0].evidence["verdict"].author, "reviewer")

    def test_late_phase_without_gate_evidence_is_rejected(self) -> None:
        value = manifest_data("Acceptance")
        del value["items"][0]["evidence"]["candidate"]
        with self.assertRaisesRegex(rp.ReconcileError, "Acceptance requires evidence: candidate"):
            self.load(value)

    def test_evidence_for_another_work_key_is_rejected(self) -> None:
        value = manifest_data("Executing")
        value["items"][0]["evidence"]["attempt"]["work_key"] = "Acme/widget:other"
        with self.assertRaisesRegex(rp.ReconcileError, "does not match item key"):
            self.load(value)

    def test_self_acceptance_is_rejected(self) -> None:
        value = manifest_data("Release ready")
        value["items"][0]["evidence"]["verdict"].update(
            author="same", implementer="same"
        )
        with self.assertRaisesRegex(rp.ReconcileError, "not independent"):
            self.load(value)

    def test_epic_phase_is_rejected(self) -> None:
        with self.assertRaisesRegex(rp.ReconcileError, "epic Work phase must be omitted"):
            self.load(manifest_data("Backlog", kind="epic"))


class ProjectSelectionTest(unittest.TestCase):
    def repo(self, name: str = "Acme/widget"):
        return rp.RepositoryState(
            id="R_immutable",
            name_with_owner=name,
            owner_login="Acme",
            owner_id="O_owner",
            owner_type="Organization",
            linked_projects=[],
        )

    def project(self, number: int, readme: str = "", title: str = "Roadmap"):
        return rp.ProjectState(
            id=f"P_{number}",
            number=number,
            title=title,
            url=f"https://example.invalid/{number}",
            readme=readme,
            short_description="",
            closed=False,
            creator="agent",
            created_at="2026-09-24T00:00:00Z",
            repositories={"Acme/widget"},
        )

    def test_unmarked_human_project_is_not_implicitly_adopted(self) -> None:
        selected = rp.select_project(
            [self.project(1)], self.repo(), "github.com", None, None, "agent"
        )
        self.assertIsNone(selected)

    def test_explicit_adoption_selects_exact_project(self) -> None:
        selected = rp.select_project(
            [self.project(1), self.project(2)], self.repo(), "github.com", 2, None, "agent"
        )
        self.assertEqual(selected.number, 2)

    def test_immutable_marker_survives_repository_rename(self) -> None:
        readme = (
            "human\n\n<!-- work-accountability:begin-project -->\n"
            "<!-- work-accountability:project-v1 github.com:R_immutable Acme/old -->\n"
            "<!-- work-accountability:end-project -->\n"
        )
        selected = rp.select_project(
            [self.project(3, readme)], self.repo("Acme/widget"), "github.com", None, None, "agent"
        )
        self.assertEqual(selected.number, 3)

    def test_multiple_marked_projects_fail_closed(self) -> None:
        marker = "<!-- work-accountability:project-v1 github.com:R_immutable Acme/widget -->"
        with self.assertRaisesRegex(rp.ReconcileError, "multiple canonical Projects"):
            rp.select_project(
                [self.project(1, marker), self.project(2, marker)],
                self.repo(),
                "github.com",
                None,
                None,
                "agent",
            )

    def test_managed_readme_preserves_human_text_and_is_idempotent(self) -> None:
        repo = self.repo()
        once = rp.managed_readme("human text\n", "github.com", repo, 4)
        twice = rp.managed_readme(once, "github.com", repo, 4)
        self.assertEqual(once, twice)
        self.assertIn("human text", once)
        self.assertIn("Lifecycle view: 4", once)


class FakeTransport:
    def __init__(self):
        self.graphql_calls = []
        self.rest_calls = []
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def graphql(self, query, variables, mutation=False):
        self.graphql_calls.append((query, variables, mutation))
        if "deleteProjectV2View" in query:
            return {"result": {"projectV2View": {"id": "old"}}}
        return {"rateLimit": {"cost": 1, "remaining": 1000, "resetAt": "later"}}

    def rest(self, endpoint, method="GET", data=None):
        self.rest_calls.append((endpoint, method, data))
        return {"value": {"number": 9}}


class ReconciliationMechanicsTest(unittest.TestCase):
    def test_batch_size_bounds_request_count(self) -> None:
        transport = FakeTransport()
        payloads = [(f"write-{n}", {"n": n}) for n in range(51)]
        applied = rp.batch_mutations(
            transport, "doThing", "ThingInput", payloads, "thing { id }", cap=25
        )
        self.assertEqual(len(applied), 51)
        self.assertEqual(len(transport.graphql_calls), 3)
        self.assertTrue(all(call[2] for call in transport.graphql_calls))
        self.assertTrue(all("rateLimit" not in call[0] for call in transport.graphql_calls))

    def test_view_requires_work_phase_columns_and_sort(self) -> None:
        fields = {
            "Work phase": rp.FieldState("phase", 1, "Work phase", "SINGLE_SELECT"),
            "Priority": rp.FieldState("priority", 2, "Priority", "SINGLE_SELECT"),
            "Rank": rp.FieldState("rank", 3, "Rank", "NUMBER"),
        }
        good = rp.ViewState(
            "view", 1, "Lifecycle", "BOARD_LAYOUT", None, ["phase"],
            [("priority", "ASC"), ("rank", "ASC")],
        )
        bad = rp.ViewState(
            "view", 1, "Lifecycle", "TABLE_LAYOUT", None, [], []
        )
        wrong_direction = rp.ViewState(
            "view", 1, "Lifecycle", "BOARD_LAYOUT", None, ["phase"],
            [("priority", "DESC"), ("rank", "ASC")],
        )
        self.assertTrue(rp.lifecycle_view_valid(good, fields))
        self.assertFalse(rp.lifecycle_view_valid(bad, fields))
        self.assertFalse(rp.lifecycle_view_valid(wrong_direction, fields))

    def test_rest_view_uses_numeric_work_phase_id(self) -> None:
        transport = FakeTransport()
        repo = rp.RepositoryState(
            "R", "Acme/widget", "Acme", "O", "Organization", []
        )
        project = rp.ProjectState(
            "P", 3, "widget — Delivery", "url", "", "", False, "agent", None,
            {"Acme/widget"},
        )
        project.fields = {
            name: rp.FieldState(name, index, name, "NUMBER" if name == "Rank" else "SINGLE_SELECT")
            for index, name in enumerate(
                ["Title", "Work phase", "Health", "Priority", "Source freshness", "Rank"],
                start=10,
            )
        }
        manifest = type("M", (), {"project_owner": "Acme"})()
        number = rp.create_lifecycle_view(
            transport, project, repo, manifest, rp.Receipt(), False
        )
        self.assertEqual(number, 9)
        endpoint, method, payload = transport.rest_calls[0]
        self.assertEqual(endpoint, "orgs/Acme/projectsV2/3/views")
        self.assertEqual(method, "POST")
        self.assertEqual(payload["layout"], "board")
        self.assertEqual(payload["vertical_group_by"], [11])

    def test_explicit_repair_deletes_only_malformed_lifecycle_view(self) -> None:
        transport = FakeTransport()
        repo = rp.RepositoryState("R", "Acme/widget", "Acme", "O", "Organization", [])
        project = rp.ProjectState("P", 3, "x", "u", "", "", False, None, None, {"Acme/widget"})
        project.views[1] = rp.ViewState("legacy", 1, "Lifecycle", "TABLE_LAYOUT", None, [], [])
        project.fields = {
            name: rp.FieldState(name, index, name, "NUMBER" if name == "Rank" else "SINGLE_SELECT")
            for index, name in enumerate(
                ["Title", "Work phase", "Health", "Priority", "Source freshness", "Rank"],
                start=10,
            )
        }
        manifest = type("M", (), {"project_owner": "Acme"})()
        receipt = rp.Receipt()
        def refreshed(*_args, **_kwargs):
            project.views.clear()
            return project
        with mock.patch.object(rp, "load_project_until", side_effect=refreshed):
            number = rp.create_lifecycle_view(
                transport, project, repo, manifest, receipt, True
            )
        self.assertEqual(number, 9)
        self.assertIn("delete malformed Lifecycle view #1", receipt.applied_mutations)
        self.assertEqual(len(transport.graphql_calls), 1)
        self.assertEqual(len(transport.rest_calls), 1)

    def test_field_type_conflict_fails_without_mutation(self) -> None:
        transport = FakeTransport()
        project = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        project.fields["Work phase"] = rp.FieldState(
            "bad", 1, "Work phase", "TEXT"
        )
        manifest = type("M", (), {"priority_options": ("High", "Low")})()
        receipt = rp.Receipt()
        with self.assertRaisesRegex(rp.ReconcileError, "expected SINGLE_SELECT"):
            rp.ensure_fields(transport, project, manifest, receipt)
        self.assertEqual(transport.graphql_calls, [])

    def test_case_only_select_differences_do_not_write(self) -> None:
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            value = manifest_data("Ready")
            path.write_text(json.dumps(value), encoding="utf-8")
            manifest = rp.load_manifest(path)
        project = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        project.fields = {
            "Work phase": rp.FieldState("phase", 1, "Work phase", "SINGLE_SELECT", [{"id": "ready", "name": "Ready"}]),
            "Health": rp.FieldState("health", 2, "Health", "SINGLE_SELECT", [{"id": "on", "name": "On track"}]),
            "Source freshness": rp.FieldState("fresh", 3, "Source freshness", "SINGLE_SELECT", [{"id": "current", "name": "Current"}]),
            "Priority": rp.FieldState("priority", 4, "Priority", "SINGLE_SELECT", [{"id": "high", "name": "High"}]),
            "Rank": rp.FieldState("rank", 5, "Rank", "NUMBER"),
        }
        project.items[7] = rp.ItemState(
            "item", "issue", 7, "Acme/widget", False,
            {
                "Work phase": "ready",
                "Health": "on track",
                "Source freshness": "current",
                "Priority": "high",
                "Rank": 1.0,
            },
        )
        rp.ensure_values(transport, project, manifest, rp.Receipt())
        self.assertEqual(transport.graphql_calls, [])

    def test_items_from_other_repositories_cannot_collide_by_number(self) -> None:
        project = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        def node(item_id: str, repository: str):
            return {
                "id": item_id,
                "isArchived": False,
                "content": {"id": f"issue-{item_id}", "number": 7, "repository": {"nameWithOwner": repository}},
                "fieldValues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            }
        rp.parse_item_nodes(
            project,
            [node("foreign", "Elsewhere/widget"), node("target", "Acme/widget")],
            "Acme/widget",
        )
        self.assertEqual(project.items[7].id, "target")
        self.assertEqual(project.items[7].repository, "Acme/widget")

    def test_epic_existing_phase_is_cleared(self) -> None:
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest_data(None, kind="epic")), encoding="utf-8")
            manifest = rp.load_manifest(path)
        project = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        project.fields = {
            "Work phase": rp.FieldState("phase", 1, "Work phase", "SINGLE_SELECT"),
            "Health": rp.FieldState("health", 2, "Health", "SINGLE_SELECT"),
            "Source freshness": rp.FieldState("fresh", 3, "Source freshness", "SINGLE_SELECT"),
            "Priority": rp.FieldState("priority", 4, "Priority", "SINGLE_SELECT"),
            "Rank": rp.FieldState("rank", 5, "Rank", "NUMBER"),
        }
        project.items[7] = rp.ItemState(
            "item", "issue", 7, "Acme/widget", False,
            {"Work phase": "Executing", "Health": "On track", "Source freshness": "Current", "Priority": "High", "Rank": 1.0},
        )
        receipt = rp.Receipt()
        rp.ensure_values(transport, project, manifest, receipt)
        self.assertIn("clear #7 epic Work phase", receipt.applied_mutations)
        self.assertEqual(len(transport.graphql_calls), 1)
        self.assertIn("clearProjectV2ItemFieldValue", transport.graphql_calls[0][0])

    def test_read_after_write_delay_polls_without_replaying_mutation(self) -> None:
        transport = FakeTransport()
        absent = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        present = rp.ProjectState("P", 1, "x", "u", "", "", False, None, None, set())
        present.items[7] = rp.ItemState("item", "issue", 7, "Acme/widget", False, {})
        with mock.patch.object(rp, "load_project_detail", side_effect=[absent, present]) as load:
            result = rp.load_project_until(
                transport, "Organization", "Acme", 1, "Acme/widget",
                lambda state: 7 in state.items,
                "issue #7",
                delays=(0, 1),
            )
        self.assertIs(result, present)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(transport.sleeps, [1])


class ManagedIssueInventoryTest(unittest.TestCase):
    def test_duplicate_work_key_across_pages_fails_closed(self) -> None:
        class Transport:
            def rest(self, endpoint):
                page = int(endpoint.rsplit("page=", 1)[1])
                if page == 1:
                    filler = [
                        {
                            "number": n,
                            "node_id": f"I{n}",
                            "title": f"#{n}",
                            "state": "open",
                            "body": "",
                            "html_url": f"u{n}",
                        }
                        for n in range(1, 100)
                    ]
                    filler.append(
                        {
                            "number": 100,
                            "node_id": "I100",
                            "title": "first",
                            "state": "open",
                            "body": "<!-- work-accountability:key Acme/widget:key -->",
                            "html_url": "u100",
                        }
                    )
                    return filler
                return [
                    {
                        "number": 101,
                        "node_id": "I101",
                        "title": "second",
                        "state": "closed",
                        "body": "<!-- work-accountability:key Acme/widget:key -->",
                        "html_url": "u101",
                    }
                ]

        with self.assertRaisesRegex(rp.ReconcileError, "occurs in issues #100 and #101"):
            rp.list_managed_issues(Transport(), "Acme/widget")


if __name__ == "__main__":
    unittest.main()
