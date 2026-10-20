---
name: execute-pick
description: Open to choose which story to start next. Teaches awa ready, its order, the in-progress, blocked, at-risk and needs-a-decision lists, the in-flight phase counts, labels and the --label filter, and why a session takes the first story it is allowed to take rather than reading the board.
---

# Pick the next story

A session working this leaf MUST also follow the [execute domain rules](../GUIDE.md).

## Steps

1. The session MUST list eligible stories with `awa ready` (read-only) and MUST NOT choose work by reading
   the board itself:

   ```sh
   awa ready [--repo OWNER/REPO] [--root ROOT_ISSUE] [--label LABEL] [--json]
   ```

   ```text
   vox — Demo workstream (Project #1, root #1)
     ready to start, in board order:
       #4 Beta story (Medium)
       #5 Gamma story (Medium)
     in flight: 1 executing, 1 acceptance
     in progress: #3 Alpha story (attempt alpha-1 by impl-1 since 2026-10-19T12:00:00Z)
     blocked: #6 Delta story (blocked by #7)
     at risk: #8 Epsilon story
   ```

   With `--json`, each document lists `ready`, `in_progress`, `blocked`, `at_risk` and `needs_decision`
   (each entry carries issue, title, priority, rank, `phase` and `labels`), plus `counts` (open
   non-terminal stories per phase). `--label LABEL` limits the whole report to stories carrying that
   GitHub label, so a session MAY answer a topic- or platform-scoped question without a raw `gh` search.

2. The session SHOULD start the first story it is allowed to take, in the order printed
   ([attempt](../attempt/GUIDE.md)). It MAY skip a story for a stated reason (for example, the operator
   assigned it elsewhere) and MUST NOT skip one without a stated reason. It MUST say which story it skipped
   and why.

## What the results mean

| Output | Means | Required action |
|---|---|---|
| `ready to start, in board order:` | Stories in Ready, not Blocked, issue open, no open blocked-by issue, no `Attempt:` line; ordered by Priority (the field's option order), then Rank. | The session SHOULD start the first one it may take. |
| `ready to start: none` | No story is eligible in that document. | The session MUST NOT start work there, and MUST read the lines below before reporting the document idle. |
| `in flight: N executing, M acceptance, …` | Open non-terminal stories per phase (the `counts` map). | An empty ready list with a non-empty `in flight` line means work is under way, not that the document is idle; the session MUST NOT report it idle. |
| `in progress: #N … (attempt ID by ACTOR since TIME)` | Another attempt holds that story. | The session MUST NOT start it; it MAY ask that attempt's owner or a person. |
| `blocked: #N … (blocked by #M)` or `(reason)` | A non-terminal story whose Health is Blocked or that has an open blocked-by issue. | The session MUST NOT start it; it MAY clear the blocker ([blocker](../blocker/GUIDE.md)) or report it. |
| `at risk: #N …` | A non-terminal story whose Health is At risk. | The session MAY start an at-risk Ready story; it SHOULD note the risk. |
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
$RECONCILE --ready --repo OWNER/REPO [--root ROOT] [--label LABEL] [--json]
```

Beneath it: each document Project's items and Work phase, Health, Priority and Rank values
(`ProjectV2.items`), each issue's open/closed state and `Attempt:` / `Ended attempts:` lines, and open
blocked-by issues (`Issue.blockedBy`). A session MAY read these directly to explain a surprising result, but
MUST NOT use its own reading to override `awa ready`.
