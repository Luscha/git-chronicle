# gitchronicle

**Turn a git repository into a knowledge base that answers questions about itself.** Not
what the code is *now* — every tool does that — but what was built, when, why, and what it
replaced. Years of commits become a catalogue of features, frameworks and tools, each with
its own dated story, its files, and what it is built on.

```
$ gitchronicle ask "how did the battle pass evolve?"
The battle pass was first built in May 2021 (`8bff020db2`), removed that August
(`fc5255f192`), and revived in 2024. It now ships seasonal content and is still
maintained — the last change is from June 2026.
```

The same knowledge base serves your editor's agent over MCP, a local studio for reading
and curating it, and static HTML + markdown you can publish.

---

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .          # add '[vertex]' for Google Vertex, '[dev]' to run the tests
```

Python ≥ 3.11 and `git`. No embedding server, no database server, no build step: one
SQLite file and one chat model.

## First run

```bash
cp config.example.toml config.toml     # set [repo].path and one [providers.chat]
gitchronicle doctor                    # checks git, the repo and every model role
gitchronicle init                      # drafts the scope (what is the product); review it
gitchronicle cost --estimate 6500      # what the first build will cost, before it runs
gitchronicle update --chronicle        # build it: catalogue + narrated history
gitchronicle studio                    # read and curate it at 127.0.0.1:8765
```

A **6,574-commit, 15-year repository costs about $8 and 30 minutes**, narration included,
on Gemini 2.5 Flash with thinking off. A rebuild that finds nothing new makes **zero**
model calls, so `update` is safe to schedule. It untangles the newest commits first and
writes as it goes, so the last month is queryable minutes in.

## The commands

| | |
|---|---|
| `update` | ingest what is new and rebuild. `--chronicle` also narrates. The one to schedule |
| `init` | draft the scope map — which paths are the product — for you to review |
| `studio` | the local web app: ask, browse, graph, timeline, curate |
| `ask` | one question, answered with citations |
| `mcp` | serve the knowledge base to coding agents (stdio) |
| `doctor` | check git, the repository, the scope and every model role |
| `cost` | what the runs cost, per stage; `--estimate N` before they run |
| `eval` | grade the answers against facts you already know |
| `ledger` | the curation file: draft one, or open it in `$EDITOR` |

## What you get

- **A catalogue** — every feature, framework, tool and content area, with its files, its
  tier, when it was born and when it was last touched.
- **A story per entry** — chapters cut at real breaks in the work, narrated from the
  commits' own evidence, with the commits behind each one.
- **A dependency graph** — what is built on what, from imports rather than guesses.
- **Answers** — `gitchronicle ask`, citing entries and commits.
- **Exports** — `kb.html` and per-entry markdown, for reading or publishing.

## Curation: the tool proposes, you decide

No tool can know that two directories are one framework while a third is one feature and
not three. So gitchronicle proposes a decomposition and you correct it in
`gitchronicle.plan`, a text file of rules over **paths** — which is why your corrections
survive re-runs and years of new commits:

```
entry "Scripting Runtime"
  claim  server/libscript/**
  reject scripting/protobuf/**                 # per-feature schemas belong to the features
  tier   framework
entry "Locale Strings"
  claim  locale/strings/** from "Quest Content" # take only that entry's files
merge  "Wiki Builder Tabs" -> "Wiki Manager"
reject "Constinfo System"                       # never propose it again
keep-split client/ui/**                         # shared on purpose; stop asking
```

The **studio** is where this is comfortable: *Review* ranks folders the catalogue split
across entries when nothing is named after them; an entry's *Files* tab lets you tick
folders and assign them elsewhere; *Save & rebuild* applies it. Commits and stories follow
the files. Replay is pure, so re-graining costs milliseconds and no model calls. A full
example is in [`examples/ledger.plan`](examples/ledger.plan).

`gitchronicle.md` holds the other half: the **scope map** (which paths are the product)
and an optional **Direction** section — glossary, grain rules, and how the narration
should read.

## For agents: MCP

```bash
claude mcp add chronicle -- gitchronicle mcp --config /abs/path/config.toml
```

Four read-only tools: `search`, `entry`, `path_history` (which entry owns a file and why it
looks the way it does — worth calling before changing code) and `period` (what happened in
a given year). They return evidence, not conclusions; the agent does its own reasoning.

## Models

Any OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter, Together, llama.cpp, LM Studio),
plus `kind = "ollama"`, `"anthropic"`, `"azure"` and `"vertex"` (Application Default
Credentials, no API key). Roles let one job use a different model from another, and each
falls back to `chat`, so a minimal config is four lines:

| role | what it does | cost |
|---|---|---|
| `chat` | everything not named below | — |
| `untangle` | one call per commit: what changed and why | **the bulk of the bill** |
| `naming` | the names in your catalogue | pin it: changing it renames everything |
| `narration` | the stories | modest |
| `chat_large` / `judge` | answering questions, grading | a few calls |

Two things worth knowing:

- **Turn thinking off for untangle.** It was 52% of all tokens on Gemini Flash and bought
  nothing the eval could see: `thinking_budget = 0` (Gemini) or `reasoning_effort` (others).
  On 6,574 commits: $30 → $7.51, and ~5 h → 30 min.
- **Anything else your endpoint accepts** goes through `headers`, `query` and `params`,
  without waiting for a release.

## Checking the answers

`gitchronicle eval` asks questions whose answers you already know and grades each answer
fact by fact, sampling every question several times:

```json
[{"q": "When was the battle pass first removed?", "facts": ["August 2021"]},
 {"q": "When was VR support added?", "facts": ["there is none"], "unanswerable": true}]
```

It scores 91% on a 15-year private fork and 83% on [httpie](https://github.com/httpie/cli),
whose question set ships in [`eval/httpie.json`](eval/httpie.json).
[The numbers, and what they cost](docs/measurements.md).

## Privacy

The knowledge base holds your repository's commit messages, file paths and **author names
and email addresses**; so do the exported dossiers and `kb.html`. Everything stays on your
machine except what goes to your model provider — commit messages, diffs and file paths.
Worth checking before pointing it at a private codebase, and before publishing an export.

## Limits

- The decomposition was tuned on one repository. On httpie it is weaker: some generic
  entry names, one entry mixing tests and CLI, few dependency links.
- Un-curated entries re-cluster as history grows. That is why curation lives in rules over
  paths rather than in the database.
- Narration is grounded in each commit's own evidence but not verified sentence by
  sentence.
- Linux is the only tested platform.

[How it works](docs/architecture.md) · [what was measured](docs/measurements.md) ·
[contributing](CONTRIBUTING.md) · [changelog](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
