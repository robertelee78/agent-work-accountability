---
name: execute-attempt
description: Open before implementation begins on a story (Ready → Executing), when an attempt ends without a candidate, or when another attempt already holds the story. Teaches the attempt-start comment, attempt evidence, the one-open-attempt rule, attempt_release, and ended-attempt counting.
---

# Start or end an attempt

## Start an attempt (Ready → Executing)

1. **Story choice.** The session MUST take the story from `awa ready` ([pick](../pick/GUIDE.md)); its phase MUST be
   Ready.
2. **Attempt-start comment.** The session MUST post it on the story's own issue before
   implementation begins:

   ```text
   <!-- work-accountability:event key=OWNER/REPO:DOC:ITEM event=attempt-started actor=impl-1 time=2026-10-19T12:00:00Z -->
   Attempt alpha-1 started.
   ```

   ```sh
   gh api repos/OWNER/REPO/issues/N/comments -X POST --input comment.json --jq .html_url
   ```

3. **Attempt evidence.** The session MUST draft, set the story to Executing, and add:

   ```json
   "work_phase": "Executing",
   "attempt": {"ref": "https://github.com/OWNER/REPO/issues/N#issuecomment-ID", "ref_kind": "issue_comment",
               "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID",
               "attempt_id": "alpha-1", "actor": "impl-1", "started_at": "2026-10-19T12:00:00Z",
               "state": "active"}
   ```

   - `attempt_id` MUST be one word and unique in the document; `actor` MUST be one line.
   - `ref_kind` MUST be `issue_comment`, `communication_event` or `tracker_event`; `started_at` MUST be
     timezone-qualified RFC3339.
   - The `ref` MUST name the durable attempt-start event. The session MUST NOT use a branch, pull request,
     commit, changed file, claim or agent process as the `ref`; the reconciler rejects GitHub commit URLs
     here.

4. **Dry run and application.** The session MUST dry run, read the plan, then apply. Expected plan:

   ```text
   planned_mutations: ["set #3 Work phase=Executing", "bind issue #3 to Project fields"]
   ```

   After the apply, the managed block shows `Attempt: alpha-1 by impl-1 since 2026-10-19T12:00:00Z` and
   `awa ready` lists the story under `in progress`. The session MUST NOT begin implementation until this
   apply returns `"verified": true`.

## One open attempt per story

While a story is Executing, its `Attempt:` line names who holds it. Before writing anything, awa refuses a
manifest that puts the story in Executing, Acceptance, Release ready or Done with a different attempt ID, or
that moves it to an earlier phase or Won't do without `attempt_release`. Observed:

```text
reconciliation failed: attempts (nothing was written): #3 is being worked by attempt alpha-1 (impl-1, since
2026-10-19T12:00:00Z), so attempt alpha-2 cannot take it over. Ask whoever runs alpha-1 or a person; to end
it, comment on #3 why and add attempt_release evidence naming alpha-1
```

When awa names another attempt, the session MUST stop and ask the person or whoever runs that attempt. The
session MUST NOT work around the refusal. A manifest that restates the open attempt's ID keeps the
holder as first recorded.

A claim, renewal or handoff between agents changes ownership, not progress. The session MUST record a
handoff as the old attempt's `attempt_release` together with the new attempt's start, in one manifest, and
MUST NOT move the Work phase.

## End an attempt without a candidate (Executing → Ready)

Only the attempt's owner or a person MAY end an open attempt early; any other session MUST NOT end it.
awa never releases one on its own, however old it is.

1. The session MUST comment on the story's issue why the attempt ended.
2. The session MUST draft, set `work_phase` to `Ready`, remove `attempt`, and add:

   ```json
   "attempt_release": {"ref": "https://github.com/OWNER/REPO/issues/N#issuecomment-ID",
                       "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID",
                       "attempt_id": "beta-1", "author": "impl-3", "reason": "gate failed twice"}
   ```

   `attempt_id` MUST name the attempt being ended; `reason` MUST be one line.
3. The session MUST dry run, then apply. The `Attempt:` line goes, and `Ended attempts: 1` appears.

Moving the story back without `attempt_release` is refused. Observed:

```text
reconciliation failed: attempts (nothing was written): #4 is being worked by attempt beta-1 (impl-3, since
2026-10-19T16:00:00Z), so it cannot move to Ready. Ask whoever runs beta-1 or a person; to end it, comment on
#4 why and add attempt_release evidence naming beta-1
```

A retry MUST start a new `attempt_id` with a new start event.

## Ended attempts

`Ended attempts: N` counts attempts that ended without acceptance: each release, and each candidate sent
back from Acceptance or Release ready to an earlier phase (once per attempt). At three ended attempts, a
person MUST decide before another attempt starts; the receipt notes this, and `awa ready` lists the story
under `needs_decision`. The session MUST ask before starting another attempt. A Won't do decision ends
the open attempt by itself and is not counted as failed.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | `"verified": true`; the `Attempt:` line is recorded. | The session MAY begin implementation. |
| 2 | `… cannot take it over …` | Another attempt holds the story. | The session MUST stop and ask. |
| 2 | `… cannot move to Ready …` | Moving back without releasing the attempt. | The session MUST add `attempt_release`, or ask the attempt's owner. |
| 2 | `reconciliation failed: these items changed since the manifest was drafted …` | Someone moved the story after the draft. | The session MUST re-draft and retry. |

If a person moves a card to an earlier phase on the board, no manifest made that move: awa keeps the
`Attempt:` line, notes it, and `awa ready` keeps the story out of the ready list until the attempt is
released. Reaching Acceptance or later with the recorded attempt removes the line; awa never removes it on
age. Stories past Executing before 0.10.11 have no `Attempt:` line.
