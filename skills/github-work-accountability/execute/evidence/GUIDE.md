---
name: execute-evidence
description: Open to see what evidence a story already has before recording an event, or when unsure whether evidence is missing. Teaches awa status --evidence.
---

# List a story's evidence

## Steps

The session MUST use this command, not a text search, to decide whether a story has evidence:

```sh
awa status --evidence N [--repo OWNER/REPO] [--json]
```

It is read-only and lists three things. Observed:

```text
#3 Alpha story (closed as completed, milestone v1.0.0)
  work key: OWNER/REPO:DEMO:S1:alpha
  records in the managed block:
    Project: https://github.com/users/OWNER/projects/1/views/2
    Planned delivery: release v1.0.0
    Delivery: release v1.0.0
    Release: v1.0.0
    Integration: 0000…1235 via https://github.com/OWNER/REPO/pull/9001
    Delivered: https://github.com/OWNER/REPO/releases/tag/v1.0.0
  comments with work-accountability markers: 2
    2026-09-20T12:00:00Z @LOGIN event event=attempt-started actor=impl-1 time=2026-10-19T12:00:00Z
      https://github.com/OWNER/REPO/issues/3#issuecomment-1144
      Attempt alpha-1 started.
    …
  evidence in manifests this machine applied:
    design_approval: …
    attempt: …
    candidate: …
    verdict: …
    integration: …
    delivery: …
```

## What the results mean

| Section | Means |
|---|---|
| records in the managed block | The facts awa wrote and trusts. |
| comments with work-accountability markers | Every marker comment, including older ID-only markers such as `<!-- work-accountability:event 2026-09-25-batch3 -->`. |
| evidence in manifests this machine applied | Evidence lives in applied manifests on the machine that applied them, not on GitHub; another machine shows less. |

When the third section is empty on this machine but the first two show the story's history, the session
MUST NOT conclude the evidence is missing; it SHOULD reconcile from the machine that applied it, or supply
the earlier manifest with `--base` ([board/reconcile](../../board/reconcile/GUIDE.md)).
