---
name: plan-adr
description: Open when an Architecture Decision Record is a planning source, or an ADR is about to change. Teaches discovering the repository's own ADR policy, inspect_adr.py, separating decision status from execution status, and the write interlock that keeps issues bound to the ADR's bytes.
---

# ADRs as planning sources

## When to use

A session MUST use this leaf when:

- an ADR is (or is about to become) the source of an epic/story tree; or
- an ADR that already has managed issues is being edited, renamed, superseded or deleted.

## Steps

1. **Discover the repository's ADR contract.** The session MUST look, in this order, at: repository
   instructions and repository-local ADR skills; repository validators, CI checks, templates and indexes;
   the named ADR and the related ADRs it explicitly references; and conventional `docs/adr/` or
   `docs/adrs/` locations only as a fallback. A session MUST NOT impose another tool's status vocabulary,
   filename scheme, template or supersession rules.

2. **Read the ADR from Git.** A semantic index, graph database, memory system, generated index or agent
   cache MAY help locate a file but MUST NOT establish its status, relationships or bytes. The session MUST
   read the ADR with the inspector:

   ```sh
   python3 "$SKILL_DIR/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo REPO --ref HEAD --require-status
   python3 "$SKILL_DIR/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo REPO --ref RECORDED_COMMIT --against origin/main
   python3 "$SKILL_DIR/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo REPO --working-tree --against HEAD
   ```

3. **Keep the clocks apart.** Without a repository mapping, the session MUST interpret decision status as
   follows:
   - *proposed* or *draft*: the ADR MAY be decomposed for design discussion but MUST NOT make stories
     execution-ready;
   - *accepted*: MAY supply a design-approval fact and MUST NOT be used as implementation, acceptance,
     release or Done evidence;
   - *implemented*: MAY identify a candidate implementation and MUST NOT be used as independent acceptance
     or delivery evidence;
   - *superseded*: MUST trigger reconciliation with the successor and any residual obligations, and MUST NOT
     close issues silently.

   When frontmatter and prose disagree, the session MUST stop and reconcile the ADR before changing GitHub.
   When the repository requires fields or validators beyond what the inspector understands, the
   repository's own validator MUST remain the gate.

4. **Extract.** The session MUST continue in [plan/extract](../extract/SKILL.md) with `"kind": "adr"` and the
   commit and blob just read.

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | `"ok": true`, `metadata.decision_status.normalized` (for example `accepted`), `execution_status` | The ADR's declared metadata, raw values kept beside normalized spellings. | The session MUST map status per step 3 and MUST NOT derive a Work phase from it directly. |
| 1 | `errors: [...]` | Contradictory or missing declarations (with `--require-status`, a missing status). | The session MUST fix the ADR through the repository's own workflow. |
| 1 | (with `--against`) the blob at the comparison ref differs | The ADR changed after the recorded commit. | The session MUST run the write interlock below. |

The inspector accepts byte-zero YAML frontmatter, declared Markdown metadata, or both, and fails on
contradictory declarations. It reports `relations` (`supersedes`, `superseded_by`, `amends`, `amended_by`,
`depends_on`, `related`) as declared.

## ADR write interlock

Every ADR editor, command, hook and agent is an external writer. An ADR mutation and tracker reconciliation
form one accountability operation even when different tools perform them. The session responsible for the
overall task MUST complete all six steps before claiming the ADR update complete:

1. Before editing, the session MUST inspect the committed ADR, its repository policy, its linked work keys
   and the existing GitHub items.
2. The session MUST make the change through the repository's own workflow, keeping required relationships,
   dates, indexes and validation rules.
3. The session MUST inspect the working-tree bytes against the prior commit and MUST run every
   repository-required ADR validator.
4. Once the change has an immutable commit, the session MUST regenerate and validate the extraction against
   that commit.
5. The session MUST update the existing issues by stable work key: replace the source excerpt and binding,
   reconcile added, changed, removed or superseded obligations, and keep history.
6. The session MUST read GitHub back. The operation is complete only when each affected item records the
   new commit and blob and Source freshness is Current. If GitHub cannot be updated, the session MUST set or
   queue Reconciliation needed ([plan/drift](../drift/SKILL.md)) and MUST NOT claim synchronization.

At the start and end of work, a session SHOULD compare every tracked source binding with the default
branch. A changed, moved or deleted ADR is a reconciliation event whichever tool changed it. Drift MUST NOT
be repaired by rewriting the ADR from issue text.

## Renames, deletion and supersession

- A file rename with the same logical ADR ID MUST update the source path and binding on the existing epic
  and stories.
- A material requirement edit MUST keep stable work keys where outcomes keep their identity; new keys MUST
  be minted only for genuinely new outcomes.
- A deleted ADR is not proof that its delivered behavior should be removed. The session MUST keep history
  and MUST obtain an explicit replacement, cancellation or retirement decision.
- A superseding ADR MUST link the old and new sources. Unfinished obligations MUST be reconciled
  explicitly; a cache-inferred edge MUST NOT decide their disposition.

## Dropping down a level

When the inspector cannot run or its result is in doubt, the session SHOULD read the history directly:

```sh
git -C REPO log --format='%H %ad' -- docs/adr/ADR-001-example.md     # every commit that touched it
git -C REPO rev-parse REF:docs/adr/ADR-001-example.md                # its blob at REF
git -C REPO diff RECORDED_COMMIT REF -- docs/adr/ADR-001-example.md  # what changed since extraction
```
