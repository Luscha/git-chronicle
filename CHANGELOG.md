# Changelog

## 0.5.0

The knowledge base answers questions, and says what it costs.

- **Ask** — `gitchronicle ask`, and the same retrieval inside the studio, citing entries
  and commits. SQLite full-text; no embedding server.
- **MCP** — `gitchronicle mcp` serves `search`, `entry`, `path_history` and `period` to
  coding agents over stdio.
- **Eval** — `gitchronicle eval` grades answers fact by fact against facts you supply, and
  samples each question. 91% on a 15-year fork, 90% on httpie.
- **Studio rebuilt** — ask, catalogue, force-directed dependency graph, a timeline with a
  year view, and an entry panel where you sort files into other entries by hand.
- **Splitting** — `reject` releases files the pipeline wrongly gave an entry, and
  `claim … from "<entry>"` takes only that entry's files. Commits and stories follow.
- **Cost and preflight** — `gitchronicle cost` (per stage, from the cache's own token
  counts) and `gitchronicle doctor` (git, repo, scope, every model role).
- **Providers** — `anthropic` and `azure` kinds; `headers`, `query`, `params`,
  `reasoning_effort` and `thinking_budget` passthrough; every role optional but `chat`;
  embeddings no longer required.
- **Untangle is newest-first** and writes as it goes: the last month is queryable minutes
  into a first run, and an interrupted run keeps what it wrote.
- **Territory speaks in today's names** — renames no longer file one file under two
  entries (123 → 0 on the reference repository).
- **Thinking off by configuration** — 52% of untangle tokens on Gemini Flash bought
  nothing measurable: $30 → $7.51 and ~5 h → 30 min on 6,574 commits.
- **Removed** the v0.1–v0.3 stages (~4,200 lines) and their commands. `--help` now shows
  nine commands; the pipeline stages remain as hidden ones.

## 0.4.0

The tool proposes, the ledger decides: grain moved out of the pipeline into
`gitchronicle.plan`, rules over paths that survive re-runs. Territory from concern
evidence in union with worktree name-claiming; tiers; relations; narrated chapters.
