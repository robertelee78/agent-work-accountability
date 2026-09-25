# Agent Work Accountability contributor guidance

- Keep every skill independent of a particular product repository, company, GitHub organization, agent client, and communication transport.
- Treat `skills/<name>/` as the complete portable artifact. Client-specific discovery paths and metadata are optional adapters; the core workflow must not call client APIs, require client hooks, or fork its behavior by client.
- Preserve one logical work model across GitHub storage profiles. Detect user-owned versus organization-owned repositories and available capabilities before choosing APIs.
- Keep epics and stories repository-bound by default. Model cross-repository contracts with separate work items and an explicit shared contract identity.
- Treat planning intent, execution attempts, acceptance verdicts, and releases as separate authorities. Claims, branches, pull requests, and process exits do not prove completion.
- Treat canonical planning files and Git as the authority for ADR content. ADR managers, indexes, memory stores, and orchestration tools are optional adapters; none may silently impose a lifecycle or bypass GitHub reconciliation after an ADR write.
- Make installation idempotent and non-destructive. Never replace an existing skill without an explicit installer option and a backup.
- Keep tests local and deterministic. Tests must not mutate live GitHub repositories or require credentials.
- Test what a user or agent sees, not internal functions: if it doesn't work for a user, it doesn't work. Drive the real commands (for example the reconciler against `tests/github_sim.py`) and assert on boards, columns, groups, issue links, receipts, and error messages. Put checks that need real GitHub inside the tool's own verified run instead of adding function-level tests.
- Run `./tests/run.sh` before publishing changes.
