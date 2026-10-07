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
SKILL_VERSION = "0.10.10"
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
WONT_DO = "Won't do"
PHASES = (
    "Backlog",
    "Designing",
    "Ready",
    "Executing",
    "Acceptance",
    "Release ready",
    "Done",
    WONT_DO,
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
    WONT_DO: ("decision",),
}
COLORS = ("RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "PURPLE", "GRAY", "PINK")
EXIT_TEMPORARY = 75
# GitHub's web page names fields with spaces by their hyphenated lowercase form.
# It rejects the quoted form ('has:"Work phase"': "Invalid value ... for has"),
# which the API accepts; Safari then renders a blank Project.  Observed 2026-09-25.
LIFECYCLE_FILTER = "has:work-phase"
REJECTED_LIFECYCLE_FILTERS = ('has:"Work phase"',)
# GitHub's table "Show hierarchy" nests every item under its parent, and the API
# cannot turn it off; with the root epic in a table, every story folds under it,
# and signed-out visitors cannot expand nested rows at all. So both tables show
# stories only. Observed 2026-10-03.
RELEASE_FILTER = LIFECYCLE_FILTER
LIFECYCLE_VIEW = "Lifecycle"
SECTION_VIEW = "By section"
RELEASE_VIEW = "By release"
GUARDED_FIELDS = ("Work phase", "Health", "Source freshness", "Priority", "Rank")
VISIBILITIES = ("private", "public")
DELIVERY_KINDS = ("release", "merge", "other")
RELEASE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RELEASE_NAMED_ROOT = re.compile(r"^(?:release\s+)?v?\d+\.\d+(?:\.\d+)?\b", re.IGNORECASE)
SECTION_OPTION_PREFIX = "work-accountability:section "
# A By section group header shows its option's description, so it opens with a
# sentence for readers; the key after it lets awa follow a renamed section.
SECTION_OWNER = re.compile(r"work-accountability:section (\S+?)\)?$")


def section_description(owner: str, progress: str) -> str:
    return f"{progress} · section managed by github-work-accountability ({SECTION_OPTION_PREFIX}{owner})"


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
    commit: str | None = None
    pr: str | None = None
    release: str | None = None
    branch: str | None = None
    reason: str | None = None


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
    delivery_kind: str | None = None
    delivery_release: str | None = None
    milestone: str | None = None
    milestone_specified: bool = False
    milestone_change_reason: str | None = None
    blocked_by: tuple[str, ...] | None = None
    blocked_reason: str | None = None


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

    def section_progress(self, targets: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, str]:
        """Each section's progress, keyed by the work key that owns its Section option.

        Pass the run's targets so values kept from the board (a newer change
        someone made there) count, exactly as they do in the epics' Progress.
        """
        found = {}
        for label, owner in self.section_labels().items():
            stories = [
                replace(
                    item,
                    work_phase=(targets or {}).get(item.number, {}).get("Work phase") or item.work_phase,
                    health=(targets or {}).get(item.number, {}).get("Health") or item.health,
                )
                for item in self.items if item.kind == "story" and item.section == label
            ]
            found[owner] = progress_text(stories) if stories else "No stories yet"
        return found

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
    milestone: str | None = None
    milestone_number: int | None = None
    state_reason: str | None = None


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
    public: bool = False
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
    release_view: int | None = None
    migration_id: str | None = None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[dict[str, Any]] = field(default_factory=list)
    attached_parents: list[str] = field(default_factory=list)
    unmanaged_items: list[str] = field(default_factory=list)
    kept_live_values: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    visibility: str | None = None
    planned_mutations: list[str] = field(default_factory=list)
    applied_mutations: list[str] = field(default_factory=list)
    graphql_requests: int = 0
    rest_requests: int = 0
    graphql_cost: int = 0
    rate_remaining: int | None = None
    rate_reset: str | int | None = None
    estimated_writes: int | None = None
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
        commit=value.get("commit"),
        pr=value.get("pr"),
        release=value.get("release"),
        branch=value.get("branch"),
        reason=value.get("reason"),
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


def validate_delivery_evidence(
    prefix: str,
    phase: str | None,
    evidence: Mapping[str, Evidence],
    repository: str,
    delivery_kind: str | None,
    milestone: str | None,
) -> None:
    """Integration evidence ties the accepted candidate to the commit that landed."""
    if phase not in {"Release ready", "Done"} or delivery_kind not in {"release", "merge"}:
        return
    integration = evidence.get("integration")
    if integration is None:
        raise ReconcileError(
            f"{prefix}: {phase} for a {delivery_kind}-delivered story requires evidence: integration "
            "(the commit that landed on the default branch, bound to the accepted candidate)"
        )
    if not integration.commit or not COMMIT_SHA.fullmatch(integration.commit):
        raise ReconcileError(f"{prefix}.evidence.integration.commit must be a full 40-character commit SHA")
    candidate = evidence.get("candidate")
    if candidate is None or integration.candidate != candidate.ref:
        raise ReconcileError(
            f"{prefix}.evidence.integration.candidate must equal the accepted candidate "
            f"{candidate.ref if candidate else '(missing)'}"
        )
    if integration.branch is not None and (
        not isinstance(integration.branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", integration.branch)
    ):
        raise ReconcileError(f"{prefix}.evidence.integration.branch must be a branch name")
    if integration.pr is not None and not re.fullmatch(
        rf"https://[^/]+/{re.escape(repository)}/pull/[0-9]+", integration.pr
    ):
        raise ReconcileError(f"{prefix}.evidence.integration.pr must be a pull request URL in {repository}")
    if phase == "Done" and delivery_kind == "release":
        delivery = evidence["delivery"]
        if not delivery.release or delivery.release != milestone:
            raise ReconcileError(
                f"{prefix}.evidence.delivery.release must name the release tag {milestone!r} "
                "that delivered the story"
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
    if phase == WONT_DO:
        decision = evidence["decision"]
        if not re.fullmatch(rf"https://[^/]+/{re.escape(repository)}/issues/{number}#issuecomment-[0-9]+", decision.ref):
            raise ReconcileError(
                f"{prefix}.evidence.decision.ref must link the comment on #{number} where the decision was recorded"
            )
        if not decision.author:
            raise ReconcileError(f"{prefix}.evidence.decision needs author: who decided not to do it")
        if not decision.reason or not decision.reason.strip():
            raise ReconcileError(f"{prefix}.evidence.decision needs reason: why it won't be done")
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
    """Done out of the stories still planned; Won't do stories are counted apart."""
    done = sum(1 for story in stories if story.work_phase == "Done")
    dropped = sum(1 for story in stories if story.work_phase == WONT_DO)
    text = f"{done}/{len(stories) - dropped} Done"
    if dropped:
        text += f" · {dropped} won't do"
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


def parse_delivery(raw: Any, prefix: str, kind: str) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if kind == "epic":
        raise ReconcileError(f"{prefix}: epics have no delivery; their stories do")
    if not isinstance(raw, dict):
        raise ReconcileError(f"{prefix}.delivery must be an object")
    delivery_kind = raw.get("kind")
    if delivery_kind == "deployment":
        raise ReconcileError(f"{prefix}.delivery.kind 'deployment' is not supported; use 'other'")
    if delivery_kind not in DELIVERY_KINDS:
        raise ReconcileError(f"{prefix}.delivery.kind must be release, merge, or other")
    release = raw.get("release")
    if delivery_kind == "release":
        if not isinstance(release, str) or not (release == "next" or RELEASE_TAG.fullmatch(release)):
            raise ReconcileError(
                f"{prefix}.delivery.release must be a release tag (e.g. v0.2.10) or 'next'"
            )
    elif release is not None:
        raise ReconcileError(f"{prefix}.delivery.release is only for kind release")
    return delivery_kind, release


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
        delivery_kind, delivery_release = parse_delivery(value.get("delivery"), prefix, kind)
        milestone_specified = "milestone" in value
        milestone = value.get("milestone")
        if milestone is not None and (not isinstance(milestone, str) or not RELEASE_TAG.fullmatch(milestone)):
            raise ReconcileError(f"{prefix}.milestone must be a release tag such as v0.2.10, or null")
        if delivery_release not in (None, "next"):
            if milestone_specified and milestone != delivery_release:
                raise ReconcileError(
                    f"{prefix}: milestone {milestone!r} disagrees with delivery.release {delivery_release!r}"
                )
            milestone, milestone_specified = delivery_release, True
        if kind == "epic" and milestone is not None:
            raise ReconcileError(f"{prefix}: epics carry no release milestone; their stories do")
        change_reason = value.get("milestone_change_reason")
        if change_reason is not None:
            change_reason = require_string(change_reason, f"{prefix}.milestone_change_reason")
        validate_delivery_evidence(prefix, phase, evidence, repository, delivery_kind, milestone)
        if phase == WONT_DO:
            # Won't do leaves its release: the milestone then counts only work still planned.
            milestone, milestone_specified = None, True
            change_reason = change_reason or f"won't do: {evidence['decision'].reason}"
        blocked_by = value.get("blocked_by")
        if blocked_by is not None:
            if kind != "story" or not isinstance(blocked_by, list) or any(
                not isinstance(k, str) or not k.startswith(f"{repository}:") for k in blocked_by
            ):
                raise ReconcileError(f"{prefix}.blocked_by must list work keys in {repository} (stories only)")
            if work_key in blocked_by:
                raise ReconcileError(f"{prefix} cannot be blocked by itself")
            blocked_by = tuple(dict.fromkeys(blocked_by))
        blocked_reason = value.get("blocked_reason")
        if blocked_reason is not None:
            blocked_reason = require_string(blocked_reason, f"{prefix}.blocked_reason")
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
                delivery_kind=delivery_kind,
                delivery_release=delivery_release,
                milestone=milestone,
                milestone_specified=milestone_specified,
                milestone_change_reason=change_reason,
                blocked_by=blocked_by,
                blocked_reason=blocked_reason,
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
    if "visibility" in project or "observed_project" in raw:
        raise ReconcileError(
            "visibility is not a manifest setting: boards are private, and only "
            "`awa project visibility ROOT_ISSUE public|private` changes that (it previews first)"
        )
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
        record_write(self.host, getattr(self, "login", None) or self.requested_user or "unknown")
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


HOURLY_WRITE_LIMIT = 500  # GitHub: about 500 content-creating requests per hour per account.


def write_limit() -> int:
    """The writes awa allows itself per rolling hour: GitHub's limit less a margin for other tools."""
    raw = os.environ.get("WORK_ACCOUNTABILITY_HOURLY_WRITES", "450")
    if not (raw.isascii() and raw.isdecimal()) or int(raw) == 0:
        raise ReconcileError(f"WORK_ACCOUNTABILITY_HOURLY_WRITES must be a positive whole number, not {raw!r}")
    return int(raw)


def write_log(host: str, login: str) -> Path:
    return state_root() / "writes" / f"{host}_{login.casefold()}.log"


def record_write(host: str, login: str) -> None:
    """Log one write. Best effort: an unwritable state directory never stops a run."""
    path = write_log(host, login)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(f"{time.time():.3f}\n")
    except OSError:
        pass


def recent_writes(host: str, login: str, now: float | None = None) -> list[float]:
    """Times of the writes awa made as this account in the last hour (oldest first).

    Older lines are pruned under the same lock appends take, by an atomic
    rename, so a concurrent awa process never loses a logged write.
    """
    now = time.time() if now is None else now
    path = write_log(host, login)
    try:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            lines = handle.read().split()
            recent = sorted(
                float(v) for v in lines if v.replace(".", "", 1).isdigit() and now - float(v) < 3600
            )
            if len(recent) < len(lines):
                scratch = path.with_suffix(".tmp")
                scratch.write_text("".join(f"{v:.3f}\n" for v in recent), encoding="utf-8")
                os.replace(scratch, path)
    except OSError:
        return []
    return recent


BATCHED_WRITES = (
    re.compile(r"(?:set|clear) #\d+ (?!release milestone)"),  # field values
    re.compile(r"(?:add issue|remove out-of-scope managed issue) #\d+"),  # board membership
    re.compile(r"(?:block #\d+ by|remove #\d+ as a blocker of) #\d+"),  # blocked-by links
)


def estimate_writes(planned: Sequence[str], extra: int = 0) -> int:
    """Writes a planned run makes.

    One per issue (its single PATCH), plus one for a reopen comment; field
    values, board membership and blocked-by links go 25 to a request; every
    other step is one write. `extra` covers writes the plan has no line for.
    """
    issues: set[str] = set()
    state_changes: set[str] = set()
    rebound: set[str] = set()
    batched = [0] * len(BATCHED_WRITES)
    other = 0
    for label in planned:
        issue = re.match(r"(bind issue|close|reopen|label|remove awaiting-release from) #(\d+)", label)
        if issue:
            issues.add(issue.group(2))
            # A reopen also posts a comment; a state change written with new
            # text goes as two requests (see write_issues).
            other += issue.group(1) == "reopen"
            if issue.group(1) in ("close", "reopen"):
                state_changes.add(issue.group(2))
            if issue.group(1) == "bind issue":
                rebound.add(issue.group(2))
            continue
        kind = next((i for i, pattern in enumerate(BATCHED_WRITES) if pattern.match(label)), None)
        if kind is None:
            other += 1
        else:
            batched[kind] += 1
    return len(issues) + len(state_changes & rebound) + sum(-(-count // 25) for count in batched) + other + extra


def write_budget(transport: "GhTransport", need: int, what: str) -> tuple[bool, str]:
    """Whether `need` more writes fit in this account's rolling hour, and a sentence saying so."""
    login = getattr(transport, "login", None) or transport.requested_user or "unknown"
    used = recent_writes(transport.host, login)
    limit = write_limit()
    summary = (
        f"{what} needs about {need} write{'' if need == 1 else 's'}; awa has made {len(used)} as {login} in the last hour "
        f"(GitHub allows about {HOURLY_WRITE_LIMIT} an hour; awa stops at {limit})"
    )
    if need > limit:
        return True, (
            f"{summary}. That is more than one hour allows, so GitHub may stop it partway; if it does, "
            "rerun the same command after the reset and it continues where it stopped"
        )
    if len(used) + need > limit:
        free_at = used[len(used) + need - limit - 1] + 3600
        when = datetime.fromtimestamp(free_at, timezone.utc).strftime("%H:%M UTC")
        return False, (
            f"GitHub write limit: {summary}. Run it at {when} or later, when enough of the last hour's "
            "writes have aged out. Writes other tools made as this account are not counted"
        )
    return True, summary


def check_write_budget(transport: "GhTransport", need: int, what: str) -> None:
    """Stop before writing when this run would push the account past GitHub's hourly write limit."""
    ok, message = write_budget(transport, need, what)
    if not ok:
        raise TemporaryFailure(message + ". Nothing was written in this run.")
    if need > write_limit():
        sys.stderr.write(f"awa: {message}.\n")


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
                milestone=(raw.get("milestone") or {}).get("title"),
                milestone_number=(raw.get("milestone") or {}).get("number"),
                state_reason=raw.get("state_reason"),
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


RECORD_NAMES = ("Delivery", "Release", "Integration", "Delivered", "Won't do", "Blocked by", "Blocked reason")


def issue_record(body: str, name: str) -> str | None:
    """A fact awa wrote into the issue's managed block (for example `Release: v0.2.10`)."""
    match = MANAGED_ISSUE_BLOCK.search(body)
    if not match:
        return None
    line = re.search(rf"^{name}:[ \t]*(.+?)[ \t]*$", match.group(0), re.MULTILINE)
    return line.group(1) if line else None


def issue_project_projection(
    issue: ManagedIssue,
    project_url: str,
    records: Mapping[str, str | None] | None = None,
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
    for name, value in (records or {}).items():
        pattern = re.compile(rf"^{name}:[^\n]*\n?", re.MULTILINE)
        if value is None:
            block = pattern.sub("", block, count=1)
        elif pattern.search(block):
            block = pattern.sub(f"{name}: {value}\n", block, count=1)
        else:
            anchor = PROJECT_LINE.search(block)
            assert anchor is not None
            block = block[: anchor.end()] + f"\n{name}: {value}" + block[anchor.end() :]
    body = issue.body[: match.start()] + block + issue.body[match.end() :]
    labels = tuple(
        label
        for label in issue.labels
        if not label.startswith(("phase/", "health/", "source/"))
    )
    return body, labels


def issue_labels(raw: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(label["name"] if isinstance(label, dict) else str(label) for label in raw.get("labels") or [])


def write_issues(
    transport: GhTransport,
    manifest: "Manifest",
    issues: Mapping[int, ManagedIssue],
    project_url: str,
    receipt: Receipt,
    records: Mapping[int, Mapping[str, str | None]] | None,
    state_changes: Sequence["IssueStateChange"],
) -> None:
    """Bring each issue's managed block, labels and open/closed state up to date.

    One PATCH per issue keeps large runs fast and inside GitHub's write limits,
    except that a close or reopen that also changes the managed block is two
    requests, text first, because GitHub skips a milestone's counter when one
    request changes both. Each issue is re-read just before its write, so an
    edit made during the run is kept, and the write's own response confirms it.
    """
    owner, repo = manifest.repository.split("/", 1)
    by_number = {change.number: change for change in state_changes}
    pending = []
    for number in sorted(issues):
        body, labels = issue_project_projection(issues[number], project_url, (records or {}).get(number))
        if number in by_number or body != issues[number].body or labels != issues[number].labels:
            pending.append(number)
    bound: list[str] = []
    states: list[str] = []
    progress = Progress("issues", len(pending))
    for number in pending:
        progress.step()
        change = by_number.get(number)
        fresh = transport.rest(f"repos/{owner}/{repo}/issues/{number}")
        current = ManagedIssue(
            number=number,
            node_id=fresh["node_id"],
            database_id=int(fresh["id"]),
            title=fresh["title"],
            state=fresh["state"],
            body=fresh.get("body") or "",
            work_key=issues[number].work_key,
            html_url=fresh["html_url"],
            labels=issue_labels(fresh),
        )
        if MANAGED_KEY.findall(current.body)[:1] != [issues[number].work_key]:
            raise ReconcileError(f"issue #{number} lost or changed its work key during this run")
        body, labels = issue_project_projection(current, project_url, (records or {}).get(number))
        data: dict[str, Any] = {}
        described: list[str] = []
        if body != current.body or labels != current.labels:
            described.append(f"bind issue #{number} to Project fields")
            bound.append(described[-1])
        wanted = [label for label in labels if not (change and change.remove_label and label == AWAITING_RELEASE_LABEL)]
        if change and change.add_label and AWAITING_RELEASE_LABEL not in wanted:
            wanted.append(AWAITING_RELEASE_LABEL)
        if body != current.body:
            data["body"] = body
        if tuple(wanted) != current.labels:
            data["labels"] = wanted
        if change:
            for label in describe_issue_state(change):
                described.append(label)
                states.append(label)
            if change.close and (current.state != "closed" or fresh.get("state_reason") != change.close):
                data.update(state="closed", state_reason=change.close)
            if change.reopen_from and current.state == "closed":
                post_once(
                    transport, manifest.repository, number,
                    f"work-accountability:reopened #{number} {change.reopen_from}",
                    (f"Reopened: a story under this epic went back to an unfinished state, so the epic is open again."
                     if change.is_epic
                     else f"Reopened: this story went back to {change.reopen_from} after {change.reopen_after}."),
                )
                data["state"] = "open"
        receipt.planned_mutations.extend(described)
        if not data:
            continue
        if "state" in data and "body" in data:
            # GitHub skips a milestone's open/closed counter when one request
            # changes both an issue's state and its body (reproduced in a clean
            # repository, 2026-10-03), so the text goes first, on its own.
            transport.rest(f"repos/{owner}/{repo}/issues/{number}", method="PATCH", data={"body": data.pop("body")})
        updated = transport.rest(f"repos/{owner}/{repo}/issues/{number}", method="PATCH", data=data)
        problems = []
        if updated.get("body") != body:
            problems.append("its managed block")
        if set(issue_labels(updated)) != set(wanted):
            problems.append("its labels")
        if "state" in data and (
            updated.get("state") != data["state"]
            or (data["state"] == "closed" and updated.get("state_reason") != data["state_reason"])
        ):
            problems.append("its open/closed state")
        if problems:
            raise ReconcileError(f"GitHub did not show #{number} as planned ({', '.join(problems)}): " + "; ".join(described))
    receipt.applied_mutations.extend(bound + states)


class Progress:
    """Progress lines on stderr for long runs, so a slow run never looks stuck."""

    def __init__(self, what: str, total: int, every: int = 10) -> None:
        self.what, self.total, self.every, self.done = what, total, every, 0
        self.started = time.monotonic()

    def step(self) -> None:
        self.done += 1
        if self.total >= self.every and (self.done % self.every == 0 or self.done == self.total):
            elapsed = time.monotonic() - self.started
            left = elapsed / self.done * (self.total - self.done)
            sys.stderr.write(f"awa: {self.what} {self.done}/{self.total} ({int(left // 60)}m{int(left % 60):02d}s left)\n")
            sys.stderr.flush()


_RUN_COMPARE: dict[tuple[str, str], dict[tuple[str, str], str | None]] = {}
_RUN_RELEASES: dict[tuple[str, str], dict[str, Mapping[str, Any] | None]] = {}


class GitHubFacts:
    """Read-only lookups shared by one run, cached by immutable identities."""

    def __init__(self, transport: "GhTransport", repository: str) -> None:
        self.transport = transport
        self.repository = repository
        self.owner, self.name = repository.split("/", 1)
        self._default: str | None = None
        # Shared by every document a run touches (a release close reconciles several).
        self._compare = _RUN_COMPARE.setdefault((transport.host, repository), {})
        self._releases = _RUN_RELEASES.setdefault((transport.host, repository), {})

    def default_branch(self) -> str:
        if self._default is None:
            self._default = self.transport.rest(f"repos/{self.owner}/{self.name}")["default_branch"]
        return self._default

    def contains(self, ref: str, sha: str) -> bool | None:
        """Is `sha` contained in `ref`? None when GitHub does not know the commit."""
        key = (ref, sha)
        if key not in self._compare:
            result = self.transport.rest_optional(f"repos/{self.owner}/{self.name}/compare/{ref}...{sha}")
            self._compare[key] = None if result is None else result.get("status")
        status = self._compare[key]
        if status is None:
            return None
        return status in {"behind", "identical"}

    def release(self, tag: str) -> Mapping[str, Any] | None:
        """The published (non-draft) Release for a tag, or None."""
        if tag not in self._releases:
            self._releases[tag] = self.transport.rest_optional(
                f"repos/{self.owner}/{self.name}/releases/tags/{tag}"
            )
        return self._releases[tag]

    def full_releases(self) -> list[Mapping[str, Any]]:
        """Published, non-draft, non-prerelease Releases, oldest publication first."""
        found: list[Mapping[str, Any]] = []
        page = 1
        while True:
            values = self.transport.rest(
                f"repos/{self.owner}/{self.name}/releases?per_page=100&page={page}"
            )
            found.extend(values)
            if len(values) < 100:
                break
            page += 1
        full = [r for r in found if not r.get("draft") and not r.get("prerelease") and r.get("published_at")]
        return sorted(full, key=lambda r: (r["published_at"], r["id"]))

    def pull(self, url: str) -> Mapping[str, Any] | None:
        """Whether a PR merged, and its merge commit.

        REST API version 2026-03-10 no longer returns `merge_commit_sha`, so
        this reads `mergeCommit` through GraphQL.
        """
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        data = self.transport.graphql(
            "query($owner:String!,$name:String!,$n:Int!) { repository(owner:$owner,name:$name) { "
            "pullRequest(number:$n) { merged mergeCommit { oid } } } rateLimit { cost remaining resetAt } }",
            {"owner": self.owner, "name": self.name, "n": number},
        )
        found = (data.get("repository") or {}).get("pullRequest")
        if not found:
            return None
        return {"merged": bool(found.get("merged")), "merge_commit_sha": (found.get("mergeCommit") or {}).get("oid")}


def delivery_text(item: "DesiredItem") -> str | None:
    if item.delivery_kind is None:
        return None
    if item.delivery_kind == "release":
        return f"release {item.delivery_release}"
    return item.delivery_kind


def check_delivery(
    facts: GitHubFacts, manifest: "Manifest", issues: Mapping[int, "ManagedIssue"]
) -> dict[int, dict[str, str | None]]:
    """Check integration and delivery evidence against GitHub; return issue records.

    Every problem is listed at once, before anything is written.
    """
    problems: list[str] = []
    records: dict[int, dict[str, str | None]] = {}
    for item in manifest.items:
        if item.kind != "story":
            continue
        rec: dict[str, str | None] = {}
        decision = item.evidence.get("decision") if item.work_phase == WONT_DO else None
        rec["Won't do"] = f"{decision.reason} (decided by @{decision.author}: {decision.ref})" if decision else None
        if item.delivery_kind is not None:
            rec["Delivery"] = delivery_text(item)
        integration = item.evidence.get("integration")
        if integration is not None and integration.commit:
            rec["Integration"] = (
                integration.commit
                + (f" via {integration.pr}" if integration.pr else "")
                + (f" on {integration.branch}" if integration.branch else "")
            )
        gated = item.work_phase in {"Release ready", "Done"} and item.delivery_kind in {"release", "merge"}
        if gated:
            assert integration is not None and integration.commit
            branch = integration.branch or facts.default_branch()
            on_branch = facts.contains(branch, integration.commit)
            if on_branch is None:
                problems.append(
                    f"#{item.number}: GitHub does not know integration commit {integration.commit[:12]} or branch {branch}"
                )
            elif not on_branch:
                problems.append(f"#{item.number}: integration commit {integration.commit[:12]} is not on {branch}")
            if integration.pr:
                pull = facts.pull(integration.pr)
                if not pull or not pull.get("merged") or pull.get("merge_commit_sha") != integration.commit:
                    problems.append(
                        f"#{item.number}: {integration.pr} is not merged as {integration.commit[:12]}"
                    )
        if item.work_phase == "Done":
            delivery = item.evidence.get("delivery")
            if item.delivery_kind == "release" and integration is not None:
                tag = item.milestone or ""
                release = facts.release(tag)
                if release is None:
                    problems.append(f"#{item.number}: Done in {tag}, but {tag} has no published GitHub Release")
                elif release.get("prerelease"):
                    problems.append(f"#{item.number}: {tag} is a pre-release; only full releases deliver work")
                elif not facts.contains(tag, integration.commit or ""):
                    problems.append(f"#{item.number}: {tag} does not contain integration commit {integration.commit[:12]}")
                else:
                    rec["Delivered"] = release.get("html_url")
            elif item.delivery_kind == "merge" and integration is not None:
                rec["Delivered"] = f"https://github.com/{manifest.repository}/commit/{integration.commit}"
            elif delivery is not None:
                rec["Delivered"] = delivery.ref
        elif item.delivery_kind is not None:
            rec["Delivered"] = None
        records[item.number] = rec
    if problems:
        raise ReconcileError("delivery evidence does not match GitHub (nothing was written): " + "; ".join(problems))
    return records


@dataclass
class MilestoneChange:
    number: int
    before: str | None
    after: str | None
    reason: str | None


def plan_milestones(
    manifest: "Manifest", issues: Mapping[int, "ManagedIssue"]
) -> tuple[list[MilestoneChange], dict[int, dict[str, str | None]]]:
    """Guard and plan each story's release milestone.

    `Release:` in the managed block records the milestone awa last set.  A live
    milestone that is neither that record nor what this manifest asks for was
    changed outside awa, so the run stops instead of undoing it.
    """
    problems: list[str] = []
    changes: list[MilestoneChange] = []
    records: dict[int, dict[str, str | None]] = {}
    for item in manifest.items:
        if item.kind != "story":
            continue
        issue = issues[item.number]
        live = issue.milestone
        recorded = issue_record(issue.body, "Release")
        desired = item.milestone if item.milestone_specified else recorded
        if live not in (recorded, desired):
            shown = f"milestone {live}" if live else "no milestone"
            if recorded is not None:
                problems.append(
                    f"#{item.number}'s managed block has the line `Release: {recorded}`, which says awa set "
                    f"milestone {recorded}, but GitHub shows {shown}. If a person or agent typed that line, "
                    "delete it from the issue body and run again: awa writes the Release:, Delivery:, "
                    "Integration: and Delivered: lines itself. If awa did set it, the milestone was changed "
                    "outside awa: put it back, or re-run --draft and move it in the manifest with a "
                    "milestone_change_reason"
                )
            else:
                problems.append(
                    f"#{item.number} is in {shown} on GitHub, but awa never set a milestone on it and this "
                    f"manifest asks for {desired!r}; it was changed outside awa. Put it back, or re-run "
                    "--draft and move it in the manifest with a milestone_change_reason"
                )
            continue
        records[item.number] = {"Release": desired}
        if live == desired:
            continue
        if recorded is not None and desired != recorded and not item.milestone_change_reason:
            problems.append(
                f"#{item.number} moves from release {recorded} to {desired!r}; "
                "a release move needs milestone_change_reason"
            )
            continue
        changes.append(
            MilestoneChange(
                number=item.number,
                before=live,
                after=desired,
                reason=item.milestone_change_reason if recorded is not None else None,
            )
        )
    if problems:
        raise ReconcileError("release milestones (nothing was written): " + "; ".join(problems))
    return changes, records


def list_milestones(transport: "GhTransport", repository: str) -> dict[str, Mapping[str, Any]]:
    owner, name = repository.split("/", 1)
    found: dict[str, Mapping[str, Any]] = {}
    page = 1
    while True:
        values = transport.rest(f"repos/{owner}/{name}/milestones?state=all&per_page=100&page={page}")
        for value in values:
            found[value["title"]] = value
        if len(values) < 100:
            return found
        page += 1


def ensure_milestone(
    transport: "GhTransport", repository: str, title: str, receipt: "Receipt",
    milestones: dict[str, Mapping[str, Any]], *, description: str | None = None,
) -> Mapping[str, Any]:
    existing = milestones.get(title)
    if existing is not None:
        return existing
    owner, name = repository.split("/", 1)
    label = f"create release milestone {title}"
    receipt.planned_mutations.append(label)
    payload: dict[str, Any] = {"title": title}
    if description:
        payload["description"] = description
    created = transport.rest(f"repos/{owner}/{name}/milestones", method="POST", data=payload)
    milestones[title] = created
    receipt.applied_mutations.append(label)
    return created


def post_once(transport: "GhTransport", repository: str, number: int, marker: str, text: str) -> bool:
    """Post a comment unless one carrying `marker` already exists."""
    owner, name = repository.split("/", 1)
    page = 1
    while True:
        comments = transport.rest(f"repos/{owner}/{name}/issues/{number}/comments?per_page=100&page={page}")
        if any(marker in (comment.get("body") or "") for comment in comments):
            return False
        if len(comments) < 100:
            break
        page += 1
    transport.rest(
        f"repos/{owner}/{name}/issues/{number}/comments",
        method="POST",
        data={"body": f"{text}\n\n<!-- {marker} -->"},
    )
    return True


def apply_milestones(
    transport: "GhTransport", manifest: "Manifest", changes: Sequence[MilestoneChange], receipt: "Receipt",
    *, allow_closed: bool = False,
) -> None:
    if not changes:
        return
    owner, name = manifest.repository.split("/", 1)
    milestones = list_milestones(transport, manifest.repository)
    for change in changes:
        if change.after is not None:
            milestone = ensure_milestone(transport, manifest.repository, change.after, receipt, milestones)
            if milestone.get("state") == "closed" and not allow_closed:
                raise ReconcileError(
                    f"#{change.number} cannot join release milestone {change.after}: it is closed (released)"
                )
        if change.reason:
            marker = f"work-accountability:release-move #{change.number} {change.before}->{change.after}"
            if post_once(
                transport, manifest.repository, change.number, marker,
                f"Release target moved from {change.before} to {change.after or 'no release'}: {change.reason}",
            ):
                receipt.applied_mutations.append(f"comment on #{change.number}: release move reason")
        label = f"set #{change.number} release milestone {change.after or '(none)'}"
        receipt.planned_mutations.append(label)
        number = milestones[change.after]["number"] if change.after is not None else None
        fresh = transport.rest(
            f"repos/{owner}/{name}/issues/{change.number}", method="PATCH", data={"milestone": number}
        )
        if (fresh.get("milestone") or {}).get("title") != change.after:
            raise ReconcileError(
                f"GitHub did not keep release milestone {change.after!r} on #{change.number}. GitHub drops "
                "milestone changes silently when the account lacks push access to the repository."
            )
        receipt.applied_mutations.append(label)


def read_blockers(
    transport: "GhTransport", repository: str, numbers: Sequence[int]
) -> dict[int, dict[int, str]]:
    """Each issue's same-repository blockers and their state (OPEN/CLOSED)."""
    owner, name = repository.split("/", 1)
    found: dict[int, dict[int, str]] = {}
    for offset in range(0, len(numbers), 50):
        batch = numbers[offset : offset + 50]
        aliases = "\n".join(
            f"i{n}:issue(number:{n}) {{ id blockedBy(first:50) {{ nodes {{ number state repository {{ nameWithOwner }} }} pageInfo {{ hasNextPage }} }} }}"
            for n in batch
        )
        data = transport.graphql(
            f"query($owner:String!,$name:String!) {{ repository(owner:$owner,name:$name) {{ {aliases} }} rateLimit {{ cost remaining resetAt }} }}",
            {"owner": owner, "name": name},
        )
        for n in batch:
            nodes = data["repository"][f"i{n}"]["blockedBy"]["nodes"]
            found[n] = {
                int(node["number"]): node["state"]
                for node in nodes
                if (node.get("repository") or {}).get("nameWithOwner", repository).casefold() == repository.casefold()
            }
    return found


@dataclass
class DependencyPlan:
    add: list[tuple[int, int]]
    remove: list[tuple[int, int]]
    records: dict[int, dict[str, str | None]]


def plan_dependencies(
    manifest: "Manifest",
    issues: Mapping[int, "ManagedIssue"],
    all_issues: Mapping[int, "ManagedIssue"],
    live: Mapping[int, Mapping[int, str]],
    targets: Mapping[int, Mapping[str, Any]],
    receipt: "Receipt",
) -> DependencyPlan:
    """Make awa's blocked-by links match the manifest; never touch links people added.

    `Blocked by:` in the managed block records the links awa made.  Links people
    added are kept and reported.  Health = Blocked needs an open blocker or a
    recorded reason.
    """
    by_key = {issue.work_key: number for number, issue in all_issues.items()}
    problems: list[str] = []
    plan = DependencyPlan(add=[], remove=[], records={})
    for item in manifest.items:
        if item.kind != "story":
            continue
        recorded_text = issue_record(issues[item.number].body, "Blocked by") or ""
        recorded = {int(n) for n in re.findall(r"#(\d+)", recorded_text)}
        current = dict(live.get(item.number, {}))
        if item.blocked_by is None:
            desired = recorded
        else:
            desired = set()
            for key in item.blocked_by:
                if key not in by_key:
                    problems.append(f"#{item.number} is blocked by {key}, which is not a managed issue")
                else:
                    desired.add(by_key[key])
        for blocker in sorted(desired - set(current)):
            plan.add.append((item.number, blocker))
        for blocker in sorted((recorded - desired) & set(current)):
            plan.remove.append((item.number, blocker))
        human = sorted(set(current) - recorded - desired)
        if human:
            receipt.notes.append(
                f"#{item.number} is also blocked by " + ", ".join(f"#{n}" for n in human)
                + " (added outside awa; kept)"
            )
        after = {n: state for n, state in current.items() if n not in {b for i, b in plan.remove if i == item.number}}
        for blocker in desired:
            after.setdefault(blocker, all_issues[blocker].state.upper() if blocker in all_issues else "OPEN")
        open_blockers = sorted(n for n, state in after.items() if state == "OPEN")
        health = targets.get(item.number, {}).get("Health")
        reason = item.blocked_reason or (
            issue_record(issues[item.number].body, "Blocked reason") if health == "Blocked" else None
        )
        if health == "Blocked" and not open_blockers and not reason:
            problems.append(
                f"#{item.number} is Blocked but has no open blocked-by issue; add blocked_by or blocked_reason"
            )
        if health == "On track" and open_blockers:
            receipt.notes.append(
                f"#{item.number} is On track but blocked by open " + ", ".join(f"#{n}" for n in open_blockers)
            )
        plan.records[item.number] = {
            "Blocked by": ", ".join(f"#{n}" for n in sorted(desired)) or None,
            "Blocked reason": reason if health == "Blocked" else None,
        }
    if problems:
        raise ReconcileError("blocked-by (nothing was written): " + "; ".join(problems))
    return plan


def apply_dependencies(
    transport: "GhTransport",
    manifest: "Manifest",
    all_issues: Mapping[int, "ManagedIssue"],
    plan: DependencyPlan,
    receipt: "Receipt",
) -> None:
    if not plan.add and not plan.remove:
        return
    for mutation, pairs, verb in (("addBlockedBy", plan.add, "block"), ("removeBlockedBy", plan.remove, "unblock")):
        payloads = [
            (
                f"{verb} #{issue} by #{blocker}" if verb == "block" else f"remove #{blocker} as a blocker of #{issue}",
                {"issueId": all_issues[issue].node_id, "blockingIssueId": all_issues[blocker].node_id},
            )
            for issue, blocker in pairs
        ]
        for label, _payload in payloads:
            receipt.planned_mutations.append(label)
        receipt.applied_mutations.extend(
            batch_mutations(
                transport, mutation, mutation[0].upper() + mutation[1:] + "Input", payloads,
                "issue { id }",
            )
        )
    touched = sorted({issue for issue, _ in plan.add + plan.remove})
    live = read_blockers(transport, manifest.repository, touched)
    for issue, blocker in plan.add:
        if blocker not in live.get(issue, {}):
            raise ReconcileError(f"GitHub did not show #{issue} blocked by #{blocker} after adding it")
    for issue, blocker in plan.remove:
        if blocker in live.get(issue, {}):
            raise ReconcileError(f"GitHub still shows #{issue} blocked by #{blocker} after removing it")


STATUS_MARKER = "<!-- work-accountability:status -->"
STATUS_FOR_HEALTH = {"On track": "ON_TRACK", "At risk": "AT_RISK", "Blocked": "OFF_TRACK"}


def document_status(manifest: "Manifest", targets: Mapping[int, Mapping[str, Any]]) -> tuple[str, str]:
    """The document's status and the body awa posts for it."""
    stories = [item for item in manifest.items if item.kind == "story"]
    root = targets[manifest.root_number]
    if stories and all(targets[item.number].get("Work phase") in ("Done", WONT_DO) for item in stories):
        status = "COMPLETE"
    else:
        status = STATUS_FOR_HEALTH.get(root.get("Health") or "On track", "ON_TRACK")
    releases = sorted({
        item.milestone for item in stories
        if item.milestone and targets[item.number].get("Work phase") not in ("Done", WONT_DO)
    })
    body = f"Progress: {root.get('Progress') or '0/0 Done'}"
    if releases:
        body += "\n\nOpen release milestones: " + ", ".join(releases)
    return status, body + "\n\n" + STATUS_MARKER


def latest_awa_status(
    transport: "GhTransport", owner_type: str, owner: str, number: int
) -> Mapping[str, Any] | None:
    """awa's newest Project status update (id, status, body), or None. Newest first, paging until found."""
    owner_field = "organization" if owner_type == "Organization" else "user"
    cursor: str | None = None
    while True:
        data = transport.graphql(
            f"""query($login:String!,$number:Int!,$after:String) {{ {owner_field}(login:$login) {{ projectV2(number:$number) {{
              statusUpdates(first:50, after:$after, orderBy:{{field:CREATED_AT, direction:DESC}}) {{
                nodes {{ id status body createdAt }} pageInfo {{ hasNextPage endCursor }} }}
            }} }} rateLimit {{ cost remaining resetAt }} }}""",
            {"login": owner, "number": number, "after": cursor},
        )
        page = data[owner_field]["projectV2"]["statusUpdates"]
        mine = [node for node in page["nodes"] if STATUS_MARKER in (node.get("body") or "")]
        if mine:
            return max(mine, key=lambda node: node.get("createdAt") or "")
        if not page["pageInfo"].get("hasNextPage"):
            return None
        cursor = page["pageInfo"].get("endCursor")


def post_status(
    transport: "GhTransport",
    project: "ProjectState",
    repo: "RepositoryState",
    manifest: "Manifest",
    targets: Mapping[int, Mapping[str, Any]],
    receipt: "Receipt",
    *,
    apply: bool,
) -> None:
    """Keep the document's Project status current.

    A new status update is posted only when the status itself changes, so the
    status history stays meaningful; when only the progress text changes, awa
    edits its latest update in place so it never shows stale numbers.
    """
    status, body = document_status(manifest, targets)
    latest = latest_awa_status(transport, repo.owner_type, manifest.project_owner, project.number)
    if latest is not None and latest.get("status") == status:
        if (latest.get("body") or "") == body:
            return
        label = f"update Project status text ({status})"
        receipt.planned_mutations.append(label)
        if not apply:
            return
        mutate_one(
            transport,
            "updateProjectV2StatusUpdate",
            "UpdateProjectV2StatusUpdateInput",
            {"statusUpdateId": latest["id"], "body": body},
            "statusUpdate { id status body }",
        )
    else:
        label = f"post Project status {status}"
        receipt.planned_mutations.append(label)
        if not apply:
            return
        mutate_one(
            transport,
            "createProjectV2StatusUpdate",
            "CreateProjectV2StatusUpdateInput",
            {"projectId": project.id, "status": status, "body": body},
            "statusUpdate { id status }",
        )
    now = latest_awa_status(transport, repo.owner_type, manifest.project_owner, project.number)
    if not now or now.get("status") != status or (now.get("body") or "") != body:
        raise ReconcileError(f"GitHub did not show the {status} status update on Project #{project.number}")
    receipt.applied_mutations.append(label)


ACCEPTED_PHASES = ("Release ready", "Done")
AWAITING_RELEASE_LABEL = "awaiting-release"


@dataclass
class IssueStateChange:
    number: int
    close: str | None = None  # the state_reason to close with: completed or not_planned
    reopen_from: str | None = None
    reopen_after: str = "it was accepted"
    add_label: bool = False
    remove_label: bool = False
    is_epic: bool = False


def plan_issue_states(
    manifest: "Manifest",
    issues: Mapping[int, "ManagedIssue"],
    targets: Mapping[int, Mapping[str, Any]],
    receipt: "Receipt",
) -> list[IssueStateChange]:
    """Closed means accepted: an issue is closed exactly while its story is Release ready or Done.

    That makes a release milestone's progress bar show accepted work before the
    release ships.  Accepted release-delivered stories carry `awaiting-release`
    until the release ships.  An issue a person closed as not planned or
    duplicate is reported, never reopened.
    """
    changes: list[IssueStateChange] = []
    for item in manifest.items:
        if item.kind != "story":
            continue
        issue = issues[item.number]
        phase = targets.get(item.number, {}).get("Work phase") or item.work_phase
        accepted = phase in ACCEPTED_PHASES
        wont_do = phase == WONT_DO
        awaiting = accepted and phase != "Done" and item.delivery_kind == "release"
        has_label = AWAITING_RELEASE_LABEL in issue.labels
        # awa closed it as not planned for Won't do, so awa may change that again.
        closed_by_awa = issue.state_reason in (None, "completed") or (
            issue.state_reason == "not_planned" and issue_record(issue.body, WONT_DO) is not None
        )
        change = IssueStateChange(number=item.number)
        if wont_do:
            if issue.state == "open" or issue.state_reason != "not_planned":
                change.close = "not_planned"
        elif accepted:
            if issue.state == "open" or (issue.state_reason == "not_planned" and closed_by_awa):
                change.close = "completed"
        elif issue.state == "closed":
            if closed_by_awa:
                change.reopen_from = phase
                if issue.state_reason == "not_planned":
                    change.reopen_after = "it was marked won't do"
            else:
                receipt.notes.append(
                    f"#{item.number} is closed as {issue.state_reason} but its Work phase is {phase}; "
                    "left closed because a person closed it that way"
                )
        change.add_label = awaiting and not has_label
        change.remove_label = has_label and not awaiting
        if change.close or change.reopen_from or change.add_label or change.remove_label:
            changes.append(change)

    # Close an epic once every story beneath it is finished; reopen it if one comes
    # back.  An ongoing workstream root (a root with no bound planning document) is
    # never closed, so it can keep taking new work.
    children: dict[str, list["DesiredItem"]] = {}
    for item in manifest.items:
        if item.parent is not None:
            children.setdefault(item.parent, []).append(item)

    def leaf_stories(item: "DesiredItem") -> list["DesiredItem"]:
        if item.kind == "story":
            return [item]
        found: list["DesiredItem"] = []
        for child in children.get(item.work_key, []):
            found.extend(leaf_stories(child))
        return found

    source_path = manifest.source.get("path") if isinstance(manifest.source, Mapping) else None
    for item in manifest.items:
        if item.kind != "epic":
            continue
        is_root = item.parent is None and item.number == manifest.root_number
        if is_root and not source_path:
            continue  # ongoing workstream root: keep it open
        stories = leaf_stories(item)
        if not stories:
            continue  # an epic with no stories yet is not "finished"
        finished = all(
            (targets.get(s.number, {}).get("Work phase") or s.work_phase) in ("Done", WONT_DO)
            for s in stories
        )
        issue = issues[item.number]
        closed_by_awa = issue.state_reason in (None, "completed")
        change = IssueStateChange(number=item.number, is_epic=True)
        if finished and issue.state == "open":
            change.close = "completed"
        elif not finished and issue.state == "closed":
            if closed_by_awa:
                change.reopen_from = "an unfinished state"
                change.reopen_after = "a story under it reopened"
            else:
                receipt.notes.append(
                    f"#{item.number} (epic) is closed as {issue.state_reason} but its stories are not all "
                    "finished; left closed because a person closed it that way"
                )
        if change.close or change.reopen_from:
            changes.append(change)
    return changes


def describe_issue_state(change: IssueStateChange) -> list[str]:
    labels = []
    if change.close == "completed":
        labels.append(
            f"close epic #{change.number} (all stories finished)" if change.is_epic
            else f"close #{change.number} as completed (accepted)"
        )
    elif change.close:
        labels.append(f"close #{change.number} as not planned (won't do)")
    if change.reopen_from:
        labels.append(
            f"reopen epic #{change.number} (a story reopened)" if change.is_epic
            else f"reopen #{change.number} (back in {change.reopen_from})"
        )
    if change.add_label:
        labels.append(f"label #{change.number} {AWAITING_RELEASE_LABEL}")
    if change.remove_label:
        labels.append(f"remove {AWAITING_RELEASE_LABEL} from #{change.number}")
    return labels


def project_fragment() -> str:
    return """
      id number title url readme shortDescription closed public createdAt
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
        public=bool(raw.get("public")),
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
    visibility: str | None = None,
    release_view: int | None = None,
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
    release_view = release_view if release_view is not None else read_view_marker(current, "Release")
    if release_view is not None:
        block.append(f"Release view: {release_view}")
    if initial_views:
        block.append(f"Initial views: {' '.join(initial_views)}")
    recorded = visibility or recorded_visibility(current)
    if recorded:
        block.append(f"Visibility: {recorded}")
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


def setup_title(manifest: Manifest) -> str:
    """A neutral title a new Project carries until it is verified private."""
    return "work-accountability setup " + hashlib.sha256(manifest.root_work_key.encode()).hexdigest()[:10]


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
        "title": setup_title(manifest),
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
            # GitHub may create it public; the real title is set only after it is private.
            "title": setup_title(manifest),
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


def live_visibility(project: ProjectState) -> str:
    return "public" if project.public else "private"


VISIBILITY_RECORD = re.compile(r"^Visibility:\s*(private|public)\s*$", re.MULTILINE)


def recorded_visibility(readme: str) -> str | None:
    """The visibility awa last set, from the Project README's managed block."""
    block = PROJECT_BLOCK.search(readme)
    match = VISIBILITY_RECORD.search(block.group(0)) if block else None
    return match.group(1) if match else None


def plan_visibility(manifest: Manifest, project: ProjectState) -> str:
    """Return the visibility an existing board must have; refuse drift.

    The README's `Visibility:` line, written only by `awa project visibility`,
    is the record of a deliberate choice; a board without it must be private.
    Reconcile never changes visibility, so a board whose live visibility differs
    from that record was changed outside awa and stops the run.
    """
    expected = recorded_visibility(project.readme) or "private"
    live = live_visibility(project)
    if live != expected:
        raise ReconcileError(
            f"Project #{project.number} is {live} on GitHub, but awa last set it {expected}; "
            "it was changed outside awa. Nothing was written. To keep it "
            f"{live}, run `awa project visibility {manifest.root_number} {live}`; "
            f"otherwise make it {expected} again in GitHub."
        )
    return expected


def set_visibility(
    transport: GhTransport, project: ProjectState, visibility: str, receipt: Receipt
) -> ProjectState:
    label = f"make Project #{project.number} {visibility}"
    receipt.planned_mutations.append(label)
    try:
        result = mutate_one(
            transport,
            "updateProjectV2",
            "UpdateProjectV2Input",
            {
                "projectId": project.id,
                "public": visibility == "public",
                "clientMutationId": f"work-accountability:{project.id}:visibility",
            },
            f"projectV2 {{ {project_fragment()} }}",
        )
    except TemporaryFailure:
        raise
    except ReconcileError as error:
        raise ReconcileError(
            f"GitHub refused to make Project #{project.number} {visibility} "
            f"(organization policy or missing Project admin rights): {error}. "
            "Its visibility is unchanged."
        ) from error
    updated = parse_project_summary(result["projectV2"])
    if live_visibility(updated) != visibility:
        raise ReconcileError(f"GitHub did not show Project #{project.number} as {visibility} after the change")
    receipt.applied_mutations.append(label)
    return updated


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


def section_option_payload(
    manifest: Manifest, names: Sequence[str], offset: int, progress: Mapping[str, str] | None = None
) -> list[dict[str, str]]:
    progress = progress or manifest.section_progress()
    owners = manifest.section_labels()
    return [
        {
            "name": name,
            "color": COLORS[(offset + index) % len(COLORS)],
            "description": section_description(owners[name], progress.get(owners[name], "No stories yet")),
        }
        for index, name in enumerate(names)
    ]


def planned_options(
    manifest: Manifest, name: str, current: FieldState, options: Sequence[str],
    progress: Mapping[str, str] | None = None,
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
            match.group(1): option
            for option in preserved
            if (match := SECTION_OWNER.search(option["description"]))
        }
        progress = progress or manifest.section_progress()
        wanted = {owner: section_description(owner, progress.get(owner, "No stories yet")) for owner in by_owner}
        if any(option["description"] != wanted[owner] for owner, option in by_owner.items()):
            changes.append("update section progress in the By section headers")
            for owner, option in by_owner.items():
                option["description"] = wanted[owner]
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
            preserved.extend(section_option_payload(manifest, missing, len(preserved), progress))
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
    transport: GhTransport, project: ProjectState, manifest: Manifest, receipt: Receipt,
    progress: Mapping[str, str] | None = None,
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
                    section_option_payload(manifest, options, 0, progress)
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
        payload_options, changes = planned_options(manifest, name, current, options, progress)
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


def section_filter(repository: str, root_number: int) -> str:
    """Stories only, like By release: signed-out visitors cannot expand nested rows,
    so stories sit directly in their section's group and the group header (the
    Section option's description) carries the section's progress."""
    return LIFECYCLE_FILTER


def section_view_shaped(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    """The section table's layout, grouping and sort, whatever its filter."""
    section = fields.get("Section")
    if not section or view.layout != "TABLE_LAYOUT" or view.group_ids != [section.id]:
        return False
    return sort_prefix_matches(view, fields, ("Rank",))


def section_view_valid(view: ViewState, fields: Mapping[str, FieldState], wanted_filter: str) -> bool:
    return section_view_shaped(view, fields) and view.filter == wanted_filter


def refilter_label(view: ViewState, role: str) -> str:
    """`role` is the managed table's name; a person may have renamed the view itself."""
    return f"filter {role} #{view.number} to stories, so GitHub's hierarchy cannot fold them under the root"


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
    elif which == RELEASE_VIEW:
        visible = ("Title", "Work phase", "Health", "Section", "Priority")
    else:
        visible = ("Title", "Work phase", "Health", "Priority")
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
    elif which == RELEASE_VIEW:
        payload = {
            "name": RELEASE_VIEW,
            "layout": "table",
            "filter": RELEASE_FILTER,
            "visible_fields": [fields[name].database_id for name in visible],
            "sort_by": [[fields["Rank"].database_id, "asc"]],
            "group_by": [fields["Milestone"].database_id],
        }
    else:
        payload = {
            "name": SECTION_VIEW,
            "layout": "table",
            "filter": section_filter(manifest.repository, manifest.root_number),
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
    release: int | None = None
    create_release: bool = False
    refilter: list[tuple[ViewState, str, str]] = field(default_factory=list)  # view, filter, role


def plan_views(
    project: ProjectState, repair: bool, fresh: bool, wanted_section_filter: str
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

    refilter: list[tuple[ViewState, str, str]] = []

    # By section: must come after Lifecycle. A table with the right shape but an
    # older filter keeps its number and gets the current filter.
    recorded, named = candidates("Section", SECTION_VIEW)
    pool = ([recorded] if recorded else []) + named
    after = [
        view for view in pool
        if ready and section_view_shaped(view, fields)
        and (view is recorded or view.filter in (None, "", wanted_section_filter))
        and not create_lifecycle and lifecycle is not None and view.position > lifecycle.position
    ]
    after.sort(key=lambda view: not section_view_valid(view, fields, wanted_section_filter))
    section = after[0] if after else None
    if section is not None and section.filter != wanted_section_filter:
        refilter.append((section, wanted_section_filter, SECTION_VIEW))
    for view in pool:
        if view is section:
            continue
        if view is recorded or (
            ready and section_view_shaped(view, fields) and view.filter in (None, "", wanted_section_filter)
        ) or repair:
            delete.append(view)
        else:
            raise ReconcileError(
                f"an unrecorded view named {SECTION_VIEW!r} (#{view.number}) is not the managed "
                "section table; rename it or rerun with --repair-lifecycle to replace it"
            )
    create_section = section is None

    # By release: after By section.
    recorded, named = candidates("Release", RELEASE_VIEW)
    pool = ([recorded] if recorded else []) + named
    after = [
        view for view in pool
        if ready and release_view_shaped(view, fields)
        and (view is recorded or view.filter in (None, "", RELEASE_FILTER))
        and not create_section and section is not None and view.position > section.position
    ]
    after.sort(key=lambda view: not release_view_valid(view, fields))
    release = after[0] if after else None
    if release is not None and release.filter != RELEASE_FILTER:
        refilter.append((release, RELEASE_FILTER, RELEASE_VIEW))
    for view in pool:
        if view is release:
            continue
        if view is recorded or (
            ready and release_view_shaped(view, fields) and view.filter in (None, "", RELEASE_FILTER)
        ) or repair:
            delete.append(view)
        else:
            raise ReconcileError(
                f"an unrecorded view named {RELEASE_VIEW!r} (#{view.number}) is not the managed "
                "release table; rename it or rerun with --repair-lifecycle to replace it"
            )
    create_release = release is None

    for view in project.views.values():
        if view in delete or view is lifecycle or view is section or view is release:
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
        release=release.number if release else None,
        create_release=create_release,
        refilter=refilter,
    )


def fields_ready_for_views(fields: Mapping[str, FieldState]) -> bool:
    return all(name in fields for name in ("Work phase", "Section", "Priority", "Rank", "Milestone"))


def release_view_shaped(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    milestone = fields.get("Milestone")
    if not milestone or view.layout != "TABLE_LAYOUT" or view.group_ids != [milestone.id]:
        return False
    return sort_prefix_matches(view, fields, ("Rank",))


def release_view_valid(view: ViewState, fields: Mapping[str, FieldState]) -> bool:
    return release_view_shaped(view, fields) and view.filter == RELEASE_FILTER


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
    release: ViewState | None = None,
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
    missing = sorted(leaves - shown_here)
    epic_rows = sorted(epics & shown_here)
    if missing or epic_rows:
        raise ReconcileError(
            "board check: the By section table "
            + (f"does not show stories {', '.join(f'#{n}' for n in missing)}" if missing else "")
            + ("; " if missing and epic_rows else "")
            + (f"shows epics {', '.join(f'#{n}' for n in epic_rows)}" if epic_rows else "")
            + f" (filter {section.filter!r})"
        )
    if release is not None:
        shown, _others = evaluate_view(
            transport, repo.owner_type, manifest.project_owner, project.number, release.filter
        )
        shown_here = {number for name, number in shown if name.casefold() == repository.casefold()}
        missing = sorted(leaves - shown_here)
        epic_rows = sorted(epics & shown_here)
        if missing or epic_rows:
            raise ReconcileError(
                "board check: the By release table "
                + (f"does not show stories {', '.join(f'#{n}' for n in missing)}" if missing else "")
                + ("; " if missing and epic_rows else "")
                + (f"shows epics {', '.join(f'#{n}' for n in epic_rows)}" if epic_rows else "")
                + f" (filter {release.filter!r})"
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
    expected_visibility: str,
) -> None:
    if repo.name_with_owner not in project.repositories:
        raise ReconcileError("final verification: Project is not linked to the repository")
    if not marked_for(project, transport.host, repo.id, manifest.root_work_key):
        raise ReconcileError("final verification: Project marker is absent")
    if live_visibility(project) != expected_visibility:
        raise ReconcileError(
            f"final verification: Project is {live_visibility(project)}, expected {expected_visibility}"
        )
    lifecycle = project.views.get(lifecycle_number)
    if not lifecycle or not lifecycle_view_valid(lifecycle, project.fields):
        raise ReconcileError("final verification: Lifecycle is not a whole-document Work phase Kanban")
    section = project.views.get(section_number)
    if not section or not section_view_valid(
        section, project.fields, section_filter(manifest.repository, manifest.root_number)
    ):
        raise ReconcileError("final verification: By section is not a table grouped by Section")
    if section.position < lifecycle.position:
        raise ReconcileError("final verification: By section comes before Lifecycle")
    release = project.views.get(read_view_marker(project.readme, "Release") or -1)
    if not release or not release_view_valid(release, project.fields) or release.position < section.position:
        raise ReconcileError("final verification: By release is not a table grouped by Milestone after By section")
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
    verify_boards(transport, repo, manifest, project, lifecycle, section, release)
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


def draft_source(body: str) -> dict[str, str]:
    """The planning document a root is bound to, from its `Source:` line.

    A document root reads ``Source: `PATH` at `COMMIT` (`BLOB`)``; a workstream
    root (no planning document) has free text, so this returns {} and the root
    is treated as ongoing and never auto-closed.
    """
    line = issue_record(body, "Source")
    if not line:
        return {}
    match = re.match(r"`([^`]+)` at `([0-9a-fA-F]{7,40})`", line.strip())
    return {"path": match.group(1), "commit": match.group(2)} if match else {}


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


def parse_delivery_text(text: str) -> dict[str, Any] | None:
    """`release v0.3.0`, `release next`, `merge` or `other`, as awa and extraction-v3 write it."""
    parts = text.split()
    if len(parts) == 2 and parts[0] == "release" and (parts[1] == "next" or RELEASE_TAG.fullmatch(parts[1])):
        return {"kind": "release", "release": parts[1]}
    if len(parts) == 1 and parts[0] in {"merge", "other"}:
        return {"kind": parts[0]}
    return None


def draft_delivery(issue: ManagedIssue) -> tuple[dict[str, Any] | None, bool, str | None]:
    """The story's delivery, whether it is only a proposal from prose, and a note.

    awa's own `Delivery:` record wins, then awa's `Release:` record (boards from
    before awa recorded deliveries). Otherwise the `Planned delivery:` line an
    agent copied from an extraction-v3 manifest is used as is. Only without
    any of these is delivery proposed from the free-text boundary, flagged for
    review. Any disagreement or unreadable line becomes a note.
    """
    recorded = issue_record(issue.body, "Delivery")
    planned_text = issue_record(issue.body, "Planned delivery")
    planned = parse_delivery_text(planned_text) if planned_text else None
    notes: list[str] = []
    if planned_text and planned is None:
        notes.append(
            f"#{issue.number}: cannot read `Planned delivery: {planned_text}`; write release TAG, "
            "release next, merge or other"
        )
    def note() -> str | None:
        return "; ".join(notes) or None

    recorded_release = issue_record(issue.body, "Release")
    found = parse_delivery_text(recorded) if recorded else None
    if recorded and found is None:
        notes.append(f"#{issue.number}: cannot read awa's `Delivery: {recorded}` record; the next apply rewrites it")
    if found is None and recorded_release:
        # awa set a release milestone before it recorded deliveries: that record
        # wins over any plan or guess.
        found = {"kind": "release", "release": recorded_release}
    if found:
        if planned and planned != found:
            shown = recorded if recorded and parse_delivery_text(recorded) else f"release {recorded_release}"
            notes.append(
                f"#{issue.number}: its `Planned delivery: {planned_text}` line disagrees with awa's record "
                f"`{shown}`; awa keeps its record. Re-copy the line from the current extraction report, or "
                "move the story with delivery and a milestone_change_reason in the manifest"
            )
        return found, False, note()
    if planned:
        return planned, False, note()
    boundary = issue_record(issue.body, "Delivery boundary") or ""
    if not boundary:
        return None, False, note()
    version = re.search(r"\bv\d+\.\d+(?:\.\d+)?\b", boundary)
    if version:
        return {"kind": "release", "release": version.group(0)}, True, note()
    if re.search(r"\bmerged?\b", boundary, re.IGNORECASE):
        return {"kind": "merge"}, True, note()
    if re.search(r"\brelease\b", boundary, re.IGNORECASE):
        return {"kind": "release", "release": "next"}, True, note()
    return {"kind": "other"}, True, note()


def run_draft(args: argparse.Namespace) -> int:
    """Print a project-v4 manifest built from live GitHub state (read-only)."""
    if not args.repo or not args.root:
        raise ReconcileError("--draft needs --repo OWNER/REPOSITORY and --root ISSUE_NUMBER")
    manifest, notes = build_draft(GhTransport(args.host, args.user), args)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    for note in notes:
        sys.stderr.write(f"draft: {note}\n")
    return 0


def build_draft(transport: GhTransport, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """A complete manifest for one document, from live GitHub state (read-only)."""
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
    proposed_delivery: list[int] = []
    delivery_notes: list[str] = []
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
        if kind == "story":
            delivery, proposed, delivery_note = draft_delivery(issue)
            if delivery_note:
                delivery_notes.append(delivery_note)
            recorded = issue_record(issue.body, "Release")
            if delivery and delivery.get("release") not in (None, "next") and recorded and recorded != delivery["release"]:
                delivery["release"] = recorded  # awa's milestone record wins over boundary text
            if delivery:
                item["delivery"] = delivery
                if proposed:
                    proposed_delivery.append(number)
            if recorded is not None:
                item["milestone"] = recorded
            blockers = [int(b) for b in re.findall(r"#(\d+)", issue_record(issue.body, "Blocked by") or "")]
            if blockers:
                item["blocked_by"] = [issues[b].work_key for b in blockers if b in issues]
            reason = issue_record(issue.body, "Blocked reason")
            if reason:
                item["blocked_reason"] = reason
        if kind == "story":
            needed = list(REQUIRED_EVIDENCE.get(item["work_phase"], ()))
            if (
                item["work_phase"] in {"Release ready", "Done"}
                and (item.get("delivery") or {}).get("kind") in {"release", "merge"}
            ):
                needed.append("integration")
            absent = [n for n in needed if n not in item["evidence"]]
            if absent:
                missing_evidence.append(f"#{number} {item['work_phase']} needs {', '.join(absent)}")
        items.append(item)
        live = details[destination.number].items[number].values if destination and number in details[destination.number].items else {}
        observed[str(number)] = {name: live[name] for name in GUARDED_FIELDS if name in live}
    manifest = {
        "schema": SCHEMA,
        "repository": repository,
        "scope": {"root_number": args.root, "root_work_key": root_key, "source": draft_source(root.body)},
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
    notes = list(delivery_notes)
    if proposed_delivery:
        notes.append(
            "proposed delivery from boundary text for " + ", ".join(f"#{n}" for n in proposed_delivery)
            + "; check each before --apply (awa records it once applied)"
        )
    if include:
        notes.append("apply with --attach-parents to put " + ", ".join(f"#{n}" for n in include) + f" under #{args.root}")
    if sources:
        notes.append("supersedes lists " + ", ".join(f"#{p.number}" for p in sources) + "; they close after the merged board verifies")
    if missing_evidence:
        notes.append("add evidence before --apply: " + "; ".join(missing_evidence))
    return manifest, notes


PROJECT_URL = re.compile(r"^https://[^/]+/(users|orgs)/([^/]+)/projects/(\d+)(?:/.*)?$")


def with_visibility_record(readme: str, visibility: str) -> str:
    block = PROJECT_BLOCK.search(readme)
    if not block:
        raise ReconcileError("the Project README has no work-accountability block; reconcile it first")
    text = block.group(0)
    if VISIBILITY_RECORD.search(text):
        text = VISIBILITY_RECORD.sub(f"Visibility: {visibility}", text, count=1)
    else:
        text = text.replace(
            "<!-- work-accountability:end-project -->",
            f"Visibility: {visibility}\n<!-- work-accountability:end-project -->",
        )
    return readme[: block.start()] + text + readme[block.end() :]


def visibility_preview(
    transport: GhTransport, repo: RepositoryState, repository: str, detail: ProjectState
) -> list[str]:
    owner, name = repository.split("/", 1)
    private_repo = bool(
        transport.graphql(
            "query($o:String!,$n:String!){ repository(owner:$o,name:$n){ isPrivate } rateLimit { cost remaining resetAt } }",
            {"o": owner, "n": name},
        )["repository"]["isPrivate"]
    )
    drafts = [item for item in detail.other_items if item.startswith("DraftIssue")]
    issues = len(detail.items) + len(detail.other_items) - len(drafts)
    sections = [o["name"] for o in (detail.fields.get("Section").options if detail.fields.get("Section") else [])]
    field_ids = {f.id: name for name, f in detail.fields.items()}
    shown = sorted({field_ids.get(i, i) for view in detail.views.values() for i in view.visible_ids + view.vertical_group_ids + view.group_ids})
    readme = PROJECT_BLOCK.sub("\n", detail.readme).strip()
    lines = [
        f"Making Project #{detail.number} public: anyone on the internet will see",
        f"  title: {detail.title}",
        f"  short description: {detail.short_description or '(none)'}",
        f"  README: {readme[:400] or '(only the work-accountability block)'}",
        f"  views: {', '.join(v.name for v in sorted(detail.views.values(), key=lambda v: v.position))}",
        f"  section names: {', '.join(sections) or '(none)'}",
        f"  fields shown on views, with each card's values: {', '.join(shown) or '(none)'}",
    ]
    if private_repo:
        lines.append(
            f"  {issues} issue cards, all hidden from outsiders because {repository} is private"
            " (outsiders see that hidden items exist, not their content)"
        )
    else:
        lines.append(f"  {issues} issue cards with their titles and field values ({repository} is public)")
    if drafts:
        lines.append(f"  {len(drafts)} draft issues, fully visible: " + "; ".join(d[len('DraftIssue '):] for d in drafts[:5]))
    return lines


def run_set_visibility(args: argparse.Namespace) -> int:
    target = args.set_visibility
    transport = GhTransport(args.host, args.user)
    if args.project_url:
        match = PROJECT_URL.match(args.project_url)
        if not match:
            raise ReconcileError("--project-url must look like https://github.com/orgs/OWNER/projects/N")
        owner_type = "Organization" if match.group(1) == "orgs" else "User"
        owner_login, number = match.group(2), int(match.group(3))
        summary = load_project_detail(transport, owner_type, owner_login, number, "")
        repo_line = re.search(r"^Repository:\s*(\S+)\s*$", summary.readme, re.MULTILINE)
        if not repo_line:
            raise ReconcileError(f"Project #{number} is not managed by work-accountability")
        repository = repo_line.group(1)
    else:
        if not args.repo or not args.root:
            raise ReconcileError("pass --repo OWNER/REPOSITORY and --root ISSUE, or --project-url")
        repository = args.repo
        owner_r, name_r = repository.split("/", 1)
        root_issue = transport.rest(f"repos/{owner_r}/{name_r}/issues/{args.root}")
        keys = MANAGED_KEY.findall(root_issue.get("body") or "")
        if not keys:
            raise ReconcileError(f"#{args.root} is not a managed issue")
        repo_state, owner_projects = discover_repository(transport, repository)
        marked = [p for p in owner_projects if marked_for(p, args.host, repo_state.id, keys[0])]
        if len(marked) != 1:
            raise ReconcileError(f"found {len(marked)} Projects for {keys[0]}; reconcile the document first")
        number = marked[0].number
    repo, _projects = discover_repository(transport, repository)
    with FileLocks(
        state_root() / "locks",
        (
            f"transport:{args.host}:{transport.login.casefold()}",
            f"repository:{args.host}:{repository.casefold()}",
        ),
    ):
        detail = load_project_detail(transport, repo.owner_type, repo.owner_login, number, repository)
        if not marker_scopes(detail, args.host, repo.id):
            raise ReconcileError(f"Project #{number} is not a work-accountability document Project")
        if detail.closed or superseded_by(detail, args.host, repo.id):
            raise ReconcileError(f"Project #{number} is closed or superseded; change the current document Project instead")
        receipt = {
            "schema": "github-work-accountability/visibility-receipt-v1",
            "project_number": number,
            "project_url": detail.url,
            "previous": live_visibility(detail),
            "visibility": target,
            "applied_mutations": [],
            "verified": False,
        }
        if target == "public" and live_visibility(detail) != "public":
            for line in visibility_preview(transport, repo, repository, detail):
                sys.stderr.write(line + "\n")
            if not args.yes:
                if not sys.stdin.isatty():
                    sys.stderr.write(
                        f"Not changed yet: Project #{number} is still private. Review the preview above, "
                        "then rerun with --yes to make it public.\n"
                    )
                    return 2
                sys.stderr.write("Make it public? [y/N] ")
                if input().strip().casefold() not in {"y", "yes"}:
                    sys.stderr.write(f"Not changed: Project #{number} is still private.\n")
                    return 2
        work = Receipt(repository=repository, actor=transport.login)
        if live_visibility(detail) != target:
            set_visibility(transport, detail, target, work)
        current = load_project_detail(transport, repo.owner_type, repo.owner_login, number, repository)
        if recorded_visibility(current.readme) != target:
            label = f"record Visibility: {target} in Project #{number} README"
            update_project_metadata(transport, current, current.title, with_visibility_record(current.readme, target))
            work.applied_mutations.append(label)
        current = load_project_detail(transport, repo.owner_type, repo.owner_login, number, repository)
        if live_visibility(current) != target or recorded_visibility(current.readme) != target:
            raise ReconcileError(f"GitHub did not show Project #{number} as {target} with its record after the change")
        receipt["applied_mutations"] = work.applied_mutations
        receipt["verified"] = True
        print(canonical_json(receipt))
        return 0


DELIVERED_BLOCK = re.compile(
    r"<!-- work-accountability:delivered:begin -->.*?<!-- work-accountability:delivered:end -->",
    re.DOTALL,
)
PROJECT_NUMBER_IN_URL = re.compile(r"/projects/(\d+)")


def release_dir(host: str, repository: str, tag: str) -> Path:
    owner, repo = repository.split("/", 1)
    return state_root() / "releases" / host / owner / repo / re.sub(r"[^A-Za-z0-9._+-]", "_", tag)


def attribute_commit(facts: GitHubFacts, commit: str | None) -> tuple[str, str | None, str]:
    """('released', tag, why) | ('pending', None, why) | ('unknown', None, why)."""
    if not commit:
        return "unknown", None, "no Integration record (the landing commit was never recorded)"
    branch = facts.default_branch()
    on_branch = facts.contains(branch, commit)
    if on_branch is None:
        return "unknown", None, f"commit {commit[:12]} is unknown to GitHub"
    if not on_branch:
        return "unknown", None, f"commit {commit[:12]} is not on {branch} (a backport or unmerged change)"
    for release in facts.full_releases():
        if facts.contains(release["tag_name"], commit):
            return "released", release["tag_name"], f"first full release containing {commit[:12]}"
    return "pending", None, f"{commit[:12]} is on {branch} but in no full release yet"


def build_parser() -> argparse.ArgumentParser:
    return make_parser()


def namespace(**overrides: Any) -> argparse.Namespace:
    args = build_parser().parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def document_root(
    transport: GhTransport, repo: RepositoryState, number: int, cache: dict[int, ProjectState]
) -> ProjectState:
    if number not in cache:
        cache[number] = load_project_detail(
            transport, repo.owner_type, repo.owner_login, number, repo.name_with_owner
        )
    return cache[number]


def release_members(
    transport: GhTransport, repository: str, milestone_number: int
) -> list[Mapping[str, Any]]:
    owner, name = repository.split("/", 1)
    found: list[Mapping[str, Any]] = []
    page = 1
    while True:
        values = transport.rest(
            f"repos/{owner}/{name}/issues?state=all&milestone={milestone_number}&per_page=100&page={page}"
        )
        found.extend(value for value in values if "pull_request" not in value)
        if len(values) < 100:
            return found
        page += 1


def release_receipt(action: str, tag: str, **extra: Any) -> dict[str, Any]:
    return {"schema": "github-work-accountability/release-receipt-v1", "action": action, "tag": tag, **extra}


def run_release(args: argparse.Namespace) -> int:
    if not args.repo:
        raise ReconcileError("pass --repo OWNER/REPOSITORY")
    transport = GhTransport(args.host, args.user)
    facts = GitHubFacts(transport, args.repo)
    action = args.release
    if action == "attribute":
        if not args.issue:
            raise ReconcileError("awa release attribute needs --issue N")
        owner, name = args.repo.split("/", 1)
        issue = transport.rest(f"repos/{owner}/{name}/issues/{args.issue}")
        record = issue_record(issue.get("body") or "", "Integration")
        status, tag, why = attribute_commit(facts, record.split()[0] if record else None)
        print(canonical_json(release_receipt("attribute", tag or "", issue=args.issue, status=status, reason=why)))
        return 0
    if action != "backfill" and (not args.tag or not RELEASE_TAG.fullmatch(args.tag)):
        raise ReconcileError("pass --tag with the release tag, for example v0.2.10")
    if action == "backfill":
        return release_backfill(transport, facts, args)
    if action == "plan":
        return release_plan(transport, args)
    if action == "status":
        return release_status(transport, facts, args)
    return release_close(transport, facts, args)


def release_plan(transport: GhTransport, args: argparse.Namespace) -> int:
    owner, name = args.repo.split("/", 1)
    milestones = list_milestones(transport, args.repo)
    payload: dict[str, Any] = {}
    description = args.description
    if args.due:
        try:
            datetime.strptime(args.due, "%Y-%m-%d")
        except ValueError as error:
            raise ReconcileError("--due must be a date such as 2026-10-15") from error
        payload["due_on"] = f"{args.due}T00:00:00Z"
        if args.due_source:
            source_line = f"Due date {args.due} agreed: {args.due_source}"
            description = f"{description}\n\n{source_line}" if description else source_line
    elif args.due_source:
        raise ReconcileError("--due-source records where a --due date came from; pass --due too")
    if description:
        payload["description"] = description
    applied: list[str] = []
    existing = milestones.get(args.tag)
    if existing is None:
        created = transport.rest(
            f"repos/{owner}/{name}/milestones", method="POST", data={"title": args.tag, **payload}
        )
        applied.append(f"create release milestone {args.tag}")
        milestone = created
    elif payload and args.update:
        milestone = transport.rest(
            f"repos/{owner}/{name}/milestones/{existing['number']}", method="PATCH", data=payload
        )
        applied.append(f"update release milestone {args.tag}")
    elif payload and any(existing.get(k) != v for k, v in payload.items()):
        raise ReconcileError(
            f"release milestone {args.tag} already exists with a different description or due date; "
            "pass --update to change it"
        )
    else:
        milestone = existing
    fresh = list_milestones(transport, args.repo)[args.tag]
    print(canonical_json(release_receipt(
        "plan", args.tag, applied_mutations=applied, milestone={
            "number": fresh["number"], "state": fresh["state"], "due_on": fresh.get("due_on"),
            "description": fresh.get("description"),
        }, verified=all(fresh.get(k) == v for k, v in payload.items()),
    )))
    return 0


def classify_members(
    transport: GhTransport, facts: GitHubFacts, args: argparse.Namespace, tag: str
) -> tuple[Mapping[str, Any], RepositoryState, dict[int, ManagedIssue], dict[int, list[int]], list[int], list[str]]:
    """Members by document root, `next` stories this release delivers, and problems."""
    milestones = list_milestones(transport, args.repo)
    milestone = milestones.get(tag)
    if milestone is None:
        raise ReconcileError(f"there is no release milestone {tag}; create it with `awa release plan {tag}`")
    managed = list_managed_issues(transport, args.repo)
    repo, _projects = discover_repository(transport, args.repo)
    problems: list[str] = []
    by_document: dict[int, list[int]] = {}
    joining: list[int] = []
    cache: dict[int, ProjectState] = {}

    def place(number: int) -> None:
        issue = managed[number]
        project_url = re.search(r"^Project:\s*(\S+)", managed_block(issue.body), re.MULTILINE)
        match = PROJECT_NUMBER_IN_URL.search(project_url.group(1)) if project_url else None
        if not match:
            problems.append(f"#{number} belongs to no document Project; reconcile its document first")
            return
        board = document_root(transport, repo, int(match.group(1)), cache)
        root = re.search(r"^Root issue:\s*#(\d+)", board.readme, re.MULTILINE)
        if not root:
            problems.append(f"#{number}'s Project #{board.number} is not a document Project")
            return
        by_document.setdefault(int(root.group(1)), []).append(number)

    for raw in release_members(transport, args.repo, milestone["number"]):
        number = int(raw["number"])
        if number not in managed:
            problems.append(f"#{number} is in milestone {tag} but is not a managed work item; remove it or manage it")
            continue
        place(number)
    for number, issue in sorted(managed.items()):
        if issue_record(issue.body, "Delivery") != "release next" or issue.milestone is not None:
            continue
        record = issue_record(issue.body, "Integration")
        status, released_in, why = attribute_commit(facts, record.split()[0] if record else None)
        if status == "released" and released_in == tag:
            joining.append(number)
            place(number)
        elif status == "unknown":
            problems.append(f"#{number} (delivery: next) cannot be attributed: {why}")
    return milestone, repo, managed, by_document, joining, problems


def document_of(
    transport: GhTransport, repo: RepositoryState, issue: ManagedIssue, cache: dict[int, ProjectState]
) -> int | None:
    """The root issue of the document Project an issue's managed block points at."""
    project_url = re.search(r"^Project:\s*(\S+)", managed_block(issue.body), re.MULTILINE)
    match = PROJECT_NUMBER_IN_URL.search(project_url.group(1)) if project_url else None
    if not match:
        return None
    board = document_root(transport, repo, int(match.group(1)), cache)
    root = re.search(r"^Root issue:\s*#(\d+)", board.readme, re.MULTILINE)
    return int(root.group(1)) if root else None


def release_backfill(transport: GhTransport, facts: GitHubFacts, args: argparse.Namespace) -> int:
    """Propose (read-only) or apply accepted release milestones for work that already shipped."""
    managed = list_managed_issues(transport, args.repo)
    if not args.accept:
        proposals: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        for number, issue in sorted(managed.items()):
            if issue.milestone is not None or issue_record(issue.body, "Release"):
                continue
            record = issue_record(issue.body, "Integration")
            if record is None:
                if issue.state == "closed" and issue.state_reason == "completed":
                    unknown.append({"issue": number, "title": issue.title,
                                    "reason": "closed, but no Integration record (landing commit never recorded)"})
                continue
            status, tag, why = attribute_commit(facts, record.split()[0])
            if status == "released":
                proposals.append({"issue": number, "title": issue.title, "release": tag})
            elif status == "unknown":
                unknown.append({"issue": number, "title": issue.title, "reason": why})
        print(canonical_json(release_receipt("backfill", "", proposals=proposals, unknown=unknown, applied_mutations=[])))
        return 0
    try:
        accepted = json.loads(Path(args.accept).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconcileError(f"cannot read --accept {args.accept}: {error}") from error
    rows = accepted.get("proposals", accepted) if isinstance(accepted, dict) else accepted
    if not isinstance(rows, list):
        raise ReconcileError("--accept must hold a list of {issue, release} rows (the proposals output)")
    repo, _projects = discover_repository(transport, args.repo)
    cache: dict[int, ProjectState] = {}
    problems: list[str] = []
    by_document: dict[int, dict[int, str]] = {}
    for row in rows:
        number, tag = row.get("issue"), row.get("release")
        issue = managed.get(number)
        if issue is None or not isinstance(tag, str) or not RELEASE_TAG.fullmatch(tag):
            problems.append(f"row {row!r} is not a managed issue with a release tag")
            continue
        record = issue_record(issue.body, "Integration")
        status, actual, why = attribute_commit(facts, record.split()[0] if record else None)
        if status != "released" or actual != tag:
            problems.append(f"#{number}: accepted {tag}, but it now attributes to {actual or status} ({why})")
            continue
        root = document_of(transport, repo, issue, cache)
        if root is None:
            problems.append(f"#{number} belongs to no document Project")
            continue
        by_document.setdefault(root, {})[number] = tag
    if problems:
        sys.stderr.write("awa release backfill: refused, nothing was written:\n")
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
        return 2
    folder = state_root() / "releases" / args.host / args.repo.replace("/", "_") / "backfill"
    folder.mkdir(parents=True, exist_ok=True)
    documents = []
    for root, assignments in sorted(by_document.items()):
        manifest, _notes = build_draft(transport, namespace(host=args.host, repo=args.repo, root=root))
        for item in manifest["items"]:
            if item["number"] in assignments:
                item["milestone"] = assignments[item["number"]]
        path = folder / f"document-{root}.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        receipt = reconcile(namespace(
            host=args.host, user=args.user, manifest=str(path), apply=True, allow_closed_milestones=True,
        ))
        documents.append({"root": root, "project": receipt.project_number, "verified": receipt.verified})
    owner, name = args.repo.split("/", 1)
    milestones = list_milestones(transport, args.repo)
    closed: list[str] = []
    for tag in sorted({t for a in by_document.values() for t in a.values()}):
        milestone = milestones[tag]
        # GitHub's cached counter can be stale; count what the milestone really holds.
        if milestone.get("state") == "closed" or milestone_counts(transport, args.repo, milestone["number"])["open"]:
            continue
        if facts.release(tag) is None:
            continue
        transport.rest(f"repos/{owner}/{name}/milestones/{milestone['number']}", method="PATCH", data={"state": "closed"})
        closed.append(tag)
    print(canonical_json(release_receipt(
        "backfill", "", documents=documents, closed_milestones=closed,
        applied_mutations=[f"close release milestone {t}" for t in closed],
        verified=all(d["verified"] for d in documents),
    )))
    return 0


def milestone_counts(transport: GhTransport, repository: str, number: int) -> dict[str, int]:
    """Open and closed issues and pull requests actually in a milestone, as GitHub's counter should count them."""
    owner, name = repository.split("/", 1)
    counts = {"open": 0, "closed": 0}
    page = 1
    while True:
        values = transport.rest(
            f"repos/{owner}/{name}/issues?state=all&milestone={number}&per_page=100&page={page}"
        )
        for value in values:
            counts["open" if value.get("state") == "open" else "closed"] += 1
        if len(values) < 100:
            return counts
        page += 1


def counter_note(milestone: Mapping[str, Any], actual: Mapping[str, int]) -> str | None:
    """A sentence when GitHub's cached milestone counter disagrees with the milestone's contents."""
    shown = (milestone.get("open_issues"), milestone.get("closed_issues"))
    if shown == (actual["open"], actual["closed"]):
        return None
    return (
        f"GitHub's counter for milestone {milestone.get('title')} shows {shown[0]} open and {shown[1]} closed, "
        f"but the milestone holds {actual['open']} open and {actual['closed']} closed. GitHub's counter is stale: "
        "GitHub skips it when one request changes both an issue's state and its text, which awa 0.10.6-0.10.8 "
        "did when closing or reopening stories. The milestone's issue list and these figures are correct. "
        "GitHub recounts the milestone the next time an issue joins or leaves it."
    )


def release_status(transport: GhTransport, facts: GitHubFacts, args: argparse.Namespace) -> int:
    milestone, repo, managed, by_document, joining, problems = classify_members(transport, facts, args, args.tag)
    actual = milestone_counts(transport, args.repo, milestone["number"])
    notes = [note] if (note := counter_note(milestone, actual)) else []
    members = []
    for root, numbers in sorted(by_document.items()):
        for number in sorted(numbers):
            issue = managed[number]
            members.append({
                "number": number, "title": issue.title, "state": issue.state, "document_root": root,
                "joins_at_close": number in joining,
            })
    print(canonical_json(release_receipt(
        "status", args.tag, milestone_state=milestone["state"], members=members, problems=problems,
        issues=actual, notes=notes,
    )))
    return 0


def release_close(transport: GhTransport, facts: GitHubFacts, args: argparse.Namespace) -> int:
    tag = args.tag
    move_to = args.move_open_to
    if move_to == tag:
        raise ReconcileError("--move-open-to must name a different release")
    if move_to is not None and not RELEASE_TAG.fullmatch(move_to):
        raise ReconcileError("--move-open-to must be a release tag")
    release = facts.release(tag)
    if release is None:
        raise ReconcileError(f"{tag} has no published GitHub Release yet; publish it, then close the release")
    if release.get("prerelease"):
        raise ReconcileError(f"{tag} is a pre-release; only full releases close a release milestone")
    folder = release_dir(args.host, args.repo, tag)
    ledger_path = folder / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    if ledger is None:
        milestone, repo, managed, by_document, joining, problems = classify_members(transport, facts, args, tag)
        delivered: list[int] = []
        moved: list[int] = []
        manifests: dict[str, str] = {}
        folder.mkdir(parents=True, exist_ok=True)
        for root, numbers in sorted(by_document.items()):
            manifest, _notes = build_draft(transport, namespace(host=args.host, repo=args.repo, root=root))
            items = {item["number"]: item for item in manifest["items"]}
            for number in sorted(numbers):
                item = items.get(number)
                if item is None or item["kind"] != "story":
                    continue
                if number in joining:
                    item["milestone"] = tag
                delivery = item.get("delivery") or {}
                evidence = item.setdefault("evidence", {})
                if item["work_phase"] == "Done":
                    delivered.append(number)
                    continue
                if item["work_phase"] == "Release ready" and delivery.get("kind") == "release":
                    candidate = evidence.get("candidate")
                    integration = evidence.get("integration")
                    if not candidate or not integration:
                        problems.append(
                            f"#{number} is Release ready but this machine has no candidate/integration "
                            "evidence for it; run from the machine that applied it, or pass --base"
                        )
                        continue
                    if not facts.contains(tag, integration.get("commit") or ""):
                        problems.append(
                            f"#{number}: {tag} does not contain its landing commit "
                            f"{(integration.get('commit') or '')[:12]}"
                            + (f" (on {integration['branch']}; merge that branch first)" if integration.get("branch") else "")
                        )
                        continue
                    evidence["delivery"] = {
                        "ref": release["html_url"],
                        "work_key": item["work_key"],
                        "requirement": integration["requirement"],
                        "candidate": candidate["ref"],
                        "release": tag,
                    }
                    item["work_phase"] = "Done"
                    delivered.append(number)
                elif move_to:
                    item["milestone"] = move_to
                    if delivery.get("kind") == "release":
                        delivery["release"] = move_to
                    item["milestone_change_reason"] = f"not finished when {tag} was released; moved to {move_to}"
                    moved.append(number)
                else:
                    problems.append(f"#{number} is {item['work_phase']}, not Release ready or Done")
            path = folder / f"document-{root}.json"
            path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            try:
                load_manifest(path)
            except ReconcileError as error:
                problems.append(f"document #{root}: {error}")
            manifests[str(root)] = str(path)
        if problems:
            sys.stderr.write(
                f"awa release close {tag}: refused, nothing was written. Fix these, or rerun with "
                "--move-open-to TAG to move unfinished stories to another release:\n"
            )
            for problem in problems:
                sys.stderr.write(f"  - {problem}\n")
            for path in folder.glob("document-*.json"):
                path.unlink()
            return 2
        changing = len(delivered) + 3 * len(moved) + 10 * len(manifests) + 3
        try:
            check_write_budget(transport, changing, f"closing {tag}")
        except TemporaryFailure:
            for path in folder.glob("document-*.json"):
                path.unlink()
            raise
        ledger = {
            "tag": tag, "release_id": release["id"], "release_url": release["html_url"],
            "milestone_number": milestone["number"], "documents": manifests,
            "applied": [], "delivered": delivered, "moved": moved,
            "notes": False, "milestone_closed": False,
        }
        ledger_path.write_text(canonical_json(ledger) + "\n", encoding="utf-8")
    else:
        # Only documents not yet applied still need writes.
        pending = [root for root in ledger["documents"] if root not in ledger["applied"]]
        members: set[int] = set()
        for root in pending:
            manifest = json.loads(Path(ledger["documents"][root]).read_text(encoding="utf-8"))
            members.update(item["number"] for item in manifest.get("items") or [])
        check_write_budget(
            transport,
            len(members & set(ledger["delivered"])) + 3 * len(members & set(ledger["moved"])) + 10 * len(pending) + 3,
            f"resuming the close of {tag}",
        )
    documents = []
    for index, (root, path) in enumerate(sorted(ledger["documents"].items()), 1):
        sys.stderr.write(f"awa: closing {tag}: document {index}/{len(ledger['documents'])} (#{root})\n")
        try:
            receipt = reconcile(namespace(host=args.host, user=args.user, manifest=path, apply=True))
        except ReconcileError as error:
            sys.stderr.write(
                f"awa release close {tag}: stopped at document #{root}: {error}\n"
                f"  already applied: {', '.join('#' + r for r in ledger['applied']) or 'none'}. "
                "Fix the problem and rerun the same command; it resumes from here.\n"
            )
            return EXIT_TEMPORARY if isinstance(error, TemporaryFailure) else 2
        if root not in ledger["applied"]:
            ledger["applied"].append(root)
            ledger_path.write_text(canonical_json(ledger) + "\n", encoding="utf-8")
        documents.append({"root": int(root), "project": receipt.project_number, "verified": receipt.verified})
    owner, name = args.repo.split("/", 1)
    try:
        check_write_budget(transport, 2, f"finishing the close of {tag}")
    except TemporaryFailure as error:
        sys.stderr.write(
            f"awa release close {tag}: every document is applied, but adding the release notes and closing "
            f"the milestone must wait: {str(error).removesuffix(' Nothing was written in this run.')}. "
            "Rerun the same command then; it finishes from here.\n"
        )
        return EXIT_TEMPORARY
    managed = list_managed_issues(transport, args.repo)
    lines = [f"- #{n} {managed[n].title}" for n in sorted(ledger["delivered"]) if n in managed]
    current = transport.rest(f"repos/{owner}/{name}/releases/{ledger['release_id']}")
    body = current.get("body") or ""
    block = (
        "<!-- work-accountability:delivered:begin -->\n## Work items delivered\n\n"
        + ("\n".join(lines) or "- (none)")
        + "\n<!-- work-accountability:delivered:end -->"
    )
    new_body = DELIVERED_BLOCK.sub(block, body) if DELIVERED_BLOCK.search(body) else (body.rstrip() + "\n\n" + block).lstrip()
    applied: list[str] = []
    if new_body != body:
        updated = transport.rest(
            f"repos/{owner}/{name}/releases/{ledger['release_id']}", method="PATCH", data={"body": new_body}
        )
        if (updated.get("body") or "") != new_body:
            raise ReconcileError(f"GitHub did not keep the delivered-work section in the {tag} release notes")
        applied.append(f"add delivered work items to the {tag} release notes")
    ledger["notes"] = True
    milestone = transport.rest(f"repos/{owner}/{name}/milestones/{ledger['milestone_number']}")
    if milestone.get("state") != "closed":
        milestone = transport.rest(
            f"repos/{owner}/{name}/milestones/{ledger['milestone_number']}", method="PATCH", data={"state": "closed"}
        )
        applied.append(f"close release milestone {tag}")
    if milestone.get("state") != "closed":
        raise ReconcileError(f"GitHub did not show release milestone {tag} closed")
    ledger["milestone_closed"] = True
    ledger_path.write_text(canonical_json(ledger) + "\n", encoding="utf-8")
    print(canonical_json(release_receipt(
        "close", tag, release_url=ledger["release_url"], documents=documents,
        delivered=sorted(ledger["delivered"]), moved=sorted(ledger["moved"]),
        applied_mutations=applied, verified=all(d["verified"] for d in documents),
    )))
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


EVIDENCE_MARKER = re.compile(r"<!--\s*work-accountability:([A-Za-z0-9_-]+)\s*(.*?)\s*-->", re.DOTALL)


def parse_marker(kind: str, text: str) -> dict[str, Any]:
    """One marker, in either form.

    New form: `<!-- work-accountability:event key=K event=verdict actor=A time=T -->`.
    Old forms carry only an ID after the kind, for example
    `<!-- work-accountability:event 2026-09-25-batch3 -->` or `work-accountability:attempt-start ID`.
    """
    fields = dict(re.findall(r"([A-Za-z_][A-Za-z0-9_-]*)=(\S+)", text))
    marker: dict[str, Any] = {"kind": kind, "form": "key=value" if fields else "id"}
    if fields:
        marker["fields"] = fields
    else:
        marker["id"] = text
    return marker


def show_evidence(args: argparse.Namespace) -> int:
    """List an issue's awa records and every comment carrying a work-accountability marker (read-only)."""
    if not args.repo or "/" not in args.repo:
        raise ReconcileError("--evidence needs --repo OWNER/REPOSITORY")
    transport = GhTransport(args.host, args.user)
    owner, name = args.repo.split("/", 1)
    issue = transport.rest(f"repos/{owner}/{name}/issues/{args.evidence}")
    body = issue.get("body") or ""
    key_match = MANAGED_KEY.search(body)
    work_key = key_match.group(1) if key_match else None
    records = {
        record: value
        for record in ("Project", "Planned delivery", *RECORD_NAMES)
        if (value := issue_record(body, record)) is not None
    }
    markers: list[dict[str, Any]] = []
    page = 1
    while True:
        comments = transport.rest(f"repos/{owner}/{name}/issues/{args.evidence}/comments?per_page=100&page={page}")
        for comment in comments:
            text = comment.get("body") or ""
            found = [parse_marker(kind, rest) for kind, rest in EVIDENCE_MARKER.findall(text)]
            if not found:
                continue
            first = next((line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("<!--")), "")
            markers.append({
                "url": comment.get("html_url"),
                "author": (comment.get("user") or {}).get("login"),
                "created_at": comment.get("created_at"),
                "markers": found,
                "summary": first if len(first) <= 160 else first[:159] + "…",
            })
        if len(comments) < 100:
            break
        page += 1
    recorded: dict[str, Any] = {}
    if work_key:
        recorded, _priority, _title = load_base_manifests(remembered_manifests(args.host, args.repo))
    report = {
        "issue": args.evidence,
        "url": issue.get("html_url"),
        "state": issue.get("state"),
        "state_reason": issue.get("state_reason"),
        "milestone": (issue.get("milestone") or {}).get("title"),
        "work_key": work_key,
        "records": records,
        "marker_comments": markers,
        "recorded_evidence": recorded.get(work_key, {}) if work_key else {},
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    lines = [f"#{args.evidence} {issue.get('title')} ({issue.get('state')}"
             + (f" as {issue.get('state_reason')}" if issue.get("state") == "closed" and issue.get("state_reason") else "")
             + f", milestone {report['milestone'] or 'none'})",
             f"  work key: {work_key or '(not managed by awa)'}"]
    lines.append("  records in the managed block:" if records else "  records in the managed block: none")
    lines += [f"    {record}: {value}" for record, value in records.items()]
    lines.append(f"  comments with work-accountability markers: {len(markers)}")
    for entry in markers:
        for marker in entry["markers"]:
            if marker["form"] == "key=value":
                detail = " ".join(f"{k}={v}" for k, v in marker["fields"].items() if k != "key")
            else:
                detail = f"{marker['id']} (old form: no key= or event=)"
            lines.append(f"    {entry['created_at']} @{entry['author']} {marker['kind']} {detail}".rstrip())
        lines.append(f"      {entry['url']}")
        if entry["summary"]:
            lines.append(f"      {entry['summary']}")
    evidence = report["recorded_evidence"]
    lines.append("  evidence in manifests this machine applied:" if evidence else
                 "  evidence in manifests this machine applied: none")
    for name, value in evidence.items():
        lines.append(f"    {name}: {value.get('ref')}")
    print("\n".join(lines))
    return 0


def run(args: argparse.Namespace) -> int:
    if args.diagnose:
        print(canonical_json(diagnose(args.host, args.user)))
        return 0
    if args.draft:
        return run_draft(args)
    if args.set_visibility:
        return run_set_visibility(args)
    if args.release:
        return run_release(args)
    if args.evidence:
        return show_evidence(args)
    print_receipt(reconcile(args))
    return 0


def reconcile(args: argparse.Namespace) -> Receipt:
    """Reconcile one manifest (dry run unless args.apply) and return its receipt."""
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
        expected_visibility = "private" if fresh or detail is None else plan_visibility(manifest, detail)
        receipt.visibility = expected_visibility
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
        if project is None:
            root_title = issues[manifest.root_number].title
            if RELEASE_NAMED_ROOT.match(root_title):
                raise ReconcileError(
                    f"#{manifest.root_number} ({root_title!r}) looks like a release, not a planning document; "
                    "track releases with release milestones (`awa release plan`), not a Project. Nothing was written."
                )
        facts = GitHubFacts(transport, manifest.repository)
        issue_records = check_delivery(facts, manifest, issues)
        milestone_changes, release_records = plan_milestones(manifest, issues)
        for number, rec in release_records.items():
            issue_records.setdefault(number, {}).update(rec)
        stories = sorted(item.number for item in manifest.items if item.kind == "story")
        dependency_plan = plan_dependencies(
            manifest, issues, all_issues, read_blockers(transport, manifest.repository, stories), targets, receipt
        )
        for number, rec in dependency_plan.records.items():
            issue_records.setdefault(number, {}).update(rec)

        plan = receipt if not args.apply else Receipt()
        for parent, child in missing_edges:
            plan.planned_mutations.append(f"attach #{child} under #{parent}")
        plan_dry_run(
            manifest, repo, project, detail, issues, all_issues, targets, sources, args, plan,
            issue_records, milestone_changes,
        )
        plan.planned_mutations.extend(f"block #{i} by #{b}" for i, b in dependency_plan.add)
        plan.planned_mutations.extend(f"remove #{b} as a blocker of #{i}" for i, b in dependency_plan.remove)
        if project is not None:
            post_status(transport, project, repo, manifest, targets, plan, apply=False)
        else:
            plan.planned_mutations.append(f"post Project status {document_status(manifest, targets)[0]}")
        # Writes with no plan line: release-move reason comments, new release milestones,
        # and a new board's field values (planned as one line).
        extra = sum(1 for change in milestone_changes if change.reason)
        if milestone_changes:
            known = list_milestones(transport, manifest.repository)
            extra += len({c.after for c in milestone_changes if c.after is not None and c.after not in known})
        if project is None:
            extra += -(-sum(1 for values in targets.values() for v in values.values() if v is not None) // 25)
        receipt.estimated_writes = estimate_writes(plan.planned_mutations, extra)
        if not args.apply:
            if receipt.planned_mutations:
                receipt.notes.append("write budget: " + write_budget(transport, receipt.estimated_writes, "applying this")[1])
            complete_receipt_metrics(receipt, transport, used_at_start)
            return receipt
        if plan.planned_mutations:
            check_write_budget(transport, receipt.estimated_writes, "this run")

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
            project = set_visibility(transport, project, "private", receipt)
        receipt.project_number = project.number
        receipt.project_url = project.url
        if sources.pending or sources.done:
            receipt.migration_id = migration_id(manifest, project.id, receipt)
            append_journal(root, {"event": "migration", "id": receipt.migration_id, "key": key})
        current = load_detail(transport, repo, manifest, project.number)
        if fresh and live_visibility(current) != "private":
            # Created by an earlier run that stopped before making it private:
            # make it private before it gets its real title or any content.
            set_visibility(transport, current, "private", receipt)
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
            "private" if fresh else None,
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
        ensure_fields(transport, project, manifest, receipt, manifest.section_progress(targets))
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

        apply_milestones(
            transport, manifest, milestone_changes, receipt,
            allow_closed=getattr(args, "allow_closed_milestones", False),
        )
        apply_dependencies(transport, manifest, all_issues, dependency_plan, receipt)

        wanted_section = section_filter(manifest.repository, manifest.root_number)
        view_plan = plan_views(project, args.repair_lifecycle, fresh, wanted_section)
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
        release_number = view_plan.release
        if view_plan.create_release:
            receipt.planned_mutations.append("create By release table")
            release_number = create_view(transport, project, repo, manifest, RELEASE_VIEW)
            receipt.applied_mutations.append("create By release table")
        for view, wanted, role in view_plan.refilter:
            label = refilter_label(view, role)
            receipt.planned_mutations.append(label)
            mutate_one(
                transport,
                "updateProjectV2View",
                "UpdateProjectV2ViewInput",
                {"viewId": view.id, "filter": wanted, "clientMutationId": f"work-accountability:{view.id}:filter"},
                "projectV2View { id filter }",
            )
            receipt.applied_mutations.append(label)
        assert lifecycle_number is not None and section_number is not None and release_number is not None
        pending_initial = read_initial_views(project.readme)
        recorded_readme = managed_readme(
            project.readme, args.host, repo, manifest, lifecycle_number, section_number, pending_initial,
            release_view=release_number,
        )
        if recorded_readme != project.readme:
            project = update_project_metadata(transport, project, manifest.project_title, recorded_readme)
            receipt.applied_mutations.append("record managed views in Project README")
        if view_plan.delete:
            delete_views(transport, project, view_plan.delete, "superseded or initial", receipt)
        if pending_initial:
            final_readme = managed_readme(
                project.readme, args.host, repo, manifest, lifecycle_number, section_number,
                release_view=release_number,
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
                and release_number in state.views
                and lifecycle_view_valid(state.views[lifecycle_number], state.fields)
                and section_view_valid(state.views[section_number], state.fields, wanted_section)
                and release_view_valid(state.views[release_number], state.fields)
                and not (deleted & set(state.views))
            ),
            "the Lifecycle board and the By section and By release tables",
        )
        receipt.lifecycle_view = lifecycle_number
        receipt.section_view = section_number
        receipt.release_view = release_number
        receipt.lifecycle_url = f"{project.url}/views/{lifecycle_number}"
        receipt.section_url = f"{project.url}/views/{section_number}"
        receipt.unmanaged_items = list(project.other_items)
        verify_final(
            transport, manifest, repo, project, issues, all_issues, targets,
            lifecycle_number, section_number, expected_visibility,
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
        write_issues(
            transport, manifest, issues, receipt.lifecycle_url, receipt, issue_records,
            plan_issue_states(manifest, issues, targets, receipt),
        )
        post_status(transport, project, repo, manifest, targets, receipt, apply=True)
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
        return receipt


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
    issue_records: Mapping[int, Mapping[str, str | None]] | None = None,
    milestone_changes: Sequence[MilestoneChange] = (),
) -> None:
    for change in milestone_changes:
        receipt.planned_mutations.append(f"set #{change.number} release milestone {change.after or '(none)'}")
    for change in plan_issue_states(manifest, issues, targets, receipt):
        receipt.planned_mutations.extend(describe_issue_state(change))
    if project is None or detail is None:
        receipt.planned_mutations.append("create document Project linked to repository")
        receipt.planned_mutations.extend(f"create field {name}" for name in expected_field_schema(manifest))
        receipt.planned_mutations.extend(f"add issue #{number}" for number in sorted(issues))
        receipt.planned_mutations.append("set field values and epic rollups")
        receipt.planned_mutations.append("create Lifecycle Work phase board")
        receipt.planned_mutations.append("create By section table")
        receipt.planned_mutations.append("create By release table")
        lifecycle_url = None
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
                _payload, changes = planned_options(manifest, name, current, options, manifest.section_progress(targets))
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
        lifecycle_url = None
        # Adding options to an existing field leaves views alone, so they can be planned now.
        if all(name in detail.fields for name in expected_field_schema(manifest)):
            view_plan = plan_views(
                detail, args.repair_lifecycle, False, section_filter(manifest.repository, manifest.root_number)
            )
            receipt.notes.extend(view_plan.notes)
            receipt.planned_mutations.extend(refilter_label(view, role) for view, _, role in view_plan.refilter)
            if view_plan.create_lifecycle:
                receipt.planned_mutations.append("create Lifecycle Work phase board")
            if view_plan.create_section:
                receipt.planned_mutations.append("create By section table")
            if view_plan.create_release:
                receipt.planned_mutations.append("create By release table")
            receipt.planned_mutations.extend(
                f"delete view #{view.number} {view.name!r}" for view in view_plan.delete
            )
            if not (view_plan.create_lifecycle or view_plan.create_section or view_plan.create_release or view_plan.delete):
                lifecycle_url = f"{project.url}/views/{view_plan.lifecycle}"
                desired_readme = managed_readme(
                    detail.readme, args.host, repo, manifest,
                    view_plan.lifecycle, view_plan.section,
                )
                if (
                    detail.title != manifest.project_title
                    or detail.readme != desired_readme
                    or detail.closed
                ):
                    receipt.planned_mutations.append("update Project title/managed README block")
        else:
            receipt.planned_mutations.append("create or repair managed views after fields exist")
    if project is None or detail is None:
        receipt.planned_mutations.append("make the new Project private")
    # The same comparison write_issues() makes, without writing.
    for number in sorted(issues):
        if lifecycle_url is None:
            receipt.planned_mutations.append(f"bind issue #{number} to the new Lifecycle board")
            continue
        body, labels = issue_project_projection(
            issues[number], lifecycle_url, (issue_records or {}).get(number)
        )
        if body != issues[number].body or labels != issues[number].labels:
            receipt.planned_mutations.append(f"bind issue #{number} to Project fields")
    for number in sorted(sources.pending):
        receipt.planned_mutations.append(f"snapshot and close superseded Project #{number}")


def make_parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--set-visibility",
        choices=VISIBILITIES,
        help="Make a document Project public or private (previews public first; needs --yes off a terminal)",
    )
    parser.add_argument("--project-url", help="For --set-visibility: the Project's URL")
    parser.add_argument("--yes", action="store_true", help="For --set-visibility: confirm going public")
    parser.add_argument("--repo", help="OWNER/REPOSITORY for --draft or --set-visibility")
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
    parser.add_argument("--release", choices=("plan", "status", "attribute", "close", "backfill"), help="Release milestone commands")
    parser.add_argument("--accept", help="For --release backfill: the proposals file to apply")
    parser.add_argument("--tag", help="For --release: the release tag, e.g. v0.2.10")
    parser.add_argument("--description", help="For --release plan: the milestone description")
    parser.add_argument("--due", help="For --release plan: due date YYYY-MM-DD, only when one was agreed")
    parser.add_argument("--due-source", help="For --release plan: who agreed the due date, and when")
    parser.add_argument("--update", action="store_true", help="For --release plan: change an existing milestone")
    parser.add_argument("--issue", type=int, help="For --release attribute: the issue number")
    parser.add_argument("--move-open-to", help="For --release close: move unfinished stories to this release")
    parser.add_argument("--evidence", type=int, help="List this issue's awa records and evidence marker comments (read-only)")
    parser.add_argument("--json", action="store_true", help="For --evidence: print JSON")
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    if not (args.diagnose or args.draft or args.set_visibility or args.release or args.evidence or args.manifest):
        parser.error("--manifest is required unless --diagnose, --draft, --set-visibility, --release or --evidence is used")
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
