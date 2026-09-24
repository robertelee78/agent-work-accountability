# Synchronization contract

Synchronization must leave one authority for each kind of fact. GitHub is the durable accountability surface, but it does not replace planning sources or implementation evidence.

## Authority map

| Fact | Authority | GitHub representation |
|---|---|---|
| Why, What, scope, acceptance boundary | Current approved planning source | Source-bound managed issue block and link |
| How and technical approval | Approved design source or recorded design verdict | Design link, verdict, and phase |
| Stable work identity | Existing GitHub issue's managed identity block | Hidden work key retained through edits, retries, closure, and reopening |
| Priority and rank | Operator/product-authorized tracker change | The selected storage profile's issue field, Project field, ordering, or repository label |
| Current ownership | Claim system or explicit assignment | Optional derived owner/claim view |
| Attempt history | Execution events tied to the work key | Comments/checks or an adapter-owned event store |
| Candidate implementation | Immutable git commit, artifact digest, or deployment identity | Candidate reference |
| Acceptance | Reviewer/verifier verdict bound to candidate and criteria | Evidence and phase |
| Delivery | Release/deployment/publication system | Delivery reference and phase |
| Conversation | Issue comments or an optional agent-communication system | Links or summarized observations |

Agent communication systems are optional observation sources for claims, attempts, results, handoffs, and blockers. They do not own the item's Why, What, priority, durable phase, acceptance, or delivery verdict.

## Stable identity

Put this marker in an agent-managed issue block:

```html
<!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
```

The key is repository-qualified and unique across open and closed issues. Prefer an existing stable source ID. Otherwise mint a concise logical item ID during the first validated extraction and keep it forever. A title, heading number, source line, branch, attempt, and issue state are aliases or observations; none is the identity.

Repository qualification is also the ownership boundary. Do not use a shared key to collapse implementation work from two repositories into one issue. Cross-repository contract items retain separate work keys and share an additional contract identifier.

Before creating an issue, enumerate every open and closed issue through the paginated Issues API, exclude pull requests returned by that endpoint, and match the complete hidden marker byte-for-byte. Do not use GitHub's search index to prove absence; search may seed candidates, but it is tokenized and can lag writes. Zero exact matches permits creation, one match means update that issue, and more than one match is a conflict that stops mutation. After creation, read the issue by number and enumerate exact matches again so a concurrent duplicate is detected. Renames and source revisions update the existing issue.

An adopted repository may already use a different hidden marker. Register and search that legacy marker during migration, add the canonical marker to the same issue, and retain its key and history. Never create a replacement merely to normalize marker syntax.

## Managed issue block

Keep generated content inside explicit markers and preserve all human-authored text outside them:

```html
<!-- work-accountability:begin -->
<!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
Storage profile: `organization-fields | project-fields | repository-labels`
Source: `PATH` at `COMMIT` (`BLOB`)
Source excerpts:
> exact source text

Outcome: one observable deliverable

Acceptance:
- criterion

Validation: how acceptance will be proved

Delivery boundary: the release, deployment, publication, enablement, merge, or other event that makes this item Done
<!-- work-accountability:end -->
```

Never treat the copied prose as a second editable requirement. On reconciliation, regenerate it from a validated extraction of the current planning source.

## Reconciliation

Run reconciliation when a planning source changes, the default branch changes, an attempt submits a result, validation ends, delivery occurs, or an agent hands work off.

For ADR changes, apply the [ADR write interlock](adr-sources.md) regardless of which editor, command, hook, or agent changed the file. A successful ADR write with an old issue source blob is an incomplete accountability operation.

1. Read repository policy, the current planning source, all matching open and closed issues, relationships, fields, Project membership, and relevant git/evidence facts.
2. Extract a proposed epic/story graph. Match existing keys before proposing new ones.
3. Run deterministic source-binding, identity, and dependency validation. Exact excerpts establish provenance; a separate review establishes coverage.
4. Build a change plan. Mark removed or materially changed requirements for reconciliation rather than deleting their issues or history.
5. Materialize the desired state in a versioned manifest and apply only the computed delta. Re-read after an uncertain write instead of blindly retrying.
6. Read the resulting issues, fields, relationships, and Project membership back from GitHub and compare them with the plan.

### Pending operations

When GitHub is unavailable or a write result is uncertain, persist the validated desired-state manifest outside the product repository under `${XDG_STATE_HOME:-$HOME/.local/state}/agent-work-accountability/pending/HOST/OWNER/REPOSITORY/`. Never put credentials or copied issue prose in this directory.

Name the persisted manifest by its SHA-256 canonical JSON digest and append start and verified receipts to `journal.ndjson`. A rerun inventories GitHub again, compares it with the same desired state, and sends only missing mutations. The GitHub `clientMutationId` values are deterministic labels for diagnosis; they do not substitute for the read-before-write and read-after-write checks.

Before every resumed write, enumerate the exact work-key marker and compare current GitHub state with the manifest. If it already matches, do not write it. Conflicting desired states for one work key stop reconciliation; they are never applied last-writer-wins. A primary rate-limit failure exits with a retryable status and reset time. The next run recomputes the delta rather than replaying a blind write list.

If the source revision no longer matches the issue's recorded blob, set Source freshness to Reconciliation needed. Do not infer that implementation became invalid; determine which definition, design, validation, or delivery facts the change actually affects.

## Event-to-state rules

- Selecting a Backlog item for HLD/LLD work moves it to Designing; no meeting ritual is required.
- Product and technical approval of the HLD/LLD moves Designing to Ready.
- A work-key-bound attempt start moves Ready to Executing.
- A result naming an immutable candidate moves Executing to Acceptance.
- An independent acceptance verdict moves Acceptance to Release ready.
- A proven release or declared delivery event moves Release ready to Done.
- A blocker changes Health and leaves Work phase unchanged.
- Claim, renew, handoff, release, failure, cancellation, or expiry changes ownership or an attempt; it does not directly decide completion.

Git reconciliation may recover implementation, integration, and delivery facts. It cannot reconstruct an approval, priority decision, blocker, or conversation that was never recorded.
