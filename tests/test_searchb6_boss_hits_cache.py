"""SEARCHB6 (SPEED1's PROPOSAL 0, on the manager's loan of boss_hits.py's _biggest / hit_sizes / mean_hits, 23 Nov): the
three are cached by (boss, reading) and a fingerprint of the tables and constants they read. The cache must give the same
answers as reading afresh, and a preset or constant changed in a test must miss it."""
from __future__ import annotations

from builder.search import boss_hits, bosses


def names() -> list[str]:
    return [*bosses.PRESETS, None]


def test_the_cache_gives_what_a_fresh_reading_gives_for_every_preset_and_reading():
    for name in names():
        for reading in boss_hits.READINGS:
            for _ in range(2):  # (a miss, then a hit)
                assert boss_hits.hit_sizes(name, reading) == boss_hits.hit_sizes_read(name, reading)
                assert boss_hits.mean_hits(name, reading) == boss_hits.mean_hits_read(name, reading)
                for nukes in (False, True):
                    if name is not None:
                        assert boss_hits._biggest(name, reading, nukes) == boss_hits._biggest_read(name, reading, nukes)


def test_a_caller_changing_its_answer_doesnt_change_the_cache():
    first = boss_hits.mean_hits(bosses.WORST)
    first["Fire"] = -1.0
    assert boss_hits.mean_hits(bosses.WORST)["Fire"] != -1.0


def test_a_preset_or_constant_changed_in_a_test_misses_the_cache(monkeypatch):
    name = next(n for n in bosses.REAL if boss_hits.hit_table(n)[1].get("Fire"))
    before = boss_hits.hit_sizes(name)
    monkeypatch.setitem(boss_hits.LEVEL_MORE, "elemental", boss_hits.LEVEL_MORE["elemental"] * 2)
    assert boss_hits.hit_sizes(name)["Fire"] == 2 * before["Fire"]
    assert boss_hits.hit_sizes(name) == boss_hits.hit_sizes_read(name)
    monkeypatch.undo()
    assert boss_hits.hit_sizes(name) == before
    raw, counts = boss_hits.hit_table(name)
    monkeypatch.setitem(boss_hits.HIT_TABLE, name, (raw, {**counts, "Fire": 0}))  # (a preset's table swapped)
    assert boss_hits.hit_sizes(name)["Fire"] == 0.0
    assert boss_hits.mean_hits(name) == boss_hits.mean_hits_read(name)
    monkeypatch.undo()
    assert boss_hits.hit_sizes(name) == before
