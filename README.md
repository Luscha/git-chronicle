# gitchronicle

**Reconstruct a project's functional-feature *chronicles* from its git history** —
into a queryable, navigable knowledge base you can explore and build a docs/blog
site on top of.

`gitchronicle` reads a repository's commit history and answers a question a plain
`git log` never can: *what features does this codebase have, and how did each one
come to be?* It untangles every commit into the concrete changes it makes, groups
those changes into the **functional features** they serve (Skills, Guild War,
Login, Matchmaking, Rendering, …), and renders an interactive graph where every
feature stays traceable back to its commits and files.

> The tool produces the knowledge base. Turning it into a docs/blog/"requiem"
> site is left to you — export `domains.json` and build on top of it.

---

## The goal

A **domain is a semantic, functional feature** — reconstructed from *what the
changes do*, not from the filesystem layout. This distinction is the whole point:

- A change to a god-class (`char.cpp`) that is *about skills* belongs to **Skills**;
  the same file, changed *about potions*, belongs to **Potions**. Directory
  structure is irrelevant — a feature spans client and server, UI and logic.
- Features must be **isolable**: "Skill Tree" and "Skills" are distinct; a
  potion-policy change is **Potions**, not swallowed into a generic "Player
  Management" blob.
- There is **no target count** — features *emerge* from the history; they are not
  forced to a number.

The output is a two-level hierarchy — **areas → domains (features) → commits &
files** — plus an optional per-feature narrative *chronicle*.

## Why not just read `git log`, or existing tools?

Existing tools model your *current* code (call graphs, dependency graphs) or emit
flat metrics/changelogs. None reconstruct a **temporal, feature-level narrative**
grounded in what each commit actually did — across a god-class monolith where the
filesystem tells you nothing about features. That gap is what this fills.

---

## How it works

The pipeline is a sequence of resumable stages over a single SQLite file (the
source of truth). Re-running any stage skips work already done.

```
 extract ─▶ signals ─▶ untangle ─▶ catalog ──────────────▶ attribute ─▶ lifecycle ─▶ index ─▶ link ─▶ graph
 (git)      (derive)   (LLM: 1     (induce taxonomy ONCE,   (map          (active/     (search)  (LLM:    (HTML +
                        call/commit) then classify every      commits↔      dormant/              relations) JSON)
                                     concern BY ID)           domains)      removed)
                                                                       └▶ [chronicle] (opt-in, LLM narrative)
```

### The data model

| Unit | What it is |
|---|---|
| **Concern** | The atomic unit: one coherent change within a commit — a short capability label + the files it touches. A tangled commit produces several concerns. |
| **Domain** | A functional **feature** — a cluster of concerns that serve the same capability, regardless of which files or subsystems they touch. |
| **Area** | A broad grouping of related domains (e.g. *Combat*, *Items & Economy*, *Infrastructure & Tools*) — the top navigation layer. |

### The stages

1. **extract** — Parse the **default branch's full history**: commits, per-file
   churn, parents, branches, and tags→eras. Full ancestry means every merged-PR
   commit is included; never-merged feature branches are naturally excluded.
2. **signals** — Cheap per-commit enrichment (connected components, conventional-
   commit kind, …).
3. **untangle** — The heart of the pipeline. **One LLM pass per commit** reads the
   diff and splits it into *concerns* (label + one-sentence summary + files).
   Adaptive routing keeps it cheap: a small, clean commit is labelled from its
   message; a tangled/large/terse commit is escalated to read the (bounded) diff.
   This is what lets a change to a god-class be attributed to the *feature* it
   serves rather than the file it lives in.
4. **catalog** — Two-phase feature reconstruction (the TnT-LLM / Clio shape:
   *induce a taxonomy once, then classify everything against it by ID*):
   - **induce** (first run only) — concern facets (label + summary + paths) are
     embedded and clustered; each cluster is named **contrastively** (the LLM sees
     samples from neighbouring clusters and must pick a name that distinguishes
     this one) and gets a **definition** with includes/excludes criteria; one
     reconcile pass merges near-duplicates at taxonomy level. The result is a
     fixed, deduplicated feature list — the label space.
   - **classify** (every run) — each concern is assigned **closed-set, by feature
     ID**, through a cascade: embedding fast path (clear cosine winner, no LLM) →
     LLM pick from a top-k shortlist (an ID can't be misspelled, paraphrased or
     vague; out-of-shortlist answers are rejected and retried) → concerns that fit
     *no* feature accumulate and are grouped into **new provisional features**
     (used immediately, flagged for the optional review) → an audit pass
     re-classifies per-feature embedding outliers. Specificity lives in each
     feature's *definition*, not in global prompt heuristics.
5. **attribute** — Map commits ↔ domains (many-to-many, via their concerns), and
   derive each domain's characteristic files (weighted by *specificity* — a
   god-class touched by every feature is down-weighted; a file unique to one
   feature defines it).
6. **lifecycle** — Mark each domain active / dormant / removed over time.
7. **index** — Semantic (embeddings) + full-text (FTS5) search over the knowledge
   base.
8. **link** — Infer inter-domain relations (LLM-judged from co-change + semantics).
9. **chronicle** *(opt-in, `--chronicle`)* — Narrate each feature's evolution as
   commit-anchored chapters, plus a repository-wide chronicle.
10. **graph** — Export a self-contained interactive browser (`graph.html`) and a
    machine-readable `domains.json`.

By default `run` produces the **structural map** (features named & classified,
their files, authors, and commit timeline). Add `--chronicle` for the **narrative**.

### Incremental runs & the (optional) review seam

The taxonomy is **induced once and then frozen**: later runs classify new commits
against it cheaply and stably. Concerns that fit no existing feature become
**provisional** features — used immediately (nothing is ever left out of the KB),
visibly badged in `graph.html`/`domains.json`, and listed as a pending *changeset*
at the end of each run. The pipeline **never blocks on a human**; provisional
features that keep attracting concerns across runs auto-confirm, so a fully
unattended install converges on its own. When you *do* want to curate:

```bash
gitchronicle taxonomy review          # print the pending changeset
gitchronicle taxonomy review --edit   # rebase-i style plan in $EDITOR:
                                      #   accept | reject | lock | merge -> X | rename -> Y
gitchronicle taxonomy list|show|merge|rename|reject|confirm|export|import
```

Rejecting a feature **tombstones** its name (it is never re-proposed) and re-homes
its concerns. Every applied verb is recorded as a golden-record annotation — an
accumulating regression set for pipeline changes.

**In automation** the plan file is the interface — humans and scripts share it:

```bash
# 1. unattended (cron): run, detect drift, notify — auto-confirm converges the rest
gitchronicle run ...
gitchronicle taxonomy review --json   # {"pending": N, "proposals": [...]}

# 2. gated CI: exit 2 = changes await review (terraform-style)
gitchronicle run --frozen || {
  gitchronicle taxonomy review --edit   # non-tty: writes taxonomy-review.txt
  # commit taxonomy-review.txt (+ taxonomy export) to a branch, open a PR;
  # reviewers edit the verbs in ordinary code review
}
# on PR merge:
gitchronicle taxonomy review --apply taxonomy-review.txt

# 3. headless curation, no files
gitchronicle taxonomy merge "Skill Tree System" "Skill Tree"
gitchronicle taxonomy reject "Misc Fixes"
```

---

## Install (vanilla Python: venv + pip)

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # installs the `gitchronicle` command
```

Requirements: Python ≥ 3.11, `git` on PATH, an embedding backend, and a chat LLM.

```bash
ollama pull bge-m3          # embeddings (multilingual, cheap, runs fine on CPU)
# chat model: a cloud API key (recommended) or a local 14–32B on a GPU
```

## Quickstart

```bash
cp config.example.toml config.toml     # edit: repo path, rev-range, providers
cp .env.example .env                   # add your provider API key
gitchronicle run                       # full pipeline -> build/graph.html
# add --chronicle for the narrative evolution (extra LLM cost)
```

Or run stages individually (each is resumable):

```bash
gitchronicle extract      # commits, file churn, branches, tags
gitchronicle signals      # per-commit signals
gitchronicle untangle     # LLM: diff -> concerns (label + summary + files)
gitchronicle catalog      # induce taxonomy (once) + classify concerns by ID
gitchronicle attribute    # map commits <-> domains, derive files
gitchronicle index        # semantic + full-text search
gitchronicle graph        # build/graph.html + build/domains.json
```

Query the knowledge base:

```bash
gitchronicle domains                   # list features
gitchronicle show "Guild War"          # a feature's files, authors, timeline
gitchronicle ask "how did matchmaking evolve?"
```

## Choosing a model & provider

The dominant cost is **one LLM call per commit** (`untangle`: read a diff,
~1,900 input tokens, split into concerns) — an `O(commits)` workload of genuine
code comprehension. Model class and where you run it are the main constraints.

**Model class** (the `[providers.chat]` model — untangle + naming):

| Class | Verdict |
|---|---|
| **≤3B** | ❌ **Unfitting.** Mislabels concerns, parrots the prompt. Smoke tests only. |
| **7B** | ⚠️ **Usable floor.** Decent but error-prone. |
| **14–32B** | ✅ **Sweet spot** for untangle quality. |
| **70B-class** | Best judgment; reserve for the few `link`/merge/`ask` calls (`[providers.chat_large]`). Overkill for bulk untangle. |

**Where to run it** — same prompt, ~6,000-commit history:

| Backend | Full history | Notes |
|---|---|---|
| **Cloud API** (OpenAI-compatible) | **~minutes, ~$3** at small-model rates | Recommended default. Bring your own key. |
| **Consumer GPU** (RTX 4060+) | **~20 min** | Free after hardware; ideal for local + private. |
| **CPU** (8-core) | **~8–30 h** | Only for small repos or an overnight run. |

It's provider-agnostic — point `[providers.*]` at any OpenAI-compatible endpoint
(or Ollama for local). Runs are cache-resumable; an interrupted run resumes.

## Outputs

All written under `build/` by default (git-ignored):

- **`graph.html`** — self-contained, offline, interactive feature browser
  (areas → domains → commits/files, every node traceable to commit hashes).
- **`domains.json`** — machine-readable knowledge base for downstream use.
- **`gitchronicle.db`** — the full SQLite knowledge base (queryable with SQL).

## Status

**v0.0.1** — catalog rearchitected from open-set growing-list assignment to
**induce → freeze → classify-by-ID** (fixed taxonomy with definitions; closed-set
classification; provisional features + optional review seam; tombstones;
`--frozen` gated mode). This removes the structural causes of hallucinated /
near-duplicate feature names and the need for vague-bucket guardrail wordlists.
Planned next: narrative chronicle polish and full-history runs at scale.

## License

MIT — see [LICENSE](LICENSE).
