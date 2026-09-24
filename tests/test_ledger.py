"""The ledger is the one thing a user writes by hand, so its rules are pinned here."""

from __future__ import annotations

import pytest

from gitchronicle.taxonomy.ledger import Ledger, LedgerError


def test_claim_moves_a_file_and_reject_beats_claim():
    led = Ledger.parse('entry "Tools"\n  claim tools/**\n  reject tools/vendor/**\n')
    out, rep = led.apply({"Misc": {"tools/build.py", "tools/vendor/lib.py"}, "": set()})
    assert out["Tools"] == {"tools/build.py"}
    assert out["Misc"] == {"tools/vendor/lib.py"}
    assert rep["moved"] == 1


def test_reject_releases_what_the_pipeline_wrongly_gave_an_entry():
    led = Ledger.parse('entry "Tasks"\n  reject **/.vscode/**\n')
    out, rep = led.apply({"Tasks": {"a/.vscode/tasks.json", "quests/one.quest"}, "": set()})
    assert out["Tasks"] == {"quests/one.quest"}
    assert rep["released"] == 1


def test_scoped_claim_takes_only_that_entry_s_files():
    """A folder in one entry's territory is not the folder in the repo: without `from`,
    claiming Client-Files/** took 1,345 files instead of the 152 the entry held."""
    led = Ledger.parse('entry "Strings"\n  claim locale/** from "Blob"\n')
    out, _ = led.apply({"Blob": {"locale/it.txt"}, "Other": {"locale/de.txt"}, "": set()})
    assert out["Strings"] == {"locale/it.txt"}
    assert out["Other"] == {"locale/de.txt"}


def test_first_matching_rule_wins_in_file_order():
    led = Ledger.parse('entry "A"\n  claim src/**\nentry "B"\n  claim src/b/**\n')
    out, _ = led.apply({"X": {"src/b/thing.py"}, "": set()})
    assert out["A"] == {"src/b/thing.py"} and not out["B"]


def test_merge_carries_territory_and_tier():
    led = Ledger.parse('entry "Old"\n  tier framework\nmerge "Old" -> "New"\n')
    out, _ = led.apply({"Old": {"a.py"}, "": set()})
    assert out["New"] == {"a.py"} and "Old" not in out
    assert led.tiers()["New"] == "framework"


def test_tombstone_removes_the_entry():
    led = Ledger.parse('reject "Junk"\n')
    out, _ = led.apply({"Junk": {"a.py"}, "": set()})
    assert "Junk" not in out


def test_quoting_separates_a_tombstone_from_a_path_reject():
    led = Ledger.parse('entry "A"\n  claim a/**\nreject "B"\n')
    assert led.tombstones == ["B"] and led.entries[0].rejects == []


def test_repeated_blocks_combine_into_one_entry():
    led = Ledger.parse('entry "A"\n  claim a/**\nentry "A"\n  claim b/**\n')
    assert len(led.entries) == 1 and led.entries[0].claims == ["a/**", "b/**"]


def test_unplaced_files_can_be_rescued_by_a_rule():
    led = Ledger.parse('entry "Docs"\n  claim docs/**\n')
    out, rep = led.apply({"": {"docs/readme.md"}})
    assert out["Docs"] == {"docs/readme.md"} and rep["claimed"] == 1


def test_render_round_trips():
    text = ('entry "A"\n  claim a/**\n  claim b/** from "C"\n  reject a/x/**\n'
            '  tier tooling\n  note words\n  lock\nmerge "D" -> "A"\nkeep-split k/**\n')
    led = Ledger.parse(Ledger.parse(text).render())
    e = led.entries[0]
    assert e.claims == ["a/**"] and e.takes == [("b/**", "C")] and e.rejects == ["a/x/**"]
    assert e.tier == "tooling" and e.note == "words" and e.locked
    assert led.merges == [("D", "A")] and led.kept("k/x")


@pytest.mark.parametrize("text", ['claim a/**\n', 'entry "A"\n  tier nonsense\n',
                                  'merge "A"\n', 'entry "A"\n  wobble x\n'])
def test_bad_rules_are_refused_with_a_line_number(text):
    with pytest.raises(LedgerError):
        Ledger.parse(text)


def test_a_refusal_follows_a_rename():
    """not-uses names entries, and a merge renames one — the verdict has to come along."""
    from gitchronicle.taxonomy.ledger import Ledger
    led = Ledger.parse('not-uses   "Old Name" -> "Luna"\nmerge  "Old Name" -> "New Name"\n')
    assert led.rejected_relations() == [("New Name", "Luna")]
