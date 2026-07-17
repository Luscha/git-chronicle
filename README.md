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

## The architecture (v0.2): identity from the worktree, narrative from history

Earlier versions tried to *induce* features from commit clusters. Measured result:
commits are changes, and a pile of changes is a poor definition of a thing. The
current architecture inverts this, then lets history correct and enrich it:

```
            WORKTREE  (what the code IS)                 HISTORY  (what happened)
 ┌────────────────────────────────────────────┐   ┌─────────────────────────────────┐
 │ delta    inherited vs authored (fork-aware) │   │ untangle  commit -> concerns    │
 │ anchors  coined frameworks claim territory  │   │ classify  concerns -> features  │
 │ carve    stem/pkg/dir units + consolidation │   │           (evidence-gated)      │
 │ shelves  vendored/content/generated/docs    │   │ refine    history names things  │
 └──────────────────┬─────────────────────────┘   │ chronicle narrated chapters     │
                    │        the REGISTER          └───────────────┬─────────────────┘
                    └──────────────┬───────────────────────────────┘
                                   ▼
                  SQLite KB  ->  kb.html + dossiers/*.md|json + index.md
```

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

### 3. Local carving + evidence merges (the residue)

What anchors didn't claim is carved by component-scoped stem families, package and
directory units, then consolidated by *use*: duplicate territories fold, mutual
importers merge, fragments join their sole user, and a self-contained subtree the
project barely touched collapses into one tool. Docs are triaged (design / how-to /
meta): only corroborated design docs stand as features; a feature must own code.
Third-party (LICENSE-bearing subtrees), code-named data corpora, and generated
files are shelved, not deleted.

### 4. Attribution (closed-set, evidence first)

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

### 5. History refines the identity card

A feature's name/definition comes from code peeks; its attributed commits carry
the maintainer's own vocabulary. A post-attribution pass may rewrite name and
definition — text only, deterministically gated (no change-language, vocabulary
must recur in ≥2 commits, coined identifiers preserved, old names kept as
searchable aliases).

### 6. Relations & chronicle

`uses` edges come from static imports over register territory, including
embedded-interpreter registrations (`import luna` resolves to the C++ bridge that
registers it) — deterministic, no LLM. High fan-in marks framework hubs.
`chronicle` (opt-in) narrates each feature's evolution as commit-anchored
chapters; single-commit chapters skip the LLM entirely.

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
gitchronicle run                       # pipeline -> SQLite KB + HTML
gitchronicle relations                 # uses/used-by edges (local, free)
gitchronicle dossier --out dossiers    # kb.html + per-feature md/json + index.md
# optional:
gitchronicle chronicle                 # narrated evolution chapters (LLM)
gitchronicle check                     # health: temporal violations, dups, conflicts
```

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

Any OpenAI-compatible endpoint works (or Ollama locally). Reference point: a
~6,500-commit, 35k-file game fork — full pipeline including narrated chronicles —
ran for roughly **$40–45** of small-model API cost end-to-end, iterations
included; a single clean pass is a fraction of that.

## Status

**v0.2 — "the delta and the anchors."** Register derived from the worktree with
fork-aware authored/inherited grounding and anchor-first framework assembly;
evidence-gated attribution with temporal grounding; deterministic relations;
history-refined naming; md/json/html knowledge-base exports. Validated against a
12-year, 6.5k-commit production fork with owner review; known limits: anchor
naming still peek-derived until refine runs, and co-change territory constraints
(report-only merge/split suggestions) are designed but not yet shipped.

## License

MIT — see [LICENSE](LICENSE).
