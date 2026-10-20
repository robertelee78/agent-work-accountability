---
name: execute-pick
description: Open to choose which story to start next. Teaches awa ready, its order, the in-progress and needs-a-decision lists, and why a session takes the first story it is allowed to take rather than reading the board.
---

# Pick the next story

## Steps

1. The session MUST list eligible stories with `awa ready` (read-only) and MUST NOT choose work by reading
   the board itself:

   ```sh
   awa ready [--repo OWNER/REPO] [--root ROOT_ISSUE] [--json]
   ```

   ```text
   vox — Demo workstream (Project #1, root #1)
     ready to start, in board order:
       #4 Beta story (Medium)
       #5 Gamma story (Medium)
     in progress: #3 Alpha story (attempt alpha-1 by impl-1 since 2026-10-19T12:00:00Z)
   ```

   With `--json`, each document lists `ready` (issue, title, priority, rank), `in_progress` and
   `needs_decision`.

2. The session SHOULD start the first story it is allowed to take, in the order printed
   ([attempt](../attempt/GUIDE.md)). It MAY skip a story for a stated reason (for example, the operator
   assigned it elsewhere) and MUST NOT skip one without a stated reason. It MUST say which story it skipped
   and why.

## What the results mean

| Output | Means | Required action |
|---|---|---|
| `ready to start, in board order:` | Stories in Ready, not Blocked, issue open, no open blocked-by issue, no `Attempt:` line; ordered by Priority (the field's option order), then Rank. | The session SHOULD start the first one it may take. |
| `ready to start: none` | Nothing is eligible in that document. | The session MUST NOT start work there. |
| `in progress: #N … (attempt ID by ACTOR since TIME)` | Another attempt holds that story. | The session MUST NOT start it; it MAY ask that attempt's owner or a person. |
| `needs_decision` (or `needs a decision`) | Ready stories with three or more ended attempts. | A person MUST decide before another attempt starts; the session MUST ask. |

Readiness also requires that every predecessor is Done at its own delivery boundary and that the planning
source is Current ([model](../../model/GUIDE.md#readiness)). Priority and rank never override an unsatisfied
dependency, failed approval gate, or open attempt.

`awa ready` does not claim anything. Two agents reading it at once see the same first story; the second
one's attempt start is refused ([attempt](../attempt/GUIDE.md)). A coordinator running several agents
SHOULD hand each a different story and SHOULD record their attempt starts in one reconcile run.

## Dropping down a level

The same listing is available from the script:

```sh
$RECONCILE --ready --repo OWNER/REPO [--root ROOT] [--json]
```

Beneath it: each document Project's items and Work phase, Health, Priority and Rank values
(`ProjectV2.items`), each issue's open/closed state and `Attempt:` / `Ended attempts:` lines, and open
blocked-by issues (`Issue.blockedBy`). A session MAY read these directly to explain a surprising result, but
MUST NOT use its own reading to override `awa ready`.
