# What was measured

Every number here came from a run on a real repository; the method is in the
commit that produced it.

## A file with two owners has no story

Territory is the union of two passes — the work's own evidence, and files carrying an
entry's name. Each resolved its own contests and neither looked at the other, so the same
file could be claimed twice: `notification.proto` belonged to Protobuf by the work done on
it and to Notification System by its filename.

| | before | after name claims yield to concern evidence |
|---|---|---|
| files owned twice | 464 | **0** |
| territory rows | 6,753 | 6,289 |
| entries / commits attributed | 225 / 5,129 | unchanged |
| dependency links | 161 | **177** |
| eval (17 questions, 3 samples) | 84% ± 1 | **89% ± 1** |

Not merely tidier: while a file had two owners, both entries carried it, both could
attribute its commits, and retrieval saw one piece of evidence under two names — so an
answer could name the wrong owner and still look supported. The extra links are a side
effect: import edges between territories that were previously hidden inside a
double-owned file resolve into real edges once ownership is single.

## An import edge is only as good as the name it resolves

Import targets were matched by basename across the whole repository, with no notion of
language. `#include "config.h"` in a server file resolved to `config.js` in the web admin
panel; `import sys` resolved to a `sys.*` that had been deleted years earlier and still sat
in the history-derived territory.

| | before | after |
|---|---|---|
| `uses` edges | 234 | **163** |
| entries with no link | 122 | 132 |
| biggest hubs | Luna (35), Italy Tour (29), Game Wiki (29), UniversalCore (19), CCC (19) | Game Wiki (29), Luna (19), UniversalCore (13), Rhi Rendering (11) |

Two of the five biggest "frameworks" were made of collisions. The fix is a rule about
syntax, not a list of names: a reference may only resolve to a file its own import form
could be naming, and only to one that still exists at HEAD. Stated as an exclusion, so
shader `#include`s of `.fxh` and `.forge` scripts requiring `.lua` libraries survive — both
were verified to still resolve after the change.

## A third repository: Alacritty, and what the score does not measure

2,493 commits of Rust over ten years, a language the pipeline had never seen. The whole
build took 13 minutes, of which untangling 2,454 commits was 4.9.

| | httpie | void-queue | **alacritty** |
|---|---|---|---|
| eval | 83% | 90% ± 1 | **96% ± 3** |

The one failed question was a bad question: asked whether Alacritty has tabs, the knowledge
base answered that it has no tab management of its own but supports macOS native tabs since
July 2023 (`bfcebbcd38`, shipped 0.12.3) — verified in the repository, and a finer
distinction than the question allowed.

**And the catalogue behind that 96% is the weakest of the three.** A third of its entries
are named after directories (`ref`, `res`, `osx`, `wix`), twenty of sixty are upkeep
buckets, the most-edited files in the project (`term/mod.rs`, 174 commits) belong to no
entry, and the dependency graph had **no edges at all** until Rust imports were added, then
8. Answers ride on untangled work items, attribution and chapters; entry names and edges
make the catalogue browsable. The two are much more independent than they look, and an eval
score alone will not tell you the catalogue is poor.

## Narration reports what it is told, and only that

The battle pass was switched off on 2021-08-21 by a commit whose own untangled summary is
"battlepass gets removed". The chapter containing it was titled "Introduced and Refined",
and the story said "a battle pass quest removal feature was implemented" — a death told as
a shipment. Nothing was missing from retrieval: in v0.4 that chapter ended at the removal
and was titled "Battle Pass UI, Quests, and Removal", and it only stopped doing so because
attribution grew and the arc swallowed the event.

Two facts were then added to every chapter's evidence — how long the work stopped
afterwards, and which of the entry's own files no longer exist — and its work items were
dated.

| | eval | "removed in August 2021" | contradictions/run |
|---|---|---|---|
| before | 91% ± 0 | absent | 1.0 |
| + boundaries | 90% ± 1 | mentioned, misdated ("late October") | 1.3 |
| + dated work items | 85% ± 0 | **stated** | 2.0 |
| + "dates place events, they are not the story" | 88% ± 2 | **stated** | 1.7 |

Dating every work item made the narration more precise and less descriptive: the skill-tree
answer became one exact sentence and dropped which sides were implemented. Saying so in the
prompt recovered most of it. The net against the original is −3 ± 2 points for a removal
that is finally reported and correctly dated — worth it for a tool whose claim is *what was
built and what replaced it*, and the honest way to read it is that these two configurations
are within each other's error bars.

## What is not the product is worth more than any clustering tweak

The scope map decides which paths the pipeline may look at. Left alone, `init` drafts it
from touch counts, and on the reference repository that admitted a vendored Boost tree —
whose exclusion the owner had written, but an `include:` one level up silently overrode it.

| | before | after marking `Extern-Server/Extern/**` external |
|---|---|---|
| concerns in the corpus | 17,720 | **14,823** (2,897 dropped) |
| concerns mentioning "traits" | 121 | **21** — the rest were C++ `<type_traits>` headers |
| eval (16 questions, 3 samples) | 82% ± 1 | **90% ± 1** |
| rebuild | — | 52 s, no model calls |

Eight points from one line of scope, and it also corrected the *question*: "121 pieces of
trait work are unfiled" was itself an artefact of the vendored tree. The real figure is 21.

What it did **not** do is make a Trait entry appear. The remaining trait work is genuinely
entangled with the scripting bridge that binds it (`luna/bind_char_traits.cpp`), so twelve
of its files still sit under that entry. That is the grain problem, not a scope problem,
and the ledger is what settles it — which is the division of labour the tool is built on.

## Publishing pass (v0.5)

Measured while making the repository fit to publish:

| change | effect |
|---|---|
| the first commit counts as an upstream import only when it is itself a bulk drop | httpie: 2,086 files in the fork vs 7 in httpie — its own first commit was being filed as vendor code, giving it fake "Inherited baseline" entries |
| work that belongs to no feature keeps a catch-all entry ("Upkeep — <component>") | httpie attribution 611 → 1,688 of 1,797 commits; eval 79% → 83%. Removing the bucket outright had cost 11 points, because the evidence had nowhere to live |
| the v0.1–v0.3 stages deleted | ~4,200 lines; `--help` shows nine commands |

The same pass moved the fork's eval from 91% to 79% and httpie's from 90% to 83%, and the
cause was not isolated: attribution changed by ~1% and 29 entries were re-narrated, which
is enough to change which evidence a question retrieves. Treat differences under ten
points on these sets as noise, and read the per-question marks rather than the total.

## Results

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

**Scan direction, renames and thinking (v0.5 experiments).** Walking history newest-to-oldest
does not change what the pipeline finds — untangle is per-commit and clustering is global —
but three things measured on it were worth keeping:

| change | effect |
|---|---|
| territory in **current** names (renames resolved, cycle-guarded) | files split across two entries by a rename: **123 → 0**; 236 duplicate paths gone; answers unchanged (83% ± 6 vs 84% ± 5) |
| giving an entry its files' **pre-rename** history | **rejected**: Wiki Manager "began" in 2024 as the old wiki builder (84% → 76%) |
| untangle **newest-first**, written as it goes | the last month is queryable after ~4 min instead of after the whole run |
| Gemini Flash **thinking off** (`thinking_budget = 0`) | untangle of 6.5k commits: **$30 → $7.51** and **~5 h → 30 min**; answers **91% ± 0**, the best measured |

The last row is a re-untangle from scratch, so it also measures the floor: a full first build
of a 6,574-commit repository is about **$8 and half an hour**, narration included. Labels
come out slightly terser than with thinking on (26 vs 30 characters, 1.58 vs 1.45 concerns
per commit) and lose nothing the eval can see.

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
