"""The coverage audit's checks of lines PoB says it supports (builder/ledger/coverage.py, items f and g).

f. Lines whose effect can never switch on: a modifier tagged with a condition (or a multiplier) that nothing ever sets. A
   condition is set by a modifier named "Condition:X" on some passive, item, rune, Timeless jewel line or skill; by PoB's
   calculations (condList["X"], NewMod("Condition:X", ...)); or by a Configuration option. One set only by a Configuration
   option counts only if the optimizer turns that option on (the condition resolver, builder/search/conditions.py) or it's
   on by default. Guided Hand is the example: "Ancestrally Boosted Attacks deal 30% increased Damage" parses, but nothing a
   build can have makes an attack Ancestrally Boosted, so the node does nothing.
g. Lines PoB parses but loses part of, from the line's text against its parsed modifiers (name, type, value, flags, keyword
   flags, tags): "more" read as "increased" (or back), a lost sign ("reduced" read as increased), a number that appears in no
   modifier, a condition word ("while", "if", "recently", "per", "for each", "when", "during", "against") with no tag at all,
   and a skill, weapon or damage restriction with no flag or tag. These are heuristics: each finding says what it saw, and
   the ranking puts the ones that could inflate damage or defence first (an overcount is as bad as an undercount).

PoB's source is read as text here only to find which conditions its calculations and options set (an audit, not game data).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from builder import ROOT

POB_SRC = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src"


def _text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"--\[(=*)\[.*?\]\1\]", "", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


@lru_cache(maxsize=1)
def config_options(src: Path = POB_SRC) -> dict[str, dict]:
    """PoB's Configuration options: {var: {"sets": {"Condition:X", "enemy:Condition:X", "Multiplier:X", ...}, "default": bool}}."""
    text = (src / "Modules" / "ConfigOptions.lua").read_text(encoding="utf-8", errors="replace")
    options = {}
    starts = [m.start() for m in re.finditer(r'\{\s*var\s*=\s*"', text)]
    for a, b in zip(starts, starts[1:] + [len(text)]):
        block = text[a:b]
        var = re.match(r'\{\s*var\s*=\s*"([^"]+)"', block).group(1)
        sets = set()
        for m in re.finditer(r'(\w*[mM]odList):NewMod\("((?:Condition|Multiplier):[\w\'.]+)"', block):
            sets.add(("enemy:" if m.group(1).startswith("enemy") else "") + m.group(2))
        # (a list option's default branch counts as on: enemyIsBoss defaults to Pinnacle, which makes the enemy Unique; names
        # built from the chosen value, "Condition:" .. val, aren't matched)
        options[var] = {"sets": sets, "default": bool(re.search(r"defaultState\s*=\s*true|defaultIndex\s*=", block))}
    return options


@lru_cache(maxsize=1)
def calc_setters(src: Path = POB_SRC) -> dict[str, set[str]]:
    """Conditions and multipliers PoB's calculations set themselves (not counting Configuration options): exact names and
    prefixes (from concatenations like condList["Using"..slotName])."""
    exact, prefixes = set(), set()
    files = [p for p in (src / "Modules").glob("*.lua") if p.name not in ("ModParser.lua", "ConfigOptions.lua")]
    files += list((src / "Classes").glob("*.lua")) + list((src / "Data" / "Skills").glob("*.lua"))
    for path in files:
        text = _text(path)
        for kind, table in (("Condition", "condList|conditions"), ("Multiplier", "multipliers")):
            exact.update(f"{kind}:{n}" for n in re.findall(r'(?:%s)\["(\w+)"\]\s*=' % table, text))
            prefixes.update(f"{kind}:{n}" for n in re.findall(r'(?:%s)\["(\w*)"\s*\.\.' % table, text))
            exact.update(f"{kind}:{n}" for n in re.findall(r'(?:%s)\.(\w+)\s*=' % table, text))
        # per-hit conditions PoB sets on the calculation's own config (cfg.skillCond["CriticalStrike"], MainHandAttack ...)
        for line in text.splitlines():
            if "skillCond" in line or "handCondition" in line:
                exact.update(f"Condition:{n}" for n in re.findall(r'"([A-Z]\w+)"', line))
        exact.update(re.findall(r'NewMod\("((?:Condition|Multiplier):[\w\']+)"', text))
        exact.update(re.findall(r'(?:mod|flag)\("((?:Condition|Multiplier):[\w\']+)"', text))
        prefixes.update(re.findall(r'NewMod\("((?:Condition|Multiplier):\w*)"\s*\.\.', text))
    # ("Condition:" .. anything says nothing about which: only prefixes with a word of their own count)
    return {"exact": exact, "prefixes": {p for p in prefixes if len(p.split(":", 1)[1]) >= 3}}


# Modifier names PoB reads only through an uptime its calculations work out, not through a tag: "Ancestrally Boosted
# Attacks deal 30% increased Damage" (Guided Hand) parses into AncestralBoostDamage, which CalcOffence scales by
# BoostedAttackUptimeRatio, and only Fist of War, Ancestral Empowerment and Ancestral Call give that a value.
GATED_NAMES = {
    "AncestralBoostDamage": ("BoostedAttackUptimeRatio", "PoB calculates an Ancestral Boost (Fist of War, Ancestral Empowerment, Ancestral Call)"),
    "AncestralBoostAreaOfEffect": ("BoostedAttackUptimeRatio", "PoB calculates an Ancestral Boost (Fist of War, Ancestral Empowerment, Ancestral Call)"),
}
GATED_FIELDS = sorted({field for field, _ in GATED_NAMES.values()})


def gated(mods: list[dict], stats: dict) -> str | None:
    """If every modifier of a line is one PoB reads only through an uptime that is 0 in this build: what it needs."""
    names = [m.get("n") for m in mods or []]
    if not names or not all(n in GATED_NAMES for n in names):
        return None
    field, needed = GATED_NAMES[names[0]]
    return needed if not stats.get(field) else None


def resolver_configs() -> set[str]:
    """Configuration options the optimizer turns on: the condition resolver's, the charge options, and the boss preset."""
    from builder.search.conditions import CONDITIONS

    return {c.var for c in CONDITIONS} | {"usePowerCharges", "useFrenzyCharges", "useEnduranceCharges"}


def _walk(mods: list[dict], enemy: bool = False):
    """Each modifier with whether it acts on the enemy (an EnemyModifier wrapping it)."""
    for mod in mods or []:
        yield mod, enemy
        if mod.get("i"):
            yield from _walk([mod["i"]], enemy or mod.get("n") == "EnemyModifier")


def line_setters(lines: list[dict]) -> set[str]:
    """Conditions and multipliers that lines set: "Condition:X" (yours) or "enemy:Condition:X" (wrapped for the enemy)."""
    found = set()
    for line in lines:
        for mod, enemy in _walk(line.get("mods") or []):
            name = mod.get("n") or ""
            if name.startswith(("Condition:", "Multiplier:")):
                found.add(("enemy:" if enemy else "") + name)
    return found


def needs(mods: list[dict]) -> list[str]:
    """The conditions and multipliers a parsed line needs: "Condition:X", "enemy:Condition:X", "Multiplier:X" (negated tags,
    "not while X", don't count: they hold until something sets X). A varList needs any one of its names: "Condition:A|B"."""
    found = []
    for mod, enemy in _walk(mods):
        for tag in mod.get("g") or []:
            if tag.startswith("!"):
                continue
            kind, _, var = tag.partition("=")
            if kind == "Condition" and var:
                found.append(("enemy:" if enemy else "") + "Condition:" + var)
            elif kind == "ActorCondition" and var:
                actor, _, cond = var.rpartition(":")
                found.append(("enemy:" if actor == "enemy" else "") + "Condition:" + cond)
            elif kind == "Multiplier" and var:
                found.append(("enemy:" if enemy else "") + "Multiplier:" + var)
    return found


class Setters:
    """Who can set each condition and multiplier: lines and skills, PoB's calculations, Configuration options."""

    def __init__(self, raw: dict, src: Path = POB_SRC):
        lines = [*raw.get("passives", []), *raw.get("uniques", []), *raw.get("affixes", []), *raw.get("legion", []),
                 *raw.get("unique_variants", [])]
        skill_mods = [{"mods": s.get("baseMods") or []} for s in raw.get("skills", [])]
        # mapped gem stats: their modifier names (no tags kept), e.g. a skill stat that sets Condition:X
        stat_names = {n for s in raw.get("skills", []) for st in _stats(s) for n in st.get("names") or []}
        self.lines = line_setters(lines) | line_setters(skill_mods) | {
            ("enemy:" + n.split(">", 1)[1]) if n.startswith("EnemyModifier>") else n
            for n in stat_names if n.split(">")[-1].startswith(("Condition:", "Multiplier:"))}
        calc = calc_setters(src)
        self.calc_exact, self.calc_prefixes = calc["exact"], calc["prefixes"]
        self.options = config_options(src)
        self.turned_on = resolver_configs()

    def alternatives(self, need: str) -> list[str]:
        """The names a need is met by (a varList is any one of them), as they'd appear among a build's setters."""
        kind, _, var = self._plain(need).partition(":")
        enemy = "enemy:" if need.startswith("enemy:") else ""
        return [f"{enemy}{kind}:{one}" for one in var.split("|")] + ([f"{kind}:{one}" for one in var.split("|")] if enemy else [])

    def options_for(self, need: str) -> list[str]:
        """The Configuration options that set a need."""
        kind, _, var = self._plain(need).partition(":")
        enemy = need.startswith("enemy:")
        found = []
        for one in var.split("|"):
            name = f"{kind}:{one}"
            full = ("enemy:" if enemy else "") + name
            found += [v for v, o in self.options.items() if full in o["sets"] or (not enemy and name in o["sets"])]
        return found

    def _plain(self, need: str) -> str:
        return need.split("enemy:", 1)[-1]

    def status(self, need: str) -> tuple[str, str]:
        """("line" | "calc" | "config-on" | "config-off" | "never", detail) for one need (alternatives split by |)."""
        kind, _, var = self._plain(need).partition(":")
        enemy = need.startswith("enemy:")
        best = ("never", "nothing sets it")
        order = ("line", "calc", "config-on", "config-off", "never")
        for one in var.split("|"):
            name = f"{kind}:{one}"
            full = ("enemy:" if enemy else "") + name
            if full in self.lines or name in self.lines:
                found = ("line", "set by passives, items or skills")
            elif name in self.calc_exact or any(name.startswith(p) for p in self.calc_prefixes):
                found = ("calc", "set by PoB's calculations")
            else:
                options = [v for v, o in self.options.items() if full in o["sets"] or (not enemy and name in o["sets"])]
                on = [v for v in options if v in self.turned_on or self.options[v]["default"]]
                if on:
                    found = ("config-on", "set by the Configuration option " + ", ".join(on) + ", which the optimizer turns on")
                elif options:
                    found = ("config-off", "set only by the Configuration option " + ", ".join(options[:3])
                             + ", which the optimizer leaves off")
                else:
                    found = ("never", "nothing sets it")
            if order.index(found[0]) < order.index(best[0]):
                best = found
        return best


def _stats(skill: dict) -> list[dict]:
    out = []
    for set_ in skill.get("sets") or []:
        stats = set_.get("stats") or []
        out += list(stats.values()) if isinstance(stats, dict) else stats
    return out


def never_on(mods: list[dict], setters: Setters) -> list[tuple[str, str, str]]:
    """The needs of a line that can't be met: [(need, status, detail)] for statuses never and config-off, when EVERY
    modifier of the line has one (a line parsed into several modifiers, "for each type of Elemental Ailment", still counts
    through the others). Needs on the item's own hand ({Hand}Attack, filled in when it's equipped) aren't checked."""
    out, blocked_mods, checked = [], 0, 0
    for mod in mods or []:
        mine = [n for n in needs([mod]) if "{" not in n]
        if not mine:
            continue
        checked += 1
        blocked = []
        for need in mine:
            status, detail = setters.status(need)
            if status in ("never", "config-off"):
                blocked.append((need, status, detail))
        if blocked:
            blocked_mods += 1
            out += [b for b in blocked if b not in out]
    unconditional = sum(1 for mod in mods or [] if not [n for n in needs([mod]) if "{" not in n])
    return out if out and blocked_mods == checked and not unconditional else []


# g. What a parse lost ------------------------------------------------------------------------------------------------

NUM = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?")
CONDITION_WORDS = re.compile(r"\b(?:while|if|recently|for each|during|whilst|unless)\b"
                             r"|\bper (?!second|minute|hit\b|enemy hit|enemy killed|cast\b|use\b)\w+"
                             r"|\bagainst (?!you\b)\w+", re.I)
# Names that carry the condition themselves (life on hit or kill, recovery on block, damage taken, charges per use ...)
SELF_CONDITIONED = re.compile(r"On(?:Hit|Block|Kill|Crit|Use)|Taken|Regen|PerSecond|Leech|Recoup|Reflect|Gain|Cost|Duration|"
                              r"Chance|Charges|Penalty|FromMana|Accuracy", re.I)
RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
RADIUS = re.compile(r"\bin radius\b|\bradius also\b", re.I)
RESTRICTIONS = {
    "spell": r"\bspells?\b", "attack": r"\battacks?\b", "minion": r"\bminions?\b", "totem": r"\btotems?\b",
    "projectile": r"\bprojectiles?\b", "melee": r"\bmelee\b", "area": r"\barea (?:damage|skills?)\b",
    "weapon": r"\bwith (?:an? )?(?:bows?|crossbows?|spears?|maces?|swords?|axes?|daggers?|claws?|flails?|quarterstaffs?|staves|staffs?|"
              r"wands?|sceptres?|talismans?|one handed|two handed|shields?|focus)\b",
    "damage type": r"\b(?:fire|cold|lightning|chaos|physical|elemental) damage\b",
}
# PoB flag bits (ModFlag) and keyword flags (KeywordFlag) that stand for each restriction, from Global.lua
RESTRICTION_NAMES = {"spell": "Spell", "attack": "Attack", "minion": "Minion", "totem": "Totem", "projectile": "Projectile",
                     "melee": "Melee", "area": "Area", "weapon": "Weapon", "damage type": "Damage"}


def _numbers(text: str) -> set[float]:
    # a range "(10-20)" counts at both ends and the maximum it was parsed at
    values = set()
    for low, high in re.findall(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)", text):
        values |= {abs(float(low)), abs(float(high))}
    text = re.sub(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)", "", text)
    return values | {abs(float(n)) for n in NUM.findall(text)}


def _values(mods: list[dict]) -> set[float]:
    out = set()
    for mod, _ in _walk(mods):
        v = mod.get("v")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(abs(float(v)))
        elif isinstance(v, str) and (m := re.search(r"=(-?[\d.]+)$", v)):
            out.add(abs(float(m.group(1))))
    return out


def _number_kept(number: float, values: set[float], ranges: list[tuple[float, float]] = ()) -> bool:
    for v in values:
        if any(min(a, b) - 0.011 <= v <= max(a, b) + 0.011 for a, b in ranges):  # a roll range, parsed at some roll
            return True
        for scale in (1, 100, 0.01, 1000, 0.001, 60, 1 / 60, 10, 0.1):
            if abs(v - number * scale) <= max(0.011, 0.011 * abs(v)):
                return True
    return False


def lost(text: str, mods: list[dict]) -> list[dict]:
    """What the parse of one line appears to have lost: [{"kind", "detail", "inflates": bool}]. Lines PoB keeps as flags, or
    as a wrapped modifier (enemy, minion, skill), are checked on what they kept."""
    if not mods or RADIUS.search(text) or text.startswith("Allocates "):
        return []  # (radius jewel lines: builder/search/radius.py applies them to the passives in the radius)
    found = []
    flat = [m for m, _ in _walk(mods)]
    names = " ".join(m.get("n") or "" for m in flat)
    types = {m.get("t") for m in flat}
    tags = [t for m in flat for t in m.get("g") or []]
    lower = text.lower()
    # more / less read as increased, or increased as more
    if re.search(r"\d+(?:\.\d+)?\)?% (?:more|less)\b", lower) and "MORE" not in types and types & {"INC"}:
        found.append({"kind": "more read as increased", "detail": f"'{text}' parsed as INC", "inflates": False})
    if re.search(r"\d+(?:\.\d+)?\)?% (?:increased|reduced)\b", lower) and "MORE" in types and "INC" not in types:
        found.append({"kind": "increased read as more", "detail": f"'{text}' parsed as MORE", "inflates": True})
    # a lost sign: "reduced X" or "less X" with every INC/MORE value positive (and not "reduced X taken", whose sign PoB flips)
    signed = [m for m in flat if m.get("t") in ("INC", "MORE") and isinstance(m.get("v"), (int, float))]
    if re.search(r"\b(reduced|less)\b", lower) and signed and all(m["v"] > 0 for m in signed) \
            and not re.search(r"\b(reduced|less)\b[^.]*\btaken\b|\b(reduced|less)\b[^.]*\bcost|\breduced\b.*\brequirements?\b", lower):
        found.append({"kind": "sign lost", "detail": f"'{text}': every value positive", "inflates": True})
    # a number that appears in no modifier
    numbers = _numbers(text)
    values = _values(mods)
    ranges = [(abs(float(a)), abs(float(b))) for a, b in RANGE.findall(text)]
    if numbers and values and not any(_number_kept(n, values, ranges) for n in numbers) \
            and not re.search(r"equal to|of (?:maximum|your)|as damage of|per \d", lower):  # (shares of a stat: parsed as a ratio)
        found.append({"kind": "number lost", "detail": f"text {sorted(numbers)} vs parsed {sorted(values)}", "inflates": False})
    # a condition word with no tag on any modifier
    if (m := CONDITION_WORDS.search(text)) and not tags and not re.search(r"Condition:|Multiplier:|Per|While|If|Recently|When", names) \
            and not SELF_CONDITIONED.search(names) and not any(isinstance(x.get("v"), str) for x in flat):
        word = m.group(0).lower()
        found.append({"kind": "condition lost", "detail": f"'{word}' in the text, no tag parsed", "inflates": True})
    # a skill, weapon or damage restriction with no flag, keyword flag or tag
    restricted = any((m.get("f") or 0) or (m.get("k") or 0) for m in flat) or tags
    for kind, pattern in RESTRICTIONS.items():
        if kind == "damage type":
            continue  # PoB keeps damage types in the modifier's name (FireDamage), checked below
        if re.search(pattern, lower) and not restricted and RESTRICTION_NAMES[kind].lower() not in names.lower() \
                and not re.search(r"attack and cast|level of all|base critical hit chance|unarmed|attack hits to apply|enemy attacks|with this weapon|taken on minion", lower) \
                and not re.search(r"Gem|Level|Weapon|Local|ExtraSkill|Base|Block", names):
            found.append({"kind": "restriction lost", "detail": f"{kind} restriction in the text, none parsed", "inflates": True})
            break
    typed = re.findall(r"\b(fire|cold|lightning|chaos|physical) damage\b", lower)
    if len(set(typed)) == 1 and not re.search(r"\b(as|to|into|converted|instead)\b", lower) and "damage" in names.lower() \
            and not re.search(r"SelfDamage|Taken", names) \
            and typed[0] not in names.lower() and not restricted:
        found.append({"kind": "damage type lost", "detail": f"{typed[0]} damage in the text, parsed as {names}", "inflates": True})
    return found
