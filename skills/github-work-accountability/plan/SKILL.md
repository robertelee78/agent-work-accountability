---
name: plan
description: Domain for planning sources and managed issues. Open to turn an ADR, PRD or proposal into epics and stories, to bind or change an ADR, to create or update managed issues by work key, or when a planning source changed or a write could not reach GitHub.
---

# Plan: from a planning source to managed issues

Planning sources (ADRs, PRDs, proposals, roadmaps, design documents) own the Why, the What, scope and
acceptance intent. GitHub owns operational work state and evidence. This domain turns a source into a
validated epic/story tree, binds it to exact Git bytes, and keeps the issues faithful to it.

## Route

| The task is… | Open |
|---|---|
| Decompose a planning document into epics and stories and validate the result | [extract](extract/SKILL.md) |
| The source is an ADR: discover its policy, inspect its status, or change it | [adr](adr/SKILL.md) |
| Create or update the managed issue for a work key | [issues](issues/SKILL.md) |
| The source changed, the default branch moved, or a write result is uncertain | [drift](drift/SKILL.md) |

## Rules for the whole domain

- A session MUST read repository instructions and the current planning source, including its lifecycle
  status and revision, and MUST inspect existing issues, relationships, Project membership, implementation
  and evidence before writing.
- A model MAY propose a decomposition; a deterministic source-binding and graph check MUST pass before any
  issue is created or changed ([extract](extract/SKILL.md)). Coverage MUST be reviewed separately: exact
  quotations prove provenance, not completeness.
- A merely proposed or draft source MAY be decomposed for design discussion but MUST NOT make stories
  execution-ready. Items MUST inherit only the furthest phase justified by actual product and technical
  approvals.
- A session MUST NOT create a second writable copy of requirements: the managed block's prose MUST be
  regenerated from a validated extraction and MUST NOT be edited as a requirement.
- Writes MUST be idempotent: the session MUST re-read after an uncertain write, MUST match the stable key
  across open and closed issues, MUST stop on duplicate identities, and MUST preserve human prose, comments,
  history and earlier evidence.

## Tools in this domain

| Tool | Does | Exit |
|---|---|---|
| `python3 "$SKILL_DIR/scripts/validate_extraction.py" MANIFEST --repo PATH [--against REF]` | Checks an extraction-v3 manifest against the exact source bytes and the tree rules. | 0 `ok: true`; 1 `ok: false` with `errors[]` |
| `python3 "$SKILL_DIR/scripts/inspect_adr.py" PATH --repo PATH --ref REF [--require-status] [--against REF] [--working-tree]` | Reads an ADR's declared metadata from Git, keeping decision and execution status apart. | 0 `ok: true`; 1 with `errors[]` |
| `$RECONCILE --draft --repo OWNER/REPO --root N` | Reads the current tree and values from GitHub (read-only). | 0, or 2 with the reason |

`SKILL_DIR` and `RECONCILE` are defined in the [entry file](../SKILL.md#conventions-used-by-every-leaf).
