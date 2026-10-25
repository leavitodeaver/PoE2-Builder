"""What a search knows besides its engine: the request compiled against game data, prices, and
the catalogs derived from both (which gems may be socketed, which items fit each slot at what
price, the passive graph).

The parent process sends plain data (`payload`); each process builds a `Context` from it once and
caches it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import deque
from dataclasses import dataclass
from functools import cached_property

from builder.data import GameData, rare_prices
from builder.data.rules import rules as game_rules
from builder.ledger import gap_list, translate, wrong
from builder.request.constraints import CompiledRequest, compile_request
from builder.request.schema import BuildRequest, DefenceStyle, GearTier

# Most a single unique may cost at each gear tier (divine), on top of the request's divine cap
TIER_UNIQUE_CAP = {GearTier.LEAGUE_START: 1.0, GearTier.MID: 20.0, GearTier.ENDGAME: 300.0, GearTier.MIRROR: math.inf}
SUPPORT_SLOTS = 5  # support sockets on a fully socketed skill gem
MAX_GEM_GROUPS = 9  # non-item gem groups the game allows
GEM_LEVEL = 20
GEM_QUALITY = 20

ARMOUR_SLOTS = ["Helmet", "Body Armour", "Gloves", "Boots"]
JEWELLERY_SLOTS = ["Amulet", "Ring 1", "Ring 2", "Belt"]
SLOT_TYPE = {"Helmet": "Helmet", "Body Armour": "Body Armour", "Gloves": "Gloves", "Boots": "Boots",
             "Amulet": "Amulet", "Ring 1": "Ring", "Ring 2": "Ring", "Belt": "Belt"}
TWO_HANDED = {"Bow", "Crossbow", "Staff", "Two Hand Axe", "Two Hand Mace", "Two Hand Sword", "Talisman"}
DEFENCE_LAYERS = {
    DefenceStyle.ARMOUR: {"Armour"}, DefenceStyle.EVASION: {"Evasion"},
    DefenceStyle.ENERGY_SHIELD: {"EnergyShield"}, DefenceStyle.ARMOUR_EVASION: {"Armour", "Evasion"},
    DefenceStyle.ARMOUR_ENERGY_SHIELD: {"Armour", "EnergyShield"},
    DefenceStyle.EVASION_ENERGY_SHIELD: {"Evasion", "EnergyShield"},
    DefenceStyle.HYBRID: {"Armour", "Evasion", "EnergyShield"},
}


@dataclass(frozen=True)
class ItemOption:
    name: str  # the unique's name, or the base name for a normal item
    base: str
    type: str
    sub_type: str | None
    raw: str
    unique: bool
    price: float | None  # divine; None in SSF, where prices don't apply
    confidence: str  # price confidence: high / medium / low, "found" for normal bases, "ssf"
    level: int
    rare: bool = False
    affixes: tuple[str, ...] = ()  # a rare's modifier lines
    runes: tuple[str, ...] = ()  # what's in its sockets

    def describe(self) -> dict:
        return {"name": self.name, "base": self.base, "type": self.type, "unique": self.unique, "rare": self.rare,
                "price": self.price, "confidence": self.confidence, "affixes": list(self.affixes),
                "runes": list(self.runes)}


def normal_item(base: dict) -> str:
    """A normal (white) base, with its implicit: PoB doesn't add implicits by itself."""
    implicit = base.get("implicit")
    if not implicit:
        return f"Rarity: Normal\n{base['name']}"
    lines = [re.sub(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)",
                    lambda m: str(int(round((float(m.group(1)) + float(m.group(2))) / 2))) if "." not in m.group(0)
                    else f"{(float(m.group(1)) + float(m.group(2))) / 2:.1f}", line) for line in implicit.splitlines()]
    return "\n".join([f"Rarity: Normal", base["name"], f"Implicits: {len(lines)}", *lines])


class Context:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = BuildRequest.model_validate(payload["request"])
        self.data = GameData()
        self.unique_prices: dict[str, list] = payload.get("unique_prices") or {}
        self.gem_prices: dict[str, float] = payload.get("gem_prices") or {}
        self.augment_prices: dict[str, float] = payload.get("augment_prices") or {}
        self.rare_model = rare_prices.RarePriceModel.from_payload(payload.get("rare_model"))
        self.rare_model.materials = dict(payload.get("craft_prices") or {})
        rare_prices.use(self.rare_model)

    @cached_property
    def compiled(self) -> CompiledRequest:
        return compile_request(self.request, self.data)

    @property
    def ssf(self) -> bool:
        return self.request.budget.ssf

    @property
    def level(self) -> int:
        return self.request.level

    @cached_property
    def price_cap(self) -> float:
        """Most any single purchase may cost."""
        cap = TIER_UNIQUE_CAP[GearTier(self.request.budget.gear_tier)]
        if self.request.budget.divines is not None and not self.ssf:
            cap = min(cap, self.request.budget.divines)
        return cap

    @property
    def budget(self) -> float:
        divines = self.request.budget.divines
        return math.inf if divines is None or self.ssf else divines

    # Gems -------------------------------------------------------------------------------------

    @cached_property
    def gems(self) -> dict[str, dict]:
        """Gems a player can socket at the requested level, by name."""
        gems = {}
        for gem in self.data.gems:
            if gem.get("fromItem") or gem.get("fromTree") or gem.get("legacy"):
                continue
            if (gem.get("levelRequirement") or 0) > self.level:
                continue
            gems.setdefault(gem["name"], gem)
        return gems

    def gem_price(self, name: str) -> tuple[float | None, str] | None:
        """Price of a gem that costs real currency (lineage supports); None if it can't be bought
        within the caps. Ordinary gems are cheap and priced at 0 ("found")."""
        gem = self.gems.get(name)
        if gem is None:
            return None
        if not gem.get("lineage"):
            return 0.0, "found"
        if self.ssf:
            return None if self.request.budget.gear_tier == GearTier.LEAGUE_START else (None, "ssf")
        price = self.gem_prices.get(name)
        if price is None or price > self.price_cap:
            return None  # unpriced items are never treated as free
        return price, "exchange"

    @cached_property
    def all_gems(self) -> dict[str, dict]:
        """Every gem by name, including skills only passives or items grant."""
        gems: dict[str, dict] = {}
        for gem in self.data.gems:
            gems.setdefault(gem["name"], gem)
        return gems

    @cached_property
    def granted_by(self) -> dict[str, list[tuple[str, str, str | None]]]:
        """Skill name -> [(kind, source name, ascendancy)] for skills a passive ("passive") or a
        unique ("unique") grants."""
        grants: dict[str, list[tuple[str, str, str | None]]] = {}
        pattern = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$")
        for node in self.data.tree["nodes"]:
            for line in node.get("stats") or []:
                if m := pattern.search(line):
                    grants.setdefault(m.group(1), []).append(("passive", node["name"], node.get("ascendancy")))
        for unique in self.data.uniques:
            for line in unique["raw"].splitlines():
                if m := pattern.search(re.sub(r"\{[^}]*\}", "", line)):
                    grants.setdefault(m.group(1), []).append(("unique", unique["name"], None))
        return grants

    def skill_source(self, skill: str) -> tuple[str, str, str | None] | None:
        """For a skill no gem gives, the first allowed passive or unique that grants it."""
        for kind, source, ascendancy in self.granted_by.get(skill, []):
            if kind == "passive" and source not in self.compiled.filters.forbidden_passives:
                return kind, source, ascendancy
            if kind == "unique" and source not in self.compiled.filters.forbidden_uniques:
                return kind, source, ascendancy
        return None

    def allowed_gem(self, name: str) -> bool:
        return name in self.gems and name not in self.compiled.filters.forbidden_gems

    @cached_property
    def persistent_candidates(self) -> list[str]:
        """Persistent skills (auras, heralds, buffs) that need no trigger setup: candidates for
        spare Spirit. Meta gems need a socketed skill to do anything and aren't proposed yet."""
        names = []
        for name, gem in self.gems.items():
            types = set(gem["skillTypes"])
            if gem["support"] or "Persistent" not in types or "Meta" in types:
                continue
            if self.allowed_gem(name) and self.gem_price(name) is not None:
                names.append(name)
        return sorted(names)

    # Items ------------------------------------------------------------------------------------

    @cached_property
    def bases_by_name(self) -> dict[str, dict]:
        return {b["name"]: b for b in self.data.bases}

    def _base_ok(self, base: dict) -> bool:
        req = base.get("req") or {}
        return (not base.get("hidden") and (req.get("level") or 0) <= self.level
                and base["type"] not in self.compiled.filters.forbidden_item_classes)

    def _style_ok(self, base: dict | None) -> bool:
        """Armour pieces must only carry the defence layers the requested style allows."""
        style = DefenceStyle(self.request.defence)
        if style == DefenceStyle.ANY or base is None or base["type"] not in SLOT_TYPE.values():
            return True
        layers = {k for k, v in (base.get("armour") or {}).items() if k in ("Armour", "Evasion", "EnergyShield") and v}
        return layers <= DEFENCE_LAYERS[style]

    @cached_property
    def gaps(self) -> dict:
        """PoB's own list of passive and unique lines it doesn't calculate."""
        return gap_list(self.data.dir)

    @cached_property
    def passive_corrections(self) -> list[dict]:
        """Ledger translations for ignored passive lines: [{id, entry, lines}] for the bridge."""
        found = []
        for gap in self.gaps.get("passives", []):
            if translated := translate(gap["line"]):
                found.append({"id": gap["id"], "entry": translated[0].id, "lines": translated[1]})
        return found

    @cached_property
    def unique_gaps(self) -> dict[str, set[str]]:
        """Unique name -> its lines PoB ignores."""
        found: dict[str, set[str]] = {}
        for gap in self.gaps.get("uniques", []):
            found.setdefault(gap["unique"], set()).add(gap["line"])
        return found

    def corrected_raw(self, name: str, raw: str) -> str:
        """A unique's text with ledger corrections: lines standing in for the ones PoB ignores are
        added at the end, and lines PoB gets wrong are replaced in place. Each correction keeps its
        line's variant tags, so only the selected version of the unique counts."""
        ignored = self.unique_gaps.get(name, set())
        lines, added = [], []
        for line in raw.split("\n"):
            prefix, text = re.match(r"^((?:\{[^}]*\})*)(.*)$", line).groups()
            if text in ignored and (translated := translate(text)) and not translated[0].conditional:
                added += [prefix + extra for extra in translated[1]]
            elif fixed := wrong(text):
                line = prefix + fixed[1]
            lines.append(line)
        return "\n".join(lines + added)

    def unique_price(self, name: str) -> tuple[float | None, str] | None:
        if self.ssf:
            return None, "ssf"
        entry = self.unique_prices.get(name)
        if not entry:
            return None
        divine, listings = entry
        if divine > self.price_cap:
            return None
        confidence = "unknown" if listings is None else "high" if listings >= 20 else "medium" if listings >= 5 else "low"
        return divine, confidence

    @cached_property
    def item_options(self) -> dict[str, list[ItemOption]]:
        """Every item the gear block may propose, by item type ("Bow", "Helmet", "Ring", ...):
        normal bases (always available) and affordable, allowed uniques."""
        options: dict[str, list[ItemOption]] = {}
        rules = game_rules()
        for base in self.data.bases:
            # Bases only uniques use (Golden Visage, ...) and weapon classes not in the game yet
            # are never normal, magic or rare items (builder/data/game_rules.yaml)
            if self._base_ok(base) and self._style_ok(base) and rules.normal_base_ok(base):
                options.setdefault(base["type"], []).append(ItemOption(
                    base["name"], base["name"], base["type"], base.get("subType"), normal_item(base), False,
                    0.0, "found", (base.get("req") or {}).get("level") or 0))
        forbidden = self.compiled.filters.forbidden_uniques
        for unique in self.data.uniques:
            base = self.bases_by_name.get(unique["base"])
            if unique["name"] in forbidden or base is None or not self._base_ok(base) or not self._style_ok(base):
                continue
            priced = self.unique_price(unique["name"])
            if priced is None:
                continue
            price, confidence = priced
            options.setdefault(base["type"], []).append(ItemOption(
                unique["name"], base["name"], base["type"], base.get("subType"),
                self.corrected_raw(unique["name"], unique["raw"]), True,
                price, confidence, (base.get("req") or {}).get("level") or 0))
        return options

    def unique_option(self, name: str) -> ItemOption | None:
        """A specific unique (required by the request), affordable or not."""
        for unique in self.data.uniques:
            if unique["name"] == name:
                base = self.bases_by_name.get(unique["base"])
                if base is None:
                    return None
                priced = self.unique_price(name) or (None, "over budget or unpriced")
                return ItemOption(name, base["name"], base["type"], base.get("subType"),
                                  self.corrected_raw(name, unique["raw"]), True,
                                  priced[0], priced[1], (base.get("req") or {}).get("level") or 0)
        return None

    # Passive tree -----------------------------------------------------------------------------

    @cached_property
    def nodes(self) -> dict[int, dict]:
        return {n["id"]: n for n in self.data.tree["nodes"]}

    @cached_property
    def class_of(self) -> dict[str, str]:
        """Ascendancy name -> class name."""
        return {a: c for c, ascendancies in self.data.classes.items() for a in ascendancies}

    @cached_property
    def forbidden_node_ids(self) -> set[int]:
        names = self.compiled.filters.forbidden_passives
        return {n["id"] for n in self.data.tree["nodes"] if n.get("name") in names}

    def ascendancy_graph(self, ascendancy: str) -> tuple[int | None, dict[int, list[int]]]:
        """(start node, adjacency) of an ascendancy's own nodes. An alternate ascendancy with no
        nodes of its own (Abyssal Lich) uses the nodes of the one it shares a start node with:
        PoB switches those nodes to the alternate's stats when it's selected."""
        members = {i for i, n in self.nodes.items() if n.get("ascendancy") == ascendancy}
        if not members:
            start_node = next((a["startNode"] for c in self.data.tree["classes"] for a in c["ascendancies"]
                               if a["name"] == ascendancy), None)
            host = (self.nodes.get(start_node) or {}).get("ascendancy")
            if host and host != ascendancy:
                members = {i for i, n in self.nodes.items() if n.get("ascendancy") == host}
        start = next((i for i in members if self.nodes[i]["type"] == "AscendClassStart"), None)
        return start, {i: [j for j in self.nodes[i]["linked"] if j in members] for i in members}

    def main_tree_distances(self, class_name: str) -> dict[int, int]:
        """Hop distance of every main-tree node from the class start (for picking among
        same-named nodes)."""
        start = next(c["startNode"] for c in self.data.tree["classes"] if c["name"] == class_name)
        distances = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for other in self.nodes[node]["linked"]:
                if other not in distances and other in self.nodes and not self.nodes[other].get("ascendancy"):
                    distances[other] = distances[node] + 1
                    queue.append(other)
        return distances


_cache: dict[str, Context] = {}


def payload_key(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def context_for(payload: dict) -> Context:
    """The process-wide Context for a payload (game data loads once per worker)."""
    key = payload_key(payload)
    if key not in _cache:
        _cache.clear()
        _cache[key] = Context(payload)
    return _cache[key]


def make_payload(request: BuildRequest, prices=None) -> dict:
    """Plain data for workers: the request and the prices it may spend on (`prices` is a
    builder.data.prices.PriceTable, or None when there's no price data)."""
    unique_prices: dict[str, list] = {}
    gem_prices: dict[str, float] = {}
    augment_prices: dict[str, float] = {}
    if prices is not None:
        for name in prices.uniques:
            price = prices.unique(name)
            if price is not None:
                unique_prices[name] = [price.divine, price.listings]
        for name, price in prices.exchange.items():
            if price.category == "LineageSupportGems":
                gem_prices[name] = price.divine
            elif price.category in ("Runes", "SoulCores", "Idols"):
                augment_prices[name] = price.divine
    league = getattr(prices, "league", None)
    return {
        "request": request.model_dump(mode="json"),
        "unique_prices": unique_prices,
        "gem_prices": gem_prices,
        "augment_prices": augment_prices,
        "league": league,
        "rare_model": rare_prices.RarePriceModel.load(league).payload(),
        "craft_prices": craft_prices(prices),
    }


def craft_prices(prices) -> dict[str, float]:
    """Jewel crafting materials from poe.ninja (empty without price data: jewels then use the
    market model alone)."""
    if prices is None:
        return {}
    found = {name: prices.exchange[name].divine for name in rare_prices.CRAFT_MATERIALS if name in prices.exchange}
    return found if len(found) == len(rare_prices.CRAFT_MATERIALS) else {}
