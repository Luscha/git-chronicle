# What was measured

Every number here came from a run on a real repository; the method is in the
commit that produced it.

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
