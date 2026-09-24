# Project reconciliation

Use the bundled reconciler after creating or updating source-bound GitHub issues. One manifest declares one epic and its direct native child stories. The reconciler turns that scope into one repository-linked GitHub Project and proves that its user-visible Lifecycle Kanban is usable.

## Invocation

Write the manifest outside the product repository unless the repository deliberately tracks operations configuration:

```sh
SKILL_DIR="/path/to/skills/github-work-accountability"
python3 "$SKILL_DIR/scripts/reconcile_project.py" \
  --manifest /var/tmp/work-accountability-project.json \
  --user EXPECTED_GITHUB_LOGIN

python3 "$SKILL_DIR/scripts/reconcile_project.py" \
  --manifest /var/tmp/work-accountability-project.json \
  --user EXPECTED_GITHUB_LOGIN \
  --apply
```

The first command is a read-only delta. The second applies that delta, re-inventories GitHub between dependent stages, and prints a JSON receipt. Do not report completion unless `verified` is `true`. On first migration of an existing unmarked or legacy repository-wide Project, add `--adopt-project NUMBER`; later runs discover it through its immutable repository-plus-epic marker. If the adopted Project has an older malformed `Lifecycle` view, add `--repair-lifecycle`.

## Manifest

```json
{
  "schema": "github-work-accountability/project-v3",
  "repository": "OWNER/REPOSITORY",
  "scope": {
    "epic_number": 122,
    "epic_work_key": "OWNER/REPOSITORY:SOURCE:EPIC"
  },
  "project": {
    "owner": "OWNER",
    "title": "REPOSITORY — EPIC Delivery",
    "priority_options": ["High", "Medium", "Low"],
    "lifecycle_only": true
  },
  "items": [
    {
      "number": 122,
      "work_key": "OWNER/REPOSITORY:SOURCE:EPIC",
      "kind": "epic",
      "health": "On track",
      "source_freshness": "Current",
      "priority": "High",
      "rank": 0,
      "evidence": {}
    },
    {
      "number": 123,
      "work_key": "OWNER/REPOSITORY:SOURCE:STORY",
      "kind": "story",
      "work_phase": "Ready",
      "health": "On track",
      "source_freshness": "Current",
      "priority": "High",
      "rank": 1,
      "evidence": {
        "design_approval": {
          "ref": "docs/design.md@COMMIT",
          "work_key": "OWNER/REPOSITORY:SOURCE:STORY",
          "requirement": "SOURCE_COMMIT:REQUIREMENT_ID"
        }
      }
    }
  ]
}
```

The scope epic must be a managed issue and must appear in `items` with `kind: epic`. The reconciler reads GitHub's direct native sub-issues and requires `items` to match the epic plus every child exactly. Omitting a child or including an issue from another epic fails before mutation. A child that itself has sub-issues fails closed: give that child its own epic Project or flatten the story structure. Run a separate manifest for each active epic; local repository locks serialize concurrent writers without combining their Projects.

An epic uses `"kind": "epic"` and omits `work_phase`; its completion is a child rollup. Every item needs Health and Source freshness. Priority and Rank may be `null`, though a prioritized execution queue should set them. `lifecycle_only: true` makes Lifecycle the only view, so the Project opens directly as a Kanban. Set it to `false` only when deliberately retaining additional human-maintained views.

## Evidence gates

- **Backlog** and **Designing** need no transition evidence in the manifest.
- **Ready** adds `design_approval`.
- **Executing** adds an active `attempt` event.
- **Acceptance** adds an immutable `candidate`.
- **Release ready** adds `verdict`; it must name different `author` and `implementer` values and bind its `candidate` to the candidate evidence `ref`.
- **Done** adds `delivery`, whose `candidate` binds to the accepted candidate.

Every evidence object contains `ref`, `work_key`, and `requirement`. Attempt evidence additionally contains `ref_kind`, `attempt_id`, `actor`, `started_at`, and `state`. `ref_kind` is `issue_comment`, `communication_event`, or `tracker_event`; `started_at` is timezone-qualified RFC3339. Executing requires `state: active`. Acceptance and later phases require `state: submitted` because the attempt has produced the candidate under review.

An attempt `ref` names the durable attempt-start event. A branch, pull request, source commit, candidate commit, changed file, claim, or agent process is not an attempt-start event and cannot be reused to move several stories to Executing. Attempt IDs and event references are unique within the epic manifest. An `issue_comment` reference must point to the exact story it advances. The reconciler rejects GitHub commit URLs in this field. When an attempt fails, is released, expires, or is cancelled without a submitted candidate, move the story back to Ready and retain the ended attempt in issue history. A later retry gets a new `attempt_id` and a new start event.

The reconciler validates identity, required shape, active/submitted state, candidate binding, and independent reviewer identity. The agent remains responsible for checking that each reference exists and semantically proves the claimed fact before writing the manifest.

## Enforced outcome

A verified run proves all of these together:

- exactly one skill-marked Project selected for the immutable repository node and epic work key;
- the Project linked to the repository and therefore visible on its Projects tab;
- the root epic and every direct native child story present as active Project items, with managed issues from other epics absent;
- each scoped managed issue records `project-fields` and the direct Lifecycle-view URL in its bounded block, while obsolete `phase/`, `health/`, and `source/` fallback labels are removed without disturbing other labels or human prose;
- Work phase, Health, Source freshness, Priority, and Rank fields provisioned without replacing existing compatible option IDs;
- a `Lifecycle` board filtered to the epic's children, whose columns are Work phase and whose order uses Priority then Rank;
- when `lifecycle_only` is true, no table or secondary view can hide the Kanban as the Project's landing surface;
- each requested value read back from the Project; and
- the Project collection and every issue's Project membership agree on the item identity.

The command serializes local writers by GitHub host/account and repository, batches independent GraphQL mutations, spaces mutative requests, reports measured GraphQL cost, and persists desired state before writes. If it stops, rerun the same manifest; it re-reads current state and resumes only the missing delta.
