"""Coverage audit: every mechanic source in the game data, and whether our numbers count it.

PoB's own gap list (gaps.json) only holds the passive and unique lines its parser admits it can't
read. This audit doesn't take its word for it. It covers:

  a. passive and unique lines PoB ignores (gaps.json);
  b. item affixes, runes / Soul Cores / Idols and jewel mods (coverage.json from the bridge's
     `auditCoverage`: each line through PoB's parser, ranges at their maximum);
  c. skill and support gem stats PoB never reads: every stat of every stat set (all levels share
     one stat list; quality and constant stats included) against the stat set's stat map, the
     test PoB's own skills tab uses to mark a stat unsupported;
  d. silent no-ops: lines PoB parses into modifier names that no calculation asks for. The
     calculation side is read from PoB's Lua source (Modules/Calc*.lua and the rest of Modules/,
     Classes/Mod*.lua, the skill data's functions): every name a modifier query (Sum, More, Flag,
     Override, List, Max, Tabulate, HasMod, calcLib.mod/val, ...) is called with, names built by
     concatenation counted as wildcards. This reads PoB's code as text for an audit only; no game
     data comes from it.

Every line gets one status: `pob` (counted by PoB), `ledger` (translated by a ledger entry,
including entries that record "no effect on what's calculated"), `model` (fed to a custom model)
or `none` (not counted). What isn't counted is ranked by impact: the kind of mechanic (damage,
defence, recovery and speed before flavour), the kind of source (keystones, ascendancy passives
and notables before small passives; popular uniques by poe.ninja listing counts, an economy
endpoint), and how many sources share the line.

    uv run python -m builder.ledger.coverage            # report from the last dump
    uv run python -m builder.ledger.coverage --refresh  # re-run the bridge part first (one engine)

Writes data/pob-<commit>/coverage-report.json and prints a summary.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from builder import ROOT
from builder.ledger import entries, model_entry, parse_audit, translate

POB_SRC = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src"
QUERIES = ("Sum", "More", "Flag", "Override", "List", "Max", "Min", "Tabulate", "HasMod", "GetCondition",
           "GetMultiplier", "GetStat", "SumInternal", "MoreInternal", "FlagInternal", "ListInternal")
QUERY = re.compile(r"(?::(?:%s)|calcLib\.(?:mod|val|mods)|\bcalcLocal)\(" % "|".join(QUERIES))
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
CHAIN = re.compile(r'(?:"[^"]*"|[\w.\[\]]+)(?:\s*\.\.\s*(?:"[^"]*"|[\w.\[\]:()]+))+')
RANGE_TEXT = re.compile(r"\(-?\d+(?:\.\d+)?-\d+(?:\.\d+)?\)")
NUMBER = re.compile(r"[+-]?\(?-?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\)?")

STATUSES = ("pob", "ledger", "model", "none")


# What PoB's calculations read -------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    text = re.sub(r"--\[(=*)\[.*?\]\1\]", "", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


def _call_args(text: str, start: int) -> str:
    depth, i = 1, start
    while i < len(text) and depth:
        c = text[i]
        if c == '"':
            j = text.find('"', i + 1)
            while j > 0 and text[j - 1] == "\\":
                j = text.find('"', j + 1)
            i = j if j > 0 else len(text)
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return text[start:i - 1]


@lru_cache(maxsize=1)
def calc_reads(src: Path = POB_SRC) -> dict:
    """Names PoB's calculations query: exact names, wildcard patterns from concatenations, and every
    string literal and skillData key in the code (for condition and skill-data checks)."""
    files = [p for p in (src / "Modules").glob("*.lua") if p.name != "ModParser.lua"]
    files += list((src / "Classes").glob("*.lua"))
    files += list((src / "Data" / "Skills").glob("*.lua")) + [src / "Data" / "Minions.lua", src / "Data" / "Misc.lua"]
    exact: set[str] = set()
    patterns: set[str] = set()
    literals: set[str] = set()
    skill_data: set[str] = set()
    for path in files:
        if not path.exists():
            continue
        text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        literals.update(STRING.findall(text))
        skill_data.update(re.findall(r"skillData\.(\w+)", text))
        skill_data.update(re.findall(r'skillData\["(\w+)"\]', text))
        for m in QUERY.finditer(text):
            args = _call_args(text, m.end())
            for chain in CHAIN.findall(args):
                parts = re.split(r"\s*\.\.\s*", chain)
                glob = "".join(re.escape(p[1:-1]) if p.startswith('"') else r"\w*" for p in parts)
                if re.sub(r"\\w\*", "", glob):
                    patterns.add(glob)
            exact.update(STRING.findall(args))
    # Conditions and multipliers are read through tags on other modifiers (var = "Name"), which the
    # parser writes too
    parser = _strip_comments((src / "Modules" / "ModParser.lua").read_text(encoding="utf-8", errors="replace"))
    tag_vars = set(re.findall(r'var\s*=\s*"([^"]+)"', parser))
    for blob in re.findall(r"varList\s*=\s*\{([^}]*)\}", parser):
        tag_vars.update(STRING.findall(blob))
    for path in files:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            tag_vars.update(re.findall(r'var\s*=\s*"([^"]+)"', text))
    config = src / "Modules" / "ConfigOptions.lua"
    if config.exists():
        tag_vars.update(re.findall(r'"Condition:(\w+)"', config.read_text(encoding="utf-8", errors="replace")))
    return {"exact": exact, "patterns": re.compile("^(?:%s)$" % "|".join(sorted(patterns))) if patterns else None,
            "literals": literals, "skill_data": skill_data, "tag_vars": tag_vars}


def name_read(name: str, reads: dict | None = None) -> bool:
    """Does any calculation ask for this modifier name? `Outer>Inner` names (a modifier wrapped for
    an enemy or a minion) need both."""
    reads = reads or calc_reads()
    if ">" in name:
        return all(name_read(part, reads) for part in name.split(">", 1))
    if name.startswith("SkillData:"):
        return name.split(":", 1)[1] in reads["skill_data"]
    if ":" in name:  # Condition:X, Multiplier:X, ... read through tags or by name
        kind, var = name.split(":", 1)
        return var in reads["tag_vars"] or name in reads["exact"] or name in reads["literals"] or \
            bool(reads["patterns"] and reads["patterns"].match(name))
    return name in reads["exact"] or bool(reads["patterns"] and reads["patterns"].match(name))


def unread(names: list[str] | dict, reads: dict | None = None) -> list[str]:
    """The names no calculation reads, when that's all of them (a line also writing a marker
    nothing reads, like "+10 to Strength and Dexterity"'s StrDex, still counts through the rest)."""
    names = list(names.values()) if isinstance(names, dict) else names or []
    missing = sorted({n for n in names if not name_read(n, reads)})
    return missing if names and len(missing) == len(set(names)) else []


# What a line is about, and how much it matters ---------------------------------------------------

MECHANICS = (
    ("damage", r"damage|critical|penetrat|crit\b|ignite|shock|bleed|poison|freeze|chill|electrocute|"
               r"exposure|withered|wither|curse|projectile|area of effect|skill effect|accuracy|"
               r"impale|culling|overwhelm|power charge|frenzy charge|onslaught|archon|infusion|"
               r"spell|attack|minion|totem|strike|combo|glory|empower|maximum hit|deal"),
    ("defence", r"armour|evasion|energy shield|resist|block|damage taken|maximum life|life\b|"
                r"\bward\b|deflect|suppress|endurance charge|avoid|ailment|stun threshold|reduction|"
                r"spirit|reservation|mana\b|darkness|guard|unaffected"),
    ("recovery", r"regenerat|leech|recover|recoup|flask|charm|life on|mana on|refill|restore"),
    ("speed", r"attack speed|cast speed|skill speed|cooldown|duration|warcry speed|reload|use speed"),
)
MECHANIC_WEIGHT = {"damage": 4.0, "defence": 3.0, "speed": 3.0, "recovery": 2.5, "flavour": 0.1}
FLAVOUR = re.compile(r"movement speed|rarity|quantity|light radius|experience|stun\b(?! threshold)|"
                     r"knockback|pickup|collected|cosmetic|display|visual|flask charges gained|"
                     r"charm charges|item rarity|reveal|currency|gold", re.I)
SOURCE_WEIGHT = {"keystone": 6.0, "ascendancy": 5.0, "notable": 3.0, "jewel socket": 1.0, "small": 0.5,
                 "unique": 1.0, "affix": 2.0, "rune": 2.0, "jewel": 1.5, "corruption": 1.0, "desecrated": 1.5,
                 "exclusive": 0.3, "flask": 1.0, "charm": 1.0, "limb": 0.5,
                 "active gem": 3.0, "support gem": 2.0, "granted skill": 1.0, "hidden": 0.2}
POOLS = {"Item": "affix", "Runes": "rune", "Jewel": "jewel", "Corruption": "corruption", "Desecrated": "desecrated",
         "Exclusive": "exclusive", "Flask": "flask", "Charm": "charm", "IncursionLimb": "limb"}
DISPLAY_STAT = re.compile(r"(^|_)(display|quality_display|visual|cosmetic|hidden|virtual|"
                          r"skill_is_|is_area_damage|base_deal_no_damage|active_skill_base|level_)|"
                          r"_is_gem$|_display_|global_|_art_|_sound|_vfx|show_average_damage|damage_prediction|"
                          r"hit_effect_index", re.I)


def mechanic(text: str) -> str:
    if FLAVOUR.search(text) and not re.search(r"damage|critical|resist|armour|evasion|energy shield|life", text, re.I):
        return "flavour"
    if re.search(r"recover|regenerat|leech|recoup", text, re.I):
        return "recovery"
    if re.search(r"taken|on you\b|you take|^take |prevented|hits against you|enemies hitting you|resistances?\b|"
                 r"armour|evasion|energy shield|block|guard|maximum life|deflect|avoid", text, re.I):
        return "defence"
    for name, pattern in MECHANICS:
        if re.search(pattern, text, re.I):
            return name
    return "flavour"


STAT_BEHAVIOUR = re.compile(r"projectile_(uses|spread|maximum|minimum|distance|ballistic|speed)|spread|angle|offset|"
                            r"animation|channel_|turn_duration|check_for_targets|base_is_projectile|modular|show_|"
                            r"contact_|collide|variation|maintain_|precise_movement|range|action_distance|queue|"
                            r"_delay_ms|spacing|number_of_.*_allowed|cannot_|can_be_|_override$|statset_index|"
                            r"is_|_index|limitation|specifier|movement_speed|stun|knockback|show_|prediction|_distance|"
                            r"repeat_interval|remnant_duration|_debuff_duration_ms|attack_duration_ms|ground_effect_area|"
                            r"has_freeze_cull|radius|area_of_effect", re.I)
STAT_KINDS = (
    ("damage", r"damage|crit|penetrat|exposure|_as_|wither|shock|ignite|poison|bleed|impale|freeze|chill|"
               r"electrocut|cull|additional_projectile|number_of_projectiles|chain|repeat|echo|_final"),
    ("defence", r"armour|evasion|energy_shield|block|resist|damage_taken|maximum_life|ward|guard"),
    ("recovery", r"leech|regen|recover|recoup|life_gain|mana_gain"),
    ("speed", r"speed|cooldown|duration"),
)


def stat_mechanic(stat: str) -> str:
    """What a gem stat is about, from its id: behaviour and display stats (how a projectile flies,
    animation timing) are flavour for the numbers."""
    if DISPLAY_STAT.search(stat) or (STAT_BEHAVIOUR.search(stat) and not re.search(r"damage|crit", stat)):
        return "flavour"
    for name, pattern in STAT_KINDS:
        if re.search(pattern, stat, re.I):
            return name
    return "flavour"


def template(text: str) -> str:
    return NUMBER.sub("#", text).strip()


def passive_kind(gap: dict) -> str:
    if gap.get("ascendancy"):
        return "ascendancy"
    return {"Keystone": "keystone", "Notable": "notable", "Socket": "jewel socket"}.get(gap.get("type"), "small")


def unique_popularity(league: str | None = None) -> dict[str, float]:
    """Source weight per unique from poe.ninja's economy cache (listings and price; never fetched here)."""
    from builder.data.prices import PriceFeed

    root = ROOT / "data" / "prices"
    leagues = sorted((p for p in root.iterdir() if (p / "uniques").is_dir()), key=lambda p: p.stat().st_mtime) \
        if root.exists() else []
    if not leagues:
        return {}
    table = PriceFeed(root).table(league or leagues[-1].name, refresh=False)
    weights = {}
    for name in table.uniques:
        price = table.unique(name)
        listings = (price.listings or 0) if price else 0
        value = (price.divine or 0) if price else 0
        weights[name] = 1.0 + math.log10(1 + listings) + (1.0 if value >= 1 else 0.0)
    return weights


# Statuses -----------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def cached_templates(src: Path = POB_SRC) -> dict[str, bool]:
    """PoB's ModCache (its parse of every tree and unique line it has seen), by number template:
    True if some line of that template parses."""
    found: dict[str, bool] = {}
    path = src / "Data" / "ModCache.lua"
    if path.exists():
        for m in re.finditer(r'^c\["(.*)"\]=\{(.*)\}$', path.read_text(encoding="utf-8"), re.M):
            parsed = not m.group(2).startswith("nil") and m.group(2).endswith(",nil")
            key = template(m.group(1))
            found[key] = found.get(key, False) or parsed
    return found


def ignored_line_status(line: str, on_passive: bool) -> tuple[str, str | None, str]:
    """Status of a line PoB's parser ignores: a ledger translation, a custom model, or nothing."""
    if RANGE_TEXT.search(line) and cached_templates().get(template(line)):
        # A unique's roll range like "+(-40-40)%" fails at its middle roll ("0%"); rolled, PoB parses it
        return "pob", None, "PoB parses the rolled line (only the range text fails)"
    if entry := model_entry(line):
        return "model", entry.id, f"fed to the {entry.raw['model']} model"
    if translated := translate(line):
        entry, lines = translated
        if entry.conditional and not on_passive:
            return "none", entry.id, "ledger entry is conditional, which applies on passives only"
        if not lines and entry.raw.get("absorbed_by"):
            return "ledger", entry.id, "absorbed by an assumption: " + entry.raw["absorbed_by"]
        return "ledger", entry.id, "no effect on what's calculated" if not lines else "translated"
    if condition := resolver_condition(line):
        return "model", f"condition:{condition.var}", f"a source of {condition.label} for the condition resolver"
    return "none", None, "PoB ignores the line"


def resolver_condition(line: str):
    """The condition resolver (builder/search/conditions.py) reads every line of the build as
    evidence, ignored lines included: one that grants Onslaught or a charge switches it on."""
    from builder.search.conditions import CONDITIONS, _PATTERNS

    for condition in CONDITIONS:
        positive, negative = _PATTERNS[condition.var]
        if any(p.search(line) for p in positive) and not any(p.search(line) for p in negative):
            return condition
    return None


def stat_entries() -> dict[str, tuple[str, str]]:
    """Gem stats a ledger entry covers: `stats` on a model entry (fed to the model), or a
    `gem_stat` entry (`no effect on what's calculated` with an empty replace)."""
    covered = {}
    for entry in entries():
        for stat in entry.raw.get("stats") or ():
            covered[stat] = ("model" if entry.raw.get("model") else "ledger", entry.id)
    return covered


def gem_stat_pattern_entry(stat: str, skill: str | None = None):
    for entry in entries():
        if (entry.kind == "gem_stat" and entry.pattern is not None and entry.pattern.search(stat)
                and (skill is None or not entry.raw.get("skill") or skill in (
                    [entry.raw["skill"]] if isinstance(entry.raw["skill"], str) else entry.raw["skill"]))):
            return entry
    return None


def audit(data_dir: Path | None = None, league: str | None = None) -> dict:
    from builder.data import data_dir as default_dir

    data_dir = data_dir or default_dir()
    gaps = json.loads((data_dir / "gaps.json").read_text(encoding="utf-8"))
    coverage_path = data_dir / "coverage.json"
    raw = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else None
    reads = calc_reads()
    popularity = unique_popularity(league)
    records: list[dict] = []

    def add(category, source, kind, text, status, entry, reason, weight, group=None, **extra):
        records.append({"category": category, "source": source, "source_kind": kind, "line": text, "status": status,
                        "entry": entry, "reason": reason, "mechanic": extra.pop("mechanic", None) or mechanic(text),
                        "weight": weight, "template": template(text), "group": group or source, **extra})

    # a. lines PoB admits it ignores
    for gap in gaps["passives"]:
        kind = passive_kind(gap)
        status, entry, reason = ignored_line_status(gap["line"], True)
        add("passive", gap["node"], kind, gap["line"], status, entry, reason, SOURCE_WEIGHT[kind], node_id=gap["id"],
            ascendancy=gap.get("ascendancy"))
    for gap in gaps["uniques"]:
        status, entry, reason = ignored_line_status(gap["line"], False)
        add("unique", gap["unique"], "unique", gap["line"], status, entry, reason,
            popularity.get(gap["unique"], SOURCE_WEIGHT["unique"]))

    if raw:
        # b. affixes, runes, jewel mods
        for affix in raw["affixes"]:
            kind = POOLS.get(affix["pool"], "affix")
            text = affix["line"]
            if not text:
                continue
            if affix["supported"]:
                missing = unread(affix.get("names") or [], reads)
                status, entry, reason = ("none", None, "parsed, but no calculation reads " + ", ".join(missing)) \
                    if missing else ("pob", None, "")
                category = "affix-noop" if missing else "affix"
            else:
                category = "affix"
                status, entry, reason = ignored_line_status(text, False)
                if status == "ledger":  # affix lines aren't corrected in items yet; the entry only labels them
                    reason += " (labelled; affix corrections not applied to items yet)"
            group = f"{affix['pool']}:{re.sub(r'\d+$', '', affix['id'])}"
            add(category, f"{affix['pool']} {affix['id']}" + (f" ({affix['slot']})" if affix.get("slot") else ""),
                kind, text, status, entry, reason, SOURCE_WEIGHT[kind], group=group)
        # c. gem stats
        covered = stat_entries()
        for skill in raw["skills"]:
            gems = skill.get("gems") or []
            if isinstance(gems, dict):
                gems = list(gems.values())
            if not gems and not skill["id"].endswith(("Player", "PlayerTwo", "PlayerThree")):
                continue  # a monster's skill
            kind = ("support gem" if skill.get("support") else "active gem") if gems else \
                "hidden" if skill.get("hidden") else "granted skill"
            source = f"{skill['name']} ({skill['id']})"
            for set_ in skill.get("sets") or []:
                stats = set_.get("stats") or []
                if isinstance(stats, dict):
                    stats = list(stats.values())
                for stat in stats:
                    name = stat["stat"]
                    names = stat.get("names") or []
                    if name in covered:
                        status, entry = covered[name]
                        reason = "fed to a custom model" if status == "model" else "ledger entry"
                    elif found := gem_stat_pattern_entry(name, skill["name"]):
                        status, entry = "ledger", found.id
                        reason = "translated" if found.replace else "no effect on what's calculated"
                    elif not stat.get("mapped"):
                        status, entry, reason = "none", None, "PoB never reads this stat"
                    elif not names:
                        status, entry, reason = "none", None, "PoB maps the stat to nothing"
                    elif missing := unread(names, reads):
                        status, entry, reason = "none", None, "mapped, but no calculation reads " + ", ".join(missing)
                    else:
                        status, entry, reason = "pob", None, ""
                    add("gem-stat", source, kind, name, status, entry, reason, SOURCE_WEIGHT[kind],
                        group=skill["id"], mechanic=stat_mechanic(name), stat_set=set_.get("label"), gems=gems)
        # d. passive and unique lines PoB parses into names nothing reads
        for line in raw["passives"]:
            missing = unread(line.get("names") or [], reads)
            if missing:
                kind = passive_kind(line)
                add("passive-noop", line["node"], kind, line["line"], "none", None,
                    "parsed, but no calculation reads " + ", ".join(missing), SOURCE_WEIGHT[kind], node_id=line["id"])
        for line in raw["uniques"]:
            missing = unread(line.get("names") or [], reads)
            if missing:
                add("unique-noop", line["unique"], "unique", line["line"], "none", None,
                    "parsed, but no calculation reads " + ", ".join(missing),
                    popularity.get(line["unique"], SOURCE_WEIGHT["unique"]))
        # unique lines of variants the item parse didn't select, and Timeless jewels' passives: the same checks
        for line in raw.get("unique_variants", []):
            weight = popularity.get(line["unique"], SOURCE_WEIGHT["unique"])
            if not line["supported"]:
                status, entry, reason = ignored_line_status(line["line"], False)
                add("unique", line["unique"], "unique", line["line"], status, entry, reason, weight, variant=line.get("variants"))
            elif missing := unread(line.get("names") or [], reads):
                add("unique-noop", line["unique"], "unique", line["line"], "none", None,
                    "parsed, but no calculation reads " + ", ".join(missing), weight)
        for line in raw.get("legion", []):
            kind = "notable" if line.get("notable") or line.get("kind") == "nodes" else "small"
            if not line["supported"]:
                status, entry, reason = ignored_line_status(line["line"], False)
                add("timeless", line["node"], kind, line["line"], status, entry,
                    reason + " (a Timeless jewel's passive)", SOURCE_WEIGHT[kind] * 0.5)
            elif missing := unread(line.get("names") or [], reads):
                add("timeless-noop", line["node"], kind, line["line"], "none", None,
                    "parsed, but no calculation reads " + ", ".join(missing), SOURCE_WEIGHT[kind] * 0.5)
        # f. parsed lines whose condition nothing can switch on; g. parsed lines whose parse lost something
        setters = parse_audit.Setters(raw)
        parsed = ([("passive", l, passive_kind(l), SOURCE_WEIGHT[passive_kind(l)], l["node"]) for l in raw["passives"]]
                  + [("unique", l, "unique", popularity.get(l["unique"], 1.0), l["unique"])
                     for l in [*raw["uniques"], *[v for v in raw.get("unique_variants", []) if v["supported"]]]]
                  + [("affix", l, POOLS.get(l["pool"], "affix"), SOURCE_WEIGHT[POOLS.get(l["pool"], "affix")],
                      f"{l['pool']} {l['id']}") for l in raw["affixes"] if l["supported"]]
                  + [("timeless", l, "small", 0.5, l["node"]) for l in raw.get("legion", []) if l["supported"]])
        for source_type, line, kind, weight, source in parsed:
            mods = line.get("mods") or []
            if blocked := parse_audit.never_on(mods, setters):
                add("never-on", source, kind, line["line"], "none", None,
                    "parsed, but its condition can't switch on: "
                    + "; ".join(f"{need} ({detail})" for need, _, detail in blocked), weight, of=source_type,
                    needs=[need for need, _, _ in blocked], config_only=all(st == "config-off" for _, st, _ in blocked))
            for finding in parse_audit.lost(line["line"], mods):
                add("misparse", source, kind, line["line"], "none", None,
                    f"PoB counts it, but its parse looks wrong ({finding['kind']}: {finding['detail']})",
                    weight * (1.5 if finding["inflates"] else 1.0), of=source_type, finding=finding["kind"],
                    inflates=finding["inflates"])
    # e. skills that calculate 0 alone on a plain character (data/pob-*/skill_zero.json, scratch/zero_skills.py)
    for skill in zero_skills(data_dir):
        add("skill-zero", skill["name"], "active gem", skill["name"], skill["status"], skill.get("entry"), skill["reason"],
            SOURCE_WEIGHT["active gem"], mechanic="damage")

    return {"data_dir": str(data_dir), "bridge_part": bool(raw), "records": records,
            "summary": summarize(records), "ranking": rank(records)}


# Skills our own models give damage, or an assumption gives a trigger rate (builder/search/models.py, assumptions.py)
MODELED_SKILLS = {"Iron Ward": "iron-ward-store", "Elemental Expression": "elemental-expression-trigger",
                  "Transmutation Explosion": "assumption:remnants"}
DAMAGE_STATS = ("CombinedDPS", "TotalDPS", "TotalDotDPS", "FullDPS", "AverageDamage")


def zero_skills(data_dir: Path) -> list[dict]:
    """Audit item (e): active skills that calculate no damage alone on a plain level-90 character (bridge auditSkills, in
    coverage.json). Attacks need a weapon and buffs, auras, curses and minions deal none of their own (minion damage isn't read
    here), so only the others that deal damage are listed: a damaging spell or totem that calculates 0 is a gap."""
    path = data_dir / "coverage.json"
    if not path.exists():
        return []
    sweep = json.loads(path.read_text(encoding="utf-8")).get("skill_sweep") or []
    found = []
    for skill in sweep:
        if skill.get("error"):
            found.append({"name": skill["name"], "status": "none", "reason": "PoB failed to calculate it: " + skill["error"][:120]})
            continue
        stats, types = skill.get("stats") or {}, set(skill.get("types") or [])
        if any((stats.get(k) or 0) > 0 for k in DAMAGE_STATS):
            continue
        if "Attack" in types or types & {"Minion", "CreatesMinion", "Aura", "Buff", "Persistent", "HasReservation", "Meta"}                 or not types & {"Damage", "DamageOverTime", "Projectile", "AreaSpell", "Nova", "SummonsTotem", "SummonsAttackTotem"}:
            continue
        if entry := MODELED_SKILLS.get(skill["name"]):
            found.append({"name": skill["name"], "status": "model", "entry": entry,
                          "reason": "0 in PoB alone; a custom model or assumption gives it damage"})
        else:
            found.append({"name": skill["name"], "status": "none",
                          "reason": "a damaging skill that calculates 0 on its own in PoB (types: %s)" % ", ".join(sorted(types)[:8])})
    return found


def summarize(records: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for record in records:
        row = summary.setdefault(record["category"], {s: 0 for s in STATUSES})
        row[record["status"]] += 1
        if record["status"] == "ledger" and record["reason"].startswith("no effect"):
            row["ledger_no_effect"] = row.get("ledger_no_effect", 0) + 1
        if record["status"] == "none":
            by_kind = row.setdefault("none_by_source_kind", Counter())
            by_kind[record["source_kind"]] += 1
            by_mech = row.setdefault("none_by_mechanic", Counter())
            by_mech[record["mechanic"]] += 1
    for row in summary.values():
        for key in ("none_by_source_kind", "none_by_mechanic"):
            if key in row:
                row[key] = dict(row[key].most_common())
    return summary


def rank(records: list[dict], top: int | None = None) -> list[dict]:
    """What isn't counted, grouped by line template and ranked by impact."""
    groups: dict[tuple, dict] = {}
    for record in records:
        if record["status"] != "none":
            continue
        key = (record["category"].split("-")[0] if record["category"] != "gem-stat" else "gem-stat", record["template"])
        group = groups.setdefault(key, {"category": record["category"], "template": record["template"],
                                        "mechanic": record["mechanic"], "sources": {}, "reason": record["reason"],
                                        "example": record["line"]})
        group["sources"][record["group"]] = max(group["sources"].get(record["group"], 0), record["weight"])
        group.setdefault("source_kinds", Counter())[record["source_kind"]] += 1
        group.setdefault("names", [])
        if len(group["names"]) < 6 and record["source"] not in group["names"]:
            group["names"].append(record["source"])
    ranked = []
    for group in groups.values():
        weights = sorted(group["sources"].values(), reverse=True)
        # the strongest source counts in full, each further source a little less
        spread = sum(w / (1 + 0.5 * i) for i, w in enumerate(weights))
        score = MECHANIC_WEIGHT[group["mechanic"]] * spread
        ranked.append({"score": round(score, 2), "category": group["category"], "mechanic": group["mechanic"],
                       "template": group["template"], "sources": len(weights),
                       "source_kinds": dict(group["source_kinds"]), "examples": group["names"],
                       "reason": group["reason"], "example": group["example"]})
    ranked.sort(key=lambda g: -g["score"])
    return ranked[:top] if top else ranked


def print_summary(report: dict, top: int = 40) -> None:
    print(f"coverage audit of {report['data_dir']}" + ("" if report["bridge_part"] else
          " (no coverage.json: run with --refresh for affixes, gem stats and no-ops)"))
    print(f"{'category':14} {'pob':>7} {'ledger':>7} {'(no eff.)':>9} {'model':>7} {'none':>7}")
    totals = Counter()
    for category, row in report["summary"].items():
        print(f"{category:14} {row['pob']:7} {row['ledger']:7} {row.get('ledger_no_effect', 0):9} {row['model']:7} "
              f"{row['none']:7}")
        totals.update({s: row[s] for s in STATUSES})
        totals["no_effect"] += row.get("ledger_no_effect", 0)
    print(f"{'total':14} {totals['pob']:7} {totals['ledger']:7} {totals['no_effect']:9} {totals['model']:7} "
          f"{totals['none']:7}")
    print("(ledger includes entries recording no effect on what's calculated; model includes the condition resolver)")
    for category, row in report["summary"].items():
        if row.get("none_by_source_kind"):
            print(f"  {category} not counted by source: {row['none_by_source_kind']}")
            print(f"  {category} not counted by mechanic: {row['none_by_mechanic']}")
    print(f"\ntop {top} not counted, by impact:")
    for i, group in enumerate(report["ranking"][:top], 1):
        print(f"{i:3}. [{group['score']:6.1f}] {group['category']:12} {group['mechanic']:8} {group['template'][:90]}"
              f"  ({group['sources']} sources: {', '.join(group['examples'][:3])})")


AREAS = (
    ("A. Keystones and ascendancy passives PoB ignores",
     lambda g: g["category"] == "passive" and bool(set(g["source_kinds"]) & {"keystone", "ascendancy"})),
    ("B. Notables and small passives PoB ignores", lambda g: g["category"] == "passive"),
    ("C. Uniques PoB ignores (popular first, by poe.ninja listings)", lambda g: g["category"] == "unique"),
    ("D. Gem stats PoB never reads", lambda g: g["category"] == "gem-stat"),
    ("E. Affixes, runes and jewels PoB ignores", lambda g: g["category"] == "affix"),
    ("F. Lines whose condition never switches on (item f)", lambda g: g["category"] == "never-on"),
    ("G. Lines PoB parses but lost part of (item g; check each by hand)", lambda g: g["category"] == "misparse"),
    ("H. Parsed into nothing a calculation reads", lambda g: g["category"].endswith("-noop")),
    ("I. Damaging skills that calculate 0 alone (item e)", lambda g: g["category"] == "skill-zero"),
    ("J. Timeless jewel passives", lambda g: g["category"].startswith("timeless")),
)


def areas_markdown(report: dict, top: int = 25) -> str:
    """The ranked open list split by area (each can go to a different worker), flavour left out."""
    used, out = set(), ["# Coverage audit: what nothing counts, by area", "",
                        "Score = mechanic weight x sources, strongest first. "
                        "From `python -m builder.ledger.coverage --areas FILE`.", ""]
    for title, test in AREAS:
        rows = [(i, g) for i, g in enumerate(report["ranking"]) if i not in used and g["mechanic"] != "flavour" and test(g)]
        used.update(i for i, _ in rows)
        out.append(f"## {title} ({len(rows)} open)")
        out += [f"- [{g['score']:.1f}] {g['mechanic']}: `{g['template'][:110]}` ({g['sources']} sources: "
                f"{', '.join(g['examples'][:3])})" for _, g in rows[:top]]
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    from builder.data import data_dir

    if "--refresh" in argv:
        from builder.data import dump_coverage

        dump_coverage()
    report = audit()
    out = data_dir() / "coverage-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print_summary(report, top=int(argv[argv.index("--top") + 1]) if "--top" in argv else 40)
    print(f"\nwrote {out}")
    if "--areas" in argv:
        areas = Path(argv[argv.index("--areas") + 1])
        areas.write_text(areas_markdown(report), encoding="utf-8")
        print(f"wrote {areas}")


if __name__ == "__main__":
    main(sys.argv[1:])


# The blind spots of one build (the result's "not modeled" list) --------------------------------

@lru_cache(maxsize=2)
def _build_index(data_dir: str) -> dict:
    """From the bridge part of the audit: the templates of lines PoB parses into names nothing
    reads, and each gem's stats PoB never reads (damage, defence, recovery or speed ones)."""
    path = Path(data_dir) / "coverage.json"
    if not path.exists():
        return {"noops": {}, "gems": {}, "mods": {}, "setters": None}
    raw = json.loads(path.read_text(encoding="utf-8"))
    reads = calc_reads()
    noops: dict[str, list[str]] = {}
    mods: dict[str, list[dict]] = {}  # template -> a parse of it, for the build's own condition checks
    for line in [*raw["passives"], *raw["uniques"], *raw["affixes"], *raw.get("unique_variants", []), *raw.get("legion", [])]:
        if line.get("supported", True) and (missing := unread(line.get("names") or [], reads)):
            noops.setdefault(template(line["line"]), missing)
        if line.get("supported", True) and line.get("mods"):
            mods.setdefault(template(line["line"]), line["mods"])
    covered = stat_entries()
    gems: dict[str, list[tuple[str, str]]] = {}
    for skill in raw["skills"]:
        names = skill.get("gems") or []
        names = list(names.values()) if isinstance(names, dict) else names
        for set_ in skill.get("sets") or []:
            stats = set_.get("stats") or []
            for stat in list(stats.values()) if isinstance(stats, dict) else stats:
                name = stat["stat"]
                if stat_mechanic(name) == "flavour" or name in covered or gem_stat_pattern_entry(name, skill["name"]):
                    continue
                if stat.get("mapped") and stat.get("names") and not unread(stat["names"], reads):
                    continue
                reason = "PoB never reads this gem stat" if not stat.get("mapped") else \
                    "PoB maps this gem stat to nothing a calculation reads"
                for gem in names:
                    entry = gems.setdefault(gem, [])
                    if (name, reason) not in entry:
                        entry.append((name, reason))
    return {"noops": noops, "gems": gems, "mods": mods, "setters": parse_audit.Setters(raw)}


def _off_in_build(mods: list[dict], setters, own: set[str], config_on: set[str]) -> list[str]:
    """For a line of a build: its needs this build doesn't meet, when every modifier of the line has one."""
    off_all, checked = [], 0
    for mod in mods:
        needs = [n for n in parse_audit.needs([mod]) if "{" not in n]
        if not needs:
            return []  # a part of the line counts unconditionally
        checked += 1
        off = []
        for need in needs:
            status, detail = setters.status(need)
            if status == "calc":
                continue  # PoB decides it from the build itself
            if any(alt in own for alt in setters.alternatives(need)):
                continue  # a line of this build sets it
            if any(o in config_on or setters.options[o]["default"] for o in setters.options_for(need)):
                continue  # a Configuration option that's on for this build
            off.append(f"{need.split(':')[-1]} is on (" + ("nothing in this build sets it" if status in ("line", "never")
                                                           else detail.replace("which the optimizer turns on",
                                                                               "off for this build")) + ")")
        if not off:
            return []
        off_all += [o for o in off if o not in off_all]
    return off_all if checked else []


def build_blind_spots(source_lines: list[dict], gem_names: list[str], data_dir: Path | None = None,
                      config_on: set[str] | None = None, stats: dict | None = None) -> list[dict]:
    """What a build has that nothing counts beyond the lines PoB's parser ignores (bridge
    `unsupportedInBuild`): lines PoB parses into modifiers no calculation reads; lines whose condition is off in this
    build (no line of it sets the condition, or only a Configuration option the optimizer has off: Guided Hand's
    "Ancestrally Boosted Attacks" without a source of Ancestral Boost); and stats of its gems PoB never reads. Each comes
    with the reason, for the result's "not modeled" list. config_on: the Configuration options on for this build (the
    condition resolver's)."""
    from builder.data import data_dir as default_dir

    index = _build_index(str(data_dir or default_dir()))
    found = []
    build_mods = [{"mods": index["mods"].get(template(line["text"])) or []} for line in source_lines]
    own = parse_audit.line_setters(build_mods)
    setters = index.get("setters")
    for line in source_lines:
        if not line["source"].startswith(("passive:", "item:")):
            continue
        where = line["source"].replace(":", ": ", 1)
        if missing := index["noops"].get(template(line["text"])):
            found.append({"source": where, "line": line["text"], "kind": "no-op",
                          "reason": "PoB parses this line, but no calculation reads " + ", ".join(missing)})
        elif (need := parse_audit.gated(index["mods"].get(template(line["text"])) or [], stats or {})) and stats is not None:
            found.append({"source": where, "line": line["text"], "kind": "condition off",
                          "reason": f"PoB counts this line only while {need}; this build has none"})
        elif setters is not None and (off := _off_in_build(index["mods"].get(template(line["text"])) or [], setters, own,
                                                             config_on or set())):
            found.append({"source": where, "line": line["text"], "kind": "condition off",
                          "reason": "PoB counts this line only while " + "; ".join(off)})
    for gem in dict.fromkeys(gem_names):
        for stat, reason in index["gems"].get(gem, []):
            found.append({"source": f"gem: {gem}", "line": stat, "kind": "gem stat", "reason": reason})
    return found
