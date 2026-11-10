-- Runs the real addon/Wisp/Wisp.lua against a faked game, Questie and TomTom.
-- Pure Lua, so it runs under real Lua 5.1 (what the game uses) and under the
-- fengari VM alike. Used by tests/test_mvp.py.
--   lua tests/wow_stub.lua snapshot          -> prints the saved WispSnapshot
--   lua tests/wow_stub.lua paste <message>   -> prints what TomTom and chat received
--   WISP_STUB=noquestie,notomtom,nojournal,noauctionator,nogathermate removes those addons;
--   noprofession leaves the profession window closed

local mode, ARG = arg[1], arg[2] or ""
local flags = os.getenv("WISP_STUB") or ""
local here = (arg[0] or "tests/wow_stub.lua"):match("^(.*)[/\\][^/\\]*$") or "."

local LOG, FRAMES, TIMERS, WAYS, LISTS = {}, {}, {}, {}, {}
local out = print
print = function(s) LOG[#LOG + 1] = (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
time = os.time

local function widget()
  return setmetatable({ scripts = {}, text = "" }, { __index = function(_, k)
    if k == "SetScript" then return function(self, n, f) self.scripts[n] = f end end
    if k == "CreateFontString" then return widget end
    if k == "SetText" then return function(self, s) self.text = s end end
    if k == "GetText" then return function(self) return self.text end end
    return function() end
  end })
end
function CreateFrame(_, name)
  local f = widget()
  FRAMES[#FRAMES + 1] = f
  if name then _G[name] = f end
  return f
end
UIParent, SlashCmdList = widget(), {}
C_Timer = { After = function(_, f) TIMERS[#TIMERS + 1] = f end }
local function runTimers() while #TIMERS > 0 do table.remove(TIMERS, 1)() end end
function SetOverrideBindingClick() end

-- the client's JSON and base64, re-implemented for the test
local b = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local function b64enc(d)
  return ((d:gsub(".", function(x)
    local r, c = "", x:byte()
    for i = 8, 1, -1 do r = r .. (c % 2 ^ i - c % 2 ^ (i - 1) > 0 and "1" or "0") end
    return r
  end) .. "0000"):gsub("%d%d%d?%d?%d?%d?", function(x)
    if #x < 6 then return "" end
    local c = 0
    for i = 1, 6 do c = c + (x:sub(i, i) == "1" and 2 ^ (6 - i) or 0) end
    return b:sub(c + 1, c + 1)
  end) .. ({ "", "==", "=" })[#d % 3 + 1])
end
local function b64dec(d)
  d = d:gsub("[^" .. b .. "=]", "")
  return (d:gsub(".", function(x)
    if x == "=" then return "" end
    local r, f = "", (b:find(x, 1, true) - 1)
    for i = 6, 1, -1 do r = r .. (f % 2 ^ i - f % 2 ^ (i - 1) > 0 and "1" or "0") end
    return r
  end):gsub("%d%d%d?%d?%d?%d?%d?%d?", function(x)
    if #x ~= 8 then return "" end
    local c = 0
    for i = 1, 8 do c = c + (x:sub(i, i) == "1" and 2 ^ (8 - i) or 0) end
    return string.char(math.floor(c))
  end))
end
local function number(v)   -- 12 not 12.0, 48.9 as is, on every Lua version
  if v == math.floor(v) then return string.format("%d", v) end
  return tostring(v)
end
local jsonEscapes = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
local jsonUnescapes = { n = "\n", r = "\r", t = "\t" }
local function enc(v)
  local t = type(v)
  if t == "table" then
    local parts = {}
    if #v > 0 or next(v) == nil then
      for _, x in ipairs(v) do parts[#parts + 1] = enc(x) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, x in pairs(v) do parts[#parts + 1] = '"' .. tostring(k) .. '":' .. enc(x) end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "string" then
    return '"' .. v:gsub('[%c"\\]', function(c)
      return jsonEscapes[c] or string.format("\\u%04x", c:byte())
    end) .. '"'
  elseif t == "number" then
    return number(v)
  end
  return tostring(v)
end
local function dec(s)
  local i = 1
  local function ws() i = s:find("%S", i) or #s + 1 end
  local val
  local function str()
    local j, o = i + 1, {}
    while true do
      local c = s:sub(j, j)
      if c == "" then error("bad json") end
      if c == '"' then break end
      if c == "\\" then
        j = j + 1
        c = s:sub(j, j)
        if c == "u" then c = string.char(tonumber(s:sub(j + 1, j + 4), 16) % 256); j = j + 4 else c = jsonUnescapes[c] or c end
      end
      o[#o + 1] = c
      j = j + 1
    end
    i = j + 1
    return table.concat(o)
  end
  function val()
    ws()
    local c = s:sub(i, i)
    if c == "{" then
      local t = {}
      i = i + 1; ws()
      if s:sub(i, i) == "}" then i = i + 1; return t end
      while true do
        ws()
        local k = str()
        ws(); i = i + 1
        t[k] = val()
        ws()
        local d = s:sub(i, i); i = i + 1
        if d == "}" then return t elseif d ~= "," then error("bad json") end
      end
    elseif c == "[" then
      local t = {}
      i = i + 1; ws()
      if s:sub(i, i) == "]" then i = i + 1; return t end
      while true do
        t[#t + 1] = val()
        ws()
        local d = s:sub(i, i); i = i + 1
        if d == "]" then return t elseif d ~= "," then error("bad json") end
      end
    elseif c == '"' then
      return str()
    elseif s:sub(i, i + 3) == "true" then i = i + 4; return true
    elseif s:sub(i, i + 4) == "false" then i = i + 5; return false
    end
    local n = s:match("^-?[%d.eE+-]+", i)
    if not n then error("bad json") end
    i = i + #n
    return tonumber(n)
  end
  return val()
end
C_EncodingUtil = { SerializeJSON = enc, DeserializeJSON = dec, EncodeBase64 = b64enc, DecodeBase64 = b64dec }
local SAVED   -- with bigmarket the snapshot is kept as a table: these encoders are far too slow for 60000 rows
if flags:find("bigmarket") then
  C_EncodingUtil.SerializeJSON = function(t) SAVED = t; return "{}" end
end

-- the character: a level 12 human in Elwynn with two quests
function GetBuildInfo() return "1.60.1", "70862", "Nov 1 2026", 16001 end
function GetLocale() return "ruRU" end
function UnitClass() return "Маг", "MAGE" end
function UnitRace() return "Человек", "Human" end
function UnitLevel() return 12 end
function UnitName() return "Тестер" end
function GetRealmName() return "Test Realm" end
function UnitXP() return 4200 end
function UnitXPMax() return 10000 end
function GetProfessions() return 4, nil, nil, 7 end   -- with empty slots, as the game returns them
function GetProfessionInfo(i) if i == 4 then return "Tailoring", nil, 150, 225 end return "Fishing", nil, 31, 75 end
function UnitFactionGroup() return "Alliance", "Альянс" end
function GetMoney() return 153422 end
function GetSubZoneText() return "Златоземье" end
local maps = { [1429] = "Elwynn Forest", [1453] = "Stormwind City", [1436] = "Westfall", [1455] = "Ironforge" }
C_Map = {
  GetBestMapForUnit = function() return 1429 end,
  GetPlayerMapPosition = function() return { GetXY = function() return 0.4212, 0.6588 end } end,
  GetMapInfo = function(m) return maps[m] and { name = maps[m] } end,
  CanSetUserWaypointOnMap = function() return true end,
  SetUserWaypoint = function(p) WAYS[#WAYS + 1] = "pin " .. p.map .. " " .. p.x .. " " .. p.y end,
}
UiMapPoint = { CreateFromCoordinates = function(m, x, y) return { map = m, x = x, y = y } end }
C_SuperTrack = { SetSuperTrackedUserWaypoint = function() end }
local log = {
  { title = "Elwynn Forest", isHeader = true },
  { title = "Wolves Across the Border", questID = 33, level = 7 },
  { title = "The Jasperlode Mine", questID = 76, level = 10 },
  { title = "Hidden", questID = 999, isHidden = true },
}
C_QuestLog = {
  GetNumQuestLogEntries = function() return #log end,
  GetInfo = function(i) return log[i] end,
  IsComplete = function(id) return id == 33 end,
  GetQuestObjectives = function(id)
    if id == 33 then return { { text = "8/8 Tough Wolf Meat", finished = true } } end
    return { { text = "Scout through the Jasperlode Mine", finished = false } }
  end,
  GetNextWaypoint = function(id) if id == 76 then return 1429, 0.615, 0.538 end end,
  GetAllCompletedQuestIDs = function() return { 7, 15, 21, 54, 62 } end,
}

-- Questie, in the shape its own source gives these records: quest 33 starts and
-- ends at Eagan (npc 196) and leads to quest 34, given by a wanted poster
local quests = {
  [33] = { name = "Wolves Across the Border", level = 7, requiredLevel = 2, nextQuestInChain = 34,
           Starts = { NPC = { 196 } }, Finisher = { NPC = { 196 } },
           ObjectiveData = { { Type = "monster", Id = 118 }, { Type = "item", Id = 750, Text = "Tough Wolf Meat" } } },
  [214] = { name = "Red Silk Bandanas", ObjectiveData = { { Type = "item", Id = 915, Text = "Red Silk Bandana" } } },
  [34] = { name = "An Unwelcome Guest", level = 9, requiredLevel = 5, nextQuestInChain = 0,
           Starts = { GameObject = { 55 } }, Finisher = { NPC = { 196 } } },
  [76] = { name = "The Jasperlode Mine", level = 10, requiredLevel = 7,
           Starts = { NPC = { 240 } }, Finisher = { NPC = { 240 } } },
}
local npcs = {
  [196] = { name = "Eagan |cffff0000Peltskinner", spawns = { [12] = { { 48.94, 40.16 } } } },
  [240] = { name = "Marshal Dughan", spawns = { [12] = { { 42.1, 65.9 } }, [1519] = { { -1, -1 } } } },
}
local objects = { [55] = { name = "Wanted Poster", spawns = { [12] = { { 24.6, 74.7 } } } },
                  [77] = { name = "Silk Crate", spawns = { [12] = { { 51, 52 } } } },
                  [1731] = { name = "Copper Vein", spawns = { [12] = { { 40, 80 } } } } }
npcs[1347] = { name = "Alexandra Bolero", spawns = { [1519] = { { 43.2, 73.9 } } } }
npcs[118] = { name = "Prowler", spawns = { [12] = { { 30, 60 } } } }
for id = 100, 106 do npcs[id] = { name = "Defias Weaver " .. id, spawns = { [12] = { { id - 60, 55 } } } } end
-- where items come from: sold by a vendor, dropped by creatures, found in an object
local function from(vendors, drops, things)
  local sources = {}
  for _, id in ipairs(vendors) do sources[#sources + 1] = { Id = id, Type = "monster", Vendor = true } end
  for _, id in ipairs(drops) do sources[#sources + 1] = { Id = id, Type = "monster" } end
  for _, id in ipairs(things) do sources[#sources + 1] = { Id = id, Type = "object" } end
  return sources
end
local questieItems = {
  [2321] = { name = "Fine Thread", Sources = from({ 1347 }, {}, {}) },
  [6260] = { name = "Blue Dye", Sources = from({ 1347 }, {}, {}) },
  [4306] = { name = "Silk Cloth", Sources = from({}, { 100, 101, 102, 103, 104, 105, 106 }, { 77 }) },
  [750] = { name = "Tough Wolf Meat", Sources = from({}, { 118 }, {}) },
  [6948] = { name = "Hearthstone", Sources = {} },
  [2770] = { name = "Copper Ore", Sources = from({}, {}, { 1731 }) },
}
local QuestieDB = {
  GetItem = function(_, id) return questieItems[id] end,
  GetQuest = function(id) return quests[id] end,
  GetNPC = function(_, id) return npcs[id] end,
  GetObject = function(_, id) return objects[id] end,
}
local ZoneDB = { GetUiMapIdByAreaId = function(_, area) return ({ [12] = 1429, [1519] = 1453 })[area] end }
if not flags:find("noquestie") then
  QuestieLoader = { ImportModule = function(_, name) return name == "QuestieDB" and QuestieDB or ZoneDB end }
end
-- DungeonJournal, in the shape of its own tables (made-up entries, not its data)
local completedIds = { [65] = true, [132] = true }
C_QuestLog.IsQuestFlaggedCompleted = function(id) return completedIds[id] == true end
C_QuestLog.GetLogIndexForQuestID = function(id) if id == 33 or id == 76 then return 2 end end
if not flags:find("nojournal") then
  ForeverDungeonJournal_NS = {
    ORDER = { "Hall of Thanes", "The Deadmines" },
    DB = {
      ["Hall of Thanes"] = {
        level = "13-20", location = "Beneath Ironforge, Dun Morogh", description = "x",
        entrance = { mapID = 1455, x = 0.5, y = 0.25, label = "Hall of Thanes Entrance", detail = "Behind the forge", markerType = "dungeon" },
        quests = {
          { id = 96395, name = "An Ancient Grudge", faction = "Both", level = 15, requires = 10, objective = "Put the spirit to rest.",
            pickup = "Ghostly Attendant, inside", turnin = "Ghostly Attendant", rewards = "Choose: Cloak or Trousers", startsInDungeon = true },
          { id = 96400, name = "For the Horde Only", faction = "Horde", level = 15, requires = 10, objective = "x", pickup = "x", turnin = "x" },
          { id = 96401, name = "Hidden Step", faction = "Both", level = 15, requires = 10, hideFromMainList = true, objective = "x", pickup = "x", turnin = "x" },
        },
        bosses = { { name = "Faldrim Anvilmar", npcID = 1, loot = { { 279899, "Catacomb Cloak", "Back", 3 }, { tip = {} } } } },
      },
      ["The Deadmines"] = {
        level = "17-26", location = "Moonbrook, Westfall", description = "x",
        entrance = { mapID = 1436, x = 0.425, y = 0.717, label = "The Deadmines Entrance", detail = "Dungeon entrance in Moonbrook" },
        quests = {
          { id = 214, name = "Red Silk Bandanas", faction = "Alliance", level = 17, requires = 14, objective = "Collect 10 bandanas.",
            pickup = "Scout Riell, Sentinel Hill tower", turnin = "Scout Riell", rewardSummary = "7s" },
          { id = 168, name = "Collecting Memories", faction = "Alliance", level = 18, requires = 14, objective = "Collect 4 cards.",
            pickup = "Wilder Thistlenettle, Stormwind", turnin = "Wilder Thistlenettle", classOnly = "Warrior",
            startMap = { mapID = 1453, x = 0.655, y = 0.212, label = "Wilder Thistlenettle — Stormwind", targetName = "Wilder Thistlenettle" } },
          { id = 76, name = "The Jasperlode Mine", faction = "Both", level = 10, requires = 7, objective = "x", pickup = "x", turnin = "x" },
          { id = 132, name = "Already Done", faction = "Both", level = 20, requires = 30, objective = "x", pickup = "x", turnin = "x" },
        },
        bosses = {
          { name = "Rhahk'Zor", npcID = 644, loot = { { 872, "Rockslicer", "Two-Hand, Axe", 3 }, { 5187, "Rhahk'Zor's Hammer", "Two-Hand, Mace", 3 } } },
          { name = "Miner Johnson", npcID = 3586, rare = true, loot = {} },
        },
      },
    },
    QUEST_PREREQ_CHAINS = { [214] = { { id = 65, name = "The Defias Brotherhood" }, { id = 132, name = "The Defias Brotherhood" },
                                      { id = 155, name = "The Defias Brotherhood" } } },
    QUEST_PREREQ_DETAILS = { [155] = { pickup = "Defias Traitor, Sentinel Hill", level = 18, requires = 14,
                                       map = { mapID = 1436, x = 0.557, y = 0.475, label = "Defias Traitor — Westfall", targetName = "Defias Traitor" } } },
    QUEST_START_MAPS = { [214] = { mapID = 1436, x = 0.566, y = 0.473, label = "Scout Riell — Westfall", targetName = "Scout Riell" } },
  }
end
-- The profession window: Tailoring at 150. Made-up recipes in the shape the client gives them.
local itemNames = { [4306] = "Silk Cloth", [4305] = "Bolt of Silk Cloth", [6260] = "Blue Dye", [2321] = "Fine Thread",
                    [7048] = "Azure Silk Hood", [6948] = "Hearthstone", [4245] = "Small Silk Pack" }   -- 4234 is not cached
-- 2996 is an item the game has not loaded yet: its name comes only after it is asked for
local late, asked = { [2996] = "Bolt of Linen Cloth" }, {}
C_Item = {
  GetItemNameByID = function(id) return itemNames[id] or (asked[id] and late[id]) or nil end,
  RequestLoadItemDataByID = function(id) asked[id] = true end,
}
Enum = { CraftingReagentType = { Modifying = 0, Basic = 1, Finishing = 2 } }
local function slot(id, n, kind) return { reagents = { { itemID = id } }, quantityRequired = n, reagentType = kind or 1, required = true } end
local recipes = {
  [3839] = { info = { name = "Bolt of Silk Cloth", learned = true, relativeDifficulty = 1, maxTrivialLevel = 170, numSkillUps = 1 },
             plan = { outputItemID = 4305, quantityMin = 1, quantityMax = 1, reagentSlotSchematics = { slot(4306, 4) } } },
  [8760] = { info = { name = "Azure Silk Hood", learned = true, relativeDifficulty = 0, maxTrivialLevel = 185, numSkillUps = 1 },
             plan = { outputItemID = 7048, quantityMin = 1, quantityMax = 1,
                      reagentSlotSchematics = { slot(4305, 2), slot(6260, 2), slot(2321, 1), slot(99999, 1, 2) } } },
  [3813] = { info = { name = "Small Silk Pack", learned = true, relativeDifficulty = 3, maxTrivialLevel = 140, numSkillUps = 1 },
             plan = { outputItemID = 4245, quantityMin = 1, quantityMax = 1, reagentSlotSchematics = { slot(4305, 3), slot(4234, 2) } } },
  [8791] = { info = { name = "Not Learned Yet", learned = false, maxTrivialLevel = 200, numSkillUps = 1 },
             plan = { outputItemID = 7049, quantityMin = 1, quantityMax = 1, reagentSlotSchematics = { slot(4305, 5) } } },
  [1] = { info = { name = "A header, not a recipe", isDummyRecipe = true }, plan = {} },
}
C_TradeSkillUI = {
  IsTradeSkillReady = function() return true end,
  IsTradeSkillLinked = function() return false end,
  GetBaseProfessionInfo = function() return { professionName = "Tailoring", skillLevel = 150, maxSkillLevel = 225 } end,
  GetFilteredRecipeIDs = function() return { 1, 3839, 8760, 3813, 8791 } end,
  GetRecipeInfo = function(id) return recipes[id].info end,
  GetRecipeSchematic = function(id) return recipes[id].plan end,
}
local bagSlots = { [0] = { { itemID = 4306, stackCount = 20, itemName = "Silk Cloth" }, { itemID = 4306, stackCount = 12, itemName = "Silk Cloth" },
                           false, { itemID = 6948, stackCount = 1, itemName = "Hearthstone" } },
                   [1] = { { itemID = 2321, stackCount = 3 }, { itemID = 2770, stackCount = 4, itemName = "Copper Ore" } } }
C_Container = {
  GetContainerNumSlots = function(bag) return bagSlots[bag] and #bagSlots[bag] or 0 end,
  GetContainerItemInfo = function(bag, i) return bagSlots[bag][i] or nil end,
}
-- Auctionator's public API, as its source has it: every call checks who is calling
if not flags:find("noauctionator") then
  local function caller(id) if type(id) ~= "string" or id == "" then error("Invalid callerID. Use the name of your add-on.") end end
  -- copper, days since seen. 2589 is nothing this character carries or crafts; 31 was last seen too long ago to say
  local seen = { [4306] = { 450, 0 }, [4305] = { 2100, 3 }, [7048] = { 9000, 1 }, [2589] = { 120, 2 }, [2996] = { 300, 0 }, [31] = { 77 } }
  itemNames[2589], itemNames[31] = "Linen Cloth", "Old Thing"
  -- its own price table, keyed by text: item ids, and other keys for pets and item-level variants
  local db = { ["g:1234:20"] = { m = 5, h = {} }, ["p:77"] = { m = 9, h = {} } }
  for id, row in pairs(seen) do db[tostring(id)] = { m = row[1], h = {}, l = {}, a = {} } end
  -- its day-by-day record: day numbers count from the start of 2020, as text keys
  local day0 = os.time({ year = 2020, month = 1, day = 1, hour = 0 })
  local today = math.floor((os.time() - day0) / 86400)
  local function day(id, ago, low, high, listed)
    local row, key = db[tostring(id)], tostring(today - ago)
    row.h[key] = high
    if low < high then row.l[key] = low end
    row.a[key] = listed
  end
  day(4306, 0, 450, 470, 120); day(4306, 1, 430, 430, 80); day(4306, 5, 380, 400, 200); day(4306, 70, 300, 300, 10)
  day(2589, 2, 120, 130, 900)
  db["4305"].h[tostring(today - 3)], db["4305"].a = 2100, nil   -- an old record without quantities
  if flags:find("bigmarket") then   -- more than one logout may carry: 4000 items scanned on each of 21 days
    for id = 100000, 103999 do
      local row = { m = id, h = {}, l = {}, a = {} }
      for ago = 0, 20 do row.h[tostring(today - ago)], row.a[tostring(today - ago)] = id + ago, 40 end
      db[tostring(id)] = row
    end
  end
  if flags:find("handedover") then WispData = { marketSent = { ["Test Realm-Alliance"] = { to = today - 1, from = today - 4 } } } end
  if flags:find("handedback") then WispData = { marketSent = { ["Test Realm-Alliance"] = { to = today, from = today - 4 } } } end
  Auctionator = { Constants = { SCAN_DAY_0 = day0 }, Database = { db = db }, API = { v1 = {
    GetAuctionPriceByItemID = function(id, item) caller(id); return seen[item] and seen[item][1] end,
    GetAuctionAgeByItemID = function(id, item) caller(id); return seen[item] and seen[item][2] end,
    ConvertToSearchString = function(id, term)
      caller(id)
      return (term.isExact and '"' .. term.searchString .. '"' or term.searchString) .. ";" .. tostring(term.quantity)
    end,
    CreateShoppingList = function(id, name, searches) caller(id); LISTS[#LISTS + 1] = name .. " = " .. table.concat(searches, " + ") end,
  } } }
end
-- GatherMate2, as its source has it: one saved table per profession, zone -> { encoded place = node id },
-- read through its own functions. Made-up nodes.
if not flags:find("nogathermate") then
  -- x, y in 0-100. As floats: the game's numbers are, and the test's other Lua VM has 32-bit whole numbers
  local function at(x, y) return math.floor(x * 100 + 0.5) * 1000000.0 + math.floor(y * 100 + 0.5) * 100.0 end
  local function nodes(id, ...)
    local out, places = {}, { ... }
    for i = 1, #places, 2 do out[at(places[i], places[i + 1])] = id end
    return out
  end
  local function join(a, b) for k, v in pairs(b) do a[k] = v end return a end
  GatherMate2HerbDB = {
    [1429] = join(nodes(401, 30, 60, 31, 61, 30.5, 60.5, 45, 62, 60, 40, 61, 41, 75, 70), nodes(402, 20, 20, 22, 21)),   -- Peacebloom, Silverleaf
    [1436] = nodes(401, 50, 50, 52, 48),
  }
  GatherMate2MineDB = { [1429] = nodes(201, 40, 80, 41, 81, 70, 30, 71, 31, 55, 55) }   -- Copper Vein
  GatherMate2FishDB = {}
  local names = { ["Herb Gathering"] = { [401] = "Peacebloom", [402] = "Silverleaf" }, ["Mining"] = { [201] = "Copper Vein" }, ["Fishing"] = {} }
  local tables = { ["Herb Gathering"] = GatherMate2HerbDB, ["Mining"] = GatherMate2MineDB, ["Fishing"] = GatherMate2FishDB }
  GatherMate2 = {
    db_types = { "Herb Gathering", "Mining", "Fishing" },
    GetNodesForZone = function(_, zone, kind) return pairs(tables[kind][zone] or {}) end,
    GetNameForNode = function(_, kind, id) return names[kind][id] end,
    DecodeLoc = function(_, id) return math.floor(id / 1000000) / 10000, math.floor(id % 1000000 / 100) / 10000 end,
  }
end
if not flags:find("notomtom") then
  TomTom = { AddWaypoint = function(_, m, x, y, o) WAYS[#WAYS + 1] = "tomtom " .. m .. " " .. x .. " " .. y .. " " .. o.title end }
end

assert(loadfile(here .. "/../addon/Wisp/Wisp.lua"))()

if mode == "snapshot" then
  local boot
  for _, f in ipairs(FRAMES) do if f.scripts.OnEvent then boot = f end end
  boot.scripts.OnEvent(boot, "PLAYER_LOGIN")
  if flags:find("visitauction") then   -- names are learned after the auction house closes, a pass at a time
    boot.scripts.OnEvent(boot, "AUCTION_HOUSE_CLOSED")
    runTimers()
  end
  if not flags:find("noprofession") then
    boot.scripts.OnEvent(boot, "TRADE_SKILL_SHOW")
    boot.scripts.OnEvent(boot, "TRADE_SKILL_LIST_UPDATE")   -- it comes again and again: said in chat once
  end
  SlashCmdList.WISP("save")
  boot.scripts.OnEvent(boot, "PLAYER_LOGOUT")
  out("SNAPSHOT " .. tostring(WispSnapshot))
  if SAVED then
    local days = {}
    for _, d in ipairs(SAVED.market.days) do days[#days + 1] = number(d.day - SAVED.market.today) .. ":" .. number(#d.rows / 4) end
    out("DAYS " .. table.concat(days, " "))
  end
  for market, upTo in pairs(type(WispData) == "table" and WispData.marketSent or {}) do
    out("SENT " .. market .. " " .. upTo.to - upTo.from)
  end
elseif mode == "paste" then
  SlashCmdList.WISP("")
  WispInbox.text = ARG
  WispInbox.scripts.OnTextChanged(WispInbox, true)
  runTimers()
  for _, w in ipairs(WAYS) do out("WAY " .. w) end
  for _, l in ipairs(LISTS) do out("LIST " .. l) end
  out("BOX [" .. WispInbox.text .. "]")
else
  error("usage: wow_stub.lua snapshot | paste <message>")
end
for _, l in ipairs(LOG) do out("CHAT " .. l) end
out("LUA " .. _VERSION)
