---
name: github-work-accountability
description: Turn planning documents such as ADRs, PRDs, and design proposals into agent-maintained GitHub epics and stories, then keep phase, priority, blockers, evidence, and delivery state accurate across repositories.
---

# GitHub work accountability

Give people a trustworthy view of planned and active work without asking the user to maintain tickets or move cards. GitHub Issues hold durable work records. A repository-focused GitHub Project presents that repository's execution view when Projects are available. Planning documents remain the authority for product intent; git, CI, and source-bound proofs remain the authority for implementation facts.

Use the smallest tracker schema that preserves these boundaries. Do not create a second writable copy of requirements or infer progress from changed files alone.

This skill is a client-independent protocol. Use the repository, Git, GitHub, and evidence interfaces available in the current environment. Do not require a particular agent client, orchestrator, hook system, or client memory. Client-specific discovery metadata and install paths do not change the work model or stored GitHub state.

## Choose the operation

- For phase meanings and transitions, read [the work model](references/work-model.md).
- For repository and Project setup, read [the GitHub model](references/github-model.md). Select the storage profile from observed GitHub capabilities; do not assume an organization-owned repository.
- For decomposing or reconciling a plan, read [planning-source extraction](references/planning-source-extraction.md) and [the synchronization contract](references/synchronization-contract.md).

## Operating rules

1. Read repository instructions and the current planning source, including its lifecycle/status and revision. Inspect existing issues, relationships, Project membership, implementation, and evidence before writing.
2. Discover whether the repository owner is a user or organization, the configured GitHub actor, available field APIs, issue types, and any Project dedicated to the repository. Use organization issue fields, Project-local fields, or repository labels according to [the GitHub model](references/github-model.md). Never copy node or option IDs across owners or Projects.
3. Use one durable work identity per item. Put a repository-qualified stable key in an agent-managed issue block; retain the GitHub issue through renames, requirement edits, retries, and reopening.
4. Let a model propose decomposition, then run a deterministic source-binding and graph check before creating or changing issues. Review coverage separately; exact quotations prove provenance, not completeness.
5. Write idempotently. Re-read after uncertain writes, match the stable key across open and closed issues, and stop on duplicate identities. Preserve human prose, comments, history, and earlier evidence.
6. Update the tracker at meaningful facts: plan approved, design started or approved, attempt started, result submitted, acceptance verdict, delivery, blocker, reprioritization, or requirement drift. Do not turn routine chatter into status noise.
7. A branch or PR may touch many stories. It changes a story's phase only when the work or result is explicitly bound to that story. PR open, PR merged, file overlap, CI green, agent exit, and claim ownership are not completion verdicts.
8. Reconcile at handoff and after planning-source or default-branch changes. If GitHub is unavailable, retain an idempotent pending operation outside tracked product files and replay it by work key and operation ID.

The tracker is independent of agent communication systems. Vox or another channel may supply live claim, attempt, result, or blocker observations through an adapter; it never owns product intent, durable phase, priority, acceptance, or delivery.

## GitHub access

Use `scripts/gh_account.py --user USER -- <gh arguments>` when a specific stored GitHub identity is required without changing the user's global `gh` account. Verify required scopes before mutation. Organization-wide issue-field or workflow changes affect every repository and require explicit authorization after presenting the exact schema change. Repository- or Project-scoped changes require authority for that repository or Project.

Pass issue bodies and comments through files or structured API input. Never interpolate untrusted Markdown into shell commands. Read back issue fields, relationships, Project membership, views, and workflow settings after setup.

## Completion

The workflow is working only when agents can:

- extract a plan into complete, source-bound, nonduplicate epics and stories;
- compute which item is actually ready;
- bind a live attempt to exactly the work being executed;
- show blockers without moving the item out of its lifecycle phase;
- move a submitted candidate through Acceptance, Release ready, and its declared delivery boundary to Done; and
- detect and reconcile changed requirements without losing work history.
