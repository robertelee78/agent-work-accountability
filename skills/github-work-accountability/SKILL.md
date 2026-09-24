---
name: github-work-accountability
description: Use automatically when starting, continuing, blocking, completing, accepting, releasing, or handing off work in a repository that already has work-accountability managed issues; also use when asked to create epics or stories from planning documents, set up or sync a GitHub delivery board, reconcile a changed source, determine what is ready, or update phase, priority, blockers, evidence, acceptance, and delivery state.
metadata:
  version: "0.6.0"
---

# GitHub work accountability

Give people a trustworthy view of planned and active work without asking the user to maintain tickets or move cards. GitHub Issues hold durable work records. Each epic or independently managed workstream gets a repository-linked GitHub Project containing that epic and every direct native child story. Planning documents remain the authority for product intent; git, CI, and source-bound proofs remain the authority for implementation facts.

Use the smallest tracker schema that preserves these boundaries. Do not create a second writable copy of requirements or infer progress from changed files alone.

This skill is a client-independent protocol. Use the repository, Git, GitHub, and evidence interfaces available in the current environment. Do not require a particular agent client, orchestrator, hook system, or client memory. Client-specific discovery metadata and install paths do not change the work model or stored GitHub state.

## Choose the operation

- For phase meanings and transitions, read [the work model](references/work-model.md).
- For repository and Project setup, read [the GitHub model](references/github-model.md). Select the storage profile from observed GitHub capabilities; do not assume an organization-owned repository.
- For the desired-state manifest, efficient API path, and exact completion receipt, read [Project reconciliation](references/project-reconciliation.md).
- For decomposing or reconciling a plan, read [planning-source extraction](references/planning-source-extraction.md) and [the synchronization contract](references/synchronization-contract.md).
- When the source is an ADR or an ADR changes, read [ADR sources](references/adr-sources.md). Use the repository's native schema and lifecycle; treat indexes, memory, and orchestration systems as optional discovery aids rather than decision authority.

## Standing responsibility for managed work

Once a repository contains work-accountability managed issues, maintaining the matching issue and Project is part of executing that work. Do not wait for the user to request a status update. At the start of a work session, discover whether the assigned outcome maps to an existing stable work key. Before implementation begins, record the story-specific attempt-start event and reconcile it to Executing. Record blockers when discovered, candidate submission before Acceptance, independent verdicts before Release ready, and delivery evidence before Done. Reconcile again before handoff or the final response.

Only advance the exact story supported by the event. When the current work cannot be mapped unambiguously, leave phases unchanged and report the ambiguity rather than attaching an umbrella branch, commit, or session to several stories. Repository opt-in and the user's authorization to execute a managed story cover these routine, scoped tracker updates; Project creation, organization-wide schema changes, publication, and other separately protected actions retain their own authorization rules.

## Operating rules

1. Read repository instructions and the current planning source, including its lifecycle/status and revision. Inspect existing issues, relationships, Project membership, implementation, and evidence before writing. For ADRs, inspect canonical Git bytes and repository-native policy before invoking any ADR editor or lifecycle command.
2. Discover whether the repository owner is a user or organization, the configured GitHub actor, available field APIs, issue types, and the Project for the current epic. Use organization issue fields, Project-local fields, or repository labels according to [the GitHub model](references/github-model.md). A missing Project number, no suitable existing Project, or a Project for another epic does not make Projects unavailable. When Project APIs are writable and repository-scoped mutations are authorized, create or adopt one Project for this epic, link it to the repository, and include the epic plus every direct native child story. Never copy node or option IDs across owners or Projects.
3. Use one durable work identity per item. Put a repository-qualified stable key in an agent-managed issue block; retain the GitHub issue through renames, requirement edits, retries, and reopening.
4. Let a model propose decomposition, then run a deterministic source-binding and graph check before creating or changing issues. Review coverage separately; exact quotations prove provenance, not completeness.
5. Write idempotently. Re-read after uncertain writes, match the stable key across open and closed issues, and stop on duplicate identities. Preserve human prose, comments, history, and earlier evidence.
6. Update the tracker without prompting the user at meaningful facts: plan approved, design started or approved, attempt started, result submitted, acceptance verdict, delivery, blocker, reprioritization, or requirement drift. Do not turn routine chatter into status noise.
7. A branch or PR may touch many stories. It changes a story's phase only when the work or result is explicitly bound to that story. PR open, PR merged, file overlap, CI green, agent exit, and claim ownership are not completion verdicts.
   A branch or commit is also not an attempt-start event. Executing requires a durable event carrying a unique attempt ID, actor, start time, and the exact work key. Never reuse one commit as attempt evidence for multiple stories.
8. Reconcile at handoff, before claiming the assigned work complete, and after planning-source or default-branch changes. Tracker reconciliation is part of the task's completion contract, not optional follow-up. An ADR-writing tool does not satisfy this obligation: compare the resulting Git blob with every linked issue and complete the [ADR write interlock](references/adr-sources.md). If GitHub is unavailable, retain an idempotent pending operation outside tracked product files and replay it by work key and operation ID.
9. For writable GitHub Projects, do not improvise a chain of `gh project` and per-field GraphQL commands. After issue creation or update, write one desired-state manifest, run `scripts/reconcile_project.py` without `--apply` to inspect the delta, then run it with `--apply`. A run is successful only when its JSON receipt says `verified: true`; that gate includes the repository link, every managed issue, Project-side and issue-side membership, logical values, and the Lifecycle Kanban grouped by Work phase.

The tracker is independent of agent communication systems. An optional communication adapter may supply live claim, attempt, result, or blocker observations; it never owns product intent, durable phase, priority, acceptance, or delivery.

## GitHub access

Use `scripts/gh_account.py --user USER -- <gh arguments>` when a specific stored GitHub identity is required without changing the user's global `gh` account. Verify required scopes before mutation. Organization-wide issue-field or workflow changes affect every repository and require explicit authorization after presenting the exact schema change. Repository- or Project-scoped changes require authority for that repository or Project.

Use `scripts/reconcile_project.py --diagnose --user USER` to print the resolved skill path and digest, source revision or copy-install receipt, `gh` version, login, and remaining GraphQL budget. Existing agent sessions must restart after a skill update; an on-disk update does not change instructions already loaded into a running session.

Pass issue bodies and comments through files or structured API input. Never interpolate untrusted Markdown into shell commands. Read back issue fields, relationships, Project membership, views, and workflow settings after setup.

## Completion

The workflow is working only when agents can:

- extract a plan into complete, source-bound, nonduplicate epics and stories;
- when GitHub Projects are writable, link the epic's Project to the repository, show every native child story in its Lifecycle Kanban, and read back the repository link, exact epic membership, view configuration, and logical field values;
- compute which item is actually ready;
- bind a live attempt to exactly the work being executed;
- show blockers without moving the item out of its lifecycle phase;
- move a submitted candidate through Acceptance, Release ready, and its declared delivery boundary to Done; and
- detect and reconcile changed requirements without losing work history.

Repository labels satisfy completion only when an observed API, scope, or permission failure proves that Projects cannot be created or updated. Record that exact failure in the managed epic. The absence of a previously known Project or the presence of a differently scoped Project is not such proof.
