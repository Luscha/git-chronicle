# How gitchronicle works

## The models are yours

gitchronicle ships no endpoint, no key and no vendor. `[endpoints]` says where models live
— a protocol (openai, anthropic, ollama: the only three that cost code), an address, an
auth method — and `[roles]` says which endpoint and model does which job (chat, untangle,
naming, narration, answer, judge; each falls back to `chat`). A `kind` is sugar for a row of
data, so a new provider is config rather than a release, and a provider that speaks none of
the three protocols belongs behind a LiteLLM or OpenRouter proxy. Thinking is one setting,
translated per endpoint — it is billed as output and was 52% of all untangle tokens.

## The architecture: the tool proposes, the ledger decides

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

A reference resolves by basename, so **the syntax that produced it decides what it may
name**: `#include "config.h"` cannot mean `config.js`, and the target must still exist at
HEAD. The rule is an exclusion, not a whitelist — an unknown extension (`.fx` including
`.fxh`, `.forge` requiring a `.lua` library) resolves as before, and a Python import may
still reach a native extension module. It removed 85 of 227 edges on the reference
repository, two of whose six biggest hubs were collisions.

Edges are evidence, not fact, so both directions are open to the owner: `uses "…"` states a
relation no import can show, `not-uses "A" -> "B"` deletes one the imports assert, and the
studio shows the references behind every link before you decide.
`chronicle` (opt-in) narrates each feature's evolution as commit-anchored
chapters. Each chapter's evidence is this domain's own dated work items — never the raw
subjects of multi-feature commits — plus two facts about the chapter's edge: how long the
work stopped afterwards, and which of the domain's own files no longer exist. Without them
a feature's death reads as a shipment; with them it is told and dated (`docs/measurements.md`). Chapters follow **arcs**, not the calendar: a break means the work genuinely
stopped for months, and a long run of upkeep is one "kept it running" chapter rather than
one per month. Building versus maintaining is read from whether the entry gained code, not
from what the commit message called it. Battle Pass went from 14 chapters to 4; the corpus
went from 1,909 to 659.

---
