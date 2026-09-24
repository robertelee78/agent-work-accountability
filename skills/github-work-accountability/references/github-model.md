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

## Canonical repository Project

GitHub Projects v2 are owned by a user or organization rather than by a repository. A repository-focused Project therefore requires two distinct relationships: its owner-level identity and an explicit repository link. When Projects are available, use one linked Project as the repository's delivery surface across all epics. Put a stable repository marker such as `<!-- work-accountability:project OWNER/REPOSITORY -->` in its README and discover by that marker rather than by a remembered Project number or title.

After creating or adopting the Project, link it to the repository. An owner-level Project list, a Project URL, or issue membership does not prove this link. Read it back through the repository's own Projects connection and require the exact Project identity to appear there. This link is what makes the Project visible on the repository's **Projects** tab.

Search all open and closed Projects available to the configured owner before creating one. A deleted or missing remembered Project means only that the remembered identity is gone. An existing Project named for one ADR or epic does not make Projects unavailable. If that Project is already the skill-managed delivery surface for the repository, generalize its title and README to repository scope and retain the ADR-specific board as a filtered view. Otherwise create the marked repository Project. Do not create one Project per agent, branch, ADR, or epic.

Select the repository-label profile only after an actual capability check proves that Project operations are unavailable. Valid evidence includes an unsupported API or a credential that lacks the required scope or resource permission and cannot create or update the repository Project. Record the failing operation and error in the managed epic. These are not evidence of unavailability:

- a particular Project number does not exist;
- no suitably named Project exists yet;
- the only existing Project is scoped to another epic;
- an issue has not been added to a Project yet; or
- Project setup would require creating fields or views.

Do not declare `project-fields` in an issue before the Project exists. Once selected, link the Project to the repository, include its URL in the managed block, add every managed epic and story as a Project item, set its logical fields, and read the repository link, membership, and values back. Issue creation may precede Project creation, but reconciliation is incomplete until all three read-backs succeed.

When migrating from the repository-label fallback, change the managed issue block to `project-fields`, record the canonical Project URL, and remove only the mutually exclusive `phase/`, `health/`, and `source/` labels after the Project has passed its link, membership, field, and Kanban checks. Preserve all unrelated labels and human prose. This prevents fallback labels and Project fields from becoming two writable lifecycle clocks.

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

When the capability check above proves that Projects are unavailable, represent the logical fields with mutually exclusive labels:

- `phase/backlog`, `phase/designing`, `phase/ready`, `phase/executing`, `phase/acceptance`, `phase/release-ready`, `phase/done`
- `health/on-track`, `health/at-risk`, `health/blocked`
- `source/current`, `source/reconciliation-needed`

Issues and saved searches remain the accountability surface. This profile preserves state and synchronization semantics but does not claim that a kanban board exists.

## Repository topology

```text
Repository
├── explicitly linked repository-focused Project, when available
├── epic issue
│   └── same-repository story issues
└── source, implementation, acceptance, and release evidence
```

Represent an epic with an issue in the repository that owns its outcome and use same-repository native sub-issues by default. Use an available issue type or a consistent epic label. Represent independently executable outcomes as child issues. Use native blocked-by relationships for hard dependencies when available; otherwise record stable work-key dependencies in the managed block.

Adding a parent to a Project does not imply that its children are Project items. Add and verify every issue that must appear in a Project view. A run using the Project-local profile is incomplete while the Project is absent from the repository's Projects connection or any managed issue has empty Project membership.

For a contract shared by two repositories, keep one implementation item in each repository, give both the same additional contract identifier, and link them with a dependency or related-work reference. Each repository proves its side, and an integration item or proof establishes interoperability.

## Project views

The reconciler must create and verify this view:

1. **Lifecycle** — board grouped by Work phase, ordered by Priority and Rank.

The following views are optional projections. Add them when their filters are useful; their absence does not invalidate a first reconciliation:

2. **Needs attention** — table filtered to Health:At risk or Health:Blocked, grouped by Work phase.
3. **Ready queue** — table for Ready items that are Current, not Blocked, unclaimed, and free of unsatisfied dependencies.
4. **Acceptance** — table for Acceptance items with candidate and evidence visible.
5. **Repository rollup** — table grouped by epic with Done/total and blocked-child rollups.
6. **Reconciliation** — table filtered to Source freshness:Reconciliation needed.

GitHub supports grouping board columns by organization issue fields and Project-local single-select fields. Always read Project membership, fields, view configuration, and issue values back after mutation.

Current GitHub view creation is not covered by `gh project`. The reconciler uses the versioned REST Projects Views endpoint to create `Lifecycle` with the numeric Work phase database ID in `vertical_group_by`, then reads its node-ID configuration back through GraphQL. A plain table named Lifecycle, or a board without Work phase columns, fails verification.

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
- [Listing repository issues through the REST API](https://docs.github.com/en/rest/issues/issues#list-repository-issues)

## Automation boundary

Built-in workflows are useful for adding matching issues and simple reactions to close or merge events. A tracker adapter or agent skill remains responsible for source extraction, stable identity, design approval gates, attempt binding, independent acceptance, release semantics, and drift reconciliation. Issue open/closed state alone cannot decide those facts.
