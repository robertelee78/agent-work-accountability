# GitHub model

Use GitHub Issues as durable work records. Default each epic and its stories to one repository and give that repository a dedicated Project when Projects are available. The method must work for organization-owned and user-owned repositories.

## Detect capabilities first

Before setup or mutation, inspect:

- whether the repository owner is an organization or user;
- whether a repository-focused Project already exists and who owns it;
- whether organization issue fields are available and writable;
- available issue types, sub-issues, dependencies, workflows, and permissions; and
- existing fields or labels that already implement the logical schema.

Choose one storage profile and record it in the Project or managed epic block. Do not silently switch profiles later.

## Logical schema

Every profile exposes the same meanings:

| Logical field | Type | Values |
|---|---|---|
| **Work phase** | Single select | Backlog, Designing, Ready, Executing, Acceptance, Release ready, Done |
| **Health** | Single select | On track, At risk, Blocked |
| **Source freshness** | Single select | Current, Reconciliation needed |
| **Priority** | Existing owner/repository policy | Reuse existing values when compatible |

Keep Rank as Project ordering or an explicit numeric field when stable ordering outside one Project is required. Keep claim and attempt details in the execution system or issue history unless people need them as a filtered Project view.

## Storage profiles

### Organization issue fields

Use organization issue fields when the repository is organization-owned, the feature is available, and the operator authorizes an organization-wide schema. The values live on issues and remain consistent across Projects.

Organization field changes affect every repository. Present the exact schema and obtain explicit authorization before creating or changing fields. Discover and reuse exact matches first.

### Project-local fields

Use Project-local `Work phase`, `Health`, and `Source freshness` fields for user-owned repositories, organizations without writable issue fields, or installations that deliberately want repository-local configuration. The Project may be user-owned or organization-owned but its membership and automation remain scoped to the repository.

Do not also use the built-in Project Status as a competing lifecycle clock. Clear migrated Status values
and keep Status out of canonical views. If GitHub automation requires it, derive it one way from Work
phase and never accept Status as an input to Work phase.

### Repository labels

When Projects are unavailable, represent the logical fields with mutually exclusive labels:

- `phase/backlog`, `phase/designing`, `phase/ready`, `phase/executing`, `phase/acceptance`, `phase/release-ready`, `phase/done`
- `health/on-track`, `health/at-risk`, `health/blocked`
- `source/current`, `source/reconciliation-needed`

Issues and saved searches remain the accountability surface. This profile preserves state and synchronization semantics but does not claim that a kanban board exists.

## Repository topology

```text
Repository
├── repository-focused Project, when available
├── epic issue
│   └── same-repository story issues
└── source, implementation, acceptance, and release evidence
```

Represent an epic with an issue in the repository that owns its outcome and use same-repository native sub-issues by default. Use an available issue type or a consistent epic label. Represent independently executable outcomes as child issues. Use native blocked-by relationships for hard dependencies when available; otherwise record stable work-key dependencies in the managed block.

Adding a parent to a Project does not imply that its children are Project items. Add and verify every issue that must appear in a Project view.

For a contract shared by two repositories, keep one implementation item in each repository, give both the same additional contract identifier, and link them with a dependency or related-work reference. Each repository proves its side, and an integration item or proof establishes interoperability.

## Project views

Create these views from the same issues and logical fields:

1. **Lifecycle** — board grouped by Work phase, ordered by Priority and Rank.
2. **Needs attention** — table filtered to Health:At risk or Health:Blocked, grouped by Work phase.
3. **Ready queue** — table for Ready items that are Current, not Blocked, unclaimed, and free of unsatisfied dependencies.
4. **Acceptance** — table for Acceptance items with candidate and evidence visible.
5. **Repository rollup** — table grouped by epic with Done/total and blocked-child rollups.
6. **Reconciliation** — table filtered to Source freshness:Reconciliation needed.

GitHub supports grouping board columns by organization issue fields and Project-local single-select fields. Always read Project membership, fields, view configuration, and issue values back after mutation.

For a view scoped to one epic's native sub-issues, use GitHub's exact unquoted filter form:
`parent-issue:OWNER/REPOSITORY#NUMBER`. Quoting the whole issue reference can produce an empty view.
Verify both prerequisites independently: every child has the native parent relationship, and every child
is itself a Project item. Adding the parent issue to a Project does not add its children.

## API routing

GitHub uses different Project endpoints for organization-owned and user-owned Projects. Route from observed owner type and Project identity; never construct one from a repository-name assumption. Issue field values belong to issues, while Project-local field values belong to Project items.

Useful GitHub documentation:

- [About issue fields in Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-issue-fields)
- [Managing issue fields](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-fields-in-your-organization)
- [Adding items to a Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/adding-items-to-your-project)
- [Adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
- [Creating issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)
- [Filtering Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)

## Automation boundary

Built-in workflows are useful for adding matching issues and simple reactions to close or merge events. A tracker adapter or agent skill remains responsible for source extraction, stable identity, design approval gates, attempt binding, independent acceptance, release semantics, and drift reconciliation. Issue open/closed state alone cannot decide those facts.
