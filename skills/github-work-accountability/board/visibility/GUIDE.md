---
name: board-visibility
description: Open only when the operator explicitly asks to make a document Project public or private, or when a reconcile stops because a board's visibility was changed in GitHub's settings. Making a Project public is a protected publication action.
---

# Project visibility

A session working this leaf MUST also follow the [board domain rules](../GUIDE.md).

Every document Project is private unless someone deliberately makes it public. A public Project shows its
title, README, short description, views, section names, field values and draft issues to everyone on the
internet; items from repositories a viewer cannot read stay hidden.

## Rules

- A session MUST run `awa project visibility … public` only when the operator explicitly asks, MUST show the
  operator the preview it prints, and MUST pass `--yes` only after the operator confirms.
- Visibility is not a manifest setting, and reconcile never changes it. A session MUST NOT change visibility
  in GitHub's settings on the operator's behalf: reconcile treats that as drift and stops.
- When reconcile creates a Project, it makes it private. If the organization refuses that change, awa
  continues only when the Project reads back private (the org default) and records a note; if the Project
  would be left public, awa does not populate it and stops, so a document board is never published by
  accident. An organization owner MUST make such a Project private, then the session reruns.

## Steps

1. The session MUST run the command without `--yes` first and show the operator the preview:

   ```sh
   awa project visibility ROOT_ISSUE public --repo OWNER/REPO    # or pass the Project URL
   ```

2. The session MAY rerun with `--yes`, but MUST NOT do so before the operator confirms:

   ```sh
   awa project visibility ROOT_ISSUE public --repo OWNER/REPO --yes
   ```

3. Making a Project private needs no preview; the session MAY run it when the operator asks:

   ```sh
   awa project visibility ROOT_ISSUE private --repo OWNER/REPO
   ```

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 2 | A preview (fields shown on views, how many issue cards there are and whether outsiders can see them, draft issues) ending `Not changed yet: Project #1 is still private. Review the preview above, then rerun with --yes to make it public.` | No terminal confirmed; nothing changed. | The session MUST show the preview to the operator, and MUST NOT rerun with `--yes` until the operator confirms. |
| 0 | The new visibility, recorded in the README and read back | Changed. | The session MUST report it. |
| non-zero | GitHub refused | The organization lets only owners change visibility, or the account lacks Project admin rights. | The session MUST report the refusal; the command never retries, and the session MUST NOT retry through another account. |

When a board's live visibility differs from its README record, the next reconcile stops, names the board and
writes nothing; a fresh `--draft` does not paper over it. The session MUST then either run this command to
keep the new value (and MUST obtain the operator's confirmation first when the new value is public) or
ask the operator to change it back in GitHub. `awa doctor`, run inside a public repository, notes any of its document Projects
that are private.

## Dropping down a level

- `createProjectV2` has no visibility input and GitHub documents no default, so the reconciler creates each
  Project under a neutral title (`work-accountability setup …`), makes it private with
  `updateProjectV2(public: false)`, and only then gives it its real title and content.
- Visibility is read back from `ProjectV2.public`; a session MAY check it with
  `gh api graphql -f query='query($o:String!,$n:Int!){user(login:$o){projectV2(number:$n){public}}}' -F o=OWNER -F n=NUMBER`.
  The REST Projects API can read visibility but not change it. Organizations can restrict visibility changes
  to owners; the API cannot read that setting, so a refused mutation is the only signal.
- Visibility is separate from access: a private organization Project is still readable by members according
  to its base role and collaborators.
