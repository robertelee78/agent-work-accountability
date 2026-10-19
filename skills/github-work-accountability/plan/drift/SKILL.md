---
name: plan-drift
description: Open when a planning source changed, the default branch moved, an issue's recorded source blob no longer matches, or a GitHub write failed or its result is uncertain. Teaches Source freshness, the reconciliation procedure, and the pending-operation journal.
---

# Drift and pending operations

## When to use

A session MUST reconcile when a planning source changes, the default branch changes, an attempt submits a
result, validation ends, delivery occurs, or it hands work off. This leaf covers the two cases that need
more than a normal apply: the source moved under the issues, and a write could not reach GitHub.

## Source drift

1. The session MUST compare each tracked source binding with the chosen default-branch ref:

   ```sh
   python3 "$SKILL_DIR/scripts/validate_extraction.py" MANIFEST.json --repo REPO --against origin/main
   ```

2. If the source revision no longer matches the issue's recorded blob, the session MUST set the story's
   Source freshness to Reconciliation needed. That does not mean the implementation became invalid: the
   session MUST determine which definition, design, validation or delivery facts the change actually
   affects.

3. The session MUST then reconcile, in this order:
   1. the session MUST read repository policy, the current source, all matching open and closed issues,
      relationships, fields, Project membership and relevant Git and evidence facts;
   2. the session MUST extract a proposed tree for the whole document and MUST match existing keys before
      proposing new ones;
   3. the session MUST run the deterministic source-binding, identity and dependency validation;
   4. the session MUST build a change plan that marks removed or materially changed requirements for
      reconciliation, and the plan MUST NOT delete their issues or history;
   5. the session MUST apply only the computed delta ([board/reconcile](../../board/reconcile/SKILL.md))
      and MUST re-read after an uncertain write instead of blindly retrying;
   6. the session MUST read the issues, fields, relationships and Project membership back and MUST compare
      them with the plan.

For an ADR, the session MUST complete the [write interlock](../adr/SKILL.md#adr-write-interlock) whichever
tool changed the file. An ADR write that leaves an old source blob on its issues is an incomplete accountability
operation.

## Pending operations

When GitHub is unavailable or a write result is uncertain, the session MUST persist the validated desired
state outside the product repository:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/agent-work-accountability/pending/HOST/OWNER/REPOSITORY/
```

- The manifest MUST be named by the SHA-256 digest of its canonical JSON; start and verified receipts MUST
  be appended to `journal.ndjson`.
- Credentials and copied issue prose MUST NOT be stored there.
- GitHub `clientMutationId` values are deterministic labels for diagnosis; a session MUST NOT treat them as a
  substitute for the read-before-write and read-after-write checks.
- A rerun MUST inventory GitHub again, compare it with the same desired state, and send only missing
  mutations. Before every resumed write, the session MUST enumerate the exact work-key marker and compare
  current GitHub state with the manifest; if it already matches, it MUST NOT write it.
- Conflicting desired states for one work key MUST stop reconciliation and MUST NOT be applied
  last-writer-wins.
- After a rate-limit failure (exit 75 with the reset time), the next run MUST recompute the delta rather
  than replay a blind write list.

The reconciler already persists its manifest before writing and resumes from it when the same command is
rerun. The session MUST follow this procedure when the session, rather than the reconciler, holds the
uncertain state.

## What the results mean

| Signal | Means | Required action |
|---|---|---|
| `validate_extraction.py --against` exits 1 naming the blob | The source moved since extraction. | The session MUST set Reconciliation needed and reconcile as above. |
| A reconcile exits 75 | A temporary condition (rate or write budget); nothing beyond the reported point was written. | The session MUST rerun the same command at or after the named time. |
| A reconcile exits 2 after some writes, or the connection dropped | The run stopped part-way. | The session MUST rerun the same command; it re-reads GitHub and applies only what is missing. |
| The final response cannot include a verified receipt | Reconciliation did not finish. | The session MUST report the exact failure and the retained pending operation. |
