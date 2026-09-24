# Agent Work Accountability contributor guidance

- Keep every skill independent of a particular product repository, company, GitHub organization, agent harness, and communication transport.
- Use the portable `skills/<name>/SKILL.md` layout. Harness-specific metadata may enhance one client but must never be required for the core workflow.
- Preserve one logical work model across GitHub storage profiles. Detect user-owned versus organization-owned repositories and available capabilities before choosing APIs.
- Keep epics and stories repository-bound by default. Model cross-repository contracts with separate work items and an explicit shared contract identity.
- Treat planning intent, execution attempts, acceptance verdicts, and releases as separate authorities. Claims, branches, pull requests, and process exits do not prove completion.
- Make installation idempotent and non-destructive. Never replace an existing skill without an explicit installer option and a backup.
- Keep tests local and deterministic. Tests must not mutate live GitHub repositories or require credentials.
- Run `./tests/run.sh` before publishing changes.
