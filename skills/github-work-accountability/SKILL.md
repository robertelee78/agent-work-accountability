---
name: github-work-accountability
description: Use for substantive planning, design, implementation, testing, review, release, or handoff work in a GitHub repository. Detect and maintain existing work-accountability issues and Projects automatically, and turn ADRs, PRDs, or design documents into epics, stories, and lifecycle Kanban boards when requested.
metadata:
  version: "0.10.7"
---

# GitHub work accountability

Give people a trustworthy view of planned and active work without asking the user to maintain tickets or move cards. GitHub Issues hold durable work records. Each planning document (PRD, ADR, proposal) or independently managed workstream gets one repository-linked GitHub Project containing its root epic and every sub-issue below it. Epics may nest up to three levels (root → section → subsection → story); Work phase lives only on leaf stories, and epics show rollups. Planning documents remain the authority for product intent; git, CI, and source-bound proofs remain the authority for implementation facts.

Use the smallest tracker schema that preserves these boundaries. Do not create a second writable copy of requirements or infer progress from changed files alone.

This skill is a client-independent protocol. Use the repository, Git, GitHub, and evidence interfaces available in the current environment. Do not require a particular agent client, orchestrator, hook system, or client memory. Client-specific discovery metadata and install paths do not change the work model or stored GitHub state.

## Choose the operation

- For phase meanings and transitions, read [the work model](references/work-model.md).
- For repository and Project setup, read [the GitHub model](references/github-model.md). Select the storage profile from observed GitHub capabilities; do not assume an organization-owned repository.
- For the desired-state manifest, efficient API path, exact completion receipt, and merging older per-epic Projects into one document Project, read [Project reconciliation](references/project-reconciliation.md).
- For decomposing or reconciling a plan, read [planning-source extraction](references/planning-source-extraction.md) and [the synchronization contract](references/synchronization-contract.md).
- When the source is an ADR or an ADR changes, read [ADR sources](references/adr-sources.md). Use the repository's native schema and lifecycle; treat indexes, memory, and orchestration systems as optional discovery aids rather than decision authority.

## Standing responsibility for managed work

At the start of substantive work in a GitHub repository, run `awa status --json` once before editing. This is a read-only REST check and does not consume the GraphQL Projects budget. If `managed` is false and the user did not request tracker setup, stop the accountability workflow and continue the assigned work. If `managed` is true, find the one stable work key that matches the assigned outcome and treat maintenance of its issue and its document's Project as part of execution. If `awa` is unavailable, perform the equivalent read-only issue-body search for `work-accountability:key` before deciding the repository is unmanaged.

Do not wait for the user to request a status update. Before implementation begins, record the story-specific attempt-start event and reconcile it to Executing. Record blockers when discovered, candidate submission before Acceptance, independent verdicts before Release ready, and delivery evidence before Done. Reconcile again before handoff or the final response. Include the work key, resulting phase and health, and verified reconciliation receipt in the final response; if reconciliation could not finish, report the exact failure and retained pending operation instead of silently omitting tracker state.

Only advance the exact story supported by the event. When the current work cannot be mapped unambiguously, leave phases unchanged and report the ambiguity rather than attaching an umbrella branch, commit, or session to several stories. Repository opt-in and the user's authorization to execute a managed story cover these routine, scoped tracker updates; Project creation, organization-wide schema changes, publication, and other separately protected actions retain their own authorization rules.

## Operating rules

1. Read repository instructions and the current planning source, including its lifecycle/status and revision. Inspect existing issues, relationships, Project membership, implementation, and evidence before writing. For ADRs, inspect canonical Git bytes and repository-native policy before invoking any ADR editor or lifecycle command.
2. Discover whether the repository owner is a user or organization, the configured GitHub actor, available field APIs, issue types, and the Project for the current planning document. Use organization issue fields, Project-local fields, or repository labels according to [the GitHub model](references/github-model.md). A missing Project number, no suitable existing Project, or a Project for another document does not make Projects unavailable. When Project APIs are writable and repository-scoped mutations are authorized, create or adopt one Project for the document's root epic, link it to the repository, and include every sub-issue below the root. Never give a section epic its own Project; if older per-epic Projects exist for the document, merge them. Never copy node or option IDs across owners or Projects.
3. Use one durable work identity per item. Put a repository-qualified stable key in an agent-managed issue block; retain the GitHub issue through renames, requirement edits, retries, and reopening.
4. Let a model propose decomposition, then run a deterministic source-binding and graph check before creating or changing issues. Review coverage separately; exact quotations prove provenance, not completeness.
5. Write idempotently. Re-read after uncertain writes, match the stable key across open and closed issues, and stop on duplicate identities. Preserve human prose, comments, history, and earlier evidence.
6. Update the tracker without prompting the user at meaningful facts: plan approved, design started or approved, attempt started, result submitted, acceptance verdict, delivery, blocker, reprioritization, or requirement drift. Do not turn routine chatter into status noise.
7. A branch or PR may touch many stories. It changes a story's phase only when the work or result is explicitly bound to that story. PR open, PR merged, file overlap, CI green, agent exit, and claim ownership are not completion verdicts.
   A branch or commit is also not an attempt-start event. Executing requires a durable event carrying a unique attempt ID, actor, start time, and the exact work key. Never reuse one commit as attempt evidence for multiple stories.
8. Reconcile at handoff, before claiming the assigned work complete, and after planning-source or default-branch changes. Tracker reconciliation is part of the task's completion contract, not optional follow-up. An ADR-writing tool does not satisfy this obligation: compare the resulting Git blob with every linked issue and complete the [ADR write interlock](references/adr-sources.md). If GitHub is unavailable, retain an idempotent pending operation outside tracked product files and replay it by work key and operation ID.
9. For writable GitHub Projects, do not improvise a chain of `gh project` and per-field GraphQL commands. After issue creation or update, run `scripts/reconcile_project.py --draft` to read the document's current state, change only what your event changes, run it without `--apply` to inspect the delta, then run it with `--apply`. Never hand-edit the manifest's `observed` values: they let the reconciler refuse a write that would undo another agent's newer update. A run is successful only when its JSON receipt says `verified: true`; that gate includes the repository link, every managed issue, Project-side and issue-side membership, logical values and rollups, and a check through GitHub's own filter engine that the Lifecycle board shows every story and no epic.

The tracker is independent of agent communication systems. An optional communication adapter may supply live claim, attempt, result, or blocker observations; it never owns product intent, durable phase, priority, acceptance, or delivery.

## Releases

Each release has one **release milestone** in the repository, titled exactly as its tag (`v0.2.10`). Say "release milestone", never just "milestone", so it is not confused with ADR milestones or acceptance gates. The milestone records which release a story is aimed at; a published, non-draft, non-pre-release GitHub Release is the proof of delivery; Work phase stays the only lifecycle clock.

- **Delivery.** Every story declares `delivery`: `release` with a tag (`v0.2.10`) when the operator has said which release it ships in, otherwise `release` with `next` (it joins the first full release that contains its change); `merge` when merging to the default branch is the delivery; `other` for anything else. `--draft` proposes this from the issue's boundary text; check each proposal.
- **Landing commit.** When a release- or merge-delivered story reaches Release ready, record `integration` evidence: the commit that landed on the default branch (and its PR, if any), bound to the accepted candidate. Put a `Work-item: #N` trailer on the commits you land. Don't use GitHub closing keywords ("Fixes #N") on release-delivered stories: GitHub would close the issue at merge, before the release ships.
- **Moving a story to another release** needs a one-line `milestone_change_reason` in the manifest; awa posts it on the issue once. Never move milestones in GitHub's UI: awa treats that as drift and stops.
- **Due dates.** A release milestone carries a due date only when the operator states one. When you agree a date with the operator in conversation, record it: `awa release plan v0.3.1 --due 2026-10-15 --due-source "agreed with <who> on <date>" --update`.
- **Shipping.** After the GitHub Release is published, run `awa release close TAG`. It refuses while any story in the release is not Release ready or Done (`--move-open-to NEXT_TAG` moves the unfinished ones), then moves Release ready stories to Done, removes their `awaiting-release` label, adds the delivered work to the release notes, and closes the milestone. Never close release milestones, or open or close managed issues, by hand.
- If work lands on a release integration branch before the default branch, add `"branch": "integrate/v0.2.10"` to the `integration` evidence; `awa release close` then requires the release tag to contain it.
- `awa release backfill` proposes, read-only, which past release each recorded story shipped in; apply only the rows the operator accepts with `--accept FILE`.
- **Won't do.** When the decider rules a story out, record the decision as a comment on its issue, then set `"work_phase": "Won't do"` with `decision` evidence (`ref` to that comment, `author`, `reason`). awa closes the issue as not planned, takes it out of its release milestone, and counts it apart from progress; moving it back reopens it. Never leave ruled-out work in Backlog to stand for this.
- **Closed means accepted.** awa closes a story's issue when the story reaches Release ready (or Done), so a release milestone's progress bar shows accepted work before the release ships. Accepted release-delivered stories carry an `awaiting-release` label until the release ships. If a story falls back below Release ready, awa reopens its issue with a short comment. A release is never a planning document: don't create a Project for one.

## Evidence records

- Mark every evidence comment you post: `<!-- work-accountability:event key=WORK_KEY event=attempt-started|candidate|verdict|delivery|decision actor=WHO time=RFC3339 -->`.
- To see what evidence an issue has, run `awa status --evidence N`. It lists awa's record lines and every marker comment, including older ID-only markers such as `<!-- work-accountability:event 2026-09-25-batch3 -->`, plus the evidence this machine last applied. Don't conclude evidence is missing from a grep.
- awa writes the `Project:`, `Delivery:`, `Release:`, `Integration:`, `Delivered:`, `Won't do:`, `Blocked by:` and `Blocked reason:` lines in an issue's managed block. Never type them yourself; declare these facts in the manifest instead.

## Dependencies and status

- Record hard dependencies as `blocked_by` (work keys of managed issues in the same repository); awa writes them as GitHub blocked-by links and removes only links it made. Links people add are kept and reported.
- A story whose Health is Blocked needs an open blocker or a one-line `blocked_reason`; awa refuses otherwise.
- Large runs report progress on stderr (`awa: issues 40/170 (3m10s left)`). awa writes each issue once per run and spaces writes a second apart, as GitHub asks; don't run several awa writers in parallel to go faster.
- GitHub allows about 500 content-creating writes an hour per account. Before writing, reconcile and `awa release close` estimate their writes and stop with exit code 75, writing nothing, when the run would pass awa's limit (450 by default, `WORK_ACCOUNTABILITY_HOURLY_WRITES`); the message says when to rerun. A dry run's notes show the estimate. awa counts only its own writes.
- awa posts a Project status update (On track, At risk, Off track, Complete) when the document's overall status changes, and keeps that update's progress text current in place. Don't post status updates by hand to "fix" the board: change the stories.

## GitHub access

Use `scripts/gh_account.py --user USER -- <gh arguments>` when a specific stored GitHub identity is required without changing the user's global `gh` account. Verify required scopes before mutation. Organization-wide issue-field or workflow changes affect every repository and require explicit authorization after presenting the exact schema change. Repository- or Project-scoped changes require authority for that repository or Project.

Document Projects are private. Making one public publishes its title, README, section names, field values and draft issues to everyone on the internet, so it is a protected publication action: only run `awa project visibility ROOT_ISSUE public` when the operator explicitly asks, show them the preview it prints, and pass `--yes` only after they confirm. Visibility is not a manifest setting. Never change it in GitHub's settings on the operator's behalf: reconcile treats that as drift and stops.

Use `scripts/reconcile_project.py --diagnose --user USER` to print the resolved skill path and digest, source revision or copy-install receipt, `gh` version, login, and remaining GraphQL budget. Existing agent sessions must restart after a skill update; an on-disk update does not change instructions already loaded into a running session. `awa status --json` reports the installed `awa_version`; if it differs from the `version` of the SKILL.md you have loaded (a client may hand back a cached copy), read SKILL.md from disk before acting.

Pass issue bodies and comments through files or structured API input. Never interpolate untrusted Markdown into shell commands. Read back issue fields, relationships, Project membership, views, and workflow settings after setup.

## Completion

The workflow is working only when agents can:

- extract a plan into complete, source-bound, nonduplicate epics and stories;
- when GitHub Projects are writable, link the document's Project to the repository, show every story at any depth in its Lifecycle Kanban its sections in its By section table, its stories by release in its By release table, and read back the repository link, exact membership, view configuration, the cards each view shows, and logical field values;
- compute which item is actually ready;
- bind a live attempt to exactly the work being executed;
- show blockers without moving the item out of its lifecycle phase;
- move a submitted candidate through Acceptance, Release ready, and its declared delivery boundary to Done; and
- detect and reconcile changed requirements without losing work history.

Repository labels satisfy completion only when an observed API, scope, or permission failure proves that Projects cannot be created or updated. Record that exact failure in the managed root epic. The absence of a previously known Project or the presence of a differently scoped Project is not such proof.
