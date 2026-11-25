"""COV57 turn 7: TRAIN 146's line readers and Pyromantic Pact's row 35 marked as read."""
from builder.ledger import coverage
from builder.search import flasks, models


def test_cov57f_line_readers_are_marked():
    lines = {"Can instead consume 25% of maximum Mana to trigger Charms with insufficient charges":
             ("model-read-cov57f-mind-phylacteries", flasks.MIND_MANA),
             "Skills lose Combo 20% slower": ("model-read-cov57f-combo-slower", models.COMBO_SLOWER),
             "Maximum Mana is replaced by twice as much Maximum Infernal Flame":
             ("model-read-cov57f-pyromantic-pact-twice-flame", None)}
    for line, (entry_id, reader) in lines.items():
        assert coverage.model_entry(line).id == entry_id, line
        if reader is not None:
            assert reader.search(line), line
