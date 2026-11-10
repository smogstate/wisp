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
local clock = os.time()   -- the game's clock, which the looting below moves forward
time = function() return clock end
function strsplit(sep, text)
  local parts = {}
  for part in (text .. sep):gmatch("(.-)%" .. sep) do parts[#parts + 1] = part end
  return (table.unpack or unpack)(parts)
end

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
function GetLocale() return flags:find("russian") and "ruRU" or "enUS" end   -- the character's name is Russian either way
function UnitClass() return "Маг", "MAGE" end
function UnitRace() return "Человек", "Human" end
function UnitLevel() return 12 end
function GetQuestGreenRange() return 6 end   -- at level 12, a quest of level 6 is still green and one of level 5 is grey
function UnitName() return "Тестер" end
function GetRealmName() return "Test Realm" end
local xpNow, xpMax = 4200, 10000   -- where the bar stands at logout; a session below starts lower and climbs to it
function UnitXP() return xpNow end
function UnitXPMax() return xpMax end
function GetXPExhaustion() return 2500 end
function GetProfessions() return 4, nil, nil, 7 end   -- with empty slots, as the game returns them
function GetProfessionInfo(i) if i == 4 then return "Tailoring", nil, 150, 225 end return "Fishing", nil, 31, 75 end
function UnitFactionGroup() return "Alliance", "Альянс" end
local purse = 153422   -- at logout: the looting below starts 1s 55c short of it
function GetMoney() return purse end
function GetSubZoneText() return "Златоземье" end
local maps = { [1429] = "Elwynn Forest", [1453] = "Stormwind City", [1436] = "Westfall", [1455] = "Ironforge", [1426] = "Dun Morogh" }
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
  -- not picked up yet: Questie has these down as available to this character
  [40] = { name = "A Fishy Peril", level = 10, requiredLevel = 7, Starts = { NPC = { 240 } }, Finisher = { NPC = { 240 } } },
  [176] = { name = "Wanted: Hogger", level = 11, requiredLevel = 6, Starts = { GameObject = { 55 } }, Finisher = { NPC = { 240 } },
            ObjectiveData = { { Type = "item", Id = 1931, Text = "Huge Gnoll Claw" } } },
  [314] = { name = "Protecting the Herd", level = 12, requiredLevel = 5, Starts = { NPC = { 1265 } }, Finisher = { NPC = { 1265 } } },
  [386] = { name = "What Comes Around", level = 25, requiredLevel = 22, Starts = { NPC = { 240 } }, Finisher = { NPC = { 240 } } },
  [84] = { name = "Back to Billy", level = 5, requiredLevel = 1, Starts = { NPC = { 196 } }, Finisher = { NPC = { 196 } } },   -- grey by now
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
npcs[1265] = { name = "Rudra Amberstill", spawns = { [1] = { { 63.1, 49.8 } } } }
local tags = { [176] = "elite", [314] = "elite", [386] = "dungeon" }
QuestieDB.IsGroupQuest = function(id) return tags[id] == "elite" end
QuestieDB.IsDungeonQuest = function(id) return tags[id] == "dungeon" end
QuestieDB.IsRaidQuest = function() return false end
QuestieDB.IsPvPQuest = function() return false end
local questXp = { [33] = 450, [34] = 540, [40] = 620, [76] = 700, [176] = 900, [314] = 800, [386] = 2100, [84] = 60 }
local QuestXP = { GetQuestLogRewardXP = function(_, id) return questXp[id] or 0 end,
                  GetQuestRewardMoney = function(id) return id == 176 and 1500 or 0 end }
local ZoneDB = { GetUiMapIdByAreaId = function(_, area) return ({ [12] = 1429, [1519] = 1453, [1] = 1426 })[area] end }
-- what it draws its map icons from: quest id -> true; 34 follows a quest in the log, 9999 is one it has no record of
local AvailableQuests = { __availableQuests = { [34] = true, [40] = true, [84] = true, [176] = true, [314] = true, [386] = true, [9999] = true } }
if flags:find("oldquestie") then AvailableQuests = {} end
if not flags:find("noquestie") then
  local modules = { QuestieDB = QuestieDB, ZoneDB = ZoneDB, AvailableQuests = AvailableQuests, QuestXP = QuestXP }
  QuestieLoader = { ImportModule = function(_, name) return modules[name] or {} end }
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
                           false, { itemID = 6948, stackCount = 1, itemName = "Hearthstone", isBound = true } },
                   [1] = { { itemID = 2321, stackCount = 3 }, { itemID = 2770, stackCount = 4, itemName = "Copper Ore" } } }
C_Container = {
  GetContainerNumSlots = function(bag) return bagSlots[bag] and #bagSlots[bag] or 0 end,
  GetContainerItemInfo = function(bag, i) return bagSlots[bag][i] or nil end,
}
-- Auctionator's public API, as its source has it: every call checks who is calling
if not flags:find("noauctionator") then
  local function caller(id) if type(id) ~= "string" or id == "" then error("Invalid callerID. Use the name of your add-on.") end end
  -- copper, days since seen. 2589 is nothing this character carries or crafts; 31 was last seen too long ago to say
  local seen = { [4306] = { 450, 0 }, [4305] = { 2100, 3 }, [7048] = { 9000, 1 }, [2589] = { 120, 2 }, [2996] = { 300, 0 }, [31] = { 77 },
                 [2770] = { 80, 0 }, [2447] = { 35, 0 } }
  itemNames[2589], itemNames[31], itemNames[2447] = "Linen Cloth", "Old Thing", "Peacebloom"
  local vendorPays = { [2835] = 2, [750] = 6, [4306] = 100 }   -- what a vendor gives, for loot nobody lists
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
    GetVendorPriceByItemID = function(id, item) caller(id); return vendorPays[item] end,
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
-- Looting. Each call is one loot window: its slots as { item id or nil for coins, { guid, count, ... } }.
itemNames[750], itemNames[2770], itemNames[2835] = "Tough Wolf Meat", "Copper Ore", "Rough Stone"
local lootSlots, targetGuid = {}, nil
function GetNumLootItems() return #lootSlots end
function GetLootSlotLink(slot) return lootSlots[slot][1] and ("|cffffffff|Hitem:" .. lootSlots[slot][1] .. "::::|h[x]|h|r") or nil end
function GetLootSourceInfo(slot) return (table.unpack or unpack)(lootSlots[slot][2]) end
function UnitGUID(unit) return unit == "target" and targetGuid or nil end
local function creature(npc, spawn) return "Creature-0-1-2-3-" .. npc .. "-" .. spawn end
local function lootWindow(boot, after, coins, slots, target, targetName)
  clock = clock + after
  lootSlots, targetGuid = slots, target
  local real = UnitName
  if targetName then UnitName = function(unit) if unit == "target" then return targetName end return real(unit) end end
  boot.scripts.OnEvent(boot, "LOOT_OPENED", true)
  UnitName = real
  purse = purse + coins
  boot.scripts.OnEvent(boot, "LOOT_CLOSED")
end
local function goLooting(boot)
  purse = purse - 155
  local a, b, c, d = creature(118, "A"), creature(118, "B"), creature(100, "C"), creature(100, "D")
  local vein1, vein2 = "GameObject-0-1-2-3-1731-V1", "GameObject-0-1-2-3-1731-V2"
  lootWindow(boot, 0, 35, { { 750, { a, 1 } }, { nil, { a, 0 } } }, a, "Prowler")
  -- three corpses looted at once: cloth from one wolf, silk from two weavers
  lootWindow(boot, 60, 120, { { 2589, { b, 2 } }, { 4306, { c, 1, d, 2 } } })
  lootWindow(boot, 60, 0, { { 2589, { b, 2 } } })   -- the same corpse opened again: nothing new
  boot.scripts.OnEvent(boot, "UNIT_SPELLCAST_SENT", "player", "Copper Vein", "cast-1", 2575)
  lootWindow(boot, 80, 0, { { 2770, { vein1, 2 } }, { 2835, { vein1, 1 } } })
  boot.scripts.OnEvent(boot, "UNIT_SPELLCAST_SENT", "player", "Copper Vein", "cast-2", 2575)
  lootWindow(boot, 800, 0, { { 2770, { vein2, 3 } } })   -- after a long break: that break is not looting time
end
-- The mailbox. Auction mails carry an invoice; three of them say exactly the same (three stacks bought in one go).
local function bought(name, id, count, paid, days) return { "buyer", name, "Bob", paid, paid, 0, 0, count, id = id, days = days } end
local inbox = {
  bought("Silk Cloth", 4306, 20, 6000, 29.9), bought("Silk Cloth", 4306, 20, 6000, 29.9), bought("Silk Cloth", 4306, 20, 6000, 29.9),
  { "seller", "Bolt of Silk Cloth", "Ann", 10000, 10000, 150, 500, 5, days = 29.5 },
  { "seller_temp_invoice", "Bolt of Silk Cloth", "Ann", 10000, 10000, 150, 500, 5, days = 29.9 },   -- "your money is on its way": not a sale yet
  { nil, days = 12 },                                                                              -- a letter from a friend
  bought("Strange Dust", 10940, 10, 2000, 28),
}
function GetInboxNumItems() return #inbox end
function GetInboxInvoiceInfo(i) local m = inbox[i]; return m[1], m[2], m[3], m[4], m[5], m[6], m[7], 0, 0, 0, m[8] end
function GetInboxHeaderInfo(i) return nil, nil, "x", "x", 0, 0, inbox[i].days end
function GetInboxItem(i) local m = inbox[i]; return m[2], m.id, nil, m[8] end
local function openMailbox(boot)
  boot.scripts.OnEvent(boot, "MAIL_SHOW")
  table.remove(inbox, 1)   -- one of the three stacks is taken: the mail goes, the note of it must not
  clock = clock + 40
  for _, m in ipairs(inbox) do m.days = m.days - 40 / 86400 end
  boot.scripts.OnEvent(boot, "MAIL_INBOX_UPDATE")
end
-- A session's worth of experience: two kills, a quest handed in, a long break, another kill.
local function gain(boot, after, to, max)
  clock = clock + after
  xpNow, xpMax = to, max or xpMax
  boot.scripts.OnEvent(boot, "PLAYER_XP_UPDATE", "player")
end
local function goLeveling(boot)
  gain(boot, 0, 3150)
  gain(boot, 60, 3300)
  boot.scripts.OnEvent(boot, "QUEST_TURNED_IN", 21, 450, 300)
  gain(boot, 140, 3750)
  gain(boot, 800, 4200)   -- after a long break: that break is not time spent gaining
  if flags:find("levelup") then gain(boot, 30, 500, 11000) end   -- the bar fills and starts again
end
-- The character's own auctions, as the auction house's Auctions tab lists them.
C_AuctionHouse = { GetOwnedAuctions = function() return {
  { auctionID = 1, itemKey = { itemID = 4306 }, status = 0, quantity = 40, buyoutAmount = 500, timeLeftSeconds = 30000 },    -- dearer than the lowest
  { auctionID = 2, itemKey = { itemID = 4305 }, status = 0, quantity = 5, buyoutAmount = 2000, timeLeftSeconds = 7200, bidder = "Someone" },
  { auctionID = 3, itemKey = { itemID = 7048 }, status = 1, quantity = 1, buyoutAmount = 12000 },                             -- sold, money waiting
  { auctionID = 4, itemKey = { itemID = 2321 }, status = 0, quantity = 10, buyoutAmount = 50, timeLeftSeconds = 100 },        -- nothing known to compare with
} end }
-- The bank: one tab of the character's own (container 6) and one shared by the account (container 15).
bagSlots[6] = { { itemID = 4306, stackCount = 20, itemName = "Silk Cloth" }, { itemID = 6260, stackCount = 15, itemName = "Blue Dye" },
                { itemID = 6948, stackCount = 1, itemName = "Hearthstone", isBound = true } }
bagSlots[15] = { { itemID = 4306, stackCount = 10, itemName = "Silk Cloth" }, { itemID = 2770, stackCount = 30, itemName = "Copper Ore" } }
Enum.BankType = { Character = 0, Guild = 1, Account = 2 }
C_Bank = { FetchPurchasedBankTabData = function(kind) return kind == 0 and { { ID = 6 } } or kind == 2 and { { ID = 15 } } or {} end }
-- A full scan, as it sits in the client afterwards: every single auction, counted from 0.
-- item, how many in the auction, its buyout for the lot.
local replicated = {
  { 4306, 20, 9000 }, { 4306, 20, 9000 }, { 4306, 10, 4550 }, { 4306, 20, 9400 }, { 4306, 5, 2500 }, { 4306, 40, 28000 }, { 4306, 1, 2000 },
  { 2589, 20, 2400 }, { 2589, 20, 2600 },
  { 4305, 1, 0 },   -- bid only, no buyout: nothing to compare
}
C_AuctionHouse.GetNumReplicateItems = function() return #replicated end
C_AuctionHouse.GetReplicateItemInfo = function(i)
  local a = replicated[i + 1]
  return "x", 1, a[2], 1, true, 1, "", 0, 0, a[3], 0, nil, nil, "Owner", nil, 0, a[1], true
end
-- A search for one item: its whole ladder, with the player's own auctions marked.
local searched = { { unitPrice = 2000, quantity = 5 }, { unitPrice = 2050, quantity = 3, containsOwnerItem = true, numOwnerItems = 2 },
                   { unitPrice = 2600, quantity = 12 } }
C_AuctionHouse.GetNumCommoditySearchResults = function() return #searched end
C_AuctionHouse.GetCommoditySearchResultInfo = function(_, i) return searched[i] end
C_AuctionHouse.HasFullCommoditySearchResults = function() return true end
local function visitAuctionHouse(boot)
  boot.scripts.OnEvent(boot, "REPLICATE_ITEM_LIST_UPDATE")              -- a full scan comes in, read a batch at a time
  runTimers()
  boot.scripts.OnEvent(boot, "COMMODITY_SEARCH_RESULTS_UPDATED", 4305)   -- then the player looks one item up
end
if not flags:find("notomtom") then
  TomTom = { AddWaypoint = function(_, m, x, y, o) WAYS[#WAYS + 1] = "tomtom " .. m .. " " .. x .. " " .. y .. " " .. o.title end }
end

assert(loadfile(here .. "/../addon/Wisp/Wisp.lua"))()

if mode == "snapshot" then
  local boot
  for _, f in ipairs(FRAMES) do if f.scripts.OnEvent then boot = f end end
  if not flags:find("noxp") then xpNow = 3000 end
  boot.scripts.OnEvent(boot, "PLAYER_LOGIN")
  if not flags:find("noxp") then goLeveling(boot) end
  if flags:find("visitauction") then   -- names are learned after the auction house closes, a pass at a time
    boot.scripts.OnEvent(boot, "AUCTION_HOUSE_CLOSED")
    runTimers()
  end
  if not flags:find("noloot") then goLooting(boot) end
  if not flags:find("nomail") then openMailbox(boot) end
  if not flags:find("noauctions") then boot.scripts.OnEvent(boot, "OWNED_AUCTIONS_UPDATED") end
  if flags:find("listings") then visitAuctionHouse(boot) end
  if flags:find("bank") then   -- a visit to the bank, and the window shut again long before logout
    boot.scripts.OnEvent(boot, "BANKFRAME_OPENED")
    boot.scripts.OnEvent(boot, "BANKFRAME_CLOSED")
    bagSlots[6] = {}
    boot.scripts.OnEvent(boot, "PLAYERBANKSLOTS_CHANGED", 1)   -- with the window shut nothing can be read, and nothing is
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
