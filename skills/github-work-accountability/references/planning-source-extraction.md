# Planning-source extraction

Use this adapter for ADRs, PRDs, proposals, roadmaps, design documents, and similar sources. The source format may vary; the extracted work contract does not.

For an ADR, first use [the ADR source adapter](adr-sources.md) to discover the repository's native policy, distinguish decision status from execution status, and bind the canonical Git bytes. Do not assume one global ADR template or lifecycle.

## Extraction boundary

First identify:

- the exact repository, source path, commit, and git blob;
- the source's own approval/lifecycle status;
- the intended outcome and declared delivery boundary;
- explicit implementation order and dependencies;
- acceptance criteria, exclusions, and unresolved decisions; and
- existing GitHub work keys that already represent the source.

Do not decompose a merely proposed source as if it were execution-ready. Items inherit the furthest phase justified by actual product and technical approvals.

## Model proposal, deterministic validation

A model may interpret prose and propose the epic/story tree. Before any GitHub mutation, a deterministic check must establish that:

- the recorded commit and blob identify the exact source bytes;
- every quoted excerpt appears byte-for-byte in those source bytes;
- every work key is unique;
- every dependency names an extracted story;
- the dependency graph is acyclic;
- the epic tree has one root, no cycles, at most three levels below the root, and a unique section label on every epic directly under the root; and
- every story has one outcome, a validation method, a declared delivery boundary, a structured delivery, and at least one acceptance criterion.

The validator cannot prove semantic completeness. Review coverage separately by mapping every normative obligation, implementation-order entry, acceptance proof, explicit exclusion, and unresolved decision to a story or to a documented reason it creates no work.

## Story shape

A story should have one acceptance boundary that one execution session can reasonably advance. Give it:

- a stable work key;
- a concise outcome title;
- exact source excerpts;
- one observable outcome;
- acceptance criteria stated independently of implementation steps;
- predecessor work keys;
- the intended validation method;
- the delivery boundary; and
- any explicit non-goals.

Split a story when it has independently valuable outcomes, different owners, different delivery boundaries, or acceptance evidence that can pass and fail independently. Keep tasks together when splitting would create implementation fragments with no independently reviewable result.

Dependencies express required order. Textual order is not enough. Avoid making every story depend on the previous story when work can proceed independently.

## Extraction manifest

Use `scripts/validate_extraction.py` for a manifest shaped like:

```json
{
  "schema": "github-work-accountability/extraction-v3",
  "source": {
    "kind": "prd",
    "path": "docs/prd/PRD-001-example.md",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "blob": "fedcba9876543210fedcba9876543210fedcba98"
  },
  "root": {
    "key": "OWNER/REPO:PRD-001",
    "title": "Outcome title",
    "source_quotes": ["exact text from the source"]
  },
  "epics": [
    {
      "key": "OWNER/REPO:PRD-001:S3.1",
      "parent": "OWNER/REPO:PRD-001",
      "section_label": "§3.1 Direct mode",
      "title": "Direct mode",
      "source_quotes": ["exact section heading or text"]
    }
  ],
  "stories": [
    {
      "key": "OWNER/REPO:PRD-001:S3.1:handshake",
      "parent": "OWNER/REPO:PRD-001:S3.1",
      "title": "Freeze release manifest",
      "source_quotes": ["exact text from the source"],
      "outcome": "A generated manifest is bound to immutable release inputs.",
      "acceptance": ["The manifest records the exact tag and asset digest."],
      "validation": "Run the release-manifest verifier against the candidate tag.",
      "delivery_boundary": "Included in a published GitHub release.",
      "delivery": {"kind": "release", "release": "v0.3.0"},
      "dependencies": []
    }
  ],
  "coverage": [
    {
      "source_quote": "exact normative or acceptance text from the source",
      "stories": ["OWNER/REPO:PRD-001:S3.1:handshake"]
    }
  ]
}
```

One planning document becomes one tree: a root epic, optional section epics that mirror the document's sections, and stories. Mirror the document's own outline rather than inventing groupings; a story that belongs to no section sits directly under the root. The validator checks that every parent exists and is an epic, that the tree has no cycles and at most three levels below the root (root → section → subsection → story), and that every epic directly under the root has a unique `section_label` while deeper epics have none. The section label becomes the Section value people filter and group by on the document's Project, so keep it short.

Every story declares `delivery`, the same structure the reconciler uses: `{"kind": "release", "release": "v0.3.0"}` when the source or the operator names the release, `{"kind": "release", "release": "next"}` when it ships in whichever full release first contains it, `{"kind": "merge"}` when merging to the default branch is the delivery, or `{"kind": "other"}`. The validator checks the kind and the tag. `delivery_boundary` stays as the human-readable sentence.

The validator's report gives each story's `planned_delivery` line, for example `Planned delivery: release v0.3.0`. Copy it exactly into the story issue's managed block when you create or update the issue. `--draft` then uses it as the story's delivery without guessing. It is planning intent, not one of awa's record lines: once awa has recorded a delivery, its record wins, and a later plan that disagrees appears as a draft note telling you to move the story with a `milestone_change_reason`.

The older `extraction-v2` (no structured delivery) and `extraction-v1` (one `epic` plus `stories`, read as a root with every story directly under it) are still accepted with a warning; for their stories `--draft` proposes delivery from the boundary text and flags each proposal for review.

`coverage` is the auditable coverage assertion. Include every material normative obligation, implementation-order entry, acceptance proof, exclusion, and unresolved decision, combining entries only when one exact excerpt contains the whole obligation. Each entry must quote the source exactly and point to one or more extracted story keys. The validator proves that the mappings are well-formed and source-bound; review still determines whether the set is complete.

Run:

```sh
accountability_skill=/path/to/github-work-accountability
python3 "$accountability_skill/scripts/validate_extraction.py" MANIFEST.json --repo /path/to/repository
python3 "$accountability_skill/scripts/validate_extraction.py" MANIFEST.json --repo /path/to/repository --against origin/main
```

The second form also fails when the source blob at the comparison ref has changed. That failure means reconciliation is required; it does not decide how far any item should move backward.
