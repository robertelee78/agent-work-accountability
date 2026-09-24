# ADR sources

Use this adapter when a planning source is an Architecture Decision Record. ADRs vary across repositories; the repository's files and policy define their schema and lifecycle.

## Authority and discovery

Discover the ADR contract in this order:

1. repository instructions and repository-local ADR skills;
2. repository validators, CI checks, templates, and indexes;
3. the named ADR and related ADRs it explicitly references; and
4. conventional `docs/adr/` or `docs/adrs/` locations only as a fallback.

Read the canonical file directly from Git. A semantic index, graph database, memory system, generated index, or agent cache may help locate a file but cannot establish its status, relationships, or current bytes. Never impose another tool's status vocabulary, filename scheme, template, or supersession rules on a repository.

Inspect the source without third-party dependencies:

```sh
accountability_skill=/path/to/github-work-accountability
python3 "$accountability_skill/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo /path/to/repository --ref HEAD --require-status
python3 "$accountability_skill/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo /path/to/repository --ref RECORDED_COMMIT --against origin/main
python3 "$accountability_skill/scripts/inspect_adr.py" docs/adr/ADR-001-example.md --repo /path/to/repository --working-tree --against HEAD
```

The inspector accepts byte-zero YAML frontmatter, declared Markdown metadata, or both. It preserves raw repository values, reports their normalized spelling for comparison, keeps decision status separate from execution status, and fails on contradictory declarations. Its output does not assign a Work phase; that requires repository policy and evidence.

## Separate the clocks

An ADR decision lifecycle and a story delivery phase answer different questions:

- **Decision status** says whether the architectural or product decision is proposed, approved, implemented, superseded, or in another repository-defined state.
- **Execution status** is optional ADR prose about implementation progress. Preserve it as a source claim; verify it against Git, tests, acceptance, and delivery evidence.
- **Work phase** is the tracker verdict defined in [the work model](work-model.md).
- **Source freshness** says whether the managed issues still reflect the current ADR bytes.

Do not translate a status word mechanically. In the absence of a repository mapping:

- proposed or draft ADRs may be decomposed for design discussion but cannot make stories execution-ready;
- accepted may supply a design-approval fact, but it is not implementation, acceptance, release, or Done evidence;
- implemented may identify a candidate implementation, but it is not independent acceptance or delivery evidence; and
- superseded triggers reconciliation with the successor and any residual obligations rather than silent issue closure.

When frontmatter and prose differ, stop and reconcile the ADR before changing GitHub. When a repository requires fields or validators beyond what the inspector understands, its native validator remains the gate.

## ADR write interlock

Treat every ADR editor, command, hook, and agent as an external writer. An ADR mutation and tracker reconciliation form one accountability operation even when different tools perform them.

1. Before editing, inspect the committed ADR, its repository policy, its linked work keys, and the existing GitHub items.
2. Make the ADR change through the repository-native workflow. Preserve required relationships, dates, indexes, and validation rules.
3. Inspect working-tree bytes against the prior commit and run every repository-required ADR validator.
4. Once the change has an immutable commit, regenerate and validate the extraction against that commit.
5. Update the existing issues by stable work key. Replace the generated source excerpt and source binding, reconcile added, changed, removed, or superseded obligations, and retain history.
6. Read GitHub back. The operation is complete only when each affected item records the new commit/blob and Source freshness is Current. If GitHub cannot be updated, set or queue Reconciliation needed with an idempotent operation; do not claim synchronization.

This interlock does not require the editor to know how GitHub Projects work. It requires the agent responsible for the overall task to invoke the tracker adapter before claiming the ADR update complete.

At the start and end of work, compare every tracked source binding with the chosen default-branch ref. A changed, moved, or deleted ADR is a reconciliation event regardless of which tool changed it. Never repair drift by rewriting the ADR from issue text: the ADR owns Why, What, scope, and acceptance intent; GitHub owns operational work state and evidence.

Installing this skill does not intercept an unrelated tool that edits files without loading repository guidance. Repository adoption must add a tracked, client-neutral instruction that every ADR mutation runs this reconciliation, and may add a CI check requiring an affected work item to name the new source blob. The instruction and check are enforcement adapters around this protocol; they must not introduce another copy of the requirements or a second lifecycle.

## Renames, deletion, and supersession

- A file rename with the same logical ADR ID updates the source path and binding on the existing epic and stories.
- A material requirement edit retains stable work keys where outcomes retain identity; create new keys only for genuinely new outcomes.
- A deleted ADR is not proof that its delivered behavior should be removed. Preserve history and require an explicit replacement, cancellation, or retirement decision.
- A superseding ADR links the old and new sources. Reconcile unfinished obligations explicitly; do not let a cache-inferred edge decide their disposition.
