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
	local key = node.id * 4 + attribute
	if not cache[key] then
		local copy = copyTableSafe(spec.tree.nodes[node.id], false, true)
		spec:ReplaceNode(copy, copy.options[attribute])
		spec.tree:ProcessStats(copy)
		cache[key] = copy
	end
	return cache[key]
end

local function nodeSet(ids, withPaths, attribute)
	local set = { }
	for _, id in ipairs(ids or { }) do
		local node = nodeById(id)
		if withPaths and not node.alloc then
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

local function calculator()
	local calcFunc, calcBase = build.calcsTab:GetMiscCalculator()
	return calcFunc, calcBase
end

-- Full recalculation through PoB's normal path, as after any edit in the GUI
local function recalc()
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

function bridge.newBuild()
	newBuild()
	return bridge.summary()
end

function bridge.loadXml(args)
	loadBuildFromXML(args.xml, args.name or "build")
	if __mainObject__.promptMsg then
		error("PoB rejected the build: " .. tostring(__mainObject__.promptMsg))
	end
	return bridge.summary()
end

function bridge.exportXml()
	return { xml = build:SaveDB("code") }
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
		if not node.alloc and node.path and node.pathDist and node.pathDist <= limit
			and wanted and node.ascendancyName == ascendancy
			and not node.isMultipleChoiceOption then
			local path = { }
			for _, pathNode in ipairs(node.path) do
				path[#path + 1] = pathNode.id
			end
			nodes[#nodes + 1] = {
				id = id, name = node.dn, type = node.type, distance = node.pathDist,
				path = path, modKey = node.modKey,
			}
		end
	end
	table.sort(nodes, function(a, b) return a.id < b.id end)
	return { nodes = nodes }
end

-- Allocated nodes that can be removed, with the nodes that would go with them
function bridge.allocatedNodes()
	local nodes = { }
	for id, node in pairs(build.spec.allocNodes) do
		if node.type ~= "ClassStart" and node.type ~= "AscendClassStart" and not node.ascendancyName then
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
		results[i] = pickStats(calcFunc(override, useFullDPS), args.fields)
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
		results[i] = pickStats(calcFunc({ repSlotName = case.slot, repItem = item }, useFullDPS), args.fields)
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

-- Commit an item into a specific slot (or empty it when `raw` is absent), then recalculate fully
function bridge.equip(args)
	local slot, old = slotItem(args.slot)
	if args.raw then
		local item = makeItem(args.raw)
		build.itemsTab:AddItem(item, true)
		slot:SetSelItemId(item.id)
	else
		slot:SetSelItemId(0)
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
		group.gemList = { }
		for _, spec in ipairs(args.gems) do
			local instance = gemInstance(gemByName(spec.name), spec.level, spec.quality)
			instance.enabled = spec.enabled ~= false
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

-- Support gems a player can socket (not item- or tree-granted, not legacy) that PoB says can
-- support an active skill of the group
function bridge.supportCandidates(args)
	local group = socketGroup(args.group)
	local names = { }
	for _, gemData in pairs(build.data.gems) do
		local effect = gemData.grantedEffect
		if effect.support and not effect.fromItem and not effect.fromTree and not effect.legacy then
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
			local group = { label = "", enabled = true, gemList = { } }
			for _, name in ipairs(case.newGroup) do
				group.gemList[#group.gemList + 1] = gemInstance(gemByName(name), case.level, case.quality)
			end
			list[#list + 1] = group
			ok, output = pcall(run)
			table.remove(list)
		else
			local gemList = socketGroup(case.group).gemList
			local current = gemList[case.slot]
			if case.remove then
				local enabled = current.enabled
				current.enabled = false
				ok, output = pcall(run)
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
				ok, output = pcall(run)
				for key, value in pairs(saved) do
					current[key] = value
				end
			elseif case.slot == #gemList + 1 then
				gemList[case.slot] = gemInstance(gemByName(case.gem), case.level)
				ok, output = pcall(run)
				gemList[case.slot] = nil
			else
				error("slot " .. tostring(case.slot) .. " is past the end of group " .. tostring(case.group))
			end
		end
		if not ok then
			error(output)
		end
		results[i] = pickStats(output, args.fields)
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

-- Assumptions: modifiers for what PoB doesn't model (e.g. how often a skill is triggered when its
-- trigger source isn't in PoB), kept in the config modifier list so they reach every calculation,
-- what-ifs included. They live only in this engine: exported codes don't carry them.
local assumedMods = { }

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
				end
			end
			return unpack(results)
		end
		configTab.bridgeWrapped = true
	end
	configTab:BuildModList()
end

-- Replace the assumptions: { mods = { {kind = "SkillData", skill = name, key = k, value = v} } }
function bridge.setAssumptions(args)
	assumedMods = args.mods or { }
	installAssumptions()
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Active skills of the current build with what an assumption needs about them: base cooldown,
-- cooldown recovery, and whether they generate Remnants
function bridge.activeSkills()
	local skills = { }
	local env = build.calcsTab.mainEnv
	for _, activeSkill in ipairs(env and env.player.activeSkillList or { }) do
		local effect = activeSkill.activeEffect.grantedEffect
		skills[#skills + 1] = {
			name = effect.name,
			cooldown = activeSkill.skillData.cooldown,
			cooldownRecovery = calcLib.mod(activeSkill.skillModList, activeSkill.skillCfg, "CooldownRecovery"),
			generatesRemnants = activeSkill.skillTypes[SkillType.GeneratesRemnants] and true or false,
			triggered = activeSkill.skillTypes[SkillType.Triggered] and true or false,
			disabled = activeSkill.skillFlags and activeSkill.skillFlags.disable or false,
		}
	end
	return { skills = skills }
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
local correctedTrees = setmetatable({ }, { __mode = "k" })
function bridge.applyPassiveCorrections(args)
	local tree = build.spec.tree
	local done = correctedTrees[tree]
	if not done then
		done = { }
		correctedTrees[tree] = done
		local processStats = tree.ProcessStats
		rawset(tree, "ProcessStats", function(self, node, startIndex)
			local result = processStats(self, node, startIndex)
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
			for _, line in ipairs(correction.lines) do
				-- "flag:Name" switches ledger condition Name on; "if:Name <line>" counts only while it's on
				local flag = line:match("^flag:(%w+)$")
				local condition, rest = line:match("^if:(%w+)%s+(.+)$")
				local list, extra
				if flag then
					list = { modLib.createMod("Condition:Ledger" .. flag, "FLAG", true) }
				else
					list, extra = modLib.parseMod(rest or line)
				end
				if list and not extra then
					for _, parsed in ipairs(list) do
						local copy = copyTable(parsed)
						if condition then
							copy[#copy + 1] = { type = "Condition", var = "Ledger" .. condition }
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
					if modLine.extra or #(modLine.modList or { }) == 0 then
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
		if item and not slot.inactive then
			for _, kind in ipairs({ "enchantModLines", "runeModLines", "implicitModLines", "explicitModLines" }) do
				for _, modLine in ipairs(item[kind] or { }) do
					add("item:" .. slotName, modLine.line)
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

-- Commit an attribute choice (1 Strength, 2 Dexterity, 3 Intelligence) on allocated nodes
function bridge.setAttributes(args)
	local spec = build.spec
	for id, attribute in pairs(args.choices or { }) do
		local node = nodeById(id)
		if node.alloc and node.isAttribute then
			spec:SwitchAttributeNode(node.id, attribute)
		end
	end
	spec:BuildAllDependsAndPaths()
	recalc()
	return { stats = pickStats(build.calcsTab.mainOutput, args.fields) }
end

-- Allocated attribute nodes and the attribute each one has chosen (1 Str, 2 Dex, 3 Int)
function bridge.attributeNodes()
	local nodes = { }
	for id, node in pairs(build.spec.allocNodes) do
		if node.isAttribute then
			local chosen = 0
			local override = build.spec.hashOverrides[id]
			local stats = table.concat((override or node).sd or { }, " ")
			if stats:find("Strength") then
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
		nodes[#nodes + 1] = {
			id = id, name = node.dn, type = node.type, ascendancy = node.ascendancyName,
			stats = plain(node.sd, 2), linked = linked, x = node.x, y = node.y,
			isAttribute = node.isAttribute or nil, isMultipleChoice = node.isMultipleChoice or nil,
			isMultipleChoiceOption = node.isMultipleChoiceOption or nil,
			classStart = node.classStartIndex,
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
	return { treeVersion = build.spec.treeVersion, classes = classes, nodes = nodes }
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
	summary.stats = pickStats(build.calcsTab.mainOutput, args.fields)
	return summary
end

-- Items currently equipped, by slot
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
	local ok, result = xpcall(fn, debug.traceback, args)
	if not ok then
		return false, tostring(result)
	end
	return true, dkjson.encode(result or { })
end
