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

## The architecture (v0.4): the tool proposes, the ledger decides

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

Requirements: Python ≥ 3.11, `git` on PATH, an embedding backend, a chat LLM.

```bash
ollama pull bge-m3          # embeddings (multilingual, cheap, fine on CPU)
# chat model: a cloud API key (recommended) or a local 14–32B on a GPU
```

## Quickstart

```bash
cp config.example.toml config.toml     # edit: repo path, providers
cp .env.example .env                   # provider API key
gitchronicle update                    # ingest what's new -> KB + kb.html + dossiers
gitchronicle update --chronicle        # ... and narrate each entry's evolution (LLM)
gitchronicle studio                    # browse the catalogue, write rules, see the map
gitchronicle ledger --draft            # (or seed a ledger from the catalogue, headless)
gitchronicle check                     # health: temporal violations, dups, conflicts
```

`update` is the command to schedule: ingest and untangle skip everything already seen, the
assembly is deterministic and cheap, and the ledger means a rebuild cannot disturb your
curation. It ends by reporting what changed — new entries, entries that grew, entries gone.

### The studio

`gitchronicle studio` serves a local page that leads with the diagnostic a terminal
cannot: **which directories are split across many entries**. Fragmentation alone is the
wrong signal — the folder holding all the server code is shared by fifty entries and that
is correct — so it ranks directories that carry a name *no entry answers to*. That is the
shape of a standalone thing the assembly dissolved into its neighbours: on the reference
repo it surfaced a 400-file wiki tool smeared across fifteen game features.

Each card shows the filenames and the current holders, because only those distinguish "one
tool" from "a folder of unrelated features", and offers two answers: make it one entry, or
record `keep-split` and stop being asked. Naming autocompletes against existing entries and
warns when a new name would carve files out of one that already exists. **Map** draws the
catalogue as a layered graph — tiers as layers, edges running down to what a thing is built
on, ordered by alternating barycentre sweeps (128 crossings → 31 on the reference repo).
**Story** shows an entry's arcs. Preview replays rules through the same code the real run
uses and writes nothing until you save.

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

## The (optional) review seam

The pipeline never blocks on a human. When you want to curate:

```bash
gitchronicle taxonomy review --edit   # rebase-i style plan in $EDITOR:
                                      #   accept | reject | lock | merge -> X | rename -> Y
gitchronicle taxonomy list|show|merge|rename|reject|confirm|export|import
gitchronicle run --frozen             # gated mode: exit 2 if changes await review
```

Rejecting tombstones a name forever; every applied verb becomes a golden-record
annotation. Merges persist across re-runs.

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
Credentials rather than an API key; keep `[providers.embed]` on Ollama, since Vertex has no
OpenAI-compatible embeddings endpoint.

Any OpenAI-compatible endpoint works (or Ollama locally). Reference point: a
~6,500-commit, 35k-file game fork — full pipeline including narrated chronicles —
ran for roughly **$40–45** of small-model API cost end-to-end, iterations
included; a single clean pass is a fraction of that.

## Status

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

Honest limits. The assembly still re-partitions as history grows — 58–68% of
feature-grade clusters keep their own evidence across two years — which is *why* curation
lives in the ledger rather than the database, but it does mean un-curated entries drift
between runs. Two of the three golden validation probes are too small to score and are
reported rather than gated. Colour in the map is validated for colour-vision deficiency;
the layout is checked by crossing count, not by eye.

Two lessons are wired into the tool rather than left as advice. Feature names come from a
model, so the naming role is pinned separately from `chat` — switching the chat model once
renamed 118 entries and churned the catalogue. And nothing durable is keyed to a generated
id: the ledger keys on paths, chapters key on the domain's name, because row ids are
assigned by insertion order and shift whenever the catalogue does.

## License

MIT — see [LICENSE](LICENSE).
