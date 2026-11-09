---
name: release-progress
description: Open to report how far a release milestone or a document has progressed, before a status update or handoff, or before closing a release. Teaches awa release status (members with phase, health, attempt and blockers, plus progress totals), awa ready for one document, and what the counts do and do not prove.
---

# Report progress on a release or a document

A session working this leaf MUST also follow the [release domain rules](../GUIDE.md).

## When to use

- A person asks how far a release, a milestone or a planning document has got, or what is holding it up.
- Before a status report, a handoff or `awa release close` ([close](../close/GUIDE.md)).

A session MUST answer from these read-only commands, and MUST NOT count cards on a board by eye or infer
progress from branches, pull requests or changed files.

## Steps

1. **A release.** The session MUST run:

   ```sh
   awa release status TAG [--repo OWNER/REPO]
   ```

   Observed receipt (simulator; members trimmed to the one in progress):

   ```json
   {"action": "status", "issues": {"closed": 2, "open": 1},
    "members": [{"attempt": {"actor": "github:builder", "id": "att-c", "since": "2026-09-20T10:00:00Z"},
                 "blocked_by": [5], "blocked_reason": null, "document_root": 1, "ended_attempts": 0,
                 "health": "On track", "joins_at_close": false, "kind": "story", "number": 4,
                 "phase": "Executing", "state": "open", "title": "Sync docs"}],
    "milestone_state": "open", "notes": [], "problems": [],
    "progress": {"at_risk": [2], "blocked": [4],
                 "documents": [{"phases": {"Executing": 1, "Release ready": 2}, "project": 1, "root": 1,
                                "title": "vox — ADR-30: Sync engine"}],
                 "epics": [], "health": {"At risk": 1, "On track": 2}, "in_progress": [4],
                 "needs_decision": [], "phases": {"Executing": 1, "Release ready": 2}},
    "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.1.0"}
   ```

   A release can span several planning documents; `progress.documents` gives each one's phase counts and
   `members[].document_root` names the document of each story.

2. **One document.** The session MUST run `awa ready` with that document's root
   ([execute/pick](../../execute/pick/GUIDE.md)):

   ```sh
   awa ready --root ROOT [--repo OWNER/REPO] [--json]
   ```

   Observed output (simulator, same document):

   ```text
   vox — ADR-30: Sync engine (Project #1, root #1)
     ready to start, in board order:
       #5 Fix flaky CI (no priority)
     in flight: 1 ready, 1 executing, 3 release ready
     in progress: #4 Sync docs (attempt att-c by github:builder since 2026-09-20T10:00:00Z)
     blocked: #4 Sync docs (blocked by #5)
     at risk: #2 Sync core
   ```

   The document counts can differ from the release counts above: a document holds stories planned for other
   releases (here #5, delivered by merge) and `next` stories without a release milestone (here #6).

3. **A topic or platform.** The session MAY add `--label LABEL` to `awa ready` to limit the document report to
   stories carrying that label.

4. **One story.** When a count needs explaining, the session SHOULD read that story's records and evidence
   with [execute/evidence](../../execute/evidence/GUIDE.md).

5. **Reporting.** A report MUST name the release or document, the command and the time it ran, the phase
   counts, and each story listed under `in_progress`, `blocked`, `at_risk` and `needs_decision` with its
   number. It MUST quote `problems` and `notes` rather than drop them.

## What the results mean

| Field | Means | Does not mean |
|---|---|---|
| `issues` (`open`, `closed`) | The milestone's actual open and closed issues. | That closed work is delivered. A story closes at Release ready with the `awaiting-release` label; it is Done only after `awa release close`. |
| `members[].kind` | `story` or `epic`. An epic placed in the release milestone reports only its `health` and its `epic_progress` rollup (`7/12 Done · 1 blocked …`) and is listed in `progress.epics`. | That the epic's stories are in this release: each story is a member only through its own release milestone or `next` delivery. |
| `members[].phase` | The story's Work phase on its document Project. | Progress from code: phases move only on recorded evidence ([model](../../model/GUIDE.md)). |
| `progress.phases`, `progress.health` | Stories per Work phase and per Health; `no phase` counts stories missing from their board. Epics are not counted. | — |
| `progress.in_progress`, `members[].attempt` | Open stories with an `Attempt:` record: someone holds them. | That the holder is active now; the record has a start time, not a heartbeat. |
| `progress.blocked`, `members[].blocked_by` / `blocked_reason` | In-flight stories whose Health is Blocked or that have an open blocked-by issue. | That the story left its phase: blockers never move a phase. |
| `progress.at_risk` | In-flight stories whose Health is At risk. | — |
| `progress.needs_decision` | Ready stories with three or more ended attempts and none open: a person MUST decide before another attempt ([execute/attempt](../../execute/attempt/GUIDE.md)). | — |
| `members[].joins_at_close` | A `release next` story whose landing commit this release contains; it joins at close. | — |
| `awa ready`: `in flight: …` | Open non-terminal stories per phase in that document, plus accepted work awaiting release. | That anything is being worked: only `in progress` lines are held. An empty ready list with in-flight counts is not idle. |

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | the receipt | Read only; nothing was written. | The session MUST report from it as in step 5. |
| 0 | `problems`: `#N is in milestone TAG but is not a managed work item; remove it or manage it` | The release cannot close cleanly, and that issue has no phase to report. | The session MUST report it; it MAY manage the issue ([plan/issues](../../plan/issues/GUIDE.md)) or move it out of the milestone with the operator's agreement. |
| 0 | `notes`: `#N has no Work phase on its document Project; reconcile document #ROOT to place it` | The story is in the milestone but not yet on its board, so its phase is unknown. | The session MUST report it as unknown and SHOULD reconcile that document ([board/reconcile](../../board/reconcile/GUIDE.md)). |
| 0 | `notes`: `GitHub's counter for milestone TAG shows …` | GitHub's cached milestone counter is stale. | The session MUST trust `issues` and MUST NOT "fix" the counter by hand. |
| 2 | ``reconciliation failed: there is no release milestone v9.9.9; create it with `awa release plan v9.9.9` `` | No release milestone has that title. | The session MUST check the tag; it MAY create the milestone ([plan](../plan/GUIDE.md)). |
| 75 | `temporary failure: …` | A retryable GitHub failure. | The session MUST rerun the same command. |

## Dropping down a level

`awa release status` reads, and never writes:

- the milestone's issues: `gh api "repos/OWNER/REPO/issues?state=all&milestone=NUMBER&per_page=100"`;
- each member's document Project item and its `Work phase` and `Health` values (`ProjectV2.items`);
- each member's same-repository blockers (`Issue.blockedBy`);
- the `Attempt:`, `Ended attempts:` and `Blocked reason:` records in each managed block:
  `gh api repos/OWNER/REPO/issues/N --jq .body`.

A session MAY read these directly to explain a surprising result, and MUST NOT use its own reading to
override the receipt.
