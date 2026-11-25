-- The only code that reaches into Path of Building internals. Everything the optimizer needs
-- from PoB goes through `bridge_dispatch(method, jsonArgs)`, so a PoB upgrade only has to be
-- checked here.
--
-- Two kinds of evaluation:
--   * Commits change the loaded build and recalculate through PoB's normal path
--     (buildFlag + OnFrame), exactly as the GUI does.
--   * Deltas ask PoB's misc calculator "what if" questions (the same calculator behind node
--     power and item/gem comparison tooltips) without changing the build.
local dkjson = require("dkjson")

bridge = { }

-- Correction to PoB's copyTableSafe (Modules/Common.lua), installed at runtime; no PoB file is
-- edited. PoB gives the copy its metatable before filling it in. Class objects keep parent-class
-- proxies that are their own metatables with __newindex pointing at the object, so deep-copying
-- one wrote every proxy field into the ORIGINAL object. Every build load switches attribute
-- nodes through such deep copies of the shared passive tree (PassiveSpec:SwitchAttributeNode), so
-- the shared tree's ModLists grew with each load, sometimes doubling, until loads took seconds
-- and a worker ran out of memory (PLAN.md Phase 2 findings). This version fills the copy with
-- rawset, so the original is never touched; the copy itself is identical.
do
	local subTableMap = { }
	function copyTableSafe(tbl, noRecurse, preserveMeta, isSubTable)
		local out = { }
		if not noRecurse then
			subTableMap[tbl] = out
		end
		if preserveMeta then
			setmetatable(out, getmetatable(tbl))
		end
		for k, v in pairs(tbl) do
			if not noRecurse and type(v) == "table" then
				rawset(out, k, subTableMap[v] or copyTableSafe(v, false, preserveMeta, true))
			else
				rawset(out, k, v)
			end
		end
		if not noRecurse and not isSubTable then
			wipeTable(subTableMap)
		end
		return out
	end
end

-- Correction to PoB's parse of "while a (Rare or) Unique Enemy is in your Presence" and "while a Pinnacle Atlas Boss is
-- in your Presence" (Modules/ModParser.lua): PoB tags them with the enemy's rarity only, so a boss counted as in your
-- Presence at any distance. Such a modifier also needs PoB's own EnemyInPresence condition (CalcPerform: the Presence
-- radius against the Configuration's distance to the enemy, which the condition resolver sets). Added to the modifier
-- itself (not to one nested for an enemy or a minion), on every parse from now on and on the passive trees parsed at
-- startup. Only modifiers already tagged with the enemy's rarity are touched, so another line combined with it isn't.
do
	local function gate(list)
		for _, mod in ipairs(list or { }) do
			local rarity, gated = false, false
			for _, tag in ipairs(mod) do
				if tag.type == "ActorCondition" and tag.actor == "enemy" then
					for _, var in ipairs(tag.varList or { tag.var }) do
						rarity = rarity or var == "RareOrUnique" or var == "PinnacleBoss"
					end
				elseif tag.type == "Condition" and tag.var == "EnemyInPresence" then
					gated = true
				end
			end
			if rarity and not gated then
				mod[#mod + 1] = { type = "Condition", var = "EnemyInPresence" }
			end
		end
	end
	local function bossInPresence(line)
		local lower = type(line) == "string" and line:lower() or ""
		return lower:find("unique enemy is in your presence", 1, true) or lower:find("pinnacle atlas boss is in your presence", 1, true)
	end
	local parse = modLib.parseMod
	modLib.parseMod = function(line, ...)
		local list, extra = parse(line, ...)
		if list and bossInPresence(line) then
			gate(list)
		end
		return list, extra
	end
	for _, tree in pairs(__mainObject__.main.tree or { }) do
		for _, node in pairs(tree.nodes or { }) do
			for i, entry in pairs(node.mods or { }) do
				if node.sd and (bossInPresence(node.sd[i]) or bossInPresence(node.sd[i + 1])) then
					gate(entry.list)
				end
			end
		end
	end
end

-- Electrocute counts as an Ailment and an Elemental Ailment on the enemy (MECH80; PATCH_CHECK L4-3, an UNDERCOUNT where
-- Electrocute is the boss's only ailment). The game: KeywordPopups Ailments "The list of Ailments is: Bleeding, Ignite,
-- Chill, Freeze, Shock, Electrocute, and Poison", ElementalAilments "Ignite, Chill, Freeze, Shock, and Electrocute";
-- patch 0.2.0 "Fixed a bug where conditional effects that looked for ailments on the target didn't count electrocute",
-- 0.2.0e "... Electrocute was not counting towards "per Elemental Ailment on the Enemy"". PoB's lists for "against
-- enemies (that are) affected by (no) (elemental) ailments" and "you and enemies in your presence count as moving while
-- affected by elemental ailments" (ModParser 2154-2157, 2497-2498) leave Electrocuted out; its own "while affected by
-- an ailment" (1917), the per-type lines (3876-3892) and the Armour Break line (4341) have it. A list that names Frozen,
-- Chilled, Shocked and Ignited (a whole elemental-ailment list) gains Electrocuted, on every parse from now on and on
-- the passive trees parsed at startup; lists naming a few (e.g. "frozen, shocked or ignited") are left as written.
do
	local function addElectrocuted(list)
		for _, mod in ipairs(list or { }) do
			local tags = { }
			for _, tag in ipairs(mod) do
				tags[#tags + 1] = tag
			end
			if type(mod.value) == "table" and type(mod.value.mod) == "table" then  -- (an EnemyModifier's inner mod)
				for _, tag in ipairs(mod.value.mod) do
					tags[#tags + 1] = tag
				end
			end
			for _, tag in ipairs(tags) do
				if (tag.type == "ActorCondition" or tag.type == "Condition") and tag.varList then
					local has = { }
					for _, var in ipairs(tag.varList) do
						has[var] = true
					end
					if has.Frozen and has.Chilled and has.Shocked and has.Ignited and not has.Electrocuted then
						local vars = copyTable(tag.varList)
						vars[#vars + 1] = "Electrocuted"
						tag.varList = vars
					end
				end
			end
		end
	end
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		local list, extra = parseBefore(line, ...)
		if list then
			addElectrocuted(list)
		end
		return list, extra
	end
	for _, tree in pairs(__mainObject__.main.tree or { }) do
		for _, node in pairs(tree.nodes or { }) do
			for _, entry in pairs(node.mods or { }) do
				if type(entry) == "table" then
					addElectrocuted(entry.list)
				end
			end
		end
	end
end

-- Lucky spell crits (MECHB10; COV8's tested patch, POE2T2401; RESEARCH3's undercount): PoB parses "N% chance for Spell
-- Damage with Critical Hits to be Lucky" only in part (ModCache: Damage BASE with " for    to be Lucky " left over), so
-- never applies it. As PoB's own LuckyHitsChance on spell hits in the crit pass (CalcOffence 4205 sets
-- Condition:CriticalStrike there; 4259-4269 rolls min/3 + 2max/3 for the Lucky share): Spark L20 at 36% crit, 100% ->
-- x1.1594, the formula's x1.1594. Lands with COV-A's retire of lucky-spell-crits (pob-gaps-boss.yaml).
do
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		local list, extra = parseBefore(line, ...)
		if (not list or extra) and type(line) == "string" then
			local n = line:match("^(%d+)%% chance for Spell Damage with Critical Hits to be Lucky$")
			if n then
				return { modLib.createMod("LuckyHitsChance", "BASE", tonumber(n), "Lucky spell crits", ModFlag.Spell,
					{ type = "Condition", var = "CriticalStrike" }) }, nil
			end
		end
		return list, extra
	end
end

-- Emergent Instinct (MECH90; the coverage audit's never-on row, MECH89's Handover step 3): the helmet rune's "Targets
-- that are Blinded, Maimed, and Bleeding cannot Evade your Hits" (game stat hits_cannot_be_evaded_vs_blinded_maimed_
-- bleeding_enemies). PoB parses it (ModParser 2884) to the enemy's CannotEvade under all three of its conditions
-- (ActorCondition Blinded, Maimed, Bleeding), so with the three boxes on at part shares the search's weighing (each box
-- off alone, the effects taken as independent) took the whole gain off three times (builder/search/conditions.py
-- EMERGENT_VAR's comment). Handed to PoB here under one condition instead: the player's Condition:BlindedMaimedBleeding,
-- read from the enemy's side (actor "enemy" of the enemy's mods is the player), which conditions.py sets
-- (bridgeConditionBlindedMaimedBleeding) at the share of the fight all three hold. Matched by the parsed mod (a
-- CannotEvade enemy modifier whose tags are exactly those three conditions), on every parse from now on and on the passive
-- trees parsed at startup, whatever the source.
do
	local EMERGENT = { Blinded = true, Maimed = true, Bleeding = true }
	local function oneCondition(list)
		for _, mod in ipairs(list or { }) do
			local inner = mod.name == "EnemyModifier" and type(mod.value) == "table" and mod.value.mod
			if type(inner) == "table" and inner.name == "CannotEvade" and #inner == 3 then
				local seen = 0
				for _, tag in ipairs(inner) do
					if tag.type == "ActorCondition" and not tag.actor and EMERGENT[tag.var] then
						seen = seen + 1
					end
				end
				if seen == 3 then
					mod.value = { mod = modLib.createMod("CannotEvade", "FLAG", true,
						{ type = "ActorCondition", actor = "enemy", var = "BlindedMaimedBleeding" }) }
				end
			end
		end
	end
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		local list, extra = parseBefore(line, ...)
		if list then
			oneCondition(list)
		end
		return list, extra
	end
	for _, tree in pairs(__mainObject__.main.tree or { }) do
		for _, node in pairs(tree.nodes or { }) do
			for _, entry in pairs(node.mods or { }) do
				if type(entry) == "table" then
					oneCondition(entry.list)
				end
			end
		end
	end
end

-- Idol of Egrin's bonded "Curse zones erupt after 20% reduced delay" (MECH90; RESEARCH33 R33-9, HIGH: it is the stat
-- base_curse_delay_+%, the curse activation delay, which PoB maps to CurseDelay INC for gems, SkillStatMap 2769-2770, and
-- CalcOffence 1577-1580 reads; ModParser / ModCache have no "erupt after" form, so PoB dropped the item's line, an
-- UNDERCOUNT). Parsed here as that INC (reduced: negative), on every parse from now on; PoB's own bonded-mod rules decide
-- when the line is active. models.curse_uses reads PoB's CurseDelay (the bridge's activeSkills curseDelay).
do
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		local list, extra = parseBefore(line, ...)
		if (not list or extra) and type(line) == "string" then
			local n, way = line:lower():match("^%s*curse zones erupt after (%d+%.?%d*)%% (%a+) delay%s*$")
			if n and (way == "reduced" or way == "increased") then
				return { modLib.createMod("CurseDelay", "INC", (way == "reduced" and -1 or 1) * tonumber(n), "Curse zone delay") }, nil
			end
		end
		return list, extra
	end
end

-- Raw ledger modifiers (COV25; builder/ledger RAW_MOD): a ledger line "mod:<Name> <TYPE> <value>" (a passive's, through
-- applyLedger) or its item form "LedgerMod:<Name> <TYPE> <value>" (ledger.item_lines) is PoB's modifier <Name> of that
-- type and value, for a line no PoB text can carry: Rune of Reach's "Remnants you create have 25% reduced effect" is
-- RemnantEffect INC -25 (ModParser 5628-5629 read only "increased", and "-25% increased" doesn't match their "(%d+)").
do
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		if type(line) == "string" then
			local name, kind, value = line:match("^LedgerMod:([%w:]+)%s+(%u+)%s+(%-?[%d%.]+)$")
			if not name then
				name, kind, value = line:match("^mod:([%w:]+)%s+(%u+)%s+(%-?[%d%.]+)$")
			end
			if name and (kind == "BASE" or kind == "INC" or kind == "MORE" or kind == "FLAG") and tonumber(value) then
				local v = tonumber(value)
				if kind == "FLAG" then
					v = v ~= 0
				end
				return { modLib.createMod(name, kind, v, "Ledger") }, nil
			end
		end
		return parseBefore(line, ...)
	end
end

-- "<a line PoB parses> per Rune Socketed in Equipped Items" (COV42; Emergent Possibility in gloves, MECHB37 item 1): the
-- game wraps it over two lines, which PoB parses apart ("... random Element per" and "Rune Socketed in Equipped
-- Items", each to nothing). The ledger hands the item the joined sentence (items-cov42.yaml); here its first part is
-- parsed as PoB would and counted per Rune, Multiplier RunesInEquipment (setAugmentCounts: Runes only).
do
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		if type(line) == "string" then
			local head = line:match("^(.-)%s+per Rune Socketed in Equipped Items$")
			if head then
				local list, extra = parseBefore(head, ...)
				if list and not extra and #list > 0 then
					for _, mod in ipairs(list) do
						mod[#mod + 1] = { type = "Multiplier", var = "RunesInEquipment", actor = "player" }
					end
					return list, nil
				end
			end
		end
		return parseBefore(line, ...)
	end
end

-- Untether, the skill The Master's Reach grants (COV34; H5-3 part 1): PoB has no skill for it (only the trade site's
-- "Grants Skill: Level # Untether"), so the line parsed to nothing and the gloves granted no skill. It is added here as
-- AbyssalLivingBomb, from the game's own rows (build 26476662): ActiveSkills 826 abyssal_living_bomb ("Untether"; types
-- Spell, Damage, Area, Trappable, Totemable, Mineable, AreaSpell, Limit, Physical, UsableWhileMoving, Duration,
-- Triggerable), GrantedEffects 7733 (cast time 600 ms; GrantedEffectsPerLevel: cost 0 at every level),
-- GrantedEffectStatSets 7538 (base effectiveness 4; duration 6000 ms, radius 21, 5 seeds allowed, explodes at 100% of
-- the Ailment Threshold) and GrantedEffectStatSetsPerLevel (13% crit, spell physical damage per level, resolved values
-- as PoB writes Living Bomb's); quality: +0.2 radius per 1% (GrantedEffectQualityStats 158). poe2db's Untether page
-- gives the same (12-601 to 19-902 Physical, 2.1 m, 6 s, Limit 5, 100%). Shaped like PoB's LivingBombPlayer
-- (act_int.lua 14704), its regular sibling. PoB scores the explosion as a hit when cast; how often seeds explode (the
-- threshold, the 5-seed limit) is MECH-B's model, not PoB's (PoB reads no living_bomb_* stat for Living Bomb either).
-- "Grants Skill: Level N Untether" grants it (ModParser's gemIdLookup is built from data.gems when ModParser loads,
-- before this runs, so the line is matched here).
do
	local base = data.skills.LivingBombPlayer
	if base and not data.skills.AbyssalLivingBomb then
		local levels = { }
		for level = 1, 40 do
			local from = base.levels[level] or { }
			levels[level] = { critChance = 13, levelRequirement = from.levelRequirement or 0, cost = { } }
		end
		local skill = {
			name = "Untether",
			baseTypeName = "Untether",
			icon = "Art/2DArt/SkillIcons/AbyssalLivingBomb.dds",
			fromItem = true,
			color = 3,
			description = "Plant a seed of Abyssal Energy on the enemy's heart. Dealing enough damage to the enemy, or killing it outright, causes their heart to explode, dealing Physical damage in an area.",
			skillTypes = { [SkillType.Spell] = true, [SkillType.Damage] = true, [SkillType.Area] = true, [SkillType.Trappable] = true,
				[SkillType.Totemable] = true, [SkillType.Mineable] = true, [SkillType.AreaSpell] = true, [SkillType.Limit] = true,
				[SkillType.Physical] = true, [SkillType.UsableWhileMoving] = true, [SkillType.Duration] = true, [SkillType.Triggerable] = true, },
			castTime = 0.6,
			qualityStats = {
				{ "active_skill_base_area_of_effect_radius", 0.2, {  } },
			},
			levels = levels,
			statSets = {
				[1] = {
					label = "Untether",
					baseEffectiveness = 4,
					incrementalEffectiveness = 0.17000000178814,
					damageIncrementalEffectiveness = 0.010599999688566,
					statDescriptionScope = "living_bomb_player",
					baseFlags = { spell = true, area = true, duration = true, },
					constantStats = {
						{ "base_skill_effect_duration", 6000 },
						{ "active_skill_base_area_of_effect_radius", 21 },
						{ "living_bomb_base_number_of_bombs_allowed", 5 },
						{ "movement_speed_+%_final_while_performing_action", -70 },
						{ "movement_speed_acceleration_+%_per_second_while_performing_action", 160 },
						{ "movement_speed_while_performing_action_locked_duration_%", 50 },
						{ "living_bomb_damage_threshold_%_of_ailment_threshold", 100 },
					},
					stats = {
						"spell_minimum_base_physical_damage",
						"spell_maximum_base_physical_damage",
						"base_skill_show_average_damage_instead_of_dps",
						"is_area_damage",
						"can_perform_skill_while_moving",
					},
					notMinionStat = {
						"spell_minimum_base_physical_damage",
						"spell_maximum_base_physical_damage",
					},
					levels = {
			[1] = { 12, 19, actorLevel = 1 },
			[2] = { 18, 27, actorLevel = 3.451999903 },
			[3] = { 26, 39, actorLevel = 6.767000198 },
			[4] = { 35, 53, actorLevel = 10.30799961 },
			[5] = { 46, 69, actorLevel = 14.07499981 },
			[6] = { 58, 87, actorLevel = 18.06800079 },
			[7] = { 72, 108, actorLevel = 22.28700066 },
			[8] = { 88, 131, actorLevel = 26.73200035 },
			[9] = { 106, 159, actorLevel = 31.40299988 },
			[10] = { 126, 189, actorLevel = 36.29999924 },
			[11] = { 150, 225, actorLevel = 41.42300034 },
			[12] = { 177, 265, actorLevel = 46.77199936 },
			[13] = { 208, 312, actorLevel = 52.34700012 },
			[14] = { 243, 365, actorLevel = 58.14799881 },
			[15] = { 284, 426, actorLevel = 64.17500305 },
			[16] = { 331, 496, actorLevel = 70.4280014 },
			[17] = { 385, 577, actorLevel = 76.90699768 },
			[18] = { 447, 670, actorLevel = 83.61199951 },
			[19] = { 518, 778, actorLevel = 90.54299927 },
			[20] = { 601, 902, actorLevel = 97.69999695 },
			[21] = { 696, 1045, actorLevel = 105.0830002 },
			[22] = { 807, 1210, actorLevel = 112.6920013 },
			[23] = { 935, 1402, actorLevel = 120.5270004 },
			[24] = { 1083, 1625, actorLevel = 128.5879974 },
			[25] = { 1255, 1883, actorLevel = 136.875 },
			[26] = { 1456, 2184, actorLevel = 145.3880005 },
			[27] = { 1689, 2534, actorLevel = 154.1269989 },
			[28] = { 1961, 2942, actorLevel = 163.0919952 },
			[29] = { 2279, 3419, actorLevel = 172.2830048 },
			[30] = { 2651, 3976, actorLevel = 181.6999969 },
			[31] = { 3086, 4629, actorLevel = 191.3430023 },
			[32] = { 3597, 5395, actorLevel = 201.2120056 },
			[33] = { 4197, 6295, actorLevel = 211.3070068 },
			[34] = { 4903, 7354, actorLevel = 221.628006 },
			[35] = { 5734, 8602, actorLevel = 232.1750031 },
			[36] = { 6716, 10074, actorLevel = 242.947998 },
			[37] = { 7877, 11815, actorLevel = 253.9470062 },
			[38] = { 9251, 13876, actorLevel = 265.1719971 },
			[39] = { 10881, 16321, actorLevel = 276.6229858 },
			[40] = { 12817, 19225, actorLevel = 288.2999878 },
					},
				},
			},
		}
		for level, row in pairs(skill.statSets[1].levels) do
			row.statInterpolation = { 1, 1, }
		end
		-- as Data.lua 929-965 sets up every skill it loads (none of these statSets carries mods to source)
		skill.id = "AbyssalLivingBomb"
		skill.modSource = "Skill:AbyssalLivingBomb"
		for _, statSet in pairs(tableConcat({ skill }, skill.statSets)) do
			statSet.statMap = statSet.statMap or { }
			setmetatable(statSet.statMap, data.skillStatMapMeta)
			statSet.statMap._grantedEffect = skill
		end
		data.skills.AbyssalLivingBomb = skill
		-- Its gem row, as the game has one (SkillGems: BaseItemType Metadata/Items/Gem/SkillGemAbyssalLivingBomb,
		-- "Untether", Intelligence 100%, as Living Bomb's), set up as Data.lua's setupGem does, so PoB scores the
		-- granted skill at the highest level up to 15 whose level and Intelligence requirements the character meets
		-- (CalcSetup getGrantedSkillLevel reads the gem's reqInt; without a gem row it stayed at 15 whatever the
		-- character). Tier 0 and fromItem: no gem list the search offers takes it (Context.gems offers a fromItem skill
		-- only from ITEM_SKILL_GEMS; gems.json is PoB's own dump).
		local gemId = "Metadata/Items/Gems/SkillGemAbyssalLivingBomb"
		if not data.gems[gemId] then
			local gem = {
				name = "Untether", baseTypeName = "Untether", gameId = "Metadata/Items/Gem/SkillGemAbyssalLivingBomb",
				variantId = "AbyssalLivingBomb", grantedEffectId = "AbyssalLivingBomb",
				tags = { intelligence = true, grants_active_skill = true, spell = true, area = true, physical = true, duration = true, },
				gemType = "Spell", tagString = "AoE, Physical, Duration", reqStr = 0, reqDex = 0, reqInt = 100, Tier = 0,
				naturalMaxLevel = 20,
			}
			gem.id = gemId
			gem.grantedEffect = skill
			gem.additionalGrantedEffects = { }
			gem.grantedEffectList = { skill }
			data.gems[gemId] = gem
			data.gemForSkill[skill] = gemId
			data.gemsByGameId[gem.gameId] = data.gemsByGameId[gem.gameId] or { }
			data.gemsByGameId[gem.gameId][gem.variantId] = gem
			data.gemNameForModSource[skill.modSource] = gem.name
			data.gemForBaseName["untether"] = gemId
		end
	end
	local parseBefore = modLib.parseMod
	modLib.parseMod = function(line, ...)
		if type(line) == "string" and data.skills.AbyssalLivingBomb then
			local level = line:match("^[Gg]rants [Ss]kill: [Ll]evel (%d+) [Uu]ntether$")
			if level then
				return { modLib.createMod("ExtraSkill", "LIST", { skillId = "AbyssalLivingBomb", level = tonumber(level) }) }, nil
			end
		end
		return parseBefore(line, ...)
	end
end

-- Empowered Sparks II (GEM28; RESEARCH36 R36-16: obtainable in 0.5, MEDIUM-HIGH: SkillGems 1101, CraftingTypes [11] as
-- I's, CraftingLevel 4, poe2db "From: Uncut Support Gem", Tier 4, no legacy marker): PoB has no such gem (checked: PoB
-- src Data/Gems.lua and Data/Skills/*.lua hold "Empowered Sparks I" only; the game's BaseItemTypes 5034
-- Metadata/Items/Gems/SupportGemEmpoweredSparksTwo "Empowered Sparks II"). The game's rows (build 26650308): GemEffects
-- 1103 EmpoweredSparksSupportTwo, the same support effect as I's (GrantedEffects 1138 SupportEmpoweredSparksPlayer, its
-- text and stat skill_has_empowered_spark_support) and the same Empower skill (512 TriggeredSparkEmpowerPlayer), with
-- its own Spark, 514 TriggeredEmpoweredSparkPlayerTwo: GrantedEffectStatSets 649 against I's 651 the same constants and
-- effectiveness, and GrantedEffectStatSetsPerLevel the same damage at every level 1-40, base_number_of_projectiles 2
-- higher at every level (6 vs 4 at level 1, 11 vs 9 at 20, 15 vs 13 at 40; checked level by level). Added here as I's
-- rows with that one change; the support gets an id of its own (SupportEmpoweredSparksPlayerTwo, a copy of I's)
-- because PoB keys a gem by its granted effect (Data.lua setupGem: gemForSkill, gemNameForModSource). PoB scores a
-- triggered Spark as one hit whatever its projectile count (CalcOffence reads ProjectileCount for output only), so II
-- scores as I in PoB; how many sparks reach the boss is the Empowered Sparks model's row (models.empowered_sparks_model).
-- Tier 4 (PoB's Tier is the game's CraftingLevel: Minion Pact I / II 1 / 4, Empowered Sparks I 2).
do
	local support, spark = data.skills.SupportEmpoweredSparksPlayer, data.skills.TriggeredEmpoweredSparkPlayer
	local gemId = "Metadata/Items/Gems/SkillGemEmpoweredSparksSupportTwo"
	local function copySkill(from, id, name)
		local skill = { }
		for k, v in pairs(from) do
			skill[k] = v
		end
		skill.name = name or from.name
		skill.statSets = { }
		for i, set in ipairs(from.statSets or { }) do
			local copy = { }
			for k, v in pairs(set) do
				copy[k] = v
			end
			copy.levels = { }
			for level, row in pairs(set.levels or { }) do
				local r = { }
				for k, v in pairs(row) do
					r[k] = v
				end
				copy.levels[level] = r
			end
			copy.statMap = { }
			for k, v in pairs(set.statMap or { }) do
				if k ~= "_grantedEffect" then
					copy.statMap[k] = v
				end
			end
			skill.statSets[i] = copy
		end
		skill.statMap = { }
		for k, v in pairs(from.statMap or { }) do
			if k ~= "_grantedEffect" then
				skill.statMap[k] = v
			end
		end
		-- as Data.lua 929-965 sets up every skill it loads
		skill.id = id
		skill.modSource = "Skill:" .. id
		for _, statSet in pairs(tableConcat({ skill }, skill.statSets)) do
			setmetatable(statSet.statMap, data.skillStatMapMeta)
			statSet.statMap._grantedEffect = skill
		end
		data.skills[id] = skill
		return skill
	end
	if support and spark and data.skills.TriggeredSparkEmpowerPlayer and not data.gems[gemId]
			and not data.skills.SupportEmpoweredSparksPlayerTwo then
		local supportTwo = copySkill(support, "SupportEmpoweredSparksPlayerTwo", "Empowered Sparks II")
		if supportTwo.statSets[1] then
			supportTwo.statSets[1].label = "Empowered Sparks II"
		end
		local sparkTwo = copySkill(spark, "TriggeredEmpoweredSparkPlayerTwo")
		for _, set in ipairs(sparkTwo.statSets) do
			local at
			for i, stat in ipairs(set.stats or { }) do
				if stat == "base_number_of_projectiles" then
					at = i
				end
			end
			if at then
				for _, row in pairs(set.levels) do
					if type(row[at]) == "number" then
						row[at] = row[at] + 2
					end
				end
			end
		end
		local gem = {
			name = "Empowered Sparks II", gameId = "Metadata/Items/Gems/SupportGemEmpoweredSparksTwo",
			variantId = "EmpoweredSparksSupportTwo", grantedEffectId = "SupportEmpoweredSparksPlayerTwo",
			additionalGrantedEffectId1 = "TriggeredSparkEmpowerPlayer",
			additionalGrantedEffectId2 = "TriggeredEmpoweredSparkPlayerTwo",
			grantedEffectDisplayOrder = { 1, 2 },
			tags = { support = true, grants_active_skill = true, projectile = true, trigger = true, lightning = true, duration = true, },
			gemType = "Support", gemFamily = "Empowered Sparks", tagString = "Projectile, Trigger, Lightning, Duration",
			reqStr = 0, reqDex = 0, reqInt = 100, Tier = 4, naturalMaxLevel = 1,
		}
		-- set up as Data.lua's setupGem does
		gem.id = gemId
		gem.grantedEffect = supportTwo
		gem.additionalGrantedEffects = { data.skills.TriggeredSparkEmpowerPlayer, sparkTwo }
		-- (grantedEffectDisplayOrder { 1, 2 }, as setupGem orders I's list: the Empower skill, the Spark, then the support)
		gem.grantedEffectList = { data.skills.TriggeredSparkEmpowerPlayer, sparkTwo, supportTwo }
		data.gems[gemId] = gem
		data.gemForSkill[supportTwo] = gemId
		data.gemsByGameId[gem.gameId] = data.gemsByGameId[gem.gameId] or { }
		data.gemsByGameId[gem.gameId][gem.variantId] = gem
		data.gemNameForModSource[supportTwo.modSource] = gem.name
		data.gemForBaseName["empowered sparks ii support"] = gemId
	end
end

-- Verisium Manifestations' minion (MECHB42; RESEARCH33 R33-10, research/verisium-manifestation-numbers.md): the gem's
-- Manifest Rune (TriggeredManifestRunePlayer) summons a "Verisium Manifestation", but PoB has no minion for it, so it
-- scored 0. Added here from the game's rows (build 26650308): MonsterVarieties 2792 (DamageMultiplier 70, AttackSpeed
-- 1000 ms, LifeMultiplier 25), MonsterTypes 457 (DamageSpread 20, resistances 0), its one granted skill
-- MPAKalguuranManifestation (a projectile attack, BaseMultiplier 5000: x1.5 by PoB's own conversion of monster skills;
-- no conversion or added damage, so Physical). PoB's minion path then gives the hit with the build's minion modifiers
-- (minion level = minionLevelTable[the skill's level], PoB's convention: MEDIUM). Its attack time is the monster's 1.0 s
-- (MEDIUM-LOW: the attack uses its animation length). How many are alive (up to 10, 8 s each, one a 0.5 s cooldown on an
-- attack hit while Runic Ward pays 30 at level 20) is MECH-B's model (models.verisium_model), not PoB's.
do
	if data.skills.TriggeredManifestRunePlayer and not data.skills.MPAKalguuranManifestation then
		local skill = {
			name = "Manifestation Attack",
			hidden = true,
			description = "A Verisium Manifestation's projectile attack.",
			skillTypes = { [SkillType.Attack] = true, [SkillType.RangedAttack] = true, [SkillType.Projectile] = true,
				[SkillType.ProjectilesFromUser] = true, [SkillType.AttackInPlace] = true, },
			castTime = 1,
			qualityStats = { },
			altQualityStats = { },
			levels = { [1] = { baseMultiplier = 1.5, levelRequirement = 0, }, },
			statSets = {
				[1] = {
					label = "Manifestation Attack",
					incrementalEffectiveness = 0.054999999701977,
					statDescriptionScope = "skill_stat_descriptions",
					baseFlags = { attack = true, projectile = true, },
					constantStats = { },
					stats = { "base_is_projectile", },
					levels = { [1] = { actorLevel = 1, }, },
				},
			},
		}
		-- as Data.lua 916-955 sets up every skill it loads (no mods to source)
		skill.id = "MPAKalguuranManifestation"
		skill.modSource = "Skill:MPAKalguuranManifestation"
		for _, statSet in pairs(tableConcat({ skill }, skill.statSets)) do
			statSet.statMap = statSet.statMap or { }
			setmetatable(statSet.statMap, data.skillStatMapMeta)
			statSet.statMap._grantedEffect = skill
		end
		data.skills.MPAKalguuranManifestation = skill
		data.minions.VerisiumManifestation = {
			name = "Verisium Manifestation",
			monsterTags = { "construct", "is_unarmed", "ranged", "physical_affinity", },
			life = 0.25,
			fireResist = 0, coldResist = 0, lightningResist = 0, chaosResist = 0,
			companionFireResist = 0, companionColdResist = 0, companionLightningResist = 0, companionChaosResist = 0,
			damage = 0.7,
			damageSpread = 0.2,
			attackTime = 1,
			attackRange = 50,
			accuracy = 1,
			critChance = 5,
			baseMovementSpeed = 37,
			spectreReservation = 50,
			companionReservation = 30,
			monsterCategory = "Construct",
			spawnLocation = { },
			skillList = { "MPAKalguuranManifestation", },
			modList = { },
		}
		data.skills.TriggeredManifestRunePlayer.minionList = { "VerisiumManifestation", }
	end
end

-- The Auspex's Mist Raven (MECHB44): PoB's SummonMistRavenPlayer has no minionList, so it scored 0 (an UNDERCOUNT; probe
-- POE2T4643: "the skill summons no minion"). PoB's own "MistRaven" minion is Spiraling Conspiracy's flock ("Raven
-- Conspiracy", MonsterVarieties 2734, Tangmazu's Winged Entropy / Mad Flight), not this raven. Added here from the
-- game's rows (build 26650308; MonsterVarieties from RESEARCH33's read-only export, scratch/research33/gx2):
-- MonsterVarieties 1622 MistRavenCompanion "Mist Raven" (DamageMultiplier 150, AttackSpeed 1000 ms, LifeMultiplier 150,
-- AttackCrit 5%, attack distance 3-9, GrantedEffects 1259 / 1260 only), MonsterTypes 245 (DamageSpread 20, resistances 0).
-- Its skills: MistRavenDriveByAttack (its basic attack, ActiveSkills 40 "melee"; BaseMultiplier 20000 = x3 by PoB's own
-- conversion of monster skills, as GreatHuntPackleaderMinion's 90000 = x10; is_area_damage; 70% Physical to Chaos) and
-- MistRavenCommandDiveBomb "Mirror Breaker" (the Command; BaseMultiplier 90000 = x10, Cooldown 12 s, 1 stored use,
-- radius 25, 70% Physical to Chaos). Both use their animation length (action_attack_or_cast_time_uses_animation_length):
-- RESEARCH35 R35-5 / R35-6 (auspex-mist-raven-rate-dive-level.md): the drive-by's animation is 1,500 ms and the dive's
-- 2,666 ms (MEDIUM), not the monster's 1.0 s. The monster's 1.0 s still sets the hit's base damage (CalcActiveSkill
-- 983-988: damage x attackTime), so the rate alone is slowed, by each skill's attackSpeedMultiplier (CalcOffence 2874:
-- AttackRate x (1 + m / 100)): 1 / 1.5 and 1 / 2.666 of the monster's. PoB's minion path gives the drive-by's DPS (its
-- first skill); Mirror Breaker's press is MECH-B's model (models.mist_raven_model) through bridge.minionSkillHit.
do
	if data.skills.SummonMistRavenPlayer and not data.skills.MistRavenDriveByAttack then
		local function ravenSkill(id, name, mult, extra, types, levelExtra)
			local level = { baseMultiplier = mult, levelRequirement = 0, }
			for k, v in pairs(levelExtra or { }) do level[k] = v end
			local skill = {
				name = name,
				hidden = true,
				description = "The Mist Raven's " .. name .. ".",
				skillTypes = types,
				castTime = 1,
				qualityStats = { },
				altQualityStats = { },
				levels = { [1] = level, },
				statSets = {
					[1] = {
						label = name,
						incrementalEffectiveness = 0.054999999701977,
						statDescriptionScope = "skill_stat_descriptions",
						baseFlags = { attack = true, melee = true, area = true, },
						constantStats = extra,
						stats = { "is_area_damage", "action_attack_or_cast_time_uses_animation_length", },
						levels = { [1] = { actorLevel = 1, }, },
					},
				},
			}
			-- as Data.lua 916-955 sets up every skill it loads (no mods to source)
			skill.id = id
			skill.modSource = "Skill:" .. id
			for _, statSet in pairs(tableConcat({ skill }, skill.statSets)) do
				statSet.statMap = statSet.statMap or { }
				setmetatable(statSet.statMap, data.skillStatMapMeta)
				statSet.statMap._grantedEffect = skill
			end
			data.skills[id] = skill
		end
		ravenSkill("MistRavenDriveByAttack", "Drive-by Attack", 3,
			{ { "attack_maximum_action_distance_+", 50 }, { "active_skill_base_physical_damage_%_to_convert_to_chaos", 70 }, },
			{ [SkillType.Attack] = true, [SkillType.Melee] = true, [SkillType.Area] = true, [SkillType.Damage] = true,
				[SkillType.Physical] = true, [SkillType.Chaos] = true, },
			{ attackSpeedMultiplier = (1 / 1.5 - 1) * 100, })  -- (R35-5: a 1,500 ms animation)
		ravenSkill("MistRavenCommandDiveBomb", "Mirror Breaker", 10,
			{ { "attack_maximum_action_distance_+", 120 }, { "active_skill_base_area_of_effect_radius", 25 },
				{ "active_skill_base_physical_damage_%_to_convert_to_chaos", 70 }, },
			{ [SkillType.Attack] = true, [SkillType.Area] = true, [SkillType.Damage] = true, [SkillType.Cooldown] = true,
				[SkillType.Physical] = true, [SkillType.Chaos] = true, },
			{ cooldown = 12, storedUses = 1, attackSpeedMultiplier = (1 / 2.666 - 1) * 100, })  -- (R35-6: 2,666 ms)
		data.minions.MistRavenCompanion = {
			name = "Mist Raven",
			monsterTags = { "ranged", },
			life = 1.5,
			fireResist = 0, coldResist = 0, lightningResist = 0, chaosResist = 0,
			companionFireResist = 0, companionColdResist = 0, companionLightningResist = 0, companionChaosResist = 0,
			damage = 1.5,
			damageSpread = 0.2,
			attackTime = 1,
			attackRange = 9,
			accuracy = 1,
			critChance = 5,
			baseMovementSpeed = 36,
			spectreReservation = 50,
			companionReservation = 30,
			spawnLocation = { },
			skillList = { "MistRavenDriveByAttack", "MistRavenCommandDiveBomb", },
			modList = { },
		}
		data.skills.SummonMistRavenPlayer.minionList = { "MistRavenCompanion", }
	end
end

local DEFAULT_FIELDS = {
	"FullDPS", "CombinedDPS", "TotalDPS", "TotalDotDPS", "Life", "EnergyShield", "Mana",
	"TotalEHP", "Armour", "Evasion", "FireResist", "ColdResist", "LightningResist", "ChaosResist",
	"Spirit", "SpiritUnreserved", "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
}

-- Non-finite numbers aren't valid JSON; report them as strings
local function jsonNumber(value)
	if value ~= value then
		return "nan"
	elseif value == math.huge then
		return "inf"
	elseif value == -math.huge then
		return "-inf"
	end
	return value
end

-- (MECH86; the manager 22:55 from GEM19, an UNDERCOUNT of Shock in every build with chance to Shock) PoB never writes
-- ShockChancePerHit: it writes <ailment>ChancePerHit only in calcDamagingAilmentOutputs, for Bleed, Poison and Ignite
-- (CalcOffence 5226), and output.ShockChance only in calcAilmentDamage, which Shock never runs (5111-5118); Shock's
-- chances are ShockChanceOnHit / ShockChanceOnCrit (5613-5633, per hand for an attack). So every reader got 0. Built
-- here as PoB builds the others' per-hit chance (5226): OnHit x (1 - crit) + OnCrit x crit, a dual-wield attack's two
-- hands averaged (PoB's combineStat "AVERAGE" for ShockChance, 5956).
local function shockChancePerHit(output)
	local function of(out)
		if type(out) ~= "table" or type(out.ShockChanceOnHit) ~= "number" then
			return nil
		end
		local crit = (out.CritChance or 0) / 100
		return out.ShockChanceOnHit * (1 - crit) + (out.ShockChanceOnCrit or 0) * crit
	end
	local own = of(output)
	if own then
		return own
	end
	local main, off = of(type(output) == "table" and output.MainHand), of(type(output) == "table" and output.OffHand)
	if main and off then
		return (main + off) / 2
	end
	return main or off
end

-- (MECH95, RESEARCH36 R36-18; models.bloodhound_fill) The main skill's own multiplier on its Hits' Heavy Stun buildup,
-- as CalcOffence 5573-5575 build it: (1 + INC EnemyHeavyStunBuildup / EnemyImmobilisationBuildup) x their skill-side
-- MORE (the build's "increased Stun Buildup" lines, Melee's 50% more from CalcSetup 999). The boss-side terms (its
-- threshold, Daze's HeavyStunBuildup on the enemy) aren't in it. Bloodhound's Mark's buildup from Blood Loss isn't a
-- Hit, so the models take this out of the bar per damage they read from HeavyStunBuildupAvg. Read from the main
-- skill's mod list as "Flag:" fields are (a what-if reads the committed build's, as those do).
local function heavyStunHitMult()
	local env = build.calcsTab and build.calcsTab.mainEnv
	local skill = env and env.player and env.player.mainSkill
	if not (skill and skill.skillModList) then
		return nil
	end
	local list, cfg = skill.skillModList, skill.skillCfg
	local inc = list:Sum("INC", cfg, "EnemyHeavyStunBuildup", "EnemyImmobilisationBuildup")
	local more = list:More(cfg, "EnemyHeavyStunBuildup", "EnemyImmobilisationBuildup")
	return (1 + inc / 100) * more
end

-- (GEM31, RESEARCH42 R42-28c) PoB's alternate gem quality: the Gemling Legionnaire's Advanced Thaumaturgy ("Gem
-- quality grants socketed skills an additional effect": ModParser's GemlingQuality flag, CalcSetup 1142) makes PoB's own
-- calc add each gem's altQualityStats (CalcActiveSkill 81 / 574 pass env.useAltGemQualityStats). The bridge's stat
-- readers built the stats without it, so a model reading a gem's stats missed them for that ascendancy (Lightning
-- Conduit: +0.2 bolts a quality point, +4 at 20%: an UNDERCOUNT). nil without the flag, so nothing moves for others.
local function altGemQuality()
	local env = build.calcsTab and build.calcsTab.mainEnv
	return env and env.useAltGemQualityStats or nil
end

-- (SEARCH29, MECHC40's item (a)) A build's own LedgerAddedDPS, summed from its output as derivedCalc sums a what-if's
-- (assigned below DERIVED; nil when no added source is set)
local committedAddedDPS
local function pickStats(output, fields)
	-- An object even when empty (dkjson would otherwise encode {} as an array)
	local picked = setmetatable({ }, { __jsontype = "object" })
	for _, field in ipairs(fields or DEFAULT_FIELDS) do
		local value = output
		for part in field:gmatch("[^%.]+") do
			value = type(value) == "table" and value[part] or nil
		end
		if value == nil then
			-- Saved stats join a parent and child stat into one name, e.g. MainHandAccuracy
			local parent, child = field:match("^(MainHand)(.+)$")
			if not parent then
				parent, child = field:match("^(OffHand)(.+)$")
			end
			if parent and type(output[parent]) == "table" then
				value = output[parent][child]
			end
		end
		if value == nil and field == "LedgerAddedDPS" and committedAddedDPS then
			value = committedAddedDPS(output)  -- (SEARCH29: a committed build's added sources from its own output, as a what-if's)
		end
		if value == nil and field == "ShockChancePerHit" then
			value = shockChancePerHit(output)  -- (MECH86: PoB never writes it; shockChancePerHit)
		end
		if value == nil and field == "HeavyStunHitMult" then
			value = heavyStunHitMult()  -- (MECH95: Blood Loss isn't a Hit; models.bloodhound_fill)
		end
		if value == nil and field:match("^Flag:") then
			-- (MECH84) "Flag:<Name>": whether the main skill's mod list holds that PoB flag (models.light_stun_rate reads
			-- Flag:CannotStun, which conditions.cannot_stun_mods sets for "Your Hits cannot Stun enemies")
			local env = build.calcsTab and build.calcsTab.mainEnv
			local skill = env and env.player and env.player.mainSkill
			value = skill and skill.skillModList and skill.skillModList:Flag(skill.skillCfg, field:sub(6)) or false
		end
		if value == nil and field:match("^ModSum:") then
			-- (COV49) "ModSum:<TYPE>:<Name>": the COMMITTED build's player modDB total of that mod with no skill config
			-- (unflagged mods only; a what-if's stats still read the committed build here), for checks such as the radius damage transforms moving increases between types
			local modType, name = field:match("^ModSum:(%u+):(.+)$")
			local env = build.calcsTab and build.calcsTab.mainEnv
			value = modType and env and env.player and env.player.modDB and env.player.modDB:Sum(modType, nil, name) or 0
		end
		if type(value) == "number" then
			picked[field] = jsonNumber(value)
		elseif type(value) == "string" or type(value) == "boolean" then
			picked[field] = value
		elseif type(value) == "table" and field == "SkillDPS" then
			-- Full DPS broken down by skill (and best ailment, with the skill it came from)
			local skills = { }
			for _, entry in ipairs(value) do
				-- (MECHB62) Skyfall's Comet-Spawn under its own name (installSkyfallMinion): PoB names a skill's minion entry
				-- and its own hit's entry alike (Calcs.lua calcFullDPS), and the count model (models.skyfall_minion_model)
				-- already holds Skyfall's casts, so a press scale on "Skyfall" (models.press_model) must miss the minions
				local name = entry.name
				if type(entry.skillPart) == "string" and entry.skillPart:find("^Verisium Comet%-Spawn: ") then
					name = "Verisium Comet-Spawn"
				end
				skills[#skills + 1] = { name = name, dps = jsonNumber(entry.dps or 0), count = entry.count or 1,
					source = entry.source ~= "" and entry.source or nil }
			end
			picked[field] = skills
		elseif type(value) == "table" and field:match("WarningList$") then
			-- e.g. LifeCostWarningList: skills whose cost the pool can't pay; report how many
			local count = 0
			for _ in pairs(value) do
				count = count + 1
			end
			picked[field] = count
		end
	end
	return picked
end

-- PoB v0.23 and earlier pass constructor arguments through new(); current PoB forbids that and
-- expects new("Item"):Item(raw). Supporting both lets the release serve as a cross-check.
local function makeItem(raw)
	local ok, item = pcall(new, "Item", raw)
	if ok and item then
		return item
	end
	return new("Item"):Item(raw)
end

local function nodeById(id)
	local node = build.spec.nodes[tonumber(id)]
	if not node then
		error("unknown passive node " .. tostring(id))
	end
	return node
end

-- Unallocated attribute nodes carry no stats until an attribute is chosen. Allocation picks one
-- (PoB's AllocNode defaults to option 1, Strength), so what-if questions must use the same
-- choice or they undercount every path through attribute nodes. Copies are cached per tree.
local switchedAttributeNodes = setmetatable({ }, { __mode = "k" })

-- Pathfinder's Traveller's Wisdom: "Attribute Passive Skills can instead grant ..." (PoB ignores
-- it). Options 4-6 are those choices. The node keeps Strength's name, so PoB saves it as a
-- Strength node; exportXml/loadXml carry the real choice. Its stats count only while Traveller's
-- Wisdom is allocated (the ledger's flag:TravellersWisdom on that notable).
local TRAVELLER = {
	[4] = "5% increased Damage",
	[5] = "5% increased Armour, Evasion and Energy Shield",
	[6] = "5% increased Cost Efficiency",
}

local function attributeNode(spec, id, attribute)
	local copy = copyTableSafe(spec.tree.nodes[id], false, true)
	spec:ReplaceNode(copy, copy.options[TRAVELLER[attribute] and 1 or attribute])
	if TRAVELLER[attribute] then
		copy.sd = { TRAVELLER[attribute] }
		spec.tree:ProcessStats(copy)
		local list = new("ModList")
		for _, mod in ipairs(copy.modList) do
			local conditional = copyTable(mod)
			conditional[#conditional + 1] = { type = "Condition", var = "LedgerTravellersWisdom" }
			list:AddMod(conditional)
		end
		copy.modList = list
		copy.travellerChoice = attribute
	else
		spec.tree:ProcessStats(copy)
	end
	return copy
end

local function withAttribute(node, attribute, evenIfAllocated)
	if not node.isAttribute or (node.alloc and not evenIfAllocated) then
		return node
	end
	local spec = build.spec
	local cache = switchedAttributeNodes[spec]
	if not cache then
		cache = { }
		switchedAttributeNodes[spec] = cache
	end
	local key = node.id * 8 + attribute
	if not cache[key] then
		cache[key] = attributeNode(spec, node.id, attribute)
	end
	return cache[key]
end

-- A node a socketed jewel lets the tree allocate without a connection (Controlled
-- Metamorphosis's radius, From Nothing's keystone radius): PassiveSpec flags it and AllocNode
-- then takes the node alone, not its path
local function unconnectedOk(node)
	return node.intuitiveLeapLikesAffecting and #node.intuitiveLeapLikesAffecting > 0
end

local function nodeSet(ids, withPaths, attribute)
	local set = { }
	for _, id in ipairs(ids or { }) do
		local node = nodeById(id)
		if withPaths and not node.alloc and unconnectedOk(node) then
			set[withAttribute(node, attribute)] = true
		elseif withPaths and not node.alloc then
			if not node.path then
				error("passive node " .. id .. " has no path from the allocated tree")
			end
			for _, pathNode in ipairs(node.path) do
				set[withAttribute(pathNode, attribute)] = true
			end
		else
			set[withAttribute(node, attribute)] = true
		end
	end
	return set
end

local function countAllocated()
	local used, ascUsed = build.spec:CountAllocNodes()
	return used, ascUsed
end

local function mainSkillName()
	local env = build.calcsTab.mainEnv
	local skill = env and env.player and env.player.mainSkill
	return skill and skill.activeEffect and skill.activeEffect.grantedEffect and skill.activeEffect.grantedEffect.name or nil
end

-- The larger Time-Lost radius model (below): its calcs.initEnv wrapper, and the radius increase in
-- effect for a calculation
local installRadiusModel, radiusIncrease

-- Model inputs the bridge works out on every calculation, so a what-if sees them without a trial
-- (the what-if limit): set by setAssumptions as { kind = "Derived", name = n, fn = f, args = {...},
-- value = v }. Each is PoB multiplier Ledger<n> (a whole number: fractions come x DERIVED_UNIT), read by
-- the model's lines through `per = n`. fn "resistItems" counts the equipped items with a
-- resistance modifier of args.element (Safrin's Resolve and its kin), from the calculation's own
-- items; the others follow the output (DERIVED below): a what-if calculates once with the build's
-- values and, if its own output moves them, once more with its own.
local derivedInputs = { }
local derivedValues = { }  -- name -> value in effect for output-derived inputs
local DERIVED = { }

local DERIVED_UNIT = 1000000  -- (models.UNIT)
local RESIST_ELEMENTS = { "fire", "cold", "lightning", "chaos" }

-- The user's rule (6 Nov 2026): its own element, all Elemental, a two-element modifier with it, or
-- a socketed rune; never a maximum resistance line
local function hasResistance(item, element)
	for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
		for _, modLine in ipairs(item[kind] or { }) do
			local text = type(modLine.line) == "string" and modLine.line:lower() or ""
			if not text:find("maximum", 1, true) then
				if text:find("to all elemental resistance", 1, true) or text:find("to " .. element .. " resistance", 1, true) then
					return true
				end
				for _, other in ipairs(RESIST_ELEMENTS) do
					if text:find("to " .. element .. " and " .. other .. " resistance", 1, true)
							or text:find("to " .. other .. " and " .. element .. " resistance", 1, true) then
						return true
					end
				end
			end
		end
	end
	return false
end

local function resistItems(env, element)
	local count = 0
	for _, item in pairs(env.player and env.player.itemList or { }) do
		if type(item) == "table" and hasResistance(item, element:lower()) then
			count = count + 1
		end
	end
	return count
end

local function num(value)
	return type(value) == "number" and value == value and value or 0
end

-- Mana Tempest (models.mana_tempest_uptime): the storm drains 1% of maximum Mana per second plus
-- args.spent of the Mana and Life you spend; it lasts while Mana lasts, then refills without it; nothing for a main skill
-- that costs no Mana (it Empowers only Mana-costing Spells)
function DERIVED.manaTempest(out, a)
	local mana, regen, spend = num(out.Mana), num(out.ManaRegenRecovery), num(out.ManaPerSecondCost)
	if mana <= 0 or spend <= 0 then
		return 0
	end
	local drain = 0.01 * mana + (a.spent or 0.3) * (spend + num(out.LifePerSecondCost))
	local deficit = spend + drain - regen
	if deficit <= 0 then
		return DERIVED_UNIT
	end
	local refill = regen - spend
	if refill <= 0 then
		return 0
	end
	local on, off = mana / deficit, mana / refill
	-- (MECHC31: its effects linger args.linger s after the storm: 2 s plus its quality's 0.1 s per 1%)
	return math.floor(DERIVED_UNIT * math.min(1, (on + (a.linger or 2)) / (on + off)) + 0.5)
end

-- Lunar Blessing (models.lunar_blessing_uptime): its duration plus args.perRage per Rage, against
-- args.cooldown
function DERIVED.lunarBlessing(out, a)
	local length = (a.duration or 5) + (a.perRage or 0.2) * num(out.MaximumRage)
	local cooldown = math.max(a.cooldown or 22, length)
	return math.floor(DERIVED_UNIT * math.min(1, length / cooldown) + 0.5)
end

-- Sanguine Tides (models.sanguine_model): % of damage gained as Physical, x DERIVED_UNIT
function DERIVED.sanguine(out, a)
	local life, spent = num(out.Life), num(out.LifePerSecondCost)
	if life <= 0 or spent <= 0 then
		return 0
	end
	local gain = (a.perPercent or 2.5) * spent / life * 100
	if a.cap then  -- 40% of the Life Flask's maximum charges: the buff refreshes, it doesn't stack (models.sanguine_model)
		gain = math.min(gain, a.cap)
	end
	return math.floor(DERIVED_UNIT * gain + 0.5)
end

-- Guided Hand (models.guided_hand_model): share of attacks after a Heavy Stun, the boss's Heavy Stuns per second as
-- models.heavy_stun_rate works them out (the hit's buildup less its Cold share, the game files' threshold, +500% for
-- a.window s after each stun)
local HEAVY_STUN_TYPES = { "Physical", "Fire", "Cold", "Lightning", "Chaos" }

function DERIVED.guidedHand(out, a)
	local speed = num(out.Speed)
	local hits = speed * num(out.HitChance) / 100
	local perHit = num(out.HeavyStunBuildupAvg) / 100
	if speed <= 0 or hits <= 0 or perHit <= 0 then
		return 0
	end
	local crit = math.min(num(out.CritChance), 100) / 100
	local total, cold = 0, 0
	for _, t in ipairs(HEAVY_STUN_TYPES) do
		local w = (num(out[t .. "HitAverage"]) * (1 - crit) + num(out[t .. "CritAverage"]) * crit) * (t == "Physical" and a.physical or 1)
		total = total + w
		if t == "Cold" then
			cold = w
		end
	end
	local uncold = total > 0 and 1 - cold / total or 1
	local fill = perHit * uncold * a.boss * hits
	if fill <= 0 then
		return 0
	end
	local t = 1 / fill
	-- (MECH80, RESEARCH26 R26-45: the bar doesn't fill during the a.stun s stun; x(times) for what's left of the window)
	local stun = a.stun or 0
	local window = math.max(a.window - stun, 0)
	local every = stun + (a.times * t <= window and a.times * t or window + (1 - window / (a.times * t)) * t)
	return math.floor(DERIVED_UNIT * math.min(1, (1 / every) / speed) + 0.5)
end

-- Harmony Within (models.harmony_model): on while unreserved Mana is above Life
function DERIVED.harmony(out)
	return num(out.ManaUnreserved) > num(out.Life) and 1 or 0
end

-- Mana spent Recently (COV32, PATCH_CHECK MB-4: Indigon's "for each 200 total Mana (you have) Spent Recently" lines,
-- builder/search/assumptions.py mana_spent_stacks; must give what it gives): the average whole a.per's of Mana the main
-- skill spent in the past 4 s, x100 (a ledger share in whole percent, read as Multiplier:LedgerShare<name>). 4 s x the
-- Mana it spends a second, no more than regeneration pays (Indigon: no other Mana recovery); the 4 s sum moves by one
-- use's cost, so the whole a.per's are averaged over that spread. From the what-if's own output, so the cost line's own
-- rise feeds back (derivedCalc's second run)
local MANA_SPENT_SAMPLES = 20
function DERIVED.manaSpent(out, a)
	local per, spend, regen, cost = a.per or 200, num(out.ManaPerSecondCost), num(out.ManaRegenRecovery), num(out.ManaCost)
	local spent = 4 * math.min(spend, regen)
	if per <= 0 or spent <= 0 then
		return 0
	end
	local total = 0
	for i = 1, MANA_SPENT_SAMPLES do
		total = total + math.floor(math.max(0, spent + cost * ((i - 0.5) / MANA_SPENT_SAMPLES - 0.5)) / per)
	end
	return math.floor(100 * total / MANA_SPENT_SAMPLES + 0.5)
end

-- Melting Maelstrom (models.maelstrom_model): added boss DPS, not a modifier (reported as
-- LedgerAddedDPS, which the objective adds)
function DERIVED.maelstrom(out, a)
	return math.floor(num(out.ManaPerSecondCost) * (a.share or 1) * (1 - (a.chaosResist or 0) / 100) * 100 + 0.5)
end

-- Damage computed outside PoB's main skill (models.added_input, MECHB20: Thorns, Eternal Hunt), in hundredths of DPS:
-- a.field x a.factor from the what-if's own output, else the resolve's fixed a.fixed. Every function in ADDED_DPS is
-- summed into LedgerAddedDPS, which the objective adds to the boss DPS. No modifier reads them (their Ledger<name>
-- multipliers are set but unused), so a moved value never calculates the what-if a second time.
local ADDED_DPS = { maelstrom = true, added = true, impale = true }  -- (MECH76: impale)

-- (SEARCH29, MECHC40's item (a): the manager's 18 Nov 17:55 ruling, "commits and what-ifs must compute the same way")
-- The sum derivedCalc gives a what-if, from a committed build's own output (pickStats' LedgerAddedDPS): before it, a
-- committed score kept its added sources from the last resolve (Objective.added_dps) while a what-if of the same build
-- worked them out from its output, so a field-following source that moved after the resolve split the two.
committedAddedDPS = function(output)
	local total
	for _, input in ipairs(derivedInputs) do
		if ADDED_DPS[input.fn] then
			local fn = DERIVED[input.fn]
			total = (total or 0) + (fn and fn(output, input.args or { }) or derivedValues[input.name] or 0) / 100
		end
	end
	return total
end
function DERIVED.added(out, a)
	local base = a.field and num(out[a.field]) * (a.factor or 1) or (a.fixed or 0)
	return math.floor(base * 100 + 0.5)
end

-- Impale's added damage (MECH76: models.impale_model is the same sum, its note the rules), in hundredths of DPS, from the
-- what-if's own output (setImpale's Ledger fields): the main skill's hits a second (TotalDPS / AverageHit: PoB's rate,
-- hit chance, DPS and quantity multipliers), each Extracting min(Impales inflicted a hit, its Extracting share) Impales
-- of 30% x LedgerImpaleEffect of the Impaling hit's Physical damage (crit-weighted by which hits Impale), taken by the
-- boss at the main skill's own Physical after / before mitigation (PhysicalHitAverage / PhysicalCritAverage are after).
-- a.base: the stored share (KeywordPopups Impale "stores 30%").
function DERIVED.impale(out, a)
	local hand = type(out.MainHand) == "table" and out.MainHand or { }
	local function field(name)
		return num(out[name] ~= nil and out[name] or hand[name])
	end
	local average = num(out.AverageHit ~= nil and out.AverageHit or hand.AverageHit)
	local extract, count = num(out.LedgerImpaleExtract), num(out.LedgerImpaleCount)
	if average <= 0 or extract <= 0 or count <= 0 then
		return 0
	end
	local c = math.min(field("CritChance"), 100) / 100
	local pn, pc = num(out.LedgerImpaleChance) / 100, num(out.LedgerImpaleChanceCrit) / 100
	local p = pn * (1 - c) + pc * c
	if p <= 0 then
		return 0
	end
	local stored = (pn * (1 - c) * field("PhysicalHitAverage") + pc * c * field("PhysicalCritAverage")) / p
	local per = math.min(p * count, extract) * (a.base or 0.3) * num(out.LedgerImpaleEffect) * stored
	return math.floor(num(out.TotalDPS) / average * per * 100 + 0.5)
end

-- No Mercy (builder/search/passive_models.py no_mercy_more, COV-B): its average more Damage % x DERIVED_UNIT over a
-- fight from full Life, from the main skill's Hit share (only Hits break Concentration)
function DERIVED.noMercy(out, a)
	local combined, hits = num(out.CombinedDPS), num(out.TotalDPS)
	local B, C, b = (a.maximum or 0) / 100, (a.concentration or 30) / 100, (a.broken or 100) / 100
	if combined <= 0 or hits <= 0 or B <= 0 or C <= 0 or b <= 0 then
		return 0
	end
	local h = math.min(hits / combined, 1)
	local k = B * h * b / C
	local rampEnd = C / (h * b)
	local t
	if rampEnd >= 1 then
		t = math.log(1 + k) / k
	else
		t = math.log(1 + B) / k + (1 - rampEnd) / (1 + B)
	end
	return math.floor(DERIVED_UNIT * (1 / t - 1) * 100 + 0.5)
end

-- Rhythm of Ice (passive_models.rhythm_speed, COV-B): increased Cast Speed % x DERIVED_UNIT from the main spell's
-- Echoes in the past 4 s, a.per each up to a.cap; its cooldown caps its uses
function DERIVED.rhythmOfIce(out, a)
	local rate, cooldown = num(out.Speed), num(out.Cooldown)
	if cooldown > 0 then
		rate = math.min(rate, 1 / cooldown)
	end
	-- echoed per second: the uses that Echo by chance, and of the rest one per Tul's Avalanche Seal (echoed_per_second)
	rate = math.max(rate, 0)
	local chance = a.chance or 0
	local echoed = rate * chance + (1 - chance) * math.min(rate, math.max(a.seals or 0, 0))
	local gain = math.min(a.cap or 0, (a.per or 0) * 4 * echoed)
	return math.floor(DERIVED_UNIT * gain + 0.5)
end

local function setDerived(env)
	local modDB = env and env.modDB
	if not modDB or #derivedInputs == 0 or env.build ~= build then
		return
	end
	for _, input in ipairs(derivedInputs) do
		local value
		if input.fn == "resistItems" then
			value = resistItems(env, input.args and input.args.element or "")
		else
			value = derivedValues[input.name] or input.value or 0
		end
		modDB.multipliers["Ledger" .. input.name] = value
	end
end

local wrappedPerform
-- Spirit Vessel (Forgotten Warden's granted skill): a companion that uses copies of the Bear, Werewolf and Wyvern
-- skills socketed with it, 20% more damage per different socketed skill. PoB has neither its monster nor a minion list
-- for it, so it prices nothing. The monster's numbers are the game files' (MonsterVarieties
-- Metadata/Monsters/Companions/SpiritVessel: DamageMultiplier 210, LifeMultiplier 210, AttackSpeed 1000 ms,
-- AttackCrit 5%, mod UniqueSpiritVesselMonsterModAttackSpeed1 +25% attack speed). Its level is PoB's minion rule, the
-- game files' MinionGemLevelScaling (32 at gem level 16), inferred for Spirit Vessel. ASSUMED (the builder's guide says
-- so): the copies are minion skills, with the group's supports as PoB
-- gives any minion's skills; damage spread 20% (it only moves min and max, not the average); resistances 0.
local SPIRIT_VESSEL_TYPES = { "Bear", "Wolf", "Wyvern" }
local spiritVesselInstalled = false
local function installSpiritVessel()
	if spiritVesselInstalled or not (data and data.skills and data.minions and data.skills.SpiritVesselPlayer) then
		return
	end
	spiritVesselInstalled = true
	data.minions.SpiritVessel = {
		name = "Spirit Vessel", monsterTags = { }, life = 2.1, damage = 2.1, damageSpread = 0.2, attackTime = 1,
		attackRange = 6, accuracy = 1, critChance = 5, baseDamageIgnoresAttackSpeed = true,
		fireResist = 0, coldResist = 0, lightningResist = 0, chaosResist = 0,
		companionFireResist = 0, companionColdResist = 0, companionLightningResist = 0, companionChaosResist = 0,
		weaponType1 = "None", baseMovementSpeed = 30, monsterCategory = "Construct", spawnLocation = { },
		skillList = { },
		modList = { modLib.createMod("Speed", "INC", 25, "Spirit Vessel", ModFlag.Attack) },
	}
	data.skills.SpiritVesselPlayer.minionList = { "SpiritVessel" }
	local calcs = require("Modules.CalcBase")
	local create = calcs.createMinionSkills
	calcs.createMinionSkills = function(env, activeSkill)
		local minion = activeSkill.minion
		if not (minion and minion.type == "SpiritVessel" and activeSkill.socketGroup) then
			return create(env, activeSkill)
		end
		local ids, seen = { }, { }
		for _, gem in ipairs(activeSkill.socketGroup.gemList or { }) do
			local effect = gem.enabled ~= false and ((gem.gemData and gem.gemData.grantedEffect) or gem.grantedEffect)
			if effect and not effect.support and effect.skillTypes and not seen[effect.id] then
				for _, kind in ipairs(SPIRIT_VESSEL_TYPES) do
					if effect.skillTypes[SkillType[kind]] then
						seen[effect.id] = true
						ids[#ids + 1] = effect.id
						break
					end
				end
			end
		end
		minion.minionData.skillList = ids
		create(env, activeSkill)
		for _, minionSkill in ipairs(minion.activeSkillList) do
			if #ids == 0 then  -- nothing socketed: the companion does nothing (PoB would give it a melee hit)
				minionSkill.skillModList:NewMod("Damage", "MORE", -100, "Spirit Vessel: no socketed skill")
			else
				minionSkill.skillModList:NewMod("Damage", "MORE", 20 * #ids, "Spirit Vessel: per different socketed skill")
			end
		end
	end
	-- Its hidden support makes the socketed skills unusable for you (skill_unusable, which PoB doesn't read)
	local buildModList = calcs.buildActiveSkillModList
	calcs.buildActiveSkillModList = function(env, activeSkill)
		buildModList(env, activeSkill)
		local group = activeSkill.socketGroup
		if activeSkill.actor ~= env.player or not group or activeSkill.activeEffect.grantedEffect.id == "SpiritVesselPlayer" then
			return
		end
		local vessel = false
		for _, gem in ipairs(group.gemList or { }) do
			local effect = (gem.gemData and gem.gemData.grantedEffect) or gem.grantedEffect
			vessel = vessel or (effect and effect.id == "SpiritVesselPlayer")
		end
		if not vessel then
			return
		end
		for _, kind in ipairs(SPIRIT_VESSEL_TYPES) do
			if activeSkill.skillTypes[SkillType[kind]] then
				local flags = env.mode == "CALCS" and activeSkill.activeEffect.statSetCalcs.skillFlags
					or activeSkill.activeEffect.statSet.skillFlags
				flags.disable = true
				activeSkill.disableReason = "Socketed in Spirit Vessel: only its companion uses it"
				return
			end
		end
	end
end

-- (MECHB62; RESEARCH58 R58-7, scratch/manager/research/skyfall-comet-spawn-r58.md, HIGH except the duration) Skyfall's
-- minions, the "Verisium Comet-Spawn" (MonsterVarieties Metadata/Monsters/LeagueExpeditionNew/PlayerSummoned/
-- OctopusParasitePlayerSummoned, game build 26744644; poe2db Verisium_Comet-Spawn): PoB has no minion for it
-- (SkyfallPlayer lists minionSkillTypes but no minionList; no Verisium monster in Data/Minions.lua; probe POE2T4961:
-- summon nil, no SkillDPS entry), so its minions scored 0. Its numbers: damage x1.2, life x1.2, attack time 1 s
-- (AttackSpeed 1000; its skills use the animation's length: MEDIUM), +-20% spread, 5% crit (its +30% critical damage
-- bonus not set: the lower reading), evasion +33%, Cold 75 / Fire -30 resistances (they don't move its damage). Skills:
-- a basic melee attack (MeleeAtAnimationSpeedComboTEMP, PoB's) and GAExpedition2OctopusSlam (95% damage, 2 s cooldown,
-- 60% Physical as Cold, an area), which PoB lacks: the slam is models.skyfall_minion_model's factor, not a skill here.
-- Level: PoB's minion rule, data.minionLevelTable at the gem's level (2 x gem level: R58-7, the game's
-- MinionGemLevelScaling). Damage: PoB's ally damage table x 1.2 (R58-7: level 68, 968.76 x 1.2 = 1,163, poe2db's
-- figure). Template: PoB's Ylth Grabber (Spectres.lua 20850). How many stand: models.skyfall_minion_model.
local skyfallInstalled = false
local function installSkyfallMinion()
	if skyfallInstalled or not (data and data.skills and data.minions and data.skills.SkyfallPlayer) then
		return
	end
	skyfallInstalled = true
	data.minions.VerisiumCometSpawn = {
		name = "Verisium Comet-Spawn", monsterTags = { "beast", "cold_affinity", "crustacean_beast", "medium_movement" },
		life = 1.2, damage = 1.2, damageSpread = 0.2, attackTime = 1, attackRange = 15, accuracy = 1, critChance = 5,
		baseDamageIgnoresAttackSpeed = true, evasion = 0.33,
		fireResist = -30, coldResist = 75, lightningResist = 0, chaosResist = 0,
		companionFireResist = -30, companionColdResist = 75, companionLightningResist = 0, companionChaosResist = 0,
		baseMovementSpeed = 32, monsterCategory = "Beast", spawnLocation = { },
		skillList = { "MeleeAtAnimationSpeedComboTEMP" },
		modList = { },
	}
	data.skills.SkyfallPlayer.minionList = { "VerisiumCometSpawn" }
end

-- Augments socketed in the equipment (COV-B, COVB3), for the lines PoB parses with a multiplier it never sets:
-- Idolatry's "per Idol in your Equipment" / "per non-Idol Augment in your Equipment" (Multiplier IdolsInEquipment,
-- NonIdolAugmentsInEquipment) and the Carved Majesty rune's "+3 to Spirit per Idol socketed in your Equipment".
-- Counted from the items of each calculation (what-ifs included), as PoB reads sockets (Item:BuildModList: runes
-- 1..itemSocketCount, "None" empty). An augment is an Idol when its ModRunes entry is of type "Idol"; every other
-- socketed augment (Rune, SoulCore, AbyssalEye, and the Raven-Touched Shard, type CongealedMist) is non-Idol: the
-- game's KeywordPopups "Augment" is "items which can be placed into Augment sockets" and names Runes, Soul Cores,
-- Abyssal Eyes and Idols as augments. The shard is ASSUMED to count (it goes in an augment socket; no game text
-- says either way). Only the active weapon set's items are in PoB's item list: a swap set's augments aren't
-- counted (UNVERIFIED whether the game counts them).
-- (COV42) Runes alone, for Emergent Possibility's "Gain 1% of Damage as Extra Damage of a random Element per Rune
-- Socketed in Equipped Items" (game stat non_skill_base_all_damage_%_to_gain_as_random_element_per_socketed_rune,
-- SoulCoreStats 362; the game's own count is num_socketed_runes, SoulCoreTypes Rune's SocketedStat): Multiplier
-- RunesInEquipment counts the socketed augments whose ModRunes entry is of type "Rune" (SoulCores.Type 0, 221 rows in
-- game 26650308, the 213 live ones all in PoB's data), not Soul Cores, Idols, Abyssal Eyes or Congealed Mist, which
-- NonIdolAugmentsInEquipment also counts (an OVERCOUNT for this line). The rune carrying the line counts itself.
local function setAugmentCounts(env)
	local modDB = env and env.modDB
	if not modDB or not env.player then
		return
	end
	local idols, others, runeCount = 0, 0, 0
	local runes = data.itemMods and data.itemMods.Runes or { }
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" and item.runes then
			for i = 1, item.itemSocketCount or 0 do
				local name = item.runes[i]
				if name and name ~= "None" then
					local isIdol, isRune = false, false
					for _, entry in pairs(runes[name] or { }) do
						if type(entry) == "table" and entry.type == "Idol" then
							isIdol = true
						elseif type(entry) == "table" and entry.type == "Rune" then
							isRune = true
						end
					end
					if isRune then
						runeCount = runeCount + 1
					end
					if isIdol then
						idols = idols + 1
					else
						others = others + 1
					end
				end
			end
		end
	end
	modDB.multipliers["IdolsInEquipment"] = idols
	modDB.multipliers["NonIdolAugmentsInEquipment"] = others
	-- (COV43; RESEARCH32 R32-5, MEDIUM-HIGH: patch 0.5.4 fixed Runes tattooed with Martial Artist's Runic Meridians to
	-- "count towards the total number of Runes you had socketed") plus the Runes in the character rune slots, counted
	-- when PoB socketed them (its SocketRunesOnCharacter flag) by the initEnv wrapper (characterRuneCount)
	if env.bridgeCharacterRunes and modDB:Flag(nil, "SocketRunesOnCharacter") then
		runeCount = runeCount + env.bridgeCharacterRunes
	end
	modDB.multipliers["RunesInEquipment"] = runeCount
end

-- (COV44) Necromantic Talisman granted by an equipped item (Flesh Crucible's keystone variant / its Vaal Cultivation
-- outcome: a Keystone LIST mod). PoB moves the Amulet's mods to the minions only when node 39935 itself is allocated
-- (CalcSetup 1549: env.allocNodes[39935]), so with the item's Talisman the Amulet stayed on the player (probe POE2T3918:
-- Life 1470 -> 1523 with a "+50 to maximum Life" Amulet; 1575 either way with the node). Returns the tree's node when an
-- active item (a what-if's replacement included; a jewel only in an allocated socket) grants it and the node isn't
-- allocated; the initEnv wrapper puts that node in spec.allocNodes for the call, so PoB's own Talisman code runs. The
-- node's own mods are its keystone mod (deduplicated by mergeKeystones' keystonesAdded) and its line, which parses to
-- nothing (ModCache 5611).
local function grantedTalismanNode(override)
	local spec = build and build.spec
	local node = spec and spec.nodes and spec.nodes[39935]
	if not node or node.dn ~= "Necromantic Talisman" or spec.allocNodes[39935] then
		return nil
	end
	for slotName, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if override and override.repSlotName == slotName then
			item = override.repItem
		end
		local socket = slot.nodeId and spec.nodes[slot.nodeId]
		local socketOn = not slot.nodeId or (spec.allocNodes[slot.nodeId] and not (override and override.removeNodes
			and override.removeNodes[socket])) or (override and override.addNodes and socket and override.addNodes[socket])
		if item and not slot.inactive and socketOn then
			for _, mod in ipairs(item.modList or { }) do
				if mod.name == "Keystone" and mod.value == "Necromantic Talisman" then
					return node
				end
			end
		end
	end
	return nil
end

-- Runes (ModRunes type "Rune") in the character rune slots (Runic Meridians), with a what-if's replaced slot as PoB's
-- CalcSetup 1195-1207 reads it (override.repSlotName / repRune); 0 without the slots
local function characterRuneCount(override)
	local slots = build and build.itemsTab and build.itemsTab.runeSlots
	if not slots then
		return 0
	end
	local runes = data.itemMods and data.itemMods.Runes or { }
	local count = 0
	for slotName, slot in pairs(slots) do
		local rune = slot.GetSelValue and slot:GetSelValue()
		if override and slotName == override.repSlotName then
			rune = override.repRune
		end
		local name = rune and rune.name
		if name and name ~= "None" then
			for _, entry in pairs(runes[name] or { }) do
				if type(entry) == "table" and entry.type == "Rune" then
					count = count + 1
					break
				end
			end
		end
	end
	return count
end

-- "Your Critical Hit Chance cannot be Rerolled" (Maligaro's Virtuosity; MECH53, an OVERCOUNT). KeywordPopups RerollCrit
-- (game 26476662): "Any mechanic where the calculation of a single Hit would roll Critical Hit Chance more than once is
-- considered to be Rerolling. This includes anything that makes Critical Hit Chance Lucky, Unlucky, Bifurcated, or
-- Inevitable." PoB leaves the line unparsed (ModCache.lua 9961: nil) and keeps the FLAG mods CritChanceLucky / BifurcateCrit
-- (also from SkillStatMap 1037's attacks_roll_crits_twice) / InevitableCriticalHits (CalcOffence 3900-3925). FLAG mods
-- can't be cancelled by adding mods, and perform rebuilds each skill's mod list (CalcPerform 1205), so the player's mod
-- stores answer false for the three flags while an equipped item holds the line (wrapped Flag on the ModList / ModDB
-- classes: methods are copied into each class, Common.lua newClass). Minions keep theirs: "Your" is the player's chance.
-- (PoB has no "Unlucky crit" flag: checked grep CritChanceUnlucky in its Modules and Data, none.) Any item holding the
-- line counts, not one named unique: matched by its text.
local REROLL_CRIT_FLAGS = { CritChanceLucky = true, BifurcateCrit = true, InevitableCriticalHits = true }
local noCritRerollDB = nil -- the player's modDB while the line is equipped, for the perform running now
local function isPlayerStore(store)
	local depth = 0
	while store and depth < 8 do
		if store == noCritRerollDB then
			return true
		end
		store = store.parent
		depth = depth + 1
	end
	return false
end
-- (GEM40; the manager's ruling 24 Nov 14:09 on GEM39's proposal) PoB rounds each mod layer's product of "DPS" MORE
-- mods to two decimals (ModDB / ModList MoreInternal: round(product, 2), per layer; PoB PR #4641, 2022, from PoE1
-- tooltip screenshots of one flask, "as done ingame"; unsettled for PoE2 damage: RESEARCH R59). Our models' "DPS" MORE
-- lines (source "Assumption...") are computed factors, so rounding them moves a build by up to 0.005 / the layer's
-- product (GEM39's probe POE2T7330: Hailstorm Rounds' x0.22494 read as 0.22, +2.24% when exact; Shield Wall's x0.45642
-- as 0.46, -0.78%). So, for "DPS" only, each layer's PoB-own mods keep PoB's rounding (or its highPrecisionMods floor)
-- and ours multiply in exact: round(own, 2) x ours. Other names go to PoB's own MoreInternal untouched.
local moreRoundInstalled = false
local function installExactDpsMore()
	if moreRoundInstalled or not common or not common.classes then
		return
	end
	local function layer(isDB, self, context, cfg, flags, keywordFlags, source, modName)
		local own, ours, ownPrecision, globalLimits = 1, 1, nil, nil
		local function take(mod)
			if mod.type == "MORE" and AND64(flags, mod.flags) == mod.flags and MatchKeywordFlags(keywordFlags, mod.keywordFlags)
					and (not source or mod.source:match("[^:]+") == source) then
				local value
				if mod[1] then
					if isDB then
						globalLimits = globalLimits or { }
						value = context:EvalMod(mod, cfg, globalLimits) or 0
					else
						value = context:EvalMod(mod, cfg) or 0
					end
				else
					value = mod.value or 0
				end
				if type(mod.source) == "string" and mod.source:sub(1, 10) == "Assumption" then
					ours = ours * (1 + value / 100)
				else
					own = own * (1 + value / 100)
					local precision = data.highPrecisionMods[mod.name] and data.highPrecisionMods[mod.name][mod.type]
					if precision then
						ownPrecision = ownPrecision and math.max(ownPrecision, precision) or precision
					end
				end
			end
		end
		if isDB then
			local modList = self.mods[modName]
			for i = 1, modList and #modList or 0 do
				take(modList[i])
			end
		else
			for i = 1, #self do
				if self[i].name == modName then
					take(self[i])
				end
			end
		end
		local result
		if ownPrecision then
			local power = 10 ^ ownPrecision
			result = math.floor(own * power) / power
		else
			result = round(own, 2)
		end
		result = result * ours
		if self.parent then
			result = result * self.parent:MoreInternal(context, cfg, flags, keywordFlags, source, modName)
		end
		return result
	end
	for _, className in ipairs({ "ModList", "ModDB" }) do
		local class = common.classes[className]
		if class and class.MoreInternal then
			local original = class.MoreInternal
			local isDB = className == "ModDB"
			class.MoreInternal = function(self, context, cfg, flags, keywordFlags, source, modName)
				if modName ~= "DPS" then
					return original(self, context, cfg, flags, keywordFlags, source, modName)
				end
				return layer(isDB, self, context, cfg, flags, keywordFlags, source, modName)
			end
		end
	end
	moreRoundInstalled = true
end
local critRerollInstalled = false
local function installNoCritReroll()
	if critRerollInstalled or not common or not common.classes then
		return
	end
	for _, className in ipairs({ "ModList", "ModDB" }) do
		local class = common.classes[className]
		if class and class.Flag then
			local flag = class.Flag
			class.Flag = function(self, cfg, ...)
				if noCritRerollDB and isPlayerStore(self) then
					local n = select("#", ...)
					local kept = { }
					for i = 1, n do
						local name = select(i, ...)
						if not REROLL_CRIT_FLAGS[name] then
							kept[#kept + 1] = name
						end
					end
					if #kept == 0 then
						return false
					elseif #kept < n then
						return flag(self, cfg, unpack(kept))
					end
				end
				return flag(self, cfg, ...)
			end
		end
	end
	critRerollInstalled = true
end
local function setNoCritReroll(env)
	noCritRerollDB = nil
	if not env or not env.modDB or not env.player then
		return
	end
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" then
			for _, lines in ipairs({ item.explicitModLines or { }, item.implicitModLines or { } }) do
				for _, modLine in ipairs(lines) do
					-- (MECH88; COV42's routed item) PoB's own variant test (Item.lua 1336: the selected variant, alt variants,
					-- variant groups and versions), as sourceLines uses; the plain variantList check missed alt variants
					local inVariant
					if item.CheckModLineVariant then
						inVariant = item:CheckModLineVariant(modLine)
					else
						inVariant = not item.variant or not modLine.variantList or modLine.variantList[item.variant]
					end
					if inVariant and type(modLine.line) == "string"
						and modLine.line:lower():find("critical hit chance cannot be rerolled", 1, true) then
						noCritRerollDB = env.modDB
						return
					end
				end
			end
		end
	end
end

-- The boss's Slow taken (MECHD2; MECH-D's bosses.Boss.slow_taken). The game's Slow (KeywordPopups "Slow"): "Slows are
-- always multiplicative with each other ... 50% less Slow effect on Unique monsters ... monsters cannot be slowed below
-- 25% of their base speed"; Chill and Temporal Chains are Slows (KeywordPopups "Chill", "TemporalChains"). PoB's boss
-- presets add SlowEffectOnSelf MORE -50 (ConfigOptions.lua, MonsterUnique11 slow_potency_+%_final_from_rarity) but
-- nothing in PoB reads it (checked: grep SlowEffectOnSelf over PoB's src/: only ConfigOptions.lua and ModJewel.lua's
-- affix text), and calcs.actionSpeedMod sums Slows in full and additively: the boss acted slower than in game, so
-- enemySkillTime (its hit rate: survival time, boss_hits, every "when hit" model) was overstated. For an actor with a
-- SlowEffectOnSelf MORE, each negative ActionSpeed mod (Freeze excepted: a stop, not a Slow) and the capped Temporal
-- Chains sum count at that potency, multiplied together, floored at 25%; any other actor keeps PoB's own function.
-- A Chill counts once, the strongest: PoB adds a configured Chill to the boss twice (two -30 rows for a 30% Chill,
-- summed to -60: a probe, 12 Nov), and a target carries one Chill (KeywordPopups "Debuff": "Unless otherwise stated,
-- Debuffs of the same type do not stack - only the copy with the strongest effect applies"; "Ailments" lists Chill).
-- Temporal Chains' slow takes both the curse halving (CurseEffectOnSelf) and this one: both are target-side terms, which
-- multiply with the applier's magnitudes (KeywordPopups BuffMagnitude; RESEARCH36 R36-21, MEDIUM for this part).
-- Freeze (MECH55, MECHD3's note): PoB's Freeze row is a steady -70% action speed (CalcPerform 767-770) whenever the
-- Frozen box is on, so the boss hit at 0.3x its rate all fight whatever its Frozen share. The game's Freeze is a stop
-- (KeywordPopups "Freeze": a Frozen target can't act) for the share of the fight it lasts: with the share the conditions
-- resolver writes (config input mechFreezeShare, conditions.FREEZE_SHARE_INPUT: the Frozen box's uptime) the speed is
-- x (1 - share), the fight average (an UNDERCOUNT of the boss's hits below a 70% share, an overcount above it, before).
-- Without it, PoB's row as before.
-- Stacking Chills (MECH84; MECH83's question, probed in POE2T4043): with "Targets can be affected by two of your Chills
-- at the same time" (PoB's ChillCanStack), CalcPerform 3281-3287 sets CurrentChill (the Chill x the stacks, its
-- ChillStacks box capped at ChillStacksMax) but adds its stacked ActionSpeed row only in its breakdown pass. The boss is
-- still slowed by one Chill (the "Is the enemy Chilled?" box's own row: probe, a Frostbolt build, enemySkillTime 0.51 ->
-- 0.68 s Chilled, with or without the line and at 2 stacks), so MECH83's "no Chill slow at all" is refuted; the second
-- Chill's Slow was missing (an UNDERCOUNT of the boss's Slow). Here, with ChillCanStack and a Chill row, each stack past
-- the first counts as another Chill Slow of the same size, multiplied with the rest (KeywordPopups Slow: "always
-- multiplicative"; the line's own "affected by two of your Chills" overrides the Debuff rule that same-type Debuffs
-- don't stack; MEDIUM).
-- Slow Magnitude (MECH94; models.SLOW_MAGNITUDE's note): "Debuffs you inflict have N% increased Slow Magnitude"
-- reaches the player's modDB as MECH_SLOW_MAGNITUDE INC (PoB parses none of it); each Slow the build puts on the
-- boss (the Chill, held at its maximum; Temporal Chains, before its cap; the other negative ActionSpeed rows; not
-- Freeze, a stop) is scaled by (1 + N%) before the potency and the floor.
-- (MECH95) The Chill's hold at its maximum: RESEARCH36 R36-20 (MEDIUM; checked: KeywordPopups Chill "a default maximum
-- of 50%", SlowMagnitudeModifier "scales only the specific Magnitude that slows the target"; only the "Your Chills can
-- Slow targets by up to a maximum of N%" overrides, PoB's ChillMax, lift it; no source or test says otherwise).
-- Temporal Chains (MECH95, RESEARCH36 R36-21, HIGH; an OVERCOUNT of the boss's Slow before, defence only): its curse
-- magnitude and slow magnitude ADD (GGG corrected the 0.2.0 notes on 3 Apr 2025, "Old: ... multiplicative" -> "New:
-- ... additive with each other for Temporal Chains"; KeywordPopups BuffMagnitude: an applier's magnitude modifiers sum,
-- and multiply with the target's effect terms). PoB's TemporalChainsActionSpeed is already x(1 + the curse's INC)
-- (CalcPerform 2503-2510: CurseEffect INC + the enemy's CurseEffectOnSelf INC, x its MORE terms), so the Slow
-- Magnitude enters as (1 + c + s) / (1 + c). No Temporal Chains skill found (the curse from elsewhere): c read as 0,
-- which is the multiplied reading (the higher Slow, said in the code).
local FREEZE_SHARE_INPUT = "mechFreezeShare"
local bossSlowInstalled = false

-- (MECH95, the note above) the INC PoB scaled Temporal Chains' curse by (CalcPerform 2503), or nil
local function chainsCurseInc(player, enemyDB)
	for _, skill in ipairs(player.activeSkillList or { }) do
		local effect = skill.activeEffect and skill.activeEffect.grantedEffect
		if effect and effect.name == "Temporal Chains" and skill.skillModList then
			return skill.skillModList:Sum("INC", skill.skillCfg, "CurseEffect") + enemyDB:Sum("INC", nil, "CurseEffectOnSelf")
		end
	end
	return nil
end
local function installBossSlow()
	if bossSlowInstalled then
		return
	end
	local calcs = require("Modules.CalcBase")
	local original = calcs.actionSpeedMod
	if not original then
		return
	end
	bossSlowInstalled = true
	calcs.actionSpeedMod = function(actor)
		local modDB = actor.modDB
		local potency = modDB:More(nil, "SlowEffectOnSelf")
		if potency == 1 then
			return original(actor)
		end
		local unaffected = modDB:Flag(nil, "UnaffectedBySlows")
		local positive, mult, chill, frozen = 0, 1, 0, 0
		local player = actor.enemy
		local magnitude = 1  -- (MECH94: Slow Magnitude, the note above)
		if player and player.modDB then
			magnitude = math.max(0, 1 + player.modDB:Sum("INC", nil, "MECH_SLOW_MAGNITUDE") / 100)
			-- (MECH96: Gruelling Madness's Slowing Potency on the boss, models.madness_slow_mods: x(1 + N%) on its own
			-- potency, the Unique's 50% less included, before the floor)
			potency = potency * math.max(0, 1 + player.modDB:Sum("INC", nil, "MECH_MADNESS_POTENCY") / 100)
		end
		local freezeShare = tonumber(build and build.configTab and build.configTab.input[FREEZE_SHARE_INPUT])
		for _, row in ipairs(modDB:Tabulate("INC", nil, "ActionSpeed")) do
			local value = row.value or 0
			local source = row.mod and row.mod.source or ""
			if value < 0 and source:match("^Chill") then
				chill = math.min(chill, value)  -- one Chill on a target, the strongest (PoB adds a configured Chill twice)
			elseif value >= 0 then
				positive = positive + value
			elseif row.mod and row.mod.source == "Freeze" then
				if not unaffected and freezeShare then
					frozen = math.min(1, math.max(0, freezeShare))  -- a stop for its share of the fight
				elseif not unaffected then
					positive = positive + value
				end
			elseif not unaffected then
				mult = mult * math.max(0, 1 + value * magnitude * potency / 100)
			end
		end
		if chill < 0 and magnitude ~= 1 then  -- (MECH94: held at the Chill's maximum)
			local most = (player and player.modDB and player.modDB:Override(nil, "ChillMax"))
				or (data.nonDamagingAilment and data.nonDamagingAilment.Chill and data.nonDamagingAilment.Chill.max) or 50
			chill = math.max(-most, chill * magnitude)
		end
		if chill < 0 and not unaffected then
			local each = math.max(0, 1 + chill * potency / 100)
			mult = mult * each
			-- (MECH84: stacking Chills, the note above; `player` is actor.enemy)
			if player and player.modDB and player.modDB:Flag(nil, "ChillCanStack") then
				local cap = player.modDB:Override(nil, "ChillStacksMax") or player.modDB:Sum("BASE", nil, "ChillStacksMax")
				local extra = math.min(modDB:Sum("BASE", nil, "Multiplier:ChillStacks"), cap) - 1
				if extra > 0 then
					mult = mult * each ^ extra
				end
			end
		end
		local chains = modDB:Sum("INC", nil, "TemporalChainsActionSpeed")
		if chains >= 0 then
			positive = positive + chains
		elseif not unaffected then
			local chainsMagnitude = magnitude
			if magnitude ~= 1 and player then  -- (MECH95, R36-21: curse and slow magnitude add, the note above)
				local base = 1 + (chainsCurseInc(player, modDB) or 0) / 100
				if base > 0 then
					chainsMagnitude = math.max(0, base + magnitude - 1) / base
				end
			end
			mult = mult * math.max(0, 1 + math.max(-data.misc.TemporalChainsEffectCap, chains * chainsMagnitude) * potency / 100)
		end
		local speed = (1 + positive / 100) * math.max(0.25, mult) * (1 - frozen)
		speed = math.max((modDB:Max(nil, "MinimumActionSpeed") or 0) / 100, speed)
		local maxReduction = modDB:Max(nil, "MaximumActionSpeedReduction")
		if maxReduction then
			speed = math.min((100 - maxReduction) / 100, speed)
		end
		return speed
	end
end

-- Passive points over the level's budget (SEARCH8, Defect C: gate 2b75b delivered a tree 19 points over, which no
-- search check saw). Counted as PoB's CountAllocNodes and its "too many passive points" warning count them (Build.lua
-- 1053: points used less the smaller weapon set's, over level - 1 + quest points + ExtraPoints; a weapon set over its
-- own allowance; ascendancy points over 8), from a calculation's own allocated nodes, so a what-if adding nodes counts
-- them too. treeCheck reports the same numbers for the committed tree.
-- (SEARCH38, R54-7) the extra passive points (0-2: the Martyr of the First Edict, Olroth's Boon), which no build file
-- records; every Search sets them (bridge.setExtraPoints), so a task resumed on another engine keeps them
local extraPassivePoints = 0
-- (SEARCH39, the manager's 24 Nov ruling; RESEARCH56 R56-13) the extra WEAPON-SET passive points (Olroth's Boon:
-- "Grants one Weapon Set Passive Skill Point", HIGH): they raise each weapon set's allowance, not the main budget
local extraWeaponSetPoints = 0

local function pointsOver(allocNodes, output, granted)
	local used, ascUsed, set1, set2 = 0, 0, 0, 0
	for id, node in pairs(allocNodes or { }) do
		-- (a passive an item grants, "Allocates X": CalcSetup's addGrantedPassiveNode, no point spent)
		local spent = not (granted and granted[id] and not build.spec.allocNodes[id])
		if spent and node.type ~= "ClassStart" and node.type ~= "AscendClassStart" and node.isFreeAllocate == nil then
			if not node.ascendancyName then
				used = used + 1
			elseif not node.isMultipleChoiceOption and not (build.spec.tree.secondaryAscendNameMap
				and build.spec.tree.secondaryAscendNameMap[node.ascendancyName]) then
				ascUsed = ascUsed + 1
			end
		end
		if spent and node.allocMode == 1 then
			set1 = set1 + 1
		elseif spent and node.allocMode == 2 then
			set2 = set2 + 1
		end
	end
	output = output or { }
	local passives = math.max(build.characterLevel - 1, 0) + build.acts[build.maxActs].questPoints + extraPassivePoints
	local setPoints = (build.maxWeaponSets or 0) + (output.PassivePointsToWeaponSetPoints or 0) + extraWeaponSetPoints
	local over = math.max(used - math.min(set1, set2) - passives - (output.ExtraPoints or 0), 0)
	over = over + math.max(set1 - setPoints, 0) + math.max(set2 - setPoints, 0)
	return over, math.max(ascUsed - 8, 0)
end

-- PassivePointsOver on every calculation's output (objective.py: a hard deficit)
local function setPointsOver(env)
	local output = env and env.player and env.player.output
	if output then
		-- (SEARCH38, R53-3, probe POE2T7454: PoB fills output.ExtraPoints / PassivePointsToWeaponSetPoints only in
		-- buildOutput's MAIN block, Calcs.lua 562-564, after perform: here they are still nil. Read as that block does)
		local granted = {
			ExtraPoints = env.modDB:Sum("BASE", nil, "ExtraPoints"),
			PassivePointsToWeaponSetPoints = env.modDB:Sum("BASE", nil, "PassivePointsToWeaponSetPoints"),
		}
		local over, overAscendancy = pointsOver(env.allocNodes or build.spec.allocNodes, granted, env.grantedPassives)
		output.PassivePointsOver = over + overAscendancy
		-- (SEARCH38) the build's skill slots (PoB: 9 + SkillSlots mods, CalcSetup 728: Unset Ring, Bursting Decay,
		-- Augmented Flesh) and the gem groups that use one (the search's own count: not from an item)
		-- (SEARCH39; RESEARCH56 R56-14, MEDIUM-HIGH: forum reports 3970030 / 3974217 / 3787840) Kalandra's Touch copies
		-- the other ring's lines but NOT its Skill Slot in the game; PoB copies it (CalcSetup's Kalandra branch), one slot
		-- too many. The count leaves the copy out; PoB's own reading stays as LedgerSkillSlotsPoB (a row)
		local pobSlots = env.modDB:Sum("BASE", nil, "SkillSlots")
		local copied = 0
		local list = env.player and env.player.itemList or { }
		for _, pair in ipairs({ { "Ring 1", "Ring 2" }, { "Ring 2", "Ring 1" } }) do
			local touch, other = list[pair[1]], list[pair[2]]
			if touch and touch.name and touch.name:match("Kalandra's Touch") and other
				and not (other.name and other.name:match("Kalandra's Touch")) then
				-- (as PoB's Kalandra branch reads it: a ring's lines are in slotModList by the Touch's slot number)
				local slot = build.itemsTab.slots[pair[1]]
				local mods = other.modList or (other.slotModList and slot and other.slotModList[slot.slotNum]) or { }
				for _, mod in ipairs(mods) do
					if mod.name == "SkillSlots" and mod.type == "BASE" then
						copied = copied + (tonumber(mod.value) or 0)
					end
				end
			end
		end
		output.LedgerSkillSlotsPoB = pobSlots
		output.LedgerSkillSlots = pobSlots - copied
		local groups = 0
		for _, group in ipairs(build.skillsTab.socketGroupList) do
			if group.source == nil then
				groups = groups + 1
			end
		end
		output.LedgerGemGroups = groups
	end
end

-- Cat O' Nine Tails (MECHC8; MECHC6's design): "Life Recovery other than Flasks cannot Recover Life to above Low Life"
-- (Mods UniqueCannotRecoverAboveLowLifeExceptFlasks1, stat cannot_recover_above_low_life_except_flasks; PoB parses it to
-- nothing). Its stat is on that one mod and on no passive or skill stat set (checked: game 26476662 Mods,
-- PassiveSkills, GrantedEffectStatSets by stat index); matched here by its text on any equipped item, so another item
-- that gains it is caught too. Sets output.LowLifeRecoveryCap = 1 for models.with_darkness (max hit from Low Life).
local LOW_LIFE_CAP_TEXT = "cannot recover life to above low life"
local function setLowLifeCap(env, output)
	if not env or not env.player or not output then
		return
	end
	local found = 0
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" then
			for _, list in ipairs({ item.implicitModLines, item.explicitModLines, item.enchantModLines,
				item.runeModLines, item.crucibleModLines }) do
				for _, line in ipairs(list or { }) do
					if type(line) == "table" and type(line.line) == "string" and line.line:lower():find(LOW_LIFE_CAP_TEXT, 1, true) then
						found = 1
					end
				end
			end
		end
	end
	output.LowLifeRecoveryCap = found
end

-- "N% of Damage taken from Hits bypasses Energy Shield if Energy Shield is below half" (MECHC22; COV26 section 3, the
-- split-pool model the manager accepted 14 Nov). Stat hit_damage_bypass_energy_shield_%_when_below_half_energy_shield
-- (checked: game 26476662 Stats by id keywords bypass + energy_shield + half / below; its users in Mods, PassiveSkills,
-- GrantedEffectStatSets, SoulCoreStats: The Unleashed's UniqueHitDamageBypassesEnergyShieldWhileBelowHalfEnergyShield1
-- (15-25)% and the Legacy rune's 25% (SoulCoreStats), the same stat, so they add; no passive or skill stat set holds
-- it. Patch notes: The Unleashed new in 0.5.0, no later change found; 0.5.1 moved the Runemastered Revered Vestments'
-- 10-15% to Energy Shield Recharge, and the current Mods hold no such implicit). PoB parses it with leftover text and so
-- never applies it: its hit pools counted all the Energy Shield from full, an OVERCOUNT on low-Life / CI builds.
-- Matched by its text on any equipped item line (rune lines too). From full, Energy Shield above half takes its share
-- of each hit as before; below half, b more of each hit goes to Life (added to the type's own bypass, as PoB sums its
-- EnergyShieldBypass). Damage to die (no Life recovery): ES/2 first, then the split, then Life alone; with no other
-- bypass min(ES + L, ES/2 + L/b) (COV26). The {Type}TotalHitPool loses the difference (its Ward, Aegis, Guard and other
-- additions are kept), and {Type}MaximumHitTaken is scaled by the same ratio, as models.with_darkness scales it (armour
-- isn't linear in the hit: approximate). Whether a single hit from full is split mid-hit or only checked at its start is
-- UNVERIFIED: the floors use the split pool, the build meeting the boss's big hit after earlier hits. Sets
-- output.EsBypassBelowHalf (b, %) and output.EsBypassPoolLost (the Physical hit pool lost) for the build's notes.
local ES_BYPASS_HALF_TEXT = "(%d+%.?%d*)%% of damage taken from hits bypasses energy shield if energy shield is below half"

-- Damage taken before Life runs out: Energy Shield `above` (over half) at Life share s1, then `below` at share s2, then
-- Life alone (each in damage units; s = the share of a hit Life takes)
local function splitPoolDamage(above, below, life, s1, s2)
	local done = 0
	for _, phase in ipairs({ { above, s1 }, { below, s2 } }) do
		local es, s = phase[1], phase[2]
		if es > 0 then
			if s >= 1 then
				return done + life
			end
			local esLasts = es / (1 - s)
			if s > 0 and life / s <= esLasts then
				return done + life / s
			end
			done = done + esLasts
			life = life - s * esLasts
		end
	end
	return done + math.max(life, 0)
end

local function setEsBypassBelowHalf(env, output)
	if not env or not env.player or not output then
		return
	end
	local b = 0
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" then
			for _, list in ipairs({ item.implicitModLines, item.explicitModLines, item.enchantModLines,
				item.runeModLines, item.crucibleModLines }) do
				for _, line in ipairs(list or { }) do
					if type(line) == "table" and type(line.line) == "string" then
						-- (MECHC34; COV38's probe POE2T3814, an OVERCOUNT of defence: a unique's line is kept as its range,
						-- "(15-25)% of Damage ...", which the pattern never matched, so The Unleashed's bypass was lost;
						-- resolved at the line's own roll as PoB does, Item.lua 1290 / this file's 5184)
						local text = line.line
						if text:find("%((%-?%d+%.?%d*)%-(%-?%d+%.?%d*)%)") then
							text = itemLib.applyRange(text, line.range or main.defaultItemAffixQuality or 0.5,
								line.valueScalar, line.corruptedRange)
						end
						local value = text:lower():match(ES_BYPASS_HALF_TEXT)
						if value then
							b = b + (tonumber(value) or 0)
						end
					end
				end
			end
		end
	end
	output.EsBypassBelowHalf = b
	output.EsBypassPoolLost = 0
	local cap, maximum = output.EnergyShieldRecoveryCap or 0, output.EnergyShield or 0
	if b <= 0 or cap <= 0 or maximum <= 0 or env.player.modDB:Flag(nil, "EternalLife") then
		return
	end
	for _, damageType in ipairs({ "Physical", "Lightning", "Cold", "Fire", "Chaos" }) do
		local e = (output[damageType.."EnergyShieldBypass"] or 0) / 100
		local life = output[damageType.."MoMHitPool"]
		local pool = output[damageType.."TotalHitPool"]
		if e < 1 and life and pool and pool > 0 then
			local doubled = damageType == "Chaos" and not env.player.modDB:Flag(nil, "ChaosNotDoubleESDamage") and 2 or 1
			local above = math.max(cap - maximum / 2, 0) / doubled
			local below = math.min(cap, maximum / 2) / doubled
			local lost = splitPoolDamage(above, below, life, e, e)
				- splitPoolDamage(above, below, life, e, math.min(e + b / 100, 1))
			if lost > 0 then
				local newPool = math.max(pool - lost, 0)
				local hit = output[damageType.."MaximumHitTaken"]
				if type(hit) == "number" and hit == hit and hit < math.huge then
					output[damageType.."MaximumHitTaken"] = hit * newPool / pool
				end
				output[damageType.."TotalHitPool"] = newPool
				if damageType == "Physical" then
					output.EsBypassPoolLost = lost
				end
			end
		end
	end
end

-- Everlasting Sacrifice (MECHC36; recovery.everlasting_sacrifice): "When you reach full Energy Shield, Sacrifice all
-- Energy Shield to gain +5% to all Maximum Resistances for 4 seconds". PoB sets Condition:EverlastingSacrifice from the
-- line (ModCache 7404) and counts the whole Energy Shield in its hit pools. Assumptions.models sends the share of maximum
-- Energy Shield a boss hit doesn't meet (a raw EverlastingEsLost BASE, %, under that condition, so a what-if without the
-- shield keeps its full pool); each {Type}TotalHitPool loses the difference (Energy Shield taken first at the type's
-- own bypass, Chaos doubled on it, as setEsBypassBelowHalf's split without the half), and {Type}MaximumHitTaken is
-- scaled by the same ratio. Read after setEsBypassBelowHalf: with both lines the two cuts stack approximately (named).
-- Sets output.EverlastingSacrifice (1 with the line) for recovery.full_es_share and output.EverlastingEsPoolLost (the
-- Physical hit pool lost) for the build's notes.
local function setEverlastingSacrifice(env, output)
	if not env or not env.player or not output then
		return
	end
	local modDB = env.player.modDB
	output.EverlastingSacrifice = 0
	output.EverlastingEsPoolLost = 0
	if not modDB:Flag(nil, "Condition:EverlastingSacrifice") then
		return
	end
	output.EverlastingSacrifice = 1
	local share = math.min(math.max(modDB:Sum("BASE", nil, "EverlastingEsLost") / 100, 0), 1)
	local cap = math.min(output.EnergyShieldRecoveryCap or output.EnergyShield or 0, output.EnergyShield or 0)
	if share <= 0 or cap <= 0 or modDB:Flag(nil, "EternalLife") then
		return
	end
	for _, damageType in ipairs({ "Physical", "Lightning", "Cold", "Fire", "Chaos" }) do
		local e = (output[damageType.."EnergyShieldBypass"] or 0) / 100
		local life = output[damageType.."MoMHitPool"]
		local pool = output[damageType.."TotalHitPool"]
		if e < 1 and life and pool and pool > 0 then
			local doubled = damageType == "Chaos" and not modDB:Flag(nil, "ChaosNotDoubleESDamage") and 2 or 1
			local es = cap / doubled
			local lost = splitPoolDamage(es, 0, life, e, e) - splitPoolDamage(es * (1 - share), 0, life, e, e)
			if lost > 0 then
				local newPool = math.max(pool - lost, 0)
				local hit = output[damageType.."MaximumHitTaken"]
				if type(hit) == "number" and hit == hit and hit < math.huge then
					output[damageType.."MaximumHitTaken"] = hit * newPool / pool
				end
				output[damageType.."TotalHitPool"] = newPool
				if damageType == "Physical" then
					output.EverlastingEsPoolLost = lost
				end
			end
		end
	end
end

-- Indigon's "Mana Recovery other than Regeneration cannot Recover Mana" (MECHC14; COV17, an OVERCOUNT): PoB parses it to no
-- mod (ModCache: {nil, "<the whole line>"}), so its Mana leech, Mana on Hit / Kill / Block and Mana Recoup outputs stayed
-- in the sustain and recovery floors. Zeroed here after PoB's calculation; regeneration stays. Mana flasks: chargeGain's
-- manaRegenOnly (flasks.recovery_per_second). (checked: Mods.json has one mod with the stat,
-- UniqueCannotRecoverManaExceptRegen1, on Indigon; PassiveSkills.json none; so items are scanned)
local MANA_REGEN_ONLY_TEXT = "mana recovery other than regeneration cannot recover mana"
local MANA_RECOVERY_OUTPUTS = { "ManaLeechRate", "ManaLeechGainRate", "ManaLeechInstantRate", "ManaLeechPerHit",
	"ManaLeechInstanceRate", "ManaOnHit", "ManaOnHitRate", "ManaOnKill", "ManaOnBlock", "ManaRecoup",
	"PhysicalManaRecoup", "FireManaRecoup", "ColdManaRecoup", "LightningManaRecoup", "ChaosManaRecoup",
	"ElementalManaRecoup", "PhysicalDamageMitigatedManaPseudoRecoup", "ManaRecoupRecoveryAvg" }
local function itemHasLine(env, text)
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" then
			for _, list in ipairs({ item.implicitModLines, item.explicitModLines, item.enchantModLines,
				item.runeModLines, item.crucibleModLines }) do
				for _, line in ipairs(list or { }) do
					if type(line) == "table" and type(line.line) == "string" and line.line:lower():find(text, 1, true) then
						return true
					end
				end
			end
		end
	end
	return false
end
-- The base-chance swap (MECH96; an UNDERCOUNT and an OVERCOUNT): "Chance to Poison is calculated from your base chance
-- to inflict Bleeding instead" (stat base_poison_chance_is_bleed_chance_instead) and its mirror "Chance to inflict
-- Bleeding is calculated from your base chance to Poison instead" (base_bleed_chance_is_poison_chance_instead). PoB
-- parses neither (no ModCache entry; ModScalability lists them with no mods; SkillStatMap has neither stat), so Poison
-- kept its own base: a build with Bleed base chance and the line got no Poison (lower than the game), and one with its
-- own Poison base kept it (higher). With the line on any equipped item, PoB's flat-ailment base for the swapped ailment
-- (CalcOffence 5540: skillModList:Sum("BASE", cfg, "<Ailment>Chance", "AilmentChance")) reads the other ailment's base
-- instead, on the player's own stores only; its increases and MORE terms stay its own (CalcOffence 5541-5543), as the
-- wording says ("calculated from your base chance ... instead"). Both lines at once: each reads the other's own base
-- (UNVERIFIED in game; no source says). The enemy's SelfPoisonChance / SelfBleedChance isn't "your" chance: kept.
-- (checked: Stats.json ids holding "instead" with poison / bleed (5: the two above, two Bleeding-as-Fire, one stored
-- Poison); Mods Stat1-6: only HandWrapsAbyssModGlovesUlamanSuffixPoisonChance / BleedChance (spawn weight 0: Way of the
-- Stonefist's transform of the Desecrated gloves suffixes, builder/search/stonefist.py); PassiveSkills, SoulCoreStats,
-- GrantedEffectStatSetsPerLevel, GrantedEffectQualityStats: none; GrantedEffectStatSets: SupportMaladyPlayer (Malady,
-- both stats), drop-disabled and shut out of the search (context.py), NOT handled here: an imported build with Malady
-- keeps PoB's reading; second source PoB's own ModItem.lua 2137-2138 texts; poe2db NOT checked.)
local BASE_CHANCE_SWAPS = {
	{ text = "chance to poison is calculated from your base chance to inflict bleeding instead",
	  ailment = "PoisonChance", from = "BleedChance" },
	{ text = "chance to inflict bleeding is calculated from your base chance to poison instead",
	  ailment = "BleedChance", from = "PoisonChance" },
}
local baseSwapDB = nil -- the player's modDB while a swap line is equipped
local baseSwapFrom = { } -- ailment chance name -> the chance name its base is read from
local function inBaseSwapStore(store)
	local depth = 0
	while store and depth < 8 do
		if store == baseSwapDB then
			return true
		end
		store = store.parent
		depth = depth + 1
	end
	return false
end
local baseSwapInstalled = false
local function installBaseChanceSwap()
	if baseSwapInstalled or not common or not common.classes then
		return
	end
	for _, className in ipairs({ "ModList", "ModDB" }) do
		local class = common.classes[className]
		if class and class.Sum then
			local sum = class.Sum
			class.Sum = function(self, modType, cfg, ...)
				if baseSwapDB and modType == "BASE" and select("#", ...) == 2 then
					local name, second = ...
					local from = baseSwapFrom[name]
					if from and second == "AilmentChance" and inBaseSwapStore(self) then
						return sum(self, modType, cfg, from, second)
					end
				end
				return sum(self, modType, cfg, ...)
			end
		end
	end
	baseSwapInstalled = true
end
local function setBaseChanceSwap(env)
	baseSwapDB = nil
	baseSwapFrom = { }
	if not env or not env.modDB or not env.player then
		return
	end
	for _, swap in ipairs(BASE_CHANCE_SWAPS) do
		if itemHasLine(env, swap.text) then
			baseSwapFrom[swap.ailment] = swap.from
			baseSwapDB = env.modDB
		end
	end
end
-- Life Recharge (SEARCH15; AUDIT28 A28-3, an UNDERCOUNT): Tetzlapokal's Desire's "Life Recharges" (Mods
-- UniqueLifeRecharge1, stat base_life_recharges_like_energy_shield) parses to nothing in PoB (ModParser has only "life
-- recharges instead of energy shield", Eternal Youth's flag, which also turns Energy Shield Recharge off). The game
-- (KeywordPopups LifeRecharge): "Lost Life will start Recharging at a rate of 12.5% per second after a base delay of 4
-- seconds. Further loss of Life resets this delay ... Modifiers to Energy Shield Recharge Rate or to how fast it starts
-- will also apply to Life Recharge." So, beside Energy Shield Recharge, LifeRecharge here is PoB's own Eternal Youth sum
-- (CalcDefence 1795-1800: Life x EnergyShieldRechargeBase x the Energy Shield Recharge increases and more, x the Life
-- recovery rate), skipped where PoB set one (Eternal Youth). LifeRechargeDelay is the Energy Shield Recharge delay (PoB's
-- own sum, CalcDefence 1837-1838) for either. (checked: the stat ids holding "life_recharge" in Stats.json are on two
-- mods, UniqueLifeRecharge1 and HandWrapsUniqueVaalPact1 (keystone_eternal_youth), and one passive,
-- passive_keystone_eternal_youth; matched by the line's exact text on any equipped item, so another item that gains it
-- is caught too; second source: poe2db's Tetzlapokal's Desire page, "Life Recharges", stat base life recharges like
-- energy shield, value 1, no rate given). Energy Shield Recharge is kept beside it: the stat says "like", Eternal Youth's
-- says "instead of" (UNVERIFIED in game whether both run; poe2db doesn't say).)
local LIFE_RECHARGES_TEXT = "life recharges"
local function setLifeRecharge(env)
	local output = env and env.player and env.player.output
	local modDB = env and env.player and env.player.modDB
	if not output or not modDB then
		return
	end
	local found = output.LifeRecharge ~= nil
	if not found then
		for _, item in pairs(env.player.itemList or { }) do
			if type(item) == "table" then
				for _, list in ipairs({ item.implicitModLines, item.explicitModLines, item.enchantModLines,
					item.runeModLines, item.crucibleModLines }) do
					for _, line in ipairs(list or { }) do
						if type(line) == "table" and type(line.line) == "string"
							and line.line:lower():match("^%s*(.-)%.?%s*$") == LIFE_RECHARGES_TEXT then
							found = true
						end
					end
				end
			end
		end
		if found and not modDB:Flag(nil, "CannotRecoverLifeOutsideLeech") then
			local inc = modDB:Sum("INC", nil, "EnergyShieldRecharge")
			local more = modDB:More(nil, "EnergyShieldRecharge")
			output.LifeRecharge = round((output.Life or 0) * data.misc.EnergyShieldRechargeBase * (1 + inc / 100) * more
				* (output.LifeRecoveryRateMod or 1), 1)
		end
	end
	if output.LifeRecharge then
		local base = modDB:Override(nil, "EnergyShieldRechargeBase")
			or (data.misc.EnergyShieldRechargeDelay + modDB:Sum("BASE", nil, "EnergyShieldRechargeFaster"))
		output.LifeRechargeDelay = output.EnergyShieldRechargeDelay
			or base / (1 + modDB:Sum("INC", nil, "EnergyShieldRechargeFaster") / 100)
	end
end

local function setManaRegenOnly(env)
	local output = env and env.player and env.player.output
	if not output then
		return
	end
	if not itemHasLine(env, MANA_REGEN_ONLY_TEXT) then
		output.ManaRegenOnly = 0
		return
	end
	output.ManaRegenOnly = 1
	for _, name in ipairs(MANA_RECOVERY_OUTPUTS) do
		if output[name] ~= nil then
			output[name] = 0
		end
	end
end

-- ShowAverage (SEARCH7, the manager's ruling 12 Nov): 1 when PoB shows the main skill's damage per use (skillData.showAverage:
-- Sunder, Earthquake and other slams), so its CombinedDPS is AverageDamage, blind to attack speed and DPS multipliers
-- (CalcOffence.lua 6265); objective.main_dps then reads the per-second damage. The minion's under Minion.ShowAverage.
-- The main skill's Chill-able hit (MECH54, AUDIT25 A25): PoB sums, for Chill's magnitude, the HitAverage / CritAverage
-- of every damage type that can Chill (CalcOffence 5022-5033 calcAverageUnmitigatedSourceDamage; canDoAilment 4999-5016:
-- Cold unless <Type>CannotChill / CannotChill, any other type with <Type>CanChill / CanChill, e.g. Shaper of Winter or
-- the uniques letting Physical or all damage Chill; crits off with AilmentsAreNeverFromCrit). Those are locals of
-- CalcOffence, so the same rule is applied here to its outputs: output.ChillHitDamage / ChillCritDamage, read by
-- conditions.chill_effect for the game's 30% floor.
local CHILL_TYPES = { "Physical", "Lightning", "Cold", "Fire", "Chaos" }
local function setChillDamage(env)
	local skill = env and env.player and env.player.mainSkill
	local output = env and env.player and env.player.output
	if not skill or not output or not skill.skillModList then
		return
	end
	local mods, cfg = skill.skillModList, skill.skillCfg
	local canCrit = not mods:Flag(cfg, "AilmentsAreNeverFromCrit")
	local hit, crit = 0, 0
	for _, t in ipairs(CHILL_TYPES) do
		local allowed
		if t == "Cold" then
			allowed = not (mods:Flag(cfg, "ColdCannotChill") or mods:Flag(cfg, "CannotChill"))
		else
			allowed = mods:Flag(cfg, t .. "CanChill") or mods:Flag(cfg, "CanChill")
		end
		if allowed then
			local hand = type(output.MainHand) == "table" and output.MainHand or { }
			hit = hit + num(output[t .. "HitAverage"] or hand[t .. "HitAverage"])
			if canCrit then
				crit = crit + num(output[t .. "CritAverage"] or hand[t .. "CritAverage"])
			end
		end
	end
	output.ChillHitDamage, output.ChillCritDamage = hit, crit
end

-- Impale (MECH76; an UNDERCOUNT for every Impale build before): PoB-PoE2 sets output.ImpaleChance = 0 on every pass
-- ("Legacy PoE1 ailments (to be removed later)", CalcOffence.lua 4983-4984; its only assignment), so it scores no Impale
-- at all (MECH75's probe POE2T3087: a 100% Impale ring on Boneshatter gave 0 at any stack count). Published here for
-- the main skill of every calculation, from its own modifier list, for DERIVED.impale and models.impale_model:
--   LedgerImpaleChance / LedgerImpaleChanceCrit: % on a non-critical / critical hit (PoB's ImpaleChance BASE sums, the
--     second under Condition:CriticalStrike: SkillStatMap's spell_impale_on_crit, and models.raw_mod's
--     "Critical Hits inflict Impale", a line PoB leaves unparsed);
--   LedgerImpaleEffect: x on the stored 30% (ImpaleEffect INC / MORE, plus the active gem's impale_magnitude_+% and
--     active_skill_impale_magnitude_+%_final, which SkillStatMap doesn't map: His Vile Intrusion's 100%, Bonestorm's
--     200% more);
--   LedgerImpaleCount: Impales a hit inflicts (1 + number_of_additional_impaled_debuffs_to_apply, unmapped: His Vile
--     Intrusion 2 at gem level 20, 3 at 25, patch 0.5.0);
--   LedgerImpaleExtract: the share of the skill's hits that Extract: 0 unless an Attack (KeywordPopups Impale:
--     "Subsequent Attack Hits against Impaled targets will Extract"), 0 with a cannot_consume_impale gem on it (the
--     Impale and Crystalline Shards supports), the non-crit share with LedgerImpaleNoCritExtract ("Critical Hits cannot
--     Extract Impale", unparsed by PoB: models.raw_mod).
local IMPALE_NO_EXTRACT = "cannot_consume_impale"
local function effectCarries(granted, stat)
	for _, set in ipairs(granted and granted.statSets or { }) do
		for _, name in ipairs(set.stats or { }) do
			if name == stat then
				return true
			end
		end
		for _, pair in ipairs(set.constantStats or { }) do
			if pair[1] == stat then
				return true
			end
		end
	end
	return false
end

local function setImpale(env)
	local skill = env and env.player and env.player.mainSkill
	local output = env and env.player and env.player.output
	if not skill or not output or not skill.skillModList then
		return
	end
	local mods, cfg = skill.skillModList, skill.skillCfg
	local critCfg = copyTable(cfg or { }, true)
	critCfg.skillCond = copyTable((cfg and cfg.skillCond) or { }, true)
	critCfg.skillCond.CriticalStrike = true
	local chance = math.min(100, math.max(0, mods:Sum("BASE", cfg, "ImpaleChance")))
	local crit = math.min(100, math.max(0, mods:Sum("BASE", critCfg, "ImpaleChance")))
	output.LedgerImpaleChance, output.LedgerImpaleChanceCrit = chance, crit
	-- (the Extracting share is published for every Attack, Impaling or not: it Extracts other skills' Impales)
	local attack = (skill.skillFlags and skill.skillFlags.attack) or (skill.skillTypes and skill.skillTypes[SkillType.Attack])
	local extract = attack and 1 or 0
	output.LedgerImpaleNoExtractGem = nil
	for _, effect in ipairs(skill.effectList or { }) do
		if extract > 0 and effectCarries(effect.grantedEffect, IMPALE_NO_EXTRACT) then
			extract = 0
			output.LedgerImpaleNoExtractGem = effect.grantedEffect.name  -- (which gem stops it, for the note)
		end
	end
	if extract > 0 and mods:Flag(cfg, "LedgerImpaleNoCritExtract") then
		extract = 1 - math.min(num(output.CritChance or (type(output.MainHand) == "table" and output.MainHand.CritChance)), 100) / 100
	end
	output.LedgerImpaleExtract = extract
	if chance <= 0 and crit <= 0 then
		output.LedgerImpaleEffect, output.LedgerImpaleCount = 0, 0
		return
	end
	local gem = { }
	local set = skill.activeEffect and skill.activeEffect.statSet
	set = set and (set.statSet or set)
	if set and (set.constantStats or set.stats) then
		local ok, built = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect, skill.activeEffect.grantedEffect, set,
			altGemQuality())
		gem = ok and built or { }
	end
	local inc = mods:Sum("INC", cfg, "ImpaleEffect") + num(gem["impale_magnitude_+%"])
	local more = mods:More(cfg, "ImpaleEffect") * (1 + num(gem["active_skill_impale_magnitude_+%_final"]) / 100)
	output.LedgerImpaleEffect = math.max(0, (1 + inc / 100) * more)
	output.LedgerImpaleCount = 1 + num(gem["number_of_additional_impaled_debuffs_to_apply"])
end

local function markShowAverage(env)
	for _, actor in ipairs({ env.player, env.minion }) do
		local skill = actor and actor.mainSkill
		if actor.output then
			actor.output.ShowAverage = (skill and skill.skillData and skill.skillData.showAverage) and 1 or 0
		end
	end
end

-- Icestorm's "cold_damage_cannot_chill" (its GrantedEffectStatSets ImplicitStats, game 26476662; MECH54, an OVERCOUNT):
-- PoB's SkillStatMap has no entry for it, so PoB Chills with Icestorm's cold hits (ChillChanceOnHit 100 above a 1%
-- Chill). Mapped to the ColdCannotChill flag CalcOffence's canDoAilment reads (damageType.."Cannot"..ailment, line
-- 5006). In the game files only Icestorm carries the stat (checked: Stats.json ids holding "chill" with cannot / never;
-- GrantedEffectStatSets implicit and constant stats, Mods Stat1-4, PassiveSkills; never_freeze_or_chill, Frost Darts',
-- is PoB-mapped already; never_ignite_chill_freeze_shock has no carrier there).
local function installCannotChill()
	if data and data.skillStatMap and not data.skillStatMap.cold_damage_cannot_chill then
		data.skillStatMap.cold_damage_cannot_chill = { { name = "ColdCannotChill", type = "FLAG", value = true, flags = 0, keywordFlags = 0 } }
	end
end

-- Arctic Armour's burst (MECHB55; the manager 10:15 22 Nov, an UNDERCOUNT): "Deals {0} to {1} Cold Damage per Stage"
-- (arctic_armour_minimum / maximum_added_cold_damage_per_stack, its one stat set ArcticArmourPlayer, game 26650308) has no
-- entry in PoB's SkillStatMap or the set's statMap (checked: act_int.lua ArcticArmourPlayer, SkillStatMap.lua), so PoB
-- gave the burst no damage. Mapped as the skill's base Cold damage (spell_minimum / maximum_base_cold_damage's own
-- mods): PoB then scores one burst (one stage's damage) as a Cold Spell hit; its rate is models.arctic_armour_model's.
local function installArcticArmourBurst()
	if data and data.skillStatMap and not data.skillStatMap.arctic_armour_minimum_added_cold_damage_per_stack then
		data.skillStatMap.arctic_armour_minimum_added_cold_damage_per_stack = data.skillStatMap.spell_minimum_base_cold_damage
		data.skillStatMap.arctic_armour_maximum_added_cold_damage_per_stack = data.skillStatMap.spell_maximum_base_cold_damage
	end
end

-- The game's is_area_damage on a stat set (GEM2, 14 Nov 2026; an UNDERCOUNT): PoB maps no is_area_damage (checked: grep
-- of SkillStatMap.lua and Modules/*.lua finds none) and gives a set ModFlag.Area only from its baseFlags.area
-- (CalcActiveSkill 601; CalcOffence 581 sets skillFlags.area later from a radius, after the mod flags are built), so
-- "increased Area Damage" missed sets the game marks area damage: GEM1's probe POE2T2016 read x1.0 with 100% increased
-- Area Damage on Voltaic Barrier, Herald of Blood's Explosion, Bitter Dead's Explosion and Core, Catalysing Discharge's
-- Fire / Lightning / Cold, Midnight Zenith's Crystal, Falling Thunder's Projectile, Tempest Flurry's Third / Fourth Strike.
-- Every PoB stat set carrying the stat (in its stats or a non-zero constantStats entry) gets baseFlags.area: the game's own
-- marking, nothing else changed (scratch/gem2/area_scan.py lists the 91 sets without the flag, 64 of them spectres').
local function installAreaDamageFlag()
	if not (data and data.skills) or data.ledgerAreaDamageFlag then
		return
	end
	data.ledgerAreaDamageFlag = true
	for _, effect in pairs(data.skills) do
		for _, set in pairs(type(effect) == "table" and effect.statSets or { }) do
			local area = false
			for _, stat in ipairs(set.stats or { }) do
				if stat == "is_area_damage" then
					area = true
				end
			end
			for _, stat in ipairs(set.constantStats or { }) do
				if stat[1] == "is_area_damage" then
					area = (stat[2] or 0) ~= 0
				end
			end
			if area then
				set.baseFlags = set.baseFlags or { }
				set.baseFlags.area = true
			end
		end
	end
end

-- Corpse explosions PoB never calculates (MECH-B, MECHB26): Xibaqua's Rending's Fiery Reclamation (TriggeredXibaquasRendingPlayer)
-- "Deals Fire damage in an area around the exploding Corpse based on its maximum Life", but PoB's data gives it no
-- baseFlags and its skill types (the game's ActiveSkills triggered_detonate_dead_on_landing: SkillGrantedBySupport, Area,
-- Fire, Triggerable, Triggered, InbuiltTrigger, AttackInPlace, TargetsDestructibleCorpses) hold no Attack, Damage or
-- Projectile, so CalcActiveSkill 179 never sets skillFlags.hit and CalcOffence 4214 deals none of its damage. Its stat set
-- gets baseFlags.hit (the skill's types are left alone: adding Damage would change which supports apply). Its explosion
-- taken as a Hit, as PoB scores the other corpse explosions (Decompose: UNVERIFIED for this one). Its damage settings
-- (explodeCorpse, 6% of the corpse's Life as Fire) come from the model (models.corpse_explosion_mods), its rate from the
-- corpses the build makes (models.corpse_model).
local CORPSE_EXPLOSION_HITS = { "TriggeredXibaquasRendingPlayer" }
local function installCorpseExplosionHits()
	if not (data and data.skills) then
		return
	end
	for _, id in ipairs(CORPSE_EXPLOSION_HITS) do
		for _, set in pairs((data.skills[id] or { }).statSets or { }) do
			set.baseFlags = set.baseFlags or { }
			set.baseFlags.hit = true
		end
	end
end

-- Fist of Kalguur's Aftershock (MECH-B, MECHB53): TriggeredFistOfKalguurPlayer ("Fist of Kalguur", the rune that
-- "erupts in an Aftershock after a duration"; 490% of attack damage at level 20) is an Attack / Area / Melee / Slam skill
-- in PoB's skill types, but its one stat set has empty baseFlags, so PoB computes no damage for it (probe POE2T4803:
-- AverageDamage 0). Its set gets baseFlags attack / area / melee from its own skill types (nothing else changed); its
-- rate comes from the model (models.kalguur_rune_model: the boosted Slams' hits, its rune limit and duration).
-- (GEM43, RESEARCH59 R59-3, MEDIUM) Shockwave Totem's "Jagged Ground Eruption" set (ShockwaveTotemQuakePlayer set 2) as
-- Melee. The game's set (GrantedEffectStatSets ShockwaveTotemJaggedQuakePlayer, 26650308) has empty implicit and
-- constant stats and the flag R59-3 reads as "inherits the set before" (on 122 of 8,284 sets, almost all a skill's
-- secondary hits: Earthquake's Aftershock, Sunder's / Boneshatter's Shockwave); the set before, the Slam, holds
-- attack_is_melee_override, and the active skill's types hold Melee. PoB's copy of the set lists
-- attack_is_melee_override too but gives baseFlags totem / attack / area without melee, and PoB reads that stat nowhere
-- (SkillStatMap.lua, Modules/*.lua: no hit), so "Melee Damage" lines missed the set PoB scores for the skill (probe
-- scratch/gem43/probe_eruption.py: "100% increased Melee Damage" doubled the Slam set, 12.2 -> 24.5, and left the
-- Eruption at 44.2). The flag is added only where PoB lacks it and recorded on the set (bridgeAddedFlags); setRates gives
-- the set's DPS without it too (TotalDPSWithoutAdded), for models.set_flag_model's "not melee" row. (No new main-chunk
-- locals: bridge.lua's main function is at Lua's 200-local limit.)
local ATTACK_SET_FLAGS = { TriggeredFistOfKalguurPlayer = { attack = true, area = true, melee = true } }
local function installAttackSetFlags()
	if not (data and data.skills) then
		return
	end
	for id, flags in pairs(ATTACK_SET_FLAGS) do
		for _, set in pairs((data.skills[id] or { }).statSets or { }) do
			set.baseFlags = set.baseFlags or { }
			for flag, on in pairs(flags) do
				set.baseFlags[flag] = on
			end
		end
	end
	-- (GEM43, R59-3) the per-set readings, above
	local readings = { ShockwaveTotemQuakePlayer = { ["Jagged Ground Eruption"] = { melee = true } } }
	for id, bySet in pairs(readings) do
		for _, set in pairs((data.skills[id] or { }).statSets or { }) do
			local flags = bySet[set.label or ""]
			if flags then
				set.baseFlags = set.baseFlags or { }
				for flag in pairs(flags) do
					if not set.baseFlags[flag] then
						set.baseFlags[flag] = true
						set.bridgeAddedFlags = set.bridgeAddedFlags or { }
						set.bridgeAddedFlags[flag] = true
					end
				end
			end
		end
	end
end

-- A `share:` line's parsed modifiers counted at the share (COV17; COV32 made it shared by installLedgerShare and
-- installLedgerBonded): each at value / 100 x Multiplier:LedgerShare<share>, the share in whole percent.
local function ledgerShareMods(list, share)
	local tagged = { }
	for _, parsed in ipairs(list) do
		local copy = copyTable(parsed)
		if type(copy.value) == "number" then
			copy.value = copy.value / 100
		elseif type(copy.value) == "table" and type(copy.value.mod) == "table"
				and type(copy.value.mod.value) == "number" then
			copy.value.mod.value = copy.value.mod.value / 100  -- (a LIST of one nested mod)
		end
		-- (COV31) the share first: PoB evaluates tags in order and a PercentStat tag rounds its product
		-- up (ModStore m_ceil), so a share after it would round 1/100 of the value up (The Lethal Draw: 100 for 92)
		table.insert(copy, 1, { type = "Multiplier", var = "LedgerShare" .. share })
		tagged[#tagged + 1] = copy
	end
	return tagged
end

-- The ledger's stand-ins for a socketed rune's / Idol's Bonded line PoB can't parse (COV13; builder/ledger/items.py):
-- the item text carries each as "LedgerBonded:<augment type> <line>", which PoB can't parse; before PoB builds the
-- item's modifiers, a copy of it goes in the item's rune lines as a Bonded line of that augment type, so PoB's own
-- Bonded switch (CanUseBonded from the tree, an Idol's SocketedIdolsUseBondedModifiers; Item.lua
-- GetActiveModListForSlotNum) decides whether it counts, and the socketed-rune effect scales it as the rune's own line.
-- UpdateRunes rebuilds the rune lines from the rune names, so the copies are made again on every build of the list.
local ledgerBondedInstalled = false
local function installLedgerBonded()
	local class = not ledgerBondedInstalled and common and common.classes and common.classes.Item
	if not class or not class.BuildModList then
		return
	end
	local buildModList = class.BuildModList
	class.BuildModList = function(self, ...)
		local runeLines, explicit = rawget(self, "runeModLines"), rawget(self, "explicitModLines")
		if runeLines and explicit then
			for i = #runeLines, 1, -1 do
				if runeLines[i].ledgerBonded then
					table.remove(runeLines, i)
				end
			end
			for _, modLine in ipairs(explicit) do
				local augment, rest = tostring(modLine.line or ""):match("^LedgerBonded:(%a+)%s+(.+)$")
				if augment then
					-- (COV32) a `share:` stand-in ("LedgerShare:<Name> <line>"): <line>'s modifiers at the share
					local share, inner = rest:match("^LedgerShare:(%w+)%s+(.+)$")
					local list, extra = modLib.parseMod(inner or rest)
					if list and not extra and share then
						list = ledgerShareMods(list, share)
					end
					if list and not extra then
						runeLines[#runeLines + 1] = { line = "Bonded: " .. rest, order = math.huge, modList = list,
							rune = true, enchant = true, bonded = true, augmentType = augment, ledgerBonded = true }
					end
				end
			end
		end
		return buildModList(self, ...)
	end
	ledgerBondedInstalled = true
end

-- The ledger's `share:` lines on items (COV17; builder/ledger item_lines): the item text carries each as
-- "LedgerShare:<Name> <line>", which PoB can't parse (its modLine keeps leftover text, so PoB applies nothing). Before PoB
-- builds the item's modifiers, such an explicit line gets <line>'s modifiers (at the item's roll, as getRangedModList
-- would give them), each at value / 100 x Multiplier:LedgerShare<Name>, the share (or count) in whole percent that the
-- assumptions set (assumptions.ledger_conditions), as applyLedger does for a passive's `share:` line. A line PoB still
-- can't parse stays as it was (applies nothing).
local ledgerShareInstalled = false
local function installLedgerShare()
	local class = not ledgerShareInstalled and common and common.classes and common.classes.Item
	if not class or not class.BuildModList then
		return
	end
	local buildModList = class.BuildModList
	class.BuildModList = function(self, ...)
		for _, kind in ipairs({ "explicitModLines", "implicitModLines" }) do
			for _, modLine in ipairs(rawget(self, kind) or { }) do
				local share, rest = tostring(modLine.line or ""):match("^LedgerShare:(%w+)%s+(.+)$")
				if share then
					if rest:find("%((%-?%d+%.?%d*)%-(%-?%d+%.?%d*)%)") then
						rest = itemLib.applyRange(rest, modLine.range or main.defaultItemAffixQuality or 0.5,
							modLine.valueScalar, modLine.corruptedRange)
					end
					local list, extra = modLib.parseMod(rest)
					if list and not extra then
						modLine.modList, modLine.extra, modLine.ledgerShare = ledgerShareMods(list, share), nil, share
					end
				end
			end
		end
		return buildModList(self, ...)
	end
	ledgerShareInstalled = true
end

-- A granted skill under the id the ledger names (COV30; ledger `pob_wrong` entries with `skill_id`, ledger.wrong): an
-- item line tagged "{tags:LedgerSkill_<id>}" keeps its own text ("Grants Skill: Level 20 Lightning Bolt", which PoB's
-- ModParser maps by lower-case name to one gem id, gemIdLookup), and the skill the item grants from it is <id>
-- instead, at the same level (item.grantedSkills, which CalcSetup reads; PoB's parsed mods stay as they are). The Voltaic Staff's castable Lightning Bolt is LightningBoltPlayer; PoB's
-- lookup ends on Choir of the Storm's triggered UniqueBreachLightningBoltPlayer for every carrier. PoB keeps the tag on the line (Item.lua 991-994) and writes it back on export.
local ledgerSkillInstalled = false
local function installLedgerSkill()
	local class = not ledgerSkillInstalled and common and common.classes and common.classes.Item
	if not class or not class.BuildModList then
		return
	end
	local buildModList = class.BuildModList
	class.BuildModList = function(self, ...)
		local result = buildModList(self, ...)
		-- After PoB's build: a ranged line ("Level (1-20)") gets a fresh modList inside it (getRangedModList), so the
		-- tagged lines' ExtraSkill values are read now, and the item's grantedSkills (built from baseModList's
		-- ExtraSkill list in order, skipping "Unknown": Item.lua 2845-2858) take the ledger's id where they came from one
		local wanted = { }
		for _, kind in ipairs({ "explicitModLines", "implicitModLines", "enchantModLines" }) do
			for _, modLine in ipairs(rawget(self, kind) or { }) do
				local skillId
				for _, tag in ipairs(modLine.modTags or { }) do
					skillId = skillId or tag:match("^LedgerSkill_(%w+)$")
				end
				if skillId and data.skills[skillId] then
					for _, parsed in ipairs(modLine.modList or { }) do
						if parsed.name == "ExtraSkill" and type(parsed.value) == "table" then
							wanted[parsed.value] = skillId
						end
					end
				end
			end
		end
		if next(wanted) and rawget(self, "baseModList") and rawget(self, "grantedSkills") then
			local i = 0
			for _, skill in ipairs(self.baseModList:List(nil, "ExtraSkill")) do
				if skill.name ~= "Unknown" then
					i = i + 1
					local granted = self.grantedSkills[i]
					if wanted[skill] and granted and granted.skillId == skill.skillId then
						granted.skillId = wanted[skill]
					end
				end
			end
		end
		return result
	end
	ledgerSkillInstalled = true
end

-- Whether PoB applies this rune line now: a Bonded line only under the item's current Bonded state, which
-- GetActiveModListForSlotNum sets on each calculation ("all": CanUseBonded; "idol": an Idol's own lines only)
local function runeLineApplies(item, modLine)
	if not modLine.bonded then
		return true
	end
	local state = rawget(item, "activeBondedState")
	return state == "all" or (state == "idol" and modLine.augmentType == "Idol")
end

-- Cost warnings in a what-if (SEARCH9; a stats flag the search needs): PoB lists the skills whose cost a pool can't pay
-- only in its MAIN-mode output (Calcs.lua 533-560, buildOutput), so a what-if (CALCULATOR mode, getMiscCalculator) never
-- had them, and the search couldn't see a Runic Ward cost it couldn't pay until it committed (2b75b's Transmutation
-- Explosion, MECHB12). Here a CALCULATOR output gets the same lists by PoB's own test, each skill's cost read from the
-- committed build's MAIN pass (GlobalCache.cachedData.MAIN, wiped at each rebuild) against this what-if's pools. A cost
-- the what-if itself changes is read for the main skill only (objective.cost_short); for other skills the committed cost.
local COST_POOLS = { LifeUnreserved = "LifeCost", ManaUnreserved = "ManaCost", Rage = "RageCost", Ward = "WardCost",
	EnergyShield = "ESCost" }
local function setCostShort(env)
	local output = env and env.mode == "CALCULATOR" and env.player and env.player.output
	local main = GlobalCache and GlobalCache.cachedData and GlobalCache.cachedData.MAIN
	if not output or not main or not cacheSkillUUID then
		return
	end
	local protects = env.modDB and env.modDB:Flag(nil, "EnergyShieldProtectsMana")
	local found = { }
	for _, skill in ipairs(env.player.activeSkillList or { }) do
		local ok, uuid = pcall(cacheSkillUUID, skill, env)
		local cached = ok and main[uuid]
		if cached and cached.Env and (not skill.triggeredBy or skill.triggeredBy.grantedEffect.id ~= "SupportBlasphemyPlayer") then
			for pool, costResource in pairs(COST_POOLS) do
				local cost = cached.Env.player.output[costResource]
				if cost then
					local totalPool = (protects and costResource == "ManaCost" and output.EnergyShield or 0) + (output[pool] or 0)
					if totalPool < cost then
						local rawPool = pool:gsub("Unreserved$", "")
						local reservation = cached.Env.player.mainSkill and cached.Env.player.mainSkill.skillData[rawPool .. "ReservedPercent"]
						if not reservation or (totalPool + math.ceil((output[rawPool] or 0) * reservation / 100)) < cost then
							found[costResource] = found[costResource] or { }
							table.insert(found[costResource], skill.activeEffect.grantedEffect.name)
						end
					end
				end
			end
		end
	end
	for costResource, list in pairs(found) do
		output[costResource .. "WarningList"] = output[costResource .. "WarningList"] or list
	end
end

-- Skills a support gem grants, at the supported skill's level (MECHB16, an UNDERCOUNT). PoB makes a support's granted
-- active skill (gemData.additionalGrantedEffects) at the support gem instance's own level (CalcSetup 2288, level =
-- gemInstance.level), and a PoE2 support has one level, so Poison Spores' Pustule, Impending Doom's Doom Blast and the
-- rest are scored at level 1. The game: such a skill has its own 40 levels (GrantedEffectsPerLevel and
-- GrantedEffectStatSetsPerLevel, game 26476662: e.g. TriggeredPoisonSporesPustule 40 rows against SupportPoisonSpores'
-- 1), and poe2db's Toxic Pustule reads "This Skill will copy the Level of the Gem it is Socketed in" (supports are
-- socketed in the skill gem they support). So a granted skill with more than one level takes the level of the first
-- active gem of its group, as PoB calculated it (with its +level mods); one with a single level (28 of the 61 support-
-- granted effects, which scale otherwise) is left as it is. HIGH: the user's in-game check (13 Nov: "the support gems
-- that trigger skills like doom blast do retain their parent skill gem levels"), poe2db's text (on Toxic Pustule, Doom
-- Blast and Living Lightning) and the 40-level game data. NEEDED for PoB's own damage, not only the bridge's reads
-- (MECHB18's probe POE2T1056, scratch/mechb18/probe_*.json: the same build with and without this wrapper): without it
-- Doom Blast hits for 11 (level 1) against 349, the Pustule Explosion's TotalDPS is 16 against 51 and its stored-Poison
-- stat reads 2 against 969. PoB's CalcActiveSkill 439-460 inheritance doesn't reach those calculations (MECHB17 read
-- that it did; the probe says otherwise). Living Lightning (a minion skill) and Creeping Chill (no level-scaled stat)
-- read the same either way.
-- SETTLED moot (RESEARCH26 R26-81, HIGH, unmarked-granted-skill-level-r26.md): their level rows are identical but for
-- ActorLevel (their damage comes from fixed stats), so the copied level moves nothing; Soulbreaker's 11% crit is at level
-- 1 only (unmodelled anywhere). MECHB17's old note, kept for the record: 7 of these 34 forty-level effects look structurally different. GrantedEffects'
-- unnamed column after the two effectiveness floats is 40 on the other 27 and on no ordinary gem, and 0 on Soulbreaker,
-- Burning Inscription, Hoarfrost, Savage Snare, Creeping Chill, Empowered Sparks' TriggeredSparkEmpowerPlayer and
-- Fragment Self. poe2db has no "copy the Level" line on Creeping Chill, Soulbreaker or Fragment Self. They keep the
-- copied level here (and in PoB) until an in-game check says otherwise (UNVERIFIED either way).
local grantedLevelInstalled, groupActiveLevel = false, setmetatable({ }, { __mode = "k" })
local function installGrantedLevel()
	if grantedLevelInstalled then
		return
	end
	local calcs = require("Modules.CalcBase")
	local original = calcs.createActiveSkill
	if not original then
		return
	end
	grantedLevelInstalled = true
	calcs.createActiveSkill = function(activeEffect, supportList, env, actor, socketGroup, ...)
		local src = activeEffect and activeEffect.srcInstance
		local gemData = src and src.gemData
		if socketGroup and gemData and gemData.grantedEffect and env then
			-- Weak keys on the inner table too (PERF8): a plain one holds its socket groups, a socket group reaches its
			-- env, and LuaJIT keeps a weak-keyed entry whose own value reaches its key, so every env (and its build)
			-- ever calculated stayed alive: about 6.5 MB per build load for the engine's life (PERF7's measure).
			local levels = groupActiveLevel[env] or setmetatable({ }, { __mode = "k" })
			groupActiveLevel[env] = levels
			if not gemData.grantedEffect.support then
				if activeEffect.grantedEffect == gemData.grantedEffect and not levels[socketGroup] then
					levels[socketGroup] = activeEffect.level
				end
			elseif activeEffect.grantedEffect and not activeEffect.grantedEffect.support
					and #activeEffect.grantedEffect.levels > 1 then
				local level = levels[socketGroup]
				if not level then
					for _, gem in ipairs(socketGroup.gemList or { }) do
						if gem.enabled and gem.gemData and gem.gemData.grantedEffect and not gem.gemData.grantedEffect.support then
							level = gem.level
							break
						end
					end
				end
				if level and activeEffect.grantedEffect.levels[level] then
					activeEffect.level = level
				elseif level then
					activeEffect.level = math.max(1, math.min(level, #activeEffect.grantedEffect.levels))
				end
			end
		end
		return original(activeEffect, supportList, env, actor, socketGroup, ...)
	end
end

-- The skill amulets' granted skills reserve nothing on every path (MECHC52; THE USER, 21 Nov ~23:58: "make sure this
-- applies to absent/lament/portent amulets as well. those all grant spirit reservation skills, but don't cost spirit";
-- RESEARCH45 R45-6a, HIGH: the game flag on exactly these three bases, PoB's own Data/Bases/amulet.lua
-- grantedSkillsHaveNoReservation). PoB passes the flag to the granted gem instance (Classes/Item.lua 2852 ->
-- CalcSetup 1962 srcInstance.noReservation) and zeroes the reservation in calcs.buildActiveSkillModList's main path
-- (CalcActiveSkill 822-826), but 9 of the 51 granted skills (the five Lament Heralds, Wind Dancer, Shard Scavenger,
-- Portent's Mirage Archer and Feral Invocation; SEARCHB2's POE2T6253) still reserved 30 / 60 Spirit: MECHC52's probes
-- POE2T4989 / 4990 / 4993 read their activeEffect.srcInstance.noReservation TRUE but their grantedEffectLevel unzeroed
-- (spiritReservationFlat 30 / 60, manaReservationFlat absent), against Arctic Armour's zeroed one. THE PATH: on a build
-- whose weapon the skill can't use (the probe's wand: "Main Hand weapon is not usable with this skill"), PoB disables the
-- skill, and buildActiveSkillModList's disabled branch (CalcActiveSkill 731-742) copies the level table WITHOUT the
-- noReservation zero and returns; CalcDefence 205-225 still reserves for a disabled skill. So after PoB builds each
-- active skill, an effect whose source instance carries the
-- flag has every Mana / Life / Spirit reservation field zeroed in its level table and its skillData (the keys CalcDefence
-- reads first), whatever path built it. Supports the player sockets on the granted skill keep their own added
-- reservation (ExtraSpirit: UNVERIFIED whether the game waives it too; PoB's left).
local noReservationInstalled = false
local function installNoReservation()
	if noReservationInstalled then
		return
	end
	local calcs = require("Modules.CalcBase")
	local original = calcs.buildActiveSkillModList
	if not original then
		return
	end
	noReservationInstalled = true
	calcs.buildActiveSkillModList = function(env, activeSkill, ...)
		local results = { original(env, activeSkill, ...) }
		local effect = activeSkill and activeSkill.activeEffect
		if effect and effect.srcInstance and effect.srcInstance.noReservation then
			for _, resource in ipairs({ "mana", "life", "spirit" }) do
				for _, kind in ipairs({ "ReservationFlat", "ReservationPercent" }) do
					if effect.grantedEffectLevel then
						effect.grantedEffectLevel[resource .. kind] = 0
					end
					if activeSkill.skillData and activeSkill.skillData[resource .. kind] then
						activeSkill.skillData[resource .. kind] = 0
					end
				end
			end
		end
		return unpack(results)
	end
end

-- One stat set, two stats, one skillData slot (PERF21). PoB merges a stat set's stats in `pairs(stats)`
-- (CalcActiveSkill.lua mergeStatSet, line 87), a text-keyed table whose order LuaJIT picks anew in each Lua state, and
-- skillData keeps the LAST SkillData mod of a key (CalcActiveSkill.lua 919-920). So where two stats of one set map to
-- the same key with different values, the same build reads one value in one PoB process and the other in the next.
-- bridge.statSetSlots (below) finds every such set in PoB's data: two of 1,617 (POE2T2847, gem levels 1..40, quality
-- 0 and 20), both on "radius"; no OVERRIDE mod and no other keyed list is written twice by one set. Each entry here
-- leaves the slot one writer, the game's ("drop": the stat's own map goes and it falls back to PoB's global map,
-- Data/SkillStatMap.lua through data.skillStatMapMeta; "mute": the stat maps to nothing in this set):
-- - Earthquake's Aftershock (set 2): PoB's own map sends active_skill_base_tertiary_area_of_effect_radius (18) to
--   "radius", where the global map already sends active_skill_base_area_of_effect_radius (18 from the Impact set's
--   constants + 6..12 of the set's own at gem levels 1..20). Seen: AreaOfEffectRadius 18 in 6 PoB processes, 30 in 2
--   (PERF20, POE2T2830-2837). The game: the base stat is "Aftershock radius is {0} metres" and the tertiary one
--   "Jagged Ground radius is {0} metres" (statdescriptions/specific_skill_stat_descriptions/earthquake/statset_1.csd);
--   GrantedEffectStatSetsPerLevel adds 6..12 for EarthquakeAftershockPlayer to EarthquakePlayer's constant 18; patch
--   notes 0.3.0: Aftershock "has a radius of 2.4-3 metres at Gem levels 1-20", "Jagged Ground's radius is now 1.8
--   metres" (poe2db lists the set's own addition alone, "(0.6-1.2) metres"). Dropped: the tertiary stat is PoB's
--   radiusTertiary (the Jagged Ground, 18) and radius is 30 at gem level 20 in every process.
-- - Acidic Concoction's Acidic Burst (set 2), with quality on the gem: PoB adds the gem's quality stat
--   (active_skill_base_area_of_effect_radius, 0.2 per 1% quality) to every set (CalcTools.lua buildSkillInstanceStats
--   163-169), so the Burst's radius is its own 15 (active_skill_base_secondary_area_of_effect_radius, the set's map)
--   or the quality's 4 by the state's order. Not seen in a run; found by the scan. The game: the quality stat applies
--   to stat set 0, the Flask (GrantedEffectQualityStats ApplyToStatSets [0]), the Burst's set ignores the base radius
--   stat (GrantedEffectStatSets AcidicConcoctionPoisonBurstPlayer IgnoredStats), and "Burst radius is {0} metres" is
--   the secondary stat (acidic_concoction.csd); poe2db: "Burst radius is 1.5 metres", quality "Explosion radius is
--   (0-0.4) metres". Muted: the Burst's radius is 15 in every process.
-- tests/test_stat_set_slots.py fails on a set with this pattern that is not handled here (a PoB upgrade's).
local STAT_SET_SLOT_FIXES = {
	EarthquakePlayer = { [2] = { drop = { "active_skill_base_tertiary_area_of_effect_radius" } } },
	AcidicConcoctionPlayer = { [2] = { mute = { "active_skill_base_area_of_effect_radius" } } },
}
local statSetSlotsInstalled = false
local function installStatSetSlots()
	if statSetSlotsInstalled or not (data and data.skills and data.skillStatMapMeta) then
		return
	end
	statSetSlotsInstalled = true
	for id, sets in pairs(STAT_SET_SLOT_FIXES) do  -- (each entry stands alone: this order changes nothing)
		local effect = data.skills[id]
		for index, fix in pairs(sets) do
			local set = effect and effect.statSets and effect.statSets[index]
			if set then
				for _, stat in ipairs(fix.drop or { }) do
					rawset(set.statMap, stat, nil)
				end
				for _, stat in ipairs(fix.mute or { }) do
					rawset(set.statMap, stat, { })
				end
			end
		end
	end
end

-- Every stat set in PoB's data (skills and supports) in which two stats write one last-one-wins slot with different
-- values through the set's stat map, at any gem level the set has, at quality 0 or 20: a skillData key, an OVERRIDE
-- mod's name, or a keyed list entry (alone or inside a MinionModifier-style wrapper). Sums and products are left out:
-- their order picks no winner. After installStatSetSlots, so a healthy PoB answers no slots.
function bridge.statSetSlots()
	installStatSetSlots()
	local function slotOf(mod)
		if mod.name == "SkillData" and type(mod.value) == "table" then
			return "SkillData:" .. tostring(mod.value.key)
		elseif mod.type == "OVERRIDE" then
			return "OVERRIDE:" .. tostring(mod.name)
		elseif mod.type == "LIST" and type(mod.value) == "table" then
			local inner = mod.value.mod
			if inner and (inner.type == "LIST" or inner.type == "OVERRIDE") then
				local key = type(inner.value) == "table" and inner.value.key
				return "LIST:" .. tostring(mod.name) .. ":" .. tostring(inner.name) .. ":" .. inner.type .. (key and (":" .. tostring(key)) or "")
			elseif not inner and mod.value.key then
				return "LIST:" .. tostring(mod.name) .. ":" .. tostring(mod.value.key)
			end
		end
	end
	local ids, found, sets = { }, { }, 0
	for id in pairs(data.skills) do
		ids[#ids + 1] = id
	end
	table.sort(ids)
	for _, id in ipairs(ids) do
		local effect = data.skills[id]
		for setIndex, set in ipairs(effect.statSets or { }) do
			sets = sets + 1
			local levels, slots = { }, { }  -- slots: slot -> stat -> "level/quality" -> value
			for level in pairs(set.levels or { }) do
				levels[#levels + 1] = level
			end
			levels[1] = levels[1] or 1
			for _, level in ipairs(levels) do
				for _, quality in ipairs({ 0, 20 }) do
					local stats = calcLib.buildSkillInstanceStats({ level = level, quality = quality, grantedEffect = effect, actorLevel = 100 }, effect, set, true)
					for stat, statValue in pairs(stats) do
						for _, modOrGroup in ipairs(set.statMap[stat] or { }) do
							local holder = modOrGroup.name and set.statMap[stat] or modOrGroup
							for _, mod in ipairs(modOrGroup.name and { modOrGroup } or modOrGroup) do
								local slot = slotOf(mod)
								if slot then
									slots[slot] = slots[slot] or { }
									slots[slot][stat] = slots[slot][stat] or { }
									slots[slot][stat][level .. "/" .. quality] = holder.value or statValue * (holder.mult or 1) / (holder.div or 1) + (holder.base or 0)
								end
							end
						end
					end
				end
			end
			for slot, byStat in pairs(slots) do
				local stats, differ = { }, false
				for stat in pairs(byStat) do
					stats[#stats + 1] = stat
				end
				table.sort(stats)
				for i = 2, #stats do
					for at, value in pairs(byStat[stats[i]]) do
						differ = differ or (byStat[stats[1]][at] ~= nil and byStat[stats[1]][at] ~= value)
					end
				end
				if differ then
					found[#found + 1] = { skill = id, set = setIndex, slot = slot, stats = stats }
				end
			end
		end
	end
	table.sort(found, function(a, b)
		return a.skill .. a.set .. a.slot < b.skill .. b.set .. b.slot
	end)
	return { sets = sets, slots = found }
end

-- The Concoctions are not Unarmed Attacks (GEM30; plans/kw-unarmed.md, COVC3's open item; an OVERCOUNT). The game's
-- text on all five (ActiveSkills; PoB other.lua's descriptions): "This damage is not based on your unarmed damage, so
-- this is not considered an Unarmed Attack"; their stat sets carry replace_main_hand_unarmed_attack_stats_with_nothing_type
-- (a "nothing" weapon in place of the unarmed one) and main_hand_base_weapon_attack_duration_ms 714. PoB reads neither
-- (checked: grep of Modules/ and Data/SkillStatMap.lua: only the off-hand duration is mapped, 2198). Their weaponTypes
-- are { None }, so PoB attacks with weaponData1, the class's unarmed weapon (Data.lua 626-636: 2-5 to 2-8 Physical, 1.65
-- a second) with its ModFlag.Unarmed (Data.lua 606), and every "with Unarmed Attacks" line applied (probe POE2T5792:
-- "100% increased Damage with Unarmed Attacks" took Explosive Concoction's hit 313 -> 626, Acidic's 97 -> 292; "Adds 100
-- to 100 Physical Damage to Unarmed Attacks" +30 / +71). So, after PoB builds the skill's modifier list: ModFlag.Unarmed
-- comes off its weapon flags and configs (lines for any Attack still apply); the unarmed base damage is cancelled by a
-- BASE minus the class's unarmed Physical, which PoB sums with the skill's own main_hand_weapon damage before the
-- skill's multiplier (CalcOffence 4135; an "added damage" multiplier, rare, would leave a sliver: named). Its attack
-- rate is left as PoB has it: the unarmed 1.65 x the skill's attackSpeedMultiplier -15 = 1.4025 a second against
-- 1000 / 714 ms = 1.4006 (entry gem-active-pob-concoction-attack-time; whether the game also applies the -15 to the
-- 714 ms, 1.19 a second, is UNVERIFIED). The five by id; the stat's other carriers (Caltrops, Trail
-- of Caltrops, Blood Explosion, the Heralds, Fan the Flames ...) are left as they are (whether they use weapon damage is
-- UNVERIFIED, COVC3). With a weapon held PoB disables a Concoction (weaponTypes None): not changed here (UNVERIFIED
-- whether the game lets you throw one with a weapon in hand: a research question).
local CONCOCTIONS = { AcidicConcoctionPlayer = true, BleedingConcoctionPlayer = true, ExplosiveConcoctionPlayer = true,
	FulminatingConcoctionPlayer = true, ShatteringConcoctionPlayer = true }
local function installConcoctionsNotUnarmed()
	local calcs = require("Modules.CalcBase")
	if calcs.ledgerConcoctions or not calcs.buildActiveSkillModList then
		return
	end
	calcs.ledgerConcoctions = true
	local bit = require("bit")
	local buildModList = calcs.buildActiveSkillModList
	calcs.buildActiveSkillModList = function(env, activeSkill)
		buildModList(env, activeSkill)
		local effect = activeSkill.activeEffect and activeSkill.activeEffect.grantedEffect
		if not (effect and CONCOCTIONS[effect.id]) or activeSkill.actor ~= env.player then
			return
		end
		local keep = bit.bnot(ModFlag.Unarmed)
		activeSkill.weapon1Flags = bit.band(activeSkill.weapon1Flags or 0, keep)
		activeSkill.weapon2Flags = bit.band(activeSkill.weapon2Flags or 0, keep)
		for _, key in ipairs({ "skillCfg", "weapon1Cfg", "weapon2Cfg" }) do
			local cfg = activeSkill[key]
			if cfg then
				cfg.flags = bit.band(cfg.flags or 0, keep)
			end
		end
		local weapon = activeSkill.actor.weaponData1
		if weapon and weapon.type == "None" and activeSkill.skillModList then
			local list, source = activeSkill.skillModList, "Concoction: not an Unarmed Attack (GEM30)"
			list:NewMod("PhysicalMin", "BASE", -(weapon.PhysicalMin or 0), source, 0, KeywordFlag.Attack)
			list:NewMod("PhysicalMax", "BASE", -(weapon.PhysicalMax or 0), source, 0, KeywordFlag.Attack)
		end
	end
end

local function installDerived()
	installStatSetSlots()
	installLedgerBonded()
	installLedgerShare()
	installLedgerSkill()
	installSpiritVessel()
	installSkyfallMinion()
	installCannotChill()
	installArcticArmourBurst()
	installAreaDamageFlag()
	installCorpseExplosionHits()
	installAttackSetFlags()
	installBossSlow()
	installNoCritReroll()
	installBaseChanceSwap()
	installExactDpsMore()  -- (GEM40: our DPS MORE lines exact, PoB's own rounded)
	installGrantedLevel()
	installNoReservation()  -- (MECHC52: the skill amulets' granted skills reserve nothing)
	installConcoctionsNotUnarmed()
	local calcs = require("Modules.CalcBase")
	if calcs.perform == wrappedPerform or not calcs.perform then
		return
	end
	local perform = calcs.perform
	wrappedPerform = function(env, ...)
		setDerived(env)
		setAugmentCounts(env)
		setNoCritReroll(env)
		setBaseChanceSwap(env)
		local function finish(...)
			setLowLifeCap(env, env and env.player and env.player.output)
			setEsBypassBelowHalf(env, env and env.player and env.player.output)
			setEverlastingSacrifice(env, env and env.player and env.player.output)
			setLifeRecharge(env)
			setManaRegenOnly(env)
			markShowAverage(env)
			setChillDamage(env)
			setImpale(env)
			-- (MECH76: the monster level PoB scored the enemy at, CalcSetup 875's env.enemyLevel: the config's Enemy Level,
			-- else the boss type's placeholder (82 for the Guardian / Pinnacle default), else min(85, character level);
			-- assumptions.enemy_level reads it instead of guessing)
			if env and env.player and env.player.output then
				env.player.output.LedgerEnemyLevel = env.enemyLevel
				-- (MECH76: whether the main skill hits with Unarmed Melee damage, for Crackling Palm's bolts,
				-- conditions.crackling_palm_rate: a Melee Attack with the Unarmed flag or no main-hand weapon)
				local main = env.player.mainSkill
				-- (MECH77: current PoB keeps the flags on the stat set, as activeSkills' "disabled" reads them; the
				-- top-level skillFlags alone read 0 for every skill, Killing Palm with Hollow Palm Technique included,
				-- probe POE2T3109)
				local flags = main and (main.skillFlags or (main.activeEffect and main.activeEffect.statSet or { }).skillFlags) or { }
				local unarmed = flags.unarmed or not (env.player.itemList and env.player.itemList["Weapon 1"])
				env.player.output.LedgerUnarmedMelee = (flags.attack and flags.melee and unarmed) and 1 or 0
				-- (PREVIEW5: the main skill's damage effectiveness at its gem level, the same expression CalcOffence's base
				-- hit damage uses (activeEffect.grantedEffectLevel.baseMultiplier, else skillData.baseMultiplier, else 1);
				-- the 1.0 giant sword's yardstick for a current-game Strike, builder/preview/supports.py; read only there)
				local level = main and main.activeEffect and main.activeEffect.grantedEffectLevel
				env.player.output.LedgerBaseMultiplier = main and ((level and level.baseMultiplier)
					or (main.skillData and main.skillData.baseMultiplier) or 1) or nil
			end
			setPointsOver(env)
			setCostShort(env)
			return ...
		end
		return finish(perform(env, ...))
	end
	calcs.perform = wrappedPerform
end

-- The output-derived inputs a what-if's own output gives (nil when there are none)
local function deriveFrom(output)
	local found
	for _, input in ipairs(derivedInputs) do
		local fn = DERIVED[input.fn]
		if fn then
			found = found or { }
			found[input.name] = fn(output, input.args or { })
		end
	end
	return found
end

-- A what-if's calculation with its own model inputs: `run` calculates with the build's values;
-- if the output moves an output-derived input (beyond rounding), once more with its own. Adds
-- LedgerAddedDPS (the sum of the ADDED_DPS sources: Melting Maelstrom's pulses, models.added_input's) to the picked
-- stats when a model reports one.
local function derivedCalc(run, fields)
	local output = run()
	local found = deriveFrom(output)
	if found then
		local moved = false
		for _, input in ipairs(derivedInputs) do
			local value = found[input.name]
			if value ~= nil and not ADDED_DPS[input.fn] then
				local was = derivedValues[input.name] or 0
				if math.abs(value - was) > math.max(0.5, 0.005 * math.abs(was)) then
					moved = true
				end
			end
		end
		if moved then
			local saved = derivedValues
			derivedValues = setmetatable(found, { __index = saved })
			local ok, second = pcall(run)
			derivedValues = saved
			if not ok then
				error(second, 0)
			end
			output = second
		end
	end
	local picked = pickStats(output, fields)
	picked.LedgerAddedDPS = nil  -- (SEARCH29: pickStats' own sum is the first output's; the what-if's is summed here, as before)
	for _, input in ipairs(derivedInputs) do
		if ADDED_DPS[input.fn] then  -- (MECHB20: every added source summed, not only Melting Maelstrom's pulses)
			picked.LedgerAddedDPS = (picked.LedgerAddedDPS or 0)
				+ (found and found[input.name] or derivedValues[input.name] or 0) / 100
		end
	end
	return picked
end

local function calculator()
	installRadiusModel()
	installDerived()
	local calcFunc, calcBase = build.calcsTab:GetMiscCalculator()
	return calcFunc, calcBase
end

-- Full recalculation through PoB's normal path, as after any edit in the GUI
local function recalc()
	installRadiusModel()
	installDerived()
	build.buildFlag = true
	runCallback("OnFrame")
end

function bridge.summary()
	local used, ascUsed = countAllocated()
	return {
		className = build.spec.curClassName,
		ascendClassName = build.spec.curAscendClassName,
		level = build.characterLevel,
		treeVersion = build.spec.treeVersion,
		mainSkill = mainSkillName(),
		allocated = used,
		ascendancyAllocated = ascUsed,
		outputRevision = build.outputRevision,
	}
end

-- PoB's skill cache (GlobalCache) is keyed by skill name and slot and isn't wiped when a build
-- is loaded, so the next build's first calculation could read another build's cached skill (a
-- weapon-set-2 Ice Shot's Mana cost, seen in gate 5). Every new or loaded build starts clean.
function bridge.newBuild()
	wipeGlobalCache()
	newBuild()
	return bridge.summary()
end

-- Traveller's Wisdom choices aren't in PoB's format (it saves Strength, Dexterity or
-- Intelligence per attribute node), so the export adds them as an element PoB ignores
local function applyTravellerChoices(choices)
	local spec = build.spec
	for id, attribute in pairs(choices) do
		local node = spec.nodes[id]
		if node and node.alloc and node.isAttribute then
			spec.hashOverrides[id] = attributeNode(spec, id, attribute)
		end
	end
	spec:BuildAllDependsAndPaths()
end

function bridge.loadXml(args)
	wipeGlobalCache()
	installDerived()  -- (the load calculates: a fresh engine's first load gets the derived outputs too, PassivePointsOver among them)
	loadBuildFromXML(args.xml, args.name or "build")
	if __mainObject__.promptMsg then
		error("PoB rejected the build: " .. tostring(__mainObject__.promptMsg))
	end
	local saved = args.xml:match('<BuilderTraveller choices="([^"]*)"')
	if saved and saved ~= "" then
		local choices = { }
		for id, attribute in saved:gmatch("(%d+):(%d+)") do
			choices[tonumber(id)] = tonumber(attribute)
		end
		applyTravellerChoices(choices)
		recalc()
	elseif radiusIncrease(nil) > 0 then
		recalc()  -- the load calculated before the radius model was back in place
	end
	return bridge.summary()
end

function bridge.exportXml()
	local xml = build:SaveDB("code")
	local choices = { }
	for id, override in pairs(build.spec.hashOverrides) do
		if override.travellerChoice and build.spec.allocNodes[id] then
			choices[#choices + 1] = id .. ":" .. override.travellerChoice
		end
	end
	if #choices > 0 then
		table.sort(choices)
		xml = xml:gsub("</PathOfBuilding2>%s*$", '<BuilderTraveller choices="' .. table.concat(choices, ",")
			.. '"/>\n</PathOfBuilding2>')
	end
	return { xml = xml }
end

-- Stats from the GUI's main output (what the sidebar and saved PlayerStats show)
function bridge.stats(args)
	return pickStats(build.calcsTab.mainOutput, args.fields)
end

-- (MECH104; COV54's probe POE2T8010) Whether PoB's own Aggravated / Moving bonus is on in the scored build (its
-- AilmentMagnitude MORE 100, CalcSetup 1000: the enemy Moving or BleedAggravated, from the config boxes or a
-- "Bleeding you inflict ... is Aggravated" line whose condition holds), and whether the build's Bleed can take no such
-- extra at all (NoExtraBleedDamageToMovingEnemy). A model's "more Magnitude of Bleeding" line would multiply that bonus.
function bridge.bleedExtra()
	local env = build.calcsTab and build.calcsTab.mainEnv
	if not (env and env.enemy and env.player) then
		return { aggravated = false, blocked = false }
	end
	local enemy = env.enemy.modDB
	return { aggravated = (enemy:GetCondition("Moving") or enemy:GetCondition("BleedAggravated")) and true or false,
		blocked = env.player.modDB:GetCondition("NoExtraBleedDamageToMovingEnemy") and true or false }
end

-- (MECH105, R55-1) Heart of Ice's Chill, which PoB doesn't score: its stat set has no hit flag, so the group's Cold hit is 0
-- (probe POE2T6527). Its text: "Chills enemies in your Presence as though dealing {0} to {1} Cold damage", on the ordinary
-- spell base-damage stats. Returned: its base at the level PoB gave it, the build's increased / more Spell and Cold damage
-- (a Spell config, without the Hit flag: "as though dealing" isn't a hit, so "with Hits" lines aren't taken, the lower
-- reading), its Chill Magnitude (CalcOffence 5687-5689's sum, with the gem's quality) and the boss's Cold resistance
-- (calcResistForType's sum, 695-698; no penetration), for conditions.heart_of_ice. found = false without the skill.
function bridge.heartOfIce()
	local env = build.calcsTab and build.calcsTab.mainEnv
	if not (env and env.player) then
		return { found = false }
	end
	for _, skill in ipairs(env.player.activeSkillList or { }) do
		local effect = skill.activeEffect
		if effect and effect.grantedEffect and effect.grantedEffect.id == "HeartOfIcePlayer" then
			local cfg = copyTable(skill.skillCfg or { }, true)
			cfg.flags = bit.bor(cfg.flags or 0, ModFlag.Spell)
			local mods, enemy = skill.skillModList, env.enemyDB
			local base = skill.skillData or { }
			local resist = enemy:Override(nil, "ColdResist") or (enemy:Sum("BASE", nil, "ColdResist", "ElementalResist")
				* math.max(calcLib.mod(enemy, nil, "ColdResist", "ElementalResist"), 0))
			return { found = true, level = effect.level, min = jsonNumber(base.ColdMin or 0), max = jsonNumber(base.ColdMax or 0),
				inc = jsonNumber(mods:Sum("INC", cfg, "Damage", "ColdDamage", "ElementalDamage")),
				more = jsonNumber(mods:More(cfg, "Damage", "ColdDamage", "ElementalDamage")),
				chillInc = jsonNumber(mods:Sum("INC", cfg, "EnemyChillMagnitude", "AilmentMagnitude")),
				chillMore = jsonNumber(mods:More(cfg, "EnemyChillMagnitude")),
				resist = jsonNumber(math.max(math.min(resist, data.misc.EnemyMaxResist), data.misc.ResistFloor)) }
		end
	end
	return { found = false }
end

function bridge.recalc(args)
	local start = os.clock()
	recalc()
	return { seconds = os.clock() - start, stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Unallocated nodes reachable from the current tree, with their paths. With `ascendancy`, lists
-- the selected ascendancy's nodes instead of the main tree's; `sockets = "only"` lists jewel
-- sockets (which have no stats of their own) instead. `maxDistanceByType` overrides the
-- distance limit per node type (e.g. reach further for notables than for small nodes).
function bridge.frontier(args)
	local maxDistance = args.maxDistance or 3
	local byType = args.maxDistanceByType or { }
	local ascendancy = args.ascendancy and build.spec.curAscendClassName or nil
	local nodes = { }
	for id, node in pairs(build.spec.nodes) do
		local limit = byType[node.type] or maxDistance
		local wanted = (node.modKey and node.modKey ~= "") or (args.sockets and node.type == "Socket")
		if args.sockets == "only" then
			wanted = node.type == "Socket"
		end
		-- A node a jewel lets the tree take without a connection costs one point, however far it is
		local unconnected = not node.alloc and node.path and unconnectedOk(node)
		local distance = unconnected and 1 or node.pathDist
		if not node.alloc and node.path and distance and distance <= limit
			and wanted and node.ascendancyName == ascendancy
			and not node.isMultipleChoiceOption then
			local path = { }
			for _, pathNode in ipairs(unconnected and { node } or node.path) do
				path[#path + 1] = pathNode.id
			end
			nodes[#nodes + 1] = {
				id = id, name = node.dn, type = node.type, distance = distance,
				path = path, modKey = node.modKey, unconnected = unconnected or nil,
			}
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	return { nodes = nodes }
end

-- Allocated nodes that can be removed, with the nodes that would go with them. A passive an item
-- grants (an anoint's "Allocates X") is left out: it costs no point and the tree can't remove it
function bridge.allocatedNodes()
	local nodes = { }
	for id, node in pairs(build.spec.allocNodes) do
		if node.type ~= "ClassStart" and node.type ~= "AscendClassStart" and not node.ascendancyName
			and not (node.isGrantedPassive and node.isFreeAllocate) then
			local depends = { }
			for _, dep in ipairs(node.depends or { }) do
				depends[#depends + 1] = dep.id
			end
			nodes[#nodes + 1] = { id = id, name = node.dn, type = node.type, depends = depends }
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	return { nodes = nodes }
end

-- (SEARCH34) Every allocated node that holds a jewel slot, with what it accepts: a tree socket (type "Socket"), an
-- ascendancy notable with a jewel socket (containJewelSocket: Lich's Crystalline Phylactery, basic non-unique jewels only,
-- ItemsTab.lua 2654-2659), a Sinister Jewel Socket (sinister: non-unique only, ItemsTab 2651; Zarokh's Gift's anoint and
-- the sockets an item allocates are granted passives). allocatedNodes leaves ascendancy and granted nodes out (it is the
-- tree search's list), so these slots were never filled: PLANS6 kw-basic-jewel, the manager's 03:05 / 03:25 notes
function bridge.jewelSockets()
	local sockets = { }
	for id, node in pairs(build.spec.allocNodes) do
		if node.type == "Socket" or node.containJewelSocket then
			sockets[#sockets + 1] = { id = id, name = node.dn, basicOnly = node.containJewelSocket and true or false,
				nonUnique = (node.containJewelSocket or node.sinister) and true or false, noRadius = node.noRadius and true or false,
				granted = node.isGrantedPassive and true or false, ascendancy = node.ascendancyName ~= nil }
		end
	end
	table.sort(sockets, function(a, b) return a.id < b.id end)
	return { sockets = sockets }
end

-- The lines of the nodes a socketed Timeless jewel conquered (PassiveSpec replaces a conquered keystone by its
-- legion keystone; the search's tree reads the raw tree's lines, SEARCH6). (MECHB20: the old example here, Dance with
-- Death's "Can't use Helmets", is PoE1 Maraketh data left in PoB's files, not a PoE2 keystone: the user's note
-- poe1-leftovers and SEARCH13's relabel of its own files)
function bridge.conqueredNodes()
	local nodes = { }
	for id, node in pairs(build.spec.nodes) do
		if node.conqueredBy and node.type ~= "Socket" then
			nodes[#nodes + 1] = { id = id, name = node.dn, lines = node.sd or { } }
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	return { nodes = nodes }
end

-- What-if passive changes. Each case: { add = {ids}, remove = {ids}, addPaths = bool,
-- attribute = 1|2|3, switch = {id, attribute} }. With addPaths, each added id brings its whole
-- path from the allocated tree; attribute nodes on it take the given option (default 1, as
-- allocation does). `switch` asks what an allocated attribute node would give with another
-- attribute chosen.
function bridge.nodeDeltas(args)
	local calcFunc = calculator()
	local useFullDPS = args.fullDPS ~= false
	local results = { }
	local start = os.clock()
	for i, case in ipairs(args.cases) do
		local override = { }
		if case.add and #case.add > 0 then
			override.addNodes = nodeSet(case.add, case.addPaths, case.attribute or 1)
		end
		if case.remove and #case.remove > 0 then
			override.removeNodes = nodeSet(case.remove, false)
		end
		if case.switch then
			local node = nodeById(case.switch[1])
			override.removeNodes = override.removeNodes or { }
			override.removeNodes[node] = true
			override.addNodes = override.addNodes or { }
			override.addNodes[withAttribute(node, case.switch[2], true)] = true
		end
		results[i] = derivedCalc(function() return calcFunc(override, useFullDPS) end, args.fields)
	end
	return { results = results, seconds = os.clock() - start }
end

-- What-if item swaps. Each case: { slot = "Body Armour", raw = "<item text>" }
function bridge.itemDeltas(args)
	local calcFunc = calculator()
	local useFullDPS = args.fullDPS ~= false
	local results = { }
	local start = os.clock()
	for i, case in ipairs(args.cases) do
		local item = makeItem(case.raw)
		-- A flask or charm counts only in an active slot; `active` sets that for this what-if alone
		local slot = case.active ~= nil and build.itemsTab.slots[case.slot]
		local wasActive = slot and slot.active
		if slot then
			slot.active = case.active
		end
		results[i] = derivedCalc(function() return calcFunc({ repSlotName = case.slot, repItem = item }, useFullDPS) end, args.fields)
		if slot then
			slot.active = wasActive
		end
	end
	return { results = results, seconds = os.clock() - start }
end

-- Character rune sockets (SEARCH22; PATCH_CHECK H5-4): Martial Artist's Runic Meridians ("Can tattoo Runes onto your
-- body, gaining additional Rune-only sockets: 1 Helmet socket, 2 Body Armour sockets, 1 Gloves socket, 1 Boots socket";
-- PoB's flag SocketRunesOnCharacter, ModParser 6353). PoB adds the rune chosen in each ItemsTab rune slot's list
-- (characterRuneSlotList: runes with canSocketInChakraSlots, not socket-bound, of the slot's type or "armour") to its
-- item mods (CalcSetup 1195-1207); a what-if replaces one slot's rune (override repSlotName / repRune).
local function runeSlotEntry(slot, name)
	for _, rune in ipairs(slot.list or { }) do
		if rune.name == name then
			return rune
		end
	end
end

function bridge.characterRunes()
	local env = build.calcsTab.mainEnv
	local active = env and env.modDB:Flag(nil, "SocketRunesOnCharacter") or false
	local slots = { }
	for _, slotName in ipairs(build.itemsTab.runeSlotOrder or { }) do
		local slot = build.itemsTab.runeSlots[slotName]
		local names = { }
		for _, rune in ipairs(slot.list or { }) do
			if rune.name ~= "None" then
				names[#names + 1] = { name = rune.name, limit = rune.limit, level = rune.req }
			end
		end
		-- (MECH88: the slot's list comes in an order that changes between processes, which went stale in recordings,
		-- POE2T4124: sorted by name, then level, for a steady answer; nothing reads a position in it)
		table.sort(names, function(a, b)
			if a.name ~= b.name then return tostring(a.name) < tostring(b.name) end
			return (tonumber(a.level) or 0) < (tonumber(b.level) or 0)
		end)
		slots[#slots + 1] = { slot = slotName, rune = (slot:GetSelValue() or { }).name or "None", runes = names }
	end
	return { active = active, slots = slots }
end

-- What-if runes in character rune slots. Each case: { slot = "Boots Rune #1", rune = "<name>" or "None" }
function bridge.characterRuneDeltas(args)
	local calcFunc = calculator()
	local useFullDPS = args.fullDPS ~= false
	local results = { }
	local start = os.clock()
	for i, case in ipairs(args.cases) do
		local slot = build.itemsTab.runeSlots[case.slot]
		local rune = slot and runeSlotEntry(slot, case.rune or "None")
		if rune then
			results[i] = derivedCalc(function() return calcFunc({ repSlotName = case.slot, repRune = rune }, useFullDPS) end, args.fields)
		else
			results[i] = { error = "no such rune slot or rune: " .. tostring(case.slot) .. " / " .. tostring(case.rune) }
		end
	end
	return { results = results, seconds = os.clock() - start }
end

-- Commit a rune into a character rune slot (saved in the item set: ItemsTab's RuneSlot elements), then recalculate
function bridge.setCharacterRune(args)
	local slot = build.itemsTab.runeSlots[args.slot]
	local rune = slot and runeSlotEntry(slot, args.rune or "None")
	if not rune then
		error("no such rune slot or rune: " .. tostring(args.slot) .. " / " .. tostring(args.rune))
	end
	slot:SelByValue(rune.name, "name")
	build.itemsTab.activeItemSet[args.slot] = { runeName = rune.name }
	local start = os.clock()
	recalc()
	return { seconds = os.clock() - start, summary = bridge.summary(), stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Commit passive changes: allocate each id with its path, deallocate each removal with its
-- dependents, then recalculate fully. Attribute nodes on new paths take `attribute` (default 1).
function bridge.allocate(args)
	local spec = build.spec
	spec.attributeIndex = args.attribute or 1
	for _, id in ipairs(args.remove or { }) do
		local node = nodeById(id)
		if node.alloc then
			spec:DeallocNode(node)
		end
	end
	for _, id in ipairs(args.add or { }) do
		local node = nodeById(id)
		if not node.alloc then
			spec:AllocNode(node)
		end
	end
	local start = os.clock()
	recalc()
	return {
		seconds = os.clock() - start,
		summary = bridge.summary(),
		stats = pickStats(build.calcsTab.mainOutput, args.fields),
	}
end

-- Radius jewels and the tree's allocation rules ---------------------------------------------------

-- Jewels in allocated sockets: { socket = node, item = item }
local function socketedJewels()
	local list = { }
	for socketId, itemId in pairs(build.spec.jewels) do
		local socket = build.spec.nodes[socketId]
		local item = itemId and itemId ~= 0 and build.itemsTab.items[itemId]
		if socket and socket.alloc and item then
			list[#list + 1] = { socket = socket, item = item }
		end
	end
	table.sort(list, function(a, b) return a.socket.id < b.socket.id end)
	return list
end

local function radiusInfo(index)
	local info = index and data.jewelRadius[index]
	return info and {
		index = index, label = info.label,
		inner = math.sqrt(info.innerSquared or 0), outer = math.sqrt(info.outerSquared or 0),
	} or nil
end

-- What a jewel does to the tree, read from PoB's parse of it (no names listed here): its radius,
-- which nodes it lets the tree take without a connection, the class start it adds, what it conquers
local function jewelTraits(item)
	local jewelData = item.jewelData or { }
	local keystones = { }
	for name in pairs(jewelData.fromNothingKeystones or { }) do
		local key = build.spec.tree.keystoneMap[name]
		if key then
			keystones[#keystones + 1] = key.id
		end
	end
	table.sort(keystones)
	local funcs, grants = 0, 0
	for _ in ipairs(jewelData.funcList or { }) do
		funcs = funcs + 1
	end
	for _, mod in ipairs(item.modList or { }) do
		if mod.name == "GrantedPassive" then  -- "Allocates <passive>" (Megalomaniac)
			grants = grants + 1
		end
	end
	return {
		radius = radiusInfo(item.jewelRadiusIndex),
		unconnected = jewelData.intuitiveLeapLike and true or nil,
		keystones = #keystones > 0 and keystones or nil,
		classStart = jewelData.alternateClassStart,
		-- Timeless jewels: PoB-PoE2 has no seed tables, so conquered notables keep their stats
		conquers = jewelData.conqueredBy and true or nil,
		seedData = data.readLUT ~= nil,
		radiusFuncs = funcs,
		grants = grants > 0 and grants or nil,
	}
end

-- PoB's rule for nodes a jewel may free from the connection rule (PassiveSpec
-- BuildAllDependsAndPaths): not class starts, sockets or ascendancy nodes
local function freeable(node)
	return node.type ~= "ClassStart" and node.type ~= "Socket" and not node.ascendancyName
end

-- Node id -> where the permission comes from (socket or keystone id): every node the socketed
-- jewels (and allocated passives with "AllocateFromNodeRadius") let the tree allocate without a
-- connection. Derived from the jewels themselves, not from the flags PassiveSpec sets, so the
-- check below is independent of the pathing it checks.
local function unconnectedAllowed()
	local spec, allowed = build.spec, { }
	local function allow(nodes, source, types)
		for id in pairs(nodes or { }) do
			local node = spec.nodes[id]
			if node and freeable(node) and (not types or types[node.type]) then
				allowed[id] = allowed[id] or source
			end
		end
	end
	for _, entry in ipairs(socketedJewels()) do
		local item, socket = entry.item, entry.socket
		local index = item.jewelRadiusIndex
		if index and item.jewelData and not item.jewelData.limitDisabled then
			if item.jewelData.intuitiveLeapLike and socket.nodesInRadius then
				allow(socket.nodesInRadius[index], socket.id)
			end
			for name in pairs(item.jewelData.fromNothingKeystones or { }) do
				local key = spec.tree.keystoneMap[name]
				if key and key.nodesInRadius then
					allow(key.nodesInRadius[index], key.id)
				end
			end
		end
	end
	for _, node in pairs(spec.allocNodes) do
		if node.modList then
			for _, radius in ipairs(node.modList:List(nil, "AllocateFromNodeRadius")) do
				local types = { }
				for _, kind in ipairs(radius.to or { }) do
					types[kind] = true
				end
				for _, key in pairs(spec.tree.keystoneMap) do
					if radius.from == "Keystone" and spec.allocNodes[key.id] and key.nodesInRadius then
						allow(key.nodesInRadius[radius.radiusIndex], key.id, types)
					end
				end
			end
		end
	end
	return allowed
end

-- Whether an allocated ascendancy node's own text lets the tree step from it onto main-tree node `other`: "Can Allocate
-- Passive Skills from the <class>'s starting point" (Pathfinder's Path of the Sorceress / Warrior; the wordings are
-- ModParser's AlternateClassStart, 2786-2787), and `other` must be next to that class's start. Never into a class start.
local function startGrantedTo(node, other)
	if not node.ascendancyName or other.ascendancyName or other.type == "ClassStart" then
		return false
	end
	local map = build.spec.tree.classStartNodeNameMap or { }
	for _, line in ipairs(node.sd or { }) do
		local text = line:lower()
		local className = text:match("can allocate passive skills from the (%a+)'s starting point")
			or text:match("can allocate passives from the (%a+)'s starting point")
		local startId = className and map[className:sub(1, 1):upper() .. className:sub(2)]
		if startId then
			for _, linked in ipairs(other.linked or { }) do
				if linked.id == startId then
					return true
				end
			end
		end
	end
	return false
end

-- Every allocated node must be reachable under the game's rules: through allocated nodes from the
-- class start (or the ascendancy start, or a class start a jewel adds, or an allocated ascendancy node
-- that grants another class's start: startGrantedTo), or be a node a socketed
-- jewel lets the tree take without a connection. Nodes reached only that way don't carry a path
-- on. Returns the nodes that break the rule; with `prune`, deallocates them and recalculates.
function bridge.treeCheck(args)
	args = args or { }
	local spec = build.spec
	local allowed = unconnectedAllowed()
	local starts = { }
	if spec.curClass and spec.curClass.startNodeId then
		starts[#starts + 1] = spec.curClass.startNodeId
	end
	if spec.curAscendClass and spec.curAscendClass.startNodeId then
		starts[#starts + 1] = spec.curAscendClass.startNodeId
	end
	for _, entry in ipairs(socketedJewels()) do
		local className = entry.item.jewelData and entry.item.jewelData.alternateClassStart
		local startId = className and spec.tree.classStartNodeNameMap and spec.tree.classStartNodeNameMap[className]
		if startId then
			starts[#starts + 1] = startId
		end
	end
	local connected, queue = { }, { }
	for _, id in ipairs(starts) do
		local node = spec.nodes[id]
		if node and not connected[id] then
			connected[id] = true
			queue[#queue + 1] = node
		end
	end
	for _, node in pairs(spec.allocNodes) do
		if node.isFreeAllocate and not connected[node.id] then  -- granted by an item, not pathed
			connected[node.id] = true
			queue[#queue + 1] = node
		end
	end
	local head = 1
	while head <= #queue do
		local node = queue[head]
		head = head + 1
		for _, other in ipairs(node.linked or { }) do
			if other.alloc and not connected[other.id]
				and (other.ascendancyName == node.ascendancyName or startGrantedTo(node, other)) then
				connected[other.id] = true
				queue[#queue + 1] = other
			end
		end
	end
	local broken, unconnected = { }, { }
	for id, node in pairs(spec.allocNodes) do
		if not connected[id] then
			if allowed[id] then
				unconnected[#unconnected + 1] = id
			elseif node.type ~= "ClassStart" and node.type ~= "AscendClassStart" then
				broken[#broken + 1] = { id = id, name = node.dn, type = node.type }
			end
		end
		local constraint = node.unlockConstraint
		for _, needed in ipairs(constraint and constraint.nodes or { }) do
			if not (spec.nodes[needed] and spec.nodes[needed].alloc) then
				broken[#broken + 1] = { id = id, name = node.dn, type = node.type, needs = needed }
				break
			end
		end
	end
	table.sort(broken, function(a, b) return a.id < b.id end)
	table.sort(unconnected)
	-- Points as PoB's EstimatePlayerProgress counts them: a passive on each weapon set costs one
	-- point between them, and each set holds at most its weapon-set allowance
	local over, overAscendancy = pointsOver(spec.allocNodes, build.calcsTab.mainOutput)
	if args.prune and #broken > 0 then
		for _, entry in ipairs(broken) do
			local node = spec.nodes[entry.id]
			if node.alloc then
				spec:DeallocSingleNode(node)
			end
		end
		spec:BuildAllDependsAndPaths()
		recalc()
	end
	-- `ok` is reachability alone: the user's own builds hold a point more than level and quests
	-- give in PoB (2b75a: 117 at level 93), so points are reported, not failed here
	return {
		ok = #broken == 0, broken = broken, unconnected = unconnected,
		overBudget = over, overAscendancy = overAscendancy, pruned = args.prune and #broken > 0 or nil,
	}
end

-- Larger Time-Lost radii (custom model, ledger entry baryanic-leylines) --------------------------

-- PoB ignores "Non-Unique Time-Lost Jewels have N% increased radius" (Disciple of Varashta's
-- Baryanic Leylines). While a passive with that line is allocated, committed or in a what-if, a
-- socketed non-unique Time-Lost jewel works on the passives within its radius N% larger, by the
-- distance rule PoB uses for every jewel radius (PassiveTree.lua). calcs.initEnv is wrapped to give
-- the jewel's socket that larger node set for the one calculation, so PoB itself applies the
-- jewel's "in Radius" lines to the extra passives and they stack with everything else.
local RADIUS_LINE = "^non%-unique time%-lost jewels have (%d+)%% increased radius$"
local radiusModel = { enabled = true }
local radiusPassiveCache = setmetatable({ }, { __mode = "k" })  -- tree -> { [node id] = percent }
local enlargedCache = setmetatable({ }, { __mode = "k" })  -- spec -> { [key] = node set }

local function radiusPassives()
	local tree = build.spec.tree
	if not radiusPassiveCache[tree] then
		local found = { }
		for id, node in pairs(tree.nodes) do
			for _, line in ipairs(node.sd or { }) do
				local percent = line:lower():match(RADIUS_LINE)
				if percent then
					found[id] = (found[id] or 0) + tonumber(percent)
				end
			end
		end
		radiusPassiveCache[tree] = found
	end
	return radiusPassiveCache[tree]
end

-- The radius increase in effect for a calculation with this override (0 when none)
radiusIncrease = function(override)
	if not radiusModel.enabled then
		return 0
	end
	local total = 0
	for id, percent in pairs(radiusPassives()) do
		local node = build.spec.nodes[id]
		local on = node ~= nil and node.alloc
		for added in pairs(override and override.addNodes or { }) do
			on = on or added.id == id
		end
		for removed in pairs(override and override.removeNodes or { }) do
			on = on and removed.id ~= id
		end
		total = total + (on and percent or 0)
	end
	return total
end

local function enlargedRadiusEligible(item)
	return item and item.rarity ~= "UNIQUE" and item.rarity ~= "RELIC" and item.jewelRadiusIndex ~= nil
		and (item.baseName or ""):find("Time%-Lost") ~= nil
end

-- The nodes within a socket's radius `index` grown by `percent`, by PassiveTree.lua's rule: the
-- squared distance between the inner and outer bounds, no masteries, proxies, blighted or
-- group-less nodes, not the socket itself. At 0% it is PoB's own set.
local function enlargedNodes(socket, index, percent)
	local spec = build.spec
	enlargedCache[spec] = enlargedCache[spec] or { }
	local key = socket.id .. ":" .. index .. ":" .. percent
	if not enlargedCache[spec][key] then
		local info = data.jewelRadius[index]
		local scale = (1 + percent / 100) ^ 2
		local inner2, outer2 = info.innerSquared * scale, info.outerSquared * scale
		local set = { }
		for id, node in pairs(spec.tree.nodes) do
			if node.x and node.y and id ~= socket.id and not node.isBlighted and node.group and not node.isProxy
				and not node.group.isProxy and not node.isMastery then
				local dx, dy = node.x - socket.x, node.y - socket.y
				local d2 = dx * dx + dy * dy
				if d2 <= outer2 and inner2 <= d2 then
					set[id] = spec.nodes[id] or node
				end
			end
		end
		enlargedCache[spec][key] = set
	end
	return enlargedCache[spec][key]
end

-- Tolerant Equipment (MECH90; the coverage audit's never-on rows, MECH89's Handover step 2). PoB parses "Immune to
-- Bleeding if Equipped Helmet has higher Armour than Evasion Rating" and "Immune to Poison if Equipped Helmet has higher
-- Evasion Rating than Armour" (ModParser 5293-5294) to BleedImmune / PoisonImmune under Condition:
-- HelmetArmourHigherThanEvasion / HelmetEvasionHigherThanArmour, which nothing in PoB sets (checked: grep of both names
-- in vendor/PathOfBuilding-PoE2/src: ModParser's two lines and ModCache's parse only). Set here from the equipped helmet's
-- own Armour and Evasion (item.armourData: its base, local modifiers and quality; a what-if's helmet is already in
-- env.player.itemList), strictly higher, neither when equal or with no helmet. The immunity then reaches PoB's
-- Bleed / PoisonAvoidChance (CalcDefence 2001: 100 when immune), which the boss's Bleeds on you read. The game's stats
-- (26650308 Stats: immune_to_bleed_if_helmet_armour_higher_than_helmet_evasion, immune_to_bleeding_if_helmet_grants_
-- higher_armour_than_evasion and the two Poison / Evasion ones) carry the same two wordings; any source of them counts.
local function helmetConditions(env)
	local helmet = env.player and env.player.itemList and env.player.itemList["Helmet"]
	if not helmet or not helmet.armourData or not env.modDB then
		return
	end
	-- (as PoB's own ArmourOnHelmet / EvasionOnHelmet, CalcDefence 803-853: the item's value at the character's level)
	local armour = helmet:GetArmourDataValue("Armour", env.player.level)
	local evasion = helmet:GetArmourDataValue("Evasion", env.player.level)
	if armour > evasion then
		env.modDB:NewMod("Condition:HelmetArmourHigherThanEvasion", "FLAG", true, "Helmet")
	elseif evasion > armour then
		env.modDB:NewMod("Condition:HelmetEvasionHigherThanArmour", "FLAG", true, "Helmet")
	end
end

-- Radius damage transforms (COV49; the manager's ruling 20 Nov: COV-A's item, jewels.py lent): "Increases and
-- Reductions to <A> and <B> Damage in Radius are transformed to apply to <C> Damage" (game 26650308 Mods
-- JewelRadiusIncColdFreToLightning / IncLightningColdToFire / IncLightningFireToCold, stats
-- local_jewel_transform_damage_increases_from_*; spawn weight 0: a crafted line that Ancient Concentrated Liquid
-- Isolation (BaseItemTypes DistilledEmotionTimeLost10, a Delirium Liquid Emotion) adds to a Rare Time-Lost jewel,
-- Emerald -> Lightning, Ruby -> Fire, Sapphire -> Cold (poe2db's Jewels / Liquid Emotions pages, current). PoB parses
-- none of it (ModCache 8322-8324: nil), and for a Time-Lost jewel PoB never runs a per-node radius function anyway
-- (CalcSetup 220-238 copies its cached "also grant" lists and stops), so the line counted 0: an UNDERCOUNT for a build
-- with Lightning skills and Cold / Fire passives in the radius (and an overcount of the Cold / Fire it moves away).
-- Here, every passive in the jewel's radius (allocated ones are the ones PoB adds to the build) has its <A> and <B>
-- Damage increases and reductions (PoB's ColdDamage / FireDamage / LightningDamage INC, with their flags and tags:
-- "with Spells", "over time", conditions) moved to <C>: the same move PoB's own getSimpleConv makes for its other
-- "transformed" radius lines (ModParser.lua 29-49, 7093). Read this way, UNVERIFIED (no in-game test found; RESEARCH37
-- R37-15 kept all three, research/radius-transform-jewels-and-fissure-layouts.md): (a) lines naming another type or
-- "Elemental Damage" aren't moved (the line names two types), MEDIUM-LOW: the same result for a build dealing only <C>
-- (an Elemental increase already applies to it), so only a mixed-element build would need a "moved" row; (b) the
-- passives' Time-Lost "also grant" lines in the radius are moved too (they are increases on those passives),
-- MEDIUM-LOW, with a row for the lower reading, the passives' own stats only (radiusTransformBaseOnly,
-- bridge.radiusTransformBaseOnly); (c) two transforms on one passive move each increase once (the first jewel's),
-- MEDIUM: a move, not a copy, so no reading counts an increase twice (where it goes when two overlap is the open part).
-- The game's wording sets nothing else (checked: stat_descriptions text
-- via PoB's ModJewel.lua 360-362 and QueryMods.lua; patch notes: no hit for "transformed to apply", which is not
-- evidence of absence).
local RADIUS_TRANSFORM_LINE = "(%a+) and (%a+) damage in radius are transformed to apply to (%a+) damage"
local RADIUS_TRANSFORM_TYPES = { cold = "ColdDamage", fire = "FireDamage", lightning = "LightningDamage",
	physical = "PhysicalDamage", chaos = "ChaosDamage" }
local radiusTransformCache = setmetatable({ }, { __mode = "k" })
local function radiusTransforms(item)
	local cached = radiusTransformCache[item]
	if cached ~= nil then
		return cached or nil
	end
	local found = { }
	for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
		for _, modLine in ipairs(item[kind] or { }) do
			local a, b, to = tostring(modLine.line or ""):lower():match(RADIUS_TRANSFORM_LINE)
			local from = a and { [RADIUS_TRANSFORM_TYPES[a] or ""] = true, [RADIUS_TRANSFORM_TYPES[b] or ""] = true }
			if from and RADIUS_TRANSFORM_TYPES[to] and (not item.CheckModLineVariant or item:CheckModLineVariant(modLine)) then
				from[""] = nil
				found[#found + 1] = { from = from, to = RADIUS_TRANSFORM_TYPES[to], line = modLine.line }
			end
		end
	end
	radiusTransformCache[item] = #found > 0 and found or false
	return #found > 0 and found or nil
end
local radiusTransformBaseOnly = false  -- (COV50) set only inside bridge.radiusTransformBaseOnly
local function applyRadiusTransforms(env, node, modList)
	if not env or not env.radiusJewelList or #env.radiusJewelList == 0 or not node or node.type == "Mastery" then
		return
	end
	local seen, moves
	for _, rad in ipairs(env.radiusJewelList) do
		if rad.item and rad.nodes and rad.nodes[node.id] and not (seen and seen[rad.item]) then
			seen = seen or { }
			seen[rad.item] = true
			for _, move in ipairs(radiusTransforms(rad.item) or { }) do
				moves = moves or { }
				moves[#moves + 1] = move
			end
		end
	end
	if not moves then
		return
	end
	local last = #modList
	if radiusTransformBaseOnly then
		-- (COV50, R37-15 (b)'s row) the passive's own stats only: CalcSetup's buildModListForNode adds node.modList first,
		-- then the Time-Lost jewels' cached "also grant" lists (CalcSetup 212 / 228-233)
		last = math.min(last, node.type ~= "Keystone" and #(node.modList or { }) or 0)
	end
	for i = 1, last do
		local mod = modList[i]
		if mod.type == "INC" and mod.value ~= 0 then
			for _, move in ipairs(moves) do
				if move.from[mod.name] then
					modList:NewMod(mod.name, "INC", -mod.value, mod.source, mod.flags, mod.keywordFlags, unpack(mod))
					modList:NewMod(move.to, "INC", mod.value, mod.source, mod.flags, mod.keywordFlags, unpack(mod))
					break
				end
			end
		end
	end
end
-- The item's radius damage transform lines, for bridge.radiusJewels (nil when none)
local function transformLines(item)
	local lines = { }
	for _, move in ipairs(radiusTransforms(item) or { }) do
		lines[#lines + 1] = move.line
	end
	return #lines > 0 and lines or nil
end
local wrappedNodeModList
local function installRadiusTransforms()
	local calcs = require("Modules.CalcBase")
	if calcs.buildModListForNode == wrappedNodeModList or not calcs.buildModListForNode then
		return
	end
	local original = calcs.buildModListForNode
	wrappedNodeModList = function(env, node, ...)
		local modList = original(env, node, ...)
		if modList then
			applyRadiusTransforms(env, node, modList)
		end
		return modList
	end
	calcs.buildModListForNode = wrappedNodeModList
end
-- (COV50, RESEARCH37 R37-15 (b)) The stats with the radius damage transforms moving only the passives' own stats, not
-- the Time-Lost jewels' "also grant" lines on them: the row for the lower reading. Two recalculations (on, then back).
function bridge.radiusTransformBaseOnly(args)
	radiusTransformBaseOnly = true
	local ok, err = pcall(recalc)
	local stats = ok and pickStats(build.calcsTab.mainOutput, args.fields) or nil
	radiusTransformBaseOnly = false
	recalc()
	if not ok then
		error(err)
	end
	return { stats = stats }
end

-- (SEARCH35; MECHB53's POE2T4809 / 4810, RESEARCH45 R45-8 HIGH / R45-9 MEDIUM-HIGH) A stat set whose own stats carry
-- base_deal_no_damage deals no damage in the game, but PoB maps no such stat and scores it ("Load Hailstorm Rounds" with
-- the crossbow's damage, and in Full DPS beside the firing skill). Per SET, never per skill: the flag sits on the first
-- set of a multi-set skill whose later sets carry the hits (Rolling Slam, Tempest Bell). A minion skill keeps its
-- damage: PoB reports its minion's (Raising / Summoning sets carry the flag; the minion deals the hits). Checked: the
-- Ballistas' "Ballista" sets carry it, their bolts are the separate Artillery / Siege BallistaProjectilePlayer effects
-- (no flag), so they keep their damage
local noDamageSetsOn = true  -- (bridge.setNoDamageSets: off only for a with / without probe or test)
local function noDamageSet(activeSkill, statSet)
	if not noDamageSetsOn or not statSet or (activeSkill and activeSkill.minion) then
		return false
	end
	return effectCarries({ statSets = { statSet } }, "base_deal_no_damage")
end
-- Full DPS (calcs.calcFullDPS skips a skill getActiveSkillCount calls not enabled): such a set adds nothing; its count
-- is kept (the other two callers, reservation in CalcDefence and Umbral Well in CalcPerform, read the count only)
local wrappedActiveSkillCount
local function installNoDamageSets()
	local calcs = require("Modules.CalcBase")
	if calcs.getActiveSkillCount == wrappedActiveSkillCount or not calcs.getActiveSkillCount then
		return
	end
	local original = calcs.getActiveSkillCount
	wrappedActiveSkillCount = function(activeSkill, ...)
		local count, enabled = original(activeSkill, ...)
		local effect = activeSkill and activeSkill.activeEffect
		local current = effect and effect.statSet and (effect.statSet.statSet
			or (effect.grantedEffect and effect.grantedEffect.statSets or { })[effect.statSet.index or 1])
		if enabled and current and noDamageSet(activeSkill, current) then
			return count, false
		end
		return count, enabled
	end
	calcs.getActiveSkillCount = wrappedActiveSkillCount
end

function bridge.setNoDamageSets(args)
	noDamageSetsOn = args.on ~= false
	recalc()
	return { on = noDamageSetsOn }
end

-- Installed before every bridge call: PoB defines calcs.initEnv again whenever it loads its
-- calculation modules (a new build's CalcsTab)
local wrappedInitEnv
installRadiusModel = function()
	installRadiusTransforms()  -- (COV49: the radius damage transforms, above; PoB redefines it with CalcSetup too)
	installNoDamageSets()  -- (SEARCH35: no-damage stat sets out of Full DPS)
	local calcs = require("Modules.CalcBase")
	if calcs.initEnv == wrappedInitEnv or not calcs.initEnv then
		return
	end
	local initEnv = calcs.initEnv
	wrappedInitEnv = function(buildArg, mode, override, specEnv)
		local percent = build and buildArg == build and radiusIncrease(override) or 0
		local swapped = { }
		if percent > 0 then
			for _, entry in ipairs(socketedJewels()) do
				local socket, item = entry.socket, entry.item
				if override and override.repSlotName == "Jewel " .. socket.id then
					item = override.repItem  -- the jewel a what-if puts there
				end
				if enlargedRadiusEligible(item) and socket.nodesInRadius then
					local index = item.jewelRadiusIndex
					swapped[#swapped + 1] = { socket.nodesInRadius, index, socket.nodesInRadius[index] }
					socket.nodesInRadius[index] = enlargedNodes(socket, index, percent)
				end
			end
		end
		-- (COV44) an item's Necromantic Talisman: node 39935 in spec.allocNodes for this call (grantedTalismanNode)
		local talisman = build and buildArg == build and grantedTalismanNode(override)
		if talisman then
			build.spec.allocNodes[39935] = talisman
		end
		-- Put PoB's own node sets back whatever happens, and pass every result through
		local function restore(ok, ...)
			for i = #swapped, 1, -1 do
				swapped[i][1][swapped[i][2]] = swapped[i][3]
			end
			if talisman then
				build.spec.allocNodes[39935] = nil
			end
			if not ok then
				error((...), 0)
			end
			return ...
		end
		-- (COV43) the character rune slots' Runes, for setAugmentCounts' RunesInEquipment
		local function tagRunes(env, ...)
			if type(env) == "table" and build and buildArg == build then
				env.bridgeCharacterRunes = characterRuneCount(override)
				helmetConditions(env)  -- (MECH90: Tolerant Equipment)
				-- (COV44's own setter of the same two Helmet conditions was here; removed when MECH90's helmetConditions
				-- landed in train 91: one setter)
			end
			return env, ...
		end
		return restore(pcall(function() return tagRunes(initEnv(buildArg, mode, override, specEnv)) end))
	end
	calcs.initEnv = wrappedInitEnv
end

-- The passives with a radius increase, the one in effect, and the model's switch (`enabled`)
function bridge.radiusModel(args)
	if args and args.enabled ~= nil and radiusModel.enabled ~= (args.enabled and true or false) then
		radiusModel.enabled = args.enabled and true or false
		recalc()  -- the committed numbers follow the switch
	end
	local passives = { }
	for id, percent in pairs(radiusPassives()) do
		local node = build.spec.tree.nodes[id]
		passives[#passives + 1] = { id = id, name = node.dn, ascendancy = node.ascendancyName, percent = percent }
	end
	table.sort(passives, function(a, b) return a.id < b.id end)
	return { enabled = radiusModel.enabled, increase = radiusIncrease(nil), passives = passives }
end

-- Test support: a socket's node set for radius `index` grown by `percent`, as the model computes it
function bridge.enlargedRadius(args)
	local socket = nodeById(args.socket)
	local ids = { }
	for id in pairs(enlargedNodes(socket, args.index, args.percent or 0)) do
		ids[#ids + 1] = id
	end
	table.sort(ids)
	local base = { }
	for id in pairs(socket.nodesInRadius and socket.nodesInRadius[args.index] or { }) do
		base[#base + 1] = id
	end
	table.sort(base)
	return { ids = ids, base = base }
end

-- Socketed radius jewels: the radius, the nodes in it, the allocated nodes it changes, the nodes the
-- tree holds only through it, and where the radius sits (the socket, or a keystone for From Nothing).
-- A radius the custom model enlarges also gives `enlarged` (percent, radius and the extra passives).
function bridge.radiusJewels()
	local spec = build.spec
	local allowed = unconnectedAllowed()
	local connectedOnlyThrough = { }
	local check = bridge.treeCheck()
	for _, id in ipairs(check.unconnected) do
		connectedOnlyThrough[id] = allowed[id]
	end
	local list = { }
	for _, entry in ipairs(socketedJewels()) do
		local item, socket = entry.item, entry.socket
		local traits = jewelTraits(item)
		if traits.radius then
			local centres = traits.keystones or { socket.id }
			local inRadius, affected = { }, { }
			for _, centreId in ipairs(centres) do
				local centre = centreId == socket.id and socket or spec.tree.nodes[centreId]
				for id in pairs(centre.nodesInRadius and centre.nodesInRadius[item.jewelRadiusIndex] or { }) do
					local node = spec.nodes[id]
					if node then
						inRadius[#inRadius + 1] = id
						if node.alloc then
							affected[#affected + 1] = id
						end
					end
				end
			end
			table.sort(inRadius)
			table.sort(affected)
			local through = { }
			for id, source in pairs(connectedOnlyThrough) do
				if source == socket.id or (traits.keystones and isValueInArray(traits.keystones, source)) then
					through[#through + 1] = id
				end
			end
			table.sort(through)
			local enlarged
			local percent = radiusIncrease(nil)
			if percent > 0 and enlargedRadiusEligible(item) and socket.nodesInRadius then
				local base = socket.nodesInRadius[item.jewelRadiusIndex] or { }
				local extra = { }
				for id, node in pairs(enlargedNodes(socket, item.jewelRadiusIndex, percent)) do
					if not base[id] then
						extra[#extra + 1] = id
						inRadius[#inRadius + 1] = id
						if node.alloc then
							affected[#affected + 1] = id
						end
					end
				end
				table.sort(extra)
				table.sort(inRadius)
				table.sort(affected)
				local scale = 1 + percent / 100
				enlarged = {
					percent = percent, outer = traits.radius.outer * scale, inner = traits.radius.inner * scale,
					extra = extra,
				}
			end
			list[#list + 1] = {
				socket = socket.id, name = item.title or item.name, base = item.baseName,
				radius = traits.radius, centres = centres, inRadius = inRadius, affected = affected,
				unconnected = through, allowsUnconnected = traits.unconnected or (traits.keystones and true) or nil,
				conquers = traits.conquers, enlarged = enlarged,
				transforms = transformLines(item),  -- (COV49: its radius damage transforms, applyRadiusTransforms)
			}
		end
	end
	return { jewels = list }
end

-- What each jewel (item text) does to the tree, for the search to classify candidates without a
-- hand-made list: { traits = [{ radius, unconnected, keystones, classStart, conqueredBy, ... }] }
function bridge.jewelTraits(args)
	local traits = { }
	for i, raw in ipairs(args.raws or { }) do
		local item = makeItem(raw)
		local entry = jewelTraits(item)
		entry.variants = item.variantList and #item.variantList or 0
		traits[i] = entry
	end
	return { traits = traits }
end

-- Classes and their ascendancies, as named in the passive tree
function bridge.classes()
	local classes = { }
	for _, class in pairs(build.spec.tree.classes) do
		local ascendancies = { }
		for _, ascend in pairs(class.classes) do
			if ascend.name ~= "None" then
				ascendancies[#ascendancies + 1] = ascend.name
			end
		end
		table.sort(ascendancies)
		classes[class.name] = ascendancies
	end
	return classes
end

function bridge.setClass(args)
	local spec = build.spec
	local classId = spec.tree.classNameMap[args.className]
	if not classId then
		error("unknown class " .. tostring(args.className))
	end
	spec:SelectClass(classId)
	if args.ascendancy then
		local found
		for ascendId, ascend in pairs(spec.curClass.classes) do
			if ascend.name == args.ascendancy then
				found = ascendId
			end
		end
		if not found then
			error("unknown ascendancy " .. tostring(args.ascendancy) .. " for " .. args.className)
		end
		spec:SelectAscendClass(found)
	end
	recalc()
	return bridge.summary()
end

function bridge.setLevel(args)
	build.characterLevel = math.min(math.max(args.level, 1), 100)
	build.characterLevelAutoMode = false
	recalc()
	return bridge.summary()
end

-- Add a socket group from PoB's skill text format ("Fireball 20/0 1" per line), optionally
-- making it the main skill and/or counting it toward Full DPS
function bridge.addSkillGroup(args)
	local skillsTab = build.skillsTab
	local before = #skillsTab.socketGroupList
	skillsTab:PasteSocketGroup(args.text)
	if #skillsTab.socketGroupList == before then
		error("PoB parsed no gems from: " .. args.text)
	end
	local group = skillsTab.socketGroupList[#skillsTab.socketGroupList]
	group.includeInFullDPS = args.fullDPS and true or nil
	if args.main then
		build.mainSocketGroup = #skillsTab.socketGroupList
	end
	recalc()
	return bridge.summary()
end

-- Check gem names against PoB's gem data
function bridge.findGems(args)
	local found = { }
	for _, name in ipairs(args.names) do
		-- PoB also matches abbreviations, so callers should check the returned name
		local err, gemData = build.skillsTab:FindSkillGem(name)
		found[name] = gemData and gemData.name or err
	end
	return found
end

-- Add an item from its text and let PoB equip it in the matching slot
function bridge.addItem(args)
	local raw = args.raw
	if args.unique then
		local item = main.uniqueDB.list[args.unique]
		if not item then
			error("unknown unique " .. args.unique)
		end
		raw = item.raw
	end
	build.itemsTab:CreateDisplayItemFromRaw(raw)
	if not build.itemsTab.displayItem then
		error("PoB could not parse item: " .. tostring(raw))
	end
	build.itemsTab:AddDisplayItem()
	recalc()
	return bridge.equipped()
end

-- The item in a slot, if any
local function slotItem(slotName)
	local slot = build.itemsTab.slots[slotName]
	if not slot then
		error("unknown item slot " .. tostring(slotName))
	end
	return slot, slot.selItemId and slot.selItemId ~= 0 and build.itemsTab.items[slot.selItemId] or nil
end

-- Drop an item from the build's item list unless another slot still uses it, so long searches
-- don't pile up thousands of replaced items
local function forgetItem(item)
	if not item then
		return
	end
	for _, slot in pairs(build.itemsTab.slots) do
		if slot.selItemId == item.id then
			return
		end
	end
	build.itemsTab.items[item.id] = nil
	for index, id in ipairs(build.itemsTab.itemOrderList) do
		if id == item.id then
			table.remove(build.itemsTab.itemOrderList, index)
			break
		end
	end
end

-- Flask and charm slots count only while active (the GUI's checkbox); the item set keeps the flag
-- for the export
local function setSlotActive(slotName, active)
	local slot = build.itemsTab.slots[slotName]
	slot.active = active
	build.itemsTab.activeItemSet[slotName].active = active
	if slot.controls and slot.controls.activate then
		slot.controls.activate.state = active
	end
end

-- Commit an item into a specific slot (or empty it when `raw` is absent), then recalculate fully.
-- `active` switches a flask or charm slot on or off. `recalc = false` leaves the recalculation to the next equip.
function bridge.equip(args)
	local slot, old = slotItem(args.slot)
	if args.raw then
		local item = makeItem(args.raw)
		build.itemsTab:AddItem(item, true)
		slot:SetSelItemId(item.id)
	else
		slot:SetSelItemId(0)
	end
	-- (JEWEL2) The item's jewel sockets open or close with it, as ItemSlotControl:Populate sets them (only a load runs it).
	-- Before, facts and sourceLines missed a jewel socketed during a search and kept reading one whose item was swapped
	-- away, while PoB's calculation (CalcSetup's jewelSocketCount check) had it right: probe POE2T7937
	local equipped = slot.selItemId > 0 and build.itemsTab.items[slot.selItemId]
	local jewelSockets = equipped and equipped.jewelSocketCount or 0
	for i, jewelSocket in ipairs(slot.jewelSocketList or { }) do
		jewelSocket.inactive = i > jewelSockets
	end
	if args.active ~= nil then
		setSlotActive(args.slot, args.active and args.raw ~= nil)
	end
	forgetItem(old)
	if args.recalc == false then
		-- (PERF23) The caller's next call is another equip, whose recalculation covers this change too: Build's
		-- real trials of item candidates put the old item back between two trials (builder/search/state.py)
		return { seconds = 0, stats = setmetatable({ }, { __jsontype = "object" }) }
	end
	local start = os.clock()
	recalc()
	return { seconds = os.clock() - start, stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Skill gems -----------------------------------------------------------------------------------

-- (PERF24) A NAME SEVERAL PoB GEMS SHARE means the same gem in every PoB process. PoB ships different skills
-- under one display name, and pairs(build.data.gems) lists them in an order that changes from one process to
-- the next, so "the first gem of that name" was another skill in another process: PERF23's two coverage dumps
-- gave "Lightning Bolt" the types Cooldown, InbuiltTrigger and Triggered in one and not in the other. PoB's own
-- calculation finds a skill by its id, so only our by-name reads moved.
-- (checked, PoB bb52d6b: every key of Data/Gems.lua grouped by name, tests/test_shared_gem_names.py redoes it
-- at each run. Five names are shared: the three below; "Unleash", the support gem and the skill an item
-- grants, which gemByName's socketable-first rule already settled; "Skeletal Sniper", one key written twice.
-- Second source, the game's ActiveSkills: rows 659 lightning_bolt and 660 unique_breach_lightning_bolt are both
-- displayed "Lightning Bolt", and only 660 holds Cooldown, InbuiltTrigger and Triggered, as PoB's two skills do.)
-- The bare name can't say which the build has (that depends on the item or weapon), so each entry is the one
-- every lane's data dump already put first (data/pob-bb52d6b368/gems.json, the same in all 11 folders on
-- 14 Nov 2026), which Context.all_gems reads: no number moves. "Lightning Bolt" is also the skill PoB's parser
-- grants for "Grants Skill: Level N Lightning Bolt" (ModParser's gemIdLookup, ModCache). ASSUMPTION, LOW: a
-- build with a one-handed or two maces, or a Spear Stab from the off hand, reads the listed skill's types
-- and text by name; the fix that removes the guess is to carry the skill id with a group's gem.
local SHARED_GEM_NAMES = {
	["Lightning Bolt"] = "UniqueBreachLightningBoltPlayer",
	["Mace Strike"] = "Melee2HMacePlayer",
	["Spear Stab"] = "MeleeSpearPlayer",
}

-- PoB's gems in one order for every process: by name, a shared name's listed skill first, then by gem id
local orderedGems
local function gemsInOrder()
	if not orderedGems then
		orderedGems = { }
		for id, gemData in pairs(build.data.gems) do
			orderedGems[#orderedGems + 1] = { id = id, gem = gemData,
				rank = SHARED_GEM_NAMES[gemData.name] == gemData.grantedEffectId and 0 or 1 }
		end
		table.sort(orderedGems, function(a, b)
			if a.gem.name ~= b.gem.name then
				return a.gem.name < b.gem.name
			elseif a.rank ~= b.rank then
				return a.rank < b.rank
			end
			return a.id < b.id
		end)
	end
	return orderedGems
end

local gemsByName
local function gemByName(name)
	if not gemsByName then
		gemsByName = { }
		for _, entry in ipairs(gemsInOrder()) do
			local gemData = entry.gem
			local effect = gemData.grantedEffect
			local known = gemsByName[gemData.name]
			-- Prefer the socketable gem when an item- or tree-granted skill shares the name
			if not known or (known.grantedEffect.fromItem or known.grantedEffect.fromTree) and not (effect.fromItem or effect.fromTree) then
				gemsByName[gemData.name] = gemData
			end
		end
	end
	local gemData = gemsByName[name]
	if not gemData then
		error("unknown gem " .. tostring(name))
	end
	return gemData
end

-- (COV30) The gem whose granted effect is `skillId`, in gemsInOrder's order; nil when PoB has none
local function gemBySkillId(skillId)
	for _, entry in ipairs(gemsInOrder()) do
		if entry.gem.grantedEffectId == skillId then
			return entry.gem
		end
	end
	return nil
end

local function socketGroup(index)
	local group = build.skillsTab.socketGroupList[index]
	if not group then
		error("no socket group " .. tostring(index))
	end
	return group
end

-- Attribute requirements of a gem instance, as SkillsTab:ProcessSocketGroup sets them
local function setGemRequirements(instance)
	local effect = instance.gemData.grantedEffect
	local levelData = effect.levels[instance.level] or effect.levels[#effect.levels]
	instance.reqLevel = levelData and levelData.levelRequirement or 1
	instance.reqStr = calcLib.getGemStatRequirement(instance.reqLevel, instance.gemData.reqStr, effect.support)
	instance.reqDex = calcLib.getGemStatRequirement(instance.reqLevel, instance.gemData.reqDex, effect.support)
	instance.reqInt = calcLib.getGemStatRequirement(instance.reqLevel, instance.gemData.reqInt, effect.support)
end

-- Character level and attributes each level of each gem needs, as PoB computes them: lets the
-- search use a gem at the highest level the build can actually equip
function bridge.gemRequirements(args)
	local results = setmetatable({ }, { __jsontype = "object" })
	for _, name in ipairs(args.names) do
		local gemData = gemByName(name)
		local levels = { }
		for level = 1, args.maxLevel or 20 do
			local instance = { gemData = gemData, level = level }
			if not gemData.grantedEffect.levels[level] then
				break
			end
			setGemRequirements(instance)
			levels[#levels + 1] = { level = level, levelRequirement = instance.reqLevel,
				str = instance.reqStr, dex = instance.reqDex, int = instance.reqInt }
		end
		results[name] = levels
	end
	return { gems = results }
end

local function gemInstance(gemData, level, quality)
	local instance = {
		level = level or gemData.naturalMaxLevel, quality = quality or 0, count = 1, enabled = true,
		enableGlobal1 = true, enableGlobal2 = true, gemId = gemData.id, nameSpec = gemData.name,
		skillId = gemData.grantedEffectId, gemData = gemData,
	}
	setGemRequirements(instance)
	return instance
end

local function describeGroup(index, group)
	local gems = { }
	for _, gem in ipairs(group.gemList or { }) do
		local effect = gem.gemData and gem.gemData.grantedEffect or gem.grantedEffect
		gems[#gems + 1] = {
			name = gem.gemData and gem.gemData.name or (gem.nameSpec ~= "" and gem.nameSpec) or (gem.grantedEffect and gem.grantedEffect.name) or "", level = gem.level, quality = gem.quality,
			support = effect and effect.support and true or false, enabled = gem.enabled ~= false,
			-- the granted effect's id (COV30): which of two gems sharing a name ("Lightning Bolt") this is
			skillId = effect and effect.id or nil,
		}
	end
	local skills = { }
	for _, activeSkill in ipairs(group.displaySkillList or { }) do
		local effect = activeSkill.activeEffect
		local parts = { }
		for _, part in ipairs(effect.grantedEffect.parts or { }) do
			parts[#parts + 1] = part.name
		end
		local statSets, noDamageSets = { }, { }
		local currentSet = effect.statSet and effect.statSet.index or nil  -- (SEARCH35: the set PoB calculates it on)
		for setIndex, statSet in ipairs(effect.grantedEffect.statSets or { }) do
			statSets[#statSets + 1] = statSet.label or ""
			-- (SEARCH35; MECHB53's POE2T4809 / 4810, RESEARCH45 R45-8 / R45-9) the sets whose own stats carry
			-- base_deal_no_damage: the game deals no damage from that set (PoB maps no such stat and scores it, e.g.
			-- "Load Hailstorm Rounds" with the crossbow's damage). Per SET: the flag sits on the first set of a
			-- multi-set skill (Rolling Slam, Tempest Bell) whose later sets carry the hits
			if noDamageSet(activeSkill, statSet) then
				noDamageSets[#noDamageSets + 1] = setIndex
			end
		end
		-- (SEARCH36) the skill's Cost & Reservation multiplier as PoB applies it (CalcDefence: More "ReservationMultiplier";
		-- 0 on Purity of Fire / Ice / Lightning and Impurity, RESEARCH46 R46-1) and the Spirit its supports add
		-- (Sum BASE "ExtraSpirit"), before that multiplier: gems.free_groups and the result's at-full-cost row
		local reservationMultiplier, extraSpirit
		if activeSkill.skillModList and activeSkill.skillCfg then
			reservationMultiplier = activeSkill.skillModList:More(activeSkill.skillCfg, "ReservationMultiplier")
			extraSpirit = activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "ExtraSpirit")
		end
		skills[#skills + 1] = { name = effect.grantedEffect.name, parts = parts, statSets = statSets,
			noDamageSets = noDamageSets, statSet = currentSet,
			reservationMultiplier = reservationMultiplier, extraSpirit = extraSpirit,
			-- (SEARCH36, R45-6b) the Spirit PoB reserves for it (CalcDefence's skillData.SpiritReservedBase)
			spiritReserved = activeSkill.skillData and activeSkill.skillData.SpiritReservedBase or 0,
			part = effect.srcInstance and effect.srcInstance.skillPart,
			-- (MECHB51, Spectre step 3: Assumptions.choose_monsters) the granted effect's id and a Spectre / Tamed
			-- Companion gem's chosen monster
			id = effect.grantedEffect.id, minion = effect.srcInstance and effect.srcInstance.skillMinion or nil }
	end
	return {
		index = index, label = group.label, enabled = group.enabled ~= false, main = index == build.mainSocketGroup,
		fullDPS = group.includeInFullDPS and true or false, fromItem = group.source ~= nil,
		mainActiveSkill = group.mainActiveSkill or 1, gems = gems, skills = skills,
		set1 = group.set1 ~= false, set2 = group.set2 ~= false,
		-- (SEARCH36, R45-6b) an item group's slot ("Amulet", "Weapon 1") and source: which item grants it
		slot = group.slot, source = group.source,
	}
end

function bridge.groups()
	local groups = { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		groups[#groups + 1] = describeGroup(index, group)
	end
	return { groups = groups, mainSocketGroup = build.mainSocketGroup }
end

-- (PERF14, setGroup's check) A copy of a table's plain values (numbers, strings, booleans, tables of them), and
-- where two such copies first differ (nil when they are equal)
local function plainCopy(value, seen)
	if type(value) ~= "table" then
		return (type(value) == "number" or type(value) == "string" or type(value) == "boolean") and value or nil
	end
	seen = seen or { }
	if seen[value] then
		return seen[value]
	end
	local copy = { }
	seen[value] = copy
	for key, item in pairs(value) do
		if type(key) == "string" or type(key) == "number" then
			copy[key] = plainCopy(item, seen)
		end
	end
	return copy
end

local function firstDifference(a, b, path, seen)
	if type(a) == "table" and type(b) == "table" then
		seen = seen or { }
		if seen[a] then
			return nil
		end
		seen[a] = true
		for key, item in pairs(a) do
			local where = firstDifference(item, b[key], path .. "." .. tostring(key), seen)
			if where then
				return where
			end
		end
		for key, item in pairs(b) do
			if a[key] == nil then
				return path .. "." .. tostring(key) .. ": only in the second"
			end
		end
		return nil
	end
	if a ~= b and not (a ~= a and b ~= b) then
		return path .. ": " .. tostring(a) .. " ~= " .. tostring(b)
	end
	return nil
end

-- Create (no `index`) or rewrite a socket group from gem names, then recalculate fully.
-- gems: { {name=, level=, quality=, enabled=} }; main/fullDPS/enabled as flags.
function bridge.setGroup(args)
	local list = build.skillsTab.socketGroupList
	local group
	if args.index then
		group = socketGroup(args.index)
	else
		group = { label = args.label or "", enabled = true, gemList = { } }
		list[#list + 1] = group
		args.index = #list
	end
	if args.gems then
		-- The skill part and stat set PoB calculates (e.g. Glacial Lance's Explosion) live on the
		-- gem instance; carry them over, or every support change would quietly reset them
		local kept = { }
		for _, old in ipairs(group.gemList or { }) do
			local name = old.gemData and old.gemData.name
			if name and not kept[name] then
				kept[name] = { skillPart = old.skillPart, skillPartCalcs = old.skillPartCalcs,
					statSet = old.statSet, statSetCalcs = old.statSetCalcs }
			end
		end
		group.gemList = { }
		for _, spec in ipairs(args.gems) do
			-- (COV30) `skillId` picks the gem of that granted effect where several share the name (the audit sweep's
			-- Unleash: gemByName's socketable-first rule gives the support)
			local instance = gemInstance(spec.skillId and gemBySkillId(spec.skillId) or gemByName(spec.name), spec.level, spec.quality)
			instance.enabled = spec.enabled ~= false
			for key, value in pairs(kept[spec.name] or { }) do
				instance[key] = value
			end
			group.gemList[#group.gemList + 1] = instance
		end
		build.skillsTab:ProcessSocketGroup(group)
	end
	if args.enabled ~= nil then
		group.enabled = args.enabled
	end
	if args.fullDPS ~= nil then
		group.includeInFullDPS = args.fullDPS or nil
	end
	-- Weapon sets the group is used with (both by default); PoB calculates a group that only
	-- weapon set 2 uses with that set's weapons and carries its buffs over to the main skill
	if args.set1 ~= nil or args.set2 ~= nil then
		if args.set1 ~= nil then
			group.set1 = args.set1
		end
		if args.set2 ~= nil then
			group.set2 = args.set2
		end
		if not group.set1 and not group.set2 then
			group.set1, group.set2 = true, true
		end
	end
	if args.main then
		build.mainSocketGroup = args.index
	end
	if args.mainActiveSkill then
		group.mainActiveSkill = args.mainActiveSkill
		group.mainActiveSkillCalcs = args.mainActiveSkill
	end
	-- The skill part and stat set live on the gem instance of the group's chosen active skill, which the group's
	-- displaySkillList (from a calculation) names.
	local function target()
		local activeSkill = group.displaySkillList and group.displaySkillList[group.mainActiveSkill or 1]
		local instance = activeSkill and activeSkill.activeEffect.srcInstance
		return instance, instance and activeSkill.activeEffect.grantedEffect.id
	end
	-- Writes them; gives back what was there
	local function write(instance, id)
		local was = { skillPart = instance.skillPart, skillPartCalcs = instance.skillPartCalcs,
			hadSet = instance.statSet ~= nil, hadSetCalcs = instance.statSetCalcs ~= nil,
			set = instance.statSet and instance.statSet[id], setCalcs = instance.statSetCalcs and instance.statSetCalcs[id] }
		if args.skillPart then
			instance.skillPart = args.skillPart
			instance.skillPartCalcs = args.skillPart
		end
		if args.statSet then
			-- Which of the skill's stat sets PoB calculates (e.g. the Fire, Cold or Lightning storm)
			instance.statSet = instance.statSet or { }
			instance.statSetCalcs = instance.statSetCalcs or { }
			instance.statSet[id] = args.statSet
			instance.statSetCalcs[id] = args.statSet
		end
		return was
	end
	local function unwrite(instance, id, was)
		instance.skillPart, instance.skillPartCalcs = was.skillPart, was.skillPartCalcs
		if was.hadSet then instance.statSet[id] = was.set else instance.statSet = nil end
		if was.hadSetCalcs then instance.statSetCalcs[id] = was.setCalcs else instance.statSetCalcs = nil end
	end
	local wanted = args.skillPart or args.statSet
	-- (PERF14) One recalculation instead of two: with no new gems the list from the last calculation already names
	-- the instance, so the part / stat set is written before the calculation. After it the list is read again: if
	-- it names another instance or skill (or none), the old values go back and the two-step path below runs, so
	-- what is written is the same either way. BRIDGE_SETGROUP = "two" always takes the two-step path; "check" takes
	-- both and raises when PoB's output differs (POE2_SETGROUP, builder/pob/engine.py).
	local early, earlyId, was
	if wanted and not args.gems and BRIDGE_SETGROUP ~= "two" then
		early, earlyId = target()
		if early then
			was = write(early, earlyId)
		end
	end
	recalc()
	if wanted then
		local instance, id = target()
		if early and (instance ~= early or id ~= earlyId) then
			unwrite(early, earlyId, was)
			early = nil
			if not instance then
				recalc()
			end
		end
		if instance and not early then
			write(instance, id)
			recalc()
		elseif instance and BRIDGE_SETGROUP == "check" then
			local one = plainCopy(build.calcsTab.mainOutput)
			unwrite(instance, id, was)
			recalc()
			write(instance, id)
			recalc()
			local where = firstDifference(one, plainCopy(build.calcsTab.mainOutput), "output")
			if where then
				error("setGroup: one recalculation and two give different outputs (BRIDGE_SETGROUP=check): " .. where, 0)
			end
		end
	end
	return { group = describeGroup(args.index, group), stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

function bridge.removeGroup(args)
	local list = build.skillsTab.socketGroupList
	socketGroup(args.index)
	table.remove(list, args.index)
	if build.mainSocketGroup == args.index then
		build.mainSocketGroup = 1
	elseif build.mainSocketGroup > args.index then
		build.mainSocketGroup = build.mainSocketGroup - 1
	end
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Support gems a player can socket (not item- or tree-granted) that PoB says can support an active
-- skill of the group. PoB's legacy gems are returned too (COV23): the caller keeps only the gems
-- Context.allowed_gem offers (context.LIVE_LEGACY names the legacy ones the game still has, e.g.
-- Murderous Intent; the rest stay out there), so the search can offer a live gem PoB marks legacy.
function bridge.supportCandidates(args)
	local group = socketGroup(args.group)
	local names = { }
	for _, gemData in pairs(build.data.gems) do
		local effect = gemData.grantedEffect
		if effect.support and not effect.fromItem and not effect.fromTree then
			for _, activeSkill in ipairs(group.displaySkillList or { }) do
				if calcLib.canGrantedEffectSupportActiveSkill(effect, activeSkill) then
					names[#names + 1] = gemData.name
					break
				end
			end
		end
	end
	table.sort(names)
	return { names = names }
end

-- What-if gem changes through PoB's accelerated path, the one the GUI uses to sort gems by DPS
-- (GemSelectControl:CalcOutputWithThisGem): tree and items are carried over, only the gems
-- change. Each case is one of
--   { group = i, slot = j, gem = name }   put a gem in slot j (j = #gems + 1 appends)
--   { group = i, slot = j, remove = true } disable the gem in slot j
--   { newGroup = {names} }                add a temporary group with these gems
-- Gem requirements are recomputed (not accelerated), so attribute shortfalls show up.
function bridge.gemDeltas(args)
	local calcFunc = calculator()
	local useFullDPS = args.fullDPS == true
	-- (SEARCHB4, 22 Nov) no requirementsItems: with it PoB's misc calculator skips CalcSetup's item block on a reused
	-- environment, and an item's Spirit (CalcSetup 1692: a Sceptre's) and Charm limit (1689) are written into env.modDB,
	-- which wipeEnv clears every call: every gem what-if after the first read a Sceptre build 100 Spirit short
	-- (POE2T6297), a false hard deficit on the supports and Spirit skills. No measurable time cost (POE2T6298)
	local fast = { nodeAlloc = true, skipEHP = args.skipEHP ~= false }
	local list = build.skillsTab.socketGroupList
	local results = { }
	local start = os.clock()
	local function run()
		return calcFunc(nil, useFullDPS, fast)
	end
	for i, case in ipairs(args.cases) do
		local ok, output
		if case.newGroup then
			local group = { label = "", enabled = true, gemList = { }, includeInFullDPS = case.fullDPS or nil }
			for _, name in ipairs(case.newGroup) do
				group.gemList[#group.gemList + 1] = gemInstance(gemByName(name), case.level, case.quality)
			end
			list[#list + 1] = group
			ok, output = pcall(derivedCalc, run, args.fields)
			table.remove(list)
		else
			local gemList = socketGroup(case.group).gemList
			local current = gemList[case.slot]
			if case.remove then
				local enabled = current.enabled
				current.enabled = false
				ok, output = pcall(derivedCalc, run, args.fields)
				current.enabled = enabled
			elseif current then
				local saved = { }
				for _, key in ipairs({ "gemData", "level", "gemId", "nameSpec", "skillId", "displayEffect",
						"reqLevel", "reqStr", "reqDex", "reqInt" }) do
					saved[key] = current[key]
				end
				local gemData = gemByName(case.gem)
				current.gemData, current.gemId, current.nameSpec, current.skillId = gemData, gemData.id, gemData.name, gemData.grantedEffectId
				current.level = case.level or gemData.naturalMaxLevel
				current.displayEffect = nil
				setGemRequirements(current)
				ok, output = pcall(derivedCalc, run, args.fields)
				for key, value in pairs(saved) do
					current[key] = value
				end
			elseif case.slot == #gemList + 1 then
				gemList[case.slot] = gemInstance(gemByName(case.gem), case.level)
				ok, output = pcall(derivedCalc, run, args.fields)
				gemList[case.slot] = nil
			else
				error("slot " .. tostring(case.slot) .. " is past the end of group " .. tostring(case.group))
			end
		end
		if not ok then
			error(output)
		end
		results[i] = output
	end
	return { results = results, seconds = os.clock() - start }
end

-- Configuration ---------------------------------------------------------------------------------

-- (MECH78, MA-7) A config input "bridgeCondition<Name>" sets PoB's Condition:<Name> alone (in combat, as PoB's own boxes
-- do), for a PoB box that also sets something the tool works out itself: Whirlwind's element box (whirlwindBuffCold etc.)
-- also makes the boss Chilled / Ignited / Shocked outright, while the ailment rows give those their real shares.
local function installBridgeConditions()
	local configTab = build.configTab
	if rawget(configTab, "bridgeConditionsWrapped") then
		return
	end
	local original = configTab.BuildModList
	configTab.BuildModList = function(self, ...)
		local results = { original(self, ...) }
		for var, value in pairs(self.input or { }) do
			local name = type(var) == "string" and var:match("^bridgeCondition(%w+)$")
			if name and value then
				self.modList:NewMod("Condition:" .. name, "FLAG", true, "Config", { type = "Condition", var = "Combat" })
			end
		end
		return unpack(results)
	end
	configTab.bridgeConditionsWrapped = true
end

-- Set config options (PoB's Configuration tab), e.g. { usePowerCharges = true }. A value of
-- false or "nil" clears the option back to its default.
function bridge.setConfig(args)
	local configTab = build.configTab
	installBridgeConditions()
	for var, value in pairs(args.vars or { }) do
		if value == false or value == "nil" then
			configTab.input[var] = nil
		else
			configTab.input[var] = value
		end
	end
	configTab:BuildModList()
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

function bridge.getConfig()
	local input = { }
	for var, value in pairs(build.configTab.input) do
		local kind = type(value)
		if kind == "string" or kind == "boolean" or kind == "number" then
			input[var] = value
		end
	end
	return { input = input }
end

-- The boss's own resistances (MECH50; builder/search/bosses.py PRESETS), not PoB's Pinnacle placeholder 50/50/50/0;
-- by default the invented worst-case pinnacle (MECHD5, the user's decision 12 Nov 2026: each stat the worst of the 12
-- real presets), its hits per type included. Must equal bosses.config_vars(bosses.DEFAULT) (tests/test_models.py).
local BOSS_DEFAULT = { enemyFireResist = 45, enemyColdResist = 45, enemyLightningResist = 45, enemyChaosResist = 25,
	enemySpeed = 510, cullingStrikeUniqueThreshold = 4.6086, enemyPhysicalDamage = 1467, enemyFireDamage = 8993,
	enemyColdDamage = 4059, enemyLightningDamage = 3490, enemyChaosDamage = 1471,
	-- (MECHC34, R28-11 CD-1..3: the worst case's Armour +80% / Evasion +40% of the level-82 table, no penetration; PoB's
	-- Pinnacle preset gave 150% / 124.9% (PoE1 bosses' means) and 3% penetration of your elemental resistances)
	enemyArmour = 9675, enemyEvasion = 1317, enemyLightningPen = 0, enemyColdPen = 0, enemyFirePen = 0 }
-- Broken boss faces (Facebreaker's "+3 to +4 per Boss's Face Broken"; COV32, PATCH_CHECK MB-5 / R5): a count the
-- character builds over its campaign and keeps (KeywordPopups 868: each World Screen "Boss Encounter" icon beaten), not
-- a per-fight rate. PoB's box has no default, so it counted 0. 56 for a finished campaign + Interludes: the lower of the
-- sources' two counts (56: Mobalytics / aoeah / u4gm's "176-236"; 60: poe2db's wiki note's "188-252"; the game files
-- have no world-area table to count them). MEDIUM that the count is 56-60, LOW for 56 exactly. A loaded code's own
-- count (the character's tracker) is kept, as its quest choices are. builder/search/assumptions.py facebreaker_notes.
local BOSS_FACES_BROKEN = 56
-- PoB's own Unique Culling Strike threshold (Data/Misc.lua: 5%). The boss's `cullingStrikeUniqueThreshold` (MECHD4,
-- bosses.cull_threshold: 5% x Life / (Life + Energy Shield), so CullMultiplier is the gain over the whole pool) replaces
-- it in data.gameConstants, which CalcOffence reads (4071 / 4075); every resetConfig puts PoB's back first.
local POB_CULL_UNIQUE  -- (read at the first resetConfig, once PoB's data is loaded)
-- The Arbiter of Ash's hits (MECHC6): must equal boss_hits.config_vars(boss_hits.DEFAULT_BOSS) (tests/test_models.py)
local BOSS_HITS_DEFAULT = { enemyPhysicalDamage = 1074, enemyFireDamage = 1282, enemyColdDamage = 0,
	enemyLightningDamage = 0, enemyChaosDamage = 0 }

-- A loaded code's configuration isn't evidence (PLAN.md §5.4): the active config set goes back
-- to what a new build has (PoB's own new-set defaults, the boss preset included), with no custom
-- modifiers, and the boss's resistances (args.boss, else BOSS_DEFAULT). Only the campaign quest
-- choices stay; they're free picks, not conditions. So does the broken-face count (campaign progress, BOSS_FACES_BROKEN).
function bridge.resetConfig(args)
	local configTab = build.configTab
	local id = configTab.activeConfigSetId
	local old = configTab.configSets[id]
	local fresh = configTab:CreateConfigSet(id, old.title)
	for var, value in pairs(old.input) do
		if var:match("^quest") or var == "configBossFaceBroken" then
			fresh.input[var] = value
		end
	end
	fresh.input.configBossFaceBroken = fresh.input.configBossFaceBroken or BOSS_FACES_BROKEN
	POB_CULL_UNIQUE = POB_CULL_UNIQUE or data.gameConstants["CullingStrikeUniqueThreshold"]
	data.gameConstants["CullingStrikeUniqueThreshold"] = POB_CULL_UNIQUE
	-- The boss's hits per damage type (MECHC6, builder/search/boss_hits.py config_vars: the game's monster tables, not
	-- PoB's Pinnacle 965 per type): each type the boss deals at its full size, the others 0 (args.bossHits, else the
	-- Arbiter's). Written first: the boss's own config (bosses.config_vars, MECHD5: it carries the boss's hits) wins.
	for var, value in pairs((args and args.bossHits) or BOSS_HITS_DEFAULT) do
		fresh.input[var] = value
	end
	for var, value in pairs((args and args.boss) or BOSS_DEFAULT) do
		fresh.input[var] = value
		if var == "cullingStrikeUniqueThreshold" then
			data.gameConstants["CullingStrikeUniqueThreshold"] = value
		end
	end
	configTab:SetActiveConfigSet(id, false, true)
	recalc()
	return bridge.getConfig()
end

-- Assumptions: modifiers for what PoB doesn't model (e.g. how often a skill is triggered when its
-- trigger source isn't in PoB), kept in the config modifier list so they reach every calculation,
-- what-ifs included. They live only in this engine: exported codes don't carry them.
local assumedMods = { }

-- A custom model's modifier line (kind "Line"): PoB parses `line`, and each parsed modifier is
-- scaled by `scale` and gets the model's tags:
--   when = { {var = "ArchonMana"}, {any = {"A", "B"}, neg = true} }  ledger conditions (all must hold);
--                                      {pob = "Unbound"}: a PoB condition itself, not a ledger one
--   per = "ArchonDuration", base = b   value = scale * Multiplier:LedgerArchonDuration + b, the
--                                      linear part of a model around the current build, so
--                                      what-ifs that add or remove its passives see the change
--   stat = "Armour", base = b          value = scale * the build's Armour (a PoB output stat) + b,
--                                      so what-ifs that change that stat see the change
--   skill = name                       only for that skill
local function lineMods(mod)
	local list, extra = modLib.parseMod(mod.line)
	if not list or extra then
		error("an assumption line PoB can't parse: " .. tostring(mod.line))
	end
	local mods = { }
	for _, parsed in ipairs(list) do
		local copy = copyTable(parsed)
		if mod.scale and type(copy.value) == "number" then
			copy.value = copy.value * mod.scale
		elseif mod.scale and type(copy.value) == "table" and type(copy.value.mod) == "table"
				and type(copy.value.mod.value) == "number" then
			-- a LIST of one nested mod (EnemyModifier: "Enemies you Mark take 1% increased damage"): scale the inner value
			copy.value.mod.value = copy.value.mod.value * mod.scale
		end
		for _, condition in ipairs(mod.when or { }) do
			if condition.pob then
				-- a PoB condition itself (MECHC10: Condition:Unbound, set by the conditions' box)
				copy[#copy + 1] = { type = "Condition", var = condition.pob, neg = condition.neg or nil }
			elseif condition.any then
				local vars = { }
				for i, var in ipairs(condition.any) do
					vars[i] = "Ledger" .. var
				end
				copy[#copy + 1] = { type = "Condition", varList = vars, neg = condition.neg or nil }
			else
				copy[#copy + 1] = { type = "Condition", var = "Ledger" .. condition.var, neg = condition.neg or nil }
			end
		end
		if mod.per then
			copy[#copy + 1] = { type = "Multiplier", var = "Ledger" .. mod.per, base = mod.base or 0 }
		elseif mod.stat then
			copy[#copy + 1] = { type = "PerStat", stat = mod.stat, base = mod.base or 0 }
		end
		if mod.skill then
			copy[#copy + 1] = { type = "SkillName", skillName = mod.skill }
		end
		if mod.group then  -- (MECHB62, U1) only that socket group's copy of the skill: installGroupAssumptions
			copy[#copy + 1] = { type = "Condition", var = "BridgeGroup" .. mod.group }
		end
		modLib.setSource(copy, "Assumption")
		mods[#mods + 1] = copy
	end
	return mods
end

-- (MECHB62, U1 from COV56's calibration, probe POE2T8044) An assumption for ONE socket group's copy of a skill
-- (models.in_group: a mod with `group`, the group's index in socketGroupList). PoB has no tag for a socket group, and
-- its SkillName tag reaches every copy of a spell (Comet cast by hand and in two meta gems), keeping the LAST SkillData
-- of a key (CalcActiveSkill 919-920). So each player skill built in a group gets skillCond["BridgeGroup<index>"]
-- (ModStore's Condition tag reads cfg.skillCond; weapon1Cfg / weapon2Cfg inherit it through their __index), which a
-- group's Line / Raw mods are tagged with, and a group's SkillData is set on that copy after PoB's own extraction
-- (CalcActiveSkill 915-920: so it wins over a name-wide one).
local function installGroupAssumptions()
	local calcs = require("Modules.CalcBase")
	if calcs.ledgerGroupAssumptions or not calcs.buildActiveSkillModList then
		return
	end
	calcs.ledgerGroupAssumptions = true
	local buildModList = calcs.buildActiveSkillModList
	calcs.buildActiveSkillModList = function(env, activeSkill, ...)
		local results = { buildModList(env, activeSkill, ...) }
		local group = activeSkill and activeSkill.socketGroup
		local cfg = activeSkill and activeSkill.skillCfg
		if group and cfg and cfg.skillCond and build and build.skillsTab then
			local index
			for i, candidate in ipairs(build.skillsTab.socketGroupList) do
				if candidate == group then
					index = i
					break
				end
			end
			if index then
				cfg.skillCond["BridgeGroup" .. index] = true
				local name = (cfg.skillName or ""):lower()
				for _, mod in ipairs(assumedMods) do
					if mod.kind == "SkillData" and mod.group == index and (not mod.skill or mod.skill:lower() == name) then
						activeSkill.skillData[mod.key] = mod.value
					end
				end
			end
		end
		return unpack(results)
	end
end

local function installAssumptions()
	installGroupAssumptions()
	local configTab = build.configTab
	if not rawget(configTab, "bridgeWrapped") then
		local original = configTab.BuildModList
		configTab.BuildModList = function(self, ...)
			local results = { original(self, ...) }
			for _, mod in ipairs(assumedMods) do
				local tags = mod.skill and { { type = "SkillName", skillName = mod.skill } } or { }
				if mod.condition then  -- (MECH75: models.raw_mod's condition, e.g. "Shocked": only while you are)
					tags[#tags + 1] = { type = "Condition", var = mod.condition }
				end
				-- (MECHC32: models.raw_mod's not_condition, only while the condition is off, as PoB's "if you have not been
				-- Hit Recently" lines; slots, only on those items' own defences, as PoB's SlotName lines, CalcDefence 1212-1301)
				if mod.notCondition then
					tags[#tags + 1] = { type = "Condition", var = mod.notCondition, neg = true }
				end
				if mod.slots then
					tags[#tags + 1] = { type = "SlotName", slotNameList = mod.slots }
				end
				if mod.group then  -- (MECHB62, U1) only that socket group's copy of the skill: installGroupAssumptions
					tags[#tags + 1] = { type = "Condition", var = "BridgeGroup" .. mod.group }
				end
				if mod.kind == "SkillData" and mod.group then
					-- (set on that group's copy by installGroupAssumptions, after PoB's own SkillData)
				elseif mod.kind == "SkillData" then
					self.modList:NewMod("SkillData", "LIST", { key = mod.key, value = mod.value }, "Assumption", unpack(tags))
				elseif mod.kind == "Condition" then  -- a ledger condition the build's skills switch on
					self.modList:NewMod("Condition:Ledger" .. mod.name, "FLAG", true, "Ledger")
				elseif mod.kind == "Raw" then  -- a PoB modifier by name (models.raw_mod), e.g. a skill's DebuffEffect MORE
					-- (mod.tag: a sub-source, "Assumption:<tag>", that a bridge read can leave out: models.TOTEM_ONE_TAG)
					local source = "Assumption" .. (mod.tag and (":" .. mod.tag) or "")
					if mod.enemy then  -- (MECH107: models.raw_mod's enemy, a mod ON THE ENEMY, as PoB's config boxes put
						-- theirs in enemyModList: carried as the player's EnemyModifier, which CalcPerform's applyEnemyModifiers
						-- adds to env.enemy's modDB; the player's and the minions' hits both read that enemy (CalcActiveSkill
						-- 964-967: a non-hostile minion's enemy is env.enemy), e.g. Critical Weakness's SelfCritChance BASE,
						-- CalcOffence 3881)
						self.modList:NewMod("EnemyModifier", "LIST",
							{ mod = modLib.createMod(mod.name, mod.type, mod.value, source) }, source, unpack(tags))
					elseif mod.keywordFlags then  -- (GEM22: models.raw_mod's keywords, e.g. {"Ignite"}: only that ailment's
						-- calculation, whose cfg carries KeywordFlag[ailment], CalcOffence 5206-5213)
						local bits = 0
						for _, name in ipairs(mod.keywordFlags) do
							bits = bit.bor(bits, KeywordFlag[name] or 0)
						end
						self.modList:NewMod(mod.name, mod.type, mod.value, source, 0, bits, unpack(tags))
					else
						self.modList:NewMod(mod.name, mod.type, mod.value, source, unpack(tags))
					end
				elseif mod.kind == "Line" then
					for _, parsed in ipairs(mod.parsed) do
						self.modList:AddMod(parsed)
					end
				end
			end
			return unpack(results)
		end
		configTab.bridgeWrapped = true
	end
	configTab:BuildModList()
end

-- Replace the assumptions: { mods = { {kind = "SkillData", skill = name, key = k, value = v},
-- {kind = "Condition", name = n}, {kind = "Line", line = "...", ...} } }
function bridge.setAssumptions(args)
	local mods = args.mods or { }
	for _, mod in ipairs(mods) do
		if mod.kind == "Line" then
			mod.parsed = lineMods(mod)  -- parsed once, not on every rebuild of the config list
		end
	end
	assumedMods = mods
	derivedInputs, derivedValues = { }, { }
	for _, mod in ipairs(mods) do
		if mod.kind == "Derived" then
			derivedInputs[#derivedInputs + 1] = mod
			derivedValues[mod.name] = mod.value or 0
		end
	end
	installDerived()
	installAssumptions()
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- How fast each of these groups' active skill is used if it were the main skill (models.press_model: a pressed
-- non-main skill counts in Full DPS at its real use, not nonstop). Each group is calculated in its own environment,
-- as calcFullDPS does; the build's main group and outputs are left as they were.
-- (MECH58) The main skill's raw Culling Strike thresholds as PoB's CalcOffence 4070-4077 reads them, before it scales the
-- crit-only one by 1 - (1 - crit)^hitRate: critical (CriticalCullPercent override or CritCanCull's threshold for the
-- enemy's rarity), regular (CullPercent override or CanCull's), inc (1 + INC CullPercent / 100), critChance (%)
-- (MECH64) cullSplitOf(env, skill) gives the same for any skill calculated as the main one (bridge.useRates' cullSplit,
-- for a Cull on another skill than the main one)
local function cullSplitOf(env, skill)
	if not skill or not skill.skillModList then
		return { critical = 0, regular = 0, inc = 1, critChance = 0 }
	end
	local mods, cfg = skill.skillModList, skill.skillCfg
	local enemyDB = env.player.enemy and env.player.enemy.modDB
	local rarity = enemyDB and (enemyDB:Flag(nil, "Condition:Unique") and "Unique"
		or (enemyDB:Flag(nil, "Condition:RareOrUnique") and "Rare")) or "Normal"
	local threshold = data.gameConstants["CullingStrike" .. rarity .. "Threshold"] or 0
	local critical = mods:Override(cfg, "CriticalCullPercent") or (mods:Flag(cfg, "CritCanCull") and threshold or 0)
	local regular = mods:Override(cfg, "CullPercent") or (mods:Flag(cfg, "CanCull") and threshold or 0)
	local output = env.player.output or { }
	-- (MECH59) heavyOnly: every CanCull that holds is Heavy Contact's (ModParser 5470: CanCull under the condition
	-- AlwaysHeavyStunning), so the regular Cull lands only on a Heavy Stun
	local heavyOnly = false
	if regular > 0 and not mods:Override(cfg, "CullPercent") then
		heavyOnly = true
		for _, row in ipairs(mods:Tabulate("FLAG", cfg, "CanCull")) do
			local tagged = false
			for _, tag in ipairs(row.mod) do
				if type(tag) == "table" and tag.type == "Condition" and tag.var == "AlwaysHeavyStunning" then
					tagged = true
				end
			end
			if not tagged then
				heavyOnly = false
			end
		end
	end
	-- (MECH63) where each CanCull that holds comes from, and its tags' kinds, for the Empowered Cull's check
	local rows = { }
	for _, row in ipairs(mods:Tabulate("FLAG", cfg, "CanCull")) do
		local tags = { }
		for _, tag in ipairs(row.mod) do
			if type(tag) == "table" and tag.type then
				tags[#tags + 1] = tag.type .. (tag.var and (":" .. tostring(tag.var)) or "")
					.. (tag.effectType and (":" .. tostring(tag.effectType)) or "")
			end
		end
		rows[#rows + 1] = { source = tostring(row.mod.source or ""), tags = table.concat(tags, ",") }
	end
	-- (MECH63) empowerOnly: every CanCull that holds is Murderous Intent's, which PoB copies onto the Empowering skill's
	-- buff (CalcActiveSkill applyExtraEmpowerMods, without the Empowered mod's Spell flag), so every hit Culls (POE2T588:
	-- Mana Tempest + Murderous Intent gave 2b75c's Explosive Spear, an attack, a 5% Cull); the game's Cull is on the
	-- Empowered uses only (Assumptions.empowered_uses)
	local empowerOnly = regular > 0 and not mods:Override(cfg, "CullPercent") and #rows > 0
	for _, row in ipairs(rows) do
		if row.source ~= "Skill:SupportEmpoweredCullPlayer" then
			empowerOnly = false
		end
	end
	return { critical = jsonNumber(critical), regular = jsonNumber(regular), heavyOnly = heavyOnly, empowerOnly = empowerOnly, rows = rows,
		inc = jsonNumber(1 + mods:Sum("INC", cfg, "CullPercent") / 100), critChance = jsonNumber(output.CritChance or 0) }
end

function bridge.cullSplit()
	local env = build.calcsTab.mainEnv
	return cullSplitOf(env, env and env.player and env.player.mainSkill)
end

function bridge.useRates(args)
	local calcs = require("Modules.CalcBase")
	local saved = build.mainSocketGroup
	local rates = { }
	-- (GEM5: Elemental Discharge, models.discharge_element_hits) args.config: config inputs set for this call only, as
	-- setConfig sets them ({ var = value }, false for off), put back before it returns, e.g. the three Consumed boxes
	-- PoB's triggered Elemental Discharge deals its damage under (ConfigOptions conditionIgniteConsumed etc.)
	local configTab = build.configTab
	local savedInput = { }
	if args and args.config then
		for var, value in pairs(args.config) do
			savedInput[var] = { configTab.input[var] }
			configTab.input[var] = (value ~= false and value ~= "nil") and value or nil
		end
		configTab:BuildModList()
	end
	for n, index in ipairs(args and args.indices or { }) do
		build.mainSocketGroup = index
		-- (MECHB10: args.actives[n], if given, is the active skill of that group to rate, for a skill Full DPS counts
		-- beside the group's calculated one, e.g. Poison Spores' Pustule Explosion; put back after)
		local group = build.skillsTab.socketGroupList[index]
		local active = args.actives and tonumber(args.actives[n])
		-- (initEnv's "CALCULATOR" mode reads mainActiveSkill; only "CALCS" reads mainActiveSkillCalcs: CalcSetup 2393-2397)
		local savedActive = group and group.mainActiveSkill
		if group and active and active > 0 then
			group.mainActiveSkill = active
		end
		-- (MECH82: Oil Ground's Ignite, conditions.oil_ground) args.statSets[n], if given, is the stat set of that group's
		-- active gems to rate (CALCULATOR mode reads gemInstance.statSet[grantedEffect.id]: CalcSetup 2300-2304), e.g. Oil
		-- Grenade's "Oil Ground" set, whose Fire damage the lit ground's Ignite adds; put back after
		local statSetIndex = args.statSets and tonumber(args.statSets[n])
		local savedSets = { }
		if group and statSetIndex and statSetIndex > 0 then
			for _, gemInstance in ipairs(group.gemList or { }) do
				local effect = gemInstance.gemData and gemInstance.gemData.grantedEffect
				if effect and not effect.support then
					savedSets[#savedSets + 1] = { gemInstance, gemInstance.statSet, effect.id,
						gemInstance.statSet and gemInstance.statSet[effect.id] }
					gemInstance.statSet = gemInstance.statSet or { }
					gemInstance.statSet[effect.id] = statSetIndex
				end
			end
		end
		local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
		if group and active and active > 0 then
			group.mainActiveSkill = savedActive
		end
		-- (initEnv copied the index into each activeEffect.statSet, a new table: the gems' own choice can go back now)
		for _, saved in ipairs(savedSets) do
			local gemInstance, table0, id, index0 = saved[1], saved[2], saved[3], saved[4]
			if table0 == nil then
				gemInstance.statSet = nil
			else
				gemInstance.statSet = table0
				table0[id] = index0
			end
		end
		local skill = ok and env and env.player.mainSkill
		if skill and pcall(calcs.perform, env, true) then
			local output = env.player.output
			rates[#rates + 1] = {
				index = index, name = calcs.getActiveSkillDisplayName(skill), active = active,
				-- (MECHB10) a DetonatesAfterTime skill's fuse, PoB's DetonationTime (base x inc x more)
				detonationTime = output.DetonationTime and jsonNumber(output.DetonationTime) or nil,
				speed = jsonNumber(output.Speed or 0), cooldown = output.Cooldown and jsonNumber(output.Cooldown) or nil,
				-- (MECH79, conditions' Incinerate fuel) one use's Mana cost, PoB's ManaCost
				manaCost = type(output.ManaCost) == "number" and jsonNumber(output.ManaCost) or nil,
				-- (MECHB64, models.mana_cap_row) its Mana a second used nonstop, PoB's ManaPerSecondCost (a channel's
				-- per-second cost, GEM15's cast-speed override in it; else ManaCost x Speed)
				manaPerSecond = type(output.ManaPerSecondCost) == "number" and jsonNumber(output.ManaPerSecondCost) or nil,
				lifePerSecond = type(output.LifePerSecondCost) == "number" and jsonNumber(output.LifePerSecondCost) or nil,
				-- (MECHC63, conditions.RAGE_COST_LINE) one use's upfront Rage cost, PoB's RageCost
				rageCost = type(output.RageCost) == "number" and jsonNumber(output.RageCost) or nil,
				repeats = jsonNumber(output.Repeats or 1),
				-- (MECH57) its minion's counted skill's uses per second, PoB's minion Speed (cooldown-capped, CalcOffence 3008)
				minionSpeed = env.minion and env.minion.output and type(env.minion.output.Speed) == "number"
					and jsonNumber(env.minion.output.Speed) or nil,
				duration = output.Duration and jsonNumber(output.Duration) or nil,
				-- (MECHC51, R44-5) the skill's Skill Effect Duration multiplier (CalcOffence's DurationMod), for a
				-- duration the skill's own data doesn't carry (Charged Staff's 6 s a Power Charge)
				durationMod = type(output.DurationMod) == "number" and jsonNumber(output.DurationMod) or nil,
				-- (MECHB39: models.totem_deaths) a totem skill's own totem duration with the build's modifiers
				totemDuration = type(output.TotemDuration) == "number" and jsonNumber(output.TotemDuration) or nil,
				-- (MECHB40: models.tawhoa_model) a totem skill's totem's Life with the build's modifiers (PoB's TotemLife,
				-- CalcOffence 1944-1945: the ally life table x the totem's multiplier x TotemLife inc / more)
				totemLife = type(output.TotemLife) == "number" and jsonNumber(output.TotemLife) or nil,
				-- (MECHB39: models.duration_press_rate) a Projectile skill's Duration is its projectiles' life, not a
				-- thing kept up: pressing again adds projectiles
				projectile = (skill.skillTypes and skill.skillTypes[SkillType.Projectile]) and true or nil,
				castTime = jsonNumber(skill.activeEffect.grantedEffect.castTime or 0),
				-- (MECHB51: models.bear_stance_model) the divisor PoB puts on an attack's base time (CalcOffence 2839:
				-- baseTime / round((1 + inc / 100) x more, 2)) with the main hand's cfg, and the BASE "Speed" seconds in that base
				-- time (2741-2743): so a model can turn base seconds into final ones
				speedMod = (function()
					local cfg = skill.weapon1Cfg or skill.skillCfg
					local list = skill.skillModList
					local inc = list:Sum("INC", cfg, "Speed")
					return jsonNumber(round((1 + inc / 100) * list:More(cfg, "Speed"), 2))
				end)(),
				baseSpeedAdd = jsonNumber(skill.skillModList:Sum("BASE", skill.weapon1Cfg or skill.skillCfg, "Speed")),
				-- PoB's HitTime or Time; NOTE Time is 1 / Speed AFTER the cooldown cap (CalcOffence 3008 then 3052), so for a
				-- cooldown-capped skill it is the capped interval, not one use (models.press_action, MECH94)
				time = (output.HitTime or output.Time) and jsonNumber(output.HitTime or output.Time) or nil,
				-- (MECH94: conditions.Conditions._creeping_chill_magnitude) an Ice Crystal skill's crystal Life with the
				-- build's modifiers (CalcOffence 1724-1726) and its Chill magnitude multiplier (5689)
				iceCrystalLife = (type(output.IceCrystalLife) == "number" and output.IceCrystalLife > 0)
					and jsonNumber(output.IceCrystalLife) or nil,
				chillEffectMod = type(output.ChillEffectMod) == "number" and jsonNumber(output.ChillEffectMod) or nil,
				-- (MECHB47: models.press_action) one use's rate BEFORE the cooldown cap: PoB's CastRate (CalcOffence 2983-3003:
				-- 1 / (cast or attack time) with the build's speed, action speed and totem speed), set before Speed is capped at
				-- 3008; an attack's own pass output where the combined one lacks it. A Warcry's is 1 / its WarcryCastTime
				-- (calcWarcryCastTime, 435: warcry speed and action speed in it): PoB's CastRate multiplies that by the action
				-- speed a second time (2978 then 2985, probe POE2T4700: 0.8 s base, action speed 0.517, WarcryCastTime 1.547 s,
				-- CastRate 1 / 2.99 s); an instant Warcry (WarcryCastTime 0) one server tick
				castRate = (type(output.WarcryCastTime) == "number" and (output.WarcryCastTime > 0
						and jsonNumber(1 / output.WarcryCastTime) or jsonNumber(data.misc.ServerTickRate)))
					or (type(output.CastRate) == "number" and output.CastRate > 0 and jsonNumber(output.CastRate))
					or (output.MainHand and type(output.MainHand.CastRate) == "number" and output.MainHand.CastRate > 0
						and jsonNumber(output.MainHand.CastRate)) or nil,
				-- each ailment's stacks at that speed over the most that count (PoB: chance x duration x rate / max)
				stackPotential = setmetatable({ Ignite = output.IgniteStackPotential and jsonNumber(output.IgniteStackPotential),
					Bleed = output.BleedStackPotential and jsonNumber(output.BleedStackPotential),
					Poison = output.PoisonStackPotential and jsonNumber(output.PoisonStackPotential) }, { __jsontype = "object" }),
			}
			-- (MECHB7, for COVE8: a side skill's own Heavy Stun buildup, hit / crit chance and per-type averages under
			-- PoB's own output names, models.HEAVY_STUN_GROUP_FIELDS: Practical Magic on a stunning side skill)
			local entry = rates[#rates]
			-- (MECH64, conditions.cull_window_scale's other skill) where this skill Culls, its Culling multiplier, hit
			-- chance, hit DPS and raw thresholds, so a Cull on another skill than the main one is priced at its own hits
			if type(output.CullMultiplier) == "number" and output.CullMultiplier > 1 then
				entry.cullMultiplier = jsonNumber(output.CullMultiplier)
				entry.hitChance = jsonNumber(output.HitChance or 100)
				entry.hitDPS = jsonNumber(output.TotalDPS or 0)
				entry.cullSplit = cullSplitOf(env, skill)
			end
			-- (MECH63, Assumptions.empowered_uses) a Warcry's Empowered attacks per use, PoB's Num<Warcry>Empowers
			-- (CalcPerform 2196-2215: its Power over the boss's, its extra Empowers), named as CalcPerform names it
			if skill.skillTypes and skill.skillTypes[SkillType.Warcry] then
				-- (MECH96: conditions.use_ceiling, for every reader of the row: a Warcry's PoB Speed counts the action
				-- speed twice, its castRate doesn't; MECHB47 flagged rows only inside Assumptions.rate_presses)
				entry.warcry = true
				local warcryName = entry.name:gsub(" Cry", ""):gsub("'s", ""):gsub(" ", "")
				local count = env.player.modDB:Sum("BASE", nil, "Num" .. warcryName .. "Empowers")
				entry.warcryEmpowers = count > 0 and jsonNumber(count) or nil
				-- (MECHB40: models.echoing_power_model) the Power PoB counts (CalcPerform 2201's formula, global) and this
				-- Warcry's Empowers per Power and cap (its WarcryPowerPer / WarcryPowerCap stats)
				local modDB = env.player.modDB
				entry.warcryPower = jsonNumber(modDB:Override(nil, "WarcryPower") or math.max((modDB:Sum("BASE", nil, "WarcryPower") or 0)
					* (1 + (modDB:Sum("INC", nil, "WarcryPower") or 0) / 100), (modDB:Sum("BASE", nil, "MinimumWarcryPower") or 0)))
				entry.warcryPowerPer = jsonNumber(skill.skillModList:Sum("BASE", nil, "WarcryPowerPer") or 0)
				entry.warcryPowerCap = jsonNumber(skill.skillModList:Sum("BASE", nil, "WarcryPowerCap") or 0)
			end
			-- (MECHB12, AUDIT25 A25-2: Minion Pact) the group's minion's maximum Life, which PoB calculates only for the
			-- main skill's minion (env.minion)
			if env.minion and type(env.minion.output.Life) == "number" then
				entry.minionLife = jsonNumber(env.minion.output.Life)
			end
			-- (MECHB14: Toxic Growth's Pustule Limit, models.pustule_cap) the scored set's
			-- number_of_poisonbloom_arrow_bloom_allowed with quality, plus "+N to Limit" lines, which PoB maps to
			-- AdditionalCooldownUses (SkillStatMap base_limit_+; ModCache "Skills have +1 to Limit")
			local set = skill.activeEffect.statSet
			set = set and (set.statSet or set)
			if set and set.constantStats then
				local okStats, stats = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect, skill.activeEffect.grantedEffect, set,
					altGemQuality())
				local base = okStats and stats and stats["number_of_poisonbloom_arrow_bloom_allowed"]
				if base and base > 0 then
					entry.pustuleLimit = jsonNumber(base + skill.skillModList:Sum("BASE", skill.skillCfg, "AdditionalCooldownUses"))
				end
				-- (MECHB39, SEARCH28 item 4a: a chosen press rate for a duration skill, models.press_use_rate) how many of
				-- its instances can be up at once: its own "number_of_X_allowed" / "base_number_of_X_allowed" stat
				-- (Lightning Rod's number_of_lightning_rods_allowed 10), plus "+N to Limit" lines (AdditionalCooldownUses, as
				-- the Pustules above). Totems and minions have their own limits (totemLimit, summon.limit): left out here.
				local most
				for id, value in pairs(okStats and stats or {}) do
					if type(value) == "number" and value > 0 and (id:match("^number_of_.+_allowed$")
						or id:match("^base_number_of_.+_allowed$")) then
						if not id:match("totem") and not id:match("minion") and not id:match("skeleton")
							and not id:match("zombie") and not id:match("spectre") and not id:match("golem") then
							most = math.max(most or 0, value)
						end
					end
				end
				if most then
					entry.instanceLimit = jsonNumber(most + skill.skillModList:Sum("BASE", skill.skillCfg, "AdditionalCooldownUses"))
				end
				-- (MECHB15, AUDIT25 A25-8: the Pustules' stored Poison, models.pustule_stored) the set's own 1% more per X
				-- stored Poison damage (by gem level) and its maximum (300); PoB reads neither
				local per = okStats and stats and stats["toxic_pustule_burst_1%_more_damage_per_x_stored_poison"]
				if per and per > 0 then
					entry.storedPoisonPerPercent = jsonNumber(per)
					entry.storedPoisonMaxMore = jsonNumber(stats["toxic_pustule_max_burst_damage_+%_final_from_stored_poison"] or 0)
				end
			end
			-- (MECHB13: Runic Ward pays for Ward-cost skills, models.ward_plan) one use's Runic Ward cost, PoB's WardCost
			if type(output.WardCost) == "number" and output.WardCost > 0 then
				entry.wardCost = jsonNumber(output.WardCost)
			end
			-- (MECHB28: Orb of Storms as a side skill, models.orb_cycle) whether the rated skill is a Spell the player
			-- casts (not triggered, not channelled): the casts that make a side Orb fire; and for Orb of Storms itself its
			-- set's maximum hits (with quality, and the second quality stat when PoB's GemlingQuality flag is on:
			-- CalcSetup 1142) and bolt frequency
			local types = skill.skillTypes or { }
			if types[SkillType.Spell] then
				entry.spell = true
				entry.triggered = (types[SkillType.Triggered] or (skill.skillFlags and skill.skillFlags.triggered)) and true or nil
				entry.channel = types[SkillType.Channel] and true or nil
			end
			if set and set.constantStats then
				local okOrb, orbStats = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect,
					skill.activeEffect.grantedEffect, set, env.useAltGemQualityStats)
				local hits = okOrb and orbStats and orbStats["orb_of_storms_maximum_number_of_hits"]
				if hits and hits > 0 then
					entry.orbMaxHits = jsonNumber(hits)
					entry.orbBoltFrequency = jsonNumber(orbStats["orb_of_storms_base_bolt_frequency_ms"] or 0)
				end
			end
			-- (MECHB14: IgniteDuration, how long an Ignite of the group lasts: Frostflame Nova's ice per cast, models.frostflame_plan)
			-- (MECH69: PhysicalStoredCombinedAvg / PhysicalStoredHitAvg, the group's pre-mitigation Physical hit, crit-weighted /
			-- not: High Velocity Rounds' Riven Armour magnitude, GEM3's models.riven_armour_model)
			-- (MECH74: IgniteDPS / IgniteChancePerHit, the group's own Ignite: His Winnowing Flame's Grisly Pyres Consume
			-- another group's Ignite, assumptions.grisly_pyre)
			-- (MECH75: ShockChancePerHit, the group's Shocks: Overcharge reflects them back to you, conditions._self_shocked)
			-- (MECH76: the Ledger Impale fields, setImpale's, with AverageHit / TotalDPS: another group's Impales, models.impale_model)
			-- (MECH84: Freeze / Electrocute / Pin BuildupAvg, the Thorns group's buildup, models.thorns_fills)
			for _, field in ipairs({ "HeavyStunBuildupAvg", "FreezeBuildupAvg", "ElectrocuteBuildupAvg", "PinBuildupAvg",
					"HitChance", "CritChance", "IgniteDuration",
					"PhysicalStoredCombinedAvg", "PhysicalStoredHitAvg", "IgniteDPS", "IgniteChancePerHit",
					"ShockChancePerHit", "LedgerImpaleChance", "LedgerImpaleChanceCrit", "LedgerImpaleEffect",
					"LedgerImpaleCount", "LedgerImpaleExtract", "AverageHit", "TotalDPS",
					-- (MECH88: each group's own ailment DPS, for the "Best <ailment> DPS" row when its source is used 0 times a
					-- second: Assumptions.zero_use_ailments takes the next-best group's)
					"PoisonDPS", "BleedDPS", "CorruptingBloodDPS", "BurningGroundDPS", "CausticGroundDPS",
					-- (MECH95: each group's hit and crit by damage type, for its Heavy Stun buildup's Cold share,
					-- models.stun_uncold: Cold hits build none; conditions.buildup_others, models.heavy_stun_rate)
					"PhysicalHitAverage", "PhysicalCritAverage", "FireHitAverage", "FireCritAverage", "ColdHitAverage",
					"ColdCritAverage", "LightningHitAverage", "LightningCritAverage", "ChaosHitAverage",
					"ChaosCritAverage",
					-- (MECH102: each group's own Shock magnitude multiplier and duration, PoB's ShockEffectMod / ShockDuration
					-- (CalcOffence 5729-5731: Enemy/SelfShockMagnitude, the group's own supports included), for its Shocks
					-- on the boss beside the main skill's: conditions.shock_others, the strongest live Shock applying)
					"ShockEffectMod", "ShockDuration",
					-- (MECHC56: each group's own Chill, for the Glory of a skill fed by Chills, conditions.side_hit_rows:
					-- PoB's ChillChanceOnHit and ChillEffectMod, and setChillDamage's ChillHitDamage / ChillCritDamage for
					-- the game's 30% floor, conditions._chill_per_hit)
					"ChillChanceOnHit", "ChillEffectMod", "ChillHitDamage", "ChillCritDamage",
					-- (MECHC56, RESEARCH51 R51-3: a Poison is a Chaos damage over time Debuff, which builds Archon of
					-- Chayula's Glory: each group's chance to Poison)
					"PoisonChanceOnHit", "PoisonChanceOnCrit" }) do
				if type(output[field]) == "number" then
					entry[field] = jsonNumber(output[field])
				end
			end
			-- (MECHC56: the damage types an Attack's hit carries, for Glory fed by Elemental or Chaos hits, conditions.
			-- side_hit_rows' "types": PoB keeps an Attack's <Type>HitAverage per hand and leaves the top level empty, as
			-- MECHB44's AverageHit below; given under their own keys, so the top-level readers are unchanged)
			for _, hand in ipairs({ "MainHand", "OffHand" }) do
				if type(output[hand]) == "table" then
					for _, t in ipairs(CHILL_TYPES) do
						local v = output[hand][t .. "HitAverage"]
						if type(v) == "number" and v > 0 then
							entry[hand .. "." .. t .. "HitAverage"] = jsonNumber(v)
						end
					end
				end
			end
			-- (MECHB44: models.ll_hits_a_use) an Attack keeps its average hit per hand (output.MainHand / OffHand.AverageHit),
			-- not at the top level, so Living Lightning read one hit a use on every Attack (an UNDERCOUNT, probe POE2T4643)
			if type(output.AverageHit) ~= "number" then
				for _, hand in ipairs({ "MainHand", "OffHand" }) do
					if output[hand] and type(output[hand].AverageHit) == "number" then
						entry[hand .. ".AverageHit"] = jsonNumber(output[hand].AverageHit)
					end
				end
			end
			local shock = shockChancePerHit(output)  -- (MECH86: PoB never writes ShockChancePerHit; shockChancePerHit)
			if shock then
				entry.ShockChancePerHit = jsonNumber(shock)
			end
			for _, kind in ipairs({ "Physical", "Fire", "Cold", "Lightning", "Chaos" }) do
				for _, part in ipairs({ "Hit", "Crit" }) do
					local field = kind .. part .. "Average"
					if type(output[field]) == "number" then
						entry[field] = jsonNumber(output[field])
					end
					-- (MECH82: an attack's per-hand figures, PoB's output.MainHand: Oil Ground's Ignite adds a share of
					-- "main hand total attack fire damage", conditions.oil_ground)
					if output.MainHand and type(output.MainHand[field]) == "number" then
						entry["MainHand." .. field] = jsonNumber(output.MainHand[field])
					end
				end
			end
		end
	end
	build.mainSocketGroup = saved
	if next(savedInput) then
		for var, old in pairs(savedInput) do
			configTab.input[var] = old[1]
		end
		configTab:BuildModList()
	end
	return { rates = rates }
end

-- (MECHB20) One of a minion's other skills as PoB would score it if the minion used it: its hit and speed. PoB counts
-- only the minion's chosen skill (CalcActiveSkill 1195-1203: srcInstance.skillMinionSkill, an index into the minion's
-- skill list, which the tool never sets, so its first skill), so a Command's damage (the Azmerian Wolf's Eternal Hunt,
-- models.eternal_hunt_model) is never scored. args.index: the group; args.active: the summon skill's position in it;
-- args.skill: the minion skill's id (GreatHuntPackleaderMinion). Gives the minion's AverageHit, AverageDamage (with its
-- hit chance), Speed and Cooldown with that skill chosen, and baseDPS, the minion's TotalDPS on its own counted skill;
-- the main group, chosen skill and outputs are put back.
function bridge.minionSkillHit(args)
	local calcs = require("Modules.CalcBase")
	local group = build.skillsTab.socketGroupList[args.index]
	local position = tonumber(args.active) or (group and group.mainActiveSkill) or 1
	local activeSkill = group and group.displaySkillList and group.displaySkillList[position]
	local instance = activeSkill and activeSkill.activeEffect.srcInstance
	local granted = activeSkill and activeSkill.activeEffect.grantedEffect
	-- (the display list's skill may carry no minion: its own minion data, else the skill's first listed minion's)
	local minionData = activeSkill and activeSkill.minion and activeSkill.minion.minionData
		or (granted and granted.minionList and data.minions[granted.minionList[1]])
	if not instance or not minionData then
		return { reason = not instance and "no such skill in the group" or "the skill summons no minion" }
	end
	local found
	local n = 0
	for _, id in ipairs(minionData.skillList or { }) do
		if data.skills[id] then  -- (as CalcActiveSkill 1143-1146 lists them)
			n = n + 1
			if id == args.skill then
				found = n
			end
		end
	end
	if not found then
		return { reason = "the minion has no such skill" }
	end
	local saved, oldSkill, oldActive = build.mainSocketGroup, instance.skillMinionSkill, group.mainActiveSkill
	-- (MECH83) args.statSet: that stat set of the minion skill (CalcActiveSkill 1133-1145 reads
	-- srcInstance.skillMinionSkillStatSetIndexLookup[the summon's id][the skill's position]), put back after
	local oldLookup = instance.skillMinionSkillStatSetIndexLookup
	if tonumber(args.statSet) then
		local lookup, per = { }, { }
		for key, value in pairs(oldLookup or { }) do
			lookup[key] = value
		end
		for key, value in pairs((oldLookup or { })[granted.id] or { }) do
			per[key] = value
		end
		per[found] = tonumber(args.statSet)
		lookup[granted.id] = per
		instance.skillMinionSkillStatSetIndexLookup = lookup
	end
	build.mainSocketGroup, group.mainActiveSkill, instance.skillMinionSkill = args.index, position, found
	local result = { reason = "PoB calculated no minion" }  -- (never an empty table: it would reach Python as a list)
	local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
	-- (env.minion exists only once perform has run: useRates reads it the same way)
	if ok and env and pcall(calcs.perform, env, true) and env.minion then
		result.reason = nil
		local output = env.minion.output or { }
		-- (MECH104: CastRate is the use rate BEFORE the cooldown cap, CalcOffence 2983-3010; Speed is after it)
		for _, field in ipairs({ "AverageHit", "AverageDamage", "Speed", "CastRate", "Cooldown", "TotalDPS", "HitChance",
				"CritChance" }) do  -- (MECH107: CritChance, read by a probe of the Commands' Critical Weakness)
			if type(output[field]) == "number" then
				result[field] = jsonNumber(output[field])
			end
		end
		result.level = type(env.minion.level) == "number" and env.minion.level or nil  -- (MECHB42: the minion's level)
		-- (an attack's AverageHit is per hand: CalcOffence 4721-4728 combines only AverageDamage into the output)
		local hand = output.MainHand or output.OffHand
		if result.AverageHit == nil and hand and type(hand.AverageHit) == "number" then
			result.AverageHit = jsonNumber(hand.AverageHit)
		end
		if result.CastRate == nil and hand and type(hand.CastRate) == "number" then
			result.CastRate = jsonNumber(hand.CastRate)
		end
		result.name = env.minion.mainSkill and env.minion.mainSkill.activeEffect.grantedEffect.name or nil
		local set = env.minion.mainSkill and env.minion.mainSkill.activeEffect.statSet
		result.statSet = set and set.index or nil  -- (MECH83: the stat set PoB used)
	end
	-- the minion's own counted skill's TotalDPS (what it loses while it performs the other skill)
	instance.skillMinionSkill, instance.skillMinionSkillStatSetIndexLookup = oldSkill, oldLookup
	ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
	if ok and env and pcall(calcs.perform, env, true) and env.minion then
		local output = env.minion.output or { }
		result.baseDPS = type(output.TotalDPS) == "number" and jsonNumber(output.TotalDPS) or nil
	end
	build.mainSocketGroup, group.mainActiveSkill, instance.skillMinionSkill = saved, oldActive, oldSkill
	return result
end

-- (MECHB29, models.ancestral_spirits_model) Each minion type a summon skill can make, as PoB would score it with that
-- type chosen (srcInstance.skillMinion, CalcActiveSkill 951-953; the tool never sets it, so PoB rates the first listed
-- type only): Warbringer's Ancestral Spirits lists four (Turtle, Hulk, Caster, Warhorn). args.index: the group;
-- args.active: the skill's position in it. Gives { types = { { id, name, CombinedDPS, TotalDPS, Speed }, ... } }; the
-- main group, active skill and chosen minion are put back.
function bridge.minionTypes(args)
	local calcs = require("Modules.CalcBase")
	local group = build.skillsTab.socketGroupList[args.index]
	local position = tonumber(args.active) or (group and group.mainActiveSkill) or 1
	local activeSkill = group and group.displaySkillList and group.displaySkillList[position]
	local instance = activeSkill and activeSkill.activeEffect.srcInstance
	local granted = activeSkill and activeSkill.activeEffect.grantedEffect
	if not instance or not granted or not granted.minionList then
		return { reason = not instance and "no such skill in the group" or "the skill lists no minion types" }
	end
	local saved, oldMinion, oldActive = build.mainSocketGroup, instance.skillMinion, group.mainActiveSkill
	local types = { }
	for _, id in ipairs(granted.minionList) do
		build.mainSocketGroup, group.mainActiveSkill, instance.skillMinion = args.index, position, id
		local entry = { id = id, name = data.minions[id] and data.minions[id].name or id }
		local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
		if ok and env and pcall(calcs.perform, env, true) and env.minion then
			local output = env.minion.output or { }
			for _, field in ipairs({ "CombinedDPS", "TotalDPS", "Speed" }) do
				if type(output[field]) == "number" then
					entry[field] = jsonNumber(output[field])
				end
			end
		end
		types[#types + 1] = entry
	end
	build.mainSocketGroup, group.mainActiveSkill, instance.skillMinion = saved, oldActive, oldMinion
	return { types = types }
end

-- (MECHB49, Spectre / Tamed Companion step 1; MECHB48's plan) PoB fills a Spectre's / Tamed Companion's minion list from
-- build.spectreList / build.beastList (CalcActiveSkill 924-930, for a granted effect named "Spectre..." / "Companion...")
-- and picks srcInstance.skillMinion from it (951-953); only a build load, an import (ImportTab 1177-1214) or PoB's library
-- sets them (checked: every spectreList / beastList use in PoB's Modules and Classes: Build.lua 49-50, 1146-1155,
-- 1684-1858, ImportTab, CompareEntry), so the tool's Summon Spectre / Tamed Companion gems scored 0 (probe POE2T4728). Every listed Spectre's modList buffs apply whichever is
-- chosen (CalcPerform 2064), so a list holds only the monsters gems really have (monsterList below).
local MONSTER_GEMS = { SummonSpectrePlayer = "spectreList", SummonBeastPlayer = "beastList" }

-- The ids every group's Spectre (kind "spectreList") or Tamed Companion ("beastList") gem has chosen, in group order.
local function monsterList(key)
	local list, seen = { }, { }
	for _, group in ipairs(build.skillsTab.socketGroupList) do
		for _, activeSkill in ipairs(group.displaySkillList or { }) do
			local granted = activeSkill.activeEffect and activeSkill.activeEffect.grantedEffect
			local instance = activeSkill.activeEffect and activeSkill.activeEffect.srcInstance
			local id = instance and instance.skillMinion
			if granted and MONSTER_GEMS[granted.id] == key and id and data.minions[id] and not seen[id] then
				seen[id] = true
				list[#list + 1] = id
			end
		end
	end
	return list
end

-- The env's copy of a group's active skill (CalcDefence writes the Spirit reservation there, not on the display list's).
local function envSkill(env, instance)
	for _, activeSkill in ipairs(env.player.activeSkillList or { }) do
		if activeSkill.activeEffect and activeSkill.activeEffect.srcInstance == instance then
			return activeSkill
		end
	end
end

-- args.index: the group; args.active: the gem's position in it; args.ids: monster ids (data.minions keys, PoB
-- Data/Spectres.lua); args.skills: also score each of the monster's skills chosen in turn (srcInstance.skillMinionSkill,
-- as minionSkillHit). Gives { kind, rates = { { id, name, CombinedDPS, TotalDPS, Speed, spiritFlat, spiritPercent,
-- skills = { { index, name, TotalDPS, Speed, Cooldown } } } } }: each scored with only that monster listed beside the
-- other gems' own choices, and chosen; the list, the choice, the main group and outputs are put back.
function bridge.monsterRates(args)
	local calcs = require("Modules.CalcBase")
	local group = build.skillsTab.socketGroupList[args.index]
	local position = tonumber(args.active) or (group and group.mainActiveSkill) or 1
	local activeSkill = group and group.displaySkillList and group.displaySkillList[position]
	local instance = activeSkill and activeSkill.activeEffect.srcInstance
	local key = activeSkill and MONSTER_GEMS[activeSkill.activeEffect.grantedEffect.id]
	if not instance or not key then
		return { reason = not instance and "no such skill in the group" or "not a Spectre / Tamed Companion gem" }
	end
	local savedList, savedMinion, savedCalcs = build[key], instance.skillMinion, instance.skillMinionCalcs
	local savedSkill, saved, oldActive = instance.skillMinionSkill, build.mainSocketGroup, group.mainActiveSkill
	instance.skillMinion = nil
	local others = monsterList(key)  -- (the other gems' monsters stay listed: their buffs apply in the game too)
	local rates = { }
	local function score(id, skill)
		local list = { id }
		for _, other in ipairs(others) do
			if other ~= id then
				list[#list + 1] = other
			end
		end
		build[key] = list
		build.mainSocketGroup, group.mainActiveSkill = args.index, position
		instance.skillMinion, instance.skillMinionCalcs, instance.skillMinionSkill = id, id, skill
		local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
		if ok and env and pcall(calcs.perform, env, true) and env.minion then
			return env
		end
	end
	for _, id in ipairs(args.ids or { }) do
		local entry = { id = id, name = data.minions[id] and data.minions[id].name or id }
		if data.minions[id] then
			local env = score(id, savedSkill)
			if env then
				local output = env.minion.output or { }
				for _, field in ipairs({ "CombinedDPS", "TotalDPS", "Speed" }) do
					if type(output[field]) == "number" then
						entry[field] = jsonNumber(output[field])
					end
				end
				local own = envSkill(env, instance)
				local skillData = own and own.skillData or { }  -- (CalcDefence 205-213)
				entry.spiritFlat = type(skillData.spiritReservationFlat) == "number" and skillData.spiritReservationFlat or nil
				entry.spiritPercent = type(skillData.spiritReservationPercent) == "number"
					and skillData.spiritReservationPercent or nil
				local count = #(env.minion.activeSkillList or { })
				if args.skills and count > 0 then
					entry.skills = { }
					for k = 1, count do
						local env2 = score(id, k)
						local row = { index = k }
						if env2 then
							local out2 = env2.minion.output or { }
							row.name = env2.minion.mainSkill and env2.minion.mainSkill.activeEffect.grantedEffect.name or nil
							for _, field in ipairs({ "TotalDPS", "Speed", "Cooldown" }) do
								if type(out2[field]) == "number" then
									row[field] = jsonNumber(out2[field])
								end
							end
						end
						entry.skills[#entry.skills + 1] = row
					end
				end
			else
				entry.reason = "PoB calculated no minion"
			end
		else
			entry.reason = "no such monster"
		end
		rates[#rates + 1] = entry
	end
	build[key], instance.skillMinion, instance.skillMinionCalcs = savedList, savedMinion, savedCalcs
	instance.skillMinionSkill, build.mainSocketGroup, group.mainActiveSkill = savedSkill, saved, oldActive
	return { kind = key, rates = rates }
end

-- Keeps one: args.id becomes the gem's monster (args.skill: its skill index, else PoB's first), the list is rebuilt from
-- every gem's choice (monsterList: no stale monster's buffs), then a recalc. exportXml / loadXml carry both (Build.lua
-- 1146-1155 / 1190-1193 the list, SkillsTab 357-362 the gem's skillMinion); set_group keeps the gem's choice
-- (GEM_CHOICES). Gives { ok, list }.
function bridge.setMonster(args)
	local group = build.skillsTab.socketGroupList[args.index]
	local position = tonumber(args.active) or (group and group.mainActiveSkill) or 1
	local activeSkill = group and group.displaySkillList and group.displaySkillList[position]
	local instance = activeSkill and activeSkill.activeEffect.srcInstance
	local key = activeSkill and MONSTER_GEMS[activeSkill.activeEffect.grantedEffect.id]
	if not instance or not key or (args.id and not data.minions[args.id]) then
		return { reason = not instance and "no such skill in the group" or not key and "not a Spectre / Tamed Companion gem"
			or "no such monster" }
	end
	instance.skillMinion, instance.skillMinionCalcs = args.id, args.id
	instance.skillMinionSkill, instance.skillMinionSkillCalcs = tonumber(args.skill), tonumber(args.skill)
	build[key] = monsterList(key)
	recalc()
	return { ok = true, list = build[key] }
end

-- (MECHB51, Spectre step 3, MECHB50's draft) Rebuilds build.spectreList / beastList from the monsters the gems now hold
-- (monsterList): a group removed or a gem swapped leaves its monster listed, and every listed Spectre's buffs apply while
-- any Spectre gem is active (CalcPerform 2052-2064). Recalculates only when a list changed. Gives { changed }.
function bridge.syncMonsters()
	local changed = false
	for _, key in ipairs({ "spectreList", "beastList" }) do
		local now, old = monsterList(key), build[key] or { }
		local same = #now == #old
		for k = 1, #now do
			same = same and now[k] == old[k]
		end
		if not same then
			build[key], changed = now, true
		end
	end
	if changed then
		recalc()
	end
	return { changed = changed }
end

-- (MECH72, models.battershout_model) Battershout's explosion (TriggeredBattershoutExplosionPlayer, the support gem's
-- additional granted effect) has no base damage in PoB: "Physical Area Damage based off of the Broken Armour of the
-- target" (support_battershout_physical_damage_equal_to_%_of_broken_armour 40, which PoB maps to nothing). Each
-- enabled group holding it is calculated with the explosion as its main skill, its base Physical damage args.base
-- (both min and max, as CalcOffence's monster explosions set skillData), and the Warcry keyword (patch 0.2.0g: "now
-- counts as a Warcry skill"; game files' is_warcry, which PoB maps to nothing), so the Warcry damage lines apply.
-- args.unbroken: with the Armour Broken box off (the explosion lands after it Consumes the Full Break). Gives, per
-- group: index, warcry (the group's other active skill), AverageDamage, AverageHit, CritChance. Nothing is kept.
function bridge.battershoutHit(args)
	local calcs = require("Modules.CalcBase")
	local configTab = build.configTab
	local savedBroken = configTab.input.conditionEnemyArmourBroken
	if args.unbroken and savedBroken ~= nil then
		configTab.input.conditionEnemyArmourBroken = nil
		configTab:BuildModList()
	end
	local results = { }
	local saved = build.mainSocketGroup
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		local position, warcry
		for at, activeSkill in ipairs(group.enabled and group.displaySkillList or { }) do
			local effect = activeSkill.activeEffect.grantedEffect
			if effect.id == "TriggeredBattershoutExplosionPlayer" then
				position = at
			elseif not warcry then
				warcry = effect.name
			end
		end
		if position then
			local oldActive = group.mainActiveSkill
			build.mainSocketGroup, group.mainActiveSkill = index, position
			local row = { index = index, warcry = warcry }
			local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
			local skill = ok and env and env.player.mainSkill
			if skill and skill.activeEffect.grantedEffect.id == "TriggeredBattershoutExplosionPlayer" then
				skill.skillData.PhysicalMin, skill.skillData.PhysicalMax = args.base or 0, args.base or 0
				skill.skillTypes[SkillType.Warcry] = true
				skill.skillCfg.keywordFlags = require("bit").bor(skill.skillCfg.keywordFlags or 0, KeywordFlag.Warcry)
				if pcall(calcs.perform, env, true) then
					local output = env.player.output or { }
					for _, field in ipairs({ "AverageDamage", "AverageHit", "CritChance" }) do
						if type(output[field]) == "number" then
							row[field] = jsonNumber(output[field])
						end
					end
				end
			else
				row.reason = "PoB calculated no explosion"
			end
			group.mainActiveSkill = oldActive
			results[#results + 1] = row
		end
	end
	build.mainSocketGroup = saved
	if args.unbroken and savedBroken ~= nil then
		configTab.input.conditionEnemyArmourBroken = savedBroken
		configTab:BuildModList()
	end
	return { groups = results, found = #results }
end

-- (MECHB15, models.multi_set_model) Each stat set of a group's active skill calculated in its own environment, as
-- useRates does: one use of Rolling Slam performs its First Slam and its Second Slam, and PoB scores one set. Gives each
-- set's label, TotalDPS, AverageDamage and Speed; the group's chosen set, main group and outputs are put back.
function bridge.setRates(args)
	local calcs = require("Modules.CalcBase")
	local saved = build.mainSocketGroup
	local group = build.skillsTab.socketGroupList[args.index]
	local position = tonumber(args.active) or (group and group.mainActiveSkill) or 1
	local activeSkill = group and group.displaySkillList and group.displaySkillList[position]
	local instance = activeSkill and activeSkill.activeEffect.srcInstance
	local sets = { }
	if not instance then
		return { sets = sets }
	end
	local granted = activeSkill.activeEffect.grantedEffect
	local id = granted.id
	instance.statSet = instance.statSet or { }
	instance.statSetCalcs = instance.statSetCalcs or { }
	local oldSet, oldCalcs, oldActive = instance.statSet[id], instance.statSetCalcs[id], group.mainActiveSkill
	build.mainSocketGroup = args.index
	group.mainActiveSkill = position
	for k, statSet in ipairs(granted.statSets or { }) do
		instance.statSet[id], instance.statSetCalcs[id] = k, k
		local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
		local entry = { index = k, label = statSet.label or "" }
		if ok and env and env.player.mainSkill and pcall(calcs.perform, env, true) then
			local output = env.player.output
			-- (MECHB38: ProjectileCount, Dusk Vigil's Embers a trigger, models.dusk_vigil_model; GEM21: PoisonDPS, Gas
			-- Arrow's cloud Poison beside the Arrow set it scores, models.gas_arrow_model, SEARCH28's item 5)
			-- (GEM22: each set's Ignite / Bleed, their stack potential and cap, and the DPS multiplier, for
			-- models.strongest_ailment_model: one Ignite / Bleed counts, the strongest, at its own set's uptime)
			-- (GEM25: a support-triggered skill's own damage over time and its Duration, for models.profane_decay_model
			-- (TotalDot) and models.twisted_pact_model (CorruptingBloodDPS): PoB counts them only in Full DPS)
			-- (MECHC48: HitSpeed and CritChance, a side Galvanic Field's Orb bolts and their crits a second,
			-- conditions.Conditions._side_galvanic)
			-- (GEM28: PierceCount, ChainMax, ForkCountMax, SplitCount, whether a projectile ends its flight on the boss
			-- it hits, for the Caltrops support, models.caltrops_support_model; AreaOfEffectRadius / Secondary, the
			-- Caltrops' own and landing radii with the build's area lines, RESEARCH38 R38-3)
			for _, field in ipairs({ "TotalDPS", "AverageDamage", "Speed", "ProjectileCount", "PoisonDPS", "IgniteDPS",
					"BleedDPS", "IgniteStackPotential", "BleedStackPotential", "IgniteStacksMax", "BleedStacksMax",
					"DpsMultiplier", "TotalDot", "CorruptingBloodDPS", "Duration", "HitSpeed", "CritChance", "PierceCount",
					"ChainMax", "ForkCountMax", "SplitCount", "AreaOfEffectRadius", "AreaOfEffectRadiusSecondary",
					-- (MECH109, R60-12: a set's Poison stacks and the Poison limit, models.cloud_poison_scale)
					"PoisonStackPotential", "PoisonStacksMax",
					-- (MECHB56, RESEARCH48 R48-1: an Ice Crystal set's Life with the build's modifiers, CalcOffence 1724-1726:
					-- Midnight Zenith's Crystal set, models.midnight_zenith_model)
					"IceCrystalLife" }) do
				if type(output[field]) == "number" then
					entry[field] = jsonNumber(output[field])
				end
			end
		end
		-- (GEM43) a set given a flag PoB's data lacks (installAttackSetFlags' readings): its DPS once more without it, the row
		local added = statSet.bridgeAddedFlags
		if added and entry.TotalDPS then
			local names = { }
			for flag in pairs(added) do
				statSet.baseFlags[flag] = nil
				names[#names + 1] = flag
			end
			table.sort(names)
			local okWithout, envWithout = pcall(calcs.initEnv, build, "CALCULATOR")
			if okWithout and envWithout and envWithout.player.mainSkill and pcall(calcs.perform, envWithout, true)
					and type(envWithout.player.output.TotalDPS) == "number" then
				entry.TotalDPSWithoutAdded = jsonNumber(envWithout.player.output.TotalDPS)
			end
			for flag in pairs(added) do
				statSet.baseFlags[flag] = true
			end
			entry.addedFlags = table.concat(names, ",")
		end
		sets[#sets + 1] = entry
	end
	instance.statSet[id], instance.statSetCalcs[id], group.mainActiveSkill = oldSet, oldCalcs, oldActive
	build.mainSocketGroup = saved
	-- (MECHB16) the label of the set the group scores (a Full DPS group's own choice), for models.multi_set_model
	local chosen = (granted.statSets or { })[oldSet or 1]
	return { sets = sets, chosen = chosen and chosen.label or "" }
end

-- MECH-B (models.hazard_early_model): "N% less damage if destroyed within 0.5 seconds by something other than you", a
-- stat PoB maps to nothing, read from the skill's scored stat set with what keeps the boss from setting it off that
-- early: the set's arming time, and supports that delay triggering (Controlled Hazard's "can't trigger for N seconds
-- after creation", Delayed Reaction's "trigger at the end of their duration instead").
local EARLY_HAZARD = {
	["hazard_damage_+%_final_for_first_500_ms_when_not_killed_by_self"] = "hazard",
	["ice_crystals_damage_+%_final_for_first_500_ms_when_not_killed_by_self"] = "ice_crystals",
	["shield_wall_damage_+%_final_for_first_500_ms_when_not_killed_by_self"] = "shield_wall",
}
local HAZARD_ARMING = { ["permafrost_shard_arming_time_ms"] = true, ["briarpatches_arming_time_ms"] = true }
local function earlyHazard(activeSkill)
	-- (current PoB wraps the scored set: activeEffect.statSet = { statSet = <the set>, index, skillFlags })
	local set = activeSkill.activeEffect.statSet
	set = set and (set.statSet or set)
	local early
	for _, pair in ipairs(set and set.constantStats or { }) do
		if EARLY_HAZARD[pair[1]] then
			early = { kind = EARLY_HAZARD[pair[1]], more = pair[2] }
		end
	end
	if not early then
		return nil
	end
	-- Ice Crystals by the game's own type, whatever the stat's name (Grim Pillars' "Ice-Crystal spears" carry the
	-- hazard_ id but explode as is_ice_crystal_explosion, like Frost Wall's and Glacial Bolt's)
	for _, other in ipairs(activeSkill.activeEffect.grantedEffect.statSets or { }) do
		for _, stat in ipairs(other.stats or { }) do
			if stat == "is_ice_crystal_explosion" then
				early.kind = "ice_crystals"
			end
		end
	end
	for _, pair in ipairs(set.constantStats) do
		if HAZARD_ARMING[pair[1]] then
			early.arming = pair[2]
		end
	end
	for _, support in ipairs(activeSkill.effectList or { }) do
		local granted = support.grantedEffect
		local first = granted and granted.support and granted.statSets and granted.statSets[1]
		for _, pair in ipairs(first and first.constantStats or { }) do
			if pair[1] == "hazards_cant_trigger_x_seconds_after_creation" then
				early.delay = (early.delay or 0) + pair[2]
				early.delayBy = granted.name
			end
		end
		for _, stat in ipairs(first and first.stats or { }) do
			if stat == "hazards_trigger_at_end_of_duration_instead" then
				early.atEnd = granted.name
			end
		end
	end
	return early
end

-- (MECH-B, models.energy_gain) the skill's own "active_skill_energy_generated_+%_final" (Incinerate and Wyvern Flame
-- Breath -98, Flame Wall and Solar Orb -50; game files GrantedEffectStatSets): neither PoB nor its stat map reads it.
-- The scored set's value, else any set's.
local function energyFinal(activeSkill)
	local set = activeSkill.activeEffect.statSet
	set = set and (set.statSet or set)
	for _, pair in ipairs(set and set.constantStats or { }) do
		if pair[1] == "active_skill_energy_generated_+%_final" then
			return pair[2]
		end
	end
	for _, other in ipairs(activeSkill.activeEffect.grantedEffect.statSets or { }) do
		for _, pair in ipairs(other.constantStats or { }) do
			if pair[1] == "active_skill_energy_generated_+%_final" then
				return pair[2]
			end
		end
	end
	return nil
end

-- (MECH73, AUDIT35 A35-2) Whether using this active effect hits enemies: PoB's own hit rule (CalcActiveSkill 179:
-- baseFlags.hit, or the Attack, Damage or Projectile type), minus effects whose use doesn't hit: a Persistent buff (a Herald,
-- Iron Ward's store: its nova is a separate effect), a minion maker (the hits
-- are its minions'), a Wall, a damage-over-time-only spell, and any stat set the game marks display_statset_no_hit_damage
-- (Fortifying Cry: its Shield Wave is the hit). conditions.daze_hitter reads it.
local function hitsOnUse(effect, types)
	if types[SkillType.Persistent] or types[SkillType.CreatesMinion] or types[SkillType.Wall]
		or types[SkillType.DegenOnlySpellDamage] then
		return false
	end
	local hit = types[SkillType.Attack] or types[SkillType.Damage] or types[SkillType.Projectile]
	for _, set in ipairs(effect.statSets or { }) do
		for _, stat in ipairs(set.stats or { }) do
			if stat == "display_statset_no_hit_damage" then
				return false
			end
		end
		hit = hit or (set.baseFlags or { }).hit
	end
	return hit and true or false
end

-- Active skills of the current build with what an assumption needs about them: base cooldown,
-- cooldown recovery, and whether they generate Remnants. `levelStats = {names}` adds those skills'
-- stats at their current level and quality (e.g. Iron Ward's store, which PoB doesn't read).
-- (see bridge.activeSkills: support stats the models always get)
local modelSupportStats = {
	["number_of_totems_allowed_is_1"] = true,
	["support_splinter_totem_X_times"] = true,
	-- (MECHB34) Splinter Totem's actions per stage and its per-splinter less damage / skill speed (models.splinter_factor)
	["support_splinter_totem_splinter_after_X_actions"] = true,
	["support_splinter_totem_damage_+%_final_each_splinter"] = true,
	["support_splinter_totem_skill_speed_+%_final_each_splinter"] = true,
	["support_minion_pact_user_life_%_removed_from_nearby_minion"] = true,
	["support_minion_pact_damage_+%_final"] = true,
	-- (MECH63) Eternal Mark's "Marks are not Consumed the first time they Activate" (models.mark_model)
	["marks_avoid_consumption_when_first_activated"] = true,
	-- (MECHB31) Infernal Legion I / II: which minion skills it supports (models.infernal_legion_ignited, MECH-A's Ignited box)
	["support_minions_ignite_for_%_max_life"] = true,
}

-- (MECHB30, M1-2) PoB's computed cooldown of a skill with a base cooldown (calcSkillCooldown, global in CalcOffence),
-- or nil (no base cooldown, or PoB's function unavailable)
local function skillCooldown(activeSkill)
	local base = activeSkill.skillData and activeSkill.skillData.cooldown
	if not base or base <= 0 or type(calcSkillCooldown) ~= "function" then
		return nil
	end
	local ok, cooldown = pcall(calcSkillCooldown, activeSkill.skillModList, activeSkill.skillCfg, activeSkill.skillData)
	return (ok and type(cooldown) == "number" and cooldown > 0) and cooldown or nil
end

-- (MECH57) the ailments a summoning skill's counted minion skill consumes by its skill types, and its name
local function minionConsumes(activeSkill)
	local minionSkill = activeSkill.minion and activeSkill.minion.mainSkill
	local types = minionSkill and minionSkill.skillTypes
	if not types or not (types[SkillType.SkillConsumesFreeze] or types[SkillType.SkillConsumesShock]
		or types[SkillType.SkillConsumesIgnite]) then
		return nil
	end
	return {
		skill = minionSkill.activeEffect.grantedEffect.name,
		Freeze = types[SkillType.SkillConsumesFreeze] and true or nil,
		Shock = types[SkillType.SkillConsumesShock] and true or nil,
		Ignite = types[SkillType.SkillConsumesIgnite] and true or nil,
	}
end

function bridge.activeSkills(args)
	local wantStats = { }
	for _, name in ipairs(args and args.levelStats or { }) do
		wantStats[name] = true
	end
	local skills = { }
	local env = build.calcsTab.mainEnv
	local groupIndex = { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		groupIndex[group] = index
	end
	local weapon1 = env and env.player.weaponData1 and env.player.weaponData1.type
	local weapon2 = env and env.player.weaponData2 and env.player.weaponData2.type
	for _, activeSkill in ipairs(env and env.player.activeSkillList or { }) do
		local effect = activeSkill.activeEffect.grantedEffect
		local types = activeSkill.skillTypes
		local computed = skillCooldown(activeSkill)
		skills[#skills + 1] = {
			name = effect.name,
			skillId = effect.id,  -- (COV31) the granted effect: four names are shared by two gems
			cooldown = activeSkill.skillData.cooldown,
			-- (MECHB30, PATCH_CHECK M1-2) PoB's own cooldown (CalcOffence calcSkillCooldown: the base plus added
			-- cooldown and Temporalis's cut, over Cooldown Recovery, floored at 0.1 s with Temporalis, rounded to server
			-- ticks), and the recovery that gives it from the base: the models divide the base by cooldownRecovery, so
			-- they read PoB's cooldown (before: base / recovery alone, missing Temporalis and added cooldown)
			computedCooldown = computed,
			cooldownRecovery = computed and activeSkill.skillData.cooldown / computed
				or calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "CooldownRecovery"),
			generatesRemnants = types[SkillType.GeneratesRemnants] and true or false,
			-- it consumes or targets corpses (the game's skill types; models.corpse_model)
			corpses = (types[SkillType.TargetsDestructibleCorpses] or types[SkillType.TargetsDestructibleRareCorpses]
				or types[SkillType.CanTargetUnusableCorpse]) and true or nil,
			triggered = types[SkillType.Triggered] and true or false,
			-- (MECHB37, models.cannons_ready_model) used by a totem (PoB's totem flag or UsedByTotem), and an Attack the
			-- player fires projectiles with (Projectile, CrossbowSkill or GroundTargetedProjectile: "Totems only use
			-- Skills when you fire an Attack Projectile"; patch 0.2.1 / 0.5.0: skills firing upwards count). (MECHB54;
			-- RESEARCH46 R46-6, an OVERCOUNT) Not CrossbowAmmoSkill: that type marks only the LOAD parts ("Load X", Basic
			-- Bolt), which fire nothing ("The Ammunition Skill itself does not fire the Ammunition, only load it"); every
			-- crossbow firing effect carries CrossbowSkill, the three without Projectile (Hailstorm Rounds, Siege Cascade,
			-- Stormblast Bolts) firing upwards (checked: every PoB Skills/*.lua effect's skillTypes)
			byTotem = ((activeSkill.skillFlags and activeSkill.skillFlags.totem) or types[SkillType.UsedByTotem]) and true or false,
			projectileAttack = (types[SkillType.Attack] and (types[SkillType.Projectile] or types[SkillType.CrossbowSkill]
				or types[SkillType.GroundTargetedProjectile])) and true or false,
			hitsOnUse = hitsOnUse(effect, types),
			-- (MECHB12, AUDIT25 A25-41) a curse's own duration with the build's modifiers ("curse duration" is Duration
			-- with the Curse keyword, ModParser 483): base x Duration / PrimaryDuration, as PoB builds a skill's duration
			curseDuration = types[SkillType.AppliesCurse] and activeSkill.skillData.duration and jsonNumber(
				((activeSkill.skillData.duration or 0) + activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "Duration", "PrimaryDuration"))
				* calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "Duration", "PrimaryDuration")) or nil,
			-- (MECH86) a curse's activation delay with the build's modifiers, as CalcOffence 1576-1580 builds
			-- output.CurseDelay: (curse_delay_duration_ms + CurseDelayBase) / CurseActivation ("N% faster Curse
			-- Activation": passives, jewels, runes, uniques) x CurseDelay (Focused / Ritualistic Curse, "no Activation
			-- Delay", "Double Activation Delay"), rounded up to a server tick; models.curse_uses
			curseDelay = types[SkillType.AppliesCurse] and jsonNumber(math.ceil(
				((activeSkill.skillData.curseDelay or 0) + activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "CurseDelayBase"))
				/ calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "CurseActivation")
				* calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "CurseDelay") * data.misc.ServerTickRate)
				/ data.misc.ServerTickRate) or nil,
			-- (MECH96, RESEARCH38 R38-2) a curse's own area radius in units, as CalcOffence's calcAreaOfEffect builds
			-- output.AreaOfEffectRadius (567-584: skillData.radius + radiusExtra (Blasphemy's +1 m, its
			-- active_skill_base_area_of_effect_radius as radiusExtra on the held curse) + BASE AreaOfEffect, x the square root
			-- of the AreaOfEffect / AreaOfEffectPrimary mod (calcAreaMod's rounding), x AreaOfEffectRadius); models.curse_uses
			-- tests a Blasphemy aura's reach with it
			curseRadius = types[SkillType.AppliesCurse] and activeSkill.skillData.radius and (function()
				local mods, cfg = activeSkill.skillModList, activeSkill.skillCfg
				local inc, more = calcLib.mods(mods, cfg, "AreaOfEffect", "AreaOfEffectPrimary")
				local pct = roundSymmetric(inc * 100) * roundSymmetric(math.max(0, more) * 100) / 100
				pct = pct >= 0 and math.floor(pct) or math.ceil(pct)
				local areaMod = math.max(0, pct) / 100
				local base = activeSkill.skillData.radius + (activeSkill.skillData.radiusExtra or 0)
					+ mods:Sum("BASE", cfg, "AreaOfEffect")
				local radiusPercent = math.floor(math.floor(100 * math.sqrt(areaMod)) * calcLib.mod(mods, cfg, "AreaOfEffectRadius"))
				return jsonNumber(math.floor(base * radiusPercent / 100))
			end)() or nil,
			-- (MECH62, AUDIT27 A27-2) a Mark's own duration with the build's modifiers ("Mark Skills have N% increased
			-- Skill Effect Duration": Duration on SkillType Mark, ModCache; mark_skill_duration_+% on jewels, passives and
			-- Marksman influence), built as curseDuration; models.mark_duration
			markDuration = types[SkillType.Mark] and activeSkill.skillData.duration and jsonNumber(
				((activeSkill.skillData.duration or 0) + activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "Duration", "PrimaryDuration"))
				* calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "Duration", "PrimaryDuration")) or nil,
			-- the ailments it consumes by its skill types alone (Snap, Elemental Sundering, Snipe, Shockchain Arrow,
			-- Icestorm carry no consume stat; conditions.main_consumes / side_consumes, MECH55)
			-- (MECH75: and Bleeding, SkillConsumesBleeding: Blood Hunt; the Heralds of Blood / the Royal Queen carry it
			-- too, but explode only on a kill and name no Consume, so the Python side never takes them as consumers)
			consumes = (types[SkillType.SkillConsumesFreeze] or types[SkillType.SkillConsumesShock]
				or types[SkillType.SkillConsumesIgnite] or types[SkillType.SkillConsumesBleeding]) and {
					Freeze = types[SkillType.SkillConsumesFreeze] and true or nil,
					Shock = types[SkillType.SkillConsumesShock] and true or nil,
					Ignite = types[SkillType.SkillConsumesIgnite] and true or nil,
					Bleed = types[SkillType.SkillConsumesBleeding] and true or nil,
				} or nil,
			-- the ailments its minion's counted skill (PoB's minion.mainSkill, the one Full DPS counts) consumes by its
			-- skill types: the Skeletal Brute's Shattering Roar consumes Freeze (MECH57, AUDIT25's small overcount;
			-- conditions.main_consumes / side_consumes read it at PoB's minion Speed, useRates' minionSpeed)
			minionConsumes = minionConsumes(activeSkill),
			-- it summons minions (assumptions.NEEDS_MINIONS: Grim Feast's Remnants come from their deaths)
			minions = types[SkillType.CreatesMinion] and true or nil,
			-- (MECHB33) a Companion skill (the game's Companion / CreatesCompanion types): models.companion_plan's limit
			companion = (types[SkillType.Companion] or types[SkillType.CreatesCompanion]) and true or nil,
			-- current PoB keeps the flags on the stat set (activeSkill.skillFlags is v0.23's)
			disabled = (activeSkill.skillFlags or (activeSkill.activeEffect.statSet or { }).skillFlags or { }).disable or false,
			-- for the custom models: what kind of skill, its base cast time, its group, and
			-- whether it's a meta skill that gains Energy
			spell = types[SkillType.Spell] and true or false,
			attack = types[SkillType.Attack] and true or false,
			melee = types[SkillType.Melee] and true or false,
			-- (MECH64) it can Empower other skills (Murderous Intent's requireSkillTypes; Assumptions.empowered_uses)
			empowers = types[SkillType.EmpowersOtherSkill] and true or nil,
			-- (MECH65, AUDIT29 A29-3/4) what a skill must be for another to Empower it (Assumptions.empowered_uses):
			-- Repeatable (Multicastable; KeywordPopups Repeat: Triggered, Instant and Channelling skills can't Repeat),
			-- a Slam (Seismic Cry), a Shield attack (Fortifying Cry)
			repeatable = (types[SkillType.Multicastable] and not types[SkillType.Instant] and not types[SkillType.Channel]
				and not types[SkillType.Triggered]) and true or nil,
			slam = types[SkillType.Slam] and true or nil,
			shield = types[SkillType.RequiresShield] and true or nil,
			instant = (effect.castTime or 0) == 0 or nil,
			castTime = effect.castTime,
			energy = types[SkillType.GeneratesEnergy] and true or false,
			-- an Energy meta's gem level: its own increased Energy comes from it (models.gem_energy)
			level = types[SkillType.GeneratesEnergy] and activeSkill.activeEffect.level or nil,
			-- (MECH63) a Mark's level with the build's +level lines (CalcSetup applyGemMods adds GemProperty "level" to
			-- the effect): Charged Mark's triggered skill copies it (conditions.charged_mark's radius)
			markLevel = types[SkillType.Mark] and activeSkill.activeEffect.level or nil,
			group = activeSkill.socketGroup and groupIndex[activeSkill.socketGroup] or nil,
			-- a weapon type it needs in neither hand (PoB disables only Attacks for that; Ferocious Roar needs a Talisman)
			wrongWeapon = effect.weaponTypes and not (weapon1 and effect.weaponTypes[weapon1] or weapon2
				and effect.weaponTypes[weapon2]) or nil,
		}
		-- (MECHB13, models.ward_plan) it costs Runic Ward: a Ward cost at its level, or a modifier that adds one (PoB's
		-- WardCostBase / WardCost / WardCostNoMult / WardCostAsPercentOf{Life,Mana}Cost, CalcOffence's costs); the cost
		-- itself, after multipliers, comes from useRates
		do
			local ok, ward = pcall(function()
				local level = activeSkill.activeEffect.grantedEffectLevel
				if level and level.cost and (level.cost.Ward or 0) > 0 then
					return true
				end
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				return list:Sum("BASE", cfg, "WardCostBase", "WardCost", "WardCostNoMult", "WardCostAsPercentOfLifeCost",
					"WardCostAsPercentOfManaCost") > 0
			end)
			skills[#skills].wardCost = ok and ward or nil
		end
		-- (MECHC12, recovery.remnant_income) a Remnant skill's Life / Mana per Remnant collected, as PoB's CalcOffence
		-- 1660-1685 builds LifeGainPerRemnant / ManaGainPerRemnant (only for the main skill there): base x RemnantEffect
		if types[SkillType.GeneratesRemnants] then
			local ok, gain = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				local effectMod = calcLib.mod(list, cfg, "RemnantEffect")
				local life = list:Sum("BASE", cfg, "LifeGainPerRemnant")
				local mana = list:Sum("BASE", cfg, "ManaGainPerRemnant")
				-- (MECHC26, H1-9) Into the Breach's Red / Blue Flames of Chayula: % of maximum Life / Mana Leeched on
				-- collection, as CalcOffence 1700-1718 builds BreachFlameLifeLeech / ManaLeech (floor(base x effect))
				local flameLife = list:Sum("BASE", cfg, "BreachFlameLifeLeech")
				local flameMana = list:Sum("BASE", cfg, "BreachFlameManaLeech")
				if life <= 0 and mana <= 0 and flameLife <= 0 and flameMana <= 0 then
					return nil
				end
				return { Life = life > 0 and jsonNumber(math.floor(life * effectMod)) or nil,
					Mana = mana > 0 and jsonNumber(math.floor(mana * effectMod)) or nil,
					FlameLife = flameLife > 0 and jsonNumber(math.floor(flameLife * effectMod)) or nil,
					FlameMana = flameMana > 0 and jsonNumber(math.floor(flameMana * effectMod)) or nil }
			end)
			skills[#skills].remnantGain = ok and gain or nil
		end
		do
			local ok, early = pcall(earlyHazard, activeSkill)
			skills[#skills].earlyHazard = ok and early or nil
		end
		do
			-- the label of the stat set PoB calculates for it (MECHB7, for COVE8: models.Skill.stat_set; Long Fuse's
			-- detonating sets, Poison Spores). Current PoB wraps it: activeEffect.statSet = { statSet, index, ... }
			local ok, label = pcall(function()
				local set = activeSkill.activeEffect.statSet
				local inner = set and (set.statSet or set)
				if inner and inner.label then
					return inner.label
				end
				local sets = effect.statSets or { }
				return set and set.index and sets[set.index] and sets[set.index].label or nil
			end)
			skills[#skills].statSet = ok and label or nil
		end
		do
			local ok, final = pcall(energyFinal, activeSkill)
			skills[#skills].energyFinal = ok and final or nil
		end
		-- "+N seconds to Total Cast / Attack Time" (total_cast_time_+_ms, PoB's TotalCastTime: Comet, Lightning Conduit,
		-- Crushing Earth, Skyfall ...): the game's Energy definition counts it double in Energy costs (models.energy_cost)
		do
			local ok, added = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				return list:Sum("BASE", cfg, "TotalCastTime") + list:Sum("BASE", cfg, "TotalAttackTime")
			end)
			skills[#skills].totalTimeAdded = ok and added ~= 0 and added or nil
		end
		-- (GEM15) "Modifiers to cast speed also affect this skill's cost" (cast_speed_modifiers_apply_to_over_time_cost,
		-- PoB "Display Only": Bonestorm, Flameblast, Volcano): the skill's raw per-minute costs from its level data (never
		-- the override models.over_time_cost_model sets, so every pass reads the same) and its cast speed multiplier as
		-- CalcOffence 1293 builds it (calcLib.mod Speed); PoB keeps the per-minute cost fixed
		do
			local ok, info = pcall(function()
				local cost = activeSkill.activeEffect.grantedEffectLevel.cost or { }
				local perMinute
				for resource, value in pairs(cost) do
					if resource:match("PerMinute$") then
						perMinute = perMinute or setmetatable({ }, { __jsontype = "object" })
						perMinute[resource] = value
					end
				end
				if not perMinute then
					return nil
				end
				for _, set in ipairs(effect.statSets or { }) do
					for _, stat in ipairs(set.stats or { }) do
						if stat == "cast_speed_modifiers_apply_to_over_time_cost" then
							return { perMinute = perMinute, castSpeed = jsonNumber(
								calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "Speed")) }
						end
					end
				end
			end)
			skills[#skills].overTimeCost = ok and info or nil
		end
		-- A totem-summoning skill's Totem Limit (models.totem_count_model, MECHB8): PoB reports ActiveTotemLimit only for the
		-- skill it calculates, and the damaging part beside it (Shockwave Slam, Ballista Bolt, Vile Effusion) has none.
		-- Both reads leave out the one-totem correction (source "Assumption:OneTotem", models.TOTEM_ONE_TAG), so the model
		-- reads the same limit on every pass, its own line or not (MECHB11, AUDIT24 A24-3)
		local function totemLimitBase(list, cfg)
			local total = 0
			for _, entry in ipairs(list:Tabulate("BASE", cfg, "ActiveTotemLimit", "ActiveBallistaLimit")) do
				if entry.mod.source ~= "Assumption:OneTotem" then
					total = total + entry.value
				end
			end
			return total
		end
		if types[SkillType.SummonsTotem] then
			-- (MECHB34) the skill's own base Totem Limit read from its data: base_number_of_totems_allowed in ANY of its
			-- stat sets (constant or at its level) plus its quality line. PoB's skillModList holds only the calculated
			-- set's stats, so Shockwave Totem calculated on its slam set read 0 bare and 1 with a "+1 Totems" line (probe
			-- POE2T3242); the game's limit is its Totem set's 1 plus the line. The build's lines (every BASE entry not
			-- from the skill itself, the one-totem correction left out) are added to it
			local function ownLimit()
				local effect = activeSkill.activeEffect.grantedEffect
				local level = activeSkill.activeEffect.level or 1
				local best = 0
				for _, set in ipairs(effect.statSets or { }) do
					for _, constant in ipairs(set.constantStats or { }) do
						if constant[1] == "base_number_of_totems_allowed" then
							best = math.max(best, constant[2] or 0)
						end
					end
					local row = set.levels and set.levels[level]
					for i, stat in ipairs(set.stats or { }) do
						if stat == "base_number_of_totems_allowed" and row and row[i] then
							best = math.max(best, row[i])
						end
					end
				end
				local quality = activeSkill.activeEffect.quality or 0
				for _, line in ipairs(effect.qualityStats or { }) do
					if line[1] == "base_number_of_totems_allowed" then
						best = best + math.floor(quality * (line[2] or 0) + 1e-9)
					end
				end
				return best
			end
			local okOwn, own = pcall(ownLimit)
			own = okOwn and own or nil
			-- (GEM41, COV57 row 41) The game flags Shockwave Totem is_melee_attack_totem (GrantedEffectStatSets
			-- ShockwaveTotemPlayer, 26650308; poe2db's hidden stats and its Melee tag), while its own skill types (the
			-- game's and PoB's) hold neither Attack nor Melee, so PoB's "Melee Attack Skills have +N to maximum number of
			-- Summoned Totems" (Ancestral Artifice; the Ulaman mace prefix: SkillType Melee + Attack tags) never reached
			-- its limit. Read as the flag's purpose (MEDIUM-HIGH since RESEARCH59 R59-3: the hidden tag is only on its
			-- summon set): the limit lines are tabulated as for a Melee Attack skill. (GEM42, R59-3, MEDIUM) "Attack
			-- Skills have +N to maximum number of Summoned Totems" (KeywordFlag Attack, not a skill type) probably counts
			-- too: tabulated with the Attack keyword on top, its share reported apart (attackTotemAdded).
			-- models.totem_count_model gives the "without it" rows (meleeAttackTotemAdded, attackTotemAdded)
			local function meleeAttackTotemCfg(cfg)
				for _, set in ipairs(activeSkill.activeEffect.grantedEffect.statSets or { }) do
					for _, stat in ipairs(set.stats or { }) do
						if stat == "is_melee_attack_totem" then
							local wider = copyTable(cfg or { }, true)
							wider.skillTypes = copyTable((cfg and cfg.skillTypes) or { }, true)
							wider.skillTypes[SkillType.Attack] = true
							wider.skillTypes[SkillType.Melee] = true
							local widest = copyTable(wider, true)
							widest.skillTypes = copyTable(wider.skillTypes, true)
							widest.keywordFlags = require("bit").bor(wider.keywordFlags or 0, KeywordFlag.Attack)
							return wider, widest
						end
					end
				end
			end
			local ok, limit = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				local melee, attack = meleeAttackTotemCfg(cfg)
				if melee then
					local function sum(c)
						local total = 0
						for _, entry in ipairs(list:Tabulate("BASE", c, "ActiveTotemLimit", "ActiveBallistaLimit")) do
							total = total + entry.value
						end
						return total
					end
					local plain, wide, widest = sum(cfg), sum(melee), sum(attack)
					skills[#skills].meleeAttackTotemAdded = wide - plain
					skills[#skills].attackTotemAdded = widest - wide
					cfg = attack
				end
				local override = list:Override(cfg, "ActiveTotemLimit", "ActiveBallistaLimit")
				if override then
					return override
				end
				if not own or own <= 0 then
					return totemLimitBase(list, cfg)
				end
				local id = "Skill:" .. activeSkill.activeEffect.grantedEffect.id
				local added = 0
				for _, entry in ipairs(list:Tabulate("BASE", cfg, "ActiveTotemLimit", "ActiveBallistaLimit")) do
					if entry.mod.source ~= "Assumption:OneTotem" and entry.mod.source ~= id then
						added = added + entry.value
					end
				end
				return own + added
			end)
			skills[#skills].totemLimit = ok and limit or nil
			skills[#skills].totemLimitOwn = own
		end
		-- The count PoB multiplies a totem skill's Ignite / Poison / Bleed stacks by (CalcOffence 5264-5267, skills flagged
		-- totem): the TotemsSummoned override, else the skill's own ActiveTotemLimit (one-totem correction left out)
		do
			local flags = activeSkill.skillFlags or (activeSkill.activeEffect.statSet or { }).skillFlags or { }
			if flags.totem then
				local ok, count = pcall(function()
					local override = env.modDB:Override(nil, "TotemsSummoned")
					return { override = override,
						limit = totemLimitBase(activeSkill.skillModList, activeSkill.skillCfg) }
				end)
				skills[#skills].ailmentTotems = ok and count or nil
			end
		end
		-- Every minion skill's minions (models.minion_deaths): how many, how long they last (0: until killed), Persistent
		if activeSkill.minion and activeSkill.minion.minionData then
			local ok, summon = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				local minionData = activeSkill.minion.minionData
				local base = (activeSkill.skillData.duration or 0) + list:Sum("BASE", cfg, "Duration", "PrimaryDuration")
				return {
					-- (MECHB20, MECHC15's silent 0) as PoB's ActiveMinionLimit, CalcOffence 1420: the override, else the
					-- minion's own limit stat plus ActiveMinionLimit (Skeletal Warriors' count comes from the latter;
					-- read alone, the former gave them 0, and every Sacrifice / minion-death model 0)
					limit = minionData.limit and math.floor(env.modDB:Override(nil, minionData.limit)
						or (calcLib.val(list, minionData.limit, cfg) + calcLib.val(list, "ActiveMinionLimit", cfg))) or nil,
					-- PoE2's persistent minion gems carry no limit stat (Skeletal Warrior: probe POE2T1072 gave 0); their
					-- own count is skill_desired_initial_amount (2 on Skeletal Warrior), which PoB doesn't map
					-- (SkillStatMap has only first_X_minions_have_0_base_spirit_reservation -> MinionFreeSpiritCount)
					desired = (function()
						local sets = effect.statSets
						local ok2, built = pcall(calcLib.buildSkillInstanceStats, activeSkill.activeEffect, effect,
							sets and sets[1], altGemQuality())
						return ok2 and built and (built["skill_desired_initial_amount"] or built["skill_desired_amount_override"])
							or nil
					end)(),
					duration = base * calcLib.mod(list, cfg, "Duration", "PrimaryDuration"),
					persistent = types[SkillType.Persistent] and true or false,
					undead = types[SkillType.CreatesUndeadMinion] and true or false,
					-- (models.fallen_skeletons, MECH-B, MECHB20: Death Storm strikes fallen Skeletons)
					skeleton = types[SkillType.CreatesSkeletonMinion] and true or false,
					-- (models.minion_deaths, MECH-B) revival: PoB's MinionRevivalSpeed modifier (Sacrifice's 40% slower,
					-- "Minions Revive N% faster") and the skill's own base resummon time (minion_base_resummon_time_ms)
					reviveSpeed = list:Flag(cfg, "RevivingMinion") and calcLib.mod(list, cfg, "MinionRevivalSpeed") or nil,
					reviveBase = list:Flag(cfg, "RevivingMinion") and list:Override(cfg, "BaseReviveTime") or nil,
				}
			end)
			if ok then
				if (summon.limit or 0) <= 0 and (summon.desired or 0) > 0 then
					summon.limit, summon.limitFrom = summon.desired, "desired"
				elseif (summon.limit or 0) <= 0 and summon.persistent then
					-- (AUDIT29 A29-2) the Reavers, Brutes, Snipers, Clerics, Arsonists, Frost and Storm Mages carry no
					-- count stat in the game files or PoB: one each, the least a summon can mean (UNVERIFIED: poe2db
					-- gives no count either; an undercount if a gem summons more)
					summon.limit, summon.limitFrom = 1, "default"
				end
				skills[#skills].summon = summon
			end
		end
		if wantStats[effect.name] and effect.statSets and effect.statSets[1] then
			-- every stat set's stats (the first set's value wins where two share a stat); ledger
			-- gem_stat entries read stats of any set
			local stats = setmetatable({ }, { __jsontype = "object" })
			for _, set in ipairs(effect.statSets) do
				local ok, built = pcall(calcLib.buildSkillInstanceStats, activeSkill.activeEffect, effect, set, altGemQuality())
				for stat, value in pairs(ok and built or { }) do
					if type(value) == "number" and stats[stat] == nil then
						stats[stat] = value
					end
				end
			end
			skills[#skills].levelStats = stats
		end
		if args and args.supportStats then
			-- the named stats of the supports on this skill, summed (conditions.armour_broken_uptime: Armour Break supports)
			local stats = setmetatable({ }, { __jsontype = "object" })
			for _, support in ipairs(activeSkill.effectList or { }) do
				local granted = support.grantedEffect
				if granted and granted.support and granted.statSets and granted.statSets[1] then
					local ok, built = pcall(calcLib.buildSkillInstanceStats, support, granted, granted.statSets[1],
						altGemQuality())
					for _, stat in ipairs(args.supportStats) do
						local value = ok and built and built[stat]
						if type(value) == "number" then
							stats[stat] = (stats[stat] or 0) + value
						end
					end
				end
			end
			skills[#skills].supportStats = stats
		end
		-- Support stats MECH-B's models read on every call (MECHB11: models.TOTEM_ONE_STAT, TOTEM_SPLINTER_STAT,
		-- MINION_PACT_STAT; minion_deaths has callers that ask for none): read from the support's own constant stats and
		-- flag stats (a flag reads 1, as CalcTools 183), without building it. Merged into supportStats.
		do
			local found
			for _, support in ipairs(activeSkill.effectList or { }) do
				local granted = support.grantedEffect
				local set = granted and granted.support and granted.statSets and granted.statSets[1]
				if set then
					for _, pair in ipairs(set.constantStats or { }) do
						if modelSupportStats[pair[1]] then
							found = found or { }
							found[pair[1]] = (found[pair[1]] or 0) + (pair[2] or 0)
						end
					end
					for _, name in ipairs(set.stats or { }) do
						if modelSupportStats[name] then
							found = found or { }
							found[name] = (found[name] or 0) + 1
						end
					end
				end
			end
			if found then
				local stats = skills[#skills].supportStats or setmetatable({ }, { __jsontype = "object" })
				for name, value in pairs(found) do
					if stats[name] == nil then
						stats[name] = value
					end
				end
				skills[#skills].supportStats = stats
			end
		end
		if activeSkill.minion and types[SkillType.Triggered] then
			-- a triggered skill that summons Temporary minions (Wardbound Minions in a meta group): PoB counts one minion
			-- casting nonstop, so what each one lives (models.minion_count) is reported. Only the main skill's minion
			-- gets a full calculation, so its cast speed is the minion modifiers' (the skill's and yours) over its
			-- skill's base cast time.
			local ok, life = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				local minionMods = new("ModList")
				for _, value in ipairs(list:List(cfg, "MinionModifier")) do
					minionMods:AddMod(value.mod)
				end
				local minionSkill = activeSkill.minion.mainSkill
				local casts
				for _, set in ipairs(effect.statSets or { }) do
					local built = calcLib.buildSkillInstanceStats(activeSkill.activeEffect, effect, set, altGemQuality())
					for stat, value in pairs(built or { }) do
						if stat:match("maximum_number_of_casts$") and type(value) == "number" then
							casts = value
						end
					end
				end
				local base = (activeSkill.skillData.duration or 0) + list:Sum("BASE", cfg, "Duration", "PrimaryDuration")
				return {
					duration = base * calcLib.mod(list, cfg, "Duration", "PrimaryDuration"),
					maxCasts = casts,
					limit = math.floor(calcLib.val(list, activeSkill.minion.minionData.limit, cfg)),
					castTime = minionSkill and minionSkill.activeEffect.grantedEffect.castTime or nil,
					speed = calcLib.mod(minionMods, { flags = ModFlag.Cast }, "Speed"),
				}
			end)
			skills[#skills].minion = ok and life or { failed = true }
		end
	end
	-- the boss's base Armour (PoB's enemy preset), for Armour Break models (Ferocious Roar)
	local enemyArmour = env and env.enemyDB and env.enemyDB:Sum("BASE", nil, "Armour") or 0
	return { skills = skills, enemyArmour = enemyArmour }
end

-- What PoB can't calculate: every passive-tree line and unique-item line its modifier parser
-- doesn't understand (those lines do nothing in PoB's numbers), for the gap ledger
function bridge.auditUnsupported()
	local passives, uniques = { }, { }
	for id, node in pairs(build.spec.tree.nodes) do
		for i, mod in ipairs(node.mods or { }) do
			local line = node.sd and node.sd[i]
			if line and not mod.combined and (mod.extra or not mod.list or #mod.list == 0) then
				passives[#passives + 1] = { id = id, node = node.dn, type = node.type, ascendancy = node.ascendancyName,
					line = line }
			end
		end
	end
	for name, unique in pairs(main.uniqueDB.list) do
		local ok, item = pcall(makeItem, unique.raw)
		if ok and item then
			for _, kind in ipairs({ "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					if modLine.extra or #(modLine.modList or { }) == 0 then
						uniques[#uniques + 1] = { unique = item.title or name, base = item.baseName, line = modLine.line }
					end
				end
			end
		end
	end
	return { passives = passives, uniques = uniques }
end

-- Coverage audit (builder/ledger/coverage.py): what PoB's numbers can see beyond its own gap list.
-- names(list): the modifier names a parsed modifier list produces, with those wrapped inside list
-- modifiers (an enemy's or a minion's modifier) as "Outer>Inner"
local function modNames(list, out)
	out = out or { }
	for _, mod in ipairs(list or { }) do
		if mod.name then
			local name = mod.name
			local inner = type(mod.value) == "table" and mod.value.mod
			if inner and inner.name then
				name = name .. ">" .. inner.name
			end
			out[#out + 1] = name
		end
	end
	return out
end

-- One parsed modifier in full, for the audit's checks of what a parse kept (name, type, value,
-- flags, keyword flags, tags as "Type=var", "!Type=var" when negated; an inner modifier as `i`)
local function modDesc(mod)
	local tags = { }
	for _, tag in ipairs(mod) do
		local value = tag.var or (tag.varList and table.concat(tag.varList, "|")) or tag.skillType or tag.skillName
			or tag.stat or (tag.statList and table.concat(tag.statList, "|")) or tag.keyword or tag.skillId
		-- (COV35) the actor on a varList too: "while a Rare or Unique Enemy is in your Presence" is an enemy ActorCondition
		-- with varList {NearbyRareOrUniqueEnemy, RareOrUnique}; without the actor the audit read it as the player's own
		-- conditions and called it never set (the boss Configuration sets the enemy's RareOrUnique, ConfigOptions 1980-2099)
		if tag.actor and (tag.var or tag.varList) then
			value = tag.actor .. ":" .. value
		end
		tags[#tags + 1] = (tag.neg and "!" or "") .. tostring(tag.type) .. (value ~= nil and ("=" .. tostring(value)) or "")
	end
	local value, inner = mod.value, nil
	if type(value) == "table" then
		if value.mod then
			inner = modDesc(value.mod)
			value = "mod"
		elseif value.key then
			value = "key:" .. tostring(value.key) .. (type(value.value) == "number" and ("=" .. value.value) or "")
		else
			value = "table"
		end
	end
	return { n = mod.name, t = mod.type, v = value, f = mod.flags, k = mod.keywordFlags, g = tags, i = inner }
end

local function modDescs(list)
	local out = { }
	for _, mod in ipairs(list or { }) do
		if mod.name then
			out[#out + 1] = modDesc(mod)
		end
	end
	return out
end

-- Every item affix, rune / Soul Core / Idol line and jewel mod, through the parser as the item
-- does (ranges at their maximum); every gem's stats against its stat set's stat map (PoB's skills
-- tab marks the same unmapped stats as unsupported); and the modifier names that passives,
-- uniques, affixes and mapped gem stats produce, for the check against what calculations read.
function bridge.auditCoverage()
	local function parse(line)
		local resolved = itemLib.applyRange(line, 1.0)
		local list, extra = modLib.parseMod(resolved)
		local supported = list ~= nil and #list > 0 and not extra
		return supported, modNames(list), extra, modDescs(list)
	end
	local affixes = { }
	-- A mod's lines as PoB reads them on an item: a line it can't parse is joined with the next one when the two parse
	-- together (Item.lua's "Try to combine it with the next line"), so a two-line stat description ("Increases and
	-- Reductions to Mana Regeneration Rate also" / "apply to Energy Shield Recharge Rate") is one line PoB counts, not
	-- two it ignores (COV11; FIX_QUEUE, PLANS1's catalog finding). Not for runes: PoB rebuilds an item's rune lines
	-- from the rune's name (ItemClass:UpdateRunes), and reads neither half of Jiquani's Thesis's boots pair (COV9,
	-- items-two-line.yaml, whose whole-sentence stand-in needs the halves listed as unparsed). Yields line, supported,
	-- names, mods.
	local function modLines(part, join)
		local found, i = { }, 1
		while part[i] do
			local line = part[i]
			local supported, names, _, mods = parse(line)
			if join and not supported and part[i + 1] then
				local joined = line .. " " .. part[i + 1]:gsub("%b{}", ""):gsub(" ?%(%l+%)", "")
				local ok, joinedNames, _, joinedMods = parse(joined)
				if ok then
					line, supported, names, mods = joined, ok, joinedNames, joinedMods
					i = i + 1
				end
			end
			found[#found + 1] = { line = line, supported = supported, names = names, mods = mods }
			i = i + 1
		end
		return found
	end
	for pool, mods in pairs(build.data.itemMods) do
		if pool == "Runes" then
			for name, rune in pairs(mods) do
				for slot, mod in pairs(rune) do
					if type(mod) == "table" then
						for _, part in ipairs({ mod, mod.bonded }) do
							for _, l in ipairs(modLines(part, false)) do
								affixes[#affixes + 1] = { pool = "Runes", id = name, slot = slot, bonded = part == mod.bonded or nil,
									line = l.line, supported = l.supported, names = l.names, rune = mod.type, mods = l.mods }
							end
						end
					end
				end
			end
		else
			for id, mod in pairs(mods) do
				for _, l in ipairs(modLines(mod, true)) do
					affixes[#affixes + 1] = { pool = pool, id = id, type = mod.type, line = l.line, supported = l.supported,
						names = l.names, level = mod.level, tags = mod.modTags, mods = l.mods }
				end
			end
		end
	end
	local gemsOf = { }
	for _, gem in pairs(build.data.gems) do
		for _, effect in ipairs(gem.grantedEffectList or { gem.grantedEffect }) do
			if effect and effect.id then
				gemsOf[effect.id] = gemsOf[effect.id] or { }
				table.insert(gemsOf[effect.id], gem.name)
			end
		end
	end
	local skills = { }
	for id, effect in pairs(build.data.skills) do
		local sets = { }
		for setIndex, set in ipairs(effect.statSets or { }) do
			local stats = { }
			local function check(stat, source)
				if type(stat) ~= "string" or stats[stat] then
					return
				end
				local map = set.statMap[stat]
				local names = { }
				if map then
					for _, modOrGroup in ipairs(map) do
						if modOrGroup.name then
							modNames({ modOrGroup }, names)
						else
							modNames(modOrGroup, names)
						end
					end
				end
				stats[stat] = { source = source, mapped = map ~= nil, names = names }
			end
			for _, stat in ipairs(set.stats or { }) do
				check(stat, "level")
			end
			for _, constant in ipairs(set.constantStats or { }) do
				check(constant[1], "constant")
			end
			for _, quality in ipairs(effect.qualityStats or { }) do
				check(quality[1], "quality")
			end
			local list = { }
			for stat, info in pairs(stats) do
				info.stat = stat
				list[#list + 1] = info
			end
			local flags = { }
			for flag in pairs(set.baseFlags or { }) do
				flags[#flags + 1] = flag
			end
			sets[#sets + 1] = { label = set.label, stats = list, baseFlags = flags }
		end
		skills[#skills + 1] = { id = id, name = effect.name, hidden = effect.hidden or nil, support = effect.support or nil,
			gems = gemsOf[id], sets = sets }
	end
	local passiveNames, uniqueNames = { }, { }
	for id, node in pairs(build.spec.tree.nodes) do
		for i, mod in ipairs(node.mods or { }) do
			local line = node.sd and node.sd[i]
			if line and mod.list and #mod.list > 0 and not mod.extra then
				passiveNames[#passiveNames + 1] = { id = id, node = node.dn, type = node.type, ascendancy = node.ascendancyName,
					line = line, names = modNames(mod.list), mods = modDescs(mod.list) }
			end
		end
	end
	for name, unique in pairs(main.uniqueDB.list) do
		local ok, item = pcall(makeItem, unique.raw)
		if ok and item then
			for _, kind in ipairs({ "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					if not modLine.extra and #(modLine.modList or { }) > 0 then
						uniqueNames[#uniqueNames + 1] = { unique = item.title or name, line = modLine.line,
							names = modNames(modLine.modList), mods = modDescs(modLine.modList) }
					end
				end
			end
		end
	end
	-- Timeless jewels' passives (the tree's legion data: the notables a conquered passive becomes and the lines it gains)
	local legion = { }
	local data = build.spec.tree.legion or { }
	for _, kind in ipairs({ "nodes", "additions" }) do
		for _, node in ipairs(data[kind] or { }) do
			for _, line in ipairs(node.sd or { }) do
				local supported, names, _, mods = parse(line)
				legion[#legion + 1] = { kind = kind, id = node.id, node = node.dn, line = line, supported = supported, names = names,
					mods = mods, keystone = node.ks or nil, notable = node["not"] or nil }
			end
		end
	end
	-- Modifiers every skill carries in its data regardless of stats (baseMods): conditions and flags they set
	for _, skill in ipairs(skills) do
		local effect = build.data.skills[skill.id]
		local base = { }
		for _, part in ipairs(tableConcat({ effect }, effect.statSets or { })) do
			for _, mod in ipairs(part.baseMods or { }) do
				if mod.name then
					base[#base + 1] = modDesc(mod)
				else
					for _, inner in ipairs(mod) do
						base[#base + 1] = modDesc(inner)
					end
				end
			end
		end
		skill.baseMods = base
	end
	return { affixes = affixes, skills = skills, passives = passiveNames, uniques = uniqueNames, legion = legion }
end

-- Audit item (e): every active skill gem alone on a plain level-90 character (no gear, no passives), its
-- damage as PoB calculates it; the skills that calculate 0 there are listed by the coverage audit
function bridge.auditSkills(args)
	bridge.newBuild()
	bridge.setLevel({ level = 90 })
	local fields = args.fields or { "CombinedDPS", "TotalDPS", "TotalDotDPS", "FullDPS", "AverageDamage" }
	local out, index = { }, nil
	-- (COV32; COV31's finding) every active skill id once, not one gem per name: since PERF24 a by-name "Lightning Bolt"
	-- is Choir of the Storm's triggered bolt, so the castable one, the one-handed Mace Strike and the off-hand Spear Stab
	-- were never swept on their own. Each row carries its skillId (the gem's grantedEffectId). args.only (names) limits
	-- the sweep (tests)
	local only
	for _, name in ipairs(args.only or { }) do
		only = only or { }
		only[name] = true
	end
	local sweep = { }
	for _, entry in ipairs(gemsInOrder()) do
		local effect = entry.gem.grantedEffect
		local id = entry.gem.grantedEffectId
		if effect and not effect.support and id and not sweep[id] and (not only or only[entry.gem.name]) then
			sweep[id] = true
			sweep[#sweep + 1] = { name = entry.gem.name, skillId = id, effect = effect }
		end
	end
	table.sort(sweep, function(a, b)
		if a.name ~= b.name then
			return a.name < b.name
		end
		return a.skillId < b.skillId
	end)
	-- an attack gets a plain weapon of the first kind it allows (Normal rarity, the highest base of that kind)
	local weaponFor = { }
	for baseName, base in pairs(build.data.itemBases) do
		if base.type and base.weapon and not base.hidden then
			local best = weaponFor[base.type]
			if not best or (base.req and base.req.level or 0) > (best.req and best.req.level or 0) then
				weaponFor[base.type] = { name = baseName, req = base.req }
			end
		end
	end
	for _, row in ipairs(sweep) do
		local name, skillId = row.name, row.skillId
		local effect = row.effect
		local kinds = { }
		for kind in pairs(effect.weaponTypes or { }) do
			kinds[#kinds + 1] = kind
		end
		table.sort(kinds)
		local weapon = kinds[1] and weaponFor[kinds[1]]
		pcall(bridge.equip, { slot = "Weapon 1", raw = weapon and ("Rarity: NORMAL\n" .. weapon.name .. "\n" .. weapon.name) or nil })
		if #build.skillsTab.socketGroupList == 0 then
			index = nil  -- (a skill's setup can drop the group: start a new one)
		end
		local ok, result = pcall(bridge.setGroup, { index = index, gems = { { name = name, skillId = skillId, level = 20, quality = 0 } },
			main = true, fields = fields })
		if ok then
			local minion = build.calcsTab.mainOutput and build.calcsTab.mainOutput.Minion
			result.stats.MinionDPS = minion and (minion.CombinedDPS or minion.TotalDPS) or nil
			result.stats.weapon = weapon and weapon.name or nil
			index = 1
			local types = { }
			for skillType in pairs(effect.skillTypes or { }) do
				for typeName, value in pairs(SkillType) do
					if value == skillType then
						types[#types + 1] = typeName
					end
				end
			end
			out[#out + 1] = { name = name, skillId = skillId, stats = result.stats, types = types }
		else
			out[#out + 1] = { name = name, skillId = skillId, error = tostring(result) }
		end
	end
	return { skills = out }
end

-- The coverage audit's parse of any lines (ranges at their maximum), as auditCoverage parses affixes: for the
-- unique lines of variants an item's own parse doesn't select
function bridge.auditLines(args)
	local results = { }
	for i, line in ipairs(args.lines or { }) do
		local list, extra = modLib.parseMod(itemLib.applyRange(line, 1.0))
		results[i] = { line = line, supported = list ~= nil and #list > 0 and not extra, names = modNames(list), mods = modDescs(list) }
	end
	return { results = results }
end

-- Does PoB's modifier parser understand these lines? For checking ledger translations.
function bridge.parseLines(args)
	local results = { }
	for i, line in ipairs(args.lines or { }) do
		local list, extra = modLib.parseMod(line)
		local parsed = { }
		for _, mod in ipairs(list or { }) do
			local tags, varLists = { }, { }
			for _, tag in ipairs(mod) do
				tags[#tags + 1] = tostring(tag.type) .. (tag.var and (":" .. tostring(tag.var)) or "")
					.. (tag.skillType and (":" .. tostring(tag.skillType)) or "")
				if tag.varList then  -- (MECH80: a tag's list of conditions, e.g. the enemy's ailments, L4-3)
					varLists[#varLists + 1] = tostring(tag.type) .. ":" .. table.concat(tag.varList, ",")
				end
			end
			parsed[#parsed + 1] = { name = mod.name, type = mod.type,
				value = type(mod.value) == "table" and (mod.value.mod and mod.value.mod.name or "table") or mod.value,
				flags = mod.flags, keywordFlags = mod.keywordFlags, tags = tags,
				varLists = #varLists > 0 and varLists or nil }  -- (only when a tag has one: exact-match tests compare the rest)
		end
		results[i] = { line = line, supported = list ~= nil and #list > 0 and not extra, mods = list and #list or 0,
			extra = extra, parsed = parsed }
	end
	return { results = results }
end

-- Ledger corrections for passive lines PoB ignores: each node gets the parsed modifiers of its
-- replacement lines added to its own modifier list, so allocations and what-ifs both see them.
-- Applied once per tree per engine (the tree is shared by every build this engine loads).
-- PoB re-parses an allocated ascendancy node's text whenever paths are rebuilt
-- (PassiveTree:ProcessStats gives it a fresh modifier list), so the tree's ProcessStats is wrapped
-- to add the node's ledger modifiers back each time.
-- A correction with `drop` (a tree line PoB parses but counts wrongly: a `pob_wrong` entry, builder/search/
-- passive_models.py wrong_tree_lines) also removes the modifiers PoB parsed from that line, here and after each re-parse.
local correctedTrees = setmetatable({ }, { __mode = "k" })
local function dropLedgerLines(node, modList)
	if not (node.ledgerDrop and node.sd and node.mods and modList) then
		return
	end
	local gone = { }
	for i, text in ipairs(node.sd) do
		if node.ledgerDrop[text] and node.mods[i] and node.mods[i].list then
			for _, mod in ipairs(node.mods[i].list) do
				gone[mod] = true
			end
		end
	end
	for k = #modList, 1, -1 do
		if gone[modList[k]] then
			table.remove(modList, k)
		end
	end
end
function bridge.applyPassiveCorrections(args)
	local tree = build.spec.tree
	local done = correctedTrees[tree]
	if not done then
		done = { }
		correctedTrees[tree] = done
		local processStats = tree.ProcessStats
		rawset(tree, "ProcessStats", function(self, node, startIndex)
			local result = processStats(self, node, startIndex)
			dropLedgerLines(node, node.modList)
			if node.ledgerMods and (startIndex or 1) == 1 then
				for _, mod in ipairs(node.ledgerMods) do
					node.modList:AddMod(mod)
				end
			end
			return result
		end)
	end
	local applied, failed = 0, { }
	for _, correction in ipairs(args.nodes or { }) do
		local key = tostring(correction.id) .. "|" .. correction.entry
		local node = tree.nodes[tonumber(correction.id)]
		if node and not done[key] then
			done[key] = true
			-- The current build may hold its own copy of the node's modifiers (made before this
			-- correction); later builds read or copy the corrected tree node
			local specNode = build.spec.nodes[tonumber(correction.id)]
			local specList = specNode and rawget(specNode, "modList")
			if specList == node.modList then
				specList = nil
			end
			if correction.drop then
				node.ledgerDrop = node.ledgerDrop or { }
				node.ledgerDrop[correction.drop] = true
				dropLedgerLines(node, node.modList)
				dropLedgerLines(node, specList)
			end
			for _, line in ipairs(correction.lines or { }) do
				-- "flag:Name" switches ledger condition Name on; "if:Name <line>" counts only while it's on;
				-- "mult:Name N" adds N to a custom model's input Name (PoB multiplier LedgerName);
				-- "share:Name <line>" counts the line at the share of the fight condition Name holds: its value / 100 x
				-- Multiplier:LedgerShareName, which the assumptions set to the share in whole percent (COV11; e.g. the
				-- boss's share of the fight in the build's Jagged Ground, conditions.jagged_ground). PoB floors a
				-- Multiplier tag's count (ModStore), so the share is carried in percent, not as a fraction.
				local flag = line:match("^flag:(%w+)$")
				local multName, multValue = line:match("^mult:(%w+)%s+(-?[%d%.]+)$")
				local condition, rest = line:match("^if:(%w+)%s+(.+)$")
				local share, shareRest = line:match("^share:(%w+)%s+(.+)$")
				if share then
					rest = shareRest
				end
				local list, extra
				if flag then
					list = { modLib.createMod("Condition:Ledger" .. flag, "FLAG", true) }
				elseif multName then
					list = { modLib.createMod("Multiplier:Ledger" .. multName, "BASE", tonumber(multValue)) }
				else
					list, extra = modLib.parseMod(rest or line)
				end
				if list and not extra then
					for _, parsed in ipairs(list) do
						local copy = copyTable(parsed)
						if condition then
							copy[#copy + 1] = { type = "Condition", var = "Ledger" .. condition }
						elseif share then
							if type(copy.value) == "number" then
								copy.value = copy.value / 100
							elseif type(copy.value) == "table" and type(copy.value.mod) == "table"
									and type(copy.value.mod.value) == "number" then
								copy.value.mod.value = copy.value.mod.value / 100  -- (a LIST of one nested mod: EnemyModifier)
							end
							table.insert(copy, 1, { type = "Multiplier", var = "LedgerShare" .. share })  -- (COV31: first, as installLedgerShare)
						end
						modLib.setSource(copy, "Ledger:" .. correction.entry)
						node.modList:AddMod(copy)
						node.ledgerMods = node.ledgerMods or { }
						node.ledgerMods[#node.ledgerMods + 1] = copy
						if specList then
							specList:AddMod(copy)
						end
					end
					applied = applied + 1
				else
					failed[#failed + 1] = line
				end
			end
			node.ledger = node.ledger or { }
			node.ledger[#node.ledger + 1] = correction.entry
		end
	end
	return { applied = applied, failed = failed }
end

-- (JEWEL2) A hidden Ring 3, or a jewel in its sockets: PoB's calculation ignores both without a "+1 Ring Slot" (CalcSetup's
-- AdditionalRingSlot checks), but no slot flag says so, and the search keeps a free ring there until the passive is taken
-- (Gear.add_ring_slot). The readers below skip them, as PoB does (probe POE2T7947)
local function inHiddenRing(slot)
	if (slot.parentSlot and slot.parentSlot.slotName or slot.slotName) ~= "Ring 3" then
		return false
	end
	local env = build.calcsTab.mainEnv
	return env ~= nil and not env.modDB:Flag(nil, "AdditionalRingSlot")
end

-- The current build's blind spots: lines on its allocated passives and equipped items that PoB
-- doesn't calculate
function bridge.unsupportedInBuild()
	local found = { }
	for id, node in pairs(build.spec.allocNodes) do
		for i, mod in ipairs(node.mods or { }) do
			local line = node.sd and node.sd[i]
			if line and not mod.combined and not node.isAttribute and (mod.extra or not mod.list or #mod.list == 0) then
				found[#found + 1] = { source = "passive: " .. tostring(node.dn), line = line }
			end
		end
	end
	for slotName, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if item and not slot.inactive and not inHiddenRing(slot) then
			for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					local text = tostring(modLine.line or "")
					local standin = modLine.ledgerBonded or text:match("^LedgerBonded:") or text:match("^LedgerShare:")
					if (modLine.extra or #(modLine.modList or { }) == 0) and not standin then
						found[#found + 1] = { source = slotName .. ": " .. tostring(item.title or item.name), line = modLine.line }
					end
				end
			end
		end
	end
	return { lines = found }
end

-- Campaign quest rewards that are a choice (Configuration tab lists), as PoB names them
function bridge.questOptions()
	local quests = { }
	for _, quest in ipairs(data.questRewards) do
		if quest.useConfig ~= false and quest.Options then
			quests[#quests + 1] = {
				var = "quest" .. quest.Description .. quest.Area .. quest.Info,
				label = string.format("%s: %s (%s)", quest.Description, quest.Area, quest.Info),
				options = quest.Options,
				current = build.configTab.input["quest" .. quest.Description .. quest.Area .. quest.Info],
			}
		end
	end
	return { quests = quests }
end

-- Text of everything the build draws modifiers from, for evidence checks (does the build have a
-- source of Onslaught, Frenzy Charges, Shock, ...): allocated passives, equipped item lines and
-- the descriptions of enabled gems
function bridge.sourceLines()
	local lines, shares = { }, { }
	local function add(source, text)
		if type(text) == "string" and text ~= "" then
			lines[#lines + 1] = { source = source, text = text }
		end
	end
	local keystones = { }
	for _, node in pairs(build.spec.allocNodes) do
		for _, line in ipairs(node.sd or { }) do
			add("passive:" .. (node.dn or node.id), line)
		end
		if node.type == "Keystone" and node.dn then
			keystones[node.dn] = true
		end
	end
	-- (COV30, AUDIT35 A35-1) a keystone the build has without allocating it: an item's line names it (Flesh Crucible's
	-- variants, "Grants keystone" lines) and PoB adds a Keystone LIST mod, which mergeKeystones (CalcPerform 147-157)
	-- turns into the tree node's own modList, ledger corrections included (applyPassiveCorrections adds them to every
	-- node, allocated or not; keystoneMap holds the same node tables, PassiveTree.lua 252). Its text came from no
	-- allocated node, so the models (models.read_inputs) and the condition resolver never read it: Ritual Cadence from a
	-- Flesh Crucible counted none of its model lines. Given as the keystone's passive lines, as if allocated.
	local env = build.calcsTab and build.calcsTab.mainEnv
	local keystoneMap = build.spec.tree.keystoneMap or { }
	for _, name in ipairs(env and env.modDB and env.modDB:List(nil, "Keystone") or { }) do
		local node = type(name) == "string" and keystoneMap[name]
		if node and node.dn and not keystones[node.dn] then
			keystones[node.dn] = true
			for _, line in ipairs(node.sd or { }) do
				add("passive:" .. node.dn, line)
			end
		end
	end
	-- (COV44) Necromantic Talisman, "All bonuses from Equipped Amulet apply to your Minions instead of you": PoB moves the
	-- Amulet's mods to the minions when node 39935 is allocated (CalcSetup 1549: env.talismanModList, not the player's
	-- itemModDB; CalcPerform 1101 / 1233), so the Amulet's lines aren't the player's either: before, the models read them
	-- as the player's (probe POE2T3907: Life 1323 with or without a "+50 to maximum Life" Amulet under the Talisman, and
	-- both its lines still listed here). PoB's own test, so the two can't disagree; the minions' side of a line only a
	-- model reads isn't counted (an undercount, said in COV44's report). An item's Talisman (Flesh Crucible) too: the
	-- initEnv wrapper makes PoB move the Amulet then as well (grantedTalismanNode), and `keystones` holds it.
	local talismanNode = build.spec.allocNodes[39935]
	local talisman = (talismanNode and talismanNode.dn == "Necromantic Talisman") or keystones["Necromantic Talisman"]
	for slotName, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		-- a flask or charm slot the search switched off (flasks.ACTIVE_UPTIME) gives no lines either (MECHC4; COVD5: before,
		-- The Fall of the Axe's "Grants Onslaught during effect" reached the Onslaught model from a switched-off charm and
		-- counted full-time): PoB applies such an item's mods only while its slot is active
		local flaskOff = (slotName:match("^Flask") or slotName:match("^Charm")) and not slot.active
		local toMinions = talisman and slotName == "Amulet" and item and item.type == "Amulet"
		-- (SEARCH40, from MECHC59's question on POE2T7139) the weapon set the main skill isn't using gives no lines either:
		-- PoB's own calculation skips its items and the jewels in them (CalcSetup 1255: slot.weaponSet ~= env.weaponSet,
		-- env.weaponSet the main group's set; POE2T7513: a "+100 to Strength" swap wand left the Oracle's Str at 258), so
		-- before, a model reading such a line counted it on the build (probe POE2T7507: the Oracle's swap wand's lines listed
		-- on set 1). A group on the other set is calculated in PoB's own weaponSetEnvs; its lines aren't the main skill's
		local offSet = slot.weaponSet and env and env.weaponSet and slot.weaponSet ~= env.weaponSet
		if item and not slot.inactive and not inHiddenRing(slot) and not flaskOff and not toMinions and not offSet then
			for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
				-- (COV42; MECHB37 item 2) only the equipped variant's lines: a unique's text holds every variant's lines
				-- ("{variant:1}Repeat +1 time", "{variant:2}... +2 times": Lioneye's Glare) and PoB applies a line only
				-- when Item:CheckModLineVariant passes (Item.lua 1336: the selected variant, alt variants, variant groups
				-- and versions); before, an old variant's line reached the models (probe POE2T3867: both Lioneye lines)
				if not item.CheckModLineVariant or item:CheckModLineVariant(modLine) then
					-- A unique's line can hold its range, "(10-16)% increased ...": the models' and conditions' regexes
					-- read numbers, so give them the rolled line, as PoB's own getRangedModList does (Item.lua 2726-2735;
					-- COVD1: nearly every custom model missed ranged unique lines)
					local text = modLine.line
					-- (COV32) an item `share:` stand-in that replaced its line in place (a unique's or rare's pob_wrong line:
					-- The Lethal Draw, Indigon, the Alloy lines) is listed by name in `shares`, not in `lines`, so a
					-- condition's shown_for still finds it (Assumptions.has_line; before, the line was gone from here and
					-- its share condition never switched on) and no model reads the bare line or a marker as a line
					local shareName = type(text) == "string" and not modLine.ledgerBonded and text:match("^LedgerShare:(%w+)")
					if shareName then
						shares[#shares + 1] = "LedgerShare:" .. shareName
					end
					-- a Bonded rune line only while PoB applies it, without its "Bonded: " prefix, so the models and the
					-- condition resolver read it as the line it is (COV13: before, an inactive one reached the resolver's
					-- unanchored patterns, and an active one no anchored model input); a ledger stand-in's text never
					-- (a `share:` line's stand-in, COV17's installLedgerShare, never either: the models read the line it stands for)
					if modLine.ledgerBonded or (type(text) == "string" and (text:match("^LedgerBonded:") or text:match("^LedgerShare:")))
							or (kind == "runeModLines" and not runeLineApplies(item, modLine)) then
						text = nil
					elseif modLine.bonded and type(text) == "string" then
						text = text:gsub("^Bonded:%s*", "")
					end
					if type(text) == "string" and text:find("%((%-?%d+%.?%d*)%-(%-?%d+%.?%d*)%)") then
						text = itemLib.applyRange(text, modLine.range or main.defaultItemAffixQuality or 0.5,
							modLine.valueScalar, modLine.corruptedRange)
					end
					add("item:" .. slotName, text)
				end
				end
			end
		end
	end
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		if group.enabled ~= false then
			for _, gem in ipairs(group.gemList or { }) do
				local effect = gem.gemData and gem.gemData.grantedEffect or gem.grantedEffect
				if gem.enabled ~= false and effect then
					add("gem:" .. (gem.gemData and gem.gemData.name or effect.name), effect.description)
				end
			end
		end
	end
	return { lines = lines, shares = shares }
end

-- Passive points available at the build's level, as PoB counts them (level - 1 plus every
-- campaign quest point; weapon-set points are separate)
function bridge.pointBudget()
	local questPoints = build.acts[build.maxActs].questPoints
	local used, ascUsed = countAllocated()
	return {
		passives = math.max(build.characterLevel - 1, 0) + questPoints + extraPassivePoints, ascendancy = 8,
		used = used, ascendancyUsed = ascUsed, questPoints = questPoints,
	}
end

-- (SEARCH38) The passive points beyond level and quests for the build being searched
function bridge.setExtraPoints(args)
	local points = args.points or 0
	local weaponSet = args.weaponSetPoints or 0
	if points ~= extraPassivePoints or weaponSet ~= extraWeaponSetPoints then
		extraPassivePoints, extraWeaponSetPoints = points, weaponSet
		recalc()
	end
	return { points = extraPassivePoints, weaponSetPoints = extraWeaponSetPoints }
end

-- Commit an attribute choice (1 Strength, 2 Dexterity, 3 Intelligence, 4-6 Traveller's Wisdom's
-- Damage, defences or Cost Efficiency) on allocated nodes
function bridge.setAttributes(args)
	local spec = build.spec
	local traveller = { }
	for id, attribute in pairs(args.choices or { }) do
		local node = nodeById(id)
		if node.alloc and node.isAttribute then
			if TRAVELLER[attribute] then
				traveller[node.id] = attribute
			else
				spec:SwitchAttributeNode(node.id, attribute)
			end
		end
	end
	applyTravellerChoices(traveller)
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Allocated attribute nodes and the attribute each one has chosen (1 Str, 2 Dex, 3 Int, 4-6
-- Traveller's Wisdom choices)
function bridge.attributeNodes()
	local nodes = { }
	for id, node in pairs(build.spec.allocNodes) do
		if node.isAttribute then
			local chosen = 0
			local override = build.spec.hashOverrides[id]
			local stats = table.concat((override or node).sd or { }, " ")
			if override and override.travellerChoice then
				chosen = override.travellerChoice
			elseif stats:find("Strength") then
				chosen = 1
			elseif stats:find("Dexterity") then
				chosen = 2
			elseif stats:find("Intelligence") then
				chosen = 3
			end
			nodes[#nodes + 1] = { id = id, attribute = chosen }
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	return { nodes = nodes }
end

-- Every numeric stat in the main output, flattened one level (e.g. "MainHand.Accuracy"), for
-- comparing PoB versions or explaining a change
function bridge.outputAll()
	local flat = { }
	local function add(prefix, tbl, depth)
		for key, value in pairs(tbl) do
			if type(key) == "string" then
				if type(value) == "number" then
					flat[prefix .. key] = jsonNumber(value)
				elseif type(value) == "table" and depth < 1 and (key == "MainHand" or key == "OffHand" or key == "Minion") then
					add(prefix .. key .. ".", value, depth + 1)
				end
			end
		end
	end
	add("", build.calcsTab.mainOutput, 0)
	return flat
end

-- How PoB read each modifier line on the equipped items: lines it can't calculate are the
-- first place to look when numbers look wrong
function bridge.itemMods()
	local slots = { }
	for name, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if item then
			local lines = { }
			for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					lines[#lines + 1] = {
						kind = kind:gsub("ModLines", ""),
						line = modLine.line,
						supported = not modLine.extra and #(modLine.modList or { }) > 0,
						mods = #(modLine.modList or { }),
					}
				end
			end
			slots[name] = { name = item.name, base = item.baseName, lines = lines }
		end
	end
	return slots
end

-- Game data export ---------------------------------------------------------------------------

-- Convert a PoB data table to plain JSON-able values: consecutive integer keys become arrays,
-- anything else an object with string keys. Functions, userdata and cycles are dropped.
local function plain(value, depth, seen)
	local kind = type(value)
	if kind == "number" then
		return jsonNumber(value)
	elseif kind == "string" or kind == "boolean" then
		return value
	elseif kind ~= "table" or depth <= 0 then
		return nil
	end
	seen = seen or { }
	if seen[value] then
		return nil
	end
	seen[value] = true
	local count, maxIndex = 0, 0
	for key in pairs(value) do
		count = count + 1
		if type(key) == "number" and key >= 1 and key % 1 == 0 then
			maxIndex = math.max(maxIndex, key)
		end
	end
	local result = { }
	if count > 0 and maxIndex == count then
		for i = 1, count do
			result[i] = plain(value[i], depth - 1, seen)
		end
	else
		for key, item in pairs(value) do
			if type(key) == "string" or type(key) == "number" then
				result[tostring(key)] = plain(item, depth - 1, seen)
			end
		end
	end
	seen[value] = nil
	return result
end

local skillTypeNames
local function skillTypeList(types)
	if not skillTypeNames then
		skillTypeNames = { }
		for name, id in pairs(SkillType) do
			skillTypeNames[id] = name
		end
	end
	local list = { }
	for id, enabled in pairs(types or { }) do
		if enabled and skillTypeNames[id] then
			list[#list + 1] = skillTypeNames[id]
		end
	end
	table.sort(list)
	return list
end

local function keyList(set)
	local list = { }
	for key, enabled in pairs(set or { }) do
		if enabled then
			list[#list + 1] = tostring(key)
		end
	end
	table.sort(list)
	return list
end

local dumpers = { }

function dumpers.gems()
	local gems = { }
	for _, entry in ipairs(gemsInOrder()) do  -- (PERF24) the same order from every PoB process
		local id, gem = entry.id, entry.gem
		local effect = gem.grantedEffect or { }
		gems[#gems + 1] = {
			id = id, name = gem.name, baseTypeName = gem.baseTypeName, tier = gem.Tier,
			gemType = gem.gemType, tags = keyList(gem.tags), tagString = gem.tagString,
			support = effect.support and true or false, skillTypes = skillTypeList(effect.skillTypes),
			reqStr = gem.reqStr, reqDex = gem.reqDex, reqInt = gem.reqInt,
			naturalMaxLevel = gem.naturalMaxLevel, grantedEffectId = gem.grantedEffectId,
			description = effect.description,
			-- Socketable by the player unless granted by an item or the tree; legacy gems are gone
			fromItem = effect.fromItem and true or nil, fromTree = effect.fromTree and true or nil,
			legacy = effect.legacy and true or nil, lineage = effect.isLineage and true or nil,
			families = effect.gemFamily and plain(effect.gemFamily, 2) or nil,
			weaponTypes = effect.weaponTypes and keyList(effect.weaponTypes) or nil,
			levelRequirement = effect.levels and effect.levels[1] and effect.levels[1].levelRequirement or nil,
		}
	end
	return { gems = gems }
end

function dumpers.tree()
	local tree = build.spec.tree
	local nodes = { }
	for id, node in pairs(tree.nodes) do
		local linked = { }
		for _, otherId in ipairs(node.linkedId or { }) do
			linked[#linked + 1] = otherId
		end
		table.sort(linked)
		-- Nodes near a class's start that become another passive for some classes (PoB swaps them
		-- in PassiveSpec when that class is the build's): the version each such class gets
		local variants
		if node.isSwitchable and node.options then
			for className, option in pairs(node.options) do
				if tree.classNameMap[className] then
					variants = variants or { }
					variants[className] = { id = option.id, name = option.name, stats = plain(option.stats, 2), icon = option.icon }
				end
			end
		end
		-- How the page draws the node: its icon, its frame (by state) and their sizes
		local frame = node.overlay and node.overlay.alloc and
			{ alloc = node.overlay.alloc, path = node.overlay.path, unalloc = node.overlay.unalloc } or nil
		nodes[#nodes + 1] = {
			id = id, name = node.dn, type = node.type, ascendancy = node.ascendancyName,
			stats = plain(node.sd, 2), linked = linked, x = node.x, y = node.y,
			isAttribute = node.isAttribute or nil, isMultipleChoice = node.isMultipleChoice or nil,
			isMultipleChoiceOption = node.isMultipleChoiceOption or nil,
			classStart = node.classStartIndex,
			icon = node.icon, frame = frame, variants = variants,
			iconSize = node.targetSize and node.targetSize.width or nil,
			frameSize = node.targetSize and node.targetSize.overlay and node.targetSize.overlay.width or nil,
		}
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	local classes = { }
	for classId, class in pairs(tree.classes) do
		local ascendancies = { }
		for ascendId, ascend in pairs(class.classes) do
			if ascend.name ~= "None" then
				ascendancies[#ascendancies + 1] = { id = ascendId, name = ascend.name, startNode = ascend.startNodeId }
			end
		end
		table.sort(ascendancies, function(a, b) return a.name < b.name end)
		classes[#classes + 1] = { id = classId, name = class.name, startNode = class.startNodeId, ascendancies = ascendancies }
	end
	table.sort(classes, function(a, b) return a.name < b.name end)
	-- Where each sprite sits in PoB's texture sheets (file -> name -> {x, y, w, h, layer}), for the
	-- icons and frames the page draws
	local sprites = { }
	for file, entries in pairs(tree.ddsCoords or { }) do
		if file:find("^skills") or file:find("^group%-background") or file:find("^jewel%-sockets") then
			sprites[file] = plain(entries, 3)
		end
	end
	return { treeVersion = build.spec.treeVersion, classes = classes, nodes = nodes, sprites = sprites }
end

function dumpers.bases()
	local bases = { }
	for name, base in pairs(build.data.itemBases) do
		bases[#bases + 1] = {
			name = name, type = base.type, subType = base.subType, hidden = base.hidden or nil,
			req = plain(base.req, 2), tags = keyList(base.tags), implicit = base.implicit,
			socketLimit = base.socketLimit, weapon = plain(base.weapon, 2), armour = plain(base.armour, 2),
			flask = plain(base.flask, 2), charm = plain(base.charm, 2),
		}
	end
	table.sort(bases, function(a, b) return a.name < b.name end)
	return { bases = bases }
end

function dumpers.uniques()
	local uniques = { }
	for name, item in pairs(main.uniqueDB.list) do
		uniques[#uniques + 1] = { name = item.title or name, key = name, base = item.baseName, type = item.type, raw = item.raw }
	end
	table.sort(uniques, function(a, b) return a.key < b.key end)
	return { uniques = uniques }
end

function dumpers.mods()
	local pools = { }
	for pool, mods in pairs(build.data.itemMods) do
		if pool ~= "Runes" then
			local list = { }
			for id, mod in pairs(mods) do
				local entry = plain(mod, 4)
				entry.id = id
				list[#list + 1] = entry
			end
			table.sort(list, function(a, b) return a.id < b.id end)
			pools[pool] = list
		end
	end
	return { pools = pools }
end

function dumpers.runes()
	local runes = { }
	for name, rune in pairs(build.data.itemMods.Runes) do
		runes[#runes + 1] = { name = name, slots = plain(rune, 5) }
	end
	table.sort(runes, function(a, b) return a.name < b.name end)
	return { runes = runes }
end

function bridge.dumpData(args)
	local dumper = dumpers[args.part]
	if not dumper then
		error("unknown data part " .. tostring(args.part))
	end
	return dumper()
end

-- Facts about the loaded build that requirement checks need
function bridge.facts(args)
	local skillGroups = { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		local gems = { }
		for _, gem in ipairs(group.gemList or { }) do
			local effect = gem.gemData and gem.gemData.grantedEffect or gem.grantedEffect
			gems[#gems + 1] = {
				name = gem.gemData and gem.gemData.name or (gem.nameSpec ~= "" and gem.nameSpec) or (gem.grantedEffect and gem.grantedEffect.name) or "",
				support = effect and effect.support and true or false,
				enabled = gem.enabled ~= false,
				skillTypes = skillTypeList(effect and effect.skillTypes),
				tags = gem.gemData and keyList(gem.gemData.tags) or { },
			}
		end
		skillGroups[#skillGroups + 1] = {
			index = index, label = group.label, enabled = group.enabled ~= false, slot = group.slot,
			fromItem = group.source ~= nil or group.sourceItem ~= nil, main = index == build.mainSocketGroup,
			fullDPS = group.includeInFullDPS and true or false, gems = gems,
			set1 = group.set1 ~= false, set2 = group.set2 ~= false,
		}
	end
	local passives = { }
	for id, node in pairs(build.spec.allocNodes) do
		passives[#passives + 1] = { id = id, name = node.dn, type = node.type, ascendancy = node.ascendancyName }
	end
	table.sort(passives, function(a, b) return a.id < b.id end)
	local items = { }
	for slotName, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if item and not slot.inactive and not inHiddenRing(slot) then
			items[#items + 1] = {
				slot = slotName, name = item.title or item.name, base = item.baseName, type = item.type,
				rarity = item.rarity, runes = plain(item.runes, 2) or { },
			}
		end
	end
	table.sort(items, function(a, b) return a.slot < b.slot end)
	local warnings = { }
	-- (SEARCH38) PoB's own limit doesn't know the extra points: its warning stands only if they don't cover the tree
	local covered = (extraPassivePoints > 0 or extraWeaponSetPoints > 0)
		and pointsOver(build.spec.allocNodes, build.calcsTab.mainOutput) == 0
	for _, line in ipairs(build.controls.warnings and build.controls.warnings.lines or { }) do
		local text = tostring(line)
		if not (covered and (text:find("too many passive points", 1, true)
			or (extraWeaponSetPoints > 0 and text:find("too many weapon set", 1, true)))) then
			warnings[#warnings + 1] = tostring(line)
		end
	end
	local summary = bridge.summary()
	summary.skillGroups = skillGroups
	summary.passives = passives
	summary.items = items
	summary.warnings = warnings
	summary.radiusJewels = bridge.radiusJewels().jewels
	summary.treeCheck = bridge.treeCheck()
	summary.stats = pickStats(build.calcsTab.mainOutput, args.fields)
	return summary
end

-- Items currently equipped, by slot
-- Whether PoB accepts an item in a slot with the gear already equipped (dual wielding rules)
function bridge.validForSlot(args)
	local item = makeItem(args.raw)
	return { valid = build.itemsTab:IsItemValidForSlot(item, args.slot) and true or false }
end

-- Flask and charm numbers PoB parsed from each item, local modifiers included (duration, charges
-- per use, local charge gain, recovery per use), for the search's uptime model
function bridge.flaskData(args)
	local keys = { "duration", "chargesMax", "chargesUsed", "gainBase", "gainInc", "gainMod", "lifeTotal", "manaTotal" }
	local results = { }
	for i, raw in ipairs(args.raws) do
		local item = makeItem(raw)
		local data = item.base.flask and item.flaskData or item.base.charm and item.charmData
		local entry = setmetatable({ }, { __jsontype = "object" })
		if data then
			entry.kind = item.base.flask and "Flask" or "Charm"
			entry.unique = item.rarity == "UNIQUE"
			for _, key in ipairs(keys) do
				entry[key] = data[key]
			end
			-- a flask that "cannot be Used but applies its Effect constantly" (Lavianga's Spirits): PoB has no rule for it
			for _, modLine in ipairs(item.explicitModLines or { }) do
				if modLine.line and modLine.line:lower():find("cannot be used but applies its effect constantly", 1, true) then
					entry.constant = true
				end
			end
		end
		results[i] = entry
	end
	return { results = results }
end

-- Gems that can't be used at will, for the search's usage rules (builder/search/usage.py): PoB's
-- HasUsageCondition skill type (a skill's own, or one a support adds to the skills it supports)
-- and the constant stats that name what a use needs or costs (Glory or Combo required, charges
-- required, flask charges consumed, Glory built from Life spent). PoB calculates these skills as if
-- they were used at will. Stat names are matched loosely; usage.py decides what each one means.
-- "flask_throw": the Concoctions' flask_throw_base_charges_used (5 Mana Flask charges a throw; COVC2, the manager's
-- ruling of 12 Nov 2026). usage.py reads it but doesn't act on it yet (an open overcount: see FLASK_THROW there).
-- unusable_until_moved: Echoing Cry and Hit and Run's "travel a distance before each use" (usage.py MOVED)
-- "rapidshot_requiem": Compose Requiem's anguish fill rate and Requiem duration beside its 100-anguish requirement
-- (usage.py ANGUISH_PER_MINUTE, REQUIEM_MS; COVC12: without them the rate read 0 and no build got the 12 / 42 cap)
local USAGE_STAT_WORDS = { "require", "cannot_be_used_unless", "unusable_until", "flask_charges_on_skill_use", "as_glory", "flask_throw", "rapidshot_requiem" }
function bridge.usageRules()
	local rules = setmetatable({ }, { __jsontype = "object" })
	for _, entry in ipairs(gemsInOrder()) do  -- (PERF24) a shared name's rule is the same gem's in every process
		local gem = entry.gem
		if gem.grantedEffect and gem.name and not rules[gem.name] then
			local stats = setmetatable({ }, { __jsontype = "object" })
			local rule = { stats = stats }
			local found = false
			-- A banner's Glory gate is on its second effect (the placed banner), not the reservation. The stats and
			-- usage condition of a Banner-type effect after the gem's first one are also listed in rule.placed
			-- (usage.py "placed banner"; COVC8): they gate placing the banner, not the reserved one. stats keeps
			-- them too (the GlorySkill ledger condition reads it). PoB counts the placed banner's buff only with
			-- the config condition BannerPlanted, which the tool never sets
			local effects = { gem.grantedEffect }
			for _, additional in ipairs(gem.additionalGrantedEffects or { }) do
				effects[#effects + 1] = additional
			end
			for index, effect in ipairs(effects) do
				local placed = index > 1 and effect.skillTypes and effect.skillTypes[SkillType.Banner]
				if placed then
					rule.placed = rule.placed or { }
				end
				for _, statSet in ipairs(effect.statSets or { }) do
					for _, stat in ipairs(statSet.constantStats or { }) do
						for _, word in ipairs(USAGE_STAT_WORDS) do
							if type(stat[1]) == "string" and stat[1]:find(word, 1, true) then
								stats[stat[1]] = stat[2]
								if placed then
									rule.placed[#rule.placed + 1] = stat[1]
								end
								found = true
							end
						end
					end
					-- Talisman stats (usage.py talisman; AUDIT7 A7-3), flags in the stat set's stats list:
					-- usable_with_talisman is an exemption (the skill can be used under a Talisman);
					-- ignore_talisman_inherent_form_restriction / ignore_shapeshift_talisman_requirement (Feral
					-- Invocation, Spirit Vessel, Deadly Resolve's trigger) free the Shapeshift skills socketed in
					-- it from the Talisman's form, never another skill (socketedFormFree)
					local function talismanStat(name)
						if name == "usable_with_talisman" then
							rule.talismanExempt = true
							found = true
						elseif name == "ignore_talisman_inherent_form_restriction" or name == "ignore_shapeshift_talisman_requirement" then
							rule.socketedFormFree = true
							found = true
						end
					end
					for _, name in ipairs(statSet.stats or { }) do
						if type(name) == "string" then
							talismanStat(name)
						end
					end
					for _, stat in ipairs(statSet.constantStats or { }) do
						if type(stat[1]) == "string" then
							talismanStat(stat[1])
						end
					end
					-- "N% chance to build an additional Combo on Hit" (Practiced Combo; usage.py combo_rate, COVC8)
					for _, stat in ipairs(statSet.constantStats or { }) do
						if stat[1] == "additional_combo_gain_chance_%" then
							rule.comboChance = stat[2]
							found = true
						end
					end
					-- Skyfall's Glory gate is a per-level stat, not a constant one: its value at level 20
					for i, name in ipairs(statSet.stats or { }) do
						if name == "active_skill_requires_X_glory" and stats[name] == nil then
							local level = statSet.levels and (statSet.levels[20] or statSet.levels[1])
							stats[name] = level and level[i] or 0
							found = true
						end
					end
				end
				if effect.skillTypes and effect.skillTypes[SkillType.HasUsageCondition] then
					if placed then
						rule.placedUsageCondition = true
					else
						rule.usageCondition = true
					end
					found = true
				end
				for _, skillType in ipairs(effect.addSkillTypes or { }) do
					if skillType == SkillType.HasUsageCondition then
						rule.addsUsageCondition = true
						found = true
					end
				end
			end
			if found then
				rules[gem.name] = rule
			end
		end
	end
	return { gems = rules }
end

-- What a Concoction's throw costs, besides its base 5 Mana Flask charges (usage.py throw_cost; COVC8, RESEARCH16 #82):
-- the player's "increased / reduced Flask Charges used" (FlaskChargesUsed and ManaFlaskChargesUsed, INC and MORE: belts,
-- the passive flasks5, Husk of Dreams' "less") and "chance for Flasks to not consume Charges" (FlaskChanceNotConsumeCharges:
-- Abyss belt prefixes, passives flasks18 / flasks21). PoB applies them to a flask's own use only (ItemsTab 4307-4344)
function bridge.flaskThrowCost()
	local modDB = build.calcsTab.mainEnv.modDB
	return {
		usedInc = modDB:Sum("INC", nil, "FlaskChargesUsed", "ManaFlaskChargesUsed"),
		usedMore = modDB:More(nil, "FlaskChargesUsed", "ManaFlaskChargesUsed"),
		notConsume = modDB:Sum("BASE", nil, "FlaskChanceNotConsumeCharges"),
	}
end

-- Charge generation and flask/charm scaling from the committed build (passives, gear): what keeps
-- flasks and charms going in a boss fight, where no kills refill them
function bridge.chargeGain()
	local modDB = build.calcsTab.mainEnv.modDB
	local function base(name)
		return modDB:Sum("BASE", nil, name)
	end
	local function inc(name)
		return modDB:Sum("INC", nil, name)
	end
	local active = setmetatable({ }, { __jsontype = "object" })
	for _, name in ipairs({ "Flask 1", "Flask 2", "Charm 1", "Charm 2", "Charm 3" }) do
		active[name] = build.itemsTab.slots[name].active and true or false
	end
	-- "Flasks do not recover Life" (Sanguine Tides; the unique mod UniqueLifeFlaskNoRecovery1): PoB doesn't parse it
	local lifeFlaskBlocked = false
	-- Reverie (MECHC3): "Cannot use Life Flasks", "Non-Unique Life Flasks apply their Effects constantly", "(40-60)% less
	-- Life Flask Recovery"; PoB leaves all three unparsed (ModCache: leftover text, so never applied)
	local lifeFlaskUnusable, lifeFlaskConstant, lifeFlaskLess, lifeEffectKept = false, false, 0, false
	local lifeLossPerUse = 0
	local manaRegenOnly = false
	-- (MECHC23; AUDIT33 A33-1, an UNDERCOUNT) "Life Recovery from Flasks also applies to Energy Shield" (Shavronne's
	-- Satchel, UniqueFlaskLifeRecoveryEnergyShield1), its "during Effect" form and "... applies to Energy Shield instead":
	-- PoB reads its flag LifeFlaskAppliesToEnergyShield only for the flask tooltip (ItemsTab 4265), so flasks.py counts
	-- the Life flask's recovery on Energy Shield at its own use rate; "instead" (PoB's LifeFlaskDoesNotApply) takes it
	-- off Life. esRecoveryMod: PoB's EnergyShieldRecoveryRateMod, the tooltip's multiplier on that recovery
	local lifeFlaskToEs, lifeFlaskEsInstead = false, false
	for _, line in ipairs(bridge.sourceLines().lines) do
		local text = line.text:lower()
		if text:match("life recovery from flasks a?l?s?o? ?applies to energy shield") then
			lifeFlaskToEs = true
			if text:match("applies to energy shield instead") then
				lifeFlaskEsInstead = true
			end
		end
		if text:match("^%s*flasks do not recover life%s*$") then
			lifeFlaskBlocked = true
		end
		if text:match("^%s*mana recovery other than regeneration cannot recover mana%s*$") then
			manaRegenOnly = true
		end
		if text:match("^%s*cannot use life flasks%s*$") then
			lifeFlaskUnusable = true
		end
		if text:match("^%s*non%-unique life flasks apply their effects constantly%s*$") then
			lifeFlaskConstant = true
		end
		-- Enduring Elixirs (MECHC4, flasks.full_pool_cap): Life flask effects outlast full Life
		if text:match("^%s*life flask effects are not removed when unreserved life is filled%s*$") then
			lifeEffectKept = true
		end
		-- Crown of Thorns (MECHC5, COVD7): "Lose 5 Life when you use a Skill", a Life loss (not a cost) per skill use
		local lose = text:match("^%s*lose (%d+) life when you use a skill%s*$")
		if lose then
			lifeLossPerUse = lifeLossPerUse + tonumber(lose)
		end
		local less = text:match("^%s*(%d+)%% less life flask recovery%s*$")
		if less then
			lifeFlaskLess = lifeFlaskLess + tonumber(less)
		end
	end
	-- What a charm's "Used when ..." trigger needs from the fight (MECHC2, flasks.trigger_rate): the boss preset's hit
	-- cadence and the damage types it deals, your evade and block, and the build's Perfect Timing skills (the main skill's
	-- uses per second when it is one)
	local output = build.calcsTab.mainOutput or { }
	local boss = setmetatable({ }, { __jsontype = "object" })
	-- each type's damage taken per hit (MECHC6: the boss's types come at their full size, boss_hits.config_vars; flasks.py
	-- weights them by each type's share of the boss's hits)
	local takenByType = setmetatable({ }, { __jsontype = "object" })
	for _, kind in ipairs({ "Physical", "Fire", "Cold", "Lightning", "Chaos" }) do
		boss[kind] = output[kind .. "EnemyDamage"] or 0
		takenByType[kind] = output[kind .. "TakenHit"] or 0
	end
	local perfect, perfectMain = 0, false
	local main = build.calcsTab.mainEnv.player.mainSkill
	-- (MECHC12) the socket groups of the Perfect Timing skills other than the main one, for flasks.Charges.of to rate
	-- their presses (their cooldown, duration or the search's chosen rate; before, one press per 5 s, a flat guess)
	local perfectGroups, groupIndex = { }, { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		groupIndex[group] = index
	end
	for _, skill in ipairs(build.calcsTab.mainEnv.player.activeSkillList) do
		if skill.skillTypes and skill.skillTypes[SkillType.PerfectTiming] then
			perfect = perfect + 1
			perfectMain = perfectMain or skill == main
			local index = skill ~= main and skill.socketGroup and groupIndex[skill.socketGroup]
			if index then
				perfectGroups[#perfectGroups + 1] = index
			end
		end
	end
	-- Wildwood's Gifts (MECHC7; RESEARCH19 #105, KeywordPopups AncientBlooms: "Vivid Blooms grant 5 Charges to all of your
	-- Charms; Primal Blooms grant 5 Charges to all of your Mana Flasks; Wild Blooms grant 5 Charges to all of your Life
	-- Flasks"): Blooms per second from the skill's breach_flame_spawn_rate_ms at its level, each kind's charges, and the
	-- "increased effect" of Ancient Blooms (remnant_effect_+%) for the upper reading (flasks.bloom_charges)
	local blooms = { rate = 0, life = 0, mana = 0, charm = 0, effect = 0 }
	for _, skill in ipairs(build.calcsTab.mainEnv.player.activeSkillList) do
		local effect = skill.activeEffect and skill.activeEffect.grantedEffect
		if effect and effect.name == "Wildwood's Gifts" and effect.statSets then
			local stats = { }
			for _, set in ipairs(effect.statSets) do
				local ok, built = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect, effect, set, altGemQuality())
				for stat, value in pairs(ok and built or { }) do
					if type(value) == "number" and stats[stat] == nil then
						stats[stat] = value
					end
				end
			end
			local ms = stats["breach_flame_spawn_rate_ms"] or 0
			-- (MECHC62; RESEARCH60 R60-2, research/wildwood-gifts-two-copies-r60.md: at most ONE Wildwood's Gifts exists, HIGH:
			-- ModGrantedSkills has one mod for it, The Hollow Mask's; no other carrier; no Uncut gem; a Buff, and same-type
			-- Buffs don't stack) one copy is read, the fastest (the highest level), never a sum over copies
			if ms > 0 and 1000 / ms > blooms.rate then
				blooms.rate = 1000 / ms
				blooms.life = stats["ancient_gifts_life_flask_charges_restored"] or 0
				blooms.mana = stats["ancient_gifts_mana_flask_charges_restored"] or 0
				blooms.charm = stats["ancient_gifts_charm_charges_restored"] or 0
				blooms.effect = stats["remnant_effect_+%"] or 0
			end
		end
	end
	return {
		active = active, lifeFlaskBlocked = lifeFlaskBlocked, lifeFlaskUnusable = lifeFlaskUnusable,
		lifeFlaskToEs = lifeFlaskToEs, lifeFlaskEsInstead = lifeFlaskEsInstead,
		esRecoveryMod = output.EnergyShieldRecoveryRateMod or 1,
		lifeFlaskConstant = lifeFlaskConstant, lifeFlaskLess = lifeFlaskLess, lifeEffectKept = lifeEffectKept,
		-- what fills and drains Life and Mana in the fight (MECHC4, flasks.full_pool_cap: a Life / Mana flask's effect ends
		-- when the pool is full)
		takenHit = output.totalTakenHit or 0, takenByType = takenByType, mom = output.sharedMindOverMatter or 0,
		energyShield = output.EnergyShield or 0, esRegen = output.EnergyShieldRegenRecovery or 0,
		lifeRegen = output.LifeRegenRecovery or 0, manaRegen = output.ManaRegenRecovery or 0,
		lifeCost = output.LifePerSecondCost or 0, manaCost = output.ManaPerSecondCost or 0,
		-- the build's own damage over time on Life (MECHC9: PoB's degen share on Life, after Mind over Matter and Energy
		-- Shield; Heartbound Loop's damage on Minion Death through the model's degen line), flasks.full_pool_cap
		lifeDegen = output.TotalBuildDegen and math.max(0, (output.LifeRegenRecovery or 0) - (output.NetLifeRegen or 0)) or 0,
		bossDamage = boss, enemySkillTime = output.enemySkillTime or 0, evade = output.EvadeChance or 0,
		block = output.EffectiveBlockChance or 0, perfectSkills = perfect, perfectGroups = perfectGroups,
		-- (MECHC16) the boss's Light Stuns on you, for charms "Used when you become Stunned" (recovery.boss_stuns)
		stunThreshold = output.StunThreshold or 0, stunAvoid = output.StunAvoidChance or 0,
		deflect = output.DeflectChance or 0, deflectEffect = output.DeflectEffect or 0,
		-- (MECHC17) the boss's ailments on you, for charms "Used when you become Ignited / Shocked / Frozen / Poisoned",
		-- "start Bleeding" (boss_hits.ailment_rate): your Ailment Threshold and avoidance (100 when immune)
		ailmentThreshold = output.AilmentThreshold or 0, life = output.Life or 0,
		ailmentAvoid = { Ignite = output.IgniteAvoidChance or 0, Shock = output.ShockAvoidChance or 0,
			Freeze = output.FreezeAvoidChance or 0, Bleed = output.BleedAvoidChance or 0,
			Poison = output.PoisonAvoidChance or 0,
			Chill = output.ChillAvoidChance or 0 },  -- (MECHC18) the Slow charms' Chill part (boss_hits.slow_rate)
		blooms = blooms,
		perfectMainRate = perfectMain and (output.Speed or 0) or 0,
		-- the main skill's name (MECHC3: a Concoction main skill spends the Mana Flask's charges, flasks.THROW_CHARGES)
		mainSkill = main and main.activeEffect and main.activeEffect.grantedEffect and main.activeEffect.grantedEffect.name or "",
		flask = base("FlaskChargesGenerated"), lifeFlask = base("LifeFlaskChargesGenerated"),
		manaFlask = base("ManaFlaskChargesGenerated"), flaskGainInc = inc("FlaskChargesGained"),
		charm = base("CharmChargesGenerated"), charmGainInc = inc("CharmChargesGained"),
		flaskDurationInc = inc("FlaskDuration"), charmDurationInc = inc("CharmDuration"),
		lifeRecoveryInc = inc("FlaskRecovery") + inc("FlaskLifeRecovery"),
		manaRecoveryInc = inc("FlaskRecovery") + inc("FlaskManaRecovery"),
		charmLimit = math.min(modDB:Override(nil, "CharmLimit") or modDB:Sum("BASE", nil, "CharmLimit"), 3),
		-- (MECHC5, AUDIT14 A14-2) "Gain a Flask Charge when you deal a Critical Hit" (PoB's FlaskChargeOnCritChance, its
		-- "while at maximum Frenzy Charges" threshold tag evaluated here), the main skill's crits; "reduced / increased Flask
		-- Charges used" (and "from Mana Flasks"), which change a Concoction throw's cost; Crown of Thorns' Life loss
		flaskOnCrit = base("FlaskChargeOnCritChance"),
		critRate = (output.Speed or 0) * (output.HitChance or 100) / 100 * (output.CritChance or 0) / 100,
		manaChargesUsedInc = inc("FlaskChargesUsed") + inc("ManaFlaskChargesUsed"),
		manaChargesUsedMore = modDB:More(nil, "FlaskChargesUsed", "ManaFlaskChargesUsed"),
		-- (MECHC19, COV22) "N% increased / reduced Charm Charges used" (belts, passives, Zerphi's Genesis, Ryslatha's
		-- Coil): PoB parses it (CharmChargesUsed) but only shows it in its calcs tab; flasks.charges_per_use applies it
		charmChargesUsedInc = inc("CharmChargesUsed"),
		manaRegenOnly = manaRegenOnly,
		lifeLossPerUse = lifeLossPerUse, mainRate = output.Speed or 0,
		flaskNotConsume = base("FlaskChanceNotConsumeCharges"),
	}
end

-- The Shock magnitude of your Shocked Ground (MECH45, FIX_QUEUE; the user's expectation, 11 Nov 2026): PoB takes its
-- "Effect of Shock" box raw (ShockVal, capped at MaximumShock), so the conditions scale the ground's base Shock by the main
-- skill's Shock-magnitude modifiers (global ones plus the skill's own: KeywordPopups BuffMagnitude, "come from whoever
-- applies it"), as PoB itself does for its own ground skills (CalcPerform's "Shock Ground"). Left out: Overcharge (it
-- makes the skill's Shocks from hits stronger) and crit-only lines (their CriticalStrike condition isn't met here).
function bridge.shockMagnitude()
	local env = build.calcsTab.mainEnv
	local skill = env.player.mainSkill
	local inc, more, left = 0, 1, { }
	if skill then
		-- not a Critical Hit: PoB's calcs can leave the skill's CriticalStrike condition met (a crit-only line counted, a
		-- test caught it), so it's forced off in a copy of the skill's config
		local cfg = copyTable(skill.skillCfg or { }, true)
		cfg.skillCond = setmetatable({ CriticalStrike = false }, { __index = (skill.skillCfg or { }).skillCond })
		cfg.overrideCond = setmetatable({ CriticalStrike = false }, { __index = (skill.skillCfg or { }).overrideCond })
		for _, kind in ipairs({ "INC", "MORE" }) do
			for _, row in ipairs(skill.skillModList:Tabulate(kind, cfg, "EnemyShockMagnitude", "AilmentMagnitude")) do
				local source = tostring(row.mod.source or "")
				if source:match("Overcharge") then
					table.insert(left, source)
				elseif kind == "INC" then
					inc = inc + row.value
				else
					more = more * (1 + row.value / 100)
				end
			end
		end
	end
	local max = env.modDB:Override(nil, "ShockMax")
	if not max then  -- as CalcPerform: the highest over the active skills
		max = 0
		for _, active in ipairs(env.player.activeSkillList) do
			max = math.max(max, data.nonDamagingAilment.Shock.max + active.baseSkillModList:Sum("BASE", nil, "ShockMax"))
		end
	end
	return {
		inc = inc, more = more, enemy = calcLib.mod(env.enemyDB, nil, "SelfShockMagnitude", "AilmentMagnitude"),
		max = max, left = left,
	}
end

-- The build's Flammability Magnitude modifiers for an Ignite that doesn't Hit (MECH80: Radiant Grief's aura, your
-- Ignited Ground; conditions.as_though_flammability). PoB parses "Flammability Magnitude" as EnemyIgniteChance
-- (ModParser 789) and scales a hit's Flammability by INC / MORE of EnemyIgniteChance and AilmentChance and the enemy's
-- SelfIgniteChance (CalcOffence 5619-5621). The aura or ground isn't the main skill, so only the player's global mods
-- are read (env.player.modDB, no skill cfg): a skill's own lines (Incendiary Shot's "more Flammability") are left out.
function bridge.flammabilityMagnitude()
	local env = build.calcsTab.mainEnv
	local db = env.player.modDB
	local inc = db:Sum("INC", nil, "EnemyIgniteChance", "AilmentChance") + env.enemyDB:Sum("INC", nil, "SelfIgniteChance")
	local more = db:More(nil, "EnemyIgniteChance", "AilmentChance") * env.enemyDB:More(nil, "SelfIgniteChance")
	return { inc = inc, more = more }
end

-- The curses you put on the boss (MECH84; conditions.reflected_curse_mods: "Curses you inflict are reflected back to
-- you", Shackles of the Wretched / a hand-wraps line, and the Atziri's Allure support): each player curse (not a Mark)
-- in PoB's curse slots (env.curseSlots, CalcPerform 3073: the ones the curse limit lets onto the boss), with its skill's
-- grantedEffect id and level (for PoB's ExtraCurse applyToPlayer path, CalcPerform 2957-2987) and its own curse-effect
-- modifiers as CalcPerform 2503-2507 reads them for the boss, without the boss's CurseEffectOnSelf: inc (INC CurseEffect,
-- plus AuraEffect for an aura curse) and more (MORE CurseEffect x the skill's Magnitude); atziri: Atziri's Allure
-- supports it.
function bridge.cursesInflicted()
	local env = build.calcsTab.mainEnv
	local out = { }
	local seen = { }
	for _, slot in ipairs(env.curseSlots or { }) do
		if slot.fromPlayer and not slot.isMark and slot.name and not seen[slot.name] then
			for _, skill in ipairs(env.player.activeSkillList) do
				local found = false
				for _, buff in ipairs(skill.buffList or { }) do
					if buff.type == "Curse" and buff.name == slot.name then
						found = true
					end
				end
				if found and not seen[slot.name] then
					seen[slot.name] = true
					local mods, cfg = skill.skillModList, skill.skillCfg
					local inc = mods:Sum("INC", cfg, "CurseEffect")
					if skill.skillTypes[SkillType.Aura] then
						inc = inc + mods:Sum("INC", cfg, "AuraEffect")
					end
					local more = mods:More(cfg, "CurseEffect") * calcLib.mod(mods, cfg, "Magnitude")
					local atziri = false
					for _, support in ipairs(skill.supportList or { }) do
						if support.grantedEffect and support.grantedEffect.id == "SupportAtzirisAllurePlayer" then
							atziri = true
						end
					end
					local effect = skill.activeEffect
					out[#out + 1] = {
						name = effect.grantedEffect.name or slot.name, skillId = effect.grantedEffect.id, level = effect.level or 1,
						inc = jsonNumber(inc), more = jsonNumber(more), atziri = atziri,
					}
				end
			end
		end
	end
	return { curses = out }
end

-- (MECH88; SEARCH27's ReplayMismatch flake, the manager 11:35) PoB's Item:BuildRaw writes an item's base-variant lines by
-- iterating pairs(self.baseLines) (Classes/Item.lua 2034 / 2064), so their order changes between processes and recorded
-- answers go stale. The bridge's copy sorts each run of consecutive base-variant lines (a line naming one of the item's
-- baseLines after its "{...}" prefixes); item.raw itself, what PoB parses, is left as it is.
local function steadyRaw(item)
	local raw = item.raw
	if type(raw) ~= "string" or not next(item.baseLines or { }) then
		return raw
	end
	local lines, run, out = { }, { }, { }
	for text in (raw .. "\n"):gmatch("(.-)\r?\n") do
		lines[#lines + 1] = text
	end
	local function flush()
		table.sort(run)
		for _, text in ipairs(run) do
			out[#out + 1] = text
		end
		run = { }
	end
	for _, text in ipairs(lines) do
		local bare = text:gsub("^%b{}", ""):gsub("^%b{}", ""):gsub("^%b{}", "")
		if item.baseLines[bare] and bare ~= text then
			run[#run + 1] = text
		else
			flush()
			out[#out + 1] = text
		end
	end
	flush()
	if out[#out] == "" and raw:sub(-1) ~= "\n" then
		out[#out] = nil
	end
	return table.concat(out, "\n")
end

function bridge.equipped()
	local slots = { }
	for name, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if item then
			slots[name] = steadyRaw(item)
		end
	end
	return { slots = slots }
end

function bridge_dispatch(method, jsonArgs)
	local fn = bridge[method]
	if not fn then
		return false, "unknown bridge method " .. tostring(method)
	end
	local args = dkjson.decode(jsonArgs) or { }
	installRadiusModel()
	local ok, result = xpcall(fn, debug.traceback, args)
	if not ok then
		return false, tostring(result)
	end
	return true, dkjson.encode(result or { })
end

-- (PERF18) PoB's second calculation pass, put off until an export needs it.
-- CalcsTab:BuildOutput (Classes/CalcsTab.lua 484-487) calculates the build twice on every recalculation: "MAIN",
-- then "CALCS" (the Calcs tab's own skill choices). Headless, the second pass's result is read by Build:Save alone
-- (Modules/Build.lua 1221-1223: the saved code's BuffList / CombatList / CurseList), but on its way the pass also
-- writes state that lasts: its setup (calcs.initEnv "CALCS") clamps the Calcs tab's selection fields, which the
-- code saves, and the rest writes a reserving Spectre's / Companion's names from the Calcs tab's minion
-- (Modules/CalcDefence.lua 205-216), which this bridge and the next pass read.
-- BRIDGE_CALCS_PASS = "late" (POE2_CALCS_PASS, builder/pob/engine.py): the setup still runs at once; the rest runs
-- when Build:Save is next called, before it reads the result. The guard: whenever a gem's or group's Calcs-tab
-- choice differs from its main one after the setup, the whole pass runs at once, as before. "now": PoB untouched.
BRIDGE_CALCS_PASS = BRIDGE_CALCS_PASS or "now"
BRIDGE_CALCS_GUARD = true -- (false only in the probe that shows the check catches an unguarded Spectre)
BRIDGE_CALCS_COUNT = { late = 0, guarded = 0, settled = 0, why = { } } -- (why: which choice made the guard run the pass)
do
	local owed = setmetatable({ }, { __mode = "k" })
	local GEM_CHOICES = { { "skillPart", "skillPartCalcs" }, { "skillMinion", "skillMinionCalcs" },
		{ "skillMinionItemSet", "skillMinionItemSetCalcs" }, { "skillMinionSkill", "skillMinionSkillCalcs" },
		{ "statSet", "statSetCalcs" }, { "skillMineCount", "skillMineCountCalcs" },
		{ "skillStageCount", "skillStageCountCalcs" },
		{ "skillMinionSkillStatSetIndexLookup", "skillMinionSkillStatSetIndexLookupCalcs" } }
	local function same(a, b) -- equal values, or tables equal key by key (a gem's per-skill lookups)
		if a == b then
			return true
		end
		if a == nil and type(b) == "table" then -- (an unset lookup reads as an empty one)
			a = { }
		elseif b == nil and type(a) == "table" then
			b = { }
		end
		if type(a) ~= "table" or type(b) ~= "table" then
			return false
		end
		for key, value in pairs(a) do
			if not same(value, b[key]) then
				return false
			end
		end
		for key in pairs(b) do
			if a[key] == nil then
				return false
			end
		end
		return true
	end
	-- Which Calcs-tab choice differs from its main twin (nil: none). The pass reads a group's mainActiveSkillCalcs
	-- for one group alone, the Calcs tab's own (index == env.mainSocketGroup, CalcSetup.lua 2390-2396; checked:
	-- every "mainActiveSkillCalcs" in Modules/Calc*.lua: those lines and 803 / 809, the granted groups' kept state,
	-- read in every mode's setup alike), and reads an unset index as 1 (2394 / 2397); a gem's fields it reads for
	-- every skill (statSetCalcs: CalcPerform.lua 3396; mine and stage counts: CalcActiveSkill.lua 869 / 892).
	local function choicesDiffer(b)
		local list = b.skillsTab.socketGroupList
		local own = list[b.calcsTab.input.skill_number]
		if own and (own.mainActiveSkill or 1) ~= (own.mainActiveSkillCalcs or 1) then
			return "mainActiveSkill"
		end
		for _, group in ipairs(list) do
			for _, gem in ipairs(group.gemList) do
				for _, pair in ipairs(GEM_CHOICES) do
					if not same(gem[pair[1]], gem[pair[2]]) then
						return pair[1] .. (gem[pair[2]] == nil and " (Calcs unset)" or gem[pair[1]] == nil and " (main unset)" or "")
					end
				end
			end
		end
		return nil
	end
	local function settle(tab)
		if tab and owed[tab] then
			owed[tab] = nil
			tab.calcsEnv = tab.calcs.buildOutput(tab.build, "CALCS")
			tab.calcsOutput = tab.calcsEnv.player.output
			BRIDGE_CALCS_COUNT.settled = BRIDGE_CALCS_COUNT.settled + 1
		end
	end
	local class = common.classes.CalcsTab
	local realBuildOutput = class.BuildOutput
	class.BuildOutput = function(self, ...)
		if BRIDGE_CALCS_PASS ~= "late" then
			owed[self] = nil
			return realBuildOutput(self, ...)
		end
		local owner = self.build
		if not rawget(owner, "calcsPassSave") then
			local realSave = owner.Save
			owner.calcsPassSave = true
			owner.Save = function(saved, xml)
				settle(saved.calcsTab)
				return realSave(saved, xml)
			end
		end
		local calcs = self.calcs
		local real = calcs.buildOutput
		local main
		calcs.buildOutput = function(b, mode)
			if mode ~= "CALCS" or not main then
				local env = real(b, mode)
				if mode == "MAIN" then
					main = env
				end
				return env
			end
			calcs.initEnv(b, "CALCS")
			local why = BRIDGE_CALCS_GUARD and choicesDiffer(b)
			if why then
				owed[self] = nil
				BRIDGE_CALCS_COUNT.guarded = BRIDGE_CALCS_COUNT.guarded + 1
				BRIDGE_CALCS_COUNT.why[why] = (BRIDGE_CALCS_COUNT.why[why] or 0) + 1
				return real(b, mode)
			end
			owed[self] = true
			BRIDGE_CALCS_COUNT.late = BRIDGE_CALCS_COUNT.late + 1
			return main
		end
		local ok, err = xpcall(function(...) return realBuildOutput(self, ...) end, debug.traceback, ...)
		calcs.buildOutput = real
		if not ok then
			error(err, 0)
		end
	end
end
