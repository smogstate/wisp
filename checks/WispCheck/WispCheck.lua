-- WispCheck: a throwaway addon for milestone 0 (wiki/Checks.md).
-- It answers one question per check: does the Forever client really give an
-- addon this? Every check runs inside pcall, so a wrong guess about an
-- interface shows up as an error line in the report, never as a broken addon.
--
--   /wispcheck          run the checks, print a summary, store the report
--   /wispcheck paste    open the paste box (then press Ctrl+V)
--
-- The report is written to disk when you log out:
--   WTF\Account\<account>\SavedVariables\WispCheck.lua

local R            -- the report being built
local PASTE_KEY = "CTRL-V"

local function say(text) print("|cff7fd4ffWispCheck|r " .. text) end

-- "C_QuestLine.GetAvailableQuestLines" -> the function, or nil
local function resolve(path)
  local v = _G
  for part in path:gmatch("[^.]+") do
    if type(v) ~= "table" then return nil end
    v = v[part]
  end
  return v
end

-- Run one check. fn returns a table of findings; an error is recorded as text.
local function check(name, fn)
  local ok, result = pcall(fn)
  if ok then
    R.checks[name] = result
  else
    R.checks[name] = { error = tostring(result) }
  end
end

-- the field names of a table, so the report shows a structure without guessing it
local function keys(t)
  local names = {}
  for k in pairs(t) do
    if type(k) == "string" then names[#names + 1] = k end
  end
  table.sort(names)
  return table.concat(names, " ", 1, math.min(#names, 60))
end

local function playerMap()
  local map = C_Map.GetBestMapForUnit("player")
  local x, y
  local pos = map and C_Map.GetPlayerMapPosition(map, "player")
  if pos then x, y = pos:GetXY() end
  return map, x and math.floor(x * 1000) / 10, y and math.floor(y * 1000) / 10
end

------------------------------------------------------------ presence ----

local WANTED = {
  "CreateMacro", "EditMacro", "DeleteMacro", "GetNumMacros", "GetMacroInfo",
  "C_EncodingUtil.SerializeJSON", "C_EncodingUtil.DeserializeJSON",
  "C_EncodingUtil.EncodeBase64", "C_EncodingUtil.DecodeBase64", "C_EncodingUtil.CompressString",
  "C_QuestLog.GetAllCompletedQuestIDs", "C_QuestLog.GetInfo", "C_QuestLog.GetQuestObjectives",
  "C_QuestLog.GetNextWaypoint", "C_QuestLog.GetQuestsOnMap",
  "C_QuestLine.GetAvailableQuestLines", "C_QuestLine.GetQuestLineQuests", "C_QuestLine.RequestQuestLinesForMap",
  "C_EncounterJournal.GetDungeonEntrancesForMap", "C_EncounterJournal.GetLootInfoByIndex",
  "EJ_GetNumTiers", "EJ_SelectTier", "EJ_GetInstanceByIndex", "EJ_SelectInstance",
  "EJ_GetEncounterInfoByIndex", "EJ_SelectEncounter", "EJ_GetNumLoot",
  "C_TaxiMap.GetTaxiNodesForMap", "C_TaxiMap.GetAllTaxiNodes",
  "C_AreaPoiInfo.GetAreaPOIForMap", "C_AreaPoiInfo.GetAreaPOIInfo",
  "C_TooltipInfo.GetItemByID", "C_TooltipInfo.GetSpellByID", "C_TooltipInfo.GetInventoryItem",
  "C_Traits.GenerateImportString", "C_ClassTalents.GetActiveConfigID", "C_ClassTalents.ImportLoadout",
  "GetProfessions", "GetProfessionInfo", "GetNumSkillLines", "GetSkillLineInfo",
  "C_TradeSkillUI.GetAllRecipeIDs", "C_TradeSkillUI.GetFilteredRecipeIDs", "C_TradeSkillUI.GetRecipeInfo",
  "C_TradeSkillUI.GetRecipeSchematic", "C_TradeSkillUI.IsTradeSkillReady", "C_TradeSkillUI.GetAllProfessionTradeSkillLines",
  "GetNumTradeSkills", "GetTradeSkillInfo", "GetTradeSkillReagentInfo",
  "C_SpellBook.GetNumSpellBookSkillLines", "C_SpellBook.GetSpellBookSkillLineInfo", "C_SpellBook.GetSpellBookItemName",
  "C_Container.GetContainerNumSlots", "C_Container.GetContainerItemID", "C_Bank.FetchNumPurchasedBankTabs",
  "C_Map.SetUserWaypoint", "C_Map.CanSetUserWaypointOnMap", "C_SuperTrack.SetSuperTrackedUserWaypoint",
  "C_Map.GetMapInfo", "C_Map.GetMapChildrenInfo", "C_Map.GetAreaInfo",
  "C_Reputation.GetNumFactions", "C_DeathRecap.HasRecapEvents", "C_DamageMeter.IsDamageMeterAvailable",
  "C_AddOns.IsAddOnLoaded", "issecretvalue",
  "TomTom.AddWaypoint", "TomTom.RemoveWaypoint",
  "Auctionator.API.v1.CreateShoppingList", "Auctionator.API.v1.ConvertToSearchString",
  "Auctionator.API.v1.GetAuctionPriceByItemID",
  "QuestieLoader.ImportModule", "C_AddOns.GetNumAddOns", "C_AddOns.GetAddOnInfo",
}

-------------------------------------------------------------- checks ----

local function runChecks()
  check("client", function()
    local version, build, _, toc = GetBuildInfo()
    return { version = version, build = build, interface = toc, locale = GetLocale(),
             class = select(2, UnitClass("player")), level = UnitLevel("player") }
  end)

  check("presence", function()
    local t = {}
    for _, path in ipairs(WANTED) do t[path] = type(resolve(path)) end
    return t
  end)

  check("encoding", function()
    local E = C_EncodingUtil
    local value = { a = 1, list = { "x", "й" }, nested = { ok = true } }
    local json = E.SerializeJSON(value)
    local back = E.DeserializeJSON(json)
    local b64 = E.EncodeBase64(json)
    local out = { json = json, roundTrip = back.a == 1 and back.list[2] == "й" and back.nested.ok == true,
                  base64RoundTrip = E.DecodeBase64(b64) == json }
    -- a message exactly as the overlay would send it (made outside the game)
    local sample = "eyJjbWRzIjpbeyJkbyI6InRvbXRvbS53YXlwb2ludCIsInpvbmUiOjE1MTksIngiOjUzLjQsInkiOjU5LjQsInRpdGxlIjoiQXVjdGlvbmVlciBGaXRjaCJ9XX0="
    local msg = E.DeserializeJSON(E.DecodeBase64(sample))
    out.outsideMessage = msg.cmds[1]["do"] .. " " .. tostring(msg.cmds[1].x)
    local okC, packed = pcall(E.CompressString, json)
    out.compress = okC and #packed or tostring(packed)
    return out
  end)

  check("map", function()
    local map, x, y = playerMap()
    local info = map and C_Map.GetMapInfo(map)
    local out = { map = map, x = x, y = y, name = info and info.name, mapType = info and info.mapType,
                  parent = info and info.parentMapID }
    out.canSetWaypoint = map and C_Map.CanSetUserWaypointOnMap(map)
    local kids = info and info.parentMapID and C_Map.GetMapChildrenInfo(info.parentMapID)
    out.siblings = kids and #kids
    return out
  end)

  check("completedQuests", function()
    local ids = C_QuestLog.GetAllCompletedQuestIDs()
    return { count = #ids, first = ids[1], last = ids[#ids] }
  end)

  check("questLog", function()
    local n = C_QuestLog.GetNumQuestLogEntries()
    local out = { entries = n, quests = 0 }
    for i = 1, n do
      local q = C_QuestLog.GetInfo(i)
      if q and not q.isHeader and not q.isHidden then
        out.quests = out.quests + 1
        if not out.sample then
          local objectives = C_QuestLog.GetQuestObjectives(q.questID)
          local wm, wx, wy = C_QuestLog.GetNextWaypoint(q.questID)
          out.sample = { id = q.questID, title = q.title, objectives = objectives and #objectives,
                         objective1 = objectives and objectives[1] and objectives[1].text,
                         waypointMap = wm, waypointX = wx, waypointY = wy }
        end
      end
    end
    return out
  end)

  check("questLinesRequest", function()
    local map = playerMap()
    C_QuestLine.RequestQuestLinesForMap(map)   -- answer is read in finish()
    return { requestedFor = map }
  end)

  check("journal", function()
    local out = { tiers = EJ_GetNumTiers() }
    if out.tiers and out.tiers > 0 then EJ_SelectTier(1) end
    local function count(isRaid)
      local n, firstId, firstName = 0, nil, nil
      for i = 1, 200 do
        local id, name = EJ_GetInstanceByIndex(i, isRaid)
        if not id then break end
        n = n + 1
        if not firstId then firstId, firstName = id, name end
      end
      return n, firstId, firstName
    end
    local dId, dName
    out.dungeons, dId, dName = count(false)
    out.raids = count(true)
    out.firstDungeon = dName
    if dId then
      EJ_SelectInstance(dId)
      local bossName, _, encounterId = EJ_GetEncounterInfoByIndex(1, dId)
      out.firstBoss = bossName
      if encounterId then
        EJ_SelectEncounter(encounterId)
        out.lootCount = EJ_GetNumLoot()        -- may still be 0: loot arrives late, see finish()
        out.encounterId = encounterId
      end
    end
    local map = playerMap()
    local entrances = C_EncounterJournal.GetDungeonEntrancesForMap(map)
    out.entrancesOnThisMap = entrances and #entrances
    out.entrance1 = entrances and entrances[1] and entrances[1].name
    return out
  end)

  check("flightPoints", function()
    local map = playerMap()
    local nodes = C_TaxiMap.GetTaxiNodesForMap(map)
    local first = nodes and nodes[1]
    local x, y
    if first and first.position then x, y = first.position:GetXY() end
    return { count = nodes and #nodes, name = first and first.name, x = x, y = y }
  end)

  check("pointsOfInterest", function()
    local map = playerMap()
    local ids = C_AreaPoiInfo.GetAreaPOIForMap(map)
    local info = ids and ids[1] and C_AreaPoiInfo.GetAreaPOIInfo(map, ids[1])
    return { count = ids and #ids, name = info and info.name }
  end)

  check("tooltipById", function()
    local hearthstone = C_TooltipInfo.GetItemByID(6948)
    local worn = C_TooltipInfo.GetInventoryItem("player", 16)
    return { itemLines = hearthstone and hearthstone.lines and #hearthstone.lines,
             itemLine1 = hearthstone and hearthstone.lines and hearthstone.lines[1] and hearthstone.lines[1].leftText,
             mainHandLines = worn and worn.lines and #worn.lines }
  end)

  check("talents", function()
    local config = C_ClassTalents.GetActiveConfigID()
    local text = config and C_Traits.GenerateImportString(config)
    return { configId = config, importString = text, length = text and #text,
             spec = GetSpecialization and GetSpecialization() }
  end)

  check("professions", function()
    local out = { modern = {}, classicSkillLines = GetNumSkillLines and GetNumSkillLines() }
    if GetProfessions then
      for _, index in ipairs({ GetProfessions() }) do
        local name, _, skill, maxSkill, _, _, skillLine = GetProfessionInfo(index)
        out.modern[#out.modern + 1] = { name = name, skill = skill, max = maxSkill, skillLine = skillLine }
      end
    end
    return out
  end)

  check("recipes", function()   -- only meaningful with a profession window open
    local out = { ready = C_TradeSkillUI.IsTradeSkillReady and C_TradeSkillUI.IsTradeSkillReady() }
    local ids = C_TradeSkillUI.GetAllRecipeIDs and C_TradeSkillUI.GetAllRecipeIDs()
    out.allRecipeIds = ids and #ids
    local filtered = C_TradeSkillUI.GetFilteredRecipeIDs and C_TradeSkillUI.GetFilteredRecipeIDs()
    out.filteredRecipeIds = filtered and #filtered
    local id = (ids and ids[1]) or (filtered and filtered[1])
    if id then
      local info = C_TradeSkillUI.GetRecipeInfo(id)
      local schematic = C_TradeSkillUI.GetRecipeSchematic(id, false)
      out.sample = { id = id, name = info and info.name, learned = info and info.learned,
                     difficulty = info and info.relativeDifficulty,
                     reagentSlots = schematic and schematic.reagentSlotSchematics and #schematic.reagentSlotSchematics }
    end
    out.classicTradeSkills = GetNumTradeSkills and GetNumTradeSkills()
    return out
  end)

  check("spellbook", function()
    local lines = C_SpellBook.GetNumSpellBookSkillLines()
    local total, firstName = 0, nil
    for i = 1, lines do
      local info = C_SpellBook.GetSpellBookSkillLineInfo(i)
      total = total + (info and info.numSpellBookItems or 0)
    end
    if total > 0 then firstName = C_SpellBook.GetSpellBookItemName(1, Enum.SpellBookSpellBank.Player) end
    return { skillLines = lines, spells = total, first = firstName }
  end)

  check("bags", function()
    local slots, items = 0, 0
    for bag = 0, 4 do
      local n = C_Container.GetContainerNumSlots(bag) or 0
      slots = slots + n
      for slot = 1, n do
        if C_Container.GetContainerItemID(bag, slot) then items = items + 1 end
      end
    end
    return { slots = slots, items = items }
  end)

  check("macros", function()   -- creates one macro and deletes it again
    local account, character = GetNumMacros()
    local out = { account = account, character = character, inCombat = InCombatLockdown() }
    if not InCombatLockdown() and account < 120 then
      local id = CreateMacro("WispCheck", "INV_MISC_QUESTIONMARK", "/say WispCheck", false)
      out.created = id
      local name, _, body = GetMacroInfo("WispCheck")
      out.readBack = name == "WispCheck" and body
      DeleteMacro("WispCheck")
      out.deleted = GetMacroInfo("WispCheck") == nil
    end
    return out
  end)

  check("tomtom", function()   -- adds one waypoint where you stand and removes it again
    local loaded = C_AddOns.IsAddOnLoaded("TomTom")
    local out = { loaded = loaded }
    if loaded then
      local map, x, y = playerMap()
      local uid = TomTom:AddWaypoint(map, x / 100, y / 100,
        { title = "WispCheck", persistent = false, minimap = false, world = false, from = "WispCheck" })
      out.added = uid ~= nil
      if uid then TomTom:RemoveWaypoint(uid); out.removed = true end
    end
    return out
  end)

  check("auctionator", function()   -- builds a search string only; creates no list
    local loaded = C_AddOns.IsAddOnLoaded("Auctionator")
    local out = { loaded = loaded }
    if loaded then
      out.searchString = Auctionator.API.v1.ConvertToSearchString("WispCheck",
        { searchString = "Linen Cloth", isExact = true, quantity = 5 })
      out.createList = type(Auctionator.API.v1.CreateShoppingList)
    end
    return out
  end)

  check("addons", function()   -- which addons are installed and running
    local loaded = {}
    for i = 1, C_AddOns.GetNumAddOns() do
      local name = C_AddOns.GetAddOnInfo(i)
      if C_AddOns.IsAddOnLoaded(i) and not name:match("^Blizzard_") then loaded[#loaded + 1] = name end
    end
    return { installed = C_AddOns.GetNumAddOns(), loaded = table.concat(loaded, ", ") }
  end)

  check("questie", function()   -- can another addon read Questie's database?
    local out = { loaded = C_AddOns.IsAddOnLoaded("Questie"), loader = type(QuestieLoader) }
    if type(QuestieLoader) ~= "table" then return out end
    local DB = QuestieLoader:ImportModule("QuestieDB")
    out.getQuest, out.getNpc, out.dropRate = type(DB.GetQuest), type(DB.GetNPC), type(DB.GetItemDroprate)
    local questId
    for i = 1, C_QuestLog.GetNumQuestLogEntries() do
      local q = C_QuestLog.GetInfo(i)
      if q and not q.isHeader then questId = q.questID; break end
    end
    out.questId = questId
    local quest = questId and DB.GetQuest(questId)
    if not quest then return out end
    out.questName, out.questFields = quest.name, keys(quest)
    out.nextInChain, out.requiredLevel = quest.nextQuestInChain, quest.requiredLevel
    local npcId = quest.Starts and quest.Starts.NPC and quest.Starts.NPC[1]
    out.starterNpc = npcId
    local npc = npcId and DB:GetNPC(npcId)
    if npc then
      out.npcName, out.npcFields = npc.name, keys(npc)
      local zones = 0
      for zone, points in pairs(npc.spawns or {}) do
        zones = zones + 1
        if not out.spawnZone and points[1] then out.spawnZone, out.spawnX, out.spawnY = zone, points[1][1], points[1][2] end
      end
      out.spawnZones = zones
    end
    return out
  end)

  check("misc", function()
    return { factions = C_Reputation.GetNumFactions(),
             deathRecap = C_DeathRecap.HasRecapEvents(),
             damageMeter = C_DamageMeter.IsDamageMeterAvailable() }
  end)
end

-- Answers that arrive a moment after they are asked for.
local function finish()
  check("questLines", function()
    local map = playerMap()
    local lines = C_QuestLine.GetAvailableQuestLines(map)
    local out = { count = lines and #lines }
    local first = lines and lines[1]
    if first then
      local quests = C_QuestLine.GetQuestLineQuests(first.questLineID)
      out.sample = { line = first.questLineName, quest = first.questName, questId = first.questID,
                     x = first.x, y = first.y, questsInLine = quests and #quests }
    end
    return out
  end)

  check("journalLoot", function()
    local out = { lootCount = EJ_GetNumLoot() }
    local item = out.lootCount and out.lootCount > 0 and C_EncounterJournal.GetLootInfoByIndex(1)
    if item then out.item1 = item.name; out.item1Id = item.itemID; out.slot = item.slot end
    return out
  end)

  check("exportSize", function()   -- how big would a real snapshot be
    local export = { completed = C_QuestLog.GetAllCompletedQuestIDs(), bags = {}, checks = R.checks }
    for bag = 0, 4 do
      for slot = 1, C_Container.GetContainerNumSlots(bag) or 0 do
        export.bags[#export.bags + 1] = C_Container.GetContainerItemID(bag, slot) or 0
      end
    end
    local json = C_EncodingUtil.SerializeJSON(export)
    local okC, packed = pcall(C_EncodingUtil.CompressString, json)
    return { jsonBytes = #json, compressedBytes = okC and #packed or nil }
  end)

  R.finished = true
  local failed, total = 0, 0
  for name, result in pairs(R.checks) do
    total = total + 1
    if type(result) == "table" and result.error then
      failed = failed + 1
      say("|cffff6060error|r " .. name .. ": " .. result.error)
    end
  end
  local missing = 0
  for path, kind in pairs(R.checks.presence or {}) do
    if kind == "nil" then missing = missing + 1 end
  end
  say(("done: %d checks, %d with errors, %d of %d interfaces missing."):format(total, failed, missing, #WANTED))
  say("Next: /wispcheck paste (twice, one per sample), then log out and send the saved file.")
end

--------------------------------------------------------------- paste ----

local box = CreateFrame("EditBox", "WispCheckPaste", UIParent, "InputBoxTemplate")
box:SetSize(320, 24)
box:SetPoint("TOP", 0, -140)
box:SetAutoFocus(false)
box:SetMaxLetters(0)
box:SetMaxBytes(0)
box:Hide()
local hint = box:CreateFontString(nil, "OVERLAY", "GameFontNormal")
hint:SetPoint("BOTTOM", box, "TOP", 0, 4)
hint:SetText("WispCheck: paste now (Ctrl+V). Esc closes.")

local events = 0   -- how many change events one paste produces

local function closeBox()
  box:SetText("")
  box:ClearFocus()
  box:Hide()
end

box:SetScript("OnEscapePressed", closeBox)
box:SetScript("OnTextChanged", function(self, userInput)
  if not userInput then return end
  events = events + 1
  local text = self:GetText()
  -- wait a moment: if a paste arrives in several pieces, record the last one
  local seen = events
  C_Timer.After(0.3, function()
    if seen ~= events then return end
    local entry = { chars = #text, changeEvents = events, tagged = text:sub(1, 6) == "WISP1:" }
    local ok, result = pcall(function()
      local message = C_EncodingUtil.DeserializeJSON(C_EncodingUtil.DecodeBase64(text:sub(7)))
      return #message.cmds
    end)
    entry.decoded = ok
    entry.commands = ok and result or tostring(result)
    WispCheckDB.paste = WispCheckDB.paste or {}
    table.insert(WispCheckDB.paste, entry)
    say(("paste: %d characters in %d event(s), decoded: %s, commands: %s")
      :format(entry.chars, entry.changeEvents, tostring(entry.decoded), tostring(entry.commands)))
    events = 0
    closeBox()
  end)
end)

local function openBox()
  events = 0
  box:Show()
  box:SetFocus()
end

local opener = CreateFrame("Button", "WispCheckOpener")   -- the key binding clicks this
opener:SetScript("OnClick", function()
  WispCheckDB.bindingFired = true
  openBox()
end)

---------------------------------------------- learned by playing ----

-- Does talking to an NPC tell an addon who it is and where you stand?
local NPC_EVENTS = { "MERCHANT_SHOW", "TRAINER_SHOW", "GOSSIP_SHOW", "QUEST_DETAIL", "QUEST_GREETING",
                     "TAXIMAP_OPENED", "BANKFRAME_OPENED", "AUCTION_HOUSE_SHOW" }

local function sawNpc(event)
  local seen = WispCheckDB.seen
  if #seen >= 30 then return end
  local ok, entry = pcall(function()
    local map, x, y = playerMap()
    local guid = UnitGUID("npc")
    return { event = event, name = UnitName("npc"), guidKind = guid and guid:match("^(%a+)"),
             npcId = guid and tonumber((select(6, strsplit("-", guid)))), map = map, x = x, y = y }
  end)
  seen[#seen + 1] = ok and entry or { event = event, error = tostring(entry) }
end

---------------------------------------------------------------- boot ----

local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")
frame:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGIN" then
    WispCheckDB = WispCheckDB or {}
    WispCheckDB.seen = WispCheckDB.seen or {}
    for _, name in ipairs(NPC_EVENTS) do pcall(frame.RegisterEvent, frame, name) end
    local ok, err = pcall(SetOverrideBindingClick, opener, false, PASTE_KEY, "WispCheckOpener")
    WispCheckDB.bindingSet = ok or tostring(err)
    say("loaded. Type /wispcheck")
  else
    sawNpc(event)
  end
end)

SLASH_WISPCHECK1 = "/wispcheck"
SlashCmdList.WISPCHECK = function(msg)
  if msg:match("paste") then
    openBox()
    return
  end
  R = { ranAt = date("%Y-%m-%d %H:%M:%S"), checks = {} }
  WispCheckDB.report = R
  say("running... results in 4 seconds.")
  runChecks()
  C_Timer.After(4, finish)
end
