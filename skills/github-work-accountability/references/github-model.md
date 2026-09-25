# GitHub model

Use GitHub Issues as durable work records. Keep a planning document's epics and stories in one repository, and give each planning document (PRD, ADR, proposal) or independently managed workstream one repository-linked Project when Projects are available. Epics may nest inside that Project; only leaf stories carry a Work phase. The method must work for organization-owned and user-owned repositories.

## Detect capabilities first

Before setup or mutation, inspect:

- whether the repository owner is an organization or user;
- whether the current planning document already has a repository-linked Project and who owns it;
- whether organization issue fields are available and writable;
- available issue types, sub-issues, dependencies, workflows, and permissions; and
- existing fields or labels that already implement the logical schema.

Choose one storage profile and record it in the Project or managed epic block. Do not silently switch profiles later.

## Canonical document Project

GitHub Projects v2 are owned by a user or organization rather than by a repository. A document Project therefore requires three identities: its owner-level Project identity, an explicit repository link, and the stable work key of the document's root epic. Put `<!-- work-accountability:project-v2 HOST:REPOSITORY_NODE_ID ROOT_WORK_KEY -->` in its README and discover it by that marker rather than by a remembered Project number or title. Multiple Projects linked to one repository are expected when that repository has several planning documents or workstreams.

A workstream with no planning document (for example "Release hardening") still has a managed root epic issue; its work key is the workstream's identity.

After creating or adopting the Project, link it to the repository. An owner-level Project list, a Project URL, or issue membership does not prove this link. Read it back through the repository's own Projects connection and require the exact Project identity to appear there. This link is what makes the Project visible on the repository's **Projects** tab.

Search all open and closed Projects available to the configured owner before creating one. A deleted or missing remembered Project means only that the remembered identity is gone. Reuse only the Project carrying the exact root marker, or explicitly adopt an unmarked Project. Never create one Project per section, epic, agent, session, branch, or execution attempt. A section epic is part of its document's Project; a Project marked for a section's work key is a leftover to merge (see [Project reconciliation](project-reconciliation.md#merging-per-epic-projects-into-one-document-project)). A merged Project is closed, not deleted, and its README carries `<!-- work-accountability:project-superseded HOST:REPOSITORY_NODE_ID OLD_WORK_KEY -> NEW_PROJECT_URL -->`; it is never selected or reopened by the reconciler.

Select the repository-label profile only after an actual capability check proves that Project operations are unavailable. Valid evidence includes an unsupported API or a credential that lacks the required scope or resource permission and cannot create or update the repository Project. Record the failing operation and error in the managed epic. These are not evidence of unavailability:

- a particular Project number does not exist;
- no suitably named Project exists yet;
- the only existing Project is scoped to another document;
- an issue has not been added to a Project yet; or
- Project setup would require creating fields or views.

Do not declare `project-fields` in an issue before the Project exists. Once selected, link the Project to the repository, include the direct Lifecycle-view URL in every scoped managed block, add the root epic and every native sub-issue at any depth as Project items, remove managed items belonging to other documents, set the logical fields, and read the repository link, membership, views, values, and the cards each view shows back. Issue creation may precede Project creation, but reconciliation is incomplete until these read-backs succeed.

When migrating from the repository-label fallback, change the managed issue block to `project-fields`, record the canonical Project URL, and remove only the mutually exclusive `phase/`, `health/`, and `source/` labels after the Project has passed its link, membership, field, and Kanban checks. Preserve all unrelated labels and human prose. This prevents fallback labels and Project fields from becoming two writable lifecycle clocks.

## Logical schema

Every profile exposes the same meanings:

| Logical field | Type | Values |
|---|---|---|
| **Work phase** | Single select | Backlog, Designing, Ready, Executing, Acceptance, Release ready, Done |
| **Health** | Single select | On track, At risk, Blocked |
| **Source freshness** | Single select | Current, Reconciliation needed |
| **Priority** | Existing owner/repository policy | Reuse existing values when compatible |
| **Section** | Single select | The epic directly under the root that the item belongs to, or the document's general label |
| **Progress** | Text, epics only | Done/total stories below the epic, plus blocked and at-risk counts |

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
├── explicitly linked Project for PRD-001
│   ├── PRD-001 root epic
│   ├── §3.1 section epic
│   │   ├── story
│   │   └── §3.1.2 subsection epic
│   │       └── story
│   ├── §3.2 section epic
│   │   └── story
│   └── story directly under the root (Section: "PRD-001 (general)")
├── explicitly linked Project for ADR-059
│   ├── ADR-059 root epic
│   └── stories
└── source, implementation, acceptance, and release evidence
```

At most three levels sit below the root: root → section → subsection → story. An epic groups sub-issues (a new section may have none yet); it carries no Work phase, and its Health and Progress are rollups of the stories below it.

Represent an epic with an issue in the repository that owns its outcome and use same-repository native sub-issues by default. Use an available issue type or a consistent epic label. Represent independently executable outcomes as leaf stories; group them under section epics when the planning document has sections. Use native blocked-by relationships for hard dependencies when available; otherwise record stable work-key dependencies in the managed block.

Adding a parent to a Project does not by itself prove that its children are Project items. (GitHub may add new sub-issues to their parent's Project automatically; the reconciler still adds and verifies every tree issue explicitly.) Add and verify every issue that must appear in a Project view. A run using the Project-local profile is incomplete while the Project is absent from the repository's Projects connection or any managed issue has empty Project membership.

For a contract shared by two repositories, keep one implementation item in each repository, give both the same additional contract identifier, and link them with a dependency or related-work reference. Each repository proves its side, and an integration item or proof establishes interoperability.

## Project views

The reconciler creates and verifies two views:

1. **Lifecycle** — board filtered by `has:work-phase`, grouped by Work phase, and ordered by Priority and Rank. It shows every story in the document and no epics. It is created first, so it is the first tab.
2. **By section** — table grouped by Section and ordered by Rank, with Work phase, Health, Progress, and Priority visible. It shows the whole document, sections carrying their Progress.

Views people add are kept. The following are useful optional projections; their absence does not invalidate reconciliation:

- **Needs attention** — Health At risk or Blocked.
- **Ready queue** — Ready stories that are Current, not Blocked, unclaimed, and free of unsatisfied dependencies.
- **Acceptance** — stories in Acceptance with candidate and evidence visible.
- **Reconciliation** — Source freshness: Reconciliation needed.

GitHub supports grouping by organization issue fields and Project-local single-select fields. Always read Project membership, fields, view configuration, issue values, and the cards each view shows back after mutation.

Current GitHub view creation is not covered by `gh project`. The reconciler uses the versioned REST Projects Views endpoint to create both views, with numeric field database IDs in `vertical_group_by` (board) or `group_by` (table), then reads their node-ID configuration back through GraphQL. It then passes each view's saved filter to `ProjectV2.items(query:)`, GitHub's own filter engine, and requires the returned cards to be exactly what people should see. A plain table named Lifecycle, a board without Work phase columns, or a filter that hides a story fails verification.

Filter details observed on GitHub:

- Name a field that contains a space by its hyphenated lowercase form: `has:work-phase`. GitHub's API also accepts `has:"Work phase"`, but the web page rejects it ("Invalid value \"Work phase\" for has"): Chrome then shows every card under a warning and Safari renders a blank Project. `has:Work phase` returns nothing. The API and the web page do not parse filters identically, so write only the hyphenated form.
- Value filters on such fields (`"Work phase":Acceptance`) return nothing through the API; use `has:` or grouping.
- `parent-issue:OWNER/REPOSITORY#NUMBER` (unquoted) matches only direct sub-issues, which is why document Projects do not use it.
- GitHub has no API to reorder views, and it opens each person's last-visited view.
- GitHub refuses to delete a Project's last view ("Cannot destroy the last remaining view of a project"); create a replacement before deleting.

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
