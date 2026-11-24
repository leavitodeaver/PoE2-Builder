"""The Lua that adds the 1.0 preview gems (gems.json) to PoB's data, as engine/bridge.lua adds Untether (COV34): each
skill set up as PoB's Data.lua sets up the skills it loads (929-965) and each gem as its setupGem does (980-1052).

Levels: a gem is revealed at one level (model.shown_level). Damage and cost at the other levels follow the curve
reference's (gems.json curve_reference, a 0.5.5 attack's, LOW): value(L) = shown value x ref(L) / ref(shown). Level
requirements and each stat set's actorLevel / effectiveness come from the reference too. Attack speed is a constant
(poe2db's "Attack Speed: N% of base" is PoB's attackSpeedMultiplier N - 100, as Boneshatter's -40 is the game's 60%).
"""

from __future__ import annotations

import json
from functools import cache

from builder.preview import _gems_file, base_rows, gem_rows
from builder.preview.readings import payoff_stage, stage_damage


def lua_value(value) -> str:
    """A Python value (dict / list / str / number / bool) as a Lua literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # JSON string escapes are valid Lua escapes for this text (no \u past ASCII used)
    if isinstance(value, dict):
        return "{ " + " ".join(f"[{lua_value(k)}] = {lua_value(v)}," for k, v in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "{ " + " ".join(f"{lua_value(v)}," for v in value) + " }"
    raise TypeError(f"no Lua literal for {value!r}")


INSTALLER = r"""
if not PREVIEW_1_0_INSTALLED then
	local refName = %(ref)s
	local ref = data.skills[refName]
	if not ref then
		error("preview_1_0: the curve reference skill " .. refName .. " is missing from PoB's data")
	end
	local refSet = ref.statSets[1]
	-- (PREVIEW3) "skills that enter Stealth" (Conceal's requirement): PoB has no such skill type, so the preview adds one
	-- past PoB's last number, carried only by preview skills that enter Stealth (gems.json: Shroud)
	if not SkillType.PreviewStealth then
		local last = 0
		for _, number in pairs(SkillType) do
			if type(number) == "number" and number > last then
				last = number
			end
		end
		SkillType.PreviewStealth = last + 1
	end
	local function setUp(skill, id)
		-- as Data.lua 929-965 sets up every skill it loads
		skill.id = id
		skill.modSource = "Skill:" .. id
		skill.statSets = skill.statSets or { }
		for _, statSet in pairs(tableConcat({ skill }, skill.statSets)) do
			statSet.statMap = statSet.statMap or { }
			setmetatable(statSet.statMap, data.skillStatMapMeta)
			statSet.statMap._grantedEffect = skill
		end
		data.skills[id] = skill
	end
	local function addGem(row, skill)
		-- as Data.lua's setupGem
		local gem = {
			name = row.name, baseTypeName = row.name, gameId = row.id, variantId = row.grantedEffectId,
			grantedEffectId = row.grantedEffectId, tags = row.tags, gemType = row.gemType, tagString = row.tagString,
			weaponRequirements = row.weaponRequirements, reqStr = row.reqStr, reqDex = row.reqDex, reqInt = row.reqInt,
			Tier = row.tier, naturalMaxLevel = row.support and 1 or 20, preview = true,
		}
		gem.id = row.id
		gem.grantedEffect = skill
		gem.additionalGrantedEffects = { }
		gem.grantedEffectList = { skill }
		data.gems[row.id] = gem
		data.gemForSkill[skill] = row.id
		data.gemsByGameId[gem.gameId] = data.gemsByGameId[gem.gameId] or { }
		data.gemsByGameId[gem.gameId][gem.variantId] = gem
		data.gemNameForModSource[skill.modSource] = gem.name
		local baseName = row.support and (row.name .. " Support") or row.name
		data.gemForBaseName[baseName:lower()] = row.id
		data.gemForBaseName[row.name:lower()] = data.gemForBaseName[row.name:lower()] or row.id
	end
	local function types(list)
		local out = { }
		for _, name in ipairs(list) do
			if not SkillType[name] then
				error("preview_1_0: no SkillType " .. name)
			end
			out[SkillType[name]] = true
		end
		return out
	end
	local function keyset(list)
		local out = { }
		for _, name in ipairs(list) do
			out[name] = true
		end
		return out
	end
	local function addActive(row)
		local m = row.model
		local shown = ref.levels[m.shown_level]
		local levels, setLevels = { }, { }
		for level = 1, 40 do
			local r = ref.levels[level]
			local cost = math.max(1, math.floor(m.mana * r.cost.Mana / shown.cost.Mana + 0.5))
			levels[level] = {
				baseMultiplier = m.damage_pct / 100 * r.baseMultiplier / shown.baseMultiplier,
				attackSpeedMultiplier = m.attack_speed_pct and (m.attack_speed_pct - 100) or nil,
				levelRequirement = r.levelRequirement,
				cost = { Mana = cost },
				cooldown = m.cooldown, storedUses = m.storedUses,
			}
			setLevels[level] = { actorLevel = refSet.levels[level].actorLevel }
		end
		local skill = {
			name = row.name, baseTypeName = row.name, color = row.color, description = row.description,
			skillTypes = types(row.skillTypes), weaponTypes = keyset(row.weaponTypes or { }),
			castTime = m.attack_time_ms and (m.attack_time_ms / 1000) or 1, preview = true,
			qualityStats = { }, levels = levels,
			statSets = {
				[1] = {
					label = row.name,
					incrementalEffectiveness = refSet.incrementalEffectiveness,
					statDescriptionScope = "skill_stat_descriptions",
					baseFlags = keyset(m.baseFlags),
					constantStats = m.constantStats,
					stats = m.stats,
					levels = setLevels,
				},
			},
		}
		setUp(skill, row.grantedEffectId)
		addGem(row, skill)
	end
	local function addSupport(row)
		local m = row.model
		local skill = {
			name = row.name, description = row.description, color = row.color, support = true, preview = true,
			requireSkillTypes = (function() local t = { } for _, n in ipairs(m.requireSkillTypes) do table.insert(t, SkillType[n]) end return t end)(),
			addSkillTypes = { }, excludeSkillTypes = { }, gemFamily = row.families,
			levels = { [1] = { levelRequirement = 0, manaMultiplier = m.manaMultiplier }, },
			statSets = {
				[1] = {
					label = row.name,
					incrementalEffectiveness = refSet.incrementalEffectiveness,
					statDescriptionScope = "gem_stat_descriptions",
					baseFlags = { },
					constantStats = m.constantStats,
					stats = { },
					levels = { [1] = { actorLevel = 1 }, },
				},
			},
		}
		setUp(skill, row.grantedEffectId)
		addGem(row, skill)
	end
	for _, row in ipairs(%(rows)s) do
		-- a gem PoB already has by that name wins (release day: PoB's real 1.0 data replaces the preview row)
		local taken = data.gemForBaseName[(row.support and (row.name .. " support") or row.name):lower()]
		if not data.skills[row.grantedEffectId] and not taken then
			if row.support then
				addSupport(row)
			else
				addActive(row)
			end
		end
	end
	for _, row in ipairs(%(bases)s) do
		-- a base PoB already has wins (release day); else a copy of its nearest base with the card's numbers
		if not data.itemBases[row.name] then
			local like = data.itemBases[row.like]
			if not like then
				error("preview_1_0: no base " .. row.like)
			end
			local base = copyTable(like)
			base.implicit = nil
			base.req = row.req
			if row.weapon then
				for key, value in pairs(row.weapon) do
					base.weapon[key] = value
				end
			end
			data.itemBases[row.name] = base
		end
	end
	PREVIEW_1_0_INSTALLED = true
end
"""

COLORS = {"str": 1, "dex": 2, "int": 3}


def _lua_row(row: dict) -> dict:
    out = {k: row[k] for k in ("name", "grantedEffectId", "id", "gemType", "support", "tagString", "skillTypes",
                               "reqStr", "reqDex", "reqInt", "tier", "description") if k in row}
    out["tags"] = {tag: True for tag in row["tags"]}
    if row.get("weaponTypes"):
        out["weaponTypes"] = row["weaponTypes"]
        out["weaponRequirements"] = ", ".join(row["weaponTypes"])
    if row.get("families"):
        out["families"] = row["families"]
    reqs = {"str": row.get("reqStr", 0), "dex": row.get("reqDex", 0), "int": row.get("reqInt", 0)}
    out["color"] = COLORS[max(reqs, key=reqs.get)]
    model = dict(row["model"])
    held = model.pop("held_stages", None)
    if held:  # a staged channel, as one release at its payoff stage (readings.py): the hold's damage over the hold's time
        stage = payoff_stage(held["maximum"], held["more_per_additional_pct"])
        model["damage_pct"] = model["damage_pct"] * stage_damage(stage, held["more_per_additional_pct"])
        model["attack_time_ms"] = held["attack_time_ms"] * stage
        # (PoB reads a skill's own attack time from castTime under this stat, CalcOffence 2737-2739; level.attackTime is
        # only read on its off-hand path, so a Worldcutter using it scored at the weapon's speed)
        model["stats"] = [*model["stats"], "cast_time_overrides_attack_duration"]
    if "constantStats" in model:
        model["constantStats"] = [list(pair) for pair in model["constantStats"]]
    out["model"] = model
    return out


@cache
def installer() -> str:
    """The Lua chunk that installs every preview gem (idempotent per Lua state)."""
    rows = [_lua_row(row) for row in gem_rows()]
    bases = [{k: base[k] for k in ("name", "like", "req", "weapon") if k in base} for base in base_rows()]
    return INSTALLER % {"ref": lua_value(_gems_file()["curve_reference"]["skill"]), "rows": lua_value(rows),
                        "bases": lua_value(bases)}
