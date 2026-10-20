---
name: model
description: The work model behind every awa operation. Open when you must decide whether a Work phase, Health, Source freshness, or evidence claim is justified, which authority owns a fact, or which storage profile a repository uses.
---

# Work model

This leaf is the shared vocabulary. The other leaves cite it rather than restate it.

## Work phase

The board is a left-to-right delivery flow. Its columns answer **how far has this work progressed from a
credible idea to released value?** They do not encode ownership, priority, or trouble.

| Phase | Meaning | Entry fact | Exit fact |
|---|---|---|---|
| **Backlog** | A possible work item with a crisp problem statement: the Why. A proposed What is useful but optional. | The Why is recorded well enough for another person or agent to understand the problem. | The operator selects the item for HLD/LLD work. |
| **Designing** | Product and engineering iterate on the high-level, then the low-level design. | Design work starts against the recorded Why and any proposed What. | Product authority and a technical reviewer approve the design, acceptance boundary, dependencies, and delivery intent. |
| **Ready** | Why, What, HLD and LLD are aligned enough for confident execution. | Design approval authorizes the item for the execution queue. | A specific execution attempt starts. |
| **Executing** | Engineers or agents are producing the agreed deliverable. | An attempt explicitly names the work key. | The implementer submits an immutable candidate. |
| **Acceptance** | Testing and product acceptance prove whether the candidate meets the requirement. | A candidate is identified by commit, artifact digest, deployment identity, or another immutable reference. | An authorized independent verdict accepts every required criterion for that candidate. |
| **Release ready** | Acceptance passed; the item awaits its declared delivery. | The acceptance verdict is recorded. | The item is included in a proven release, deployment, publication, enablement, or other declared delivery. |
| **Done** | The accepted outcome is available at its declared delivery boundary. | Release or delivery evidence is recorded. | Terminal unless the requirement changes or delivered behavior regresses. |
| **Won't do** | The decider ruled the work out; nothing will be delivered. | A recorded decision names who decided and why. | Terminal unless the decider takes it back. |

Won't do is the only way to end a story without delivering it. Its issue is closed as *not planned*, it
leaves its release milestone, and epic progress counts it apart (`3/7 Done · 1 won't do`).

Backlog collapses informal Idea and Why/What confirmation stages. A session MAY challenge an unclear Why or
What. The move to Designing is an operator prioritization decision, not proof that a meeting occurred. HLD and LLD are design evidence recorded on the
issue, not board columns.

When importing existing work, a session MUST place each item at the furthest phase its recorded facts
support. It MUST NOT replay old transitions and MUST NOT infer a later phase from code presence alone.

## Transition rules

- A phase transition MUST be backed by a recorded fact; card movement is not a fact.
- A branch, claim, commit, changed file, open pull request, passing process or agent exit MUST NOT by itself
  move an item to Executing, Acceptance, Release ready or Done.
- A story MUST NOT move from Ready to Executing except on a durable attempt-start event carrying its own
  attempt ID, actor, start time and the exact work key. One commit or branch MUST NOT be reused as attempt
  evidence for several stories.
- When an attempt fails, is released, expires or is cancelled without a candidate, the story MUST return to
  Ready with the ended attempt kept in history. A retry MUST use a new attempt ID.
- Ready means execution is authorized and queued. A later impediment MAY make a Ready item Blocked without
  revoking its approved design.
- A story MUST NOT move from Executing to Acceptance until a specific candidate and the required evidence
  are submitted.
- A story MUST NOT move from Acceptance to Release ready except on a verdict independent of the
  implementation assertion. A rejection MUST return the story to Ready, or to Executing when a new attempt
  has started, and MUST keep the rejected candidate in history.
- A story MUST NOT move from Release ready to Done until the declared delivery boundary is crossed. A merge
  MUST be treated as delivery only for a story that declares merge as its boundary.
- A requirement change MUST set Source freshness to Reconciliation needed and MUST move the phase backward
  only as far as the changed decision invalidates.
- A regression of delivered behavior MUST reopen the item to Acceptance (the accepted candidate is still
  being diagnosed) or Ready (a new implementation is authorized), keeping the earlier delivery evidence.
- Health MUST be recomputed when the phase changes. At risk or Blocked MUST be cleared only when its
  recorded condition is resolved, and MUST be cleared once it is: a resolved condition MUST NOT be carried
  into the next phase.
- An infrastructure failure ends or pauses an attempt. A session MUST NOT treat it as proof that the product
  work failed.

## Independent axes

These facts MUST NOT become extra phase columns.

| Axis | Values | Meaning |
|---|---|---|
| **Health** | On track, At risk, Blocked | Whether a known condition threatens (At risk) or prevents (Blocked) the next transition. A session MUST keep a blocked item in the phase it reached. |
| **Priority** | Repository policy, commonly Urgent, High, Medium, Low | Relative importance chosen by the operator or product authority. |
| **Rank** | Ordered position within a priority | Which eligible item is taken next. |
| **Ownership** | Unclaimed, or the one open attempt (`Attempt:` line) | Who currently may run an attempt. It is not progress. |
| **Attempt** | ID, actor, start, heartbeat, candidate, end | One bounded execution or verification try. |
| **Attempt outcome** | Submitted, verified success, work failure, infrastructure failure, cancelled, expired, indeterminate | What happened to one attempt. A failed attempt does not delete or complete the item. |
| **Source freshness** | Current, Reconciliation needed | Whether the issue still represents the current planning source. |

## Readiness

A session MUST select an item for execution only when:

1. its phase is Ready;
2. Health is not Blocked;
3. every required predecessor is Done at its own declared delivery boundary;
4. no open attempt holds it; and
5. its planning source is Current.

Priority and rank choose among eligible items. A session MUST NOT let them override an unsatisfied
dependency, a failed approval gate, or an open attempt.

## Epics

An epic is a rollup. A planning document has one root epic; section and subsection epics MAY sit below it,
but the tree MUST NOT go more than three levels deep (root → section → subsection → story). Only leaf stories carry a Work phase. An
epic MUST NOT be given a manually maintained phase. awa derives each epic's facts:

- **Health**: the worst Health of the stories below it (Blocked, then At risk, then On track); clearing a
  story's condition clears the epic's;
- **Progress**: Done stories out of all stories below it, with blocked, at-risk and won't-do counts;
- **Source freshness**: not Current while anything below it needs reconciliation; an epic MAY itself need
  reconciliation when its own source text changed.

A session SHOULD keep Rank as Project ordering, or as an explicit numeric field when stable ordering
outside one Project is required. Claim and attempt details stay in the execution system or issue history
unless people need them as a filtered Project view.

If a surface requires a single epic status, a session MUST derive it from the stories below and MUST label
it as a rollup, not as an execution fact.

awa closes an epic's issue (as completed) once every story beneath it, through nested epics, is Done or
Won't do; an epic with no stories yet stays open. When one of those stories leaves Done or Won't do, awa
reopens the epic and comments once why. The root of an ongoing workstream (a root whose issue has no
`Source:` line binding it to a planning document) is never closed or reopened by awa, so it can keep taking
new work (if a person closes one, that is their call); a document root closes like any other epic once its
whole tree is finished. awa reads that `Source:` line from the root issue on every run, not from the manifest. An epic a person closed as not planned or duplicate is
left closed; one a person closed as completed while stories remain unfinished is reopened. A session MUST
NOT open or close epic issues by hand.

## Delivery kinds

| Kind | Delivered when | Extra evidence |
|---|---|---|
| `release` with a tag | A published, non-draft, non-pre-release GitHub Release for that tag contains the story's landing commit | The story MUST carry `integration` from Release ready; `delivery.release` MUST name the tag at Done |
| `release` with `next` | The first such Release (by publication time) that contains the landing commit; awa assigns it at `awa release close` | The story MUST carry the same evidence as for a tag |
| `merge` | The landing commit is on the default branch | The story MUST carry `integration` from Release ready |
| `other` | The agent MUST attest with `delivery` evidence | — |

A tag with no published Release delivers nothing. Pre-releases are not supported. The **release milestone**
(titled exactly as the tag) is planning intent; a session MUST NOT use it as a second lifecycle clock. awa
closes a story's issue when it is accepted (Release ready), labels release-delivered stories
`awaiting-release` until the release ships, and moves them to Done at `awa release close`.

## Authority map

| Fact | Authority | GitHub representation |
|---|---|---|
| Why, What, scope, acceptance boundary | Current approved planning source | Source-bound managed issue block and link |
| How and technical approval | Approved design source or recorded design verdict | Design link, verdict, and phase |
| Stable work identity | The issue's managed identity block | Hidden work key kept through edits, retries, closure and reopening |
| Priority and rank | Operator- or product-authorized tracker change | The storage profile's field, ordering, or label |
| Current ownership | Claim system or explicit assignment | The `Attempt:` line |
| Attempt history | Execution events tied to the work key | Comments, checks, or an adapter's event store |
| Candidate implementation | Immutable commit, artifact digest, or deployment identity | Candidate reference |
| Acceptance | Reviewer or verifier verdict bound to candidate and criteria | Evidence and phase |
| Delivery | Release, deployment or publication system | Delivery reference and phase |
| Conversation | Issue comments or an optional communication system | Links or summarized observations |

A session MUST take each fact from the authority this table names. Agent communication systems are
optional observation sources for claims, attempts, results, handoffs and blockers; they do not own the
item's Why, What, priority, durable phase, acceptance, or delivery verdict. Git reconciliation can recover
implementation, integration and delivery facts; it cannot reconstruct an approval, priority decision,
blocker, or conversation that was never recorded, so a session MUST NOT invent one.

## Logical schema

| Logical field | Type | Values |
|---|---|---|
| **Work phase** | Single select | Backlog, Designing, Ready, Executing, Acceptance, Release ready, Done, Won't do |
| **Health** | Single select | On track, At risk, Blocked |
| **Source freshness** | Single select | Current, Reconciliation needed |
| **Priority** | Existing owner or repository policy | A session MUST reuse existing compatible values |
| **Section** | Single select | The epic directly under the root the item belongs to, or the document's general label |
| **Progress** | Text, epics only | Done/total stories below the epic, plus blocked and at-risk counts |
| **Rank** | Number | Order across the whole document |

## Storage profiles

A session MUST inspect, before setup or mutation: whether the owner is an organization or a user; whether
the document already has a repository-linked Project and who owns it; whether organization issue fields
exist and are writable; available issue types, sub-issues, dependencies, workflows and permissions; and
existing fields or labels that already implement the schema. It MUST choose one profile, MUST record it in
the Project or managed epic block, and MUST NOT switch profiles silently later.

- **Organization issue fields.** A session MAY choose this profile only when the repository is
  organization-owned, the feature is available, and the operator authorizes an organization-wide schema.
  The values live on issues and remain consistent across Projects. Organization field changes affect every
  repository, so the session MUST present the exact schema and MUST obtain explicit authorization before it
  creates or changes fields, and it MUST reuse exact existing matches first.
- **Project-local fields** (`project-fields`). A session SHOULD choose this profile for user-owned
  repositories, organizations without writable issue fields, or repository-local configuration. The
  built-in Project Status MUST NOT be used as a competing lifecycle clock: migrated Status values are
  cleared and Status is kept out of managed views. If GitHub automation requires Status, a session MUST
  derive it one way from Work phase and MUST NOT accept Status as an input to Work phase. The Project MAY be
  user- or organization-owned, but its membership and automation remain scoped to the repository.
- **Repository labels.** A session MUST choose this profile only when a capability check proves Projects
  unavailable: an unsupported API, or a credential lacking the scope or permission to create or update the
  Project, recorded in the managed epic with the failing operation and error. A session MUST NOT treat
  these as proof: a missing Project number, no suitably named Project, a Project scoped to another
  document, an issue not yet added to a Project, or setup needing new fields or views. A session MUST keep
  these labels mutually exclusive: `phase/backlog`, `phase/designing`, `phase/ready`, `phase/executing`,
  `phase/acceptance`, `phase/release-ready`, `phase/done`, `health/on-track`, `health/at-risk`,
  `health/blocked`, `source/current`, `source/reconciliation-needed`. Issues and saved searches remain the
  accountability surface. This profile keeps state semantics, but a session MUST NOT describe it as a
  Kanban board.

Moving from labels to `project-fields` is taught in [board/migrate](../board/migrate/GUIDE.md).

## Repository topology

```text
Repository
├── linked Project for PRD-001
│   ├── PRD-001 root epic
│   ├── §3.1 section epic
│   │   ├── story
│   │   └── §3.1.2 subsection epic
│   │       └── story
│   └── story directly under the root (Section: "PRD-001 (general)")
├── linked Project for ADR-059
│   ├── ADR-059 root epic
│   └── stories
└── source, implementation, acceptance and release evidence
```

A workstream with no planning document still has a managed root epic; its work key is the workstream's
identity, and awa never closes or reopens such a root. Epics and stories of one document MUST stay in one
repository and MUST use same-repository native sub-issues; a session SHOULD represent an epic with an
available issue type or a consistent epic label. A session SHOULD record hard dependencies as native
blocked-by relationships where available, and otherwise MUST record stable work-key dependencies in the
managed block. For a contract shared by two repositories, each repository MUST keep its own item and work
key, both MUST carry an additional shared contract identifier, the two items MUST be linked with a
dependency or related-work reference, and an integration item or proof MUST establish interoperability.
