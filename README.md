# gitchronicle

**Turn a repository into a browsable feature knowledge base** — every feature with
its territory, its relations, its commit history, and (optionally) its narrated
chronicle. Queryable SQLite underneath, self-contained HTML + markdown dossiers on
top.

`gitchronicle` answers what a plain `git log` never can: *what features does this
codebase actually have, which of them are* ***mine*** *, and how did each one come
to be?*

> The tool produces the knowledge base. Turning it into a docs/blog/"requiem" site
> is left to you — the per-feature `*.md`/`*.json` dossiers are built to be fed to
> whatever writes prose.

---

## The architecture (since v0.4): the tool proposes, the ledger decides

Three earlier versions each hard-coded a **grain** — how big one "feature" is — and each
one was wrong in its own way, because the grain is the one thing that cannot be settled in
advance. Architecture-recovery research says the same: clustering is judged against an
*authoritative decomposition*, and which decomposition is authoritative depends on the
viewpoint. A framework whose territory spans eleven components is one thing from a feature
viewpoint and eleven from a package viewpoint, and both readings are defensible.

So v0.4 stops deciding. The pipeline proposes a decomposition from evidence; a text
**ledger** overrides it with rules; and because those rules name paths rather than
generated ids, they survive re-runs and years of new history.

```
            EVIDENCE  (expensive, deterministic, incremental)
 ┌──────────────────────────────────────────────────────────────┐
 │ extract/untangle  commits -> concerns        (only new ones)  │
 │ delta             inherited vs authored (fork-aware)          │
 │ lineage           coined-stem seeded assembly of concerns     │
 │ territory         concern-derived ∪ worktree name-claim       │
 └────────────────────────────┬─────────────────────────────────┘
                              │
   ══════════ gitchronicle.plan — the ledger, in git ═══════════
                              │
            CURATION  (free, instant, replayed every run)
 ┌──────────────────────────────────────────────────────────────┐
 │ entry "Luna Scripting System"                                 │
 │   claim Server/libluna/**   reject **/protobuf/**             │
 │   tier framework                                              │
 └────────────────────────────┬─────────────────────────────────┘
                              ▼
      relations (imports) → tiers → SQLite KB → kb.html + dossiers
```

**Why the ledger is not keyed to features.** Measured on twelve years of real history:
assemble the catalogue at a past date, assemble it again at HEAD, and only 58–68% of
feature-grade clusters still hold their own evidence. Six candidate identity keys — seed
stem, birth roots, minimum concern id, founding commit, founding triple, oldest root —
track their cluster at best **89%** of the time, never 95%. The ceiling is structural: a
cluster's founding member is itself a clustering output, so it moves when the partition
moves. Rules over paths have no such problem, which is also why the `## Scope` section has
always survived re-runs while every in-database curation died.

### 1. The authored delta (fork-aware grounding)

For a fork, the first partition is not "feature vs vendored" — it is **inherited
vs authored**. A file is *authored* iff its rename-chain root was added by a work
commit; everything that arrived in the founding import or an import-majority mega
drop is the **inherited baseline**: catalogued, attributable (your maintenance
commits land there), but never presented as your feature. Touch counts are *not*
authorship — inherited files collect fixes, authored files can be stable.

### 2. Anchors (frameworks are cross-cutting by definition)

Coined identities — names the owner invented — are discovered from evidence:
embedded-module registrations (`Py_InitModule("luna", …)`), directories recurring
across components, `Doc/<name>/` trees, import census, the owner's own DSL
extensions (an extension whose files are ~100% authored *is* a DSL). Coinage is
verified against the full vanilla corpus: your identities cannot pre-exist there.
Anchors claim every authored file that **carries their name** across all
components — the one merge class exempt from locality guards, because the claim
rule is the guard.

### 3. Territory: two kinds of evidence, then the ledger

An entry owns the files its concerns touched, **in union with** the worktree files carrying
its own identifiers. Neither alone is enough — concern evidence gave 15 entries no
territory at all, name-claiming alone gave 63 none — and the union roughly doubles the
median entry (6 → 15 files). Name-claiming scores a filename match above a directory match,
the inverse of the anchor rule: a framework owns its tree, but
`luna/protobuf/ue/battlepass_pb2.py` is the Battle Pass schema and only lives under `luna/`
because that is where the generator writes.

What no rule can settle is left to you on purpose. `Server/game/src/luna/bind_arena.cpp` is
Luna's binding *for* Arena; Luna's tree holds 113 core files beside 115 per-feature protobuf
schemas. The default ships, the contested files are visible, and one ledger line moves them.

### 4. Local carving + evidence merges (the residue)

What anchors didn't claim is carved by component-scoped stem families, package and
directory units, then consolidated by *use*: duplicate territories fold, mutual
importers merge, fragments join their sole user, and a self-contained subtree the
project barely touched collapses into one tool. Docs are triaged (design / how-to /
meta): only corroborated design docs stand as features; a feature must own code.
Third-party (LICENSE-bearing subtrees), code-named data corpora, and generated
files are shelved, not deleted.

### 5. Attribution (closed-set, evidence first)

Every concern (untangled from each commit: label + summary + files) is assigned
through a cascade, cheapest sufficient signal first:

1. **territory evidence** — files overlap a feature's register territory: assigned,
   no LLM, never second-guessed;
2. **embedding fast path** — clear cosine winner vs feature definitions;
3. **LLM pick-by-ID** from a top-k shortlist (an ID can't be hallucinated;
   out-of-shortlist answers are rejected);
4. **batch novelty** — coherent orphan clusters may mint provisional features,
   but **only from majority-authored files** (removed features like a deleted
   backoffice re-emerge here; vanilla maintenance routes to the baseline; a Boost
   drop can never become "Date and Time Handling System");
5. **audit** — per-feature embedding outliers re-checked.

Everything is **temporally grounded**: a feature's birth is the first git
appearance of its territory (rename-aware), and no semantic stage may attribute a
commit older than the feature's own code. `check` reports violations (must be 0).

### 6. History refines the identity card

A feature's name/definition comes from code peeks; its attributed commits carry
the maintainer's own vocabulary. A post-attribution pass may rewrite name and
definition — text only, deterministically gated (no change-language, vocabulary
must recur in ≥2 commits, coined identifiers preserved, old names kept as
searchable aliases).

### 7. Tiers, relations & chronicle

Entries carry a **tier** — foundation, framework, feature, content, tooling — as a flat
attribute, never a hierarchy: filing them into a tree forces a single parent on exactly the
cross-cutting things that have none. A foundation is imported by many entries, from several
components, over years; all three, because fan-in alone promotes a toolchain used often
inside one component.

### 8. Relations & chronicle

`uses` edges come from static imports over register territory, including
embedded-interpreter registrations (`import luna` resolves to the C++ bridge that
registers it) — deterministic, no LLM. High fan-in marks framework hubs.
`chronicle` (opt-in) narrates each feature's evolution as commit-anchored
chapters. Chapters follow **arcs**, not the calendar: a break means the work genuinely
stopped for months, and a long run of upkeep is one "kept it running" chapter rather than
one per month. Building versus maintaining is read from whether the entry gained code, not
from what the commit message called it. Battle Pass went from 14 chapters to 4; the corpus
went from 1,909 to 659.

---

## Install (vanilla Python: venv + pip)

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # installs the `gitchronicle` command
```

Requirements: Python ≥ 3.11, `git` on PATH, and a chat LLM: any OpenAI-compatible API,
Google Vertex (`pip install -e '.[vertex]'`), or a local 14–32B model through Ollama. No
embedding server: search is SQLite full-text, in the project's own vocabulary.

## Quickstart

```bash
cp config.example.toml config.toml     # edit: repo path, providers
cp .env.example .env                   # provider API key
gitchronicle init                      # draft the scope (what is the product); review it
gitchronicle update --chronicle        # ingest -> KB + narrated history (LLM)
gitchronicle studio                    # ask, browse, curate — http://127.0.0.1:8765
gitchronicle ask "how did the battle pass evolve?"
gitchronicle mcp                       # the same knowledge base, for coding agents
```

Seven commands with `eval`; `--help` shows only these (the stage-by-stage commands of earlier versions
still work, hidden). `update` is the one to schedule: ingest and untangle skip everything
already seen, the assembly is deterministic and cheap, narration reuses every chapter
already paid for, and the ledger means a rebuild cannot disturb your curation. It ends by
reporting what changed. Without `--chronicle` it never buys narration: new or changed
entries are listed as waiting for it.

### The studio

`gitchronicle studio` is a local web app with two halves.

**Explore.** *Ask* answers questions from the knowledge base with citations to entries and
commits, and shows the matching entries, chapters and commits as you type. *Catalogue* is
every entry with its tier, activity over time, size and dependents. *Graph* is the
force-directed "uses" network: bigger nodes have more dependents, hovering lights up a
node's neighbourhood. *Timeline* shows every entry's activity month by month with its
chapters as bands; click a year to see what was worked on, first seen and removed. Any
entry opens a panel with its narrated story, its files, its links and curation actions.

**Curate.** *Review* ranks folders the catalogue split across entries when nothing is named
after them, the shape of a standalone thing the assembly dissolved (on the reference repo,
a 400-file wiki tool smeared across fifteen game features). *Rules* is the ledger, edited
in place. In an entry's **Files** tab you sort its files by hand: tick folders or files and
assign them to another entry, existing or new, or remove them. That is how a wrongly
generated blob gets split, by your categorisation rather than a guess, and its commits and
story follow the files on the next rebuild. Nothing is written until you save; *Save &
rebuild* runs `update` and reloads.

### Splitting and other rules

```
entry "Ganpeki Tasks"
  reject Client-Files/**                               # give these back
entry "Locale Strings"
  claim  Client-Files/** from "Ganpeki Tasks"          # ... and take only Ganpeki's
merge  "Mob Proto" -> "Monster Prototypes"             # rename (tier and notes follow)
reject "Constinfo System"                              # never propose it again
keep-split Client/UserInterface/**                     # shared on purpose; stop asking
```

`claim … from` matters: a folder in one entry's territory is not the folder in the repo.
Without it, sorting Ganpeki's 152 locale files into a new entry would have claimed all
1,345 files under `Client-Files/`.

### Direction (optional)

A `## Direction` section in `gitchronicle.md` states what you are looking for, in typed
statements rather than prose — `glossary` for meanings paths cannot reveal, `rule` for
grain, `voice`/`audience` for how the narration should read. Omit the section and the
pipeline behaves identically (auto mode). Every run reports what was active, which the
v0.1 free-prose charter never did — it was measured ineffective and removed precisely
because nobody could tell. Direction text joins the prompts, so enabling or editing it
changes the cache key and re-pays naming and chronicle.

**Zero-config first run works.** The optional scope file `gitchronicle.md`
(drafted by `gitchronicle init`) uses include/exclude/**acknowledge** verbs with
default-include semantics — it is an economy and curation lever, never a
prerequisite. `acknowledge` catalogues an owned sub-product as ONE entry without
decomposing it.

## Outputs

- **`kb.html`** — single-file, offline feature browser: searchable list, feature
  pages (definition, relations, territory, story, commit table), journey view.
  Shelved classes (inherited / third-party / content / generated) stay out of the
  main list but remain browsable.
- **`dossiers/*.md` + `*.json`** — one self-contained bundle per feature with
  commit-hash citations; `index.md` orders features as a development journey.
- **the SQLite db** — source of truth: `domains`, `domain_files` (register vs
  history territory), `commit_domains`, `domain_edges`, `evolution_chapters`,
  `concerns`, FTS5 indexes. Query it with any SQLite client.

## Agents: MCP

`gitchronicle mcp` serves the knowledge base over the Model Context Protocol (stdio, no
extra dependency), read-only. Register it once:

```bash
claude mcp add chronicle -- gitchronicle mcp --config /abs/path/to/config.toml
```

Four tools, all returning evidence rather than conclusions, because the agent does its own
reasoning: `search` (entries, dated chapters, commits), `entry` (what it is, who built it,
what it uses and what uses it, its full dated story), `path_history` (which entry owns a
file or folder, and the commits that touched it; call it before changing code) and
`period` (what happened in a year or date range).

## Does it tell the truth? `gitchronicle eval`

Structure metrics (blob size, territory, probes) never said whether an answer was right.
`eval` asks questions whose answers you know and grades each answer fact by fact, quoting
the words that state or contradict each fact, and scores contradictions separately from
omissions: "the evidence doesn't cover it" beats an invented date.

```json
[{"q": "When was the battle pass first removed?", "facts": ["August 2021"]},
 {"q": "When was VR support added?", "facts": ["there is none"], "unanswerable": true}]
```

`eval/` holds the question sets used below. Their facts come from `git log` and, for
httpie, its CHANGELOG, never from the knowledge base itself.

## Choosing a model & provider

Bulk cost is `O(commits)` untangling plus one classification pass; register peeks
are `O(worktree units)` and content-cached, so **rebuilds with unchanged inputs
are nearly free** (all stages are deterministic by construction — same input,
same cache key).

| Class | Verdict |
|---|---|
| ≤3B | ❌ unfitting |
| 7B | ⚠️ usable floor |
| **14–32B** | ✅ sweet spot (bulk work) |
| 70B-class | reserve for the few large-context calls |

**Pin the naming role before changing models.** A feature's name is model output, so
switching the chat model re-runs naming and renames the catalogue — once, here, that was
118 entries "gone" and 125 "new". `[providers.naming]` keeps naming on whichever model
named it first (those answers are cached, so it costs nothing) while `chat` moves freely
for the expensive stages. Unset, it falls back to `chat`.

Google Vertex is reachable with `kind = "vertex"`, authorised by Application Default
Credentials rather than an API key. (`[providers.embed]` is only read by the hidden legacy
commands; Vertex has no OpenAI-compatible embeddings endpoint, so leave it off Vertex.)

Any OpenAI-compatible endpoint works (or Ollama locally). Reference point: a
~6,500-commit, 35k-file game fork — full pipeline including narrated chronicles —
ran for roughly **$40–45** of small-model API cost end-to-end, iterations
included; a single clean pass is a fraction of that.

## Status

**v0.5 — the knowledge base answers.** Ask with citations, the same evidence served to
coding agents over MCP, a studio rebuilt around exploring (graph, timeline) with manual
splitting of wrongly generated entries, and `eval`, the first measure of whether answers
are true. Built on v0.4:

**v0.4 — "the tool proposes, the ledger decides."** Grain is no longer hard-coded: the
pipeline proposes, and `gitchronicle.plan` overrides it with path rules that survive
re-runs and history growth. Territory is concern evidence in union with worktree
name-claiming; entries carry a derived tier; relations, tiers and narrated chapters all
come off the improved territory.

Measured against v0.3 on the same 12-year, 6.5k-commit fork:

| | v0.3 | v0.4 |
|---|---|---|
| entries | 185 | 194 |
| median territory | 6 files | **13** |
| entries with no territory | 15 | **4** |
| relation edges | **0** | **160** |
| chronicle chapters | 1,909 | **779** |
| tiers | — | 4 foundations, 8 frameworks, 15 tooling |
| curation | — | ledger rules, replayed every run |

Narrating all 779 chapters cost about **$0.50** on Gemini 2.5 Flash, and a rebuild that
changes nothing now makes **zero** LLM calls.

**Does it tell the truth?** Graded with `gitchronicle eval` (facts from `git log` and the
CHANGELOG, never from the KB; the grader quotes the words behind each verdict):

| | void-queue (16 q) | httpie (15 q, second repo, no tuning) |
|---|---|---|
| strict score | **78%** (was 34% before the fixes below) | **73%** (was 40%) |
| answers with a contradiction | 3 | 4 |
| unanswerable question refused | yes | yes |

Reading the answers by hand, several "contradictions" are the grader being stricter than
the facts: CCC's history really does start in 2021, and "built December 2021, shipped in
3.0.0" is right for Bearer auth. The fixes the eval drove: whole-project facts in the
evidence (it had answered "when did the project start?" from the oldest *narrated* entry,
three years late); full chapter text for the leading entries (a 300-character cut had
dropped the battle pass's 2021 removal); and releases, because commits say when work was
done and a changelog says when it shipped.

**Second repository.** httpie (1,797 commits, 12 years) ran end to end with an accepted
default scope in 27 minutes. Q&A carries over; the decomposition does not yet: 49 entries,
several named after code words (`init`, `json`, `processors`), a 175-commit "Test CLI"
entry mixing tests, CLI and downloads, sessions split in two, only 2 dependency edges, and
fork-aware "inherited baseline" entries on a repo that is not a fork. The pipeline's
defaults were tuned on one repository and it shows.

Honest limits. The assembly still re-partitions as history grows — 58–68% of
feature-grade clusters keep their own evidence across two years — which is *why* curation
lives in the ledger rather than the database, but it does mean un-curated entries drift
between runs. Two of the three golden validation probes are too small to score and are
reported rather than gated (probes are now per-repo config, `[lineage.golden]`). A release
is inferred as the first tag dated after a commit, which is wrong for work merged late
(`--offline` was attributed to 2.1.0 instead of 2.0.0); tag ancestry would fix it.

Two lessons are wired into the tool rather than left as advice. Feature names come from a
model, so the naming role is pinned separately from `chat` — switching the chat model once
renamed 118 entries and churned the catalogue. And nothing durable is keyed to a generated
id: the ledger keys on paths, chapters key on the domain's name, because row ids are
assigned by insertion order and shift whenever the catalogue does.

## License

MIT — see [LICENSE](LICENSE).
