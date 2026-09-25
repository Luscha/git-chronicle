# Changelog

## Unreleased

### Models: bring your own, whichever it is

- **Two tables instead of one enum.** `[endpoints.<name>]` is where models live — a
  protocol (openai · anthropic · ollama, the only three that cost code), an address, and an
  auth method (bearer · header:<name> · query:<name> · adc · none). `[roles.<role>]` says
  which endpoint and model does which job. A vendor nobody here has heard of is four fields
  in your own config, and `kind` is sugar for a row of data — never a branch.
- **Five roles, not seven.** `embed` is gone (nothing in the pipeline embedded anything);
  `chat_large` is now `answer`, which says what it is for. `chat` · `untangle` · `naming` ·
  `narration` · `answer`, plus `judge` for grading with a model that did not answer.
- **One thinking setting**, `think = "off" | <budget> | low|medium|high`, translated to what
  each endpoint understands: `thinking_config` for Gemini/Vertex, `reasoning_effort` for
  OpenAI's protocol, `thinking.budget_tokens` for Anthropic, `think: false` for Ollama. It
  was writing a *Google* body to every OpenAI-compatible endpoint.
- **`--model` / `--think` / `--endpoint ROLE=VALUE`** on every command, and the environment
  for a process someone else launches: `GITCHRONICLE_<ROLE>_MODEL|THINK|ENDPOINT`, or
  `GITCHRONICLE_KIND|BASE_URL|API_KEY|MODEL` for a whole configuration with **no config
  file at all**. Applied in `load_config`, because the previous attempt wired them into one
  helper that `doctor` did not use — it advertised the flags and ignored them.
- **A key is never printed** — `doctor`, `models` and the studio show where it came from,
  and a literal one as `set, not shown (…1234)`.
- **`api_key = "file:/run/secrets/key"`** beside `env:VAR`, for hosts that mount secrets as
  files. `gitchronicle models` prints endpoints, roles and what each resolves to;
  `--available` asks an endpoint which models it can run. `doctor` reports each role's
  model, thinking and credential *reference* — never the value.
- **Studio → Models**, at the foot of the rail: connect an endpoint (provider picker, key or
  GCP project, a test that asks the endpoint what it can run), then say which model does
  which job. **A key typed there goes to the `.env` beside the config (0600)** and the config
  keeps only `api_key = "env:NAME"` — refusing to take a key at all protected nothing on a
  localhost tool and forced everyone into a text editor. The project's own
  numbers moved from the rail to the Catalogue, where they are about something.
- **A config that names only a repository analyses its whole history** (`rev_range` defaults
  to `HEAD`, not the last 300 commits) and has a default knowledge-base path.

### Curation and stories

- **Files nobody owns that carry an entry's name** — a third review queue, and the gap the
  other two cannot see: the catalogue names the thing and holds none of its code, because a
  word several entries answer to identifies none of them. `char_affect.cpp`, in 135 commits,
  belonged to nothing and nothing said so; 134 such files here, `gitchronicle inspect
  --unclaimed`, and the `entry` MCP tool now tells an agent about them too.
- **Files come INTO an entry** — its Files tab could push files out and never pull them in.
  Search from inside the entry, tick, add; the rules are written per source entry exactly as
  the Files view writes them.
- **Rust**: `use` and `mod` are read as imports, and a module path is resolved to its
  module rather than its type (Rust's own snake_case/CamelCase convention says which is
  which). Alacritty produced **zero** dependency links before this.
- **A chapter carries what happened after it** — how long the work then stopped, and which
  of the entry's own files stopped existing — and its work items are dated. The battle pass
  was switched off in a commit whose summary says "battlepass gets removed", and the story
  said "a battle pass quest removal feature was implemented"; it now says the system was
  removed in August 2021 and that nothing followed for 20 months. Measured: the fact is
  right where it was wrong, at −3 ± 2 points on a 17-question eval, because dated evidence
  pulls the narration toward chronology (`docs/measurements.md`).
- **Stories** — a studio view for the one action that spends money: how many entries have
  no story, which ones, and a narrate button that says what it will cost you.
- **An import resolves only to a file its own syntax could name**, and only to one that
  still exists at HEAD. `#include "config.h"` was reaching `config.js` in a web panel: 85 of
  227 edges on the reference repository were that collision, and two of the six biggest
  hubs were made of it. Stated as an exclusion, so unknown extensions (`.fx`, `.forge`)
  resolve as before.
- **Every link and every file says why** — a link opens into the references behind it, file
  by file; a file says whether a rule, the work filed under the entry, or its own name put
  it there. In the studio, in `gitchronicle inspect`, and in the `entry` MCP tool.
- **Narration from the studio** — the one action that spends money, named as such, with the
  number of entries still without a story standing in the sidebar.
- **`not-uses` removes a link**, rather than only stopping the studio proposing it. Every
  link now shows the evidence behind it and can be taken back from the entry's Links tab,
  and a relation no import can show can be stated there as well.

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
