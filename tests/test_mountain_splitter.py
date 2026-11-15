"""Mountain Splitter from the build's own slam (COV31, PATCH_CHECK G1-2): "Every Third Slam Skill that doesn't create
Fissures which you use yourself causes 3 additional Aftershocks ahead and to each side of the initial area". Before,
every Slam got a flat 11% more DPS (CLAUDE.md's no-flat-rates rule; an OVERCOUNT for Fissure slams and slams you don't
use yourself). Now the line counts only while MountainSplitterSlam is on."""
from builder.ledger import translate
from builder.search.assumptions import mountain_splitter_slam

LINE = ("Every Third Slam skill that doesn't create Fissures which you use yourself causes 3 additional Aftershocks "
        "ahead and to each side of the initial area")
SLAM = {"Attack", "Slam", "Area", "Melee"}


def test_the_line_counts_only_through_the_condition():
    entry, lines = translate(LINE)
    assert entry.id == "mountain-splitter"
    assert lines == ["share:MountainSplitterSlam Slam Skills have 33% more DPS"]  # one hit of three per third slam


def test_only_a_slam_you_use_yourself_that_creates_no_fissures_counts():
    group = {"index": 1, "gems": [{"name": "Boneshatter"}]}
    me = {"group": 1, "attack": True}
    assert mountain_splitter_slam("Boneshatter", SLAM, me, [], group)[0]
    assert not mountain_splitter_slam("Sunder", SLAM | {"CreatesFissure"}, me, [], group)[0]
    crater = {"index": 1, "gems": [{"name": "Leap Slam"}, {"name": "Crater"}]}
    assert not mountain_splitter_slam("Leap Slam", SLAM, me, [], crater)[0]
    assert not mountain_splitter_slam("Boneshatter", SLAM, {**me, "triggered": True}, [], group)[0]
    totem = [{"name": "Spell Totem", "group": 1}]  # (models.TOTEM_METAS: the totem uses it, not you)
    assert not mountain_splitter_slam("Boneshatter", SLAM, me, totem, group)[0]
    assert not mountain_splitter_slam("Spark", {"Spell", "Projectile"}, {"group": 1}, [], group)[0]
    assert not mountain_splitter_slam(None, set(), {}, [], None)[0]
