#!/usr/bin/env python3
"""Reconcile one epic and its stories into a repository-linked GitHub Project.

The command is intentionally state-oriented: it inventories GitHub, computes a
delta, applies only that delta, and reads the result back.  It never infers work
phase from GitHub activity; callers provide an evidence-bound desired state.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, field
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


SCHEMA = "github-work-accountability/project-v3"
SKILL_VERSION = "0.6.0"
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


@dataclass(frozen=True)
class DesiredItem:
    number: int
    work_key: str
    kind: str
    work_phase: str | None
    health: str
    source_freshness: str
    priority: str | None
    rank: float | None
    evidence: Mapping[str, Evidence]


@dataclass(frozen=True)
class Manifest:
    repository: str
    epic_number: int
    epic_work_key: str
    project_owner: str
    project_title: str
    priority_options: tuple[str, ...]
    lifecycle_only: bool
    items: tuple[DesiredItem, ...]
    digest: str
    raw: Mapping[str, Any]


@dataclass
class ManagedIssue:
    number: int
    node_id: str
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
    schema: str = "github-work-accountability/receipt-v1"
    skill_version: str = SKILL_VERSION
    repository: str = ""
    manifest_digest: str = ""
    actor: str = ""
    project_number: int | None = None
    project_url: str | None = None
    lifecycle_view: int | None = None
    lifecycle_url: str | None = None
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


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconcileError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ReconcileError(f"manifest schema must be {SCHEMA!r}")
    repository = require_string(raw.get("repository"), "repository")
    if repository.count("/") != 1:
        raise ReconcileError("repository must be OWNER/REPOSITORY")
    repo_owner, repo_name = repository.split("/", 1)
    scope = raw.get("scope")
    if not isinstance(scope, dict):
        raise ReconcileError("scope must be an object")
    epic_number = scope.get("epic_number")
    if not isinstance(epic_number, int) or epic_number <= 0:
        raise ReconcileError("scope.epic_number must be a positive integer")
    epic_work_key = require_string(scope.get("epic_work_key"), "scope.epic_work_key")
    if not epic_work_key.startswith(f"{repository}:"):
        raise ReconcileError("scope.epic_work_key must be qualified by repository")
    project = raw.get("project") or {}
    if not isinstance(project, dict):
        raise ReconcileError("project must be an object")
    project_owner = require_string(project.get("owner", repo_owner), "project.owner")
    project_title = require_string(
        project.get("title", f"{repo_name} — {epic_work_key.rsplit(':', 1)[-1]}"),
        "project.title",
    )
    lifecycle_only = project.get("lifecycle_only")
    if not isinstance(lifecycle_only, bool):
        raise ReconcileError("project.lifecycle_only must be true or false")
    raw_priority = project.get("priority_options", ["High", "Medium", "Low"])
    if not isinstance(raw_priority, list) or not raw_priority:
        raise ReconcileError("project.priority_options must be a non-empty array")
    priority_options = tuple(require_string(v, "project.priority_options[]") for v in raw_priority)
    if len({v.casefold() for v in priority_options}) != len(priority_options):
        raise ReconcileError("project.priority_options contains duplicates")

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
        if not isinstance(number, int) or number <= 0:
            raise ReconcileError(f"{prefix}.number must be a positive integer")
        work_key = require_string(value.get("work_key"), f"{prefix}.work_key")
        if not work_key.startswith(f"{repository}:"):
            raise ReconcileError(f"{prefix}.work_key is not qualified by {repository}:")
        kind = value.get("kind", "story")
        if kind not in {"story", "epic"}:
            raise ReconcileError(f"{prefix}.kind must be 'story' or 'epic'")
        phase = value.get("work_phase")
        if phase is not None and phase not in PHASES:
            raise ReconcileError(f"{prefix}.work_phase is not a recognized Work phase")
        if kind == "epic" and phase is not None:
            raise ReconcileError(f"{prefix}: epic Work phase must be omitted; epics are rollups")
        health = value.get("health", "On track")
        freshness = value.get("source_freshness", "Current")
        if health not in HEALTH:
            raise ReconcileError(f"{prefix}.health is not recognized")
        if freshness not in FRESHNESS:
            raise ReconcileError(f"{prefix}.source_freshness is not recognized")
        priority = value.get("priority")
        if priority is not None and priority not in priority_options:
            raise ReconcileError(f"{prefix}.priority is absent from project.priority_options")
        rank = value.get("rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, (int, float))):
            raise ReconcileError(f"{prefix}.rank must be numeric")
        raw_evidence = value.get("evidence") or {}
        if not isinstance(raw_evidence, dict):
            raise ReconcileError(f"{prefix}.evidence must be an object")
        evidence = {
            name: parse_evidence(item, f"{prefix}.evidence.{name}", work_key)
            for name, item in raw_evidence.items()
        }
        if phase in REQUIRED_EVIDENCE:
            missing = [name for name in REQUIRED_EVIDENCE[phase] if name not in evidence]
            if missing:
                raise ReconcileError(
                    f"{prefix}: {phase} requires evidence: {', '.join(missing)}"
                )
            if "attempt" in REQUIRED_EVIDENCE[phase]:
                validate_attempt_evidence(
                    evidence["attempt"],
                    f"{prefix}.evidence.attempt",
                    phase,
                    repository,
                    number,
                )
                attempt = evidence["attempt"]
                assert attempt.attempt_id is not None
                if attempt.attempt_id in attempt_ids:
                    raise ReconcileError(
                        f"{prefix}.evidence.attempt.attempt_id is reused across stories"
                    )
                if attempt.ref in attempt_refs:
                    raise ReconcileError(
                        f"{prefix}.evidence.attempt.ref is reused across stories"
                    )
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
                work_phase=phase,
                health=health,
                source_freshness=freshness,
                priority=priority,
                rank=float(rank) if rank is not None else None,
                evidence=evidence,
            )
        )
    scoped_epic = [
        item
        for item in items
        if item.number == epic_number and item.work_key == epic_work_key and item.kind == "epic"
    ]
    if len(scoped_epic) != 1:
        raise ReconcileError(
            "items must contain exactly one epic matching scope.epic_number and scope.epic_work_key"
        )
    return Manifest(
        repository=repository,
        epic_number=epic_number,
        epic_work_key=epic_work_key,
        project_owner=project_owner,
        project_title=project_title,
        priority_options=priority_options,
        lifecycle_only=lifecycle_only,
        items=tuple(items),
        digest=hashlib.sha256(canonical_json(raw).encode()).hexdigest(),
        raw=raw,
    )


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
        now = self.monotonic()
        remaining = 1.0 - (now - self.last_mutation)
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


def create_intent_path(root: Path, epic_work_key: str) -> Path:
    scope = hashlib.sha256(epic_work_key.encode()).hexdigest()[:16]
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
    result = transport.rest("rate_limit")
    return ((result or {}).get("resources") or {}).get("graphql") or {}


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


def list_epic_tree(
    transport: GhTransport, repository: str, epic_number: int
) -> set[int]:
    """Return one epic and its direct native child stories."""
    owner, repo = repository.split("/", 1)
    discovered = {epic_number}
    page = 1
    while True:
        values = transport.rest(
            f"repos/{owner}/{repo}/issues/{epic_number}/sub_issues?per_page=100&page={page}"
        )
        if not isinstance(values, list):
            raise ReconcileError(f"sub-issue inventory for #{epic_number} was not an array")
        for raw in values:
            number = raw.get("number")
            if not isinstance(number, int) or number <= 0:
                raise ReconcileError(
                    f"sub-issue inventory for #{epic_number} omitted an issue number"
                )
            summary = raw.get("sub_issues_summary")
            if isinstance(summary, dict) and int(summary.get("total") or 0) > 0:
                raise ReconcileError(
                    f"child #{number} has its own sub-issues; create a separate epic Project "
                    "or flatten the stories before reconciliation"
                )
            discovered.add(number)
        if len(values) < 100:
            break
        page += 1
    return discovered


def validate_manifest_against_issues(
    manifest: Manifest,
    issues: Mapping[int, ManagedIssue],
    epic_tree: set[int],
) -> dict[int, ManagedIssue]:
    desired = {item.number: item for item in manifest.items}
    for number, item in desired.items():
        issue = issues.get(number)
        if not issue:
            raise ReconcileError(f"manifest issue #{number} is not a managed issue in {manifest.repository}")
        if issue.work_key != item.work_key:
            raise ReconcileError(
                f"issue #{number} has work key {issue.work_key}, manifest requested {item.work_key}"
            )
    omitted = sorted(epic_tree - set(desired))
    extra = sorted(set(desired) - epic_tree)
    unmanaged = sorted(epic_tree - set(issues))
    if unmanaged:
        raise ReconcileError(
            "epic tree contains issues without work-accountability identities: "
            + ", ".join(f"#{number}" for number in unmanaged)
        )
    if omitted:
        raise ReconcileError(
            "epic manifest omits native child stories: "
            + ", ".join(f"#{number}" for number in omitted)
        )
    if extra:
        raise ReconcileError(
            "epic manifest includes issues outside its native sub-issue tree: "
            + ", ".join(f"#{number}" for number in extra)
        )
    return {number: issues[number] for number in sorted(epic_tree)}


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


def plan_issue_projection(
    issues: Mapping[int, ManagedIssue],
    project_url: str,
    receipt: Receipt,
) -> None:
    for issue in issues.values():
        body, labels = issue_project_projection(issue, project_url)
        if body != issue.body or labels != issue.labels:
            receipt.planned_mutations.append(
                f"bind issue #{issue.number} to Project fields"
            )


def ensure_issue_projection(
    transport: GhTransport,
    repository: str,
    issues: Mapping[int, ManagedIssue],
    project_url: str,
    receipt: Receipt,
) -> None:
    owner, repo = repository.split("/", 1)
    for issue in issues.values():
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


def marked_for(
    project: ProjectState, host: str, repository_id: str, epic_work_key: str
) -> bool:
    for identity, scope in PROJECT_MARKER.findall(project.readme):
        if identity == f"{host}:{repository_id}" and scope == epic_work_key:
            return True
    return False


def select_project(
    projects: Sequence[ProjectState],
    repo: RepositoryState,
    host: str,
    epic_work_key: str,
    adopt_number: int | None,
    intent: Mapping[str, Any] | None,
    actor: str,
) -> ProjectState | None:
    marked = [
        project
        for project in projects
        if marked_for(project, host, repo.id, epic_work_key)
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
            scope
            for identity, scope in PROJECT_MARKER.findall(matches[0].readme)
            if identity == f"{host}:{repo.id}" and scope != epic_work_key
        ]
        if foreign_scopes:
            raise ReconcileError(
                f"Project {adopt_number} is already managed for epic {foreign_scopes[0]}"
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


def managed_readme(
    current: str,
    host: str,
    repo: RepositoryState,
    manifest: Manifest,
    lifecycle_view: int | None,
) -> str:
    block = [
        "<!-- work-accountability:begin-project -->",
        f"<!-- work-accountability:project-v2 {host}:{repo.id} {manifest.epic_work_key} -->",
        f"Repository: {repo.name_with_owner}",
        f"Epic issue: #{manifest.epic_number}",
        f"Work accountability skill: {SKILL_VERSION}",
    ]
    if lifecycle_view is not None:
        block.append(f"Lifecycle view: {lifecycle_view}")
    block.append("<!-- work-accountability:end-project -->")
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
        "schema": "github-work-accountability/create-intent-v2",
        "repository_id": repo.id,
        "repository": repo.name_with_owner,
        "epic_work_key": manifest.epic_work_key,
        "epic_number": manifest.epic_number,
        "owner": manifest.project_owner,
        "title": manifest.project_title,
        "actor": transport.login,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    create_intent_path(root, manifest.epic_work_key).write_text(
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
                verticalGroupByFields(first:10) {{ nodes {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} }} }}
                sortByFields(first:10) {{ nodes {{ direction field {{ ... on ProjectV2Field {{ id name }} ... on ProjectV2SingleSelectField {{ id name }} }} }} }}
              }}
              pageInfo {{ hasNextPage }}
            }}
            items(first:100) {{
              nodes {{
                id isArchived
                content {{ ... on Issue {{ id number repository {{ nameWithOwner }} }} }}
                fieldValues(first:100) {{
                  nodes {{
                    __typename
                    ... on ProjectV2ItemFieldSingleSelectValue {{ name optionId field {{ ... on ProjectV2SingleSelectField {{ id name }} }} }}
                    ... on ProjectV2ItemFieldNumberValue {{ number field {{ ... on ProjectV2Field {{ id name }} }} }}
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
    for raw_view in views["nodes"]:
        vertical = [node["id"] for node in raw_view["verticalGroupByFields"]["nodes"]]
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
                  id isArchived content {{ ... on Issue {{ id number repository {{ nameWithOwner }} }} }}
                  fieldValues(first:100) {{
                    nodes {{
                      __typename
                      ... on ProjectV2ItemFieldSingleSelectValue {{ name optionId field {{ ... on ProjectV2SingleSelectField {{ id name }} }} }}
                      ... on ProjectV2ItemFieldNumberValue {{ number field {{ ... on ProjectV2Field {{ id name }} }} }}
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
        content = raw.get("content")
        if not content or not content.get("number") or not content.get("repository"):
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
        number = int(content["number"])
        item_repository = content["repository"]["nameWithOwner"]
        if item_repository != repository:
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


def ensure_fields(
    transport: GhTransport, project: ProjectState, manifest: Manifest, receipt: Receipt
) -> None:
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
                payload["singleSelectOptions"] = field_option_payload(options)
            mutate_one(
                transport,
                "createProjectV2Field",
                "CreateProjectV2FieldInput",
                payload,
                "projectV2Field { __typename ... on ProjectV2Field { id name } ... on ProjectV2SingleSelectField { id name } }",
            )
            receipt.applied_mutations.append(f"create field {name}")
            continue
        if not options:
            continue
        existing = {option["name"].casefold(): option for option in current.options}
        missing = [option for option in options if option.casefold() not in existing]
        if not missing:
            continue
        receipt.planned_mutations.append(f"add options to field {name}: {', '.join(missing)}")
        preserved = [
            {
                "id": option["id"],
                "name": option["name"],
                "color": option.get("color") or "GRAY",
                "description": option.get("description") or "",
            }
            for option in current.options
        ]
        preserved.extend(field_option_payload(missing))
        mutate_one(
            transport,
            "updateProjectV2Field",
            "UpdateProjectV2FieldInput",
            {
                "fieldId": current.id,
                "singleSelectOptions": preserved,
                "clientMutationId": f"work-accountability:{current.id}:options",
            },
            "projectV2Field { ... on ProjectV2SingleSelectField { id name options { id name } } }",
        )
        receipt.applied_mutations.append(f"add options to field {name}: {', '.join(missing)}")


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


def ensure_values(
    transport: GhTransport,
    project: ProjectState,
    manifest: Manifest,
    receipt: Receipt,
) -> None:
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    clears: list[tuple[str, Mapping[str, Any]]] = []
    for desired in manifest.items:
        item = project.items.get(desired.number)
        if not item:
            raise ReconcileError(f"issue #{desired.number} is still absent after membership reconciliation")
        values: list[tuple[str, Any]] = [
            ("Health", desired.health),
            ("Source freshness", desired.source_freshness),
            ("Priority", desired.priority),
            ("Rank", desired.rank),
        ]
        if desired.kind == "story":
            values.insert(0, ("Work phase", desired.work_phase))
        for name, value in values:
            if value is None:
                continue
            current = item.values.get(name)
            if isinstance(value, float):
                matches = current is not None and float(current) == value
            else:
                matches = (
                    isinstance(current, str)
                    and isinstance(value, str)
                    and current.casefold() == value.casefold()
                ) or current == value
            if matches:
                continue
            field_state = project.fields[name]
            field_value = (
                {"number": value}
                if field_state.data_type == "NUMBER"
                else {"singleSelectOptionId": option_id(field_state, str(value))}
            )
            label = f"set #{desired.number} {name}={value}"
            receipt.planned_mutations.append(label)
            payloads.append(
                (
                    label,
                    {
                        "projectId": project.id,
                        "itemId": item.id,
                        "fieldId": field_state.id,
                        "value": field_value,
                        "clientMutationId": f"work-accountability:{project.id}:{desired.number}:{name}",
                    },
                )
            )
        if "Status" in item.values and "Status" in project.fields:
            label = f"clear #{desired.number} built-in Status"
            receipt.planned_mutations.append(label)
            clears.append(
                (
                    label,
                    {
                        "projectId": project.id,
                        "itemId": item.id,
                        "fieldId": project.fields["Status"].id,
                        "clientMutationId": f"work-accountability:{project.id}:{desired.number}:clear-status",
                    },
                )
            )
        if desired.kind == "epic" and "Work phase" in item.values:
            label = f"clear #{desired.number} epic Work phase"
            receipt.planned_mutations.append(label)
            clears.append(
                (
                    label,
                    {
                        "projectId": project.id,
                        "itemId": item.id,
                        "fieldId": project.fields["Work phase"].id,
                        "clientMutationId": (
                            f"work-accountability:{project.id}:{desired.number}:clear-epic-phase"
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


def requested_values_match(project: ProjectState, manifest: Manifest) -> bool:
    for desired in manifest.items:
        item = project.items.get(desired.number)
        if not item:
            return False
        expected: dict[str, Any] = {
            "Health": desired.health,
            "Source freshness": desired.source_freshness,
        }
        if desired.kind == "story":
            expected["Work phase"] = desired.work_phase
        if desired.priority is not None:
            expected["Priority"] = desired.priority
        if desired.rank is not None:
            expected["Rank"] = desired.rank
        for name, value in expected.items():
            actual = item.values.get(name)
            if isinstance(value, float) and actual is not None:
                matches = float(actual) == value
            else:
                matches = (
                    isinstance(actual, str)
                    and isinstance(value, str)
                    and actual.casefold() == value.casefold()
                ) or actual == value
            if not matches:
                return False
        if "Status" in item.values:
            return False
        if desired.kind == "epic" and "Work phase" in item.values:
            return False
    return True


def read_lifecycle_marker(readme: str) -> int | None:
    match = re.search(r"^Lifecycle view:\s*(\d+)\s*$", readme, re.MULTILINE)
    return int(match.group(1)) if match else None


def epic_view_filter(manifest: Manifest) -> str:
    return f"parent-issue:{manifest.repository}#{manifest.epic_number}"


def lifecycle_view_valid(
    view: ViewState, fields: Mapping[str, FieldState], manifest: Manifest
) -> bool:
    work_phase = fields.get("Work phase")
    if not work_phase:
        return False
    if view.layout != "BOARD_LAYOUT" or view.vertical_group_ids != [work_phase.id]:
        return False
    if view.filter != epic_view_filter(manifest):
        return False
    desired_sort = [fields[name].id for name in ("Priority", "Rank") if name in fields]
    actual_sort = [
        (field_id, direction.upper()) for field_id, direction in view.sort_fields
    ]
    expected_sort = [(field_id, "ASC") for field_id in desired_sort]
    return actual_sort[: len(expected_sort)] == expected_sort


def create_lifecycle_view(
    transport: GhTransport,
    project: ProjectState,
    repo: RepositoryState,
    manifest: Manifest,
    receipt: Receipt,
    repair_conflict: bool,
) -> int:
    collisions = [view for view in project.views.values() if view.name == "Lifecycle"]
    if collisions:
        if len(collisions) == 1 and lifecycle_view_valid(
            collisions[0], project.fields, manifest
        ):
            return collisions[0].number
        if len(collisions) != 1 or not repair_conflict:
            raise ReconcileError(
                "an unrecorded conflicting Lifecycle view exists; "
                "rerun with --repair-lifecycle to replace only that malformed view"
            )
        old = collisions[0]
        label = f"delete malformed Lifecycle view #{old.number}"
        receipt.planned_mutations.append(label)
        mutate_one(
            transport,
            "deleteProjectV2View",
            "DeleteProjectV2ViewInput",
            {
                "viewId": old.id,
                "clientMutationId": f"work-accountability:{project.id}:replace-view:{old.id}",
            },
            "projectV2View { id number name }",
        )
        receipt.applied_mutations.append(label)
        project = load_project_until(
            transport,
            repo.owner_type,
            manifest.project_owner,
            project.number,
            repo.name_with_owner,
            lambda state: not any(view.name == "Lifecycle" for view in state.views.values()),
            "removal of the malformed Lifecycle view",
        )
    required = [project.fields[name] for name in ("Title", "Health", "Priority", "Source freshness", "Rank")]
    if any(field.database_id is None for field in required + [project.fields["Work phase"]]):
        raise ReconcileError("GitHub did not expose numeric field IDs required by the REST Views API")
    sort_fields = [
        [project.fields["Priority"].database_id, "asc"],
        [project.fields["Rank"].database_id, "asc"],
    ]
    payload = {
        "name": "Lifecycle",
        "layout": "board",
        "filter": epic_view_filter(manifest),
        "visible_fields": [field.database_id for field in required],
        "sort_by": sort_fields,
        "vertical_group_by": [project.fields["Work phase"].database_id],
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


def prune_non_lifecycle_views(
    transport: GhTransport,
    project: ProjectState,
    lifecycle_number: int,
    receipt: Receipt,
) -> None:
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    for view in sorted(project.views.values(), key=lambda candidate: candidate.number):
        if view.number == lifecycle_number:
            continue
        label = f"delete non-Lifecycle view #{view.number} {view.name!r}"
        receipt.planned_mutations.append(label)
        payloads.append(
            (
                label,
                {
                    "viewId": view.id,
                    "clientMutationId": (
                        f"work-accountability:{project.id}:delete-view:{view.id}"
                    ),
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
    lifecycle_number: int,
) -> None:
    if repo.name_with_owner not in project.repositories:
        raise ReconcileError("final verification: Project is not linked to the repository")
    if not marked_for(project, transport.host, repo.id, manifest.epic_work_key):
        raise ReconcileError("final verification: Project marker is absent")
    view = project.views.get(lifecycle_number)
    if not view or not lifecycle_view_valid(view, project.fields, manifest):
        raise ReconcileError("final verification: Lifecycle is not a Work phase Kanban")
    if manifest.lifecycle_only and set(project.views) != {lifecycle_number}:
        raise ReconcileError("final verification: Lifecycle is not the Project's only view")
    desired = {item.number: item for item in manifest.items}
    for number in scoped_issues:
        item = project.items.get(number)
        if not item or item.repository != manifest.repository or item.archived:
            raise ReconcileError(f"final verification: issue #{number} is not an active Project item")
    managed_extras = sorted(
        (set(project.items) & set(all_managed_issues)) - set(scoped_issues)
    )
    if managed_extras:
        raise ReconcileError(
            "final verification: Project contains managed issues outside its epic: "
            + ", ".join(f"#{number}" for number in managed_extras)
        )
    for number, target in desired.items():
        current = project.items[number].values
        expected: dict[str, Any] = {
            "Health": target.health,
            "Source freshness": target.source_freshness,
        }
        if target.kind == "story":
            expected["Work phase"] = target.work_phase
        elif "Work phase" in current:
            raise ReconcileError(
                f"final verification: epic #{number} has a manually maintained Work phase"
            )
        if target.priority is not None:
            expected["Priority"] = target.priority
        if target.rank is not None:
            expected["Rank"] = target.rank
        for name, value in expected.items():
            actual = current.get(name)
            if isinstance(value, float) and actual is not None:
                matches = float(actual) == value
            else:
                matches = (
                    isinstance(actual, str)
                    and isinstance(value, str)
                    and actual.casefold() == value.casefold()
                ) or actual == value
            if not matches:
                raise ReconcileError(
                    f"final verification: #{number} {name} is {actual!r}, expected {value!r}"
                )
        if "Status" in current:
            raise ReconcileError(f"final verification: #{number} still has built-in Status")
    verify_issue_memberships(
        transport,
        manifest.repository,
        sorted(scoped_issues),
        project.id,
        project.items,
    )


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


def run(args: argparse.Namespace) -> int:
    if args.diagnose:
        print(canonical_json(diagnose(args.host, args.user)))
        return 0
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
        remaining = int(rate.get("remaining") or 0)
        used_at_start = int(rate.get("used") or 0)
        reset = rate.get("reset")
        receipt.rate_remaining = remaining
        receipt.rate_reset = reset
        reserve = max(100, len(manifest.items) * 8 + 50)
        if remaining < reserve:
            raise TemporaryFailure(
                f"GraphQL budget {remaining} is below reserve {reserve}; reset={reset}; desired={persisted}"
            )
        all_issues = list_managed_issues(transport, manifest.repository)
        epic_tree = list_epic_tree(
            transport, manifest.repository, manifest.epic_number
        )
        issues = validate_manifest_against_issues(manifest, all_issues, epic_tree)
        repo, owner_projects = discover_repository(transport, manifest.repository)
        if manifest.project_owner.casefold() != repo.owner_login.casefold():
            raise ReconcileError(
                "the reconciler creates Projects under the repository owner; "
                f"manifest requested {manifest.project_owner}, repository owner is {repo.owner_login}"
            )
        intent_path = create_intent_path(root, manifest.epic_work_key)
        intent = json.loads(intent_path.read_text()) if intent_path.exists() else None
        project = select_project(
            owner_projects,
            repo,
            args.host,
            manifest.epic_work_key,
            args.adopt_project,
            intent,
            transport.login,
        )
        if project is None:
            receipt.planned_mutations.append("create epic Project linked to repository")
            if not args.apply:
                for name in expected_field_schema(manifest):
                    receipt.planned_mutations.append(f"create field {name}")
                receipt.planned_mutations.extend(
                    f"add issue #{number}" for number in sorted(issues)
                )
                receipt.planned_mutations.append(
                    "create Lifecycle Work phase board filtered to epic children"
                )
                if manifest.lifecycle_only:
                    receipt.planned_mutations.append(
                        "remove GitHub's initial table after Lifecycle is verified"
                    )
                receipt.planned_mutations.extend(
                    f"bind issue #{number} to Lifecycle Project view"
                    for number in sorted(issues)
                )
                complete_receipt_metrics(receipt, transport, used_at_start)
                print_receipt(receipt)
                return 0
            project = create_project(transport, manifest, repo, root)
            receipt.applied_mutations.append("create epic Project linked to repository")
        receipt.project_number = project.number
        receipt.project_url = project.url
        preflight_detail = load_project_detail(
            transport,
            repo.owner_type,
            manifest.project_owner,
            project.number,
            manifest.repository,
        )
        current_view_marker = read_lifecycle_marker(project.readme)
        if current_view_marker is not None:
            current_view = preflight_detail.views.get(current_view_marker)
            if not current_view or not lifecycle_view_valid(
                current_view, preflight_detail.fields, manifest
            ):
                if not args.repair_lifecycle:
                    raise ReconcileError(
                        f"managed Lifecycle view {current_view_marker} is missing or malformed; "
                        "rerun with --repair-lifecycle"
                    )
                current_view_marker = None
        desired_readme = managed_readme(
            project.readme, args.host, repo, manifest, current_view_marker
        )
        metadata_changes = project.title != manifest.project_title or project.readme != desired_readme or project.closed
        if metadata_changes:
            receipt.planned_mutations.append("update Project title/managed README block")
        if repo.name_with_owner not in project.repositories:
            receipt.planned_mutations.append("link Project to repository")
        if not args.apply:
            detail = preflight_detail
            separately_removed_views: set[int] = set()
            for name in expected_field_schema(manifest):
                if name not in detail.fields:
                    receipt.planned_mutations.append(f"create field {name}")
            for number in sorted(issues):
                if number not in detail.items:
                    receipt.planned_mutations.append(f"add issue #{number}")
            for number in sorted((set(detail.items) & set(all_issues)) - set(issues)):
                receipt.planned_mutations.append(
                    f"remove out-of-scope managed issue #{number}"
                )
            if not current_view_marker:
                collisions = [view for view in detail.views.values() if view.name == "Lifecycle"]
                invalid = [
                    view
                    for view in collisions
                    if not lifecycle_view_valid(view, detail.fields, manifest)
                ]
                if len(collisions) > 1:
                    raise ReconcileError(
                        "multiple unrecorded views named Lifecycle exist; refusing automatic replacement"
                    )
                if invalid and not args.repair_lifecycle:
                    raise ReconcileError(
                        "an unrecorded conflicting Lifecycle view exists; "
                        "rerun with --repair-lifecycle to replace only that malformed view"
                    )
                if invalid:
                    receipt.planned_mutations.append(
                        f"delete malformed Lifecycle view #{invalid[0].number}"
                    )
                    separately_removed_views.add(invalid[0].number)
                receipt.planned_mutations.append("create Lifecycle Work phase board")
            if manifest.lifecycle_only:
                for view in detail.views.values():
                    if (
                        view.number != current_view_marker
                        and view.number not in separately_removed_views
                    ):
                        receipt.planned_mutations.append(
                            f"delete non-Lifecycle view #{view.number} {view.name!r}"
                        )
            projection_url = (
                f"{project.url}/views/{current_view_marker}"
                if current_view_marker is not None
                else project.url
            )
            plan_issue_projection(issues, projection_url, receipt)
            complete_receipt_metrics(receipt, transport, used_at_start)
            print_receipt(receipt)
            return 0
        append_journal(root, {"event": "start", "manifest": manifest.digest})
        if metadata_changes:
            project = update_project_metadata(
                transport, project, manifest.project_title, desired_readme
            )
            receipt.applied_mutations.append("update Project title/managed README block")
        if repo.name_with_owner not in project.repositories:
            ensure_link(transport, project, repo)
            receipt.applied_mutations.append("link Project to repository")
        project = load_project_detail(
            transport, repo.owner_type, manifest.project_owner, project.number, manifest.repository
        )
        applied_before = len(receipt.applied_mutations)
        ensure_fields(transport, project, manifest, receipt)
        if len(receipt.applied_mutations) != applied_before:
            project = load_project_until(
                transport,
                repo.owner_type,
                manifest.project_owner,
                project.number,
                manifest.repository,
                lambda state: all(name in state.fields for name in expected_field_schema(manifest)),
                "the required Project fields",
            )
        applied_before = len(receipt.applied_mutations)
        reconcile_membership(
            transport,
            project,
            issues,
            all_issues,
            manifest.repository,
            receipt,
        )
        if len(receipt.applied_mutations) != applied_before:
            project = load_project_until(
                transport,
                repo.owner_type,
                manifest.project_owner,
                project.number,
                manifest.repository,
                lambda state: (
                    all(number in state.items for number in issues)
                    and not (
                        (set(state.items) & set(all_issues)) - set(issues)
                    )
                ),
                "the exact epic Project membership",
            )
        applied_before = len(receipt.applied_mutations)
        ensure_values(transport, project, manifest, receipt)
        if len(receipt.applied_mutations) != applied_before:
            project = load_project_until(
                transport,
                repo.owner_type,
                manifest.project_owner,
                project.number,
                manifest.repository,
                lambda state: requested_values_match(state, manifest),
                "the requested Project field values",
            )
        lifecycle_number = read_lifecycle_marker(project.readme)
        lifecycle_mutated = False
        if lifecycle_number is not None:
            view = project.views.get(lifecycle_number)
            if not view or not lifecycle_view_valid(view, project.fields, manifest):
                raise ReconcileError(
                    f"managed Lifecycle view {lifecycle_number} is missing or malformed; refusing replacement"
                )
        else:
            receipt.planned_mutations.append("create Lifecycle Work phase board")
            lifecycle_number = create_lifecycle_view(
                transport,
                project,
                repo,
                manifest,
                receipt,
                args.repair_lifecycle,
            )
            receipt.applied_mutations.append("create Lifecycle Work phase board")
            refreshed_readme = managed_readme(
                project.readme, args.host, repo, manifest, lifecycle_number
            )
            project = update_project_metadata(
                transport, project, manifest.project_title, refreshed_readme
            )
            receipt.applied_mutations.append("record Lifecycle view in Project README")
            lifecycle_mutated = True
        receipt.lifecycle_view = lifecycle_number
        if lifecycle_mutated:
            project = load_project_until(
                transport,
                repo.owner_type,
                manifest.project_owner,
                project.number,
                manifest.repository,
                lambda state: (
                    read_lifecycle_marker(state.readme) == lifecycle_number
                    and lifecycle_number in state.views
                    and lifecycle_view_valid(
                        state.views[lifecycle_number], state.fields, manifest
                    )
                ),
                "the managed Lifecycle Kanban",
            )
        if manifest.lifecycle_only and set(project.views) != {lifecycle_number}:
            prune_non_lifecycle_views(
                transport, project, lifecycle_number, receipt
            )
            project = load_project_until(
                transport,
                repo.owner_type,
                manifest.project_owner,
                project.number,
                manifest.repository,
                lambda state: set(state.views) == {lifecycle_number},
                "Lifecycle as the Project's only view",
            )
        receipt.lifecycle_url = f"{project.url}/views/{lifecycle_number}"
        verify_final(
            transport,
            manifest,
            repo,
            project,
            issues,
            all_issues,
            lifecycle_number,
        )
        ensure_issue_projection(
            transport,
            manifest.repository,
            issues,
            receipt.lifecycle_url,
            receipt,
        )
        # MutationRoot cannot select rateLimit.  The REST rate summary is free
        # of primary-rate cost, so the before/after `used` delta accounts for
        # queries and mutations without adding a query after every write.
        complete_receipt_metrics(receipt, transport, used_at_start)
        receipt.verified = True
        append_journal(root, {"event": "verified", "receipt": receipt.__dict__})
        if intent_path.exists():
            intent_path.unlink()
        print_receipt(receipt)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", help="Desired-state JSON manifest")
    parser.add_argument("--user", help="Stored gh account to use without changing global state")
    parser.add_argument("--host", default="github.com")
    parser.add_argument("--adopt-project", type=int, help="Explicitly adopt this unmarked Project")
    parser.add_argument(
        "--repair-lifecycle",
        action="store_true",
        help="Replace one malformed unrecorded view named Lifecycle",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the computed delta")
    parser.add_argument("--diagnose", action="store_true", help="Print installation and GitHub readiness")
    args = parser.parse_args()
    if not args.diagnose and not args.manifest:
        parser.error("--manifest is required unless --diagnose is used")
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
