# Synchronization contract

Synchronization must leave one authority for each kind of fact. GitHub is the durable accountability surface, but it does not replace planning sources or implementation evidence.

## Authority map

| Fact | Authority | GitHub representation |
|---|---|---|
| Why, What, scope, acceptance boundary | Current approved planning source | Source-bound managed issue block and link |
| How and technical approval | Approved design source or recorded design verdict | Design link, verdict, and phase |
| Stable work identity | Existing GitHub issue's managed identity block | Hidden work key retained through edits, retries, closure, and reopening |
| Priority and rank | Operator/product-authorized tracker change | Organization issue fields |
| Current ownership | Claim system or explicit assignment | Optional derived owner/claim view |
| Attempt history | Execution events tied to the work key | Comments/checks or an adapter-owned event store |
| Candidate implementation | Immutable git commit, artifact digest, or deployment identity | Candidate reference |
| Acceptance | Reviewer/verifier verdict bound to candidate and criteria | Evidence and phase |
| Delivery | Release/deployment/publication system | Delivery reference and phase |
| Conversation | Issue comments or an optional agent-communication system | Links or summarized observations |

Vox and similar systems are optional observation sources for claims, attempts, results, handoffs, and blockers. They do not own the item's Why, What, priority, durable phase, acceptance, or delivery verdict.

## Stable identity

Put this marker in an agent-managed issue block:

```html
<!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
```

The key is repository-qualified and unique across open and closed issues. Prefer an existing stable source ID. Otherwise mint a concise logical item ID during the first validated extraction and keep it forever. A title, heading number, source line, branch, attempt, and issue state are aliases or observations; none is the identity.

Repository qualification is also the ownership boundary. Do not use a shared key to collapse implementation work from two repositories into one issue. Cross-repository contract items retain separate work keys and share an additional contract identifier.

Before creating an issue, search all open and closed issues for the exact key. Zero matches permits creation, one match means update that issue, and more than one match is a conflict that stops mutation. Renames and source revisions update the existing issue.

An adopted repository may already use a different hidden marker. Register and search that legacy marker during migration, add the canonical marker to the same issue, and retain its key and history. Never create a replacement merely to normalize marker syntax.

## Managed issue block

Keep generated content inside explicit markers and preserve all human-authored text outside them:

```html
<!-- work-accountability:begin -->
<!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
Source: `PATH` at `COMMIT` (`BLOB`)
Source excerpts:
> exact source text

Outcome: one observable deliverable

Acceptance:
- criterion
<!-- work-accountability:end -->
```

Never treat the copied prose as a second editable requirement. On reconciliation, regenerate it from a validated extraction of the current planning source.

## Reconciliation

Run reconciliation when a planning source changes, the default branch changes, an attempt submits a result, validation ends, delivery occurs, or an agent hands work off.

1. Read repository policy, the current planning source, all matching open and closed issues, relationships, fields, Project membership, and relevant git/evidence facts.
2. Extract a proposed epic/story graph. Match existing keys before proposing new ones.
3. Run deterministic source-binding, identity, and dependency validation. Exact excerpts establish provenance; a separate review establishes coverage.
4. Build a change plan. Mark removed or materially changed requirements for reconciliation rather than deleting their issues or history.
5. Apply idempotent operations keyed by work key and operation ID. Re-read after an uncertain write instead of blindly retrying.
6. Read the resulting issues, fields, relationships, and Project membership back from GitHub and compare them with the plan.

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
