"""EVAL — does the knowledge base tell the truth?

Every earlier version was judged on its structure: blob size, territory, golden probes.
None of that says whether an answer is right. This asks the knowledge base questions whose
answers the owner already knows, then grades each answer against those known facts.

The grader is a model, so it is told exactly which facts to look for and asked to name
the ones missing or contradicted — a verdict with reasons can be checked by a person, a
bare score cannot. Hallucination is scored separately from omission: saying "the evidence
doesn't cover this" is a better failure than inventing a date.

Question file (JSON list):
    [{"q": "When was the battle pass first removed?",
      "facts": ["removed in August 2021"],
      "unanswerable": false}]
"""

from __future__ import annotations

import json
from pathlib import Path

_JUDGE = """You check an answer about a software project's history against known facts.

Judge each KNOWN FACT on its own:
- "stated": the answer says it (dates to the month are enough; paraphrase is fine). Quote \
the words that state it.
- "missing": the answer does not say it, and says nothing incompatible with it.
- "contradicted": the answer gives a DIFFERENT value for the SAME thing the fact is about \
(another month for the same event, another person for the same act). Quote those words. \
A range that contains the fact's date, a later phase, or extra events are NOT contradictions: \
"introduced June 29, 2022; initial work ran June to October 2022" STATES "June 2022".
A date in the answer that MATCHES the fact is "stated", never "contradicted".
Work is done before it ships. If the answer gives when work was done AND when it shipped \
or was released, compare the fact with the one it describes: a fact "added in October 2020" \
is stated by "built in August, shipped in 2.3.0 in October 2020". An earlier development \
date is never a contradiction of a later release date, or the reverse.

Then list "invented": specific claims that give a different value for something a known fact \
states. Extra \
detail the facts do not mention is NOT invented — it is simply unchecked.

If the question is marked UNANSWERABLE, its single fact is that the project does not \
contain it: an answer saying the evidence has nothing on it STATES that fact, even if it \
goes on to describe related parts of the project.

Return JSON only:
{"facts": [{"fact": "...", "verdict": "stated|missing|contradicted", "quote": "..."}],
 "invented": ["..."]}"""


def run_eval(questions_path: str, kb_path: str, provider, log=print, samples: int = 1) -> dict:
    """``samples`` > 1 asks every question that many times as separate calls. One run of
    this eval swung 16 points on a change of prompt wording alone, so a difference
    between two builds means something only when it exceeds the spread across samples."""
    import statistics

    from .serve.search import Index, ask

    qs = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    index = Index(kb_path)
    rows, per_sample = [], [0] * samples
    for i, q in enumerate(qs, 1):
        facts = q.get("facts", [])
        scores = []
        for k in range(samples):
            r = ask(index, kb_path, provider, q["q"], sample=f"s{k}" if k else "")
            user = (f"QUESTION: {q['q']}\n"
                    + ("UNANSWERABLE: yes\n" if q.get("unanswerable") else "")
                    + "KNOWN FACTS:\n" + "\n".join(f"- {f}" for f in facts)
                    + f"\n\nANSWER:\n{r['answer']}")
            v = provider.chat(_JUDGE, user, want_json=True, role="judge", stage="eval")
            v = v if isinstance(v, dict) else {}
            verdicts = [f for f in v.get("facts") or [] if isinstance(f, dict)]
            stated = sum(f.get("verdict") == "stated" for f in verdicts)
            bad = [f"{f.get('fact')} — “{f.get('quote', '')}”" for f in verdicts
                   if f.get("verdict") == "contradicted"] + list(v.get("invented") or [])
            missing = [f.get("fact") for f in verdicts if f.get("verdict") == "missing"]
            # scored here, not by the grader: a model asked for a number gave correct answers 0
            score = 0 if bad or not stated else (2 if stated == len(facts) else 1)
            scores.append(score)
            per_sample[k] += score
            rows.append({"q": q["q"], "sample": k, "score": score, "answer": r["answer"],
                         "missing": missing, "contradicted": bad, "verdicts": verdicts,
                         "read": r["used"]})
        marks = "".join({2: "✓", 1: "~", 0: "✗"}.get(x, "?") for x in scores)
        log(f"  {marks:<{samples}} {i:>2}. {q['q']}")
        if samples == 1:
            for m in rows[-1]["contradicted"]:
                log(f"        contradicted: {m}")
            for m in rows[-1]["missing"]:
                log(f"        missing: {m}")
    n = len(qs)
    pct = [100 * t / max(1, 2 * n) for t in per_sample]
    mean = statistics.mean(pct)
    spread = f" ± {statistics.pstdev(pct):.0f} (samples: {', '.join(f'{x:.0f}' for x in pct)})" \
        if samples > 1 else ""
    wrong = sum(1 for r in rows if r["contradicted"]) / samples
    log(f"\n  score {mean:.0f}%{spread} · answers with a contradiction or invention "
        f"{wrong:.1f}/{n} per run")
    return {"score_pct": mean, "samples_pct": pct, "contradicted_per_run": wrong, "rows": rows}
