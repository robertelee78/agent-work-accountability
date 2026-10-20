---
name: execute-blocker
description: Open when a condition threatens or prevents a story's next transition, when a dependency between stories must be recorded, or when a blocker clears. Teaches Health, blocked_by, blocked_reason, and how awa writes GitHub blocked-by links.
---

# Blockers and dependencies

A session working this leaf MUST also follow the [execute domain rules](../GUIDE.md).

A blocker changes Health, never Work phase: a blocked story stays in the phase it reached. A session MUST NOT
move a story to an earlier phase to express a blocker.

## Steps

1. The session MUST draft, then set the story's fields for the case:
   - **At risk** (meaningful work can continue): `"health": "At risk"`.
   - **Blocked by another story:** `"health": "Blocked"` and
     `"blocked_by": ["OWNER/REPO:DOC:OTHER"]` (each entry MUST be the work key of a managed issue in the
     same repository).
   - **Blocked by something else** (a decision, an outside system): `"health": "Blocked"` and a one-line
     `"blocked_reason"` that names the condition, the responsible dependency or decision, and the time it
     became blocking.
   - **Cleared:** `"health": "On track"`, and `"blocked_by": []` or no `blocked_reason`.
2. The session MUST dry run, read the plan, then apply. A blocked dependency produced, for example:

   ```text
   planned_mutations: ["update section progress in the By section headers", "set #1 Health=Blocked",
     "set #1 Progress=1/3 Done · 1 blocked", "set #2 Health=Blocked", "set #4 Health=Blocked",
     "block #4 by #5", "post Project status OFF_TRACK"]
   ```

Epics take the worst Health of their stories, and the Project's status update follows.

## Rules

- The session MUST NOT record B as blocked by A unless B needs A's output (code, data, decision or release).
  Two stories touching the same files are a merge-order question; the session MUST NOT record them as a
  blocker.
- A Blocked story MUST have an open blocker or a `blocked_reason`.
- awa writes `blocked_by` as native GitHub blocked-by links and removes only links it added (recorded as
  `Blocked by:`). Links people add are kept and listed in the receipt notes. Omitting `blocked_by` leaves
  links as awa last recorded them; to remove awa's links, the session MUST pass `"blocked_by": []`.
- Clearing a condition changes Health; the session MUST NOT advance the phase as part of clearing it.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | Health and links written; `Blocked by:` / `Blocked reason:` recorded. | The session MUST report the receipt. |
| 2 | `reconciliation failed: blocked-by (nothing was written): #4 is Blocked but has no open blocked-by issue; add blocked_by or blocked_reason` | Blocked without a cause. | The session MUST add one. |
| 0 | a note that an On track story has open blockers | A blocker is still open. | The session SHOULD decide whether Health ought to be Blocked and MUST NOT ignore the note silently. |

## Dropping down a level

GraphQL `addBlockedBy` and `removeBlockedBy` write the links (they need at least triage permission);
`Issue.blockedBy` reads them. A session MAY read them directly:

```sh
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){blockedBy(first:50){nodes{number state}}}}}' -F o=OWNER -F r=REPO -F n=N
```

Dependencies hold up to 50 issues per relationship. Projects show a blocked icon but cannot filter on
dependencies, which is why Health stays a Project field.
