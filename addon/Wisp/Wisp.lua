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

local function people(into, kind, ids, DB, ZoneDB, cap)
  for _, id in ipairs(ids or {}) do
    if #into >= (cap or 3) then return end
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
  for _, objective in ipairs(type(q.ObjectiveData) == "table" and q.ObjectiveData or {}) do   -- the items it asks for
    if type(objective) == "table" and objective.Type == "item" and type(objective.Id) == "number" then
      out.needs = out.needs or {}
      out.needs[#out.needs + 1] = { id = objective.Id, name = objective.Text }
    end
  end
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
          quest.requiredLevel, quest.needs = known.requiredLevel, known.needs
        end
      end
      quests[#quests + 1] = quest
    end
  end
  return quests
end

-- Every quest the character could pick up now, as Questie works it out: the same list it draws
-- its map icons from, so it follows Questie's own settings (how far below your level it shows).
-- For each: level, who gives it and where, the items it asks for, and whether Questie has it
-- down as elite (a group quest), dungeon, raid or PvP.
local function availableQuests(DB, ZoneDB)
  local module = questieModule("AvailableQuests")
  local ids = type(module) == "table" and module.__availableQuests
  if not (DB and ZoneDB) or type(ids) ~= "table" then return nil end
  local order = {}
  for id in pairs(ids) do
    if type(id) == "number" then order[#order + 1] = id end
  end
  table.sort(order)
  local kinds = { { "elite", DB.IsGroupQuest }, { "dungeon", DB.IsDungeonQuest }, { "raid", DB.IsRaidQuest }, { "pvp", DB.IsPvPQuest } }
  local out = {}
  for _, id in ipairs(order) do
    if #out >= 600 then break end
    local ok, known = pcall(fromQuestie, id, DB, ZoneDB, false)
    if ok and known and known.title then
      local kind
      for _, test in ipairs(kinds) do
        if not kind and type(test[2]) == "function" then
          local okT, yes = pcall(test[2], id)
          if okT and yes then kind = test[1] end
        end
      end
      out[#out + 1] = { id = id, title = known.title, level = known.level, requiredLevel = known.requiredLevel,
                        kind = kind, starts = known.starts, needs = known.needs }
    end
  end
  return out
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

local function dungeons(DB)
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
          if DB then   -- the items it asks for, where Questie knows the quest
            local okQ, known = pcall(DB.GetQuest, q.id)
            for _, objective in ipairs(okQ and type(known) == "table" and type(known.ObjectiveData) == "table" and known.ObjectiveData or {}) do
              if type(objective) == "table" and objective.Type == "item" and type(objective.Id) == "number" then
                quest.items = quest.items or {}
                quest.items[#quest.items + 1] = { id = objective.Id, name = objective.Text }
              end
            end
          end
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

-- Every item Auctionator has a price for. Its public API answers per item and cannot list them,
-- so the list of ids, and only that, is taken from its price table. The prices themselves come
-- from the API. Pets and item-level variants are kept under other keys and are left out.
local function marketIds(into)
  local db = type(Auctionator) == "table" and type(Auctionator.Database) == "table" and Auctionator.Database.db
  if type(db) ~= "table" then return end
  for key in pairs(db) do
    local id = tonumber(key)
    if id and id > 0 and id == math.floor(id) then into[id] = true end
  end
end

-- Item names are learned while playing and kept: the game loads an item it has not shown yet only
-- on request and a moment later, which is too late to ask for at logout.
local function knownName(id)
  local name = itemName(id)
  if type(WispData) ~= "table" then WispData = {} end
  WispData.items = WispData.items or {}
  if name then WispData.items[id] = name end
  return name or WispData.items[id]
end

local learning = 0
local function learnNames(pass)
  if pass == nil then
    if learning > 0 then return end   -- already under way
    pass = 1
  end
  learning = pass
  local ids, asked = {}, 0
  if not pcall(marketIds, ids) then learning = 0; return end
  for id in pairs(ids) do
    if not knownName(id) then
      asked = asked + 1
      if asked > 400 then break end   -- a few hundred at a time, so the game never stutters
      if C_Item and C_Item.RequestLoadItemDataByID then C_Item.RequestLoadItemDataByID(id) end
    end
  end
  if asked > 0 and pass < 60 then
    C_Timer.After(3, function() learnNames(pass + 1) end)
  else
    learning = 0
  end
end

-- Auctionator's price for every item it has seen and every item wanted, from the player's own
-- scans, as rows of { item id, copper, days since it was seen or -1, name or "" }: thousands of
-- rows, so kept small. Nil without Auctionator.
local function prices(wanted)
  local api = auctionator()
  if not (api and api.GetAuctionPriceByItemID) then return nil end
  pcall(marketIds, wanted)
  local out = {}
  for id in pairs(wanted) do
    local ok, price = pcall(api.GetAuctionPriceByItemID, "Wisp", id)
    if ok and type(price) == "number" and price > 0 then
      local okA, age = pcall(api.GetAuctionAgeByItemID, "Wisp", id)
      out[#out + 1] = { id, price, okA and type(age) == "number" and age or -1, knownName(id) or "" }
    end
  end
  table.sort(out, function(a, b) return a[1] < b[1] end)
  return out
end

-- Where items come from, as Questie has it: who sells one, what drops it, what it is found in.
-- Only for the items given (what is carried, crafted with, crafted, or asked for by a quest):
-- asking Questie about every item in the game at logout would take too long.
-- ponytail: the first few sources as Questie lists them, not the best ones; drop rates, and a
-- background pass over every priced item, when "where do I farm this" needs them
local function itemSources(ids, DB, ZoneDB)
  local out, done = {}, 0
  if not (DB and ZoneDB and type(DB.GetItem) == "function") then return out end
  local order = {}
  for id in pairs(ids) do order[#order + 1] = id end
  table.sort(order)
  for _, id in ipairs(order) do
    if done >= 500 then break end
    local ok, item = pcall(DB.GetItem, DB, id)
    if ok and type(item) == "table" and type(item.Sources) == "table" and #item.Sources > 0 then
      done = done + 1
      local vendorIds, dropIds, objectIds = {}, {}, {}
      for _, source in ipairs(item.Sources) do
        if type(source) == "table" and type(source.Id) == "number" then
          local list = source.Type == "object" and objectIds or source.Vendor and vendorIds or dropIds
          list[#list + 1] = source.Id
        end
      end
      local entry = { id = id, name = item.name or knownName(id), vendors = {}, drops = {}, objects = {},
                      dropCount = #dropIds, objectCount = #objectIds }
      people(entry.vendors, "npc", vendorIds, DB, ZoneDB, 4)
      people(entry.drops, "npc", dropIds, DB, ZoneDB, 5)
      people(entry.objects, "object", objectIds, DB, ZoneDB, 3)
      out[#out + 1] = entry
    end
  end
  return out
end

-- Gathering nodes, from the GatherMate2 addon when it is installed: every herb, vein, pool and
-- chest it knows, whether the player found it or imported it. Read through its own functions;
-- only the list of zones comes from its saved tables, which is where it keeps them.
-- Returns { total = n, kinds = { { kind = "Mining", zones = { { map = id, zone = name,
--           nodes = { { name = "Copper Vein", at = { x, y, x, y, ... } } } } } } } }, x and y in 0-10000.
-- ponytail: everything it knows, every logout, up to 80000 nodes; hand over only what changed
-- if a full import makes logouts slow
local GATHER_TABLES = { ["Herb Gathering"] = "Herb", ["Mining"] = "Mine", ["Fishing"] = "Fish", ["Treasure"] = "Treasure",
                        ["Extract Gas"] = "Gas", ["Archaeology"] = "Archaeology", ["Logging"] = "Logging" }
local function gathering()
  local G = _G.GatherMate2
  if type(G) ~= "table" or type(G.db_types) ~= "table" or type(G.GetNodesForZone) ~= "function"
     or type(G.GetNameForNode) ~= "function" or type(G.DecodeLoc) ~= "function" then return nil end
  local kinds, total, cut = {}, 0, false
  for _, kind in ipairs(G.db_types) do
    local saved = GATHER_TABLES[kind] and _G["GatherMate2" .. GATHER_TABLES[kind] .. "DB"]
    local zones = {}
    for map in pairs(type(saved) == "table" and saved or {}) do
      if type(map) == "number" and not cut then
        local byName = {}
        for coord, nodeID in G:GetNodesForZone(map, kind, true) do
          if total >= 80000 then cut = true; break end
          local name = type(coord) == "number" and G:GetNameForNode(kind, nodeID)
          if type(name) == "string" then
            local x, y = G:DecodeLoc(coord)
            local list = byName[name]
            if not list then list = {}; byName[name] = list end
            local n = #list
            list[n + 1], list[n + 2] = math.floor(x * 10000 + 0.5), math.floor(y * 10000 + 0.5)
            total = total + 1
          end
        end
        local nodes = {}
        for name, at in pairs(byName) do nodes[#nodes + 1] = { name = name, at = at } end
        table.sort(nodes, function(a, b) return a.name < b.name end)
        if #nodes > 0 then zones[#zones + 1] = { map = map, zone = mapName(map), nodes = nodes } end
      end
    end
    table.sort(zones, function(a, b) return a.map < b.map end)
    if #zones > 0 then kinds[#kinds + 1] = { kind = kind, zones = zones } end
  end
  return { total = total, cut = cut or nil, kinds = kinds }
end

---------------------------------------------------------------- loot ----
-- What the character really loots, added up per day, zone and source (a kind of creature, a
-- kind of node): how many were looted and what came out. Nothing in the game or its addons
-- has drop rates for ordinary loot, so Wisp measures the player's own. Kept for 60 days.
--   WispData.loot[character][day]["m" .. map] = { zone, secs, last, copper,
--       src = { ["n" .. npc id | "o" .. object id] = { name, n, items = { ["i" .. item id] = count } } } }
-- "secs" is time spent looting: the gaps between one loot and the next, when under five minutes.

local lootSeen, lootSeenCount, moneyBefore, lastCast = {}, 0, nil, nil

local function lootPlace()
  local map = C_Map.GetBestMapForUnit("player")
  if not map then return nil end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.loot = WispData.loot or {}
  local mine = WispData.loot[myKey()] or {}
  WispData.loot[myKey()] = mine
  local day = "d" .. math.floor(time() / 86400)
  mine[day] = mine[day] or {}
  local place = mine[day]["m" .. map] or { zone = mapName(map), secs = 0, copper = 0, src = {} }
  mine[day]["m" .. map] = place
  return place
end

local function lootSource(guid)   -- "Creature-0-1-2-3-<npc id>-<spawn>" -> "n<npc id>"
  local kind, _, _, _, _, id = strsplit("-", guid)
  id = tonumber(id)
  if not id then return nil end
  if kind == "GameObject" then return "o" .. id end
  if kind == "Creature" or kind == "Vehicle" then return "n" .. id end
  return nil
end

local function lootSourceName(guid, key)
  if key:sub(1, 1) == "o" then   -- a node: the gathering spell was just cast at it by name
    return lastCast and time() - lastCast.at <= 20 and lastCast.name or nil
  end
  for _, unit in ipairs({ "target", "mouseover" }) do
    if UnitGUID and UnitGUID(unit) == guid then return (UnitName(unit)) end
  end
  return nil
end

local function lootOpened()
  if not (GetNumLootItems and GetLootSlotLink and GetLootSourceInfo) then return end
  local place = lootPlace()
  if not place then return end
  local now = time()
  if place.last and now - place.last <= 300 then place.secs = place.secs + (now - place.last) end
  place.last = now
  moneyBefore = GetMoney()
  if lootSeenCount > 6000 then lootSeen, lootSeenCount = {}, 0 end
  for slot = 1, GetNumLootItems() do
    local link = GetLootSlotLink(slot)
    local item = link and tonumber(link:match("item:(%d+)"))
    local sources = { GetLootSourceInfo(slot) }   -- guid, how many from it, guid, how many, ...
    for i = 1, #sources, 2 do
      local guid, count = sources[i], tonumber(sources[i + 1]) or 0
      local key = type(guid) == "string" and lootSource(guid)
      if key then
        local source = place.src[key] or { n = 0, items = {} }
        place.src[key] = source
        source.name = source.name or lootSourceName(guid, key)
        if not lootSeen[guid] then   -- a corpse or a node counts once, however often its window opens
          lootSeen[guid], lootSeenCount = true, lootSeenCount + 1
          source.n = source.n + 1
        end
        if item and count > 0 and not lootSeen[guid .. ":" .. slot .. ":" .. item] then
          lootSeen[guid .. ":" .. slot .. ":" .. item], lootSeenCount = true, lootSeenCount + 1
          source.items["i" .. item] = (source.items["i" .. item] or 0) + count
          knownName(item)
        end
      end
    end
  end
end

local function lootClosed()
  if not moneyBefore then return end
  local gained = GetMoney() - moneyBefore   -- nothing else can pay out while the loot window is open
  moneyBefore = nil
  local place = gained > 0 and lootPlace()
  if place then place.copper = place.copper + gained end
end

local function pruneLoot()
  local mine = type(WispData) == "table" and type(WispData.loot) == "table" and WispData.loot[myKey()]
  local oldest = math.floor(time() / 86400) - 60
  for day in pairs(type(mine) == "table" and mine or {}) do
    if (tonumber(tostring(day):sub(2)) or 0) < oldest then mine[day] = nil end
  end
end

-- The loot record for the snapshot, flattened, with a name for every source where one can be found.
local function lootRecord(wanted, DB)
  local mine = type(WispData) == "table" and type(WispData.loot) == "table" and WispData.loot[myKey()]
  local out = {}
  for day, places in pairs(type(mine) == "table" and mine or {}) do
    for mapKey, place in pairs(places) do
      local sources = {}
      for key, source in pairs(place.src or {}) do
        local items = {}
        for itemKey, count in pairs(source.items or {}) do
          local id = tonumber(itemKey:sub(2))
          if id then items[#items + 1] = { id, count, knownName(id) or "" }; wanted[id] = true end
        end
        table.sort(items, function(a, b) return a[1] < b[1] end)
        local name = source.name
        if not name and DB then   -- not seen by name while looting: Questie may know it
          local ok, record = pcall(key:sub(1, 1) == "o" and DB.GetObject or DB.GetNPC, DB, tonumber(key:sub(2)))
          name = ok and type(record) == "table" and record.name or nil
        end
        sources[#sources + 1] = { key = key, name = name, n = source.n, items = items }
      end
      table.sort(sources, function(a, b) return a.key < b.key end)
      out[#out + 1] = { day = tonumber(day:sub(2)), map = tonumber(mapKey:sub(2)), zone = place.zone,
                        secs = place.secs, copper = place.copper, sources = sources }
    end
  end
  table.sort(out, function(a, b) if a.day ~= b.day then return a.day < b.day end return a.map < b.map end)
  return out
end

-- What a vendor pays for each of these items, where Auctionator has noted it. For loot nobody lists.
local function vendorPrices(ids)
  local api = auctionator()
  if not (api and api.GetVendorPriceByItemID) then return nil end
  local out = {}
  for id in pairs(ids) do
    local ok, price = pcall(api.GetVendorPriceByItemID, "Wisp", id)
    if ok and type(price) == "number" and price > 0 then out[#out + 1] = { id, price } end
  end
  table.sort(out, function(a, b) return a[1] < b[1] end)
  return out
end

local function myMarket() return tostring(GetRealmName()) .. "-" .. tostring((UnitFactionGroup("player"))) end

-- Auctionator's day-by-day record: for each day it scanned, each item's lowest and highest price
-- and the most it saw listed. It keeps about three weeks; Wisp's overlay keeps everything it is
-- handed, for good. Each logout hands over the days since the last one, plus two for overlap,
-- and then older days not handed over yet: newest first and no more than 60000 rows, so a logout
-- never takes long and a backlog drains over the next few logouts.
-- Returns { day0 = start of its day 0, today = its number for today, oldest = the oldest day
--           handed over so far, days = { { day = n, rows = { item, low, high, listed or -1, item, ... } }, ... } }
local function marketDays()
  local A = Auctionator
  local db = type(A) == "table" and type(A.Database) == "table" and A.Database.db
  local day0 = type(A) == "table" and type(A.Constants) == "table" and A.Constants.SCAN_DAY_0
  if type(db) ~= "table" or type(day0) ~= "number" then return nil end
  local today = math.floor((time() - day0) / 86400)
  local sent = type(WispData) == "table" and type(WispData.marketSent) == "table" and WispData.marketSent[myMarket()]
  if type(sent) ~= "table" then sent = {} end
  local newFrom = type(sent.to) == "number" and sent.to - 2 or today - 60   -- from here on: not handed over yet
  local oldest = type(sent.from) == "number" and sent.from or today + 1     -- before here: not handed over yet
  local counts = {}
  for key, row in pairs(db) do
    if tonumber(key) and type(row) == "table" and type(row.h) == "table" then
      for day in pairs(row.h) do
        local d = tonumber(day)
        if d and d <= today and (d >= newFrom or d < oldest) then counts[d] = (counts[d] or 0) + 1 end
      end
    end
  end
  local take, total, lowest = {}, 0, oldest
  for d = today, today - 60, -1 do
    local n = counts[d]
    if n then
      if total > 0 and total + n > 60000 then break end
      total, take[d] = total + n, true
      if d < lowest then lowest = d end
    end
  end
  local byDay = {}
  for key, row in pairs(db) do
    local id = tonumber(key)
    if id and type(row) == "table" and type(row.h) == "table" then
      for day, high in pairs(row.h) do
        local d = tonumber(day)
        if d and take[d] and type(high) == "number" then
          local list = byDay[d]
          if not list then list = {}; byDay[d] = list end
          local low = type(row.l) == "table" and type(row.l[day]) == "number" and row.l[day] or high
          local listed = type(row.a) == "table" and type(row.a[day]) == "number" and row.a[day] or -1
          local n = #list
          list[n + 1], list[n + 2], list[n + 3], list[n + 4] = id, low, high, listed
        end
      end
    end
  end
  local days = {}
  for d, list in pairs(byDay) do days[#days + 1] = { day = d, rows = list } end
  table.sort(days, function(a, b) return a.day < b.day end)
  return { day0 = day0, today = today, oldest = math.min(lowest, today), days = days }
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
  local okD, journal = pcall(dungeons, DB)   -- one addon's surprise must not cost the whole snapshot
  if not okD then journal = nil end
  local okP, skills = pcall(professions)
  if not okP then skills = {} end
  local wanted = {}   -- items worth a price: what is carried and what the recipes use and make
  local okB, carried = pcall(bags)
  if not okB then carried = {} end
  for _, line in ipairs(carried) do wanted[line.id] = true end
  local okC, books = pcall(crafting, wanted)
  if not okC then books = {} end
  local log = questLog(DB, ZoneDB)
  local okQ, open = pcall(availableQuests, DB, ZoneDB)
  if not okQ then open = nil end
  for _, quest in ipairs(log) do
    for _, need in ipairs(quest.needs or {}) do wanted[need.id] = true end
  end
  for _, dungeon in ipairs(journal or {}) do
    for _, quest in ipairs(dungeon.quests) do
      for _, need in ipairs(quest.items or {}) do wanted[need.id] = true end
    end
  end
  local okL, looted = pcall(lootRecord, wanted, DB)
  if not okL then looted = {} end
  local okS, sources = pcall(itemSources, wanted, DB, ZoneDB)   -- before every priced item joins the list
  if not okS then sources = {} end
  local okV, vendor = pcall(vendorPrices, wanted)
  if not okV then vendor = nil end
  local okA, market = pcall(prices, wanted)
  if not okA then market = nil end
  local okM, record = pcall(marketDays)
  if not okM then record = nil end
  local okG, nodes = pcall(gathering)
  if not okG then nodes = nil end
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
               dungeonjournal = journal ~= nil, auctionator = auctionator() ~= nil, gathermate = nodes ~= nil },
    dungeons = journal,
    bags = carried,
    recipes = books,
    prices = market,
    market = record,
    gather = nodes,
    loot = looted,
    vendor = vendor,
    quests = log,
    available = open,
    sources = sources,
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
frame:RegisterEvent("AUCTION_HOUSE_CLOSED")
frame:RegisterEvent("LOOT_OPENED")
frame:RegisterEvent("LOOT_CLOSED")
frame:RegisterEvent("UNIT_SPELLCAST_SENT")
frame:SetScript("OnEvent", function(_, event, ...)
  if event == "PLAYER_LOGOUT" then
    local snapshot = save()
    if snapshot and snapshot.market then   -- these days are now handed over
      if type(WispData) ~= "table" then WispData = {} end
      WispData.marketSent = WispData.marketSent or {}
      WispData.marketSent[myMarket()] = { to = snapshot.market.today, from = snapshot.market.oldest }
    end
  elseif event == "AUCTION_HOUSE_CLOSED" then
    pcall(learnNames)   -- a scan has just shown the game these items
  elseif event == "LOOT_OPENED" then
    pcall(lootOpened)
  elseif event == "LOOT_CLOSED" then
    pcall(lootClosed)
  elseif event == "UNIT_SPELLCAST_SENT" then
    local unit, target = ...
    if unit == "player" and type(target) == "string" and target ~= "" then lastCast = { name = target, at = time() } end
  elseif event ~= "PLAYER_LOGIN" then
    pcall(captureRecipes)
  else
    C_Timer.After(20, function() pcall(learnNames) end)   -- once the login rush is over
    pcall(pruneLoot)
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
