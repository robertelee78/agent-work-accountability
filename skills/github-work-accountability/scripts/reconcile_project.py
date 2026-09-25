#!/usr/bin/env python3
"""Reconcile one planning document's epic tree into a repository-linked GitHub Project.

The command is intentionally state-oriented: it inventories GitHub, computes a
delta, applies only that delta, and reads the result back.  It never infers work
phase from GitHub activity; callers provide an evidence-bound desired state.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


SCHEMA = "github-work-accountability/project-v4"
LEGACY_SCHEMAS = ("github-work-accountability/project-v3",)
SKILL_VERSION = "0.8.2"
MAX_DEPTH = 3
API_VERSION = "2026-03-10"
MANAGED_KEY = re.compile(r"<!--\s*work-accountability:key\s+([^\s]+)\s*-->")
MANAGED_ISSUE_BLOCK = re.compile(
    r"<!-- work-accountability:begin -->.*?<!-- work-accountability:end -->",
    re.DOTALL,
)
STORAGE_PROFILE_LINE = re.compile(r"^Storage profile:\s*`[^`]+`\s*$", re.MULTILINE)
PROJECT_LINE = re.compile(r"^Project:\s*\S+\s*$", re.MULTILINE)
PROJECT_MARKER = re.compile(
    r"<!--\s*work-accountability:project-v2\s+([^\s]+)\s+([^\s]+)\s*-->"
)
SUPERSEDED_MARKER = re.compile(
    r"<!--\s*work-accountability:project-superseded\s+([^\s]+)\s+([^\s]+)\s+->\s+([^\s]+)\s*-->"
)
PROJECT_BLOCK = re.compile(
    r"(?:\n)?<!-- work-accountability:begin-project -->.*?"
    r"<!-- work-accountability:end-project -->(?:\n)?",
    re.DOTALL,
)
PHASES = (
    "Backlog",
    "Designing",
    "Ready",
    "Executing",
    "Acceptance",
    "Release ready",
    "Done",
)
HEALTH = ("On track", "At risk", "Blocked")
HEALTH_SEVERITY = {"On track": 0, "At risk": 1, "Blocked": 2}
FRESHNESS = ("Current", "Reconciliation needed")
REQUIRED_EVIDENCE = {
    "Ready": ("design_approval",),
    "Executing": ("design_approval", "attempt"),
    "Acceptance": ("design_approval", "attempt", "candidate"),
    "Release ready": ("design_approval", "attempt", "candidate", "verdict"),
    "Done": ("design_approval", "attempt", "candidate", "verdict", "delivery"),
}
COLORS = ("RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "PURPLE", "GRAY", "PINK")
EXIT_TEMPORARY = 75
# GitHub's web page names fields with spaces by their hyphenated lowercase form.
# It rejects the quoted form ('has:"Work phase"': "Invalid value ... for has"),
# which the API accepts; Safari then renders a blank Project.  Observed 2026-09-25.
LIFECYCLE_FILTER = "has:work-phase"
REJECTED_LIFECYCLE_FILTERS = ('has:"Work phase"',)
LIFECYCLE_VIEW = "Lifecycle"
SECTION_VIEW = "By section"
GUARDED_FIELDS = ("Work phase", "Health", "Source freshness", "Priority", "Rank")
SECTION_OPTION_PREFIX = "work-accountability:section "


class ReconcileError(RuntimeError):
    """A deterministic reconciliation refusal."""


class TemporaryFailure(ReconcileError):
    """A retryable transport or budget failure."""


@dataclass(frozen=True)
class Evidence:
    ref: str
    work_key: str
    requirement: str
    candidate: str | None = None
    author: str | None = None
    implementer: str | None = None
    ref_kind: str | None = None
    attempt_id: str | None = None
    actor: str | None = None
    started_at: str | None = None
    state: str | None = None


@dataclass
class DesiredItem:
    number: int
    work_key: str
    kind: str
    parent: str | None
    work_phase: str | None
    health: str | None
    source_freshness: str
    priority: str | None
    rank: float | None
    evidence: Mapping[str, Evidence]
    section_label: str | None = None
    depth: int = 0
    section: str = ""
    progress: str | None = None


@dataclass(frozen=True)
class Acknowledgement:
    project: int
    number: int
    field: str
    before: Any
    after: Any
    reason: str


@dataclass(frozen=True)
class Manifest:
    repository: str
    root_number: int
    root_work_key: str
    source: Mapping[str, Any]
    project_owner: str
    project_title: str
    priority_options: tuple[str, ...]
    general_section_label: str
    supersedes: tuple[int, ...]
    acknowledged: tuple[Acknowledgement, ...]
    observed: Mapping[int, Mapping[str, Any]]
    items: tuple[DesiredItem, ...]
    digest: str
    raw: Mapping[str, Any]

    def by_number(self) -> dict[int, DesiredItem]:
        return {item.number: item for item in self.items}

    def leaf_numbers(self) -> set[int]:
        return {item.number for item in self.items if item.kind == "story"}

    def epic_numbers(self) -> set[int]:
        return {item.number for item in self.items if item.kind == "epic"}

    def section_labels(self) -> dict[str, str]:
        """Map each Section option name to the work key that owns it."""
        labels = {self.general_section_label: f"{self.root_work_key}:general"}
        for item in self.items:
            if item.section_label:
                labels[item.section_label] = item.work_key
        return labels


@dataclass
class ManagedIssue:
    number: int
    node_id: str
    database_id: int
    title: str
    state: str
    body: str
    work_key: str
    html_url: str
    labels: tuple[str, ...]


@dataclass
class FieldState:
    id: str
    database_id: int | None
    name: str
    data_type: str
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ViewState:
    id: str
    number: int
    name: str
    layout: str
    filter: str | None
    vertical_group_ids: list[str]
    sort_fields: list[tuple[str, str]]
    group_ids: list[str] = field(default_factory=list)
    visible_ids: list[str] = field(default_factory=list)
    position: int = 0


@dataclass
class ItemState:
    id: str
    content_id: str
    number: int
    repository: str
    archived: bool
    values: dict[str, Any]


@dataclass
class ProjectState:
    id: str
    number: int
    title: str
    url: str
    readme: str
    short_description: str
    closed: bool
    creator: str | None
    created_at: str | None
    repositories: set[str]
    fields: dict[str, FieldState] = field(default_factory=dict)
    views: dict[int, ViewState] = field(default_factory=dict)
    items: dict[int, ItemState] = field(default_factory=dict)
    other_items: list[str] = field(default_factory=list)


@dataclass
class RepositoryState:
    id: str
    name_with_owner: str
    owner_login: str
    owner_id: str
    owner_type: str
    linked_projects: list[ProjectState]


@dataclass
class Receipt:
    schema: str = "github-work-accountability/receipt-v2"
    skill_version: str = SKILL_VERSION
    repository: str = ""
    manifest_digest: str = ""
    actor: str = ""
    project_number: int | None = None
    project_url: str | None = None
    lifecycle_view: int | None = None
    lifecycle_url: str | None = None
    section_view: int | None = None
    section_url: str | None = None
    migration_id: str | None = None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    attached_parents: list[str] = field(default_factory=list)
    unmanaged_items: list[str] = field(default_factory=list)
    kept_live_values: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    planned_mutations: list[str] = field(default_factory=list)
    applied_mutations: list[str] = field(default_factory=list)
    graphql_requests: int = 0
    rest_requests: int = 0
    graphql_cost: int = 0
    rate_remaining: int | None = None
    rate_reset: str | int | None = None
    verified: bool = False


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconcileError(f"{path} must be a non-empty string")
    return value.strip()


def parse_evidence(value: Any, path: str, work_key: str) -> Evidence:
    if not isinstance(value, dict):
        raise ReconcileError(f"{path} must be an object")
    evidence = Evidence(
        ref=require_string(value.get("ref"), f"{path}.ref"),
        work_key=require_string(value.get("work_key"), f"{path}.work_key"),
        requirement=require_string(value.get("requirement"), f"{path}.requirement"),
        candidate=value.get("candidate"),
        author=value.get("author"),
        implementer=value.get("implementer"),
        ref_kind=value.get("ref_kind"),
        attempt_id=value.get("attempt_id"),
        actor=value.get("actor"),
        started_at=value.get("started_at"),
        state=value.get("state"),
    )
    if evidence.work_key != work_key:
        raise ReconcileError(
            f"{path}.work_key {evidence.work_key!r} does not match item key {work_key!r}"
        )
    return evidence


def validate_attempt_evidence(
    evidence: Evidence,
    path: str,
    phase: str,
    repository: str,
    issue_number: int,
) -> None:
    if evidence.ref_kind not in {"issue_comment", "communication_event", "tracker_event"}:
        raise ReconcileError(
            f"{path}.ref_kind must identify an issue_comment, communication_event, or tracker_event"
        )
    require_string(evidence.attempt_id, f"{path}.attempt_id")
    require_string(evidence.actor, f"{path}.actor")
    started_at = require_string(evidence.started_at, f"{path}.started_at")
    try:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReconcileError(f"{path}.started_at must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ReconcileError(f"{path}.started_at must include a timezone")
    expected_state = "active" if phase == "Executing" else "submitted"
    if evidence.state != expected_state:
        raise ReconcileError(
            f"{path}.state must be {expected_state!r} for Work phase {phase!r}"
        )
    if re.search(r"(?:^|/)commit/[0-9a-fA-F]{40}/?$", evidence.ref):
        raise ReconcileError(
            f"{path}.ref must name the attempt-start event, not an implementation commit"
        )
    if evidence.ref_kind == "issue_comment":
        expected = re.compile(
            rf"^https://[^/]+/{re.escape(repository)}/issues/{issue_number}#issuecomment-[0-9]+$"
        )
        if not expected.fullmatch(evidence.ref):
            raise ReconcileError(
                f"{path}.ref must name an issue comment on #{issue_number} in {repository}"
            )


def validate_story_evidence(
    prefix: str,
    phase: str | None,
    evidence: Mapping[str, Evidence],
    repository: str,
    number: int,
    attempt_ids: set[str],
    attempt_refs: set[str],
) -> None:
    if phase in REQUIRED_EVIDENCE:
        missing = [name for name in REQUIRED_EVIDENCE[phase] if name not in evidence]
        if missing:
            raise ReconcileError(f"{prefix}: {phase} requires evidence: {', '.join(missing)}")
        if "attempt" in REQUIRED_EVIDENCE[phase]:
            validate_attempt_evidence(
                evidence["attempt"], f"{prefix}.evidence.attempt", phase, repository, number
            )
            attempt = evidence["attempt"]
            assert attempt.attempt_id is not None
            if attempt.attempt_id in attempt_ids:
                raise ReconcileError(f"{prefix}.evidence.attempt.attempt_id is reused across stories")
            if attempt.ref in attempt_refs:
                raise ReconcileError(f"{prefix}.evidence.attempt.ref is reused across stories")
            attempt_ids.add(attempt.attempt_id)
            attempt_refs.add(attempt.ref)
    if phase in {"Release ready", "Done"}:
        verdict = evidence["verdict"]
        candidate = evidence["candidate"].ref
        if verdict.candidate != candidate:
            raise ReconcileError(f"{prefix}.evidence.verdict must bind candidate {candidate}")
        if not verdict.author or not verdict.implementer:
            raise ReconcileError(f"{prefix}.evidence.verdict needs author and implementer")
        if verdict.author.casefold() == verdict.implementer.casefold():
            raise ReconcileError(f"{prefix}.evidence.verdict is not independent")
    if phase == "Done" and evidence["delivery"].candidate != evidence["candidate"].ref:
        raise ReconcileError(f"{prefix}.evidence.delivery must bind the accepted candidate")


def worst_health(values: Iterable[str]) -> str:
    worst = "On track"
    for value in values:
        if HEALTH_SEVERITY[value] > HEALTH_SEVERITY[worst]:
            worst = value
    return worst


def progress_text(stories: Sequence[DesiredItem]) -> str:
    done = sum(1 for story in stories if story.work_phase == "Done")
    text = f"{done}/{len(stories)} Done"
    blocked = sum(1 for story in stories if story.health == "Blocked")
    at_risk = sum(1 for story in stories if story.health == "At risk")
    if blocked:
        text += f" · {blocked} blocked"
    if at_risk:
        text += f" · {at_risk} at risk"
    return text


def derive_tree(items: Sequence[DesiredItem], general_label: str) -> None:
    """Compute section membership and epic rollups from story values."""
    by_key = {item.work_key: item for item in items}
    children: dict[str, list[DesiredItem]] = {}
    for item in items:
        if item.parent is not None:
            children.setdefault(item.parent, []).append(item)

    def leaves(item: DesiredItem) -> list[DesiredItem]:
        if item.kind == "story":
            return [item]
        found: list[DesiredItem] = []
        for child in children.get(item.work_key, []):
            found.extend(leaves(child))
        return found

    for item in items:
        ancestor = item
        while ancestor.parent is not None and by_key[ancestor.parent].parent is not None:
            ancestor = by_key[ancestor.parent]
        if item.parent is None:
            item.section = general_label
        elif ancestor.kind == "epic" and ancestor.section_label:
            item.section = ancestor.section_label
        else:
            item.section = general_label
        if item.kind == "epic":
            stories = leaves(item)
            item.progress = progress_text(stories)
            item.health = worst_health(story.health or "On track" for story in stories)


def parse_observed(raw: Any, numbers: set[int]) -> dict[int, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ReconcileError(
            "observed must be an object recording the live values each item had when the "
            "manifest was drafted; run --draft to produce it"
        )
    observed: dict[int, dict[str, Any]] = {}
    for key, values in raw.items():
        try:
            number = int(key)
        except ValueError as error:
            raise ReconcileError(f"observed key {key!r} is not an issue number") from error
        if number not in numbers:
            raise ReconcileError(f"observed names #{number}, which is not a manifest item")
        if not isinstance(values, dict) or set(values) - set(GUARDED_FIELDS):
            raise ReconcileError(
                f"observed[{key}] must be an object with only {', '.join(GUARDED_FIELDS)}"
            )
        observed[number] = dict(values)
    missing = sorted(numbers - set(observed))
    if missing:
        raise ReconcileError(
            "observed omits " + ", ".join(f"#{number}" for number in missing)
            + "; run --draft to record what you read before changing it"
        )
    return observed


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconcileError(f"cannot read manifest {path}: {error}") from error
    if isinstance(raw, dict) and raw.get("schema") in LEGACY_SCHEMAS:
        raise ReconcileError(
            f"manifest schema {raw.get('schema')!r} is no longer accepted; regenerate it as "
            f"{SCHEMA!r} with --draft (see references/project-reconciliation.md)"
        )
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ReconcileError(f"manifest schema must be {SCHEMA!r}")
    repository = require_string(raw.get("repository"), "repository")
    if repository.count("/") != 1:
        raise ReconcileError("repository must be OWNER/REPOSITORY")
    repo_owner, repo_name = repository.split("/", 1)
    scope = raw.get("scope")
    if not isinstance(scope, dict):
        raise ReconcileError("scope must be an object")
    root_number = scope.get("root_number")
    if not isinstance(root_number, int) or isinstance(root_number, bool) or root_number <= 0:
        raise ReconcileError("scope.root_number must be a positive integer")
    root_work_key = require_string(scope.get("root_work_key"), "scope.root_work_key")
    if not root_work_key.startswith(f"{repository}:"):
        raise ReconcileError("scope.root_work_key must be qualified by repository")
    source = scope.get("source") or {}
    if not isinstance(source, dict):
        raise ReconcileError("scope.source must be an object")
    project = raw.get("project") or {}
    if not isinstance(project, dict):
        raise ReconcileError("project must be an object")
    if "lifecycle_only" in project:
        raise ReconcileError(
            "project.lifecycle_only was removed in project-v4; the managed views are "
            "Lifecycle and By section, and views people made are kept"
        )
    project_owner = require_string(project.get("owner", repo_owner), "project.owner")
    project_title = require_string(
        project.get("title", f"{repo_name} — {root_work_key.rsplit(':', 1)[-1]}"),
        "project.title",
    )
    raw_priority = project.get("priority_options", ["High", "Medium", "Low"])
    if not isinstance(raw_priority, list) or not raw_priority:
        raise ReconcileError("project.priority_options must be a non-empty array")
    priority_options = tuple(require_string(v, "project.priority_options[]") for v in raw_priority)
    if len({v.casefold() for v in priority_options}) != len(priority_options):
        raise ReconcileError("project.priority_options contains duplicates")
    general_label = require_string(
        project.get("general_section_label", f"{root_work_key.rsplit(':', 1)[-1]} (general)"),
        "project.general_section_label",
    )

    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ReconcileError("items must be a non-empty array")
    items: list[DesiredItem] = []
    numbers: set[int] = set()
    keys: set[str] = set()
    attempt_ids: set[str] = set()
    attempt_refs: set[str] = set()
    for index, value in enumerate(raw_items):
        prefix = f"items[{index}]"
        if not isinstance(value, dict):
            raise ReconcileError(f"{prefix} must be an object")
        number = value.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise ReconcileError(f"{prefix}.number must be a positive integer")
        work_key = require_string(value.get("work_key"), f"{prefix}.work_key")
        if not work_key.startswith(f"{repository}:"):
            raise ReconcileError(f"{prefix}.work_key is not qualified by {repository}:")
        kind = value.get("kind", "story")
        if kind not in {"story", "epic"}:
            raise ReconcileError(f"{prefix}.kind must be 'story' or 'epic'")
        parent = value.get("parent")
        if parent is not None:
            parent = require_string(parent, f"{prefix}.parent")
        phase = value.get("work_phase")
        if phase is not None and phase not in PHASES:
            raise ReconcileError(f"{prefix}.work_phase is not a recognized Work phase")
        if kind == "epic" and phase is not None:
            raise ReconcileError(f"{prefix}: epic Work phase must be omitted; epics are rollups")
        if kind == "story" and phase is None:
            raise ReconcileError(f"{prefix}: a story needs work_phase")
        health = value.get("health", "On track" if kind == "story" else None)
        freshness = value.get("source_freshness", "Current")
        if health is not None and health not in HEALTH:
            raise ReconcileError(f"{prefix}.health is not recognized")
        if freshness not in FRESHNESS:
            raise ReconcileError(f"{prefix}.source_freshness is not recognized")
        priority = value.get("priority")
        if priority is not None and priority not in priority_options:
            raise ReconcileError(f"{prefix}.priority is absent from project.priority_options")
        rank = value.get("rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, (int, float))):
            raise ReconcileError(f"{prefix}.rank must be numeric")
        section_label = value.get("section_label")
        if section_label is not None:
            section_label = require_string(section_label, f"{prefix}.section_label")
        raw_evidence = value.get("evidence") or {}
        if not isinstance(raw_evidence, dict):
            raise ReconcileError(f"{prefix}.evidence must be an object")
        evidence = {
            name: parse_evidence(item, f"{prefix}.evidence.{name}", work_key)
            for name, item in raw_evidence.items()
        }
        validate_story_evidence(
            prefix, phase, evidence, repository, number, attempt_ids, attempt_refs
        )
        if number in numbers:
            raise ReconcileError(f"duplicate manifest issue number #{number}")
        if work_key in keys:
            raise ReconcileError(f"duplicate manifest work key {work_key}")
        numbers.add(number)
        keys.add(work_key)
        items.append(
            DesiredItem(
                number=number,
                work_key=work_key,
                kind=kind,
                parent=parent,
                work_phase=phase,
                health=health,
                source_freshness=freshness,
                priority=priority,
                rank=float(rank) if rank is not None else None,
                evidence=evidence,
                section_label=section_label,
            )
        )

    roots = [item for item in items if item.parent is None]
    if len(roots) != 1:
        raise ReconcileError(
            "exactly one item must omit parent: the root epic named by scope"
        )
    root = roots[0]
    if root.number != root_number or root.work_key != root_work_key or root.kind != "epic":
        raise ReconcileError(
            "the item without a parent must be the root epic matching scope.root_number "
            "and scope.root_work_key"
        )
    by_key = {item.work_key: item for item in items}
    for item in items:
        if item.parent is None:
            continue
        parent_item = by_key.get(item.parent)
        if parent_item is None:
            raise ReconcileError(f"#{item.number} names unknown parent {item.parent}")
        if parent_item.kind != "epic":
            raise ReconcileError(
                f"#{item.number} names parent #{parent_item.number}, which is a story; "
                "only epics have children"
            )
    for item in items:
        seen: set[str] = set()
        cursor = item
        depth = 0
        while cursor.parent is not None:
            if cursor.work_key in seen:
                raise ReconcileError(f"manifest parent chain through #{item.number} is a cycle")
            seen.add(cursor.work_key)
            cursor = by_key[cursor.parent]
            depth += 1
        if cursor is not root:
            raise ReconcileError(f"#{item.number} does not descend from the root epic")
        if depth > MAX_DEPTH:
            raise ReconcileError(
                f"#{item.number} sits {depth} levels below the root; at most {MAX_DEPTH} "
                "levels (root → section → subsection → story) are allowed"
            )
        item.depth = depth
    labels: dict[str, int] = {general_label.casefold(): root.number}
    for item in items:
        if item.section_label is not None and not (item.kind == "epic" and item.depth == 1):
            raise ReconcileError(
                f"#{item.number}: section_label belongs only on epics directly under the root"
            )
        if item.kind == "epic" and item.depth == 1:
            if item.section_label is None:
                raise ReconcileError(f"#{item.number}: a section epic needs section_label")
            folded = item.section_label.casefold()
            if folded in labels:
                raise ReconcileError(
                    f"#{item.number}: section_label {item.section_label!r} is already used"
                )
            labels[folded] = item.number
    declared_epic_health = {item.number: item.health for item in items if item.kind == "epic"}
    for item in items:
        if item.kind == "story":
            continue
        descendants = [
            other for other in items
            if other.number != item.number and item.work_key in ancestors_of(other, by_key)
        ]
        if item.source_freshness == "Current" and any(
            other.source_freshness == "Reconciliation needed" for other in descendants
        ):
            raise ReconcileError(
                f"epic #{item.number} is Current while a descendant needs reconciliation"
            )
    derive_tree(items, general_label)
    for number, declared in declared_epic_health.items():
        derived = next(item.health for item in items if item.number == number)
        if declared is not None and declared != derived:
            raise ReconcileError(
                f"epic #{number} declares Health {declared!r}, but its stories make it "
                f"{derived!r}; omit epic health or correct the stories"
            )

    raw_supersedes = raw.get("supersedes", [])
    if not isinstance(raw_supersedes, list) or any(
        not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in raw_supersedes
    ):
        raise ReconcileError("supersedes must be an array of Project numbers")
    if len(set(raw_supersedes)) != len(raw_supersedes):
        raise ReconcileError("supersedes lists a Project more than once")
    raw_ack = raw.get("acknowledged_changes", [])
    if not isinstance(raw_ack, list):
        raise ReconcileError("acknowledged_changes must be an array")
    acknowledged: list[Acknowledgement] = []
    for index, value in enumerate(raw_ack):
        prefix = f"acknowledged_changes[{index}]"
        if not isinstance(value, dict):
            raise ReconcileError(f"{prefix} must be an object")
        project_number = value.get("project")
        number = value.get("number")
        if not isinstance(project_number, int) or project_number not in raw_supersedes:
            raise ReconcileError(f"{prefix}.project must be a Project listed in supersedes")
        if not isinstance(number, int) or number not in numbers:
            raise ReconcileError(f"{prefix}.number must be a manifest item")
        field_name = value.get("field")
        if field_name not in {"Work phase", "Health", "Source freshness", "Priority"}:
            raise ReconcileError(
                f"{prefix}.field must be Work phase, Health, Source freshness, or Priority"
            )
        if "from" not in value or "to" not in value:
            raise ReconcileError(f"{prefix} needs from and to")
        acknowledged.append(
            Acknowledgement(
                project=project_number,
                number=number,
                field=field_name,
                before=value["from"],
                after=value["to"],
                reason=require_string(value.get("reason"), f"{prefix}.reason"),
            )
        )
    observed = parse_observed(raw.get("observed"), numbers)
    return Manifest(
        repository=repository,
        root_number=root_number,
        root_work_key=root_work_key,
        source=source,
        project_owner=project_owner,
        project_title=project_title,
        priority_options=priority_options,
        general_section_label=general_label,
        supersedes=tuple(raw_supersedes),
        acknowledged=tuple(acknowledged),
        observed=observed,
        items=tuple(items),
        digest=hashlib.sha256(canonical_json(raw).encode()).hexdigest(),
        raw=raw,
    )


def ancestors_of(item: DesiredItem, by_key: Mapping[str, DesiredItem]) -> list[str]:
    found: list[str] = []
    cursor = item
    while cursor.parent is not None:
        found.append(cursor.parent)
        cursor = by_key[cursor.parent]
    return found


class GhTransport:
    def __init__(
        self,
        host: str,
        user: str | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.requested_user = user
        self.sleep = sleep
        self.monotonic = monotonic
        self.env = os.environ.copy()
        self.env["GH_HOST"] = host
        self.last_mutation = 0.0
        self.graphql_requests = 0
        self.rest_requests = 0
        self.graphql_cost = 0
        if user:
            token = self._run(["gh", "auth", "token", "--hostname", host, "--user", user])
            if not token.stdout.strip():
                raise ReconcileError(f"gh returned no stored token for {user}@{host}")
            self.env["GH_TOKEN"] = token.stdout.strip()
        identity = self.rest("user")
        self.login = require_string(identity.get("login"), "authenticated login")
        if user and self.login.casefold() != user.casefold():
            raise ReconcileError(
                f"requested GitHub actor {user!r}, token resolved to {self.login!r}"
            )

    def _run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
        allow_failure_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(command),
            input=input_text,
            env=dict(env) if env is not None else self.env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if completed.returncode and not allow_failure_output:
            message = (completed.stderr or completed.stdout).strip()
            if "rate limit" in message.casefold() or "secondary rate" in message.casefold():
                raise TemporaryFailure(message)
            raise ReconcileError(message or f"command failed: {command!r}")
        return completed

    def _pace_mutation(self) -> None:
        # GitHub asks integrators to space content-creating requests.  The
        # interval is configurable so local simulations need not wait.
        interval = float(os.environ.get("WORK_ACCOUNTABILITY_MUTATION_INTERVAL", "1.0"))
        now = self.monotonic()
        remaining = interval - (now - self.last_mutation)
        if self.last_mutation and remaining > 0:
            self.sleep(remaining)
        self.last_mutation = self.monotonic()

    def rest(
        self, endpoint: str, *, method: str = "GET", data: Any | None = None
    ) -> Any:
        mutating = method.upper() not in {"GET", "HEAD", "OPTIONS"}
        if mutating:
            self._pace_mutation()
        command = ["gh", "api", "--hostname", self.host]
        if method.upper() != "GET":
            command.extend(["--method", method.upper()])
        command.extend(["-H", "Accept: application/vnd.github+json"])
        command.extend(["-H", f"X-GitHub-Api-Version: {API_VERSION}"])
        if data is not None:
            command.extend(["--input", "-"])
        command.append(endpoint)
        completed = self._run(
            command,
            input_text=canonical_json(data) if data is not None else None,
        )
        self.rest_requests += 1
        return json.loads(completed.stdout or "null")

    def rest_optional(self, endpoint: str) -> Any | None:
        """GET a resource whose absence is a normal answer (HTTP 404)."""
        try:
            return self.rest(endpoint)
        except TemporaryFailure:
            raise
        except ReconcileError as error:
            if "HTTP 404" in str(error) or "Not Found" in str(error):
                return None
            raise

    def graphql(self, query: str, variables: Mapping[str, Any], *, mutation: bool = False) -> Any:
        if mutation:
            self._pace_mutation()
        payload = canonical_json({"query": query, "variables": variables})
        completed = self._run(
            ["gh", "api", "--hostname", self.host, "graphql", "--input", "-"],
            input_text=payload,
            # gh exits nonzero when a GraphQL response contains errors, even
            # though stdout still contains the partial response.  Parse that
            # response so callers can distinguish an API error from a CLI or
            # transport failure.  Reconciliation always re-inventories on the
            # next run, so a partially successful mutation is safe to resume.
            allow_failure_output=True,
        )
        self.graphql_requests += 1
        try:
            result = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as error:
            message = (completed.stderr or completed.stdout).strip()
            if "rate limit" in message.casefold() or "secondary rate" in message.casefold():
                raise TemporaryFailure(message) from error
            raise ReconcileError(message or "gh returned an invalid GraphQL response") from error
        rate = (result.get("data") or {}).get("rateLimit") or {}
        self.graphql_cost += int(rate.get("cost") or 0)
        if result.get("errors"):
            descriptions = []
            for error in result["errors"]:
                path = error.get("path")
                suffix = f" at {'.'.join(str(part) for part in path)}" if path else ""
                descriptions.append(f"{error.get('message', str(error))}{suffix}")
            messages = "; ".join(descriptions)
            if "rate limit" in messages.casefold():
                raise TemporaryFailure(messages)
            raise ReconcileError(f"GraphQL returned errors: {messages}")
        if completed.returncode:
            message = (completed.stderr or completed.stdout).strip()
            if "rate limit" in message.casefold() or "secondary rate" in message.casefold():
                raise TemporaryFailure(message)
            raise ReconcileError(message or "gh GraphQL command failed")
        return result.get("data") or {}


class FileLocks:
    def __init__(self, root: Path, keys: Iterable[str]) -> None:
        self.root = root
        self.keys = sorted(keys)
        self.stack = ExitStack()

    def __enter__(self) -> "FileLocks":
        self.root.mkdir(parents=True, exist_ok=True)
        for key in self.keys:
            name = hashlib.sha256(key.encode()).hexdigest() + ".lock"
            handle = self.stack.enter_context((self.root / name).open("a+", encoding="utf-8"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} key={key}\n")
            handle.flush()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stack.close()


def state_root() -> Path:
    return Path(
        os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
        )
    ) / "agent-work-accountability"


def pending_root(host: str, repository: str) -> Path:
    owner, repo = repository.split("/", 1)
    return state_root() / "pending" / host / owner / repo


def create_intent_path(root: Path, root_work_key: str) -> Path:
    scope = hashlib.sha256(root_work_key.encode()).hexdigest()[:16]
    return root / f"create-intent-{scope}.json"


def persist_desired(manifest_path: Path, manifest: Manifest, host: str) -> Path:
    root = pending_root(host, manifest.repository)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{manifest.digest}.json"
    if not destination.exists():
        shutil.copyfile(manifest_path, destination)
    return destination


def append_journal(root: Path, event: Mapping[str, Any]) -> None:
    payload = dict(event)
    payload["at"] = datetime.now(timezone.utc).isoformat()
    with (root / "journal.ndjson").open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")


def rate_status(transport: GhTransport) -> dict[str, Any]:
    return rate_resources(transport).get("graphql") or {}


def rate_resources(transport: GhTransport) -> dict[str, Any]:
    result = transport.rest("rate_limit")
    return (result or {}).get("resources") or {}


def require_budget(transport: GhTransport, graphql_points: int, rest_calls: int, where: str) -> None:
    resources = rate_resources(transport)
    graphql = resources.get("graphql") or {}
    core = resources.get("core") or {}
    remaining = int(graphql.get("remaining") or 0)
    if remaining < graphql_points:
        raise TemporaryFailure(
            f"{where}: GraphQL budget {remaining} is below the {graphql_points} points this run "
            f"needs; resets at {graphql.get('reset')}. Nothing further was written; rerun after reset."
        )
    core_remaining = core.get("remaining")
    if core_remaining is not None and int(core_remaining) < rest_calls:
        raise TemporaryFailure(
            f"{where}: REST budget {core_remaining} is below the {rest_calls} calls this run "
            f"needs; resets at {core.get('reset')}. Nothing further was written; rerun after reset."
        )


def list_managed_issues(transport: GhTransport, repository: str) -> dict[int, ManagedIssue]:
    owner, repo = repository.split("/", 1)
    found: dict[int, ManagedIssue] = {}
    keys: dict[str, int] = {}
    page = 1
    while True:
        values = transport.rest(
            f"repos/{owner}/{repo}/issues?state=all&per_page=100&page={page}"
        )
        if not isinstance(values, list):
            raise ReconcileError("GitHub issue inventory was not an array")
        for raw in values:
            if "pull_request" in raw:
                continue
            body = raw.get("body") or ""
            matches = MANAGED_KEY.findall(body)
            if not matches:
                continue
            unique = list(dict.fromkeys(matches))
            if len(unique) != 1:
                raise ReconcileError(f"issue #{raw.get('number')} has multiple managed work keys")
            key = unique[0]
            if not key.startswith(f"{repository}:"):
                raise ReconcileError(
                    f"issue #{raw.get('number')} contains cross-repository work key {key}"
                )
            number = int(raw["number"])
            if key in keys and keys[key] != number:
                raise ReconcileError(f"work key {key} occurs in issues #{keys[key]} and #{number}")
            keys[key] = number
            found[number] = ManagedIssue(
                number=number,
                node_id=raw["node_id"],
                database_id=int(raw["id"]),
                title=raw["title"],
                state=raw["state"],
                body=body,
                work_key=key,
                html_url=raw["html_url"],
                labels=tuple(
                    label["name"] if isinstance(label, dict) else str(label)
                    for label in raw.get("labels") or []
                ),
            )
        if len(values) < 100:
            break
        page += 1
    return found


@dataclass
class GitHubTree:
    parent: dict[int, int]
    has_children: dict[int, bool]


def list_sub_issues(transport: GhTransport, repository: str, number: int) -> list[Mapping[str, Any]]:
    owner, repo = repository.split("/", 1)
    found: list[Mapping[str, Any]] = []
    page = 1
    while True:
        values = transport.rest(
            f"repos/{owner}/{repo}/issues/{number}/sub_issues?per_page=100&page={page}"
        )
        if not isinstance(values, list):
            raise ReconcileError(f"sub-issue inventory for #{number} was not an array")
        found.extend(values)
        if len(values) < 100:
            return found
        page += 1


def issue_repository(raw: Mapping[str, Any]) -> str | None:
    url = raw.get("repository_url")
    if isinstance(url, str) and "/repos/" in url:
        return url.split("/repos/", 1)[1]
    repository = raw.get("repository")
    if isinstance(repository, Mapping):
        return repository.get("full_name")
    return None


def read_github_tree(
    transport: GhTransport, manifest: Manifest
) -> GitHubTree:
    """Walk native sub-issues below every epic the manifest names, recursively."""
    tree = GitHubTree(parent={}, has_children={})
    pending = [manifest.root_number] + sorted(manifest.epic_numbers() - {manifest.root_number})
    visited: set[int] = set()
    while pending:
        number = pending.pop(0)
        if number in visited:
            continue
        visited.add(number)
        children = list_sub_issues(transport, manifest.repository, number)
        tree.has_children[number] = bool(children)
        for raw in children:
            child = raw.get("number")
            if not isinstance(child, int) or child <= 0:
                raise ReconcileError(f"sub-issue inventory for #{number} omitted an issue number")
            child_repository = issue_repository(raw)
            if child_repository and child_repository.casefold() != manifest.repository.casefold():
                raise ReconcileError(
                    f"#{number} has sub-issue {child_repository}#{child} from another repository; "
                    "a document tree stays in one repository"
                )
            if child in tree.parent and tree.parent[child] != number:
                raise ReconcileError(f"#{child} appears under both #{tree.parent[child]} and #{number}")
            if child == manifest.root_number:
                raise ReconcileError(f"the root #{child} is its own descendant")
            tree.parent[child] = number
            summary = raw.get("sub_issues_summary")
            if isinstance(summary, Mapping):
                tree.has_children[child] = int(summary.get("total") or 0) > 0
            if tree.has_children.get(child) and child not in visited:
                pending.append(child)
    return tree


def issue_parent(transport: GhTransport, repository: str, number: int) -> int | None:
    owner, repo = repository.split("/", 1)
    raw = transport.rest_optional(f"repos/{owner}/{repo}/issues/{number}/parent")
    if not raw:
        return None
    parent_repository = issue_repository(raw)
    if parent_repository and parent_repository.casefold() != repository.casefold():
        raise ReconcileError(f"#{number} has parent {parent_repository}#{raw.get('number')} in another repository")
    return int(raw["number"])


def check_root_is_document_root(
    transport: GhTransport, manifest: Manifest, issues: Mapping[int, ManagedIssue]
) -> None:
    parent = issue_parent(transport, manifest.repository, manifest.root_number)
    if parent is not None and parent in issues:
        raise ReconcileError(
            f"#{manifest.root_number} belongs to managed epic #{parent} "
            f"({issues[parent].work_key}); reconcile the document root instead of "
            "giving one section its own Project"
        )


def validate_manifest_against_issues(
    transport: GhTransport,
    manifest: Manifest,
    issues: Mapping[int, ManagedIssue],
    tree: GitHubTree,
) -> tuple[dict[int, ManagedIssue], list[tuple[int, int]]]:
    """Require the manifest to be the exact native tree; return missing parent links."""
    desired = manifest.by_number()
    by_key = {item.work_key: item for item in manifest.items}
    for number, item in desired.items():
        issue = issues.get(number)
        if not issue:
            raise ReconcileError(f"manifest issue #{number} is not a managed issue in {manifest.repository}")
        if issue.work_key != item.work_key:
            raise ReconcileError(
                f"issue #{number} has work key {issue.work_key}, manifest requested {item.work_key}"
            )
    under_tree = set(tree.parent)
    unmanaged = sorted(under_tree - set(issues))
    if unmanaged:
        raise ReconcileError(
            "the document tree contains issues without work-accountability identities: "
            + ", ".join(f"#{number}" for number in unmanaged)
        )
    extra = sorted(under_tree - set(desired))
    if extra:
        raise ReconcileError(
            "the manifest omits native sub-issues of its epics: "
            + ", ".join(f"#{number} (under #{tree.parent[number]})" for number in extra)
        )
    missing: list[tuple[int, int]] = []
    moved: list[str] = []
    for item in manifest.items:
        if item.parent is None:
            if item.number in tree.parent:
                raise ReconcileError(f"the root #{item.number} is a sub-issue of #{tree.parent[item.number]}")
            continue
        expected = by_key[item.parent].number
        actual = tree.parent.get(item.number)
        if actual is None:
            actual = issue_parent(transport, manifest.repository, item.number)
            if actual is None:
                missing.append((expected, item.number))
                continue
        if actual != expected:
            moved.append(f"#{item.number} is under #{actual} on GitHub, manifest says #{expected}")
    if moved:
        raise ReconcileError("manifest parents disagree with GitHub: " + "; ".join(moved))
    for item in manifest.items:
        if item.kind == "story" and tree.has_children.get(item.number):
            raise ReconcileError(
                f"#{item.number} has native sub-issues but the manifest calls it a story; "
                "declare it kind epic"
            )
    missing_children = {child for _parent, child in missing}
    owner, repo = manifest.repository.split("/", 1)
    for number in sorted(missing_children):
        if desired[number].kind != "story":
            continue
        raw = transport.rest(f"repos/{owner}/{repo}/issues/{number}")
        summary = (raw or {}).get("sub_issues_summary") or {}
        if int(summary.get("total") or 0) > 0:
            raise ReconcileError(
                f"#{number} has native sub-issues but the manifest calls it a story; "
                "declare it kind epic"
            )
    return {number: issues[number] for number in sorted(desired)}, sorted(missing)


def attach_parents(
    transport: GhTransport,
    manifest: Manifest,
    issues: Mapping[int, ManagedIssue],
    edges: Sequence[tuple[int, int]],
    receipt: Receipt,
) -> None:
    owner, repo = manifest.repository.split("/", 1)
    depth = {item.number: item.depth for item in manifest.items}
    for parent, child in sorted(edges, key=lambda edge: (depth[edge[1]], edge)):
        current = issue_parent(transport, manifest.repository, child)
        if current == parent:
            continue
        if current is not None:
            raise ReconcileError(f"#{child} gained parent #{current} while attaching it to #{parent}")
        label = f"attach #{child} under #{parent}"
        receipt.planned_mutations.append(label)
        transport.rest(
            f"repos/{owner}/{repo}/issues/{parent}/sub_issues",
            method="POST",
            data={"sub_issue_id": issues[child].database_id},
        )
        if issue_parent(transport, manifest.repository, child) != parent:
            raise ReconcileError(f"GitHub did not show #{child} under #{parent} after attaching it")
        receipt.applied_mutations.append(label)
        receipt.attached_parents.append(f"#{parent} > #{child}")


def issue_project_projection(
    issue: ManagedIssue,
    project_url: str,
) -> tuple[str, tuple[str, ...]]:
    match = MANAGED_ISSUE_BLOCK.search(issue.body)
    if not match:
        raise ReconcileError(f"managed issue #{issue.number} has no bounded managed block")
    block = match.group(0)
    if STORAGE_PROFILE_LINE.search(block):
        block = STORAGE_PROFILE_LINE.sub("Storage profile: `project-fields`", block, count=1)
    else:
        key_match = MANAGED_KEY.search(block)
        if not key_match:
            raise ReconcileError(f"managed issue #{issue.number} has no key in its managed block")
        insertion = key_match.end()
        block = block[:insertion] + "\nStorage profile: `project-fields`" + block[insertion:]
    desired_project = f"Project: {project_url}"
    if PROJECT_LINE.search(block):
        block = PROJECT_LINE.sub(desired_project, block, count=1)
    else:
        profile = STORAGE_PROFILE_LINE.search(block)
        assert profile is not None
        block = block[: profile.end()] + "\n" + desired_project + block[profile.end() :]
    body = issue.body[: match.start()] + block + issue.body[match.end() :]
    labels = tuple(
        label
        for label in issue.labels
        if not label.startswith(("phase/", "health/", "source/"))
    )
    return body, labels


def ensure_issue_projection(
    transport: GhTransport,
    repository: str,
    issues: Mapping[int, ManagedIssue],
    project_url: str,
    receipt: Receipt,
) -> None:
    owner, repo = repository.split("/", 1)
    for number in sorted(issues):
        issue = issues[number]
        body, labels = issue_project_projection(issue, project_url)
        if body == issue.body and labels == issue.labels:
            continue
        # Rebuild from a fresh read so an edit made during this run is kept.
        fresh = transport.rest(f"repos/{owner}/{repo}/issues/{number}")
        issue = ManagedIssue(
            number=number,
            node_id=fresh["node_id"],
            database_id=int(fresh["id"]),
            title=fresh["title"],
            state=fresh["state"],
            body=fresh.get("body") or "",
            work_key=issue.work_key,
            html_url=fresh["html_url"],
            labels=tuple(
                raw["name"] if isinstance(raw, dict) else str(raw)
                for raw in fresh.get("labels") or []
            ),
        )
        if MANAGED_KEY.findall(issue.body)[:1] != [issues[number].work_key]:
            raise ReconcileError(f"issue #{number} lost or changed its work key during this run")
        body, labels = issue_project_projection(issue, project_url)
        if body == issue.body and labels == issue.labels:
            continue
        label = f"bind issue #{issue.number} to Project fields"
        receipt.planned_mutations.append(label)
        updated = transport.rest(
            f"repos/{owner}/{repo}/issues/{issue.number}",
            method="PATCH",
            data={"body": body, "labels": list(labels)},
        )
        returned_labels = tuple(
            raw["name"] if isinstance(raw, dict) else str(raw)
            for raw in updated.get("labels") or []
        )
        if updated.get("body") != body or set(returned_labels) != set(labels):
            raise ReconcileError(
                f"issue #{issue.number} Project-profile read-back disagreed with the request"
            )
        receipt.applied_mutations.append(label)


def project_fragment() -> str:
    return """
      id number title url readme shortDescription closed createdAt
      creator { login }
      repositories(first:100) { nodes { nameWithOwner } pageInfo { hasNextPage } }
    """


def discover_repository(transport: GhTransport, repository: str) -> tuple[RepositoryState, list[ProjectState]]:
    owner, name = repository.split("/", 1)
    query = f"""
      query($owner:String!, $name:String!) {{
        repository(owner:$owner,name:$name) {{
          id nameWithOwner
          owner {{ __typename id login }}
          projectsV2(first:100) {{ nodes {{ {project_fragment()} }} pageInfo {{ hasNextPage }} }}
        }}
        rateLimit {{ cost remaining resetAt }}
      }}
    """
    data = transport.graphql(query, {"owner": owner, "name": name})
    raw_repo = data.get("repository")
    if not raw_repo:
        raise ReconcileError(f"repository {repository} is unavailable to {transport.login}")
    if raw_repo["projectsV2"]["pageInfo"]["hasNextPage"]:
        raise ReconcileError("repository has more than 100 linked Projects; pagination is required")
    linked = [parse_project_summary(raw) for raw in raw_repo["projectsV2"]["nodes"]]
    owner_data = raw_repo["owner"]
    owner_type = owner_data["__typename"]
    if owner_type not in {"Organization", "User"}:
        raise ReconcileError(f"unsupported repository owner type {owner_type}")
    owner_field = "organization" if owner_type == "Organization" else "user"
    query_owner = f"""
      query($login:String!,$after:String) {{
        {owner_field}(login:$login) {{
          projectsV2(first:100,after:$after) {{
            nodes {{ {project_fragment()} }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}
        rateLimit {{ cost remaining resetAt }}
      }}
    """
    all_projects: list[ProjectState] = []
    after: str | None = None
    while True:
        page = transport.graphql(query_owner, {"login": owner_data["login"], "after": after})
        connection = page[owner_field]["projectsV2"]
        all_projects.extend(parse_project_summary(raw) for raw in connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        after = connection["pageInfo"]["endCursor"]
    return (
        RepositoryState(
            id=raw_repo["id"],
            name_with_owner=raw_repo["nameWithOwner"],
            owner_login=owner_data["login"],
            owner_id=owner_data["id"],
            owner_type=owner_type,
            linked_projects=linked,
        ),
        all_projects,
    )


def parse_project_summary(raw: Mapping[str, Any]) -> ProjectState:
    repos = raw.get("repositories") or {"nodes": [], "pageInfo": {"hasNextPage": False}}
    if (repos.get("pageInfo") or {}).get("hasNextPage"):
        raise ReconcileError(f"Project {raw.get('number')} links more than 100 repositories")
    return ProjectState(
        id=raw["id"],
        number=int(raw["number"]),
        title=raw["title"],
        url=raw["url"],
        readme=raw.get("readme") or "",
        short_description=raw.get("shortDescription") or "",
        closed=bool(raw.get("closed")),
        creator=(raw.get("creator") or {}).get("login"),
        created_at=raw.get("createdAt"),
        repositories={node["nameWithOwner"] for node in repos.get("nodes", [])},
    )


def marker_scopes(project: ProjectState, host: str, repository_id: str) -> list[str]:
    return [
        scope
        for identity, scope in PROJECT_MARKER.findall(project.readme)
        if identity == f"{host}:{repository_id}"
    ]


def superseded_by(project: ProjectState, host: str, repository_id: str) -> list[tuple[str, str]]:
    return [
        (scope, destination)
        for identity, scope, destination in SUPERSEDED_MARKER.findall(project.readme)
        if identity == f"{host}:{repository_id}"
    ]


def marked_for(
    project: ProjectState, host: str, repository_id: str, root_work_key: str
) -> bool:
    return root_work_key in marker_scopes(project, host, repository_id)


def select_project(
    projects: Sequence[ProjectState],
    repo: RepositoryState,
    host: str,
    root_work_key: str,
    adopt_number: int | None,
    intent: Mapping[str, Any] | None,
    actor: str,
) -> ProjectState | None:
    for project in projects:
        for scope, destination in superseded_by(project, host, repo.id):
            if scope == root_work_key:
                raise ReconcileError(
                    f"Project #{project.number} for {root_work_key} was superseded by {destination}; "
                    "reconcile the document root that owns it instead"
                )
    marked = [
        project
        for project in projects
        if marked_for(project, host, repo.id, root_work_key)
    ]
    if len(marked) > 1:
        raise ReconcileError(
            "multiple canonical Projects found: "
            + ", ".join(f"#{p.number} {p.url}" for p in marked)
        )
    if marked:
        if adopt_number is not None and marked[0].number != adopt_number:
            raise ReconcileError(
                f"--adopt-project {adopt_number} conflicts with marked Project {marked[0].number}"
            )
        return marked[0]
    if adopt_number is not None:
        matches = [project for project in projects if project.number == adopt_number]
        if len(matches) != 1:
            raise ReconcileError(f"Project {adopt_number} is not uniquely visible to the repository owner")
        foreign_scopes = [
            scope for scope in marker_scopes(matches[0], host, repo.id) if scope != root_work_key
        ]
        if foreign_scopes:
            raise ReconcileError(
                f"Project {adopt_number} is already managed for {foreign_scopes[0]}; "
                "merge it by listing it in supersedes instead of adopting it"
            )
        return matches[0]
    if intent:
        candidates = [
            project
            for project in projects
            if project.title == intent.get("title")
            and repo.name_with_owner in project.repositories
            and (project.creator or "").casefold() == actor.casefold()
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ReconcileError("create intent matches multiple unmarked Projects; refusing to create")
    return None


def tree_projects(
    projects: Sequence[ProjectState],
    repo: RepositoryState,
    host: str,
    manifest: Manifest,
) -> dict[int, ProjectState]:
    """Projects marked for any non-root work key in this document's tree."""
    keys = {item.work_key for item in manifest.items if item.parent is not None}
    return {
        project.number: project
        for project in projects
        if any(scope in keys for scope in marker_scopes(project, host, repo.id))
    }


def managed_readme(
    current: str,
    host: str,
    repo: RepositoryState,
    manifest: Manifest,
    lifecycle_view: int | None,
    section_view: int | None = None,
    initial_views: Sequence[str] = (),
) -> str:
    block = [
        "<!-- work-accountability:begin-project -->",
        f"<!-- work-accountability:project-v2 {host}:{repo.id} {manifest.root_work_key} -->",
        f"Repository: {repo.name_with_owner}",
        f"Root issue: #{manifest.root_number}",
    ]
    source_path = manifest.source.get("path") if isinstance(manifest.source, Mapping) else None
    if source_path:
        block.append(f"Planning source: {source_path}")
    block.append(f"Work accountability skill: {SKILL_VERSION}")
    if lifecycle_view is not None:
        block.append(f"Lifecycle view: {lifecycle_view}")
    if section_view is not None:
        block.append(f"Section view: {section_view}")
    if initial_views:
        block.append(f"Initial views: {' '.join(initial_views)}")
    block.append("<!-- work-accountability:end-project -->")
    cleaned = PROJECT_BLOCK.sub("\n", current).strip()
    return (cleaned + "\n\n" if cleaned else "") + "\n".join(block) + "\n"


def superseded_readme(
    current: str, host: str, repo: RepositoryState, scope: str, destination_url: str
) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    block = [
        "<!-- work-accountability:begin-project -->",
        f"<!-- work-accountability:project-superseded {host}:{repo.id} {scope} -> {destination_url} -->",
        f"Superseded by {destination_url} on {today}; closed, not deleted.",
        "Reopen it by hand if it is ever needed again.",
        "<!-- work-accountability:end-project -->",
    ]
    cleaned = PROJECT_BLOCK.sub("\n", current).strip()
    return (cleaned + "\n\n" if cleaned else "") + "\n".join(block) + "\n"


def mutate_one(transport: GhTransport, name: str, input_type: str, payload: Mapping[str, Any], selection: str) -> Any:
    query = f"""
      mutation($input:{input_type}!) {{
        result:{name}(input:$input) {{ {selection} }}
      }}
    """
    return transport.graphql(query, {"input": payload}, mutation=True)["result"]


def create_project(
    transport: GhTransport,
    manifest: Manifest,
    repo: RepositoryState,
    root: Path,
) -> ProjectState:
    intent = {
        "schema": "github-work-accountability/create-intent-v3",
        "repository_id": repo.id,
        "repository": repo.name_with_owner,
        "root_work_key": manifest.root_work_key,
        "root_number": manifest.root_number,
        "owner": manifest.project_owner,
        "title": manifest.project_title,
        "actor": transport.login,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    create_intent_path(root, manifest.root_work_key).write_text(
        canonical_json(intent) + "\n", encoding="utf-8"
    )
    result = mutate_one(
        transport,
        "createProjectV2",
        "CreateProjectV2Input",
        {
            "ownerId": repo.owner_id,
            "repositoryId": repo.id,
            "title": manifest.project_title,
            "clientMutationId": f"work-accountability:{manifest.digest}:create-project",
        },
        f"projectV2 {{ {project_fragment()} }}",
    )
    return parse_project_summary(result["projectV2"])


def update_project_metadata(
    transport: GhTransport,
    project: ProjectState,
    title: str,
    readme: str,
) -> ProjectState:
    if project.title == title and project.readme == readme and not project.closed:
        return project
    result = mutate_one(
        transport,
        "updateProjectV2",
        "UpdateProjectV2Input",
        {
            "projectId": project.id,
            "title": title,
            "readme": readme,
            "closed": False,
            "clientMutationId": f"work-accountability:{project.id}:metadata",
        },
        f"projectV2 {{ {project_fragment()} }}",
    )
    return parse_project_summary(result["projectV2"])


def supersede_project(
    transport: GhTransport,
    project: ProjectState,
    readme: str,
) -> ProjectState:
    """Record the successor and close in one write, so no crash leaves half of it."""
    result = mutate_one(
        transport,
        "updateProjectV2",
        "UpdateProjectV2Input",
        {
            "projectId": project.id,
            "readme": readme,
            "closed": True,
            "clientMutationId": f"work-accountability:{project.id}:supersede",
        },
        f"projectV2 {{ {project_fragment()} }}",
    )
    return parse_project_summary(result["projectV2"])


def ensure_link(transport: GhTransport, project: ProjectState, repo: RepositoryState) -> None:
    if repo.name_with_owner in project.repositories:
        return
    mutate_one(
        transport,
        "linkProjectV2ToRepository",
        "LinkProjectV2ToRepositoryInput",
        {
            "projectId": project.id,
            "repositoryId": repo.id,
            "clientMutationId": f"work-accountability:{project.id}:link:{repo.id}",
        },
        "repository { id nameWithOwner }",
    )


def field_selection() -> str:
    return """
      nodes {
        __typename
        ... on ProjectV2Field { id databaseId name dataType }
        ... on ProjectV2SingleSelectField {
          id databaseId name dataType
          options { id name color description }
        }
      }
      pageInfo { hasNextPage endCursor }
    """


def parse_field(raw: Mapping[str, Any]) -> FieldState | None:
    if not raw.get("name"):
        return None
    return FieldState(
        id=raw["id"],
        database_id=raw.get("databaseId"),
        name=raw["name"],
        data_type=raw.get("dataType") or "",
        options=[dict(option) for option in raw.get("options") or []],
    )


def load_project_detail(
    transport: GhTransport,
    owner_type: str,
    owner: str,
    number: int,
    repository: str,
) -> ProjectState:
    owner_field = "organization" if owner_type == "Organization" else "user"
    query = f"""
      query($login:String!,$number:Int!) {{
        {owner_field}(login:$login) {{
          projectV2(number:$number) {{
            {project_fragment()}
            fields(first:100) {{ {field_selection()} }}
            views(first:100) {{
              nodes {{
                id number name layout filter
                verticalGroupByFields(first:10) {{ nodes {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} ... on ProjectV2IterationField {{ id name }} }} }}
                groupByFields(first:10) {{ nodes {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} ... on ProjectV2IterationField {{ id name }} }} }}
                fields(first:30) {{ nodes {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} ... on ProjectV2IterationField {{ id name }} }} }}
                sortByFields(first:10) {{ nodes {{ direction field {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} ... on ProjectV2IterationField {{ id name }} }} }} }}
              }}
              pageInfo {{ hasNextPage }}
            }}
            items(first:100) {{
              nodes {{
                id isArchived
                content {{ __typename ... on Issue {{ id number repository {{ nameWithOwner }} }} ... on PullRequest {{ number repository {{ nameWithOwner }} }} ... on DraftIssue {{ title }} }}
                fieldValues(first:100) {{
                  nodes {{
                    __typename
                    ... on ProjectV2ItemFieldSingleSelectValue {{ name optionId field {{ ... on ProjectV2SingleSelectField {{ id name }} }} }}
                    ... on ProjectV2ItemFieldNumberValue {{ number field {{ ... on ProjectV2Field {{ id name }} }} }} ... on ProjectV2ItemFieldTextValue {{ text field {{ ... on ProjectV2Field {{ id name }} }} }}
                  }}
                  pageInfo {{ hasNextPage }}
                }}
              }}
              pageInfo {{ hasNextPage endCursor }}
            }}
          }}
        }}
        rateLimit {{ cost remaining resetAt }}
      }}
    """
    data = transport.graphql(query, {"login": owner, "number": number})
    raw = data[owner_field]["projectV2"]
    if not raw:
        raise ReconcileError(f"Project {owner}#{number} is unavailable")
    project = parse_project_summary(raw)
    fields = raw["fields"]
    if fields["pageInfo"]["hasNextPage"]:
        raise ReconcileError("Project has more than 100 fields; refusing incomplete inventory")
    for raw_field in fields["nodes"]:
        parsed = parse_field(raw_field)
        if parsed:
            if parsed.name in project.fields:
                raise ReconcileError(f"Project has duplicate field name {parsed.name!r}")
            project.fields[parsed.name] = parsed
    views = raw["views"]
    if views["pageInfo"]["hasNextPage"]:
        raise ReconcileError("Project has more than 100 views; refusing incomplete inventory")
    for position, raw_view in enumerate(views["nodes"]):
        vertical = [node["id"] for node in raw_view["verticalGroupByFields"]["nodes"] if node]
        grouped = [node["id"] for node in (raw_view.get("groupByFields") or {}).get("nodes", []) if node]
        visible = [node["id"] for node in (raw_view.get("fields") or {}).get("nodes", []) if node]
        sort_fields = [
            (node["field"]["id"], node["direction"])
            for node in raw_view["sortByFields"]["nodes"]
            if node.get("field")
        ]
        project.views[int(raw_view["number"])] = ViewState(
            id=raw_view["id"],
            number=int(raw_view["number"]),
            name=raw_view["name"],
            layout=raw_view["layout"],
            filter=raw_view.get("filter"),
            vertical_group_ids=vertical,
            sort_fields=sort_fields,
            group_ids=grouped,
            visible_ids=visible,
            position=position,
        )
    connection = raw["items"]
    parse_item_nodes(project, connection["nodes"], repository)
    after = connection["pageInfo"].get("endCursor")
    while connection["pageInfo"]["hasNextPage"]:
        page_query = f"""
          query($login:String!,$number:Int!,$after:String!) {{
            {owner_field}(login:$login) {{ projectV2(number:$number) {{
              items(first:100,after:$after) {{
                nodes {{
                  id isArchived content {{ __typename ... on Issue {{ id number repository {{ nameWithOwner }} }} ... on PullRequest {{ number repository {{ nameWithOwner }} }} ... on DraftIssue {{ title }} }}
                  fieldValues(first:100) {{
                    nodes {{
                      __typename
                      ... on ProjectV2ItemFieldSingleSelectValue {{ name optionId field {{ ... on ProjectV2SingleSelectField {{ id name }} }} }}
                      ... on ProjectV2ItemFieldNumberValue {{ number field {{ ... on ProjectV2Field {{ id name }} }} }} ... on ProjectV2ItemFieldTextValue {{ text field {{ ... on ProjectV2Field {{ id name }} }} }}
                    }}
                    pageInfo {{ hasNextPage }}
                  }}
                }}
                pageInfo {{ hasNextPage endCursor }}
              }}
            }} }}
            rateLimit {{ cost remaining resetAt }}
          }}
        """
        page = transport.graphql(
            page_query, {"login": owner, "number": number, "after": after}
        )
        connection = page[owner_field]["projectV2"]["items"]
        parse_item_nodes(project, connection["nodes"], repository)
        after = connection["pageInfo"].get("endCursor")
    return project


def parse_item_nodes(
    project: ProjectState,
    nodes: Sequence[Mapping[str, Any]],
    repository: str,
) -> None:
    for raw in nodes:
        content = raw.get("content") or {}
        if content.get("__typename") not in {None, "Issue"} or not content.get("number") or not content.get("repository"):
            kind = content.get("__typename") or "Item"
            if content.get("number") and content.get("repository"):
                name = f"{content['repository']['nameWithOwner']}#{content['number']}"
            else:
                name = content.get("title") or raw.get("id", "")
            project.other_items.append(f"{kind} {name}")
            continue
        values_connection = raw["fieldValues"]
        if values_connection["pageInfo"]["hasNextPage"]:
            raise ReconcileError(f"Project item {raw['id']} has more than 100 field values")
        values: dict[str, Any] = {}
        for value in values_connection["nodes"]:
            field_value = value.get("field") or {}
            name = field_value.get("name")
            if not name:
                continue
            if value["__typename"] == "ProjectV2ItemFieldSingleSelectValue":
                values[name] = value.get("name")
            elif value["__typename"] == "ProjectV2ItemFieldNumberValue":
                values[name] = value.get("number")
            elif value["__typename"] == "ProjectV2ItemFieldTextValue" and name != "Title":
                values[name] = value.get("text")
        number = int(content["number"])
        item_repository = content["repository"]["nameWithOwner"]
        if item_repository != repository:
            project.other_items.append(f"Issue {item_repository}#{number}")
            continue
        if number in project.items:
            raise ReconcileError(f"Project contains issue #{number} more than once")
        project.items[number] = ItemState(
            id=raw["id"],
            content_id=content["id"],
            number=number,
            repository=item_repository,
            archived=bool(raw.get("isArchived")),
            values=values,
        )


def load_project_until(
    transport: GhTransport,
    owner_type: str,
    owner: str,
    number: int,
    repository: str,
    predicate: Callable[[ProjectState], bool],
    description: str,
    *,
    delays: Sequence[float] = (0.0, 2.0, 4.0, 8.0, 16.0),
) -> ProjectState:
    """Bound GitHub's read-after-write delay without replaying a mutation."""
    latest: ProjectState | None = None
    for delay in delays:
        if delay:
            transport.sleep(delay)
        latest = load_project_detail(transport, owner_type, owner, number, repository)
        if predicate(latest):
            return latest
    raise ReconcileError(
        f"GitHub did not expose {description} after {sum(delays):g}s of read-only reconciliation"
    )


def expected_field_schema(manifest: Manifest) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {
        "Work phase": ("SINGLE_SELECT", PHASES),
        "Health": ("SINGLE_SELECT", HEALTH),
        "Source freshness": ("SINGLE_SELECT", FRESHNESS),
        "Priority": ("SINGLE_SELECT", manifest.priority_options),
        "Rank": ("NUMBER", ()),
        "Section": ("SINGLE_SELECT", tuple(manifest.section_labels())),
        "Progress": ("TEXT", ()),
    }


def field_option_payload(options: Sequence[str]) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "color": COLORS[index % len(COLORS)],
            "description": "Managed by github-work-accountability.",
        }
        for index, name in enumerate(options)
    ]


def section_option_payload(manifest: Manifest, names: Sequence[str], offset: int) -> list[dict[str, str]]:
    owners = manifest.section_labels()
    return [
        {
            "name": name,
            "color": COLORS[(offset + index) % len(COLORS)],
            "description": f"{SECTION_OPTION_PREFIX}{owners[name]}",
        }
        for index, name in enumerate(names)
    ]


def planned_options(
    manifest: Manifest, name: str, current: FieldState, options: Sequence[str]
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Return the full option list to send (existing IDs kept) and change labels.

    Options are only added or renamed.  GitHub replaces the whole option set on
    update, so every existing option is resent with its ID; a Section option is
    renamed in place when the section epic that owns it (recorded in the option
    description) now has a different label.
    """
    preserved = [
        {
            "id": option["id"],
            "name": option["name"],
            "color": option.get("color") or "GRAY",
            "description": option.get("description") or "",
        }
        for option in current.options
    ]
    changes: list[str] = []
    if name == "Section":
        owners = manifest.section_labels()
        by_owner = {
            option["description"][len(SECTION_OPTION_PREFIX):]: option
            for option in preserved
            if option["description"].startswith(SECTION_OPTION_PREFIX)
        }
        for label, owner in owners.items():
            option = by_owner.get(owner)
            if option and option["name"] != label:
                clash = [o for o in preserved if o is not option and o["name"].casefold() == label.casefold()]
                if clash:
                    raise ReconcileError(
                        f"cannot rename Section option {option['name']!r} to {label!r}: "
                        "another option already has that name"
                    )
                changes.append(f"rename Section option {option['name']!r} to {label!r}")
                option["name"] = label
    existing = {option["name"].casefold() for option in preserved}
    missing = [option for option in options if option.casefold() not in existing]
    if missing:
        changes.append(f"add options to field {name}: {', '.join(missing)}")
        if name == "Section":
            preserved.extend(section_option_payload(manifest, missing, len(preserved)))
        else:
            preserved.extend(field_option_payload(missing))
    return (preserved if changes else None), changes


def check_field_types(project: ProjectState, manifest: Manifest) -> None:
    """Refuse before any write when an existing field has the wrong type."""
    wrong = [
        f"Project field {name!r} has type {project.fields[name].data_type}, expected {data_type}"
        for name, (data_type, _options) in expected_field_schema(manifest).items()
        if name in project.fields and project.fields[name].data_type != data_type
    ]
    if wrong:
        raise ReconcileError("; ".join(wrong) + "; rename or remove that field, then rerun")


def ensure_fields(
    transport: GhTransport, project: ProjectState, manifest: Manifest, receipt: Receipt
) -> None:
    check_field_types(project, manifest)
    for name, (data_type, options) in expected_field_schema(manifest).items():
        current = project.fields.get(name)
        if current and current.data_type != data_type:
            raise ReconcileError(
                f"Project field {name!r} has type {current.data_type}, expected {data_type}"
            )
        if not current:
            receipt.planned_mutations.append(f"create field {name}")
            payload: dict[str, Any] = {
                "projectId": project.id,
                "dataType": data_type,
                "name": name,
                "clientMutationId": f"work-accountability:{project.id}:field:{name}",
            }
            if options:
                payload["singleSelectOptions"] = (
                    section_option_payload(manifest, options, 0)
                    if name == "Section"
                    else field_option_payload(options)
                )
            mutate_one(
                transport,
                "createProjectV2Field",
                "CreateProjectV2FieldInput",
                payload,
                "projectV2Field { __typename ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } }",
            )
            receipt.applied_mutations.append(f"create field {name}")
            continue
        if data_type != "SINGLE_SELECT":
            continue
        payload_options, changes = planned_options(manifest, name, current, options)
        if payload_options is None:
            continue
        receipt.planned_mutations.extend(changes)
        mutate_one(
            transport,
            "updateProjectV2Field",
            "UpdateProjectV2FieldInput",
            {
                "fieldId": current.id,
                "singleSelectOptions": payload_options,
                "clientMutationId": f"work-accountability:{current.id}:options",
            },
            "projectV2Field { ... on ProjectV2SingleSelectField { id name options { id name } } }",
        )
        receipt.applied_mutations.extend(changes)


def fields_ready(state: ProjectState, manifest: Manifest) -> bool:
    for name, (_data_type, options) in expected_field_schema(manifest).items():
        current = state.fields.get(name)
        if not current:
            return False
        names = {option["name"].casefold() for option in current.options}
        if any(option.casefold() not in names for option in options):
            return False
    return True


def batch_mutations(
    transport: GhTransport,
    mutation_name: str,
    input_type: str,
    payloads: Sequence[tuple[str, Mapping[str, Any]]],
    selection: str,
    *,
    cap: int = 25,
) -> list[str]:
    applied: list[str] = []
    for offset in range(0, len(payloads), cap):
        batch = payloads[offset : offset + cap]
        declarations = ",".join(f"$v{i}:{input_type}!" for i in range(len(batch)))
        aliases = "\n".join(
            f"m{i}:{mutation_name}(input:$v{i}) {{ {selection} }}" for i in range(len(batch))
        )
        query = f"mutation({declarations}) {{ {aliases} }}"
        variables = {f"v{i}": payload for i, (_label, payload) in enumerate(batch)}
        transport.graphql(query, variables, mutation=True)
        applied.extend(label for label, _payload in batch)
    return applied


def reconcile_membership(
    transport: GhTransport,
    project: ProjectState,
    desired_issues: Mapping[int, ManagedIssue],
    all_managed_issues: Mapping[int, ManagedIssue],
    repository: str,
    receipt: Receipt,
) -> None:
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    removals: list[tuple[str, Mapping[str, Any]]] = []
    for number, issue in sorted(desired_issues.items()):
        current = project.items.get(number)
        if current and current.repository == repository:
            if current.archived:
                raise ReconcileError(f"managed issue #{number} is archived in the canonical Project")
            continue
        label = f"add issue #{number}"
        receipt.planned_mutations.append(label)
        payloads.append(
            (
                label,
                {
                    "projectId": project.id,
                    "contentId": issue.node_id,
                    "clientMutationId": f"work-accountability:{project.id}:issue:{number}",
                },
            )
        )
    undesired = sorted(
        (set(project.items) & set(all_managed_issues)) - set(desired_issues)
    )
    for number in undesired:
        label = f"remove out-of-scope managed issue #{number}"
        receipt.planned_mutations.append(label)
        removals.append(
            (
                label,
                {
                    "projectId": project.id,
                    "itemId": project.items[number].id,
                    "clientMutationId": (
                        f"work-accountability:{project.id}:remove-issue:{number}"
                    ),
                },
            )
        )
    receipt.applied_mutations.extend(
        batch_mutations(
            transport,
            "addProjectV2ItemById",
            "AddProjectV2ItemByIdInput",
            payloads,
            "item { id }",
        )
    )
    receipt.applied_mutations.extend(
        batch_mutations(
            transport,
            "deleteProjectV2Item",
            "DeleteProjectV2ItemInput",
            removals,
            "deletedItemId",
        )
    )


def option_id(field_state: FieldState, value: str) -> str:
    matches = [option["id"] for option in field_state.options if option["name"].casefold() == value.casefold()]
    if len(matches) != 1:
        raise ReconcileError(f"field {field_state.name!r} has no unique option {value!r}")
    return matches[0]


def same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and not isinstance(left, bool) or isinstance(right, (int, float)) and not isinstance(right, bool):
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def guarded_desired(item: DesiredItem) -> dict[str, Any]:
    values: dict[str, Any] = {"Source freshness": item.source_freshness}
    if item.kind == "story":
        values["Work phase"] = item.work_phase
        values["Health"] = item.health
    if item.priority is not None:
        values["Priority"] = item.priority
    if item.rank is not None:
        values["Rank"] = item.rank
    return values


def plan_targets(
    manifest: Manifest, project: ProjectState | None, receipt: Receipt
) -> dict[int, dict[str, Any]]:
    """Apply the lost-update guard, then compute every value the board must show.

    A field the manifest changes (desired differs from what the author observed)
    may be written only if GitHub still shows what the author observed.  A field
    the manifest leaves alone keeps whatever GitHub shows now, so one agent's run
    cannot undo another agent's newer write.
    """
    conflicts: list[str] = []
    effective: list[DesiredItem] = []
    for item in manifest.items:
        live_item = project.items.get(item.number) if project else None
        live = live_item.values if live_item else {}
        observed = manifest.observed.get(item.number, {})
        copy = replace(item)
        for name, desired in guarded_desired(item).items():
            seen = observed.get(name)
            now = live.get(name)
            if same_value(desired, seen):
                if now is not None and not same_value(now, seen):
                    receipt.kept_live_values.append(
                        f"#{item.number} {name} stays {now!r} (changed by someone else; this manifest did not change it)"
                    )
                    if name == "Work phase":
                        copy.work_phase = now
                    elif name == "Health":
                        copy.health = now
                    elif name == "Source freshness":
                        copy.source_freshness = now
                    elif name == "Priority":
                        copy.priority = now
                    elif name == "Rank":
                        copy.rank = float(now)
            elif not same_value(now, seen) and not same_value(now, desired):
                conflicts.append(
                    f"#{item.number} {name}: you read {seen!r}, GitHub now shows {now!r}, you want {desired!r}"
                )
        effective.append(copy)
    if conflicts:
        raise ReconcileError(
            "these items changed since the manifest was drafted, so writing it would lose "
            "someone else's update: " + "; ".join(conflicts)
            + ". Nothing was written. Re-run --draft, re-apply your change, and retry."
        )
    derive_tree(effective, manifest.general_section_label)
    targets: dict[int, dict[str, Any]] = {}
    for item in effective:
        values: dict[str, Any] = {
            "Health": item.health,
            "Source freshness": item.source_freshness,
            "Section": item.section,
        }
        if item.kind == "story":
            values["Work phase"] = item.work_phase
        else:
            values["Progress"] = item.progress
        if item.priority is not None:
            values["Priority"] = item.priority
        if item.rank is not None:
            values["Rank"] = item.rank
        targets[item.number] = values
    return targets


def value_mismatches(
    project: ProjectState, manifest: Manifest, targets: Mapping[int, Mapping[str, Any]]
) -> list[str]:
    problems: list[str] = []
    kinds = {item.number: item.kind for item in manifest.items}
    for number, expected in targets.items():
        item = project.items.get(number)
        if not item:
            problems.append(f"#{number} is not a Project item")
            continue
        for name, value in expected.items():
            actual = item.values.get(name)
            if not same_value(actual, value):
                problems.append(f"#{number} {name} is {actual!r}, expected {value!r}")
        if "Status" in item.values:
            problems.append(f"#{number} still has built-in Status")
        if kinds[number] == "epic" and "Work phase" in item.values:
            problems.append(f"epic #{number} has a manually maintained Work phase")
        if kinds[number] == "story" and item.values.get("Progress"):
            problems.append(f"story #{number} carries an epic Progress value")
    return problems


def ensure_values(
    transport: GhTransport,
    project: ProjectState,
    manifest: Manifest,
    targets: Mapping[int, Mapping[str, Any]],
    receipt: Receipt,
) -> None:
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    clears: list[tuple[str, Mapping[str, Any]]] = []
    kinds = {item.number: item.kind for item in manifest.items}
    for number, expected in targets.items():
        item = project.items.get(number)
        if not item:
            raise ReconcileError(f"issue #{number} is still absent after membership reconciliation")
        for name, value in expected.items():
            if value is None or same_value(item.values.get(name), value):
                continue
            field_state = project.fields[name]
            if field_state.data_type == "NUMBER":
                field_value: dict[str, Any] = {"number": value}
            elif field_state.data_type == "TEXT":
                field_value = {"text": value}
            else:
                field_value = {"singleSelectOptionId": option_id(field_state, str(value))}
            label = f"set #{number} {name}={value}"
            receipt.planned_mutations.append(label)
            payloads.append(
                (
                    label,
                    {
                        "projectId": project.id,
                        "itemId": item.id,
                        "fieldId": field_state.id,
                        "value": field_value,
                        "clientMutationId": f"work-accountability:{project.id}:{number}:{name}",
                    },
                )
            )
        stale: list[tuple[str, str]] = []
        if "Status" in item.values and "Status" in project.fields:
            stale.append(("Status", "built-in Status"))
        if kinds[number] == "epic" and "Work phase" in item.values:
            stale.append(("Work phase", "epic Work phase"))
        if kinds[number] == "story" and item.values.get("Progress") and "Progress" in project.fields:
            stale.append(("Progress", "story Progress"))
        for field_name, description in stale:
            label = f"clear #{number} {description}"
            receipt.planned_mutations.append(label)
            clears.append(
                (
                    label,
                    {
                        "projectId": project.id,
                        "itemId": item.id,
                        "fieldId": project.fields[field_name].id,
                        "clientMutationId": (
                            f"work-accountability:{project.id}:{number}:clear:{field_name}"
                        ),
                    },
                )
            )
    receipt.applied_mutations.extend(
        batch_mutations(
            transport,
            "updateProjectV2ItemFieldValue",
            "UpdateProjectV2ItemFieldValueInput",
            payloads,
            "projectV2Item { id }",
        )
    )
    receipt.applied_mutations.extend(
        batch_mutations(
            transport,
            "clearProjectV2ItemFieldValue",
            "ClearProjectV2ItemFieldValueInput",
            clears,
            "projectV2Item { id }",
        )
    )


LEGACY_LIFECYCLE_FILTER = re.compile(r"^parent-issue:\S+#\d+$")


def read_view_marker(readme: str, label: str) -> int | None:
    match = re.search(rf"^{re.escape(label)} view:\s*(\d+)\s*$", readme, re.MULTILINE)
    return int(match.group(1)) if match else None


def read_lifecycle_marker(readme: str) -> int | None:
    return read_view_marker(readme, "Lifecycle")


def read_initial_views(readme: str) -> list[str]:
    match = re.search(r"^Initial views:\s*(.+?)\s*$", readme, re.MULTILINE)
    return match.group(1).split() if match else []


def sort_prefix_matches(view: ViewState, fields: Mapping[str, FieldState], names: Sequence[str]) -> bool:
    desired = [(fields[name].id, "ASC") for name in names if name in fields]
    actual = [(field_id, direction.upper()) for field_id, direction in view.sort_fields]
    return actual[: len(desired)] == desired


def lifecycle_view_valid(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    work_phase = fields.get("Work phase")
    if not work_phase:
        return False
    if view.layout != "BOARD_LAYOUT" or view.vertical_group_ids != [work_phase.id]:
        return False
    if view.filter != LIFECYCLE_FILTER:
        return False
    return sort_prefix_matches(view, fields, ("Priority", "Rank"))


def lifecycle_view_is_legacy(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    """An older Lifecycle board with the right shape but an outdated filter.

    0.7.x filtered to one epic's direct children; 0.8.0-0.8.1 wrote a quoted
    filter that GitHub's web page rejects.
    """
    work_phase = fields.get("Work phase")
    return bool(
        work_phase
        and view.layout == "BOARD_LAYOUT"
        and view.vertical_group_ids == [work_phase.id]
        and view.filter
        and (
            LEGACY_LIFECYCLE_FILTER.fullmatch(view.filter)
            or view.filter in REJECTED_LIFECYCLE_FILTERS
        )
    )


def section_view_valid(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    section = fields.get("Section")
    if not section:
        return False
    if view.layout != "TABLE_LAYOUT" or view.group_ids != [section.id]:
        return False
    if view.filter:
        return False
    return sort_prefix_matches(view, fields, ("Rank",))


def create_view(
    transport: GhTransport,
    project: ProjectState,
    repo: RepositoryState,
    manifest: Manifest,
    which: str,
) -> int:
    fields = project.fields
    if which == LIFECYCLE_VIEW:
        visible = ("Title", "Health", "Section", "Source freshness", "Priority", "Rank")
    else:
        visible = ("Title", "Work phase", "Health", "Progress", "Priority")
    required = [fields[name] for name in visible] + [fields["Work phase"], fields["Section"]]
    if any(field.database_id is None for field in required):
        raise ReconcileError("GitHub did not expose numeric field IDs required by the REST Views API")
    if which == LIFECYCLE_VIEW:
        payload: dict[str, Any] = {
            "name": LIFECYCLE_VIEW,
            "layout": "board",
            "filter": LIFECYCLE_FILTER,
            "visible_fields": [fields[name].database_id for name in visible],
            "sort_by": [
                [fields["Priority"].database_id, "asc"],
                [fields["Rank"].database_id, "asc"],
            ],
            "vertical_group_by": [fields["Work phase"].database_id],
        }
    else:
        payload = {
            "name": SECTION_VIEW,
            "layout": "table",
            "visible_fields": [fields[name].database_id for name in visible],
            "sort_by": [[fields["Rank"].database_id, "asc"]],
            "group_by": [fields["Section"].database_id],
        }
    prefix = "orgs" if repo.owner_type == "Organization" else "users"
    result = transport.rest(
        f"{prefix}/{manifest.project_owner}/projectsV2/{project.number}/views",
        method="POST",
        data=payload,
    )
    value = (result or {}).get("value") or result
    number = value.get("number") if isinstance(value, dict) else None
    if not isinstance(number, int):
        raise ReconcileError("REST Projects Views response omitted the view number")
    return number


def delete_views(
    transport: GhTransport,
    project: ProjectState,
    views: Sequence[ViewState],
    reason: str,
    receipt: Receipt,
) -> None:
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    for view in views:
        label = f"delete {reason} view #{view.number} {view.name!r}"
        receipt.planned_mutations.append(label)
        payloads.append(
            (
                label,
                {
                    "viewId": view.id,
                    "clientMutationId": f"work-accountability:{project.id}:delete-view:{view.id}",
                },
            )
        )
    receipt.applied_mutations.extend(
        batch_mutations(
            transport,
            "deleteProjectV2View",
            "DeleteProjectV2ViewInput",
            payloads,
            "projectV2View { id number name }",
        )
    )


@dataclass
class ViewPlan:
    lifecycle: int | None
    section: int | None
    delete: list[ViewState]
    create_lifecycle: bool
    create_section: bool
    notes: list[str]


def plan_views(
    project: ProjectState, repair: bool, fresh: bool
) -> ViewPlan:
    """Decide which managed views to keep, create, and delete.

    New views are always created before old ones are deleted (GitHub refuses to
    delete a Project's last view), so a crash in between can leave duplicates.
    A valid duplicate is adopted rather than created again.  Views people made
    are never deleted.
    """
    fields = project.fields
    notes: list[str] = []
    delete: list[ViewState] = []
    ready = fields_ready_for_views(fields)
    initial = set(read_initial_views(project.readme))

    def candidates(label: str, name: str) -> tuple[ViewState | None, list[ViewState]]:
        number = read_view_marker(project.readme, label)
        recorded = project.views.get(number) if number is not None else None
        named = [view for view in project.views.values() if view.name == name and view is not recorded]
        return recorded, sorted(named, key=lambda view: view.position)

    # Lifecycle
    recorded, named = candidates("Lifecycle", LIFECYCLE_VIEW)
    pool = ([recorded] if recorded else []) + named
    valid = [view for view in pool if ready and lifecycle_view_valid(view, fields)]
    lifecycle = valid[0] if valid else None
    for view in pool:
        if view is lifecycle:
            continue
        if view is recorded or (ready and lifecycle_view_valid(view, fields)):
            delete.append(view)
        elif ready and lifecycle_view_is_legacy(view, fields):
            delete.append(view)
        elif repair:
            delete.append(view)
        else:
            raise ReconcileError(
                f"an unrecorded view named {LIFECYCLE_VIEW!r} (#{view.number}) is not the managed "
                "board; rename it or rerun with --repair-lifecycle to replace it"
            )
        if ready and lifecycle_view_is_legacy(view, fields):
            notes.append(
                f"replace Lifecycle view #{view.number} (outdated filter {view.filter!r}) "
                f"with the whole-document board filtered by {LIFECYCLE_FILTER!r}"
            )
    create_lifecycle = lifecycle is None

    # By section: must come after Lifecycle.
    recorded, named = candidates("Section", SECTION_VIEW)
    pool = ([recorded] if recorded else []) + named
    after = [
        view for view in pool
        if ready and section_view_valid(view, fields)
        and not create_lifecycle and lifecycle is not None and view.position > lifecycle.position
    ]
    section = after[0] if after else None
    for view in pool:
        if view is section:
            continue
        if view is recorded or (ready and section_view_valid(view, fields)) or repair:
            delete.append(view)
        else:
            raise ReconcileError(
                f"an unrecorded view named {SECTION_VIEW!r} (#{view.number}) is not the managed "
                "section table; rename it or rerun with --repair-lifecycle to replace it"
            )
    create_section = section is None

    for view in project.views.values():
        if view in delete or view is lifecycle or view is section:
            continue
        if view.id in initial or (fresh and view.name.startswith("View ")):
            delete.append(view)
    return ViewPlan(
        lifecycle=lifecycle.number if lifecycle else None,
        section=section.number if section else None,
        delete=delete,
        create_lifecycle=create_lifecycle,
        create_section=create_section,
        notes=notes,
    )


def fields_ready_for_views(fields: Mapping[str, FieldState]) -> bool:
    return all(name in fields for name in ("Work phase", "Section", "Priority", "Rank"))


def evaluate_view(
    transport: GhTransport,
    owner_type: str,
    owner: str,
    number: int,
    view_filter: str | None,
) -> tuple[set[tuple[str, int]], int]:
    """Ask GitHub which cards a view's saved filter shows (the board's own filter engine)."""
    owner_field = "organization" if owner_type == "Organization" else "user"
    query = f"""
      query($login:String!,$number:Int!,$q:String,$after:String) {{
        {owner_field}(login:$login) {{ projectV2(number:$number) {{
          items(first:100,after:$after,query:$q) {{
            nodes {{ id content {{ __typename ... on Issue {{ number repository {{ nameWithOwner }} }} }} }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }} }}
        rateLimit {{ cost remaining resetAt }}
      }}
    """
    shown: set[tuple[str, int]] = set()
    others = 0
    after: str | None = None
    while True:
        data = transport.graphql(
            query, {"login": owner, "number": number, "q": view_filter or None, "after": after}
        )
        connection = data[owner_field]["projectV2"]["items"]
        for node in connection["nodes"]:
            content = node.get("content") or {}
            if content.get("__typename") == "Issue" and content.get("repository"):
                shown.add((content["repository"]["nameWithOwner"], int(content["number"])))
            else:
                others += 1
        if not connection["pageInfo"]["hasNextPage"]:
            return shown, others
        after = connection["pageInfo"]["endCursor"]


def verify_boards(
    transport: GhTransport,
    repo: RepositoryState,
    manifest: Manifest,
    project: ProjectState,
    lifecycle: ViewState,
    section: ViewState,
) -> None:
    """The user-equivalent gate: the saved filters must show the right cards."""
    repository = manifest.repository
    leaves = manifest.leaf_numbers()
    epics = manifest.epic_numbers()
    shown, _others = evaluate_view(
        transport, repo.owner_type, manifest.project_owner, project.number, lifecycle.filter
    )
    shown_here = {number for name, number in shown if name.casefold() == repository.casefold()}
    missing = sorted(leaves - shown_here)
    epic_cards = sorted(epics & shown_here)
    if missing:
        raise ReconcileError(
            "board check: the Lifecycle board does not show stories "
            + ", ".join(f"#{n}" for n in missing)
            + f" (filter {lifecycle.filter!r})"
        )
    if epic_cards:
        raise ReconcileError(
            "board check: the Lifecycle board shows epic cards "
            + ", ".join(f"#{n}" for n in epic_cards)
            + "; only stories belong there"
        )
    shown, _others = evaluate_view(
        transport, repo.owner_type, manifest.project_owner, project.number, section.filter
    )
    shown_here = {number for name, number in shown if name.casefold() == repository.casefold()}
    missing = sorted((leaves | epics) - shown_here)
    if missing:
        raise ReconcileError(
            "board check: the By section table does not show "
            + ", ".join(f"#{n}" for n in missing)
        )


def verify_issue_memberships(
    transport: GhTransport,
    repository: str,
    numbers: Sequence[int],
    project_id: str,
    project_items: Mapping[int, ItemState],
) -> None:
    owner, name = repository.split("/", 1)
    for offset in range(0, len(numbers), 50):
        batch = numbers[offset : offset + 50]
        aliases = "\n".join(
            f"i{number}:issue(number:{number}) {{ projectItems(first:100) {{ nodes {{ id project {{ id }} }} pageInfo {{ hasNextPage }} }} }}"
            for number in batch
        )
        query = f"""
          query($owner:String!,$name:String!) {{
            repository(owner:$owner,name:$name) {{ {aliases} }}
            rateLimit {{ cost remaining resetAt }}
          }}
        """
        data = transport.graphql(query, {"owner": owner, "name": name})
        raw_repo = data["repository"]
        for number in batch:
            connection = raw_repo[f"i{number}"]["projectItems"]
            if connection["pageInfo"]["hasNextPage"]:
                raise ReconcileError(f"issue #{number} belongs to more than 100 Projects")
            expected = project_items.get(number)
            matches = [node for node in connection["nodes"] if node["project"]["id"] == project_id]
            if len(matches) != 1 or not expected or matches[0]["id"] != expected.id:
                raise ReconcileError(
                    f"issue #{number} membership disagrees with the canonical Project collection"
                )


def verify_final(
    transport: GhTransport,
    manifest: Manifest,
    repo: RepositoryState,
    project: ProjectState,
    scoped_issues: Mapping[int, ManagedIssue],
    all_managed_issues: Mapping[int, ManagedIssue],
    targets: Mapping[int, Mapping[str, Any]],
    lifecycle_number: int,
    section_number: int,
) -> None:
    if repo.name_with_owner not in project.repositories:
        raise ReconcileError("final verification: Project is not linked to the repository")
    if not marked_for(project, transport.host, repo.id, manifest.root_work_key):
        raise ReconcileError("final verification: Project marker is absent")
    lifecycle = project.views.get(lifecycle_number)
    if not lifecycle or not lifecycle_view_valid(lifecycle, project.fields):
        raise ReconcileError("final verification: Lifecycle is not a whole-document Work phase Kanban")
    section = project.views.get(section_number)
    if not section or not section_view_valid(section, project.fields):
        raise ReconcileError("final verification: By section is not a table grouped by Section")
    if section.position < lifecycle.position:
        raise ReconcileError("final verification: By section comes before Lifecycle")
    for view_id in read_initial_views(project.readme):
        raise ReconcileError(f"final verification: GitHub's initial view {view_id} was not removed")
    for number in scoped_issues:
        item = project.items.get(number)
        if not item or item.repository != manifest.repository or item.archived:
            raise ReconcileError(f"final verification: issue #{number} is not an active Project item")
    managed_extras = sorted((set(project.items) & set(all_managed_issues)) - set(scoped_issues))
    if managed_extras:
        raise ReconcileError(
            "final verification: Project contains managed issues outside this document: "
            + ", ".join(f"#{number}" for number in managed_extras)
        )
    problems = value_mismatches(project, manifest, targets)
    if problems:
        raise ReconcileError("final verification: " + "; ".join(problems))
    verify_boards(transport, repo, manifest, project, lifecycle, section)
    verify_issue_memberships(
        transport,
        manifest.repository,
        sorted(scoped_issues),
        project.id,
        project.items,
    )


@dataclass
class Sources:
    pending: dict[int, ProjectState]
    done: dict[int, ProjectState]


def discover_sources(
    projects: Sequence[ProjectState],
    repo: RepositoryState,
    host: str,
    manifest: Manifest,
    destination: ProjectState | None,
) -> Sources:
    """Classify the per-epic Projects this document replaces.

    A board still marked for a work key inside the tree is pending.  A board that
    already records this destination as its successor is done, which is what makes
    a rerun after a crash resume instead of failing.
    """
    tree_keys = {item.work_key for item in manifest.items if item.parent is not None}
    by_number = {project.number: project for project in projects}
    marked = tree_projects(projects, repo, host, manifest)
    if destination is not None and destination.number in manifest.supersedes:
        raise ReconcileError(
            f"supersedes lists #{destination.number}, which is this document's own Project"
        )
    stray = sorted(set(marked) - set(manifest.supersedes))
    if stray:
        described = ", ".join(
            f"#{number} ({', '.join(s for s in marker_scopes(marked[number], host, repo.id) if s in tree_keys)})"
            for number in stray
        )
        raise ReconcileError(
            f"per-epic Projects exist for parts of this document: {described}. "
            "List them in supersedes so they are merged into the document Project and closed."
        )
    pending: dict[int, ProjectState] = {}
    done: dict[int, ProjectState] = {}
    for number in manifest.supersedes:
        project = by_number.get(number)
        if project is None:
            raise ReconcileError(f"supersedes lists Project #{number}, which the owner cannot see")
        if number in marked:
            pending[number] = project
            continue
        successors = [
            destination_url
            for scope, destination_url in superseded_by(project, host, repo.id)
            if scope in tree_keys
        ]
        if successors and destination is not None and all(url == destination.url for url in successors):
            done[number] = project
            continue
        if successors:
            raise ReconcileError(
                f"Project #{number} was already superseded by {successors[0]}, not by this document's Project"
            )
        raise ReconcileError(
            f"supersedes lists Project #{number}, which is not a board for any epic in this document"
        )
    return Sources(pending=pending, done=done)


def migration_key(manifest: Manifest) -> str:
    material = canonical_json({"root": manifest.root_work_key, "supersedes": sorted(manifest.supersedes)})
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def snapshot_root(host: str, manifest: Manifest) -> Path:
    owner, repo = manifest.repository.split("/", 1)
    return state_root() / "snapshots" / host / owner / repo / migration_key(manifest)


def project_record(project: ProjectState) -> dict[str, Any]:
    return {
        "id": project.id,
        "number": project.number,
        "title": project.title,
        "url": project.url,
        "readme": project.readme,
        "short_description": project.short_description,
        "closed": project.closed,
        "repositories": sorted(project.repositories),
        "fields": {
            name: {"id": f.id, "database_id": f.database_id, "data_type": f.data_type, "options": f.options}
            for name, f in sorted(project.fields.items())
        },
        "views": [
            {
                "number": view.number,
                "name": view.name,
                "layout": view.layout,
                "filter": view.filter,
                "vertical_group_ids": view.vertical_group_ids,
                "group_ids": view.group_ids,
                "visible_ids": view.visible_ids,
                "sort_fields": view.sort_fields,
                "position": view.position,
            }
            for view in sorted(project.views.values(), key=lambda v: v.position)
        ],
        "items": {
            str(number): {"id": item.id, "archived": item.archived, "values": item.values}
            for number, item in sorted(project.items.items())
        },
        "other_items": project.other_items,
    }


def write_snapshots(
    host: str,
    manifest: Manifest,
    details: Mapping[int, ProjectState],
    issues: Mapping[int, ManagedIssue],
    tree: GitHubTree,
    pinned: bool,
    receipt: Receipt,
) -> dict[int, dict[str, Any]]:
    """Record every board this migration will change before the first GitHub write.

    Once the migration has started writing, the first snapshots are pinned: a
    rerun compares against them instead of taking a fresh one.
    """
    root = snapshot_root(host, manifest)
    root.mkdir(parents=True, exist_ok=True)
    records: dict[int, dict[str, Any]] = {}
    for number, detail in sorted(details.items()):
        path = root / f"project-{number}.json"
        if pinned and path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
        else:
            record = {
                "schema": "github-work-accountability/project-snapshot-v1",
                "taken_at": datetime.now(timezone.utc).isoformat(),
                "repository": manifest.repository,
                "root_work_key": manifest.root_work_key,
                "project": project_record(detail),
                "issues": {
                    str(n): {
                        "work_key": issues[n].work_key,
                        "parent": tree.parent.get(n),
                        "managed_block": managed_block(issues[n].body),
                    }
                    for n in sorted(detail.items)
                    if n in issues
                },
            }
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(canonical_json(record) + "\n", encoding="utf-8")
            temporary.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records[number] = record
        receipt.snapshots.append({"project": number, "path": str(path), "sha256": digest})
    return records


def migration_started(root: Path, key: str) -> bool:
    journal = root / "journal.ndjson"
    if not journal.exists():
        return False
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "migration-started" and event.get("key") == key:
            return True
    return False


def migration_id(manifest: Manifest, destination_id: str, receipt: Receipt) -> str:
    material = canonical_json(
        {
            "root": manifest.root_work_key,
            "destination": destination_id,
            "supersedes": sorted(manifest.supersedes),
            "snapshots": sorted((entry["project"], entry["sha256"]) for entry in receipt.snapshots),
        }
    )
    return hashlib.sha256(material.encode()).hexdigest()


CONFLICT_FIELDS = ("Work phase", "Health", "Source freshness", "Priority")


def check_source_conflicts(
    manifest: Manifest, details: Mapping[int, ProjectState]
) -> list[str]:
    """Compare every old board's values with the manifest, per (board, issue)."""
    desired = manifest.by_number()
    acknowledgements = {
        (ack.project, ack.number, ack.field): ack for ack in manifest.acknowledged
    }
    problems: list[str] = []
    for project_number, detail in sorted(details.items()):
        for number, item in sorted(detail.items.items()):
            target = desired.get(number)
            if target is None:
                continue
            wanted: dict[str, Any] = {"Source freshness": target.source_freshness}
            if target.kind == "story":
                wanted["Work phase"] = target.work_phase
                wanted["Health"] = target.health
            if target.priority is not None:
                wanted["Priority"] = target.priority
            for name in CONFLICT_FIELDS:
                if name not in wanted:
                    continue
                old = item.values.get(name)
                if old is None or same_value(old, wanted[name]):
                    continue
                ack = acknowledgements.get((project_number, number, name))
                if ack and same_value(ack.before, old) and same_value(ack.after, wanted[name]):
                    continue
                if ack:
                    problems.append(
                        f"Project #{project_number} #{number} {name}: acknowledgement says "
                        f"{ack.before!r} → {ack.after!r}, but the board shows {old!r} and the manifest wants {wanted[name]!r}"
                    )
                else:
                    problems.append(
                        f"Project #{project_number} #{number} {name}: old board shows {old!r}, manifest wants {wanted[name]!r}"
                    )
    return problems


def managed_block(body: str) -> str:
    match = MANAGED_ISSUE_BLOCK.search(body)
    return match.group(0) if match else ""


def source_values(detail: ProjectState) -> dict[int, dict[str, Any]]:
    return {number: dict(item.values) for number, item in detail.items.items()}


def load_base_manifests(paths: Sequence[str]) -> tuple[dict[str, dict[str, Any]], list[str] | None, str | None]:
    """Collect evidence and project settings from earlier v3 or v4 manifests."""
    evidence: dict[str, dict[str, Any]] = {}
    priority_options: list[str] | None = None
    title: str | None = None
    for raw_path in paths:
        try:
            raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReconcileError(f"cannot read --base {raw_path}: {error}") from error
        if not isinstance(raw, dict) or raw.get("schema") not in (SCHEMA, *LEGACY_SCHEMAS):
            raise ReconcileError(f"--base {raw_path} is not a work-accountability Project manifest")
        project = raw.get("project") or {}
        if priority_options is None and isinstance(project.get("priority_options"), list):
            priority_options = list(project["priority_options"])
        if title is None and raw.get("schema") == SCHEMA and project.get("title"):
            title = project["title"]
        for item in raw.get("items") or []:
            if isinstance(item, dict) and item.get("work_key") and item.get("evidence"):
                evidence.setdefault(item["work_key"], item["evidence"])
    return evidence, priority_options, title


def remembered_manifests(host: str, repository: str) -> list[str]:
    """Manifests whose runs this machine verified, newest first (0.7.3 runs included)."""
    root = pending_root(host, repository)
    journal = root / "journal.ndjson"
    if not journal.exists():
        return []
    found: list[str] = []
    for line in reversed(journal.read_text(encoding="utf-8").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        digest = (event.get("receipt") or {}).get("manifest_digest") if event.get("event") == "verified" else None
        path = root / f"{digest}.json" if digest else None
        if path and path.exists() and str(path) not in found:
            found.append(str(path))
    return found


def section_label_from_title(title: str, root_key: str) -> str:
    source_id = root_key.rsplit(":", 1)[-1]
    label = title
    if label.startswith(source_id):
        label = label[len(source_id):].lstrip(" :—-")
    label = label or title
    return label if len(label) <= 60 else label[:59].rstrip() + "…"


def run_draft(args: argparse.Namespace) -> int:
    """Print a project-v4 manifest built from live GitHub state (read-only)."""
    if not args.repo or not args.root:
        raise ReconcileError("--draft needs --repo OWNER/REPOSITORY and --root ISSUE_NUMBER")
    transport = GhTransport(args.host, args.user)
    repository = args.repo
    issues = list_managed_issues(transport, repository)
    root = issues.get(args.root)
    if root is None:
        raise ReconcileError(f"#{args.root} is not a managed issue; create the root epic issue first")
    include = [int(n) for value in args.include or [] for n in str(value).split(",") if n.strip()]
    parents: dict[int, int] = {}
    order: list[int] = [args.root]
    frontier = [args.root] + include
    for number in include:
        if number not in issues:
            raise ReconcileError(f"--include #{number} is not a managed issue")
        current = issue_parent(transport, repository, number)
        if current not in (None, args.root):
            raise ReconcileError(f"--include #{number} already sits under #{current}")
        parents[number] = args.root
        order.append(number)
    has_children: dict[int, bool] = {}
    seen: set[int] = set()
    while frontier:
        number = frontier.pop(0)
        if number in seen:
            continue
        seen.add(number)
        children = list_sub_issues(transport, repository, number)
        has_children[number] = bool(children)
        for raw in children:
            child = int(raw["number"])
            if child not in issues:
                raise ReconcileError(f"#{child} under #{number} has no work-accountability identity")
            parents[child] = number
            if child not in order:
                order.append(child)
            frontier.append(child)
    repo_state, owner_projects = discover_repository(transport, repository)
    root_key = root.work_key
    keys = {issues[n].work_key for n in order if n != args.root}
    destination = next(
        (p for p in owner_projects if marked_for(p, args.host, repo_state.id, root_key)), None
    )
    sources = sorted(
        (p for p in owner_projects if any(scope in keys for scope in marker_scopes(p, args.host, repo_state.id))),
        key=lambda p: p.number,
    )
    details: dict[int, ProjectState] = {}
    for project in ([destination] if destination else []) + sources:
        details[project.number] = load_project_detail(
            transport, repo_state.owner_type, repo_state.owner_login, project.number, repository
        )
    remembered = remembered_manifests(args.host, repository)
    evidence, priority_options, base_title = load_base_manifests([*(args.base or []), *remembered])
    if remembered:
        sys.stderr.write(
            f"draft: reusing evidence from {len(remembered)} manifest(s) this machine applied before "
            f"(newest first; --base files take priority)\n"
        )
    if priority_options is None and destination and "Priority" in details[destination.number].fields:
        priority_options = [o["name"] for o in details[destination.number].fields["Priority"].options]
    if priority_options is None:
        for project in sources:
            field_state = details[project.number].fields.get("Priority")
            if field_state:
                priority_options = [o["name"] for o in field_state.options]
                break
    priority_options = priority_options or ["High", "Medium", "Low"]

    def depth(number: int) -> int:
        d = 0
        while number in parents:
            number = parents[number]
            d += 1
        return d

    def old_values(number: int) -> dict[str, Any]:
        if destination and number in details[destination.number].items:
            return details[destination.number].items[number].values
        chain = [issues[number].work_key]
        cursor = number
        while cursor in parents:
            cursor = parents[cursor]
            chain.append(issues[cursor].work_key)
        for key in chain:
            for project in sources:
                if key in marker_scopes(project, args.host, repo_state.id) and number in details[project.number].items:
                    return details[project.number].items[number].values
        for project in sources:
            if number in details[project.number].items:
                return details[project.number].items[number].values
        return {}

    children_of: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children_of.setdefault(parent, []).append(child)

    def sort_key(number: int) -> tuple[float, int]:
        rank = old_values(number).get("Rank")
        return (float(rank) if rank is not None else float("inf"), number)

    ranked: list[int] = []

    def walk(number: int) -> None:
        ranked.append(number)
        for child in sorted(children_of.get(number, []), key=sort_key):
            walk(child)

    walk(args.root)
    renumber = destination is None
    items: list[dict[str, Any]] = []
    observed: dict[str, dict[str, Any]] = {}
    missing_evidence: list[str] = []
    for position, number in enumerate(ranked):
        issue = issues[number]
        values = old_values(number)
        kind = "epic" if has_children.get(number) or number == args.root or number in include else "story"
        item: dict[str, Any] = {"number": number, "work_key": issue.work_key, "kind": kind}
        if number != args.root:
            item["parent"] = issues[parents[number]].work_key
        if kind == "epic" and depth(number) == 1:
            item["section_label"] = section_label_from_title(issue.title, root_key)
        if kind == "story":
            item["work_phase"] = values.get("Work phase") or "Backlog"
            item["health"] = values.get("Health") or "On track"
        item["source_freshness"] = values.get("Source freshness") or "Current"
        priority = values.get("Priority")
        item["priority"] = priority if priority in priority_options else None
        item["rank"] = position if renumber or values.get("Rank") is None else values.get("Rank")
        item["evidence"] = evidence.get(issue.work_key, {})
        if kind == "story" and REQUIRED_EVIDENCE.get(item["work_phase"]):
            absent = [n for n in REQUIRED_EVIDENCE[item["work_phase"]] if n not in item["evidence"]]
            if absent:
                missing_evidence.append(f"#{number} {item['work_phase']} needs {', '.join(absent)}")
        items.append(item)
        live = details[destination.number].items[number].values if destination and number in details[destination.number].items else {}
        observed[str(number)] = {name: live[name] for name in GUARDED_FIELDS if name in live}
    manifest = {
        "schema": SCHEMA,
        "repository": repository,
        "scope": {"root_number": args.root, "root_work_key": root_key, "source": {}},
        "project": {
            "owner": repo_state.owner_login,
            "title": destination.title if destination else base_title or f"{repository.split('/', 1)[1]} — {root.title}",
            "priority_options": priority_options,
            "general_section_label": f"{root_key.rsplit(':', 1)[-1]} (general)",
        },
        "supersedes": [project.number for project in sources],
        "acknowledged_changes": [],
        "observed": observed,
        "items": items,
    }
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    notes = []
    if include:
        notes.append("apply with --attach-parents to put " + ", ".join(f"#{n}" for n in include) + f" under #{args.root}")
    if sources:
        notes.append("supersedes lists " + ", ".join(f"#{p.number}" for p in sources) + "; they close after the merged board verifies")
    if missing_evidence:
        notes.append("add evidence before --apply: " + "; ".join(missing_evidence))
    for note in notes:
        sys.stderr.write(f"draft: {note}\n")
    return 0


def diagnose(host: str, user: str | None) -> dict[str, Any]:
    script = Path(__file__).resolve()
    skill = script.parents[1]
    digest = hashlib.sha256()
    for path in sorted(p for p in skill.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        if path.name == ".work-accountability-install.json" or path.name.endswith((".pyc", ".pyo")):
            continue
        digest.update(str(path.relative_to(skill)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    git_root = script.parents[3]
    revision = None
    dirty = None
    if (git_root / ".git").exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=git_root, text=True, capture_output=True, check=False
        )
        if result.returncode == 0:
            revision = result.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=git_root, text=True, capture_output=True, check=False
            )
            dirty = bool(status.stdout.strip())
    receipt_path = skill / ".work-accountability-install.json"
    install_receipt = None
    if receipt_path.exists():
        try:
            install_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            install_receipt = {"error": str(error)}
    gh_version_result = subprocess.run(
        ["gh", "--version"], text=True, capture_output=True, check=False
    )
    transport = GhTransport(host, user)
    return {
        "schema": "github-work-accountability/diagnose-v1",
        "skill_version": SKILL_VERSION,
        "script": str(script),
        "skill_directory": str(skill),
        "skill_digest": digest.hexdigest(),
        "git_revision": revision,
        "git_dirty": dirty,
        "install_receipt": install_receipt,
        "install_digest_matches": (
            install_receipt.get("skill_digest") == digest.hexdigest()
            if isinstance(install_receipt, dict) and install_receipt.get("skill_digest")
            else None
        ),
        "gh": shutil.which("gh"),
        "gh_version": (gh_version_result.stdout or gh_version_result.stderr).splitlines()[0],
        "host": host,
        "login": transport.login,
        "rate": rate_status(transport),
    }


def print_receipt(receipt: Receipt) -> None:
    print(canonical_json(receipt.__dict__))


def complete_receipt_metrics(
    receipt: Receipt,
    transport: GhTransport,
    used_at_start: int,
) -> None:
    rate = rate_status(transport)
    receipt.graphql_requests = transport.graphql_requests
    receipt.rest_requests = transport.rest_requests
    receipt.graphql_cost = max(
        transport.graphql_cost,
        int(rate.get("used") or 0) - used_at_start,
    )
    receipt.rate_remaining = int(rate.get("remaining") or 0)
    receipt.rate_reset = rate.get("reset")


def load_detail(transport: GhTransport, repo: RepositoryState, manifest: Manifest, number: int) -> ProjectState:
    return load_project_detail(
        transport, repo.owner_type, manifest.project_owner, number, manifest.repository
    )


def wait_for(
    transport: GhTransport,
    repo: RepositoryState,
    manifest: Manifest,
    number: int,
    predicate: Callable[[ProjectState], bool],
    description: str,
) -> ProjectState:
    return load_project_until(
        transport,
        repo.owner_type,
        manifest.project_owner,
        number,
        manifest.repository,
        predicate,
        description,
    )


def run(args: argparse.Namespace) -> int:
    if args.diagnose:
        print(canonical_json(diagnose(args.host, args.user)))
        return 0
    if args.draft:
        return run_draft(args)
    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    transport = GhTransport(args.host, args.user)
    root = pending_root(args.host, manifest.repository)
    persisted = persist_desired(manifest_path, manifest, args.host)
    receipt = Receipt(
        repository=manifest.repository,
        manifest_digest=manifest.digest,
        actor=transport.login,
    )
    lock_root = state_root() / "locks"
    with FileLocks(
        lock_root,
        (
            f"transport:{args.host}:{transport.login.casefold()}",
            f"repository:{args.host}:{manifest.repository.casefold()}",
        ),
    ):
        rate = rate_status(transport)
        used_at_start = int(rate.get("used") or 0)
        receipt.rate_remaining = int(rate.get("remaining") or 0)
        receipt.rate_reset = rate.get("reset")
        graphql_need = max(100, len(manifest.items) * 10 + 50 + len(manifest.supersedes) * 20)
        rest_need = len(manifest.items) * 4 + 50
        try:
            require_budget(transport, graphql_need, rest_need, "preflight")
        except TemporaryFailure as error:
            raise TemporaryFailure(f"{error} desired={persisted}") from error

        all_issues = list_managed_issues(transport, manifest.repository)
        check_root_is_document_root(transport, manifest, all_issues)
        tree = read_github_tree(transport, manifest)
        issues, missing_edges = validate_manifest_against_issues(transport, manifest, all_issues, tree)
        if missing_edges and args.apply and not args.attach_parents:
            raise ReconcileError(
                "GitHub lacks these parent links from the manifest: "
                + ", ".join(f"#{child} under #{parent}" for parent, child in missing_edges)
                + ". Rerun with --attach-parents to add them."
            )
        repo, owner_projects = discover_repository(transport, manifest.repository)
        if manifest.project_owner.casefold() != repo.owner_login.casefold():
            raise ReconcileError(
                "the reconciler creates Projects under the repository owner; "
                f"manifest requested {manifest.project_owner}, repository owner is {repo.owner_login}"
            )
        intent_path = create_intent_path(root, manifest.root_work_key)
        intent = json.loads(intent_path.read_text()) if intent_path.exists() else None
        project = select_project(
            owner_projects, repo, args.host, manifest.root_work_key, args.adopt_project, intent, transport.login
        )
        fresh = project is not None and args.adopt_project is None and not marked_for(
            project, args.host, repo.id, manifest.root_work_key
        )
        sources = discover_sources(owner_projects, repo, args.host, manifest, project)
        detail = load_detail(transport, repo, manifest, project.number) if project else None
        if detail is not None:
            check_field_types(detail, manifest)
        targets = plan_targets(manifest, detail, receipt)
        source_details = {
            number: load_detail(transport, repo, manifest, number) for number in sorted(sources.pending)
        }
        conflicts = check_source_conflicts(manifest, source_details)
        if conflicts:
            raise ReconcileError(
                "old boards disagree with the manifest (nothing was written): "
                + "; ".join(conflicts)
                + ". Correct the manifest, or record each intended change in acknowledged_changes."
            )
        if other := (detail.other_items if detail else []):
            receipt.unmanaged_items.extend(other)

        for parent, child in missing_edges:
            receipt.planned_mutations.append(f"attach #{child} under #{parent}")
        if not args.apply:
            plan_dry_run(manifest, repo, project, detail, issues, all_issues, targets, sources, args, receipt)
            complete_receipt_metrics(receipt, transport, used_at_start)
            print_receipt(receipt)
            return 0

        key = migration_key(manifest)
        if sources.pending:
            snapshot_details = dict(source_details)
            if detail is not None and not fresh:
                snapshot_details[detail.number] = detail
            write_snapshots(
                args.host,
                manifest,
                snapshot_details,
                issues,
                tree,
                migration_started(root, key),
                receipt,
            )
            append_journal(root, {"event": "migration-started", "key": key, "manifest": manifest.digest})
        append_journal(root, {"event": "start", "manifest": manifest.digest})
        receipt.planned_mutations.clear()
        attach_parents(transport, manifest, issues, missing_edges, receipt)

        if project is None:
            receipt.planned_mutations.append("create document Project linked to repository")
            project = create_project(transport, manifest, repo, root)
            receipt.applied_mutations.append("create document Project linked to repository")
            fresh = True
        receipt.project_number = project.number
        receipt.project_url = project.url
        if sources.pending or sources.done:
            receipt.migration_id = migration_id(manifest, project.id, receipt)
            append_journal(root, {"event": "migration", "id": receipt.migration_id, "key": key})
        current = load_detail(transport, repo, manifest, project.number)
        initial = read_initial_views(current.readme)
        if fresh and not initial:
            initial = [
                view.id for view in current.views.values()
                if view.name not in (LIFECYCLE_VIEW, SECTION_VIEW)
            ]
        desired_readme = managed_readme(
            current.readme,
            args.host,
            repo,
            manifest,
            read_lifecycle_marker(current.readme),
            read_view_marker(current.readme, "Section"),
            initial,
        )
        if current.title != manifest.project_title or current.readme != desired_readme or current.closed:
            receipt.planned_mutations.append("update Project title/managed README block")
            project = update_project_metadata(transport, current, manifest.project_title, desired_readme)
            receipt.applied_mutations.append("update Project title/managed README block")
        if repo.name_with_owner not in project.repositories:
            receipt.planned_mutations.append("link Project to repository")
            ensure_link(transport, project, repo)
            receipt.applied_mutations.append("link Project to repository")
        project = load_detail(transport, repo, manifest, project.number)

        before = len(receipt.applied_mutations)
        ensure_fields(transport, project, manifest, receipt)
        if len(receipt.applied_mutations) != before:
            project = wait_for(
                transport, repo, manifest, project.number,
                lambda state: fields_ready(state, manifest), "the required Project fields",
            )
        before = len(receipt.applied_mutations)
        reconcile_membership(transport, project, issues, all_issues, manifest.repository, receipt)
        if len(receipt.applied_mutations) != before:
            project = wait_for(
                transport, repo, manifest, project.number,
                lambda state: all(number in state.items for number in issues)
                and not ((set(state.items) & set(all_issues)) - set(issues)),
                "the exact document Project membership",
            )
        before = len(receipt.applied_mutations)
        ensure_values(transport, project, manifest, targets, receipt)
        if len(receipt.applied_mutations) != before:
            project = wait_for(
                transport, repo, manifest, project.number,
                lambda state: not value_mismatches(state, manifest, targets),
                "the requested Project field values",
            )

        view_plan = plan_views(project, args.repair_lifecycle, fresh)
        receipt.notes.extend(view_plan.notes)
        lifecycle_number = view_plan.lifecycle
        section_number = view_plan.section
        # Create first, delete last: GitHub refuses to delete a Project's last view.
        if view_plan.create_lifecycle:
            receipt.planned_mutations.append("create Lifecycle Work phase board")
            lifecycle_number = create_view(transport, project, repo, manifest, LIFECYCLE_VIEW)
            receipt.applied_mutations.append("create Lifecycle Work phase board")
        if view_plan.create_section:
            receipt.planned_mutations.append("create By section table")
            section_number = create_view(transport, project, repo, manifest, SECTION_VIEW)
            receipt.applied_mutations.append("create By section table")
        assert lifecycle_number is not None and section_number is not None
        pending_initial = read_initial_views(project.readme)
        recorded_readme = managed_readme(
            project.readme, args.host, repo, manifest, lifecycle_number, section_number, pending_initial
        )
        if recorded_readme != project.readme:
            project = update_project_metadata(transport, project, manifest.project_title, recorded_readme)
            receipt.applied_mutations.append("record managed views in Project README")
        if view_plan.delete:
            delete_views(transport, project, view_plan.delete, "superseded or initial", receipt)
        if pending_initial:
            final_readme = managed_readme(
                project.readme, args.host, repo, manifest, lifecycle_number, section_number
            )
            project = update_project_metadata(transport, project, manifest.project_title, final_readme)
            receipt.applied_mutations.append("drop the removed initial views from the Project README")
        deleted = {view.number for view in view_plan.delete}
        project = wait_for(
            transport, repo, manifest, project.number,
            lambda state: (
                read_lifecycle_marker(state.readme) == lifecycle_number
                and lifecycle_number in state.views
                and section_number in state.views
                and lifecycle_view_valid(state.views[lifecycle_number], state.fields)
                and section_view_valid(state.views[section_number], state.fields)
                and not (deleted & set(state.views))
            ),
            "the Lifecycle board and By section table",
        )
        receipt.lifecycle_view = lifecycle_number
        receipt.section_view = section_number
        receipt.lifecycle_url = f"{project.url}/views/{lifecycle_number}"
        receipt.section_url = f"{project.url}/views/{section_number}"
        receipt.unmanaged_items = list(project.other_items)
        verify_final(
            transport, manifest, repo, project, issues, all_issues, targets,
            lifecycle_number, section_number,
        )

        if sources.pending:
            require_budget(transport, 20 * len(sources.pending) + 20, 10, "before closing old boards")
            snapshots_dir = snapshot_root(args.host, manifest)
            changed: list[str] = []
            for number in sorted(sources.pending):
                snapshot = json.loads((snapshots_dir / f"project-{number}.json").read_text(encoding="utf-8"))
                before_values = {
                    int(n): entry["values"] for n, entry in snapshot["project"]["items"].items()
                }
                now = source_values(load_detail(transport, repo, manifest, number))
                for n in sorted(set(before_values) | set(now)):
                    if n in issues and before_values.get(n) != now.get(n):
                        changed.append(f"Project #{number} #{n}")
            if changed:
                raise ReconcileError(
                    "old boards changed after they were checked: " + ", ".join(changed)
                    + ". The new board is verified and the old boards are still open; "
                    "re-run --draft, reconcile the difference, and apply again."
                )
        ensure_issue_projection(transport, manifest.repository, issues, receipt.lifecycle_url, receipt)
        tree_keys = {item.work_key for item in manifest.items if item.parent is not None}
        for number, source in sorted(sources.pending.items()):
            scope = next(s for s in marker_scopes(source, args.host, repo.id) if s in tree_keys)
            readme = superseded_readme(source.readme, args.host, repo, scope, project.url)
            label = f"close superseded Project #{number}"
            receipt.planned_mutations.append(label)
            closed = supersede_project(transport, source, readme)
            if not closed.closed or not superseded_by(closed, args.host, repo.id):
                raise ReconcileError(f"GitHub did not show Project #{number} closed and marked superseded")
            receipt.applied_mutations.append(label)
        for number, source in sorted({**sources.done, **sources.pending}.items()):
            receipt.superseded.append({"project": number, "url": source.url, "closed": True})
        for number, source in sorted(sources.done.items()):
            if not source.closed:
                supersede_project(transport, source, source.readme)
                receipt.applied_mutations.append(f"close superseded Project #{number}")

        complete_receipt_metrics(receipt, transport, used_at_start)
        receipt.verified = True
        append_journal(root, {"event": "verified", "receipt": receipt.__dict__})
        if intent_path.exists():
            intent_path.unlink()
        print_receipt(receipt)
        return 0


def plan_dry_run(
    manifest: Manifest,
    repo: RepositoryState,
    project: ProjectState | None,
    detail: ProjectState | None,
    issues: Mapping[int, ManagedIssue],
    all_issues: Mapping[int, ManagedIssue],
    targets: Mapping[int, Mapping[str, Any]],
    sources: Sources,
    args: argparse.Namespace,
    receipt: Receipt,
) -> None:
    if project is None or detail is None:
        receipt.planned_mutations.append("create document Project linked to repository")
        receipt.planned_mutations.extend(f"create field {name}" for name in expected_field_schema(manifest))
        receipt.planned_mutations.extend(f"add issue #{number}" for number in sorted(issues))
        receipt.planned_mutations.append("set field values and epic rollups")
        receipt.planned_mutations.append("create Lifecycle Work phase board")
        receipt.planned_mutations.append("create By section table")
    else:
        receipt.project_number = project.number
        receipt.project_url = project.url
        if repo.name_with_owner not in detail.repositories:
            receipt.planned_mutations.append("link Project to repository")
        for name, (_data_type, options) in expected_field_schema(manifest).items():
            current = detail.fields.get(name)
            if current is None:
                receipt.planned_mutations.append(f"create field {name}")
            elif options:
                _payload, changes = planned_options(manifest, name, current, options)
                receipt.planned_mutations.extend(changes)
        for number in sorted(issues):
            if number not in detail.items:
                receipt.planned_mutations.append(f"add issue #{number}")
        for number in sorted((set(detail.items) & set(all_issues)) - set(issues)):
            receipt.planned_mutations.append(f"remove out-of-scope managed issue #{number}")
        for number, expected in sorted(targets.items()):
            current_item = detail.items.get(number)
            for name, value in expected.items():
                if value is not None and not same_value(
                    current_item.values.get(name) if current_item else None, value
                ):
                    receipt.planned_mutations.append(f"set #{number} {name}={value}")
        if fields_ready(detail, manifest):
            view_plan = plan_views(detail, args.repair_lifecycle, False)
            receipt.notes.extend(view_plan.notes)
            receipt.planned_mutations.extend(
                f"delete view #{view.number} {view.name!r}" for view in view_plan.delete
            )
            if view_plan.create_lifecycle:
                receipt.planned_mutations.append("create Lifecycle Work phase board")
            if view_plan.create_section:
                receipt.planned_mutations.append("create By section table")
        else:
            receipt.planned_mutations.append("create or repair managed views after fields exist")
    receipt.planned_mutations.append("bind managed issues to the Lifecycle board")
    for number in sorted(sources.pending):
        receipt.planned_mutations.append(f"snapshot and close superseded Project #{number}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile one planning document's epic tree into one GitHub Project."
    )
    parser.add_argument("--manifest", help="Desired-state project-v4 JSON manifest")
    parser.add_argument("--user", help="Stored gh account to use without changing global state")
    parser.add_argument("--host", default="github.com")
    parser.add_argument("--adopt-project", type=int, help="Explicitly adopt this unmarked Project")
    parser.add_argument(
        "--repair-lifecycle",
        action="store_true",
        help="Replace one malformed managed view (Lifecycle or By section)",
    )
    parser.add_argument(
        "--attach-parents",
        action="store_true",
        help="Add native sub-issue links the manifest declares but GitHub lacks",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the computed delta")
    parser.add_argument("--diagnose", action="store_true", help="Print installation and GitHub readiness")
    parser.add_argument(
        "--draft",
        "--draft-migration",
        dest="draft",
        action="store_true",
        help="Print a project-v4 manifest built from live GitHub state (read-only)",
    )
    parser.add_argument("--repo", help="OWNER/REPOSITORY for --draft")
    parser.add_argument("--root", type=int, help="Root epic issue number for --draft")
    parser.add_argument(
        "--include",
        action="append",
        help="For --draft: existing epics (comma-separated) to place directly under the root",
    )
    parser.add_argument(
        "--base",
        action="append",
        help="For --draft: an earlier v3 or v4 manifest whose evidence and settings to reuse",
    )
    args = parser.parse_args()
    if not args.diagnose and not args.draft and not args.manifest:
        parser.error("--manifest is required unless --diagnose or --draft is used")
    try:
        return run(args)
    except TemporaryFailure as error:
        sys.stderr.write(f"temporary failure: {error}\n")
        return EXIT_TEMPORARY
    except (ReconcileError, subprocess.TimeoutExpired) as error:
        sys.stderr.write(f"reconciliation failed: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
