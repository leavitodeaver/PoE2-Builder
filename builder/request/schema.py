"""The build request: everything a user can ask of the optimizer (PLAN.md §4).

Names (skills, supports, passives, uniques, augments) are written as PoB shows them; the
constraint compiler resolves and validates them against the game data.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class WeaponSwap(StrEnum):
    MUST = "must"
    MAY = "may"
    NEVER = "never"


class Offhand(StrEnum):
    ANY = "any"
    NONE = "none"
    SHIELD = "shield"
    FOCUS = "focus"
    QUIVER = "quiver"
    WEAPON = "weapon"  # dual wield


class DefenceStyle(StrEnum):
    """Which defence layers the gear leans on. CI and Mind over Matter are keystones: require them
    under `require.passives`."""

    ANY = "any"
    ARMOUR = "armour"
    EVASION = "evasion"
    ENERGY_SHIELD = "energy_shield"
    ARMOUR_EVASION = "armour_evasion"
    ARMOUR_ENERGY_SHIELD = "armour_energy_shield"
    EVASION_ENERGY_SHIELD = "evasion_energy_shield"
    HYBRID = "hybrid"


class GearTier(StrEnum):
    LEAGUE_START = "league_start"
    MID = "mid"
    ENDGAME = "endgame"
    MIRROR = "mirror"


class Objective(StrEnum):
    BOSS = "boss"
    BALANCED = "balanced"  # boss damage and clear speed together (the default)
    MAPPING = "mapping"


class DamageType(StrEnum):
    PHYSICAL = "physical"
    FIRE = "fire"
    COLD = "cold"
    LIGHTNING = "lightning"
    CHAOS = "chaos"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Choices(Model):
    """Names in each category. Used twice in a request: once as requirements, once as bans."""

    skills: list[str] = []
    supports: list[str] = []
    passives: list[str] = []  # keystones or notables, e.g. "Mind Over Matter"
    uniques: list[str] = []
    item_classes: list[str] = []  # e.g. "Shield", "Two Hand Mace"
    augments: list[str] = []  # runes, Soul Cores, Idols, Ancient Augments
    mechanics: list[str] = []  # gem tags or PoB skill types, e.g. "minion", "CreatesSkeletonMinion"

    def items(self) -> list[tuple[str, str]]:
        return [(category, name) for category, names in self.__dict__.items() for name in names]


class Floors(Model):
    """Numeric minimums. Every floor is optional; none is ever relaxed silently."""

    life: int | None = Field(None, ge=0)
    energy_shield: int | None = Field(None, ge=0)
    mana: int | None = Field(None, ge=0)
    ward: int | None = Field(None, ge=0)  # Runic Ward
    max_hit: dict[DamageType, int] = {}
    resists: dict[DamageType, int] = {
        DamageType.FIRE: 75, DamageType.COLD: 75, DamageType.LIGHTNING: 75, DamageType.CHAOS: 75,
    }
    block: int | None = Field(None, ge=0, le=100)
    evade: int | None = Field(None, ge=0, le=100)
    spare_spirit: int | None = Field(None, ge=0)


class Budget(Model):
    divines: float | None = Field(None, ge=0)  # None: no currency cap
    gear_tier: GearTier = GearTier.ENDGAME
    ssf: bool = False


class BuildRequest(Model):
    schema_version: int = SCHEMA_VERSION
    name: str = "Untitled request"
    notes: str = ""

    league: str | None = None  # None: the current challenge league
    hardcore: bool = False
    level: int = Field(95, ge=1, le=100)

    class_name: str | None = None
    ascendancies: list[str] = []  # empty: any (within class_name, if set)
    main_skill: str | None = None  # the skill whose DPS is maximized
    max_buttons: int | None = Field(None, ge=1, le=12)  # player-pressed skills only

    require: Choices = Choices()
    forbid: Choices = Choices()

    weapon_type: str | None = None  # an item class, e.g. "Bow"
    weapon_unique: str | None = None
    offhand: Offhand = Offhand.ANY
    weapon_swap: WeaponSwap = WeaponSwap.MAY

    defence: DefenceStyle = DefenceStyle.ANY
    floors: Floors = Floors()
    budget: Budget = Budget()

    objective: Objective = Objective.BALANCED
    dps_gate: float | None = Field(None, ge=0)  # below this, report infeasible with the closest result

    allow_exploits: bool = True
    forbid_ledger_entries: list[str] = []

    seed_code: str | None = None  # PoB code to start from
    effort_minutes: int = Field(10, ge=1, le=240)

    @model_validator(mode="after")
    def _consistent(self) -> BuildRequest:
        problems = []
        required = set(self.require.items())
        for category, name in self.forbid.items():
            if (category, name) in required:
                problems.append(f"{name} is both required and forbidden ({category})")
        if self.main_skill and ("skills", self.main_skill) in set(self.forbid.items()):
            problems.append(f"main skill {self.main_skill} is forbidden")
        if self.weapon_unique and ("uniques", self.weapon_unique) in set(self.forbid.items()):
            problems.append(f"weapon {self.weapon_unique} is forbidden")
        if problems:
            raise ValueError("; ".join(problems))
        return self
