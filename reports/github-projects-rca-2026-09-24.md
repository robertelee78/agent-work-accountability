# GitHub Projects pilot RCA and correction plan

Date: 2026-09-24

## Executive finding

The pilot failed because the skill described a desired work model but did not ship an executable GitHub Project reconciler or tests for the user-visible result. Agents therefore improvised the owner, Project, repository link, views, item membership, fields, and retry behavior with individual `gh` commands. Those commands created valid GitHub objects, but no operation enforced that they formed one repository-wide delivery system.

The reproducible current failures are in the skill and its use: the created Project was unlinked, scoped to one ADR, missing a Kanban, and missing four managed issues. Current GitHub APIs support the required repository link, Project fields, item membership, field values, and a board view whose columns are a custom single-select field. The first pilot's zero-card API/UI behavior was not isolated with a retained same-state A/B test, so this RCA does not assign that observation to either GitHub or the quoted filter alone. GitHub later enforced its documented shared user rate limit; the available evidence does not attribute every point in that window to this pilot.

## Evidence captured from the pilot

One consolidated GraphQL inventory on 2026-09-24 cost three points and established the following state:

- The organization-owned pilot Project exists and is addressable through GitHub's owner-level Project API.
- It was named for one epic, and its description and README scoped it to that epic rather than the repository.
- It was later linked to its repository, so the repository's `projectsV2` connection returns it. This link was absent after the initial agent run.
- Its only view is `View 1`, with `TABLE_LAYOUT`; it has no group, vertical group, or sort configuration.
- It has Work phase, Health, Source freshness, and Rank fields. It has no Priority field.
- It contained the original epic and seven child stories but omitted a related epic and its three stories from the same repository.
- Five submitted stories were in Acceptance, one was Executing, and one was Ready. The epic correctly had no manually maintained Work phase; its progress is a rollup over its children.
- The earlier rate-limit report was accurate. After reset, GitHub reported 4,997 of 5,000 GraphQL points remaining. The consolidated inventory then used three points.

The first disposable pilot also produced an empty board. Its filter used a quoted `OWNER/REPOSITORY#NUMBER`, while GitHub's documented and subsequently used form is `parent-issue:OWNER/REPOSITORY#NUMBER`; however, the pilot did not retain a same-state quoted/unquoted control, and other API/UI reads also returned zero. Every child must separately be a Project item regardless of the filter.

## Root causes

### RC1 — prose was treated as automation

The skill said to create a repository-focused Project, views, fields, membership, and read-back checks. Its deterministic scripts only inspected ADRs, validated extraction, and selected a GitHub account. No script implemented Project discovery or reconciliation. The test suite consequently proved documentation structure and local helpers, not the GitHub outcome the skill promised.

**Effect:** every agent independently translated prose into ad hoc commands. Small differences produced different Project scopes, storage profiles, and stopping conditions.

### RC2 — there was no atomic repository-level completion gate

Issue creation, Project creation, repository linking, view creation, membership, field writes, and read-back were separate optional steps. An agent could report success after creating issues or setting some fields. The skill had no machine-enforced invariant requiring all managed issues to appear in one linked repository Project with a valid lifecycle board.

**Effect:** one agent reported a Project before it was linked or usable as a Kanban. Another reported reconciliation while every issue it created had empty Project membership.

### RC3 — Project ownership and repository linkage were conflated

Projects v2 are owned by a user or organization. Repository visibility is a separate explicit link. The original guidance called the Project “repository-focused” but did not force agents to perform and read back `linkProjectV2ToRepository`.

**Effect:** the pilot Project existed at organization level while the repository Projects tab correctly showed no linked projects.

### RC4 — Project cardinality was under-specified operationally

The written model favored a repository-focused Project, but discovery relied on titles and remembered numbers. It did not define a stable Project marker or deterministic adoption rules. One agent interpreted the epic-specific Project as unavailable for related work and chose labels rather than generalizing or reusing it.

**Effect:** work from the same repository split across one ADR-specific Project and a label fallback. A future agent could create another Project per ADR.

### RC5 — the API route for views was incomplete

The implementation attempts used `gh project` and GraphQL. The GitHub CLI has no command for creating or configuring views. Current GraphQL can create a board layout and write visible fields, but its view configuration input cannot write grouping or sorting. GitHub's current REST Projects Views endpoint can create a board with `vertical_group_by`, `group_by`, and `sort_by` using numeric field IDs.

**Effect:** the agent stopped at GitHub's default table and no Work phase columns existed. This was a client/API-selection defect, not a GitHub board defect.

### RC6 — parent/child relationships were mistaken for Project membership

GitHub does not add sub-issues to a Project when their parent is added. The pilot also used an invalid quoted parent filter before this was understood.

**Effect:** a parent could appear alone, or a filtered view could show zero cards even though issues existed.

### RC7 — lifecycle semantics were not enforced at the write boundary

The work model correctly separates Work phase, Health, claims, attempts, acceptance, and release. The GitHub write path accepted whatever an agent inferred and did not require issue-specific transition evidence.

**Effect:** an umbrella execution session could make unrelated stories appear Executing, and an epic could be left without a phase while its children occupied several phases. The Project currently contains plausible-looking values without a deterministic proof that each transition gate was met.

### RC8 — priority was promised but not provisioned

The schema said to reuse an existing Priority policy but did not specify what to create when none exists. Rank was created, Priority was not.

**Effect:** the board cannot express both coarse urgency and stable ordering as designed.

### RC9 — GraphQL traffic was request-oriented instead of state-oriented

Agents listed and edited issues and fields one at a time and repeated reads after individual writes. More than one agent used the same GitHub identity concurrently. GitHub applies the 5,000-point hourly primary GraphQL limit per user and additional secondary limits; all callers using that identity share the budget. The retained logs prove inefficient per-field traffic and eventual exhaustion, but they do not contain a complete account-wide request ledger for the full 5,000 points.

**Effect:** GitHub stopped a field pass partway through with `API rate limit exceeded`. The agent had to reconstruct which writes landed. GitHub behaved as documented.

### RC10 — resumability was a convention rather than a program property

The synchronization reference described idempotent operation IDs and pending state, but no Project command implemented them. The agent manually checked some completed writes before continuing.

**Effect:** recovery depended on agent judgment. A less careful agent could replay mutations, create duplicate views, or consume another full rate window.

### RC11 — installation success was not tied to the loaded revision

The installer correctly uses one managed checkout with symlinks for Codex, Claude, OpenCode, and generic Agent Skills clients. It tells users to restart sessions, but neither the skill nor the installer exposes a simple revision receipt that agents can report before a live mutation.

**Effect:** users cannot quickly distinguish “the checkout is updated” from “this long-running agent loaded an older SKILL.md”.

## External facts that constrain the fix

- `gh project link` and the `linkProjectV2ToRepository` GraphQL mutation are the supported repository-link operations.
- GitHub's REST Projects Views endpoint, API version `2026-03-10`, accepts `layout: board` and a `vertical_group_by` field ID. GraphQL exposes the resulting `verticalGroupByFields` for read-back.
- The REST view endpoint currently creates views; GraphQL supplies view listing, update of name/layout/filter/visible fields, and deletion. A managed malformed Lifecycle view can therefore be replaced deterministically when its grouping is wrong.
- GitHub advises clients to inspect rate-limit state, avoid concurrent requests, stop until `x-ratelimit-reset` when remaining is zero, and use exponential backoff only for secondary-limit recovery. Continuing while limited can result in a ban.
- A GraphQL query should retrieve the complete state needed for a decision. One consolidated pilot inventory cost three points, while the field-by-field approach exhausted the shared budget.

Primary references:

- https://docs.github.com/en/graphql/reference/projects
- https://docs.github.com/en/rest/projects/views
- https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api
- https://cli.github.com/manual/gh_project_link

## Correction plan

### P0 — ship one deterministic Project reconciler

Add `scripts/reconcile_project.py` inside the portable skill. It accepts a versioned JSON desired-state manifest and a repository identity. Each entry binds an issue number to its stable work key and names its Work phase, Health, Source freshness, optional Priority, Rank, and transition evidence. `mode: additive` updates the named work while still repairing membership for every discovered managed issue; `mode: repository` additionally requires desired state for every managed issue. The command discovers current state, prints a delta, and applies it only with `--apply`.

The reconciler owns the plumbing that agents previously improvised:

1. Resolve and verify the configured GitHub identity without changing the user's global `gh` account.
2. Acquire an OS-released host/account transport lock for the shared API budget and a repository lock for Project ownership. These locks serialize local callers; remote callers remain external concurrency and force rediscovery on conflicts.
3. Read the free REST `/rate_limit` summary, then fetch a paginated consolidated inventory: immutable repository node ID, owner, linked and owner-level Projects, fields, views, all Project items, values, desired issues, and GraphQL rate state. Enumerate every open and closed issue containing a managed stable-key block across all pages; reject duplicate or cross-repository keys before mutation.
4. Select the Project by immutable README marker `<!-- work-accountability:project-v1 HOST:REPOSITORY_NODE_ID OWNER/REPOSITORY -->`. More than one match stops with zero writes. An unmarked Project is adopted only with explicit `--adopt-project NUMBER`; the command never seizes the only human-linked Project implicitly.
5. Before creating a Project, persist a create intent outside the product repository. Create the Project with `repositoryId` so its repository link is part of the create mutation. On uncertain completion, search linked and owner-level Projects against the intent before creating again. Append the managed README block without replacing human prose. Generalize an adopted epic-specific title only when the explicit adoption delta requested it.
6. Ensure exact Project-local fields and options for Work phase, Health, Source freshness, and Priority, plus numeric Rank.
7. Add every manifest issue to the Project. A parent relationship never substitutes for membership.
8. Probe and create a managed `Lifecycle` board through the REST Projects Views endpoint with Work phase's numeric database ID as `vertical_group_by`, and available Priority then Rank fields as its ascending sort. Read the board and its node-ID mapping back through GraphQL. Record the managed view number in the Project README block. An unrecorded conflicting Lifecycle name fails closed by default. Explicit `--repair-lifecycle` migration deletes exactly one malformed view with that name and preserves every other view. Only Lifecycle is required in P0; the other documented views are optional.
9. Compute field deltas locally and batch independent GraphQL mutations with a fixed alias cap. Parse errors per alias because GraphQL mutations are not transactional. Preserve existing option IDs, add missing compatible options, never remove an option, and fail on field type conflicts. Clear built-in Status values if an auto-add workflow supplied them, while keeping Status out of the lifecycle view.
10. Pace mutative requests by at least one second, keep one request in flight, journal the validated manifest and operation results before and after each batch, and rediscover after any timeout or partial response. Never send a write for an already matching value.
11. Perform one final paginated read and fail unless the exact repository link, immutable marker, board grouping, managed view number, complete Project collection, issue-side memberships, and every requested logical value match. A membership edge with an absent Project item fails verification.

### P0 — make rate limiting and recovery deterministic

- Derive a conservative reserve from the delta and mutation-batch count before the first mutation; treat the shared account budget as externally mutable.
- Include the rate-limit cost, remaining points, and reset time in every receipt.
- Do not retry a primary-limit failure. Persist the desired manifest outside the product repository and exit with a retryable status naming the reset time.
- Surface primary and secondary limit errors without an internal mutation retry. Retain the desired state and require the next run to re-inventory before writing; an operator or scheduler can honor GitHub's named retry interval without risking a blind replay.
- Use subprocess argument arrays and structured stdin; never interpolate issue prose into a shell command.
- Pause at least one second between mutative requests and never issue them concurrently, as GitHub's current rate guidance requires.
- Make a rerun recompute the delta, so it resumes missing work and cannot replay completed writes blindly.

### P0 — enforce visible completion

A successful receipt must prove:

- one canonical Project marker for the repository's immutable node identity;
- the Project appears in the repository's Projects connection;
- a Lifecycle view exists with board layout and Work phase columns;
- every managed issue discovered across all pages is a Project item, including items outside an additive manifest;
- the Project collection and each issue-side membership contain the same expected item identity;
- every requested field value matches;
- no issue has more than one stable work key; and
- the remaining rate budget is reported.

The repository-label profile is allowed only after the reconciler records a concrete unsupported-API or permission result. A missing Project or an unrelated Project is not a fallback condition.

### P1 — enforce transition evidence without inventing product judgment

Validate the manifest before any network write. Ready requires a design approval bound to the current work key and requirement version; Executing requires a work-key-bound attempt; Acceptance requires an immutable candidate; Release ready requires an independent acceptance verdict bound to that candidate; Done requires the declared delivery event. Source freshness remains a separate visible fact: a feature-branch candidate can be in Acceptance while its planning source still needs default-branch reconciliation. The script validates identity, required shape, candidate/verdict binding, and verifier independence; the agent must separately prove that each referenced fact exists and is semantically sufficient. Epic issues reject Work phase input, clear any migrated Work phase value, and use GitHub's child progress as their rollup.

### P1 — test behavior, not prose

Add deterministic tests with a fake `gh` transport for:

- new organization-owned and user-owned repository Projects;
- explicit existing-Project adoption, human prose preservation, and epic-specific title generalization;
- an unmarked human Project that must never be adopted implicitly;
- create success followed by client timeout or death before the marker write;
- a repository rename where immutable node identity preserves the Project;
- two marked Projects, duplicate work keys, pagination beyond 100, and field-name/type conflicts, all failing closed;
- missing repository link;
- missing and partially configured fields;
- REST creation and GraphQL read-back of a Work phase Kanban;
- missing child membership;
- a partial or per-alias-success run resumed without duplicate writes;
- exhausted primary budget before writes;
- secondary-limit fail-and-resume behavior without blind mutation retry;
- local processes serialized by host/account and repository locks, with crash-released file descriptors;
- identity mismatch and insufficient Project scope; and
- final read-back failure when issue membership exists but the Project collection omits the item;
- a request ceiling expressed as inventory pages plus capped mutation batches; and
- checked-in real response shapes plus a live REST-create/GraphQL-read-back contract probe before release.

The deterministic local suite does not mutate live GitHub. The separately recorded production proof below uses the real pilot Project.

### P1 — make installation and loaded revision observable

Have the installer print the managed checkout path, install mode, exact commit, skill-tree digest, and dirty qualification. Copy installs carry a revision receipt. Add `--diagnose` to print its resolved script path, its own source hash, the install receipt, `gh` version, authenticated login, and capability results. This proves on-disk state; a running agent proves its loaded instructions only by reporting the skill version/digest it read, and existing sessions must be restarted after update.

### P2 — repair the live pilot using the same command

After tests pass, derive a fresh repository manifest from every managed issue, current Git, candidates, verdicts, and delivery evidence; current Project values are observations, not desired truth. Run the released reconciler with explicit adoption of the pilot Project. The command must generalize its title to repository scope, append the immutable repository marker, retain the repository link, add the omitted related work, provision the manifest's Priority policy, create and verify the Lifecycle Kanban, and apply only evidence-supported story states. This is the production proof for the new path; no manual follow-up mutation counts as success.

## Acceptance criteria

1. A fresh repository with existing issues can be given one repo-linked Project and a working lifecycle Kanban by one command.
2. Running the same command twice produces zero mutations on the second run.
3. Killing the command after any write and rerunning it completes only missing work.
4. Ten items with five logical values do not cause one request per field; request counts equal paginated inventory plus capped mutation batches, and the receipt reports measured GraphQL cost.
5. Concurrent local agents using the same host/account cannot issue mutation requests simultaneously, and concurrent local writers to one repository cannot race.
6. A rate-limit stop reports the reset and leaves a replayable desired-state manifest without changing product files.
7. The live repository Projects tab exposes the repository-wide delivery Project; its Lifecycle board visibly contains the evidence-supported stories in Work phase columns, and Project membership contains every managed issue including both epics.
8. Fresh Codex, Claude Code, and OpenCode sessions resolve the same installed skill digest and report its version; diagnosis flags a stale copy, dirty source, or a session that has not reloaded.

## Resolution and production proof

The implemented protocol is version 0.3.0. `scripts/reconcile_project.py` now owns Project discovery and immutable repository marking, explicit adoption, repository linking, Project-local fields, all-managed-issue membership, evidence-gated values, REST board creation, GraphQL read-back, bounded read-after-write polling, dual-sided membership verification, local writer locks, mutation pacing, rate-budget reservation, persisted desired state, and machine-readable receipts. The installer reports the skill version, exact revision, clean/dirty qualification, and tree digest; copy installs carry the same receipt.

The real pilot found and fixed three API-path defects before release:

1. GitHub's GraphQL `Mutation` root does not expose `rateLimit`; mutation documents now omit it and the receipt combines query-reported costs with the free before/after REST rate summary.
2. `addProjectV2ItemById` can become visible after the mutation response. The reconciler now performs bounded read-only polling after dependent writes and never replays the accepted mutation while waiting.
3. The pilot Project contained a legacy table named `Lifecycle`. Explicit repair deleted only that malformed view, preserved every other view, and created the required board through the current REST Projects Views endpoint.

The verified live result is:

- The Project has a repository-wide delivery title, carries the immutable repository marker, and is visible from the repository's Projects connection.
- All twelve managed pilot issues are active Project items and their issue-side membership points to the same Project item identities.
- Project-local Work phase, Health, Source freshness, Priority (`P0`, `P1`, `P2`), and Rank fields match the source/evidence-derived manifest. Both epics have no Work phase.
- Every managed issue records the canonical Project and `project-fields`; the obsolete fallback lifecycle labels were removed while unrelated labels and prose were preserved.
- Lifecycle view 7 has board layout, Work phase columns, and ascending Priority/Rank sort.
- The applying receipt returned `verified: true`. The immediate repeat returned `verified: true`, `applied_mutations: []`, four GraphQL requests, and measured GraphQL cost 6.
