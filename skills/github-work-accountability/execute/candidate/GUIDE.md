---
name: execute-candidate
description: Open when an implementer submits an immutable result for testing and acceptance (Executing → Acceptance). Teaches candidate evidence and the submitted attempt state.
---

# Submit a candidate (Executing → Acceptance)

## Steps

1. The session MUST identify the immutable candidate: a commit, artifact digest, deployment identity or
   another immutable reference. A branch name is not immutable, so the session MUST NOT use it.
2. The session MUST draft, set the story's `work_phase` to `Acceptance`, change its attempt's `state` to
   `submitted`, and add:

   ```json
   "candidate": {"ref": "https://github.com/OWNER/REPO/commit/FULL_SHA",
                 "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID"}
   ```

   The attempt ID MUST be the one recorded on the issue.
3. The session MUST dry run, read the plan, then apply. Expected plan:

   ```text
   planned_mutations: ["set #3 Work phase=Acceptance", "bind issue #3 to Project fields"]
   ```

   The `Attempt:` line is removed once the story reaches Acceptance with its recorded attempt.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | `"verified": true`; the story awaits a verdict. | The session MUST request an independent review ([verdict](../verdict/GUIDE.md)) and MUST NOT give the verdict itself. |
| 2 | `… cannot take it over …` | The manifest names a different attempt from the recorded one. | The session MUST use the recorded attempt ID. |
| 2 | names `candidate` | Acceptance was requested without candidate evidence. | The session MUST add it. |

A candidate is a claim that the work is ready to test, not proof that it is accepted; a session MUST NOT
report a story as accepted on the strength of its candidate.
