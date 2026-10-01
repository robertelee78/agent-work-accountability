# Make releases visible with repository milestones

The github-work-accountability skill pack (awa) should make **one repository milestone per release, titled exactly as the release tag**, the GitHub-native home for every story's target release. It should also turn "Done at a release boundary" from an unchecked string into a fact the reconciler checks against a published GitHub Release. Everything below is a change to the skill pack itself: SKILL.md, the references, the extraction schema and validator, the `project-v4` manifest and `verify_final` in `reconcile_project.py`, new `awa` commands, and `tests/github_sim.py` scenarios. All of it works the same for any repository and for both user-owned and organization-owned repositories. The two pilot repositories, vox (user-owned, public) and repo-to-cve (r2c, organization-owned, private), are the evidence for these changes and their acceptance examples. They are not the subject of this report. The pilots show the gap. vox has **0 milestones** even though 136 issues name an exact version and three releases (v0.2.10, v0.3.0, v0.3.1) are active. repo-to-cve (r2c) has **0 milestones** after **10 releases in 5 days**. In both, the skill records delivery only as free text plus an opaque `delivery.ref`, and no GitHub object is involved. Without milestones, vox started using document Projects as release containers (Projects #22 and #23). Project #22 still undercounts v0.2.10 by roughly 58 issues, because Projects are per document and a release cuts across documents. Done issues also stay open: 33 of 35 in vox, and 9 delivered issues on a closed r2c board. On visibility, every Project in both repositories is private, which matches what the owner wants. But that is an accident: `createProjectV2` has no visibility input and GitHub documents no default. The reconciler should therefore set `public:false` itself, verify it, and offer one explicit command to go public. Four more native features are worth adopting: blocked-by dependencies, Project status updates, PR closing references, and issue closure at delivery. Iterations, insights, templates, and the hierarchy view are not worth it now. Organization issue types and issue fields are excluded by owner decision. This report proposes the skill changes, then lists them by file. It uses the vox and r2c data as acceptance examples, and every pilot cleanup it names runs through the skill's own reconcile and migration paths, with no one-off manual steps. It also describes simulator tests at the user level and ends with the product decisions that remain open.

## Both pilots ship releases that GitHub cannot see

The skill's work model already has the right concept but no GitHub object behind it. A story moves from Release ready to Done only when its "declared delivery boundary" is crossed. That boundary is a prose line in the managed issue block ([synchronization-contract.md:57](../skills/github-work-accountability/references/synchronization-contract.md)) and a free-text `delivery_boundary` string in the extraction manifest ([planning-source-extraction.md:88](../skills/github-work-accountability/references/planning-source-extraction.md)). The reconciler gates Done on a `delivery` evidence object whose `candidate` matches the accepted candidate. It explicitly does not check that the `ref` exists or proves anything ([project-reconciliation.md:136](../skills/github-work-accountability/references/project-reconciliation.md)), and the test fixture uses an arbitrary `"release:{attempt}"` ([scenario.py:147](../tests/scenario.py)). The skill reads or writes **no milestones, Releases, tags, or deployments**, and never closes an issue ([reconcile_project.py:1267](../skills/github-work-accountability/scripts/reconcile_project.py)).

vox shows what happens when delivery has no native home. The repository has no milestones and not one of its 292 issues has one ([milestones](https://github.com/robertelee78/vox/milestones)). Yet delivery text names a version on **107 issues for v0.2.10, 11 for v0.3.0, 23 for v0.2.9 and 6 for v0.2.8**. Another 72 say only "a published vox GitHub release", and 53 say "Merged to main" ([issues](https://github.com/robertelee78/vox/issues)). The owner's release policy is already milestone-shaped. It reads: "v0.2.10 is every known defect in what Vox ships; v0.3.0 is features, built on v0.2.10; v0.3.1 is the new features' defects." The decider adds: "I need v0.3.1 to become usable asap" ([#235](https://github.com/robertelee78/vox/issues/235)). Because nothing else groups work by release, the team created release Projects: **#22 for v0.2.10 (117 items) and #23 for v0.3.0 (11 items)** ([Project 22](https://github.com/users/robertelee78/projects/22)). The v0.2.10 plan pulls in the real-proof (RP-\*), ADR-024 and ADR-021 defect items. Those stories stay on their own document Projects (#6, #16, #1), so the release board misses about 58 of roughly 175 v0.2.10 members. Release status is spread across five places: boundary text, Project membership, plan files on a non-main branch, root issues #185 and #235, and branch names. None of these places is visible to outsiders, because every board on this public repository is private ([Project 20](https://github.com/users/robertelee78/projects/20)).

Release evidence and tracker state also disagree in vox. The v0.2.9 release notes cite #40, #41, #50 and #51, while those issues say "v0.2.10 (carried from v0.2.9)" ([v0.2.9](https://github.com/robertelee78/vox/releases/tag/v0.2.9); [#40](https://github.com/robertelee78/vox/issues/40)). #174 sits on the v0.2.10 board with a v0.2.9 boundary ([#174](https://github.com/robertelee78/vox/issues/174)). **33 of 35 Done issues remain open**, and 27 of them are visible only on a closed board, so the repository's open-issue list overstates outstanding work ([#30](https://github.com/robertelee78/vox/issues/30)). **121 issues sit in Release ready**. When v0.2.10 publishes, all of them must move to Done at once, and today nothing selects that set.

r2c shows the same gap under a very different cadence. It published **v0.1.7 through v0.1.17 in five days**, has 13 tags (v0.1.6 and v0.1.12 have no release), and has `Cargo.toml` already at 0.1.18 with four merged PRs unreleased ([releases](https://github.com/IOMachines/repo-to-cve/releases); [tags](https://github.com/IOMachines/repo-to-cve/tags)). It has 0 milestones, and the native Milestone field is empty on all 61 Project items ([milestones](https://github.com/IOMachines/repo-to-cve/milestones)). Release notes are auto-generated PR lists, so a reader of v0.1.17 cannot see that it completed #1248 and #1286 ([v0.1.17](https://github.com/IOMachines/repo-to-cve/releases/tag/v0.1.17)). Only **8 of 24 closed issues have a closing PR reference**. The case-study researcher still reconstructed each closed issue's first release from closing-PR merge commits and `git tag --contains`. That calculation is mechanical, and the skill could run it. Project #3 (ADR-059) is closed, yet all nine of its issues are open, and eight are Done ([Project 3](https://github.com/orgs/IOMachines/projects/3)). ADR-059 itself says it was "implemented through immutable stable release v0.1.14" ([#1250](https://github.com/IOMachines/repo-to-cve/issues/1250)).

The two repositories need two modes. vox plans releases ahead of time: the version is decided at planning time, and the release waits for its scope ("no carry-over"). r2c works mostly the other way. Projects #5 and #6 deliver at "Merged to `main`", and their code ships in whichever patch release follows. Only Project #4 names a release ([#1249](https://github.com/IOMachines/repo-to-cve/issues/1249)). One design has to serve both modes.

## A release milestone records the target release, while the published release is the Done evidence

GitHub's milestone model fits the skill's repository-bound rule closely. A milestone is a **repository-scoped container with a title, description, optional due date, open or closed state, and a progress percentage**. An issue holds **at most one** milestone ([About milestones](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/about-milestones); [REST: Issues](https://docs.github.com/en/rest/issues/issues)). Four mechanics shape the implementation:

| Mechanic | Consequence for the reconciler |
|---|---|
| Milestones are created, edited and closed only through REST at `/repos/{o}/{r}/milestones`. GraphQL has no milestone mutations, though `updateIssue.milestoneId` sets an issue's milestone ([REST: Milestones](https://docs.github.com/en/rest/issues/milestones)) | Milestone management is REST. The issue assignment can reuse the reconciler's existing REST issue PATCH |
| "Without push access to the repository, milestone changes are silently dropped" ([REST: Issues](https://docs.github.com/en/rest/issues/issues)) | A 200 response proves nothing. Every assignment must be read back, as labels and bodies already are |
| Projects v2 has a built-in `MILESTONE` field, but `updateProjectV2ItemFieldValue` cannot write it ([Filtering projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)) | Set the milestone on the issue. Every awa Project then shows it for free in its existing Milestone field |
| Sub-issues "inherit the Project and Milestone of their parent issue by default" ([Changelog 2025-09-11](https://github.blog/changelog/2025-09-11-a-rest-api-for-github-projects-sub-issues-improvements-and-more/)) | Do not rely on inheritance. Children in one epic can target different releases, so set every story's milestone explicitly |

Releases and milestones have **no native link**. Generated release notes are driven by PR labels and tag ranges, and `gh release create` has no milestone option ([Automatically generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes)). So the link is a convention the skill must enforce itself. The simplest convention is **milestone title equals release tag** (`v0.2.10`, `v0.1.18`), with the release theme in the description. That lets the reconciler match the two mechanically. Large projects use milestones the same way. Kubernetes restricts who may add work to a release milestone and removes stalled items at code freeze ([kubernetes/community](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-release/release.md)). VS Code drives each iteration milestone to zero open items and explicitly moves the leftovers ([VS Code wiki](https://github.com/microsoft/vscode/wiki/Development-Process)). Practitioner guidance adds: close the milestone when its release is published, and never delete a closed one ([tenthirtyam.org](https://tenthirtyam.org/dispatches/2026/05/10/github-milestones-as-release-payloads/)).

The AGENTS rule constrains how far milestones may go: planning intent, attempts, verdicts and releases are separate authorities ([AGENTS.md](../AGENTS.md)). A milestone's progress bar counts closed issues, and closing an issue is not acceptance. **A milestone must never become a second Done clock.** The clean split:

- The milestone is planning intent: the release a story targets.
- The published GitHub Release is the delivery fact.
- The Work phase field remains the only lifecycle clock.

The milestone's real value is that it gives the reconciler something it can check, which today it cannot.

### Recommended work-model changes

Replace the prose boundary with a structured one in the extraction manifest and managed block. Each story declares a `delivery.kind`: `release`, `merge`, `deployment`, or `other`. A release-kind story also declares `delivery.release`, which takes one of two forms:

- **An exact tag**, such as `v0.2.10`. This is the planned mode, which fits vox.
- **`next`**. This is the attributed mode, which fits r2c's fast patch stream and any merge-delivered story whose code still ships in a release.

The reconciler maps an exact tag to an issue milestone during apply. It assigns `next` stories when a release is published: each goes to the first published release whose tag contains the story's accepted merge commit. That is the calculation the r2c researcher ran by hand. Merge-kind stories keep today's semantics. They can still receive an informational release milestone through attribution, without changing their Done gate. The prose "Delivery boundary" line stays in the managed block, generated from the structured fields, so people still read a sentence.

Then make release-kind Done checkable. For a story with `delivery.kind: release`, the reconciler should require three things before Done:

- the `delivery.ref` names a tag
- a **published, non-draft** GitHub Release exists for that tag
- the issue's milestone title equals that tag

The non-draft condition matters for r2c, whose pipeline uploads a draft, verifies it, and then publishes it as immutable (`scripts/distribution/README.md` in [repo-to-cve](https://github.com/IOMachines/repo-to-cve)). This closes the gap the reconciliation reference admits today. The release system stays the authority. The reconciler only checks that the reference points at a real published release. The check would have caught every vox inconsistency above: #174 targets v0.2.9 but is on the v0.2.10 board, and #40/#41/#50/#51 appear in v0.2.9 notes while marked v0.2.10.

The milestone should be a **guarded field**. Add it to `GUARDED_FIELDS` ([reconcile_project.py:79](../skills/github-work-accountability/scripts/reconcile_project.py)) with the same observed-value rule as Work phase. A decider who moves #245 from v0.3.0 to v0.3.1 in the GitHub UI then gets a refusal instead of a silent revert. This is the deferral flow vox already has ("V030-09 #245 deferred from v0.2.10").

### Recommended reconciler surface

Story-to-milestone assignment belongs in the existing per-document `reconcile` run, because the target release is per-story planning intent. Milestone lifecycle belongs to the repository, not to any one document. One vox milestone, v0.2.10, gathers stories from at least five document Projects. So milestone creation and closure need a small repository-level command, for example `awa release`, with three verbs:

| Verb | Behavior |
|---|---|
| `awa release plan v0.2.10 --description …` | Idempotently creates the open milestone by exact title. It never edits a milestone's description or due date without `--update` |
| `awa release status v0.2.10` | Prints members by Project and phase, plus open blockers, from one read |
| `awa release close v0.2.10` | Verifies the published Release. Moves every member in Release ready whose evidence binds to that tag to Done, closes those issues as completed with a comment linking the release, and closes the milestone. It **refuses** if any member is not Done, listing each one, unless the caller names `--move-open-to v0.3.1`. That matches vox's no-carry-over rule and VS Code's explicit rollover |

`reconcile` would create a missing milestone by title only when a manifest names it, so a fresh plan works in one step. It would never close or delete one. Milestones are never deleted. A dry run must preview every milestone write exactly, following the rule from commit a3cc27b that the dry run is an exact preview of apply.

Each document Project should get a third managed view: a **"By release" table grouped by Milestone**. GitHub lists milestone as groupable ([table layout](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-table-layout)). Grouping avoids the filter-parsing risk that broke the Lifecycle board, where `has:"Work phase"` passed the API check but blanked the web page ([RCA](github-projects-rca-2026-09-24.md)). Any `milestone:"…"` filter must first be encoded in the simulator with its web-accepted form, and the first live use needs a human look. Renaming a milestone keeps its number, but title-based filters stop matching. That is another reason titles should be immutable tags.

### Acceptance examples from the pilots

Each example below should be produced by the skill's own commands run against that repository's existing manifests. None is a manual instruction. In vox, the reconciler would create open milestones **v0.2.10, v0.3.0 and v0.3.1**, and could optionally create closed retroactive ones for v0.2.9 and v0.2.8. It would assign the 107 exact v0.2.10 stories and the 11 v0.3.0 stories directly. It would then report the derived members as decisions, not guesses:

- §B findings #159–#182
- §C ADR-021 defects #11, #21, #28, #163, #165, #166
- the RP-\* and ADR-024 items, whose "Merged to main" boundary conflicts with a plan that makes the v0.2.10 release their boundary

v0.3.1 starts empty, which is honest: no issue targets it yet. Project #22 then has no remaining job as a release container. Retiring it should go through the reconciler's existing `supersedes` migration, which takes snapshots, checks for conflicts, and closes the old board with a `project-superseded` marker ([project-reconciliation.md:138-181](../skills/github-work-accountability/references/project-reconciliation.md)). It should not be closed by hand. The v0.2.10 milestone page becomes the cross-document public view of that release, because milestones live on the public repository. Due dates stay empty: no plan states one, and the cadence was broken by v0.2.10's week-long hardening.

In r2c, the right first move is attribution, not planning. Closed retroactive milestones v0.1.13, v0.1.15 and v0.1.17 would hold the issues whose closing merges first shipped there (#1351/#1352, #1364/#1384, #1248/#1286). An open `v0.1.18` would take #1365. Project #4's image-release stories would declare `delivery.release: next` until the owner names a version. At about two releases a day, a version chosen at planning time would be overtaken by correction releases. Projects #5 and #6 keep "merge" delivery, and their stories get release milestones only by attribution. In both repositories, the word "milestone" already means ADR acceptance gates (vox M21.x, r2c M0–M6). Skill text must say **"release milestone"** throughout to keep the two apart.

## Private by default, public through one explicit command

The visibility facts favor a simple design. A public Project "can be viewed by everyone on the internet". Items from repositories the viewer cannot read stay hidden behind a padlock and come back as `REDACTED` in the API ([Managing visibility](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-visibility-of-your-projects)). **`CreateProjectV2Input` has no `public` argument, and no GitHub page documents the v2 default.** The only documented "private by default" applies to classic projects ([GraphQL schema](https://docs.github.com/public/fpt/schema.docs.graphql)). The single write path is `updateProjectV2(input:{projectId, public})`. The read is `ProjectV2.public: Boolean!`. The REST Projects API can read `public` but cannot set it ([REST Projects](https://docs.github.com/en/rest/projects/projects)). In organizations, owners can restrict visibility changes to owners only, and enterprises can enforce that policy. Neither policy can be read through the API, so a denied mutation is the only signal ([Allowing visibility changes](https://docs.github.com/en/organizations/managing-organization-settings/allowing-project-visibility-changes-in-your-organization)).

Today the reconciler neither sends nor reads `public` ([reconcile_project.py:1517-1573](../skills/github-work-accountability/scripts/reconcile_project.py)). All 20 vox Projects and all 4 r2c Projects happen to be private. The only public board in either account is vox #21, a closed temporary render test that no tool manages ([Project 21](https://github.com/users/robertelee78/projects/21)). "Private by default" therefore holds by observation, not by code.

The recommended design has four parts:

1. **A manifest field.** Add `project.visibility: private | public`, defaulting to `private`.
2. **Set and verify on every run.** Immediately after `createProjectV2`, the reconciler sends `updateProjectV2(public:false)` as its own mutation, separate from the title and README update so a policy refusal cannot mask other writes. `verify_final` reads `public` back and makes it a condition of `verified: true`.
3. **Guard the field.** Visibility joins the guarded fields. If someone flipped a board to public in the UI and the manifest still says private, the reconciler refuses and names the drift instead of silently re-privatizing.
4. **One explicit command to go public.** `awa project visibility <document-key|project-url> public` (or `private`) edits the manifest and reconciles, in one step. Going public prints what an outsider will see before it writes:
   - the title, README and short description
   - section names and field values
   - the item count, and how many items will be redacted because their repository is private

   A refused mutation leaves the board private and reports an organization policy or permission refusal, with no retry.

Making a Project public is a publication-class action under the skill's own rule that "publication and other separately protected actions retain their own authorization rules" ([SKILL.md:30](../skills/github-work-accountability/SKILL.md)). So the command must be explicit and must never be a side effect of reconcile.

The two repositories show why one rule cannot fit everyone. vox is a public repository with private boards. Outsiders can read every issue, including its phase-free managed block, but cannot see the board, so making the v0.2.10 board public would give real transparency. r2c is private, so a public r2c Project would show strangers its metadata and README and redact every item. The flag adds almost nothing there, and organization membership and Project access are the real controls. That argues against automatically mirroring repository visibility. The default stays private everywhere, and `awa doctor` can mention the option for public repositories. Release milestones change the calculation for public repositories: they are public wherever the repository is, so vox gets a public progress bar per release even if every board stays private. Two disclosure risks remain undocumented and should be tested once against live GitHub inside a verified run, not assumed:

- draft issues on a public board
- Project-stored field values on redacted items

## Four more native features are worth adopting

| Feature | Recommendation | Evidence |
|---|---|---|
| Blocked-by dependencies | **Adopt.** Write edges from the extraction manifest's `dependencies`, verify by read-back, and use open-blocker counts to justify Health = Blocked | GA since 2025-08-21, 50 per relationship type, REST and GraphQL `addBlockedBy`, `issueDependenciesSummary` ([Changelog](https://github.blog/changelog/2025-08-21-dependencies-on-issues/)) |
| Project status updates | **Adopt.** Post one update when the root's rolled-up Health or release progress changes | `createProjectV2StatusUpdate` with ON_TRACK, AT_RISK, OFF_TRACK, INACTIVE and COMPLETE, available on user-owned Projects ([GraphQL schema](https://docs.github.com/public/fpt/schema.docs.graphql)) |
| PR closing references | **Adopt** as attempt evidence and for release attribution, never as acceptance | `closedByPullRequestsReferences`, `addCloseIssueReferences` ([GraphQL schema](https://docs.github.com/public/fpt/schema.docs.graphql)) |
| Close issues at Done | **Adopt** inside `awa release close` and for merge-delivered Done | vox 33 of 35 Done issues open; r2c closed Project #3 with 9 open issues |
| Iterations | Defer: releases, not sprints, are the time-box in both repositories | Neither repository has an iteration field |
| Insights, built-in workflows, templates | Decline: no write API for charts or workflow configuration; templates are organization-only | [Built-in automations](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations); [Templates](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-project-templates-in-your-organization) |
| Hierarchy view | Decline for now: public preview, table only, no API toggle | [Discussion #184225](https://github.com/orgs/community/discussions/184225) |
| Org issue types and issue fields | **Excluded by owner decision.** Keep type and task metadata in Project-local fields and the existing `epic` convention | Repository-level scope rule |

**Dependencies** have the clearest payoff. The skill's documentation already says "use native blocked-by relationships for hard dependencies when available", but the reconciler neither writes nor verifies them ([github-model.md:101](../skills/github-work-accountability/references/github-model.md)). The cost shows in r2c. **Project #5 has 14 of 24 items Blocked and Project #6 has 14 of 15 At risk, yet neither board has a single blocked-by edge** explaining why ([Project 5](https://github.com/orgs/IOMachines/projects/5)). Where edges do exist, #1249 stays Blocked after some of its blockers closed, which is consistent with Health having gone stale ([#1249](https://github.com/IOMachines/repo-to-cve/issues/1249)). In vox, none of the 117 v0.2.10 items uses an edge. Health should stay a Project-local field, because Projects cannot filter on `is:blocked`. But the reconciler can refuse Health = Blocked on an issue with no open blocker and no recorded external reason. It can also report an issue with open blockers whose Health says On track. Edges between documents in the same repository are already in use (r2c #1247 is blocked by #1276), so dependencies need no cross-repository design.

**Status updates** solve a problem the skill currently handles in the README. A document-level Health roll-up becomes a dated, attributed status entry that people see on the Project and in the Projects list. Its body can link the release milestone's progress. To avoid noise, post only when the status value changes, never on every run.

**PR linkage** matters mostly because it feeds release attribution. A story closed through a PR with a closing reference can be mapped mechanically to the first tag containing the merge. r2c's #1285 has no such PR, so its release cannot be determined. The reconciler should record the PR link with `addCloseIssueReferences` when an attempt's evidence names a PR, without editing PR bodies. It must keep treating that link as attempt evidence, never as acceptance. As for **release notes**, `.github/release.yml` has no milestone input, so `awa release close` should offer to append a "Work items delivered" section listing the milestone's issues. That would make r2c's v0.1.17 notes say what they completed. Editing a published release body is publication-adjacent, so this must be opt-in.

Two checks belong in the same increment, because the case studies show these problems causing confusion. Both are general skill behaviors, not pilot-specific fixes. First, the reconciler should refuse to close a Project, or `awa doctor` should flag one already closed, while managed issues on it are still open: r2c #3, and vox #2 and #17. Running `awa release close`, or a merge-delivery close, then clears those issues through the normal path. Second, `awa doctor` should report leftover `phase/*`, `health/*` and `source/*` labels on repositories that use the project-fields profile. The existing label-to-fields migration, which already strips those prefixes from issues ([github-model.md:37](../skills/github-work-accountability/references/github-model.md)), should offer to delete the now-unused label definitions with confirmation ([labels](https://github.com/IOMachines/repo-to-cve/labels)).

## The changes, file by file

| Skill file | Change |
|---|---|
| `SKILL.md` | Adds release milestones to the detection step and the Release ready → Done procedure. Uses the term "release milestone" throughout. Lists `awa release` and `awa project visibility`. Classifies going public and editing release notes as protected publication actions |
| `references/work-model.md` | Defines structured delivery (`kind`, `release`). Defines the release-kind Done gate as a published, non-draft Release whose tag equals the issue's milestone, and states that milestones are planning intent, never a lifecycle clock |
| `references/github-model.md` | Covers the milestone mechanics (REST-only management, silent drop without push access, one milestone per issue, no reliance on inheritance), Project visibility facts, blocked-by usage, and status updates. Records the owner's exclusion of organization issue types and fields |
| `references/planning-source-extraction.md` and `scripts/validate_extraction.py` | Replace the free-text `delivery_boundary` with a structured `delivery` object. The validator rejects a release kind without a tag or `next`, and warns when a plan-level boundary contradicts a story's boundary (the vox RP-\* case) |
| `references/synchronization-contract.md` | Generates the managed block's "Delivery boundary" sentence from the structured fields, and lists the milestone and visibility as reconciled facts with their authorities |
| `references/project-reconciliation.md` and `scripts/reconcile_project.py` | Manifest additions: per-item `milestone`, `project.visibility`, `dependencies`. Milestone and visibility join `GUARDED_FIELDS`. New writes: milestone assignment by REST PATCH, `public:false` after create, blocked-by edges, status updates on Health change. `verify_final` reads back milestone, `public` and edges. Adds the "By release" view, the release-kind Done gate, and the refusal to close a Project with open managed issues |
| `bin/awa` | New `release plan`, `release status` and `release close` (with `--move-open-to`, and attribution for `next`). New `project visibility public` or `private`, which previews what outsiders will see. `doctor` gains checks for closed Projects with open issues, leftover profile labels, and a public repository with private boards |
| `tests/github_sim.py` and the scenario tests | The simulator state and scenarios listed in the next section |

## Prove each increment the way a release manager would see it

The repository's testing rule applies directly. Drive the real CLI against `tests/github_sim.py`, and assert on what people see: boards, groups, milestone pages, issue state, receipts and messages ([AGENTS.md](../AGENTS.md)). The simulator fails loudly on any unknown route, query or filter ([github_sim.py:421-549](../tests/github_sim.py)), so each feature first needs simulator state:

| Feature | Simulator additions |
|---|---|
| Milestones | Repository `milestones`; REST GET, POST and PATCH on `/milestones`; `milestone` in issue GET and PATCH, **including the silent drop for callers without push access**; a rendered milestone page showing open and closed members with progress |
| Releases | Releases with a `draft` flag, plus tags with commit containment for attribution |
| Visibility | `public` on Project state, accepted by `updateProjectV2`, with a policy-refusal mode, and an anonymous-viewer render that shows the board with redacted private-repository items |
| Dependencies and status updates | Blocked-by edges and status-update objects |

Scenario tests should read like a release manager's day:

- A planner applies a vox-shaped manifest. The test asserts three release milestones with the right members, and a "By release" group on each document board.
- A decider moves a story to v0.3.1 in the UI. The next reconcile refuses and names the story.
- `awa release close v0.2.10` runs against a draft release. It refuses.
- The release is published. The command moves every bound Release-ready story to Done, closes those issues, and closes the milestone.
- The same command runs with one story still in Acceptance. It refuses and lists that story, and succeeds only with `--move-open-to`.
- r2c-shaped attribution assigns closed issues to the first tag containing their merge, including the tag-without-release case (v0.1.12 → v0.1.13).
- Visibility: a new Project is private after creation even if the simulator defaults to public. `visibility public` on an organization that forbids it leaves the board private with a policy message. The anonymous render of a public board over a private repository shows only redacted items.

Following the RCA, the first live run of each new filter or view form ends with a person opening the page, because the API and the web page have disagreed before ([RCA](github-projects-rca-2026-09-24.md)).

The recommended order puts the smallest risk first:

1. Visibility enforcement and the explicit command. These are small and close a silent dependency on an undocumented default.
2. Structured delivery plus milestone assignment and the "By release" view.
3. `awa release close` with the checkable Done gate and issue closure.
4. Dependencies and status updates.

Each step is a separate version with its own simulator scenarios. Running sessions must restart after each skill update, as the v0.5 stale-agent incident showed.

## Conclusion

The case studies show that the skill's main missing piece is not one more GitHub feature but the **link between the release system and the tracker**. vox built release Projects and r2c left delivered issues open because the skill gave release delivery nowhere to live except prose. Milestones are the right place for it because they are repository-scoped, already render in every awa Project, are public wherever the repository is, and can be read back. With them, delivery evidence can finally be checked against a real published Release, so the change also tightens the evidence model and not just the reporting. The same pattern gives visibility its design: decide explicitly, write it, read it back, and refuse on drift. Every feature recommended here follows that pattern. Features without a write or read-back path through the API, such as insights, workflow configuration and the hierarchy view, stay out until that path exists.

## Open product decisions

1. **Milestone naming.** Should the title be exactly the tag (`v0.2.10`), which this report recommends for mechanical matching, or prefixed (`vox v0.2.10`, `r2c v0.1.18`) to avoid confusion with ADR "milestones"?
2. **Planned versus attributed releases.** Should r2c-style repositories default to `delivery.release: next`, with attribution at publication, and vox-style repositories default to exact tags? Or should every repository choose one mode?
3. **Merge-delivered stories.** Should stories that are Done at "Merged to main" still receive an informational release milestone through attribution? And should vox's RP-\* and ADR-024 boundaries be corrected to "v0.2.10 release", as the plan implies?
4. **Release Projects.** Should vox Projects #22 and #23 be retired once release milestones exist, kept only for stories their release plans originate, or kept as they are? Should the skill refuse to create release-scoped Projects in the future?
5. **Closing issues at Done.** Should awa close issues as completed when they reach Done, which it never does today? Should that happen only through `awa release close`, or also for merge-delivered stories?
6. **Leftovers at release close.** Should the default be to refuse while any member is not Done (vox's no-carry-over rule), or to require an explicit `--move-open-to`, or both?
7. **Retroactive milestones.** Should awa backfill closed milestones for past releases (vox v0.2.8/v0.2.9; r2c v0.1.13–v0.1.17), or start only with the active releases?
8. **Due dates.** Should milestones carry due dates only when an owner states one, which this report recommends, or should awa ask for a date at `awa release plan`?
9. **Who may change release scope.** Should anyone with push access be able to move a story between release milestones, with awa refusing on drift, or should scope changes require a recorded decider approval, as Kubernetes does with milestone maintainers?
10. **Going public.** Should the visibility command also be offered automatically by `awa doctor` for public repositories such as vox? And should going public require a second confirmation when draft issues exist or most items would be redacted?
11. **Release notes.** Should `awa release close` append the delivered work items to the GitHub Release body, which modifies published content, or only print them?
12. **Status updates and dependencies scope.** Should status updates be posted automatically on Health changes, or only on request? Should blocked-by edges be required for Health = Blocked, or only reported when missing?
