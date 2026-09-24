"""LEDGER — the curation, kept outside the derived database.

Every earlier attempt stored human corrections in the DB, and every one of them lost
those corrections on the next run: ``emit_register`` deletes and recreates the output,
and the assembly that produced the ids re-partitions as history grows. Measured (P0b, on
12 years of real history): of the clusters a curation would attach to, only 58-68% still
hold their own evidence after two more years of commits, and the best of six candidate
identity keys -- seed stem, birth roots, minimum concern id, founding commit, founding
triple, oldest root -- tracks its cluster just 89% of the time. That ceiling is
structural, not a tuning problem: a cluster's founding member is itself a clustering
output, so it moves whenever the partition moves.

So the ledger never references a generated id. An entry is defined by RULES over things
git already guarantees are stable -- paths and the identifiers the owner coined:

    entry "Luna Scripting System"
      claim  Server/libluna/**
      claim  luna/**
      reject luna/protobuf/**     # per-feature schemas belong to their features
      tier   framework
      note   The scripting bridge everything else registers against.

    merge      "Wiki Config" -> "Game Wiki"
    reject     "Constinfo System"
    keep-split Client-Files/uiscript/**

This is deliberately the shape of the ``## Scope`` section in ``gitchronicle.md``, which
is the one curation format in this project that has ever survived a re-run. Rules are
text; text does not churn. The assembly stops being an authority and becomes a proposal
engine for evidence no rule has claimed -- free to re-partition, because nothing curated
depends on it.

Replay is pure: no LLM, no network, no git. Re-graining costs milliseconds, which is what
makes "try it and look" the normal way to use the tool.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

PLAN_FILE = "gitchronicle.plan"

TIERS = ("foundation", "framework", "feature", "content", "tooling")

_HEADER = """\
# gitchronicle — the curation ledger.
#
# Replayed on every run, in file order. This file is the authority: anything it states
# outranks what the pipeline inferred. Nothing here references a generated id, so it
# keeps working as history grows.
#
#   entry "<name>"          declare/curate an entry
#     claim  <glob>           these files are its territory, wherever they live
#     claim  <glob> from "<n>"  ... but only those entry <n> currently holds
#     reject <glob>           ... except these; on a proposed entry, gives them back
#     tier   <t>              foundation | framework | feature | content | tooling
#     note   <text>           your own words, carried into the knowledge base
#     lock                    auto-runs may never rename or re-tier it
#   merge      "<from>" -> "<to>"
#   reject     "<name>"     tombstone: never proposed again
#   keep-split <glob>       this directory is MEANT to span several entries — stop asking
#   ignore     <word>       this word runs through the work but names nothing — stop asking
"""


class LedgerError(ValueError):
    pass


class Entry:
    """One curated entry: a name plus the rules that decide what belongs to it."""

    def __init__(self, name: str):
        self.name = name
        self.claims: list[str] = []
        # (glob, holder): only the files `holder` currently has. Sorting one entry's files
        # into another must not also sweep up the rest of the repo under the same folder.
        self.takes: list[tuple[str, str]] = []
        self.rejects: list[str] = []
        self.tier: str | None = None
        self.note: str | None = None
        self.locked = False

    def owns(self, path: str) -> bool:
        """Rejects always beat claims, so a narrow exception can be written under a broad
        rule without having to restate the broad rule as a list of negations."""
        if any(fnmatch.fnmatch(path, g) for g in self.rejects):
            return False
        return any(fnmatch.fnmatch(path, g) for g in self.claims)

    def __repr__(self) -> str:
        return f"<Entry {self.name!r} claims={len(self.claims)} tier={self.tier}>"


class Ledger:
    """Parsed curation. ``entries`` keeps file order, which is also precedence order."""

    def __init__(self, entries=None, merges=None, tombstones=None, keep_splits=None,
                 ignored=None):
        self.entries: list[Entry] = entries or []
        self.merges: list[tuple[str, str]] = merges or []
        self.tombstones: list[str] = tombstones or []
        # "these files SHOULD belong to different entries" — a judgement as real as a
        # claim, and the only one the studio previously threw away on every restart
        self.keep_splits: list[str] = keep_splits or []
        # words the owner has looked at and judged to name nothing — the review queue's
        # equivalent of keep-split, and just as necessary: without it the same question
        # comes back on every run
        self.ignored: list[str] = ignored or []

    # -- parsing ----------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = PLAN_FILE) -> "Ledger":
        p = Path(path)
        if not p.exists():
            return cls()
        return cls.parse(p.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> "Ledger":
        entries: list[Entry] = []
        merges: list[tuple[str, str]] = []
        tombs: list[str] = []
        keeps: list[str] = []
        ignored: list[str] = []
        by_name: dict[str, Entry] = {}
        cur: Entry | None = None

        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip() if not _in_quotes_hash(raw) else raw.strip()
            if not line:
                continue
            verb, _, rest = line.partition(" ")
            verb, rest = verb.lower(), rest.strip()

            if verb == "entry":
                name = _unquote(rest, lineno)
                # a repeated entry block adds to the first rather than shadowing it
                cur = by_name.get(name)
                if cur is None:
                    cur = Entry(name)
                    by_name[name] = cur
                    entries.append(cur)
            elif verb == "merge":
                src, _, dst = rest.partition("->")
                if not dst.strip():
                    raise LedgerError(f"line {lineno}: merge needs '-> \"target\"'")
                merges.append((_unquote(src, lineno), _unquote(dst, lineno)))
                cur = None
            elif verb == "keep-split":
                keeps.append(rest)
                cur = None
            elif verb == "ignore":
                ignored.append(rest.strip().strip('"').lower())
                cur = None
            elif verb == "reject" and _is_quoted(rest):
                # `reject "Name"` tombstones an entry; `reject <glob>` carves paths out of
                # the block above. Deciding by whether a block is open instead would make a
                # tombstone written after one silently become that entry's path filter.
                tombs.append(_unquote(rest, lineno))
                cur = None
            elif cur is None:
                raise LedgerError(f"line {lineno}: '{verb}' outside an entry block")
            elif verb == "claim":
                m = re.fullmatch(r'(\S+)\s+from\s+("[^"]*"|\'[^\']*\')', rest)
                if m:
                    cur.takes.append((m.group(1), _unquote(m.group(2), lineno)))
                else:
                    cur.claims.append(rest)
            elif verb == "reject":
                cur.rejects.append(rest)
            elif verb == "tier":
                t = rest.lower()
                if t not in TIERS:
                    raise LedgerError(f"line {lineno}: tier '{t}' not one of {TIERS}")
                cur.tier = t
            elif verb == "note":
                cur.note = rest
            elif verb == "lock":
                cur.locked = True
            else:
                raise LedgerError(f"line {lineno}: unknown verb '{verb}'")

        return cls(entries, merges, tombs, keeps, ignored)

    # -- replay -----------------------------------------------------------
    def owner(self, path: str) -> str | None:
        """The entry a path belongs to: FIRST matching rule in file order wins, so
        precedence is visible on the page rather than hidden in a scoring function."""
        for e in self.entries:
            if e.owns(path):
                return e.name
        return None

    def destination(self, path: str, holder: str) -> str | None:
        """Where the rules send a file the assembly gave to ``holder``: another entry's
        name, "" when ``holder`` rejects it and nobody claims it, None when it stays."""
        own = self.owner(path)
        if own is not None:
            return None if own == holder else own
        for x in self.entries:
            if x.name != holder and any(src == holder and fnmatch.fnmatch(path, g)
                                        and not any(fnmatch.fnmatch(path, r) for r in x.rejects)
                                        for g, src in x.takes):
                return x.name
        e = next((x for x in self.entries if x.name == holder), None)
        if e is not None and any(fnmatch.fnmatch(path, g) for g in e.rejects):
            return ""
        return None

    def apply(self, catalogue: dict[str, set[str]]) -> tuple[dict[str, set[str]], dict]:
        """Replay over a proposed catalogue (name -> territory).

        Returns the curated catalogue and a report of what the rules actually did — the
        feedback the retired charter never gave, which is why nobody could tell whether
        it helped.
        """
        out = {n: set(fs) for n, fs in catalogue.items()}
        unplaced = out.pop("", set())
        moved = claimed = released = 0

        # 1. rules take territory from whoever the assembly gave it to, and an entry's own
        #    rejects give back what the assembly wrongly gave it — without that, a blob
        #    could only be carved by naming a new home for every piece of it
        for name, files in list(out.items()):
            for f in list(files):
                dest = self.destination(f, name)
                if dest is None:
                    continue
                files.discard(f)
                if dest:
                    out.setdefault(dest, set()).add(f)
                    moved += 1
                else:
                    released += 1

        # 2. a rule may also claim files the assembly never placed; the caller passes
        #    those in under the empty key, so curation can rescue unattributed evidence
        for f in unplaced:
            own = self.owner(f)
            if own is not None:
                out.setdefault(own, set()).add(f)
                claimed += 1

        # 3. an entry that only the ledger declares still exists, even when empty
        for e in self.entries:
            out.setdefault(e.name, set())

        # 4. merges, then tombstones (a tombstoned merge target takes its source with it)
        for src, dst in self.merges:
            if src in out:
                out.setdefault(dst, set()).update(out.pop(src))
        for name in self.tombstones:
            out.pop(name, None)

        return out, {"moved": moved, "claimed": claimed, "released": released,
                     "entries": len(self.entries), "merges": len(self.merges),
                     "tombstones": len(self.tombstones)}

    def tiers(self) -> dict[str, str]:
        return self._through_merges({e.name: e.tier for e in self.entries if e.tier})

    def notes(self) -> dict[str, str]:
        return self._through_merges({e.name: e.note for e in self.entries if e.note})

    def _through_merges(self, d: dict[str, str]) -> dict[str, str]:
        """A renamed entry keeps what was said about it: rename is a merge into the new
        name, and the tier set on the old name must not be lost on the way."""
        for src, dst in self.merges:
            if src in d and dst not in d:
                d[dst] = d[src]
        return d

    def locked(self) -> set[str]:
        return {e.name for e in self.entries if e.locked}

    def kept(self, path: str) -> bool:
        """True when a rule says this path's directory is meant to be shared."""
        return any(fnmatch.fnmatch(path, g) for g in self.keep_splits)

    def exists(self) -> bool:
        return bool(self.entries or self.merges or self.tombstones or self.keep_splits
                    or self.ignored)

    # -- writing ----------------------------------------------------------
    def render(self) -> str:
        out = [_HEADER]
        for e in self.entries:
            out.append(f'entry "{e.name}"')
            out += [f"  claim  {g}" for g in e.claims]
            out += [f'  claim  {g} from "{src}"' for g, src in e.takes]
            out += [f"  reject {g}" for g in e.rejects]
            if e.tier:
                out.append(f"  tier   {e.tier}")
            if e.note:
                out.append(f"  note   {e.note}")
            if e.locked:
                out.append("  lock")
            out.append("")
        out += [f'merge      "{s}" -> "{d}"' for s, d in self.merges]
        out += [f'reject     "{n}"' for n in self.tombstones]
        out += [f"keep-split {g}" for g in self.keep_splits]
        out += [f"ignore     {w}" for w in self.ignored]
        return "\n".join(out).rstrip() + "\n"

    def save(self, path: str | Path = PLAN_FILE) -> None:
        Path(path).write_text(self.render(), encoding="utf-8")


def _is_quoted(s: str) -> bool:
    s = s.strip()
    return len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'"


def _unquote(s: str, lineno: int) -> str:
    s = s.strip()
    m = re.fullmatch(r'"([^"]*)"|\'([^\']*)\'', s)
    if not m:
        if not s:
            raise LedgerError(f"line {lineno}: expected a quoted name")
        return s
    return m.group(1) if m.group(1) is not None else m.group(2)


def _in_quotes_hash(raw: str) -> bool:
    """True when a '#' sits inside quotes, so a name may contain one."""
    h = raw.find("#")
    return h != -1 and raw.count('"', 0, h) % 2 == 1
