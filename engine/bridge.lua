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
	local picked = { }
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
		end
	end
	return picked
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

local function withAttribute(node, attribute)
	if not node.isAttribute or node.alloc then
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
-- the selected ascendancy's nodes instead of the main tree's.
function bridge.frontier(args)
	local maxDistance = args.maxDistance or 3
	local ascendancy = args.ascendancy and build.spec.curAscendClassName or nil
	local nodes = { }
	for id, node in pairs(build.spec.nodes) do
		if not node.alloc and node.path and node.pathDist and node.pathDist <= maxDistance
			and node.modKey and node.modKey ~= "" and node.ascendancyName == ascendancy
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
-- attribute = 1|2|3 }. With addPaths, each added id brings its whole path from the allocated
-- tree; attribute nodes on it take the given option (default 1, as allocation does).
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
		local item = new("Item"):Item(case.raw)
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

-- Commit an item into a specific slot, then recalculate fully
function bridge.equip(args)
	local item = new("Item"):Item(args.raw)
	build.itemsTab:AddItem(item, true)
	build.itemsTab.slots[args.slot]:SetSelItemId(item.id)
	local start = os.clock()
	recalc()
	return { seconds = os.clock() - start, stats = pickStats(build.calcsTab.mainOutput, args.fields) }
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
