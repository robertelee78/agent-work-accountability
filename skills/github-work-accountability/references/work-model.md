# Work model

The primary board is a left-to-right delivery flow. Its columns answer **how far has this work progressed from a credible idea to released value?** They do not encode ownership, priority, or trouble.

## Work phase

| Phase | Meaning | Entry fact | Exit fact |
|---|---|---|---|
| **Backlog** | A possible work item with a crisp problem statement: the Why. A proposed What is useful but optional and may be intentionally rough. | The Why is recorded well enough for another person or agent to understand the problem. | The operator selects the item for HLD/LLD work. |
| **Designing** | Product and engineering iterate on the high-level design, then the low-level design. | Design work starts against the recorded Why and any proposed What. | Product authority and a technical reviewer approve the design, acceptance boundary, dependencies, and delivery intent. |
| **Ready** | The Why, What, HLD, and LLD are sufficiently aligned for confident execution. | Design approval authorizes the item for the execution queue. | A specific execution attempt starts. |
| **Executing** | Engineers or agents are actively producing the agreed deliverable. | An attempt explicitly names the work key. | The implementer submits an immutable candidate and claims it is ready for testing and acceptance. |
| **Acceptance** | Testing and product acceptance prove whether the candidate meets the agreed requirement. | A candidate is identified by commit, artifact digest, deployment identity, or another immutable reference. | An authorized independent verdict accepts every required criterion for that candidate. |
| **Release ready** | Acceptance passed and the item awaits its declared release or delivery action. | The acceptance verdict is recorded. | The feature is included in a proven release, deployment, publication, enablement, or other declared delivery. |
| **Done** | The accepted outcome is available at its declared delivery boundary. | Release or delivery evidence is recorded. | Terminal unless the requirement changes or delivered behavior regresses. |

Backlog intentionally collapses informal Idea and Why/What confirmation stages. A human meeting is not a required ritual. Agents may challenge an unclear Why or What, but the transition to Designing is an operator prioritization decision rather than proof that a meeting occurred.

HLD and LLD are design evidence, not separate board columns. Record their links and approval verdicts on the issue so someone can drill into the design without making the main flow wider.

When importing existing work, place it at the furthest phase supported by recorded facts. Do not replay old transitions merely to populate history, and do not infer a later phase from code presence alone.

## Independent axes

Do not create more phase columns for these facts:

| Axis | Values or shape | Meaning |
|---|---|---|
| **Health** | On track, At risk, Blocked | Whether a known condition threatens or prevents the next phase transition. |
| **Priority** | Repository policy, commonly Urgent, High, Medium, Low | Relative importance chosen by the operator or product authority. |
| **Rank** | Ordered position within a priority | Which eligible item should be taken next. |
| **Ownership** | Unclaimed or a renewable claim naming actor and session | Who currently has the right to run an attempt. It is not progress. |
| **Attempt** | ID, actor, start, heartbeat, candidate, end | One bounded execution or verification try. |
| **Attempt outcome** | Submitted, verified success, work failure, infrastructure failure, cancelled, expired, indeterminate | What happened to one attempt. A failed attempt does not delete or complete the work item. |
| **Source freshness** | Current, Reconciliation needed | Whether the issue still represents the current planning source. |

`Blocked` is Health, never a column in the delivery flow. A blocked item stays in the phase it has actually reached and names the condition that prevents its next transition. Clearing the condition changes Health; it does not itself advance the phase.

Use `At risk` when the next transition is threatened but meaningful work can continue. Use `Blocked` only when a named condition prevents the next transition. Record the condition, the responsible dependency or decision, and the time it became blocking.

## Transition rules

- Phase transitions require facts, not card movement. The board reflects the facts after they are recorded.
- Ready means execution is authorized and queued. A later impediment may make a Ready item Blocked without revoking its approved design.
- A branch, claim, commit, changed file, open pull request, passing process, or agent exit cannot by itself move an item to Executing, Acceptance, Release ready, or Done.
- Move Ready to Executing only when an attempt explicitly names the work key.
- The attempt start must be a durable event with its own attempt ID, actor, and start time. A branch, commit, pull request, claim, changed file, or agent process is not that event and cannot be reused as attempt evidence for several stories.
- When an attempt fails, is released, expires, or is cancelled without submitting a candidate, return the story to Ready and keep the ended attempt in history. A retry starts a new attempt ID before the story returns to Executing.
- Move Executing to Acceptance only when the item has a specific candidate and the required evidence has been submitted.
- Move Acceptance to Release ready only on an acceptance verdict independent of the implementation assertion.
- Move Release ready to Done only when the item's declared delivery boundary is crossed. A merge is delivery only when the item explicitly declares merge as that boundary.
- An acceptance rejection returns the item to Ready or Executing according to whether a new implementation attempt has started. Keep the rejected candidate and evidence in history.
- A requirement change sets Source freshness to Reconciliation needed. Reconciliation retains the stable work identity and moves phase backward only as far as the changed decision invalidates.
- A delivered-behavior regression reopens the item to Acceptance when the accepted candidate is still the subject of diagnosis, or to Ready when a new implementation is authorized. Keep the earlier delivery evidence in history.
- Recompute Health when a phase changes. Do not silently clear At risk or Blocked until the recorded condition is resolved; do not carry a resolved condition into the next phase.
- An infrastructure failure ends or pauses an attempt; it is not proof that the product work failed.

## Readiness and dependencies

An item can be selected for execution only when:

1. its phase is Ready;
2. Health is not Blocked;
3. every required predecessor is Done at its own declared delivery boundary;
4. no current claim owns it; and
5. the planning source is Current.

Priority and rank choose among eligible items. They never override an unsatisfied dependency, failed approval gate, or active claim.

## Epics

An epic is a rollup over the work below it. A planning document has one root epic; section and subsection epics may sit below it, at most three levels deep (root → section → subsection → story). Only leaf stories carry Work phase and pass through the transition gates above. Never assign an epic a manually maintained phase: it would hide children spread across several phases.

Each epic shows derived facts instead:

- **Health** — the worst Health of the stories below it (Blocked, then At risk, then On track). Clearing a story's condition clears the epic's.
- **Progress** — Done stories out of all stories below it, with blocked and at-risk counts.
- **Source freshness** — an epic cannot be Current while anything below it needs reconciliation; it may itself need reconciliation when its own source text changed.

If a surface requires a single epic status, derive it from the stories below and label it as a rollup rather than an execution fact.
