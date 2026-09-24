#!/usr/bin/env python3
"""Inspect an ADR directly from Git without assuming a repository lifecycle."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any


SCHEMA = "github-work-accountability/adr-source-v1"
FRONTMATTER_KEY = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")
MARKDOWN_FIELD = re.compile(
    r"^\s*(?:[-*]\s+)?\*\*"
    r"(Status|Decision\s+status|Execution\s+status|Date|Updated|"
    r"Supersedes|Superseded[- ]by|Amends|Amended[- ]by|Depends[- ]on|Related)"
    r"\s*:?\*\*\s*:?\s*(.*?)\s*$",
    re.IGNORECASE,
)
HEADING = re.compile(r"^#\s+(?:(ADR-[A-Za-z0-9-]+)\s*:\s*)?(.+?)\s*$", re.IGNORECASE)
KNOWN_STATUSES = (
    "partially implemented",
    "partly implemented",
    "release ready",
    "not started",
    "in progress",
    "superseded",
    "implemented",
    "deprecated",
    "accepted",
    "proposed",
    "rejected",
    "excellent",
    "draft",
)
RELATION_ALIASES = {
    "supersedes": "supersedes",
    "superseded-by": "superseded_by",
    "superseded_by": "superseded_by",
    "amends": "amends",
    "amended-by": "amended_by",
    "amended_by": "amended_by",
    "depends-on": "depends_on",
    "depends_on": "depends_on",
    "related": "related",
}


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def normalize_key(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def scalar_or_list(value: str) -> str | list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [unquote(part.strip()) for part in next(csv.reader([inner], skipinitialspace=True))]
    return unquote(value)


def parse_frontmatter(text: str, errors: list[str]) -> tuple[dict[str, Any], int | None]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, None
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append("byte-zero frontmatter has no closing delimiter")
        return {}, None

    result: dict[str, Any] = {}
    active_list: str | None = None
    for number, line in enumerate(lines[1:end], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace() and stripped.startswith("-") and active_list:
            item = unquote(stripped[1:].strip())
            if not item:
                errors.append(f"frontmatter line {number}: empty list item")
            else:
                cast_list = result.setdefault(active_list, [])
                if not isinstance(cast_list, list):
                    errors.append(f"frontmatter line {number}: malformed list for {active_list}")
                else:
                    cast_list.append(item)
            continue
        match = FRONTMATTER_KEY.match(line)
        if not match:
            errors.append(f"frontmatter line {number}: unsupported syntax")
            active_list = None
            continue
        key = normalize_key(match.group(1))
        if key in result:
            errors.append(f"frontmatter line {number}: duplicate field {key}")
            active_list = None
            continue
        raw_value = match.group(2)
        if not raw_value.strip():
            result[key] = []
            active_list = key
        else:
            result[key] = scalar_or_list(raw_value)
            active_list = None
    return result, end + 1


def plain_markdown(value: str) -> str:
    value = re.sub(r"[`*_]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_status(value: str) -> str:
    clean = plain_markdown(value).lower().strip()
    for status in KNOWN_STATUSES:
        if clean == status or clean.startswith(status + " ") or clean.startswith(status + ";"):
            return status
    clean = re.split(r"\s+[—–-]\s+|;|\.|\s+by\s+", clean, maxsplit=1)[0]
    return clean.strip()


def list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        stripped = stripped.strip("[]")
        values = next(csv.reader([stripped], skipinitialspace=True))
    else:
        return []
    return [plain_markdown(str(item)).strip() for item in values if str(item).strip()]


def source_record(kind: str, raw: str) -> dict[str, str]:
    return {"source": kind, "raw": plain_markdown(raw)}


def inspect_metadata(text: str, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    frontmatter, body_start = parse_frontmatter(text, errors)
    lines = text.splitlines()
    header_lines = lines[body_start or 0 : (body_start or 0) + 120]

    markdown: dict[str, list[str]] = {}
    heading_id = ""
    heading_title = ""
    for line in header_lines:
        heading_match = HEADING.match(line)
        if heading_match and not heading_title:
            heading_id = (heading_match.group(1) or "").upper()
            heading_title = plain_markdown(heading_match.group(2))
        field_match = MARKDOWN_FIELD.match(line)
        if field_match:
            key = normalize_key(field_match.group(1))
            markdown.setdefault(key, []).append(field_match.group(2))

    decision_candidates: list[dict[str, str]] = []
    for key in ("status", "decision-status"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            decision_candidates.append(source_record(f"frontmatter.{key}", value))
    for key in ("decision-status", "status"):
        for value in markdown.get(key, []):
            if value.strip():
                decision_candidates.append(source_record(f"markdown.{key}", value))

    execution_candidates: list[dict[str, str]] = []
    value = frontmatter.get("execution-status")
    if isinstance(value, str) and value.strip():
        execution_candidates.append(source_record("frontmatter.execution-status", value))
    for value in markdown.get("execution-status", []):
        if value.strip():
            execution_candidates.append(source_record("markdown.execution-status", value))

    for candidates, label in (
        (decision_candidates, "decision status"),
        (execution_candidates, "execution status"),
    ):
        normalized = {normalize_status(candidate["raw"]) for candidate in candidates}
        normalized.discard("")
        if len(normalized) > 1:
            detail = ", ".join(
                f"{candidate['source']}={candidate['raw']!r}" for candidate in candidates
            )
            errors.append(f"conflicting {label} declarations: {detail}")

    if not decision_candidates:
        warnings.append("no declared decision status found; repository policy must classify the ADR")

    id_candidates: list[dict[str, str]] = []
    if isinstance(frontmatter.get("id"), str) and frontmatter["id"].strip():
        id_candidates.append(source_record("frontmatter.id", frontmatter["id"]))
    if heading_id:
        id_candidates.append(source_record("heading", heading_id))
    normalized_ids = {candidate["raw"].upper() for candidate in id_candidates}
    if len(normalized_ids) > 1:
        errors.append(
            "conflicting ADR identifiers: "
            + ", ".join(f"{item['source']}={item['raw']!r}" for item in id_candidates)
        )

    date_candidates: list[dict[str, str]] = []
    for key in ("date", "updated"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            date_candidates.append(source_record(f"frontmatter.{key}", value))
        for item in markdown.get(key, []):
            if item.strip():
                date_candidates.append(source_record(f"markdown.{key}", item))

    dates: dict[str, dict[str, Any] | None] = {}
    for key in ("date", "updated"):
        candidates = [item for item in date_candidates if item["source"].endswith(f".{key}")]
        distinct = {item["raw"] for item in candidates}
        if len(distinct) > 1:
            errors.append(
                f"conflicting {key} declarations: "
                + ", ".join(f"{item['source']}={item['raw']!r}" for item in candidates)
            )
        dates[key] = (
            {"value": candidates[0]["raw"], "declarations": candidates} if candidates else None
        )

    relations: dict[str, list[str]] = {
        value: [] for value in dict.fromkeys(RELATION_ALIASES.values())
    }
    for raw_key, canonical in RELATION_ALIASES.items():
        for item in list_value(frontmatter.get(raw_key)):
            if item not in relations[canonical]:
                relations[canonical].append(item)
        for item in markdown.get(raw_key, []):
            for relation in list_value(item):
                if relation not in relations[canonical]:
                    relations[canonical].append(relation)

    if not frontmatter:
        warnings.append("no byte-zero frontmatter found; using declared Markdown metadata")
    if not dates["updated"]:
        warnings.append("no Updated declaration found")

    decision_status = None
    if decision_candidates:
        decision_status = {
            "raw": decision_candidates[0]["raw"],
            "normalized": normalize_status(decision_candidates[0]["raw"]),
            "declarations": decision_candidates,
        }
    execution_status = None
    if execution_candidates:
        execution_status = {
            "raw": execution_candidates[0]["raw"],
            "normalized": normalize_status(execution_candidates[0]["raw"]),
            "declarations": execution_candidates,
        }

    title_value = frontmatter.get("title") if isinstance(frontmatter.get("title"), str) else ""
    return {
        "format": (
            "frontmatter+markdown"
            if frontmatter and markdown
            else "frontmatter"
            if frontmatter
            else "markdown"
        ),
        "id": id_candidates[0]["raw"] if id_candidates else None,
        "title": plain_markdown(title_value) or heading_title or None,
        "decision_status": decision_status,
        "execution_status": execution_status,
        "date": dates["date"],
        "updated": dates["updated"],
        "relations": relations,
        "frontmatter": frontmatter,
    }


def validate_path(raw_path: str, errors: list[str]) -> PurePosixPath | None:
    parsed = PurePosixPath(raw_path)
    if parsed.is_absolute() or ".." in parsed.parts or str(parsed) in {"", "."}:
        errors.append("path must be repository-relative and cannot contain '..'")
        return None
    return parsed


def read_ref(repo: Path, ref: str, source_path: str, errors: list[str]) -> tuple[str, str, bytes]:
    commit_result = git(repo, "rev-parse", f"{ref}^{{commit}}")
    if commit_result.returncode != 0:
        errors.append(
            f"cannot resolve --ref {ref}: {commit_result.stderr.decode(errors='replace').strip()}"
        )
        return "", "", b""
    commit = commit_result.stdout.decode().strip()
    blob_result = git(repo, "rev-parse", f"{commit}:{source_path}")
    show_result = git(repo, "show", f"{commit}:{source_path}")
    if blob_result.returncode != 0 or show_result.returncode != 0:
        detail = (blob_result.stderr or show_result.stderr).decode(errors="replace").strip()
        errors.append(f"cannot read {source_path} at {commit}: {detail}")
        return commit, "", b""
    return commit, blob_result.stdout.decode().strip(), show_result.stdout


def read_worktree(repo: Path, source_path: str, errors: list[str]) -> tuple[str, bytes]:
    root = repo.resolve()
    candidate = root.joinpath(*PurePosixPath(source_path).parts)
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        errors.append("working-tree path resolves outside the repository")
        return "", b""
    try:
        content = candidate.read_bytes()
    except OSError as error:
        errors.append(f"cannot read working-tree source: {error}")
        return "", b""
    hashed = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "--stdin"],
        input=content,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if hashed.returncode != 0:
        errors.append(
            f"cannot hash working-tree source: {hashed.stderr.decode(errors='replace').strip()}"
        )
        return "", content
    return hashed.stdout.decode().strip(), content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="repository-relative ADR path")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--ref", default="HEAD", help="Git ref to inspect (default: HEAD)")
    parser.add_argument("--working-tree", action="store_true", help="inspect current file bytes")
    parser.add_argument("--against", help="fail when this ref has different source bytes")
    parser.add_argument("--require-status", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    source_path = validate_path(args.path, errors)
    commit = ""
    blob = ""
    content = b""
    if source_path:
        if args.working_tree:
            blob, content = read_worktree(args.repo, str(source_path), errors)
        else:
            commit, blob, content = read_ref(args.repo, args.ref, str(source_path), errors)

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        errors.append(f"ADR is not UTF-8: {error}")
        text = ""

    metadata = inspect_metadata(text, errors, warnings) if text else {}
    if args.require_status and not metadata.get("decision_status"):
        errors.append("a declared decision status is required")

    against_blob = ""
    changed = None
    if args.against and source_path:
        result = git(args.repo, "rev-parse", f"{args.against}:{source_path}")
        if result.returncode != 0:
            errors.append(
                f"cannot read {source_path} at --against {args.against}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        else:
            against_blob = result.stdout.decode().strip()
            changed = bool(blob and against_blob != blob)
            if changed:
                errors.append(
                    f"ADR source changed at {args.against}: inspected={blob} "
                    f"comparison={against_blob}"
                )

    report = {
        "ok": not errors,
        "schema": SCHEMA,
        "source": {
            "path": str(source_path) if source_path else args.path,
            "kind": "working-tree" if args.working_tree else "git",
            "ref": None if args.working_tree else args.ref,
            "commit": commit or None,
            "blob": blob or None,
            "against": args.against,
            "against_blob": against_blob or None,
            "changed": changed,
        },
        "metadata": metadata,
        "warnings": warnings,
        "errors": errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
