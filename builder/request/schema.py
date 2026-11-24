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
    life_es: int | None = Field(None, ge=0)  # maximum Life + maximum Energy Shield together (the user's ET request, 12 Nov)
    mana: int | None = Field(None, ge=0)
    ward: int | None = Field(None, ge=0)  # Runic Ward
    max_hit: dict[DamageType, int] = {}
    resists: dict[DamageType, int] = {
        DamageType.FIRE: 75, DamageType.COLD: 75, DamageType.LIGHTNING: 75, DamageType.CHAOS: 75,
    }
    block: int | None = Field(None, ge=0, le=100)
    evade: int | None = Field(None, ge=0, le=100)
    spare_spirit: int | None = Field(None, ge=0)
    # Optional (off by default): recovery against a boss, as a % of the boss's damage per second after mitigation that
    # the build's sustained recovery (Life, Energy Shield and Mana regeneration, leech, on-hit recovery, flasks at the
    # rate a boss fight refills them, and recoup, less what skill costs use) gives back (objective.boss_recovery)
    recovery: int | None = Field(None, ge=0, le=1000)
    # (THE USER, 23 Nov ~01:00: "I (as well as other people I've played with) won't play a build that doesn't have at least
    # 25% inc move speed outside of the campaign") increased movement speed, net of armour penalties (PoB's
    # MovementSpeedMod, not the fight's Chill-adjusted one), DEFAULT 25 for a request at ENDGAME_LEVEL or above; None: no
    # floor. The request can lower it
    movement_speed: int | None = Field(25, ge=-100, le=500)
    # (THE USER, 23 Nov ~01:00: "even if someone doesn't specifically put in a 'survivable hit' floor value, it should still
    # strive to make the build tanky enough to survive endgame maps and pinnacle fights") a floor by default: survive one
    # of each ordinary hit type the request's boss deals (boss_hits.hit_sizes, headline reading; the red-flash nukes stay
    # a reported row, made to be walked out of). False: glass cannon allowed
    survive_boss_hits: bool = True


class Budget(Model):
    # (THE USER, 22 Nov ~23:50: "don't put any caps on budget ... if I don't put a number in for the budget, it should
    # make me the best possible build without looking at costs") the whole build's spend, any one item may take all of
    # it; None: costs ignored (rares.rare_rules_tier, Context.price_cap)
    divines: float | None = Field(None, ge=0)
    gear_tier: GearTier = GearTier.ENDGAME  # solo self-found only (no prices); ignored when trading
    ssf: bool = False


OWNED_SLOTS = ("Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Belt", "Ring 1", "Ring 2",
               "Ring 3")


class OwnedItem(Model):
    """An exact item the user owns (builder/request/owned.py): its text as the game copies it (Ctrl+C) or PoB exports
    it, and the slot it goes in (None: the item's class decides; a ring or a one-hand weapon must say which)."""

    text: str = Field(min_length=1)
    slot: str | None = None


class BuildRequest(Model):
    schema_version: int = SCHEMA_VERSION
    name: str = "Untitled request"
    notes: str = ""

    league: str | None = None  # None: the current challenge league
    hardcore: bool = False
    level: int = Field(95, ge=1, le=100)
    # (R54-7, the manager's ruling 23 Nov: DEFAULT 0, a new character doesn't have them) passive points beyond level and
    # quests: the Martyr of the First Edict's and Olroth's Boon's, one each
    extra_passive_points: int = Field(0, ge=0, le=2)

    class_name: str | None = None
    ascendancies: list[str] = []  # empty: any (within class_name, if set)
    main_skill: str | None = None  # the skill whose DPS is maximized
    max_buttons: int | None = Field(None, ge=1, le=12)  # player-pressed skills only

    require: Choices = Choices()
    forbid: Choices = Choices()

    weapon_type: str | None = None  # an item class, e.g. "Bow"
    weapon_unique: str | None = None
    # The user's own items (the user, 22 Nov): never counted in the budget. owned_uniques: required uniques (or the
    # weapon unique) the user has; owned_items: exact items pasted in, locked in their slot and built around
    owned_uniques: list[str] = []
    owned_items: list[OwnedItem] = []
    offhand: Offhand = Offhand.ANY
    weapon_swap: WeaponSwap = WeaponSwap.MAY

    defence: DefenceStyle = DefenceStyle.ANY
    floors: Floors = Floors()
    budget: Budget = Budget()

    objective: Objective = Objective.BALANCED
    # The boss the numbers are scored against: a builder/search/bosses.py preset name (its resistances, hits per damage
    # type). None: the default preset (bosses.DEFAULT)
    boss: str | None = None
    dps_gate: float | None = Field(None, ge=0)  # below this, report infeasible with the closest result

    # (PREVIEW1; the user, 23 Nov 2026: announced 1.0 content, asterisked, OFF BY DEFAULT) offer the 1.0 preview gems
    # and weapon classes (builder/preview): every number they touch is "1.0 preview, subject to change"
    preview_1_0: bool = False

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
        for name in self.owned_uniques:
            if name not in self.require.uniques and name != self.weapon_unique:
                problems.append(f"{name} is marked owned but isn't a required unique or the weapon unique")
        for item in self.owned_items:
            if item.slot is not None and item.slot not in OWNED_SLOTS:
                problems.append(f"an owned item's slot {item.slot!r} isn't one of: {', '.join(OWNED_SLOTS)}")
        if self.boss:
            from builder.search import bosses

            if self.boss not in bosses.PRESETS:
                problems.append(f"boss {self.boss!r} is not a preset (one of: {', '.join(sorted(bosses.PRESETS))})")
        if problems:
            raise ValueError("; ".join(problems))
        return self
