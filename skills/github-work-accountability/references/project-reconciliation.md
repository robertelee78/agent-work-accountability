# Project reconciliation

Use the bundled reconciler after creating or updating source-bound GitHub issues. It turns one declared repository state into one repository-linked GitHub Project and proves that the user-visible Lifecycle board is usable.

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

The first command is a read-only delta. The second applies that delta, re-inventories GitHub between dependent stages, and prints a JSON receipt. Do not report completion unless `verified` is `true`. On first migration of an existing unmarked Project, add `--adopt-project NUMBER`; later runs discover it through its immutable repository marker. If that adopted Project has one older malformed table named `Lifecycle`, add `--repair-lifecycle`; the command deletes only that conflicting view and replaces it with the verified board while preserving every other view.

## Manifest

```json
{
  "schema": "github-work-accountability/project-v1",
  "repository": "OWNER/REPOSITORY",
  "mode": "repository",
  "project": {
    "owner": "OWNER",
    "title": "REPOSITORY — Delivery",
    "priority_options": ["High", "Medium", "Low"]
  },
  "items": [
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

Use `mode: repository` for the normal full-repository run. It fails if any managed issue is omitted. Use `mode: additive` only while another agent owns desired values for other items; it still repairs Project membership for every managed issue it discovers, while changing field values only for listed items.

An epic uses `"kind": "epic"` and omits `work_phase`; its completion is a child rollup. Every item needs Health and Source freshness. Priority and Rank may be `null`, though a prioritized execution queue should set them.

## Evidence gates

- **Backlog** and **Designing** need no transition evidence in the manifest.
- **Ready** adds `design_approval`.
- **Executing** adds `attempt`.
- **Acceptance** adds an immutable `candidate`.
- **Release ready** adds `verdict`; it must name different `author` and `implementer` values and bind its `candidate` to the candidate evidence `ref`.
- **Done** adds `delivery`, whose `candidate` binds to the accepted candidate.

Every evidence object contains `ref`, `work_key`, and `requirement`. The reconciler validates identity, required shape, candidate binding, and independent reviewer identity. The agent remains responsible for checking that each reference exists and semantically proves the claimed fact before writing the manifest.

## Enforced outcome

A verified run proves all of these together:

- exactly one skill-marked Project selected for the immutable repository node;
- the Project linked to the repository and therefore visible on its Projects tab;
- all open and closed issues carrying a repository-qualified work key present as active Project items;
- Work phase, Health, Source freshness, Priority, and Rank fields provisioned without replacing existing compatible option IDs;
- a `Lifecycle` board whose columns are the Work phase field and whose order uses Priority then Rank;
- each requested value read back from the Project; and
- the Project collection and every issue's Project membership agree on the item identity.

The command serializes local writers by GitHub host/account and repository, batches independent GraphQL mutations, spaces mutative requests, reports measured GraphQL cost, and persists desired state before writes. If it stops, rerun the same manifest; it re-reads current state and resumes only the missing delta.
