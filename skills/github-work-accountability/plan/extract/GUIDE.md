---
name: plan-extract
description: Open to decompose an ADR, PRD, proposal or design document into one epic/story tree and validate it against the exact source bytes before any issue is created. Teaches the extraction-v3 manifest, story shape, coverage, and validate_extraction.py.
---

# Extract a planning source into epics and stories

A session working this leaf MUST also follow the [plan domain rules](../GUIDE.md).

## When to use

A session MUST use this leaf when a planning document (or a material revision of one) has to become
managed work: one root epic, optional section epics that mirror the document's sections, and stories. For an
ADR, the session MUST first complete [plan/adr](../adr/GUIDE.md) to discover the repository's ADR policy and
bind its canonical bytes.

## Steps

1. **Extraction boundary.** The session MUST record the exact repository, source path, commit
   and Git blob; the source's own approval or lifecycle status; the intended outcome and declared delivery
   boundary; the explicit implementation order and dependencies; the acceptance criteria, exclusions and
   unresolved decisions; and any existing work keys that already represent the source.

   ```sh
   git rev-parse COMMIT:docs/adr/ADR-001-example.md     # the blob
   ```

2. **Tree proposal.** One planning document MUST become one tree. The tree MUST mirror the document's own
   outline rather than invent groupings. A story that belongs to no section MUST sit directly under the
   root.
   - The tree MUST NOT go deeper than three levels below the root: root → section → subsection → story.
   - Every epic directly under the root MUST carry a unique, short `section_label`; deeper epics MUST NOT.
     The label becomes the Section value people filter and group by.
   - Every story MUST have a stable work key, a concise outcome title, exact source excerpts, one
     observable outcome, acceptance criteria stated independently of implementation steps, predecessor
     work keys, the intended validation method, the delivery boundary, and any explicit non-goals.
   - Every story MUST be a concise execution brief: **Why** (the problem and why it matters), **What**
     (one bounded outcome and essential exclusions), **How** (known design constraints and the few
     references needed to start), and **Acceptance** (observable completion and how to verify it).
     The session MUST derive these from the source and inspected implementation, not invent missing
     decisions. An unresolved decision that affects execution MUST be stated explicitly.
   - The session SHOULD use a short paragraph or a few bullets per section, proportional to the work.
     It MUST link relevant ADR sections, code paths or symbols, and documentation when they exist,
     with a short explanation of their relevance, rather than paste background or prescribe every step.
     It MUST NOT add speculative scope or exhaustive reading lists just to fill the sections.
   - A story SHOULD have one acceptance boundary that one execution session can reasonably advance. The
     session SHOULD split a story when it has independently valuable outcomes, different owners, different
     delivery boundaries, or acceptance evidence that can pass and fail independently, and SHOULD keep tasks
     together when splitting would create fragments with no independently reviewable result.
   - Dependencies MUST express required order, not textual order. A story SHOULD NOT depend on the previous
     story when the work can proceed independently. B MUST depend on A only when B needs A's output (code,
     data, decision or release); touching the same files is merge order, not a dependency.

3. **Manifest.** The session MUST write it outside the product repository:

   ```json
   {
     "schema": "github-work-accountability/extraction-v3",
     "source": {"kind": "adr", "path": "docs/adr/ADR-001-release-manifest.md",
                "commit": "ce64fef286db7193a8a4cf1d0b30ea2e5e06a6f1",
                "blob": "7cda56f1d4b38f24497c3ce533831702d3654420"},
     "root": {"key": "OWNER/REPO:ADR-001", "title": "Release manifest",
              "source_quotes": ["# ADR-001 Release manifest"]},
     "epics": [],
     "stories": [{
       "key": "OWNER/REPO:ADR-001:freeze", "parent": "OWNER/REPO:ADR-001",
       "title": "Freeze the release manifest",
       "source_quotes": ["The release workflow MUST freeze a manifest bound to the exact tag and asset digest."],
       "outcome": "A generated manifest is bound to immutable release inputs.",
       "acceptance": ["The manifest records the exact tag and asset digest."],
       "validation": "Run the manifest verifier against the candidate tag.",
       "delivery_boundary": "Included in a published GitHub release.",
       "delivery": {"kind": "release", "release": "next"},
       "dependencies": []
     }],
     "coverage": [{"source_quote": "The release workflow MUST freeze a manifest bound to the exact tag and asset digest.",
                   "stories": ["OWNER/REPO:ADR-001:freeze"]}]
   }
   ```

   Every story MUST declare `delivery`: `{"kind": "release", "release": "TAG"}` when the source or the
   operator names the release, `{"kind": "release", "release": "next"}` when it ships in the first full
   release that contains it, `{"kind": "merge"}` when merging to the default branch is the delivery, or
   `{"kind": "other"}`. `delivery_boundary` MUST stay the human-readable sentence. A story with no
   `delivery` keeps the pre-0.10 behaviour.

   `coverage` is the auditable coverage assertion. It MUST include every material normative obligation,
   implementation-order entry, acceptance proof, exclusion and unresolved decision, each quoted exactly and
   pointing to one or more story keys. Entries MAY be combined only when one exact excerpt contains the
   whole obligation.

   Why and How are issue-writing guidance, not new required manifest fields or a schema change.
   The session MUST use the bound source and inspected references to write them; `outcome`,
   `acceptance` and `validation` already supply What and the acceptance proof. Exact excerpts MUST
   remain in the extraction for provenance; the issue SHOULD use only a short relevant excerpt and
   precise source references rather than repeat the extraction's background.

4. **Validation.** The session MUST validate against the source bytes, and MUST also validate against the
   default branch when the source is expected to be there already:

   ```sh
   python3 "$SKILL_DIR/scripts/validate_extraction.py" MANIFEST.json --repo /path/to/repository
   python3 "$SKILL_DIR/scripts/validate_extraction.py" MANIFEST.json --repo /path/to/repository --against origin/main
   ```

5. **Coverage and task review.** The session MUST review coverage separately from the validator, mapping
   every obligation to a story or to a documented reason it creates no work. It MUST also read each brief
   as the executing agent: can the agent understand the problem, find the starting points, stay within
   scope and recognize completion without reconstructing the whole planning discussion? It MUST fix
   essential gaps and remove duplicated background before creating the issues
   ([plan/issues](../issues/GUIDE.md)), and MUST copy each story's `planned_delivery` line from the report
   into its managed block. This is part of the existing review, not an additional approval gate.

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | `"ok": true`, `planned_delivery` per story, `errors: []` | The manifest is source-bound and its tree is well formed. | The session MUST review coverage before creating issues. |
| 1 | `"ok": false`, `errors: [...]` | At least one check failed; nothing is wrong on GitHub. | The session MUST fix each listed error and rerun. |
| 1 | `stories[0].source_quotes[0] is not exact source text` | A quote is not a byte-for-byte substring of the source. | The session MUST re-copy it from the source and MUST NOT paraphrase a quote. |
| 1 | (with `--against`) the blob at the comparison ref differs | The source changed since extraction. | The session MUST reconcile ([plan/drift](../drift/GUIDE.md)); the validator does not decide how far items move back. |
| 0 | `warnings` naming `extraction-v2` or `extraction-v1` | An older schema was accepted. | The session SHOULD upgrade the manifest to extraction-v3 so delivery is structured. |

The report's `planned_delivery` line, for example `Planned delivery: release next`, is planning intent, not
one of awa's record lines. `--draft` uses it as the story's delivery until awa has recorded one; afterwards
awa's record takes precedence over copied planning text. For current release scheduling, live GitHub
milestone identity and title override stale copied tags; they MUST NOT restore an obsolete assignment.
Done stories retain their historical delivery evidence. When a draft notes a disagreement, the session
MUST inspect the live schedule and current report before making an intended change
([release/plan](../../release/plan/GUIDE.md)).

The validator checks that: the recorded commit and blob identify the exact bytes; every quote appears
byte-for-byte; every work key is unique; every dependency names an extracted story; the dependency graph is
acyclic; the tree has one root, no cycles, at most three levels below the root and a unique `section_label`
on every epic directly under the root; and every story has one outcome, a validation method, a delivery
boundary, a structured delivery, and at least one acceptance criterion. It cannot prove semantic
completeness, so a session MUST NOT present a passing validation as proof of complete coverage.

## Dropping down a level

When the validator's message is unclear, the session SHOULD check the binding by hand:

```sh
git -C REPO cat-file -e COMMIT:PATH          # the commit really contains the path
git -C REPO rev-parse COMMIT:PATH            # the blob to record
git -C REPO show COMMIT:PATH                 # the exact bytes quotes are checked against
```

The older `extraction-v2` (no structured delivery) and `extraction-v1` (one `epic` plus `stories`, read as a
root with every story directly under it) are accepted with a warning; for their stories `--draft` proposes a
delivery from the boundary text and flags each proposal, which the session MUST review before applying.
