"""Boss presets for 0.5 (MECH50; FIX_QUEUE "RESEARCH8's findings", RESEARCH9's boss-presets-0-5.md, 12 Nov 2026).

PoB scores every build against its default "Guardian/Pinnacle Boss" (ConfigOptions.lua enemyIsBoss, defaultIndex 3),
whose placeholder resistances are 50% Fire / Cold / Lightning and 0% Chaos. 0.5's pinnacles have far less (0.5.4
lowered them), so elemental hits and elemental ailments were undercounted by x1.1 to x2.3 against physical and chaos.
The bridge's resetConfig now writes the chosen preset's resistances into the config set (BOSS_DEFAULT in bridge.lua
must equal PRESETS[DEFAULT]: tests/test_models.py checks it).

Penetration: PoB already floors it at 0% and leaves a resistance at or below 0% unpenetrated (CalcOffence.lua
calcPenResist, minimum ElementalPenetrationMinimum, 0 by default), as the game's KeywordPopups "Penetration" says
("can only be Penetrated down to a minimum of 0% by default"). (checked: CalcOffence.lua 466-474 and its two callers in
CalcBreakdown.lua; KeywordPopups.json Penetration, game 26476662.)

Every value: poe2db's current monster rows (read 12 Nov 2026, game 0.5.5), via RESEARCH9; resistances MEDIUM-HIGH, Life
x29 rarity at level 82 (formula HIGH, level MEDIUM). A game update (26476662 -> 26579160) was waiting on 12 Nov:
recheck after it. Which boss(es) the objective targets is the user's open question; the default stays the Arbiter of
Ash (the user's reference boss for Heavy Stun, 9 Nov).
"""
from __future__ import annotations

from typing import NamedTuple


class Boss(NamedTuple):
    name: str
    fire: int
    cold: int
    lightning: int
    chaos: int
    life: int  # at level 82 (RESEARCH9: x29 rarity; tiers / Uber not modelled, UNVERIFIED)
    types: tuple[str, ...]
    note: str = ""


PRESETS: dict[str, Boss] = {b.name: b for b in (
    Boss("Arbiter of Ash", 45, -15, 0, 0, 9461668, ("Demon", "Humanoid"), "the default target (RESEARCH4)"),
    Boss("Arbiter of Divinity", -15, 0, 45, 0, 9461668, ("Demon", "Humanoid"), "0.5 apex; 10% of Life as ES"),
    Boss("Xesht, We That Are One", 15, 15, 15, 0, 13762425, ("Demon", "Humanoid"),
         "Breach; PoB's spectre block 0/0/0 is not the boss"),
    Boss("The King in the Mists", 0, 0, 0, 0, 7167930, ("Demon", "Humanoid"), "Ritual; two bars, whole fight"),
    Boss("Zarokh, the Temporal", 15, 15, 15, 0, 7167930, ("Construct",), "Sekhemas"),
    Boss("The Raven Trickster", 0, 0, 0, 0, 9461668, ("Humanoid",), "Delirium; 10% of Life as ES"),
    Boss("The Bodach", 0, 0, 0, 0, 17394177, ("Demon",), "Ritual tier 14+; one bar for both phases (MEDIUM)"),
    Boss("Olroth, Origin of the Fall", 0, 45, 0, 0, 3364148, ("Undead", "Humanoid"), "Expedition"),
    Boss("The Aberration", 45, 0, 0, 0, 9461668, ("Demon",), "Runes of Aldur pinnacle"),
    Boss("Vessel of Kulemak", 0, 0, 0, 0, 2484882, ("Demon",), "Abyss; empowerment by Liches not modelled (UNVERIFIED)"),
    Boss("Atziri, the Red Queen", 15, 15, 15, 0, 7091472, ("Demon", "Humanoid"), "Vaal; phase 1"),
    Boss("Atziri, the Fell Serpent", 15, 15, 15, 0, 9461668, ("Demon", "Humanoid"), "Vaal phase 2; 33% of Life as ES"),
)}
DEFAULT = "Arbiter of Ash"
SOURCE = "poe2db's monster rows, read 12 Nov 2026 (game 0.5.5), RESEARCH9; MEDIUM-HIGH"


def config_vars(name: str = DEFAULT) -> dict[str, int]:
    """PoB config inputs that set the boss's resistances (bridge resetConfig's `boss` argument)."""
    b = PRESETS[name]
    return {"enemyFireResist": b.fire, "enemyColdResist": b.cold, "enemyLightningResist": b.lightning,
            "enemyChaosResist": b.chaos}


def note(name: str = DEFAULT) -> dict:
    """The report's labelled row for the boss the numbers are scored against."""
    b = PRESETS[name]
    return {"label": f"Boss: {b.name}", "value": f"Fire {b.fire}% / Cold {b.cold}% / Lightning {b.lightning}% / "
                                                  f"Chaos {b.chaos}%",
            "basis": f"the boss's own resistances ({SOURCE}), not PoB's default Pinnacle 50/50/50; Penetration stops at "
                     "0% (PoB and the game agree). Other pinnacles differ: elemental damage moves up to x2.3 between "
                     "them",
            "confidence": "MEDIUM-HIGH (recheck after the 12 Nov game update)"}
