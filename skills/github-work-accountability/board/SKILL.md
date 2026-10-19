---
name: board
description: Domain for document Projects. Open to put a planning document's epic/story tree on its one GitHub Project, to apply any manifest change, to merge older per-epic Projects or adopt an unmarked one, or to change a Project's visibility.
---

# Board: one Project per planning document

Each planning document (or independently managed workstream) has exactly one repository-linked GitHub
Project holding its root epic and every sub-issue below it at any depth. Every story-level event in
[execute](../execute/SKILL.md) is applied through the same reconcile loop taught here.

## Route

| The task is… | Open |
|---|---|
| Create, update or verify a document's Project; apply any manifest change | [reconcile](reconcile/SKILL.md) |
| Merge per-epic Projects from 0.7.x, or adopt an existing unmarked Project | [migrate](migrate/SKILL.md) |
| Make a document Project public or private | [visibility](visibility/SKILL.md) |

## Rules for the whole domain

- A Project is owned by a user or organization, not by a repository, so a document Project carries three
  identities: its owner-level Project identity, an explicit repository link, and the root epic's work key.
  Its README carries `<!-- work-accountability:project-v2 HOST:REPOSITORY_NODE_ID ROOT_WORK_KEY -->`. A
  session MUST find a Project by that marker and MUST NOT rely on a remembered number or title.
- A session MUST NOT create one Project per section, epic, agent, session, branch or attempt. A section is
  part of its document's Project; a Project marked for a section's work key is a leftover that MUST be
  merged ([migrate](migrate/SKILL.md)).
- Several Projects linked to one repository are expected when it has several planning documents or
  workstreams. An owner-level Project list, a Project URL or issue membership does not prove the
  repository link; a session MUST rely on the read-back through the repository's own Projects connection,
  which `"verified": true` includes.
- Before creating a Project, the reconciler searches all open and closed Projects of the owner. A deleted or
  missing remembered Project means only that the remembered identity is gone. Creating a Project is a
  protected action: a session MUST have authorization beyond routine tracker updates before the first apply
  that creates one.
- Organization-owned and user-owned Projects use different endpoints. Routing MUST come from the observed
  owner type and Project identity, never from a repository-name assumption.
- Built-in Project workflows MAY add matching issues and react simply to close or merge events. Source
  extraction, stable identity, approval gates, attempt binding, independent acceptance, release semantics
  and drift stay with this skill; a session MUST NOT let issue open/closed state alone decide any of them.
