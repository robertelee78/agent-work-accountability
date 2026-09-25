"""Drive the real reconciler CLI against github_sim, the way an agent would."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import github_sim as sim


ROOT = Path(__file__).resolve().parents[1]
RECONCILER = ROOT / "skills/github-work-accountability/scripts/reconcile_project.py"
REPO = "acme/vox"
PRIORITIES = ["High", "Medium", "Low"]


class World:
    """One simulated GitHub account plus the local state an agent keeps."""

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.state_file = self.path / "github.json"
        self.bin = self.path / "bin"
        self.bin.mkdir()
        shim = self.bin / "gh"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(sim.__file__).resolve()}" "$@"\n')
        shim.chmod(0o755)
        self.state = sim.new_state()
        sim.add_repository(self.state, REPO)
        self.save()

    def close(self) -> None:
        self.temporary.cleanup()

    # -- state -------------------------------------------------------------
    def save(self) -> None:
        sim.save(self.state_file, self.state)

    def reload(self) -> dict[str, Any]:
        self.state = sim.load(self.state_file)
        return self.state

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{env.get('PATH', '')}",
                sim.STATE_ENV: str(self.state_file),
                "XDG_STATE_HOME": str(self.path / "state"),
                "WORK_ACCOUNTABILITY_MUTATION_INTERVAL": "0",
            }
        )
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return env

    # -- the commands an agent runs ---------------------------------------
    def reconcile(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.save()
        result = subprocess.run(
            [sys.executable, str(RECONCILER), *arguments],
            env=self.env(),
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        self.reload()
        return result

    def write_manifest(self, manifest: dict[str, Any], name: str = "manifest.json") -> Path:
        path = self.path / name
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def draft(self, root: int, *extra: str) -> dict[str, Any]:
        result = self.reconcile("--draft", "--repo", REPO, "--root", str(root), *extra)
        if result.returncode != 0:
            raise AssertionError(f"--draft failed:\n{result.stderr}")
        return json.loads(result.stdout)

    def apply(self, manifest: dict[str, Any], *extra: str) -> dict[str, Any]:
        path = self.write_manifest(manifest)
        result = self.reconcile("--manifest", str(path), "--apply", *extra)
        if result.returncode != 0:
            raise AssertionError(f"--apply failed ({result.returncode}):\n{result.stderr}")
        return json.loads(result.stdout)

    # -- what a person sees -----------------------------------------------
    def project(self, number: int) -> dict[str, Any]:
        return sim.project_by(self.state, number=number)

    def board(self, project: int, view: str = "Lifecycle") -> dict[str, list[int]]:
        return sim.render_view(self.state, project, view)

    def value(self, project: int, number: int, field: str) -> Any:
        board = self.project(project)
        item = next(i for i in board["items"] if i["number"] == number)
        return sim.item_value(board, item, sim.field_by_name(board, field))

    def issue(self, number: int) -> dict[str, Any]:
        return sim.issue(self.state, REPO, number)

    def mutations(self) -> int:
        return self.state["mutations"]


# --------------------------------------------------------------- evidence

def evidence(key: str, phase: str, *, attempt: str | None = None) -> dict[str, Any]:
    """Evidence that satisfies the gates up to `phase` for one story."""
    attempt = attempt or f"attempt-{key.rsplit(':', 1)[-1]}"
    found: dict[str, Any] = {}
    order = ["Ready", "Executing", "Acceptance", "Release ready", "Done"]
    if phase not in order:
        return found
    reached = order[: order.index(phase) + 1]
    base = {"work_key": key, "requirement": "abc123:req"}
    if "Ready" in reached:
        found["design_approval"] = {"ref": f"docs/design.md#{attempt}", **base}
    if "Executing" in reached:
        found["attempt"] = {
            "ref": f"tracker:event:{attempt}",
            "ref_kind": "tracker_event",
            "attempt_id": attempt,
            "actor": "github:builder",
            "started_at": "2026-09-20T10:00:00Z",
            "state": "active" if phase == "Executing" else "submitted",
            **base,
        }
    if "Acceptance" in reached:
        found["candidate"] = {"ref": f"https://github.com/{REPO}/commit/{'a' * 39}{len(key) % 10}", **base}
    if "Release ready" in reached:
        found["verdict"] = {
            "ref": f"review:{attempt}",
            "candidate": found["candidate"]["ref"],
            "author": "reviewer",
            "implementer": "builder",
            **base,
        }
    if "Done" in reached:
        found["delivery"] = {"ref": f"release:{attempt}", "candidate": found["candidate"]["ref"], **base}
    return found


def legacy_readme(state: dict[str, Any], key: str, epic: int) -> str:
    repo_id = state["repositories"][REPO]["id"]
    return (
        "<!-- work-accountability:begin-project -->\n"
        f"<!-- work-accountability:project-v2 github.com:{repo_id} {key} -->\n"
        f"Repository: {REPO}\nEpic issue: #{epic}\nWork accountability skill: 0.7.3\n"
        "Lifecycle view: 1\n"
        "<!-- work-accountability:end-project -->\n"
    )


def legacy_board(
    world: World,
    title: str,
    epic: int,
    key: str,
    stories: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """A per-epic Project as skill 0.7.3 left it: one legacy Lifecycle board."""
    state = world.state
    project = sim.add_project(state, title, readme=legacy_readme(state, key, epic), repositories=[REPO])
    project["views"].clear()
    sim.add_field(state, project, "Work phase", "SINGLE_SELECT", [(p, "") for p in (
        "Backlog", "Designing", "Ready", "Executing", "Acceptance", "Release ready", "Done")])
    sim.add_field(state, project, "Health", "SINGLE_SELECT", [(h, "") for h in ("On track", "At risk", "Blocked")])
    sim.add_field(state, project, "Source freshness", "SINGLE_SELECT", [(f, "") for f in ("Current", "Reconciliation needed")])
    sim.add_field(state, project, "Priority", "SINGLE_SELECT", [(p, "") for p in PRIORITIES])
    sim.add_field(state, project, "Rank", "NUMBER")
    phase = sim.field_by_name(project, "Work phase")["id"]
    sim.add_view(
        state, project, "Lifecycle", "BOARD_LAYOUT",
        filter=f"parent-issue:{REPO}#{epic}", vertical=[phase],
        sort=[[sim.field_by_name(project, "Priority")["id"], "ASC"], [sim.field_by_name(project, "Rank")["id"], "ASC"]],
    )
    item = sim.add_item(state, project, REPO, epic)
    sim.set_value(project, item, "Health", "On track")
    sim.set_value(project, item, "Source freshness", "Current")
    for number, values in stories.items():
        item = sim.add_item(state, project, REPO, number)
        for name, value in values.items():
            sim.set_value(project, item, name, value)
    return project


def v3_manifest(key: str, epic: int, stories: dict[int, tuple[str, str]]) -> dict[str, Any]:
    """The manifest a 0.7.3 agent kept on disk for one per-epic board."""
    items = [{"number": epic, "work_key": key, "kind": "epic", "evidence": {}}]
    for number, (story_key, phase) in stories.items():
        items.append(
            {"number": number, "work_key": story_key, "kind": "story", "work_phase": phase,
             "evidence": evidence(story_key, phase)}
        )
    return {
        "schema": "github-work-accountability/project-v3",
        "repository": REPO,
        "scope": {"epic_number": epic, "epic_work_key": key},
        "project": {"owner": "acme", "title": "t", "priority_options": PRIORITIES, "lifecycle_only": True},
        "items": items,
    }
