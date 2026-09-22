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
- "contradicted": the answer says something incompatible with it. Quote those words.
A date in the answer that MATCHES the fact is "stated", never "contradicted".

Then list "invented": specific claims in the answer that contradict a known fact. Extra \
detail the facts do not mention is NOT invented — it is simply unchecked.

If the question is marked UNANSWERABLE, its single fact is that the project does not \
contain it: an answer saying the evidence has nothing on it STATES that fact, even if it \
goes on to describe related parts of the project.

Return JSON only:
{"facts": [{"fact": "...", "verdict": "stated|missing|contradicted", "quote": "..."}],
 "invented": ["..."]}"""


def run_eval(questions_path: str, kb_path: str, provider, log=print) -> dict:
    from .serve.search import Index, ask

    qs = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    index = Index(kb_path)
    rows, total = [], 0
    for i, q in enumerate(qs, 1):
        r = ask(index, kb_path, provider, q["q"])
        facts = q.get("facts", [])
        user = (f"QUESTION: {q['q']}\n"
                + ("UNANSWERABLE: yes\n" if q.get("unanswerable") else "")
                + "KNOWN FACTS:\n" + "\n".join(f"- {f}" for f in facts)
                + f"\n\nANSWER:\n{r['answer']}")
        v = provider.chat(_JUDGE, user, want_json=True, role="chat_large")
        v = v if isinstance(v, dict) else {}
        verdicts = [f for f in v.get("facts") or [] if isinstance(f, dict)]
        stated = sum(f.get("verdict") == "stated" for f in verdicts)
        bad = [f"{f.get('fact')} — “{f.get('quote', '')}”" for f in verdicts
               if f.get("verdict") == "contradicted"] + list(v.get("invented") or [])
        missing = [f.get("fact") for f in verdicts if f.get("verdict") == "missing"]
        # scored here, not by the grader: a model asked for a number gave correct answers 0
        score = 0 if bad or not stated else (2 if stated == len(facts) else 1)
        total += score
        rows.append({"q": q["q"], "score": score, "answer": r["answer"], "missing": missing,
                     "contradicted": bad, "verdicts": verdicts, "read": r["used"]})
        mark = {2: "✓", 1: "~", 0: "✗"}.get(score, "?")
        log(f"  {mark} {i:>2}. {q['q']}")
        for m in bad:
            log(f"        contradicted: {m}")
        for m in missing:
            log(f"        missing: {m}")
    n = len(qs)
    full = sum(1 for r in rows if r["score"] == 2)
    wrong = sum(1 for r in rows if r["contradicted"])
    log(f"\n  score {total}/{2 * n} ({100 * total / max(1, 2 * n):.0f}%) · fully right {full}/{n}"
        f" · with a contradiction or invention {wrong}/{n}")
    return {"score": total, "max": 2 * n, "full": full, "contradicted": wrong, "rows": rows}
