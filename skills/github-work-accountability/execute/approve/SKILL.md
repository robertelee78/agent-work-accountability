---
name: execute-approve
description: Open when product authority and a technical reviewer approved a story's design, so it moves to Ready; or when the operator selects a Backlog story for design work, so it moves to Designing.
---

# Design selected or approved

## When to use

- The operator selects a Backlog story for HLD/LLD work: Backlog → Designing. The session MAY record this
  transition without evidence.
- Product authority and a technical reviewer approve the design, acceptance boundary, dependencies and
  delivery intent: Designing (or Backlog) → Ready.

An agent MUST NOT grant design approval to its own plan. A proposed or draft planning source MUST NOT make
a story Ready ([plan/adr](../../plan/adr/SKILL.md)).

## Steps

1. The session MUST draft the document:

   ```sh
   $RECONCILE --draft --repo OWNER/REPO --root ROOT > doc.json
   ```

2. For a story selected for design, the session MUST set `work_phase` to `Designing`. For each approved
   story, it MUST set `work_phase` to `Ready` and add:

   ```json
   "design_approval": {"ref": "https://github.com/OWNER/REPO/blob/COMMIT/docs/design.md",
                       "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID"}
   ```

   `ref` MUST point to the approved design or to the recorded approval, at an immutable revision.

3. The session MUST dry run, read the plan, then apply:

   ```sh
   $RECONCILE --manifest doc.json
   $RECONCILE --manifest doc.json --apply
   ```

   ```text
   planned_mutations: ["set #3 Work phase=Ready", "set #4 Work phase=Ready", "set #5 Work phase=Ready"]
   ```

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | `"verified": true` | The stories are Ready and appear in `awa ready` in board order. | The session MUST report the receipt. |
| 2 | `reconciliation failed: … design_approval` | Ready was requested without the approval evidence. | The session MUST add the evidence, or leave the story where it was. |
