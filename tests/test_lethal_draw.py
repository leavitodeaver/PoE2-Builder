"""The Lethal Draw's flask-charge cost (COV31, PATCH_CHECK G4-1): PoB adds "added Physical damage equal to (5-10)% of
Flask's Life Recovery amount" to every Bow Attack and ignores "consume 10% of your maximum Life Flask Charges if
possible" (an OVERCOUNT). The line now counts at LethalDrawCharged's share, from the Life Flask's own charge gain."""
from types import SimpleNamespace

from builder.ledger import wrong
from builder.search.assumptions import Assumptions

LINE = ("Bow Attacks consume 10% of your maximum Life Flask Charges if possible to deal added Physical damage equal to "
        "(5-10)% of Flask's Life Recovery amount")


def test_the_line_is_counted_at_its_share():
    entry, fixed = wrong(LINE)
    assert entry.id == "wrong-lethal-draw-flask-charge-cost"
    assert fixed == "LedgerShare:LethalDrawCharged " + LINE  # the item form installLedgerShare counts at the share


def test_no_bow_attack_or_no_life_flask_pays_nothing():
    a = Assumptions.__new__(Assumptions)
    a.build = SimpleNamespace(equipped={})
    assert a.lethal_draw_share("Spark", {"Spell", "Projectile"})[0] == 0.0
    assert a.lethal_draw_share(None, set())[0] == 0.0
    share, how = a.lethal_draw_share("Lightning Arrow", {"Attack", "Bow", "Projectile"})
    assert share == 0.0 and "no Life Flask" in how
