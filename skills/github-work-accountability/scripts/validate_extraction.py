#!/usr/bin/env python3
"""Validate source binding, identities, and dependencies for an extraction manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


SCHEMA = "github-work-accountability/extraction-v1"
WORK_KEY = re.compile(r"^[^/\s:]+/[^/\s:]+:[^\s:]+(?::[^\s:]+)*$")


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def required_text(value: Any, location: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location} must be a non-empty string")
        return ""
    return value


def required_work_key(value: Any, location: str, errors: list[str]) -> str:
    key = required_text(value, location, errors)
    if key and not WORK_KEY.fullmatch(key):
        errors.append(
            f"{location} must be repository-qualified as OWNER/REPOSITORY:SOURCE[:ITEM]"
        )
    return key


def validate_quotes(
    item: dict[str, Any], location: str, source_text: str, errors: list[str]
) -> None:
    quotes = item.get("source_quotes")
    if not isinstance(quotes, list) or not quotes:
        errors.append(f"{location}.source_quotes must be a non-empty array")
        return
    for index, quote in enumerate(quotes):
        if not isinstance(quote, str) or not quote:
            errors.append(f"{location}.source_quotes[{index}] must be a non-empty string")
        elif quote not in source_text:
            errors.append(f"{location}.source_quotes[{index}] is not exact source text")


def find_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    visited: set[str] = set()
    active: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in active:
            start = path.index(node)
            return [*path[start:], node]
        if node in visited:
            return None
        visited.add(node)
        active.add(node)
        path.append(node)
        for dependency in dependencies.get(node, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        path.pop()
        active.remove(node)
        return None

    for key in dependencies:
        cycle = visit(key)
        if cycle:
            return cycle
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--against", help="also require the source blob to be unchanged at this git ref"
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}, indent=2))
        return 1

    if not isinstance(document, dict):
        errors.append("manifest root must be an object")
        document = {}
    if document.get("schema") != SCHEMA:
        errors.append(f"schema must equal {SCHEMA!r}")

    source = document.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
        source = {}
    required_text(source.get("kind"), "source.kind", errors)
    source_path = required_text(source.get("path"), "source.path", errors)
    source_commit = required_text(source.get("commit"), "source.commit", errors)
    expected_blob = required_text(source.get("blob"), "source.blob", errors)

    source_bytes = b""
    actual_commit = ""
    actual_blob = ""
    source_path_valid = False
    if source_path:
        parsed_path = PurePosixPath(source_path)
        if parsed_path.is_absolute() or ".." in parsed_path.parts:
            errors.append("source.path must be a repository-relative path without '..'")
        else:
            source_path_valid = True
    if source_commit and source_path_valid:
        commit_result = git(args.repo, "rev-parse", f"{source_commit}^{{commit}}")
        if commit_result.returncode != 0:
            errors.append(
                f"cannot resolve source.commit: {commit_result.stderr.decode(errors='replace').strip()}"
            )
        else:
            actual_commit = commit_result.stdout.decode().strip()
            blob_result = git(args.repo, "rev-parse", f"{actual_commit}:{source_path}")
            show_result = git(args.repo, "show", f"{actual_commit}:{source_path}")
            if blob_result.returncode != 0 or show_result.returncode != 0:
                detail = (blob_result.stderr or show_result.stderr).decode(errors="replace").strip()
                errors.append(f"cannot read source at recorded commit: {detail}")
            else:
                actual_blob = blob_result.stdout.decode().strip()
                source_bytes = show_result.stdout
                if expected_blob != actual_blob:
                    errors.append(
                        f"source.blob mismatch: manifest={expected_blob} git={actual_blob}"
                    )

    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        errors.append(f"source is not UTF-8: {error}")
        source_text = ""

    epic = document.get("epic")
    if not isinstance(epic, dict):
        errors.append("epic must be an object")
        epic = {}
    epic_key = required_work_key(epic.get("key"), "epic.key", errors)
    required_text(epic.get("title"), "epic.title", errors)
    if source_text:
        validate_quotes(epic, "epic", source_text, errors)

    stories = document.get("stories")
    if not isinstance(stories, list) or not stories:
        errors.append("stories must be a non-empty array")
        stories = []

    keys: set[str] = set()
    if epic_key:
        keys.add(epic_key)
    story_keys: set[str] = set()
    dependency_graph: dict[str, list[str]] = {}
    for index, raw_story in enumerate(stories):
        location = f"stories[{index}]"
        if not isinstance(raw_story, dict):
            errors.append(f"{location} must be an object")
            continue
        key = required_work_key(raw_story.get("key"), f"{location}.key", errors)
        required_text(raw_story.get("title"), f"{location}.title", errors)
        required_text(raw_story.get("outcome"), f"{location}.outcome", errors)
        required_text(raw_story.get("validation"), f"{location}.validation", errors)
        required_text(
            raw_story.get("delivery_boundary"), f"{location}.delivery_boundary", errors
        )
        acceptance = raw_story.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append(f"{location}.acceptance must be a non-empty array")
        else:
            for criterion_index, criterion in enumerate(acceptance):
                required_text(
                    criterion,
                    f"{location}.acceptance[{criterion_index}]",
                    errors,
                )
        if source_text:
            validate_quotes(raw_story, location, source_text, errors)
        if key:
            if key in keys:
                errors.append(f"duplicate work key: {key}")
            keys.add(key)
            story_keys.add(key)
        dependencies = raw_story.get("dependencies", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or not value for value in dependencies
        ):
            errors.append(f"{location}.dependencies must be an array of non-empty strings")
            dependencies = []
        if key:
            dependency_graph[key] = dependencies

    for key, dependencies in dependency_graph.items():
        for dependency in dependencies:
            if dependency == key:
                errors.append(f"story {key} cannot depend on itself")
            elif dependency not in story_keys:
                errors.append(f"story {key} has unknown dependency {dependency}")
    cycle = find_cycle(dependency_graph)
    if cycle:
        errors.append(f"dependency cycle: {' -> '.join(cycle)}")

    coverage = document.get("coverage")
    if not isinstance(coverage, list) or not coverage:
        errors.append("coverage must be a non-empty array")
        coverage = []
    covered_story_keys: set[str] = set()
    for index, raw_mapping in enumerate(coverage):
        location = f"coverage[{index}]"
        if not isinstance(raw_mapping, dict):
            errors.append(f"{location} must be an object")
            continue
        quote = required_text(raw_mapping.get("source_quote"), f"{location}.source_quote", errors)
        if quote and source_text and quote not in source_text:
            errors.append(f"{location}.source_quote is not exact source text")
        mapped_stories = raw_mapping.get("stories")
        if not isinstance(mapped_stories, list) or not mapped_stories:
            errors.append(f"{location}.stories must be a non-empty array")
            continue
        for story_key in mapped_stories:
            if not isinstance(story_key, str) or not story_key:
                errors.append(f"{location}.stories must contain non-empty strings")
            elif story_key not in story_keys:
                errors.append(f"{location} has unknown story {story_key}")
            else:
                covered_story_keys.add(story_key)
    for story_key in sorted(story_keys - covered_story_keys):
        errors.append(f"story {story_key} has no coverage mapping")

    comparison_blob = ""
    if args.against and source_path:
        comparison = git(args.repo, "rev-parse", f"{args.against}:{source_path}")
        if comparison.returncode != 0:
            errors.append(
                f"cannot read source at --against {args.against}: "
                f"{comparison.stderr.decode(errors='replace').strip()}"
            )
        else:
            comparison_blob = comparison.stdout.decode().strip()
            if actual_blob and comparison_blob != actual_blob:
                errors.append(
                    f"source changed at {args.against}: recorded={actual_blob} current={comparison_blob}"
                )

    report = {
        "ok": not errors,
        "schema": document.get("schema"),
        "source": {
            "commit": actual_commit,
            "blob": actual_blob,
            "against": args.against,
            "against_blob": comparison_blob,
        },
        "epic_key": epic_key,
        "story_count": len(stories),
        "coverage_count": len(coverage),
        "errors": errors,
    }
    print(json.dumps(report, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
