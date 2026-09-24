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
| `studio` | the local web app: ask, browse, graph, timeline, curate, narrate |
| `ask` | one question, answered with citations |
| `mcp` | serve the knowledge base to coding agents (stdio) |
| `doctor` | check git, the repository, the scope and every model role |
| `cost` | what the runs cost, per stage; `--estimate N` before they run |
| `eval` | grade the answers against facts you already know |
| `ledger` | the curation file: draft one, or open it in `$EDITOR` |
| `inspect` | one file or one word: who owns it, **why**, what it was built for, what belongs with it. `"A -> B"`: the imports behind a link. `--unclaimed`: files nobody owns that carry an entry's name |

## What you get

- **A catalogue** — every feature, framework, tool and content area, with its files, its
  tier, when it was born and when it was last touched.
- **A story per entry** — chapters cut at real breaks in the work, narrated from the
  commits' own evidence, with the commits behind each one, and told plainly when the work
  stopped or the files went away.
- **A dependency graph** — what is built on what, from imports rather than guesses, plus
  the relations you state and minus the ones you judge wrong. Every link shows the
  references behind it, file by file.
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
not-uses "Item System" -> "Locale Strings"      # not a relation: drop that link
keep-split client/ui/**                         # shared on purpose; stop asking
ignore raii                                     # a word that names nothing; stop asking
```

The **studio** is where this is comfortable. *Review* has three queues: folders the catalogue
split across several entries, **files nobody owns that carry an entry's name** — the
catalogue naming a thing while holding none of its code — and **words that run through the work and name no entry** —
the assembly's residue made visible, which is how a missing framework announces itself
rather than hiding in a log line. Each word can become an entry, be dismissed with
`ignore <word>`, or turn out to be a scope rule that is not taking effect. Beyond that, an
entry's *Files* tab lets you tick folders and assign them elsewhere, or search the whole
repository and pull files in; *Save & rebuild* applies it. Curation is free — replay costs
milliseconds and no model calls. Narration is the one paid action, so it has a place of its
own: **Stories** says how many entries have none, which ones, and what a run will spend
before it starts. Commits and stories follow
the files. Replay is pure, so re-graining costs milliseconds and no model calls. A full
example is in [`examples/ledger.plan`](examples/ledger.plan).

### Files: surgical curation

The review queues look for work that is *unfiled*. A framework whose files are scattered
across seven entries is **misfiled**, and no queue can see it — every one of its files has
an owner. The studio's **Files** view works at that grain: search a path, a filename or a
word, and every match appears with whoever owns it today.

Where the matches import each other, it also splits them in two — the files the others
include (the framework) and the files that include them (its users) — as evidence, not a
verdict; which of the two you want is your call. Tick what belongs together, name it, and
the rules are written per source entry so each file is released from exactly the entry that
held it.

Clicking a path shows what it was built for (the work items behind it), which chapters tell
its story, and what changes with it. The same data is a command (`gitchronicle inspect
<path|word>`) and an MCP tool, so an agent can ask before it edits code.

The same search runs from inside an entry, so files come **in** as well as out: the rules
are written per source entry either way, and a file nobody owns is claimed outright.

*Measured*: a trait framework whose 8 files sat across 5 entries became one entry in a
single pass — search, tick, name, save. An entry that held three utility files took its
seven real ones, and the entry that had two of them released them, in one pass and 46
seconds of replay — no model calls.

### The gap neither queue can see

Those ask what is *unfiled* or *misfiled*. Neither can see a file that is simply
**absent**: the catalogue names the thing and holds none of its code. That happens for a
reason the tool is right about — a word several entries answer to identifies none of them,
so four entries carrying "affect" left `char_affect.cpp` (135 commits) to nobody — and the
refusal was silent. Review says it out loud, for every entry whose name a
file carries word for word, with how many of that entry's own commits touched it. On the
reference repository: 134 files, led by `raid_manager.cpp` (60 commits) and `pvp_arena.cpp`
(38), none of them owned by anything. `gitchronicle inspect --unclaimed` prints the same
list, and the `entry` MCP tool tells an agent about the ones under its nose.

### Why is this here?

Territory and links are inferred, so both can be wrong, and a conclusion you cannot
interrogate is one you cannot correct. Every file in an entry answers **why it is here** —
a rule you wrote, the work items filed under the entry that changed it, or its own name —
next to what it was built for and what it changes with. Every link opens into the
references that made it: *`SpotLightDebugPanel.cpp` → `dynamiclightmanager` →
`DynamicLightManager.cpp`*. If the answer is unconvincing, `not-uses` removes the link and
the next rebuild keeps it removed. The same two answers are a command and an MCP tool —
`gitchronicle inspect <path>` and `gitchronicle inspect "Debug Panel -> Rhi Rendering"` —
so an agent can weigh a link before believing it.

That view paid for itself immediately. A link from a server feature to the web admin panel
turned out to rest on `#include "config.h"` resolving to `config.js`: references were
matched by filename across the whole repository, with no notion of language. A reference
now resolves only to a file its own syntax could be naming, and only to one that still
exists — stated as an exclusion, so `.fx` including `.fxh`, or a `.forge` script requiring
a `.lua` library, keeps working. On the reference repository that removed **85 of 227
import edges**, and two of the six biggest "frameworks" turned out to be collisions.

### Scope: what is the product

`gitchronicle.md` holds the other half of curation — the **scope map**. Every path it
admits feeds the catalogue *and its vocabulary*, so leaving a vendored tree in has a cost
beyond noise: its words start to look like yours, and a feature named after one of them
never forms. On the reference repository a Boost tree left in scope accounted for 2,897 of
17,720 concerns and cost the catalogue a whole framework.

The studio's **Scope** view is a tree of the repository, one verdict per subtree:

| verdict | meaning |
|---|---|
| analysed | part of the product; clustered into entries |
| one entry | owned, but catalogued as ONE entry without decomposing it |
| external | somebody else's code; invisible to the analysis |

Each row shows its files, how much untangled work is attached to it and how much of it the
catalogue already owns, so the cost of a verdict is visible before you choose it. Marking a
tree external drops its evidence on the next rebuild; marking one analysed untangles it
(which costs). The view also reports rules that **do nothing** — an explicit `include:`
beats an `exclude:`, so a narrower exclusion written under a drafted include is silently
inert, and it will set the verdict in a way that actually takes effect.

The optional **Direction** section of the same file states glossary, grain rules and how
the narration should read.

## For agents: MCP

```bash
claude mcp add chronicle -- gitchronicle mcp --config /abs/path/config.toml
```

Five read-only tools: `search`, `entry` (its story, its links with the evidence for each,
and the files nobody owns that carry its name), `inspect` (a file: owner, *why* that entry
holds it, the work behind it, what changes with it — a word: every match with its owner and
framework-versus-users — or `"A -> B"`: the imports behind a link), `path_history` (which
entry owns a file and why it looks the way it does — worth calling before changing code)
and `period` (what happened in a given year). They return evidence, not conclusions; the
agent does its own reasoning.

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

It scores 96% on [alacritty](https://github.com/alacritty/alacritty) (2,493 commits of
Rust, built in 13 minutes), 90% on a 15-year private fork and 83% on
[httpie](https://github.com/httpie/cli); both public question sets ship in
[`eval/`](eval/).
[The numbers, and what they cost](docs/measurements.md).

## Privacy

The knowledge base holds your repository's commit messages, file paths and **author names
and email addresses**; so do the exported dossiers and `kb.html`. Everything stays on your
machine except what goes to your model provider — commit messages, diffs and file paths.
Worth checking before pointing it at a private codebase, and before publishing an export.

## Limits

- The decomposition was tuned on one repository. Elsewhere it is weaker, and the *answers*
  hide it: alacritty scores 96% on questions while a third of its entries are named after
  directories and its dependency graph has 8 links. Expect to curate names and territory on
  a repository the tool has not seen.
- Un-curated entries re-cluster as history grows. That is why curation lives in rules over
  paths rather than in the database.
- Narration is grounded in each commit's own evidence but not verified sentence by
  sentence.
- Linux is the only tested platform.

[How it works](docs/architecture.md) · [what was measured](docs/measurements.md) ·
[contributing](CONTRIBUTING.md) · [changelog](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
