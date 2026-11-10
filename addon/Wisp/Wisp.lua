-- Wisp, game side (MVP). An addon has no network and no live file access, so:
--   out: a SNAPSHOT of the character and quest log, saved when you log out.
--        For every quest it copies out what Questie knows: who starts and
--        ends it, where they stand, the next quest in the chain.
--   in:  the INBOX. The overlay puts "WISP1:<base64 json>" on the clipboard,
--        you paste it. Only the commands listed below can run; pasted text
--        is data and is never executed as Lua.
--
--   kept: a profession's recipes can only be read while its window is open,
--        so they are copied into WispData then and go into the next snapshot.
--
--   /wisp        open the inbox (same as Ctrl+V), then press Ctrl+V to paste
--   /wisp save   build the snapshot now (still written to disk at logout)

local PASTE_KEY = "CTRL-V"
local E = C_EncodingUtil

local function say(text) print("|cff7fd4ffWisp|r " .. text) end

------------------------------------------------------------ snapshot ----

local function round1(n) return math.floor(n * 10 + 0.5) / 10 end

local function mapName(map)
  local info = map and C_Map.GetMapInfo(map)
  return info and info.name
end

local function questieModule(name)
  if type(QuestieLoader) ~= "table" then return nil end
  local ok, module = pcall(QuestieLoader.ImportModule, QuestieLoader, name)
  return ok and module or nil
end

-- Where an NPC or object stands, from its Questie record: up to three zones.
local function places(record, ZoneDB)
  local out = {}
  for zone, points in pairs(record.spawns or {}) do
    local p = points[1]
    if #out < 3 and p and p[1] and p[1] >= 0 then   -- Questie marks "inside an instance" as -1
      local ok, map = pcall(ZoneDB.GetUiMapIdByAreaId, ZoneDB, zone)
      map = ok and map or nil
      if map then
        out[#out + 1] = { map = map, zone = mapName(map), x = round1(p[1]), y = round1(p[2]) }
      end
    end
  end
  return out
end

local function people(into, kind, ids, DB, ZoneDB)
  for _, id in ipairs(ids or {}) do
    if #into >= 3 then return end
    local ok, record = pcall(kind == "npc" and DB.GetNPC or DB.GetObject, DB, id)
    if ok and record then
      into[#into + 1] = { kind = kind, id = id, name = record.name, places = places(record, ZoneDB) }
    end
  end
end

-- What Questie knows about one quest. `follow` also looks up the next quest in the chain.
local function fromQuestie(questId, DB, ZoneDB, follow)
  local ok, q = pcall(DB.GetQuest, questId)
  if not ok or not q then return nil end
  local out = { title = q.name, level = q.level, requiredLevel = q.requiredLevel, starts = {}, ends = {} }
  people(out.starts, "npc", q.Starts and q.Starts.NPC, DB, ZoneDB)
  people(out.starts, "object", q.Starts and q.Starts.GameObject, DB, ZoneDB)
  people(out.ends, "npc", q.Finisher and q.Finisher.NPC, DB, ZoneDB)
  people(out.ends, "object", q.Finisher and q.Finisher.GameObject, DB, ZoneDB)
  local nextId = q.nextQuestInChain
  if follow and type(nextId) == "number" and nextId > 0 then
    local nextQuest = fromQuestie(nextId, DB, ZoneDB, false)
    if nextQuest then
      out.next = { id = nextId, title = nextQuest.title, level = nextQuest.level, starts = nextQuest.starts }
    end
  end
  return out
end

local function questLog(DB, ZoneDB)
  local quests = {}
  for i = 1, C_QuestLog.GetNumQuestLogEntries() do
    local info = C_QuestLog.GetInfo(i)
    if info and not info.isHeader and not info.isHidden then
      local quest = { id = info.questID, title = info.title, level = info.level, objectives = {} }
      quest.complete = C_QuestLog.IsComplete and C_QuestLog.IsComplete(info.questID) or false
      for _, o in ipairs(C_QuestLog.GetQuestObjectives(info.questID) or {}) do
        quest.objectives[#quest.objectives + 1] = { text = o.text, done = o.finished and true or false }
      end
      local okW, wMap, wX, wY = pcall(C_QuestLog.GetNextWaypoint, info.questID)
      if okW and wMap and wX then   -- the game's own pointer to the next step
        quest.gameWaypoint = { map = wMap, zone = mapName(wMap), x = round1(wX * 100), y = round1(wY * 100) }
      end
      if DB and ZoneDB then
        local okQ, known = pcall(fromQuestie, info.questID, DB, ZoneDB, true)
        if okQ and known then
          quest.starts, quest.ends, quest.next = known.starts, known.ends, known.next
          quest.requiredLevel = known.requiredLevel
        end
      end
      quests[#quests + 1] = quest
    end
  end
  return quests
end

----------------------------------------------------------- dungeons ----
-- From the DungeonJournal addon, when it is installed: every dungeon with its
-- entrance, its quests and what each needs first, its bosses and their loot.
-- Wisp reads the journal's own tables as they are loaded in the game; it does
-- not carry a copy of them.

local function journalPlace(m)   -- the journal keeps positions as 0-1 on a map id
  if type(m) ~= "table" or type(m.mapID) ~= "number" or type(m.x) ~= "number" or type(m.y) ~= "number" then return nil end
  return { map = m.mapID, zone = mapName(m.mapID), x = round1(m.x * 100), y = round1(m.y * 100), label = m.targetName or m.label }
end

local function questState(id)
  if C_QuestLog.IsQuestFlaggedCompleted and C_QuestLog.IsQuestFlaggedCompleted(id) then return "done" end
  if C_QuestLog.GetLogIndexForQuestID and C_QuestLog.GetLogIndexForQuestID(id) then return "log" end
  return nil
end

local function dungeons()
  local J = _G.ForeverDungeonJournal_NS
  if type(J) ~= "table" or type(J.DB) ~= "table" then return nil end
  local faction = UnitFactionGroup("player")
  local chains, details, starts = J.QUEST_PREREQ_CHAINS or {}, J.QUEST_PREREQ_DETAILS or {}, J.QUEST_START_MAPS or {}
  local names = J.ORDER
  if type(names) ~= "table" then
    names = {}
    for name in pairs(J.DB) do names[#names + 1] = name end
    table.sort(names)
  end
  local out = {}
  for _, name in ipairs(names) do
    local d = J.DB[name]
    if type(d) == "table" then
      local dungeon = { name = name, level = d.level, location = d.location, quests = {}, bosses = {},
                        entrance = journalPlace(d.entrance), entranceHint = type(d.entrance) == "table" and d.entrance.detail or nil }
      for _, q in ipairs(d.quests or {}) do
        local mine = q.faction == nil or q.faction == "Both" or q.faction == faction
        if type(q.id) == "number" and mine and not q.hideFromMainList then
          local quest = { id = q.id, name = q.name, level = q.level, requires = q.requires, class = q.classOnly,
                          objective = q.objective, pickup = q.pickup, turnin = q.turnin,
                          rewards = q.rewards or q.rewardSummary, state = questState(q.id),
                          start = journalPlace(q.startMap or starts[q.id]), needs = {} }
          for _, step in ipairs(chains[q.id] or {}) do
            if type(step) == "table" and type(step.id) == "number" then
              local detail = details[step.id] or {}
              quest.needs[#quest.needs + 1] = { id = step.id, name = step.name, state = questState(step.id),
                                                pickup = detail.pickup, start = journalPlace(detail.map or starts[step.id]) }
            end
          end
          dungeon.quests[#dungeon.quests + 1] = quest
        end
      end
      for _, b in ipairs(d.bosses or {}) do
        local boss = { name = b.name, rare = b.rare and true or nil, loot = {} }
        for _, item in ipairs(b.loot or {}) do
          if type(item) == "table" and type(item[1]) == "number" then
            boss.loot[#boss.loot + 1] = { item[1], item[2], item[3], item[4] }   -- id, name, slot, quality
          end
        end
        dungeon.bosses[#dungeon.bosses + 1] = boss
      end
      out[#out + 1] = dungeon
    end
  end
  return out
end

-- Professions with their skill, whichever way this client offers them.
local function professions()
  local out = {}
  if GetProfessions and GetProfessionInfo then
    for i = 1, select("#", GetProfessions()) do
      local index = select(i, GetProfessions())   -- some of the slots are empty
      if index then
        local name, _, skill, maxSkill = GetProfessionInfo(index)
        if name then out[#out + 1] = { name = name, skill = skill, max = maxSkill } end
      end
    end
  elseif C_TradeSkillUI and C_TradeSkillUI.GetAllProfessionTradeSkillLines then
    for _, line in ipairs(C_TradeSkillUI.GetAllProfessionTradeSkillLines()) do
      local info = C_TradeSkillUI.GetProfessionInfoBySkillLineID(line)
      if info and info.professionName and (info.skillLevel or 0) > 0 then
        out[#out + 1] = { name = info.professionName, skill = info.skillLevel, max = info.maxSkillLevel }
      end
    end
  end
  return out
end

--------------------------------------------------- bags and crafting ----

local function itemName(id)
  local name = C_Item and C_Item.GetItemNameByID and C_Item.GetItemNameByID(id)
  if not name and GetItemInfo then name = GetItemInfo(id) end
  return name
end

local function myKey() return tostring(GetRealmName()) .. "-" .. tostring(UnitName("player")) end

local told = {}   -- professions already announced in chat this session
local COLOUR = { [0] = "orange", [1] = "yellow", [2] = "green", [3] = "grey" }   -- the game's skill-up difficulty

-- Called whenever the profession window has a list to show.
local function captureRecipes()
  local T = C_TradeSkillUI
  if not (T and T.IsTradeSkillReady and T.IsTradeSkillReady() and T.GetRecipeInfo and T.GetRecipeSchematic) then return end
  -- a profession linked in chat, or shown by a guild member or an NPC, is not this character's
  if (T.IsTradeSkillLinked and T.IsTradeSkillLinked()) or (T.IsTradeSkillGuild and T.IsTradeSkillGuild())
     or (T.IsNPCCrafting and T.IsNPCCrafting()) then return end
  local info = T.GetBaseProfessionInfo and T.GetBaseProfessionInfo()
  if not (info and type(info.professionName) == "string" and info.professionName ~= "") then return end
  -- every recipe of the profession where the client offers that, else the ones the window lists
  local ids = (T.GetAllRecipeIDs and T.GetAllRecipeIDs()) or (T.GetFilteredRecipeIDs and T.GetFilteredRecipeIDs()) or {}
  if type(WispData) ~= "table" then WispData = {} end
  WispData.recipes = WispData.recipes or {}
  local mine = WispData.recipes[myKey()] or {}
  WispData.recipes[myKey()] = mine
  local book = mine[info.professionName] or { recipes = {} }
  mine[info.professionName] = book
  book.seenAt, book.skill, book.max = time(), info.skillLevel, info.maxSkillLevel
  local basic = Enum and Enum.CraftingReagentType and Enum.CraftingReagentType.Basic
  local read = 0
  for _, id in ipairs(ids) do
    local r = T.GetRecipeInfo(id)
    if r and r.name and not (r.isDummyRecipe or r.isGatheringRecipe or r.isRecraft or r.isSalvageRecipe) then
      read = read + 1
      local plan = T.GetRecipeSchematic(id, false) or {}
      local reagents = {}
      for _, slot in ipairs(plan.reagentSlotSchematics or {}) do
        local first = slot.reagents and slot.reagents[1]
        if first and first.itemID and (slot.quantityRequired or 0) > 0 and slot.required ~= false
           and (basic == nil or slot.reagentType == nil or slot.reagentType == basic) then
          reagents[#reagents + 1] = { id = first.itemID, count = slot.quantityRequired }
        end
      end
      -- keyed by text: a table with scattered number keys does not survive as JSON.
      -- "skill" is the skill this recipe's colour was read at, so its age can be told.
      book.recipes["r" .. id] = {
        id = id, name = r.name, learned = r.learned and true or false, colour = COLOUR[r.relativeDifficulty],
        greyAt = r.maxTrivialLevel, skillUps = r.numSkillUps, skill = info.skillLevel,
        makes = plan.outputItemID, makesMin = plan.quantityMin, makesMax = plan.quantityMax, reagents = reagents,
      }
    end
  end
  if read > 0 and not told[info.professionName] then
    told[info.professionName] = true
    say(("read %d %s recipes. They reach Wisp when you log out."):format(read, info.professionName))
  end
end

-- What is in the bags, one line per item. The bank is not read: it is closed at logout.
local function bags()
  local C, seen, out = C_Container, {}, {}
  if not (C and C.GetContainerNumSlots and C.GetContainerItemInfo) then return out end
  for bag = 0, 5 do
    for slot = 1, C.GetContainerNumSlots(bag) or 0 do
      local item = C.GetContainerItemInfo(bag, slot)
      if item and item.itemID then
        local line = seen[item.itemID]
        if not line then
          line = { id = item.itemID, name = item.itemName or itemName(item.itemID), count = 0 }
          seen[item.itemID] = line
          out[#out + 1] = line
        end
        line.count = line.count + (item.stackCount or 1)
      end
    end
  end
  return out
end

-- The kept recipes of this character, with item names, and the ids of every item they mention.
local function crafting(wanted)
  local mine = type(WispData) == "table" and type(WispData.recipes) == "table" and WispData.recipes[myKey()]
  local books = {}
  for profession, book in pairs(mine or {}) do
    local list = {}
    for _, r in pairs(book.recipes or {}) do
      local reagents = {}
      for _, m in ipairs(r.reagents or {}) do
        reagents[#reagents + 1] = { id = m.id, name = itemName(m.id), count = m.count }
        wanted[m.id] = true
      end
      if r.makes then wanted[r.makes] = true end
      list[#list + 1] = { id = r.id, name = r.name, learned = r.learned, colour = r.colour, greyAt = r.greyAt,
                          skillUps = r.skillUps, skill = r.skill, makes = r.makes, makesName = r.makes and itemName(r.makes),
                          makesMin = r.makesMin, makesMax = r.makesMax, reagents = reagents }
    end
    table.sort(list, function(a, b) return a.id < b.id end)
    books[#books + 1] = { profession = profession, seenAt = book.seenAt, skill = book.skill, max = book.max, recipes = list }
  end
  table.sort(books, function(a, b) return a.profession < b.profession end)
  return books
end

local function auctionator()
  local api = type(Auctionator) == "table" and type(Auctionator.API) == "table" and Auctionator.API.v1
  return type(api) == "table" and api or nil
end

-- Auctionator's price for each wanted item, from the player's own last scans. Nil without Auctionator.
local function prices(wanted)
  local api = auctionator()
  if not (api and api.GetAuctionPriceByItemID) then return nil end
  local out = {}
  for id in pairs(wanted) do
    local ok, price = pcall(api.GetAuctionPriceByItemID, "Wisp", id)
    if ok and type(price) == "number" then
      local okA, age = pcall(api.GetAuctionAgeByItemID, "Wisp", id)
      out[#out + 1] = { id = id, price = price, days = okA and type(age) == "number" and age or nil }
    end
  end
  table.sort(out, function(a, b) return a.id < b.id end)
  return out
end

local function buildSnapshot()
  local version, _, _, interface = GetBuildInfo()
  local map = C_Map.GetBestMapForUnit("player")
  local pos = map and C_Map.GetPlayerMapPosition(map, "player")
  local x, y
  if pos then x, y = pos:GetXY() end
  local DB, ZoneDB = questieModule("QuestieDB"), questieModule("ZoneDB")
  if DB and type(DB.GetQuest) ~= "function" then DB = nil end   -- Questie still starting up
  local _, classFile = UnitClass("player")
  local okD, journal = pcall(dungeons)   -- one addon's surprise must not cost the whole snapshot
  if not okD then journal = nil end
  local okP, skills = pcall(professions)
  if not okP then skills = {} end
  local wanted = {}   -- items worth a price: what is carried and what the recipes use and make
  local okB, carried = pcall(bags)
  if not okB then carried = {} end
  for _, line in ipairs(carried) do wanted[line.id] = true end
  local okC, books = pcall(crafting, wanted)
  if not okC then books = {} end
  local okA, market = pcall(prices, wanted)
  if not okA then market = nil end
  return {
    v = 1,
    takenAt = time(),
    client = { version = version, interface = interface, locale = GetLocale() },
    -- who this is: only so that Wisp can keep each character's history apart on this PC.
    -- It stays out of what the model is given.
    who = { name = UnitName("player"), realm = GetRealmName() },
    professions = skills,
    character = {
      xp = UnitXP and UnitXP("player") or nil, xpMax = UnitXPMax and UnitXPMax("player") or nil, money = GetMoney(),
      class = classFile, race = (UnitRace("player")), level = UnitLevel("player"),
      faction = (UnitFactionGroup("player")), gold = math.floor(GetMoney() / 10000),
      map = map, zone = mapName(map), subzone = GetSubZoneText(),
      x = x and round1(x * 100), y = y and round1(y * 100),
    },
    addons = { questie = DB ~= nil, tomtom = type(TomTom) == "table" and type(TomTom.AddWaypoint) == "function",
               dungeonjournal = journal ~= nil, auctionator = auctionator() ~= nil },
    dungeons = journal,
    bags = carried,
    recipes = books,
    prices = market,
    quests = questLog(DB, ZoneDB),
    completed = C_QuestLog.GetAllCompletedQuestIDs(),
  }
end

-- Returns the snapshot, or nil and the reason. On failure the previous snapshot is kept.
local function save()
  if not (E and E.SerializeJSON and E.EncodeBase64) then return nil, "this client has no JSON encoder" end
  local ok, snapshot = pcall(buildSnapshot)
  if not ok then return nil, tostring(snapshot) end
  local okJ, text = pcall(function() return E.EncodeBase64(E.SerializeJSON(snapshot)) end)
  if not okJ then return nil, tostring(text) end
  WispSnapshot = text
  return snapshot
end

--------------------------------------------------------------- inbox ----

-- The whitelist. A pasted message picks one of these and supplies arguments.
local commands = {}

commands["tomtom.waypoint"] = function(a)   -- { map = uiMapID, x = 0-100, y = 0-100, title = text }
  local map, x, y = tonumber(a.map), tonumber(a.x), tonumber(a.y)
  if not (map and x and y and map > 0 and x >= 0 and x <= 100 and y >= 0 and y <= 100) then
    return false, "bad waypoint"
  end
  local title = "Wisp"
  if type(a.title) == "string" and a.title ~= "" then
    title = (a.title:gsub("|", "")):sub(1, 80)   -- no colour, texture or link codes from outside
  end
  if type(TomTom) == "table" and type(TomTom.AddWaypoint) == "function" then
    TomTom:AddWaypoint(map, x / 100, y / 100, { title = title, from = "Wisp" })
  elseif C_Map.CanSetUserWaypointOnMap and C_Map.CanSetUserWaypointOnMap(map) then
    C_Map.SetUserWaypoint(UiMapPoint.CreateFromCoordinates(map, x / 100, y / 100))
    C_SuperTrack.SetSuperTrackedUserWaypoint(true)
    title = title .. " (map pin: TomTom is not installed)"
  else
    return false, "cannot set a waypoint on that map"
  end
  return true, "waypoint: " .. title
end

-- { name = list name, items = { { id = item id, name = text, quantity = n }, ... } }
-- Creates (or replaces) one of Auctionator's shopping lists through its public API.
commands["auctionator.shopping_list"] = function(a)
  local api = auctionator()
  if not (api and api.CreateShoppingList and api.ConvertToSearchString) then return false, "Auctionator is not installed" end
  if type(a.items) ~= "table" or #a.items == 0 or #a.items > 60 then return false, "bad shopping list" end
  local clean = function(text, limit) return (text:gsub("[|;%c]", "")):sub(1, limit) end
  local listName = "Wisp"
  if type(a.name) == "string" and clean(a.name, 40) ~= "" then listName = clean(a.name, 40) end
  local searches = {}
  for _, item in ipairs(a.items) do
    local quantity = type(item) == "table" and tonumber(item.quantity)
    if not (quantity and quantity >= 1 and quantity <= 100000) then return false, "bad shopping list" end
    -- the game's own name for the item where it has one: it is in the player's language
    local name = tonumber(item.id) and itemName(tonumber(item.id))
    if not name and type(item.name) == "string" then name = clean(item.name, 80) end
    if not name or name == "" then return false, "bad shopping list" end
    local ok, search = pcall(api.ConvertToSearchString, "Wisp",
                             { searchString = name, isExact = true, quantity = math.floor(quantity) })
    if not ok then return false, "Auctionator refused " .. name end
    searches[#searches + 1] = search
  end
  local ok, why = pcall(api.CreateShoppingList, "Wisp", listName, searches)
  if not ok then return false, "Auctionator: " .. tostring(why) end
  return true, ("shopping list \"%s\" with %d items is in Auctionator's Shopping tab"):format(listName, #searches)
end

local function receive(text)
  if text:sub(1, 6) ~= "WISP1:" then return say("that is not a Wisp message.") end
  local ok, message = pcall(function() return E.DeserializeJSON(E.DecodeBase64(text:sub(7))) end)
  if not ok or type(message) ~= "table" or type(message.cmds) ~= "table" then
    return say("could not read that message.")
  end
  for i, cmd in ipairs(message.cmds) do
    if i > 50 then say("stopped after 50 commands."); break end
    local run = type(cmd) == "table" and commands[cmd["do"]]
    if not run then
      say("|cffff6060skipped|r unknown command " .. tostring(type(cmd) == "table" and cmd["do"]))
    else
      local okRun, good, note = pcall(run, cmd)
      if okRun and good then
        say(note)
      else
        say("|cffff6060failed|r " .. tostring(okRun and note or good))
      end
    end
  end
end

local box = CreateFrame("EditBox", "WispInbox", UIParent, "InputBoxTemplate")
box:SetSize(280, 24)
box:SetPoint("TOP", 0, -140)
box:SetAutoFocus(false)
box:SetMaxLetters(0)
box:SetMaxBytes(0)
box:Hide()
local hint = box:CreateFontString(nil, "OVERLAY", "GameFontNormal")
hint:SetPoint("BOTTOM", box, "TOP", 0, 4)
hint:SetText("Wisp: paste now (Ctrl+V). Esc closes.")

local opened = 0   -- counts openings, so an old timer cannot close a newer box

local function closeBox()
  box:SetText("")
  box:ClearFocus()
  box:Hide()
end

local function openBox()
  opened = opened + 1
  local mine = opened
  box:Show()
  box:SetFocus()
  -- a focused edit box swallows movement keys: never leave it open
  C_Timer.After(6, function()
    if mine == opened and box:GetText() == "" then closeBox() end
  end)
end

box:SetScript("OnEscapePressed", closeBox)
box:SetScript("OnTextChanged", function(self, userInput)
  if not userInput then return end
  local text = self:GetText()
  -- wait a moment in case a long paste arrives in pieces; act on the last one
  C_Timer.After(0.3, function()
    if self:GetText() ~= text or text == "" then return end
    closeBox()
    receive(text)
  end)
end)

local opener = CreateFrame("Button", "WispInboxOpener")   -- the key binding clicks this
opener:SetScript("OnClick", openBox)

---------------------------------------------------------------- boot ----

local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:RegisterEvent("TRADE_SKILL_SHOW")
frame:RegisterEvent("TRADE_SKILL_LIST_UPDATE")
frame:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGOUT" then
    save()
  elseif event ~= "PLAYER_LOGIN" then
    pcall(captureRecipes)
  else
    pcall(SetOverrideBindingClick, opener, false, PASTE_KEY, "WispInboxOpener")
  end
end)

SLASH_WISP1 = "/wisp"
SlashCmdList.WISP = function(msg)
  if msg:match("save") then
    local snapshot, why = save()
    if snapshot then
      say(("snapshot built: %d quests, Questie %s, DungeonJournal %s. It is written to disk when you log out.")
        :format(#snapshot.quests, snapshot.addons.questie and "found" or "not found",
                snapshot.dungeons and (#snapshot.dungeons .. " dungeons") or "not found"))
    else
      say("|cffff6060could not build the snapshot:|r " .. tostring(why))
    end
  else
    openBox()
  end
end
