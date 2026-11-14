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
		if type(value) == "number" then
			picked[field] = jsonNumber(value)
		elseif type(value) == "string" or type(value) == "boolean" then
			picked[field] = value
		elseif type(value) == "table" and field == "SkillDPS" then
			-- Full DPS broken down by skill (and best ailment, with the skill it came from)
			local skills = { }
			for _, entry in ipairs(value) do
				skills[#skills + 1] = { name = entry.name, dps = jsonNumber(entry.dps or 0), count = entry.count or 1,
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
	return math.floor(DERIVED_UNIT * on / (on + off) + 0.5)
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
	local every = a.times * t <= a.window and a.times * t or a.window + (1 - a.window / (a.times * t)) * t
	return math.floor(DERIVED_UNIT * math.min(1, (1 / every) / speed) + 0.5)
end

-- Harmony Within (models.harmony_model): on while unreserved Mana is above Life
function DERIVED.harmony(out)
	return num(out.ManaUnreserved) > num(out.Life) and 1 or 0
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
local ADDED_DPS = { maelstrom = true, added = true }
function DERIVED.added(out, a)
	local base = a.field and num(out[a.field]) * (a.factor or 1) or (a.fixed or 0)
	return math.floor(base * 100 + 0.5)
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
local function setAugmentCounts(env)
	local modDB = env and env.modDB
	if not modDB or not env.player then
		return
	end
	local idols, others = 0, 0
	local runes = data.itemMods and data.itemMods.Runes or { }
	for _, item in pairs(env.player.itemList or { }) do
		if type(item) == "table" and item.runes then
			for i = 1, item.itemSocketCount or 0 do
				local name = item.runes[i]
				if name and name ~= "None" then
					local isIdol = false
					for _, entry in pairs(runes[name] or { }) do
						if type(entry) == "table" and entry.type == "Idol" then
							isIdol = true
						end
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
					local inVariant = not item.variant or not modLine.variantList or modLine.variantList[item.variant]
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
-- Whether Temporal Chains' slow takes both the curse halving (CurseEffectOnSelf) and this one is UNVERIFIED; both apply
-- here, as the game's text gives each without an exception.
-- Freeze (MECH55, MECHD3's note): PoB's Freeze row is a steady -70% action speed (CalcPerform 767-770) whenever the
-- Frozen box is on, so the boss hit at 0.3x its rate all fight whatever its Frozen share. The game's Freeze is a stop
-- (KeywordPopups "Freeze": a Frozen target can't act) for the share of the fight it lasts: with the share the conditions
-- resolver writes (config input mechFreezeShare, conditions.FREEZE_SHARE_INPUT: the Frozen box's uptime) the speed is
-- x (1 - share), the fight average (an UNDERCOUNT of the boss's hits below a 70% share, an overcount above it, before).
-- Without it, PoB's row as before.
local FREEZE_SHARE_INPUT = "mechFreezeShare"
local bossSlowInstalled = false
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
				mult = mult * math.max(0, 1 + value * potency / 100)
			end
		end
		if chill < 0 and not unaffected then
			mult = mult * math.max(0, 1 + chill * potency / 100)
		end
		local chains = modDB:Sum("INC", nil, "TemporalChainsActionSpeed")
		if chains >= 0 then
			positive = positive + chains
		elseif not unaffected then
			mult = mult * math.max(0, 1 + math.max(-data.misc.TemporalChainsEffectCap, chains) * potency / 100)
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
	local passives = math.max(build.characterLevel - 1, 0) + build.acts[build.maxActs].questPoints
	local setPoints = (build.maxWeaponSets or 0) + (output.PassivePointsToWeaponSetPoints or 0)
	local over = math.max(used - math.min(set1, set2) - passives - (output.ExtraPoints or 0), 0)
	over = over + math.max(set1 - setPoints, 0) + math.max(set2 - setPoints, 0)
	return over, math.max(ascUsed - 8, 0)
end

-- PassivePointsOver on every calculation's output (objective.py: a hard deficit)
local function setPointsOver(env)
	local output = env and env.player and env.player.output
	if output then
		local over, overAscendancy = pointsOver(env.allocNodes or build.spec.allocNodes, output, env.grantedPassives)
		output.PassivePointsOver = over + overAscendancy
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
					local list, extra = modLib.parseMod(rest)
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
						local tagged = { }
						for _, parsed in ipairs(list) do
							local copy = copyTable(parsed)
							if type(copy.value) == "number" then
								copy.value = copy.value / 100
							elseif type(copy.value) == "table" and type(copy.value.mod) == "table"
									and type(copy.value.mod.value) == "number" then
								copy.value.mod.value = copy.value.mod.value / 100  -- (a LIST of one nested mod)
							end
							copy[#copy + 1] = { type = "Multiplier", var = "LedgerShare" .. share }
							tagged[#tagged + 1] = copy
						end
						modLine.modList, modLine.extra, modLine.ledgerShare = tagged, nil, share
					end
				end
			end
		end
		return buildModList(self, ...)
	end
	ledgerShareInstalled = true
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
-- OPEN (MECHB17), a possible OVERCOUNT: 7 of these 34 forty-level effects look structurally different. GrantedEffects'
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

local function installDerived()
	installLedgerBonded()
	installLedgerShare()
	installSpiritVessel()
	installCannotChill()
	installBossSlow()
	installNoCritReroll()
	installGrantedLevel()
	local calcs = require("Modules.CalcBase")
	if calcs.perform == wrappedPerform or not calcs.perform then
		return
	end
	local perform = calcs.perform
	wrappedPerform = function(env, ...)
		setDerived(env)
		setAugmentCounts(env)
		setNoCritReroll(env)
		local function finish(...)
			setLowLifeCap(env, env and env.player and env.player.output)
			setLifeRecharge(env)
			setManaRegenOnly(env)
			markShowAverage(env)
			setChillDamage(env)
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

-- Every allocated node must be reachable under the game's rules: through allocated nodes from the
-- class start (or the ascendancy start, or a class start a jewel adds), or be a node a socketed
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
			if other.alloc and not connected[other.id] and other.ascendancyName == node.ascendancyName then
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

-- Installed before every bridge call: PoB defines calcs.initEnv again whenever it loads its
-- calculation modules (a new build's CalcsTab)
local wrappedInitEnv
installRadiusModel = function()
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
		-- Put PoB's own node sets back whatever happens, and pass every result through
		local function restore(ok, ...)
			for i = #swapped, 1, -1 do
				swapped[i][1][swapped[i][2]] = swapped[i][3]
			end
			if not ok then
				error((...), 0)
			end
			return ...
		end
		return restore(pcall(initEnv, buildArg, mode, override, specEnv))
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
-- `active` switches a flask or charm slot on or off.
function bridge.equip(args)
	local slot, old = slotItem(args.slot)
	if args.raw then
		local item = makeItem(args.raw)
		build.itemsTab:AddItem(item, true)
		slot:SetSelItemId(item.id)
	else
		slot:SetSelItemId(0)
	end
	if args.active ~= nil then
		setSlotActive(args.slot, args.active and args.raw ~= nil)
	end
	forgetItem(old)
	local start = os.clock()
	recalc()
	return { seconds = os.clock() - start, stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Skill gems -----------------------------------------------------------------------------------

local gemsByName
local function gemByName(name)
	if not gemsByName then
		gemsByName = { }
		for _, gemData in pairs(build.data.gems) do
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
		}
	end
	local skills = { }
	for _, activeSkill in ipairs(group.displaySkillList or { }) do
		local effect = activeSkill.activeEffect
		local parts = { }
		for _, part in ipairs(effect.grantedEffect.parts or { }) do
			parts[#parts + 1] = part.name
		end
		local statSets = { }
		for _, statSet in ipairs(effect.grantedEffect.statSets or { }) do
			statSets[#statSets + 1] = statSet.label or ""
		end
		skills[#skills + 1] = { name = effect.grantedEffect.name, parts = parts, statSets = statSets,
			part = effect.srcInstance and effect.srcInstance.skillPart }
	end
	return {
		index = index, label = group.label, enabled = group.enabled ~= false, main = index == build.mainSocketGroup,
		fullDPS = group.includeInFullDPS and true or false, fromItem = group.source ~= nil,
		mainActiveSkill = group.mainActiveSkill or 1, gems = gems, skills = skills,
		set1 = group.set1 ~= false, set2 = group.set2 ~= false,
	}
end

function bridge.groups()
	local groups = { }
	for index, group in ipairs(build.skillsTab.socketGroupList) do
		groups[#groups + 1] = describeGroup(index, group)
	end
	return { groups = groups, mainSocketGroup = build.mainSocketGroup }
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
			local instance = gemInstance(gemByName(spec.name), spec.level, spec.quality)
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
	recalc()
	if args.skillPart or args.statSet then
		local activeSkill = group.displaySkillList and group.displaySkillList[group.mainActiveSkill or 1]
		local instance = activeSkill and activeSkill.activeEffect.srcInstance
		if instance then
			if args.skillPart then
				instance.skillPart = args.skillPart
				instance.skillPartCalcs = args.skillPart
			end
			if args.statSet then
				-- Which of the skill's stat sets PoB calculates (e.g. the Fire, Cold or Lightning storm)
				local id = activeSkill.activeEffect.grantedEffect.id
				instance.statSet = instance.statSet or { }
				instance.statSetCalcs = instance.statSetCalcs or { }
				instance.statSet[id] = args.statSet
				instance.statSetCalcs[id] = args.statSet
			end
			recalc()
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
	local fast = { nodeAlloc = true, requirementsItems = true, skipEHP = args.skipEHP ~= false }
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

-- Set config options (PoB's Configuration tab), e.g. { usePowerCharges = true }. A value of
-- false or "nil" clears the option back to its default.
function bridge.setConfig(args)
	local configTab = build.configTab
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
	enemySpeed = 510, cullingStrikeUniqueThreshold = 4.6086, enemyPhysicalDamage = 1223, enemyFireDamage = 4138,
	enemyColdDamage = 4892, enemyLightningDamage = 4892, enemyChaosDamage = 2069 }
-- PoB's own Unique Culling Strike threshold (Data/Misc.lua: 5%). The boss's `cullingStrikeUniqueThreshold` (MECHD4,
-- bosses.cull_threshold: 5% x Life / (Life + Energy Shield), so CullMultiplier is the gain over the whole pool) replaces
-- it in data.gameConstants, which CalcOffence reads (4071 / 4075); every resetConfig puts PoB's back first.
local POB_CULL_UNIQUE  -- (read at the first resetConfig, once PoB's data is loaded)
-- The Arbiter of Ash's hits (MECHC6): must equal boss_hits.config_vars(boss_hits.DEFAULT_BOSS) (tests/test_models.py)
local BOSS_HITS_DEFAULT = { enemyPhysicalDamage = 1034, enemyFireDamage = 4138, enemyColdDamage = 0,
	enemyLightningDamage = 0, enemyChaosDamage = 0 }

-- A loaded code's configuration isn't evidence (PLAN.md §5.4): the active config set goes back
-- to what a new build has (PoB's own new-set defaults, the boss preset included), with no custom
-- modifiers, and the boss's resistances (args.boss, else BOSS_DEFAULT). Only the campaign quest
-- choices stay; they're free picks, not conditions.
function bridge.resetConfig(args)
	local configTab = build.configTab
	local id = configTab.activeConfigSetId
	local old = configTab.configSets[id]
	local fresh = configTab:CreateConfigSet(id, old.title)
	for var, value in pairs(old.input) do
		if var:match("^quest") then
			fresh.input[var] = value
		end
	end
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
		modLib.setSource(copy, "Assumption")
		mods[#mods + 1] = copy
	end
	return mods
end

local function installAssumptions()
	local configTab = build.configTab
	if not rawget(configTab, "bridgeWrapped") then
		local original = configTab.BuildModList
		configTab.BuildModList = function(self, ...)
			local results = { original(self, ...) }
			for _, mod in ipairs(assumedMods) do
				local tags = mod.skill and { { type = "SkillName", skillName = mod.skill } } or { }
				if mod.kind == "SkillData" then
					self.modList:NewMod("SkillData", "LIST", { key = mod.key, value = mod.value }, "Assumption", unpack(tags))
				elseif mod.kind == "Condition" then  -- a ledger condition the build's skills switch on
					self.modList:NewMod("Condition:Ledger" .. mod.name, "FLAG", true, "Ledger")
				elseif mod.kind == "Raw" then  -- a PoB modifier by name (models.raw_mod), e.g. a skill's DebuffEffect MORE
					-- (mod.tag: a sub-source, "Assumption:<tag>", that a bridge read can leave out: models.TOTEM_ONE_TAG)
					self.modList:NewMod(mod.name, mod.type, mod.value, "Assumption" .. (mod.tag and (":" .. mod.tag) or ""),
						unpack(tags))
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
		local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
		if group and active and active > 0 then
			group.mainActiveSkill = savedActive
		end
		local skill = ok and env and env.player.mainSkill
		if skill and pcall(calcs.perform, env, true) then
			local output = env.player.output
			rates[#rates + 1] = {
				index = index, name = calcs.getActiveSkillDisplayName(skill), active = active,
				-- (MECHB10) a DetonatesAfterTime skill's fuse, PoB's DetonationTime (base x inc x more)
				detonationTime = output.DetonationTime and jsonNumber(output.DetonationTime) or nil,
				speed = jsonNumber(output.Speed or 0), cooldown = output.Cooldown and jsonNumber(output.Cooldown) or nil,
				repeats = jsonNumber(output.Repeats or 1),
				-- (MECH57) its minion's counted skill's uses per second, PoB's minion Speed (cooldown-capped, CalcOffence 3008)
				minionSpeed = env.minion and env.minion.output and type(env.minion.output.Speed) == "number"
					and jsonNumber(env.minion.output.Speed) or nil,
				duration = output.Duration and jsonNumber(output.Duration) or nil,
				castTime = jsonNumber(skill.activeEffect.grantedEffect.castTime or 0),
				-- one use's time before the cooldown caps Speed (PoB's own ailment stacks read it the same way)
				time = (output.HitTime or output.Time) and jsonNumber(output.HitTime or output.Time) or nil,
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
				local warcryName = entry.name:gsub(" Cry", ""):gsub("'s", ""):gsub(" ", "")
				local count = env.player.modDB:Sum("BASE", nil, "Num" .. warcryName .. "Empowers")
				entry.warcryEmpowers = count > 0 and jsonNumber(count) or nil
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
				local okStats, stats = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect, skill.activeEffect.grantedEffect, set)
				local base = okStats and stats and stats["number_of_poisonbloom_arrow_bloom_allowed"]
				if base and base > 0 then
					entry.pustuleLimit = jsonNumber(base + skill.skillModList:Sum("BASE", skill.skillCfg, "AdditionalCooldownUses"))
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
			-- (MECHB14: IgniteDuration, how long an Ignite of the group lasts: Frostflame Nova's ice per cast, models.frostflame_plan)
			for _, field in ipairs({ "HeavyStunBuildupAvg", "HitChance", "CritChance", "IgniteDuration" }) do
				if type(output[field]) == "number" then
					entry[field] = jsonNumber(output[field])
				end
			end
			for _, kind in ipairs({ "Physical", "Fire", "Cold", "Lightning", "Chaos" }) do
				for _, part in ipairs({ "Hit", "Crit" }) do
					local field = kind .. part .. "Average"
					if type(output[field]) == "number" then
						entry[field] = jsonNumber(output[field])
					end
				end
			end
		end
	end
	build.mainSocketGroup = saved
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
	build.mainSocketGroup, group.mainActiveSkill, instance.skillMinionSkill = args.index, position, found
	local result = { reason = "PoB calculated no minion" }  -- (never an empty table: it would reach Python as a list)
	local ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
	-- (env.minion exists only once perform has run: useRates reads it the same way)
	if ok and env and pcall(calcs.perform, env, true) and env.minion then
		result.reason = nil
		local output = env.minion.output or { }
		for _, field in ipairs({ "AverageHit", "AverageDamage", "Speed", "Cooldown", "TotalDPS" }) do
			if type(output[field]) == "number" then
				result[field] = jsonNumber(output[field])
			end
		end
		-- (an attack's AverageHit is per hand: CalcOffence 4721-4728 combines only AverageDamage into the output)
		local hand = output.MainHand or output.OffHand
		if result.AverageHit == nil and hand and type(hand.AverageHit) == "number" then
			result.AverageHit = jsonNumber(hand.AverageHit)
		end
		result.name = env.minion.mainSkill and env.minion.mainSkill.activeEffect.grantedEffect.name or nil
	end
	-- the minion's own counted skill's TotalDPS (what it loses while it performs the other skill)
	instance.skillMinionSkill = oldSkill
	ok, env = pcall(calcs.initEnv, build, "CALCULATOR")
	if ok and env and pcall(calcs.perform, env, true) and env.minion then
		local output = env.minion.output or { }
		result.baseDPS = type(output.TotalDPS) == "number" and jsonNumber(output.TotalDPS) or nil
	end
	build.mainSocketGroup, group.mainActiveSkill, instance.skillMinionSkill = saved, oldActive, oldSkill
	return result
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
			for _, field in ipairs({ "TotalDPS", "AverageDamage", "Speed" }) do
				if type(output[field]) == "number" then
					entry[field] = jsonNumber(output[field])
				end
			end
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

-- Active skills of the current build with what an assumption needs about them: base cooldown,
-- cooldown recovery, and whether they generate Remnants. `levelStats = {names}` adds those skills'
-- stats at their current level and quality (e.g. Iron Ward's store, which PoB doesn't read).
-- (see bridge.activeSkills: support stats the models always get)
local modelSupportStats = {
	["number_of_totems_allowed_is_1"] = true,
	["support_splinter_totem_X_times"] = true,
	["support_minion_pact_user_life_%_removed_from_nearby_minion"] = true,
	["support_minion_pact_damage_+%_final"] = true,
	-- (MECH63) Eternal Mark's "Marks are not Consumed the first time they Activate" (models.mark_model)
	["marks_avoid_consumption_when_first_activated"] = true,
}

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
		skills[#skills + 1] = {
			name = effect.name,
			cooldown = activeSkill.skillData.cooldown,
			cooldownRecovery = calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "CooldownRecovery"),
			generatesRemnants = types[SkillType.GeneratesRemnants] and true or false,
			-- it consumes or targets corpses (the game's skill types; models.corpse_model)
			corpses = (types[SkillType.TargetsDestructibleCorpses] or types[SkillType.TargetsDestructibleRareCorpses]
				or types[SkillType.CanTargetUnusableCorpse]) and true or nil,
			triggered = types[SkillType.Triggered] and true or false,
			-- (MECHB12, AUDIT25 A25-41) a curse's own duration with the build's modifiers ("curse duration" is Duration
			-- with the Curse keyword, ModParser 483): base x Duration / PrimaryDuration, as PoB builds a skill's duration
			curseDuration = types[SkillType.AppliesCurse] and activeSkill.skillData.duration and jsonNumber(
				((activeSkill.skillData.duration or 0) + activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "Duration", "PrimaryDuration"))
				* calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "Duration", "PrimaryDuration")) or nil,
			-- (MECH62, AUDIT27 A27-2) a Mark's own duration with the build's modifiers ("Mark Skills have N% increased
			-- Skill Effect Duration": Duration on SkillType Mark, ModCache; mark_skill_duration_+% on jewels, passives and
			-- Marksman influence), built as curseDuration; models.mark_duration
			markDuration = types[SkillType.Mark] and activeSkill.skillData.duration and jsonNumber(
				((activeSkill.skillData.duration or 0) + activeSkill.skillModList:Sum("BASE", activeSkill.skillCfg, "Duration", "PrimaryDuration"))
				* calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "Duration", "PrimaryDuration")) or nil,
			-- the ailments it consumes by its skill types alone (Snap, Elemental Sundering, Snipe, Shockchain Arrow,
			-- Icestorm carry no consume stat; conditions.main_consumes / side_consumes, MECH55)
			consumes = (types[SkillType.SkillConsumesFreeze] or types[SkillType.SkillConsumesShock]
				or types[SkillType.SkillConsumesIgnite]) and {
					Freeze = types[SkillType.SkillConsumesFreeze] and true or nil,
					Shock = types[SkillType.SkillConsumesShock] and true or nil,
					Ignite = types[SkillType.SkillConsumesIgnite] and true or nil,
				} or nil,
			-- the ailments its minion's counted skill (PoB's minion.mainSkill, the one Full DPS counts) consumes by its
			-- skill types: the Skeletal Brute's Shattering Roar consumes Freeze (MECH57, AUDIT25's small overcount;
			-- conditions.main_consumes / side_consumes read it at PoB's minion Speed, useRates' minionSpeed)
			minionConsumes = minionConsumes(activeSkill),
			-- it summons minions (assumptions.NEEDS_MINIONS: Grim Feast's Remnants come from their deaths)
			minions = types[SkillType.CreatesMinion] and true or nil,
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
				if life <= 0 and mana <= 0 then
					return nil
				end
				return { Life = life > 0 and jsonNumber(math.floor(life * effectMod)) or nil,
					Mana = mana > 0 and jsonNumber(math.floor(mana * effectMod)) or nil }
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
			local ok, limit = pcall(function()
				local list, cfg = activeSkill.skillModList, activeSkill.skillCfg
				return list:Override(cfg, "ActiveTotemLimit", "ActiveBallistaLimit") or totemLimitBase(list, cfg)
			end)
			skills[#skills].totemLimit = ok and limit or nil
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
							sets and sets[1])
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
				local ok, built = pcall(calcLib.buildSkillInstanceStats, activeSkill.activeEffect, effect, set)
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
					local ok, built = pcall(calcLib.buildSkillInstanceStats, support, granted, granted.statSets[1])
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
					local built = calcLib.buildSkillInstanceStats(activeSkill.activeEffect, effect, set)
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
		if tag.actor and tag.var then
			value = tag.actor .. ":" .. tag.var
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
	local names = { }
	for _, gem in pairs(build.data.gems) do
		local effect = gem.grantedEffect
		if effect and not effect.support and not names[gem.name] then
			names[gem.name] = true
		end
	end
	local sorted = { }
	for name in pairs(names) do
		sorted[#sorted + 1] = name
	end
	table.sort(sorted)
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
	for _, name in ipairs(sorted) do
		local effect = gemByName(name).grantedEffect
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
		local ok, result = pcall(bridge.setGroup, { index = index, gems = { { name = name, level = 20, quality = 0 } }, main = true,
			fields = fields })
		if ok then
			local minion = build.calcsTab.mainOutput and build.calcsTab.mainOutput.Minion
			result.stats.MinionDPS = minion and (minion.CombinedDPS or minion.TotalDPS) or nil
			result.stats.weapon = weapon and weapon.name or nil
			index = 1
			local types = { }
			local effect = gemByName(name).grantedEffect
			for skillType in pairs(effect.skillTypes or { }) do
				for typeName, value in pairs(SkillType) do
					if value == skillType then
						types[#types + 1] = typeName
					end
				end
			end
			out[#out + 1] = { name = name, stats = result.stats, types = types }
		else
			out[#out + 1] = { name = name, error = tostring(result) }
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
			local tags = { }
			for _, tag in ipairs(mod) do
				tags[#tags + 1] = tostring(tag.type) .. (tag.var and (":" .. tostring(tag.var)) or "")
					.. (tag.skillType and (":" .. tostring(tag.skillType)) or "")
			end
			parsed[#parsed + 1] = { name = mod.name, type = mod.type,
				value = type(mod.value) == "table" and (mod.value.mod and mod.value.mod.name or "table") or mod.value,
				flags = mod.flags, keywordFlags = mod.keywordFlags, tags = tags }
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
							copy[#copy + 1] = { type = "Multiplier", var = "LedgerShare" .. share }
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
		if item and not slot.inactive then
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
	local lines = { }
	local function add(source, text)
		if type(text) == "string" and text ~= "" then
			lines[#lines + 1] = { source = source, text = text }
		end
	end
	for _, node in pairs(build.spec.allocNodes) do
		for _, line in ipairs(node.sd or { }) do
			add("passive:" .. (node.dn or node.id), line)
		end
	end
	for slotName, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		-- a flask or charm slot the search switched off (flasks.ACTIVE_UPTIME) gives no lines either (MECHC4; COVD5: before,
		-- The Fall of the Axe's "Grants Onslaught during effect" reached the Onslaught model from a switched-off charm and
		-- counted full-time): PoB applies such an item's mods only while its slot is active
		local flaskOff = (slotName:match("^Flask") or slotName:match("^Charm")) and not slot.active
		if item and not slot.inactive and not flaskOff then
			for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					-- A unique's line can hold its range, "(10-16)% increased ...": the models' and conditions' regexes
					-- read numbers, so give them the rolled line, as PoB's own getRangedModList does (Item.lua 2726-2735;
					-- COVD1: nearly every custom model missed ranged unique lines)
					local text = modLine.line
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
	return { lines = lines }
end

-- Passive points available at the build's level, as PoB counts them (level - 1 plus every
-- campaign quest point; weapon-set points are separate)
function bridge.pointBudget()
	local questPoints = build.acts[build.maxActs].questPoints
	local used, ascUsed = countAllocated()
	return {
		passives = math.max(build.characterLevel - 1, 0) + questPoints, ascendancy = 8,
		used = used, ascendancyUsed = ascUsed, questPoints = questPoints,
	}
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
	for id, gem in pairs(build.data.gems) do
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
	table.sort(gems, function(a, b) return a.name < b.name end)
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
		if item and not slot.inactive then
			items[#items + 1] = {
				slot = slotName, name = item.title or item.name, base = item.baseName, type = item.type,
				rarity = item.rarity, runes = plain(item.runes, 2) or { },
			}
		end
	end
	table.sort(items, function(a, b) return a.slot < b.slot end)
	local warnings = { }
	for _, line in ipairs(build.controls.warnings and build.controls.warnings.lines or { }) do
		warnings[#warnings + 1] = tostring(line)
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
	for _, gem in pairs(build.data.gems) do
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
	for _, line in ipairs(bridge.sourceLines().lines) do
		local text = line.text:lower()
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
				local ok, built = pcall(calcLib.buildSkillInstanceStats, skill.activeEffect, effect, set)
				for stat, value in pairs(ok and built or { }) do
					if type(value) == "number" and stats[stat] == nil then
						stats[stat] = value
					end
				end
			end
			local ms = stats["breach_flame_spawn_rate_ms"] or 0
			if ms > 0 then
				blooms.rate = blooms.rate + 1000 / ms
				blooms.life = stats["ancient_gifts_life_flask_charges_restored"] or 0
				blooms.mana = stats["ancient_gifts_mana_flask_charges_restored"] or 0
				blooms.charm = stats["ancient_gifts_charm_charges_restored"] or 0
				blooms.effect = stats["remnant_effect_+%"] or 0
			end
		end
	end
	return {
		active = active, lifeFlaskBlocked = lifeFlaskBlocked, lifeFlaskUnusable = lifeFlaskUnusable,
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

function bridge.equipped()
	local slots = { }
	for name, slot in pairs(build.itemsTab.slots) do
		local item = slot.selItemId and build.itemsTab.items[slot.selItemId]
		if item then
			slots[name] = item.raw
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
