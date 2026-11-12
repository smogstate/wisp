-- Runs the real addon/Wisp/Wisp.lua against a faked game, Questie and TomTom.
-- Pure Lua, so it runs under real Lua 5.1 (what the game uses) and under the
-- fengari VM alike. Used by tests/test_mvp.py.
--   lua tests/wow_stub.lua snapshot          -> prints the saved WispSnapshot
--   lua tests/wow_stub.lua paste <message>   -> prints what TomTom and chat received
--   WISP_STUB=noquestie,notomtom,nojournal,noauctionator,nogathermate removes those addons;
--   noprofession leaves the profession window closed

bit = bit or { band = function(a, b)   -- the game has this library; a plain Lua does not
  local out, place = 0, 1
  while a > 0 and b > 0 do
    if a % 2 == 1 and b % 2 == 1 then out = out + place end
    a, b, place = math.floor(a / 2), math.floor(b / 2), place * 2
  end
  return out
end }
local mode, ARG = arg[1], arg[2] or ""
if ARG == "@long" then ARG = "WISP1:" .. ("A"):rep(90000) end   -- too long for a command line on Windows: made here
local flags = os.getenv("WISP_STUB") or ""
local here = (arg[0] or "tests/wow_stub.lua"):match("^(.*)[/\\][^/\\]*$") or "."

local LOG, FRAMES, TIMERS, WAYS, LISTS = {}, {}, {}, {}, {}
-- The AddOns folder as it is on disk, for the one-click inbox: a file is read when the addon asks, as the game does.
local ADDONS = os.getenv("WISP_ADDONS")
local played, loaded = {}, {}
local function onDisk(path) return ADDONS .. "/" .. (path:gsub("^Interface\\AddOns\\", ""):gsub("\\", "/")) end
function PlaySoundFile(path)
  if not ADDONS or flags:find("nosound") then return false end
  if played[path] then return true end   -- once a sound has played, the client keeps it for good
  local real   -- an empty file is no sound
  if FILE_HAS_BYTES then   -- under the fengari VM, which has no io library: tests/wow_stub.js lends this
    real = FILE_HAS_BYTES(onDisk(path))
  else
    local f = io.open(onDisk(path), "rb")
    real = f and f:read(1) ~= nil or false
    if f then f:close() end
  end
  played[path] = real or nil
  return real
end
local out = print
print = function(s) LOG[#LOG + 1] = (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
-- The game's clock, which the play below moves forward by up to an hour or so. The tests hand it over
-- (WISP_CLOCK), set so that neither that play nor what it looks back on straddles a midnight, UTC or local.
local clock = tonumber(os.getenv("WISP_CLOCK") or "") or os.time()
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
function UnitIsPlayer(unit) return unit == "player" or unit == "Тестер" or unit == "Васян" end
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
local questXp = { [33] = 450, [34] = 540, [40] = 620, [76] = 700, [176] = 900, [314] = 800, [386] = 2100, [84] = 60,
                  [7] = 250, [155] = 1200, [166] = 2800 }   -- and a chain's later parts, which only Questie can tell
local QuestXP = { GetQuestLogRewardXP = function(_, id) return questXp[id] or 0 end,
                  GetQuestRewardMoney = function(id) return id == 176 and 1500 or 0 end }
local ZoneDB = { GetUiMapIdByAreaId = function(_, area) return ({ [12] = 1429, [1519] = 1453, [1] = 1426, [40] = 1436 })[area] end }
-- Questie's whole database, as it answers one field at a time: the quests above, and some far from
-- this character: further down the Westfall chain, one already done, and three it can never take
npcs[234] = { name = "Gryan Stoutmantle", spawns = { [40] = { { 56.3, 47.5 } } } }
npcs[639] = { name = "Edwin VanCleef", spawns = { [1581] = { { -1, -1 } } } }
local raw = {}
for id, q in pairs(quests) do
  raw[id] = { name = q.name, questLevel = q.level, requiredLevel = q.requiredLevel, zoneOrSort = 12, nextQuestInChain = q.nextQuestInChain,
              startedBy = { q.Starts and q.Starts.NPC, q.Starts and q.Starts.GameObject }, finishedBy = { q.Finisher and q.Finisher.NPC } }
end
raw[33].objectives = { { { 118 } }, nil, { { 750 } } }
raw[33].objectivesText = { "Bring 8 pieces of Tough Wolf Meat to Eagan Peltskinner." }
-- a Russian client: letters of two bytes, and a text long enough to be cut. Byte 240 falls in the middle of one.
raw[76].objectivesText = { ("Убейте 10 кобольдов-рудокопов и вернитесь к маршалу Макбрайду в Аббатство Североземья, когда закончите. "):rep(3) }
raw[7] = { name = "Kobold Camp Cleanup", questLevel = 3, requiredLevel = 1, zoneOrSort = 12, startedBy = { { 240 } }, finishedBy = { { 240 } } }
raw[155] = { name = "The Defias Brotherhood", questLevel = 14, requiredLevel = 9, zoneOrSort = 40, preQuestSingle = { 7 }, nextQuestInChain = 166,
             startedBy = { { 234 } }, finishedBy = { { 234 } } }
raw[166] = { name = "The Defias Brotherhood", questLevel = 22, requiredLevel = 14, zoneOrSort = 40, preQuestSingle = { 155 },
             startedBy = { { 234 } }, finishedBy = { { 234 } }, objectives = { { { 639 } } },
             objectivesText = { "Kill Edwin VanCleef and bring his head to Gryan Stoutmantle." } }
raw[9001] = { name = "For the Horde!", questLevel = 16, requiredLevel = 10, requiredRaces = 690, startedBy = { { 240 } } }
raw[9002] = { name = "Never Seen", questLevel = 5, startedBy = { { 240 } } }
raw[9003] = { name = "A Warrior's Training", questLevel = 10, requiredClasses = 1, startedBy = { { 240 } } }
tags[166] = "dungeon"
if flags:find("ranks") then   -- a profession's next rank as a quest: it needs the skill, not a level alone
  raw[6607] = { name = "Nat Pagle, Angler Extreme", questLevel = 45, requiredLevel = 10, zoneOrSort = 40, requiredSkill = { 356, 225 },
                startedBy = { { 234 } }, finishedBy = { { 234 } } }
end
QuestieDB.QuestPointers = {}
for id in pairs(raw) do QuestieDB.QuestPointers[id] = true end
QuestieDB.QueryQuestSingle = function(id, key) return raw[id] and raw[id][key] end
QuestieDB.QueryNPCSingle = function(id, key) return npcs[id] and npcs[id][key] end
QuestieDB.QueryObjectSingle = function(id, key) return objects[id] and objects[id][key] end
-- its whole item list, a field at a time: a recipe scroll nobody here owns, sold by one vendor of each faction
npcs[3005] = { name = "Horde Vendor", friendlyToFaction = "H", spawns = { [12] = { { 10, 10 } } } }
npcs[900] = { name = "Roaming Mob", spawns = { [12] = { { 10, 10 } }, [40] = { { 50, 50 }, { 52, 50 }, { 51, 52 }, { 90, 90 } } } }
C_Map.GetAreaInfo = function(area) return ({ [1581] = "The Deadmines", [1519] = "Stormwind City" })[area] end
local rawItems = {
  [7090] = { name = "Pattern: Not Learned Yet", vendors = { 3005, 1347 } },
  -- a recipe that only drops: from a boss inside an instance, from a mob that roams two zones, from a wolf
  [6325] = { name = "Recipe: Herb Baked Egg", npcDrops = { 639, 900, 118 } },
  [2321] = { name = "Fine Thread", vendors = { 1347 } },
  [4306] = { name = "Silk Cloth", npcDrops = { 100, 101, 102, 103, 104, 105, 106 }, objectDrops = { 77 } },
  [6948] = { name = "Hearthstone" },                              -- comes from nowhere: left out
  [5000] = { name = "Horde Only Thing", vendors = { 3005 } },     -- nobody sells it to this character: left out
}
if flags:find("ranks") then   -- and as a book; a fishing pole is sold too and is named after a profession, but is no book
  rawItems[16084] = { name = "Expert First Aid - Under Wraps", vendors = { 1347 } }
  rawItems[6256] = { name = "Fishing Pole", vendors = { 1347 } }
end
-- what an item says of itself: its name, and for a book or a recipe the skill it needs, in the game's own wording
ITEM_MIN_SKILL = flags:find("russian") and "Требуется: %s (%d)" or "Requires %s (%d)"
C_TooltipInfo = { GetItemByID = function(id)
  local needs = ({ [16084] = { "First Aid", 125 }, [7090] = { "Tailoring", 160 } })[id]
  local lines = { { leftText = "an item" }, { leftText = "Binds when picked up" } }
  if needs then lines[#lines + 1] = { leftText = ITEM_MIN_SKILL:format(needs[1], needs[2]) } end
  return { lines = lines }
end }
-- a profession trainer's window, as the game lists what it teaches: name, rank, state; the skill, the level, the cost
local trainerOffers = {}
function IsTradeskillTrainer() return true end
function GetNumTrainerServices() return #trainerOffers end
function GetTrainerServiceInfo(i) local o = trainerOffers[i]; return o[1], o[2], o[3] end
function GetTrainerServiceSkillReq(i) local o = trainerOffers[i]; return o[4], o[5], true end
function GetTrainerServiceLevelReq(i) return trainerOffers[i][6] end
function GetTrainerServiceCost(i) return trainerOffers[i][7] end
local function visitTrainers(boot)
  local real = UnitGUID
  UnitGUID = function(unit) if unit == "npc" then return "Creature-0-1-2-3-9001-00001" end return real(unit) end
  trainerOffers = { { "Tailoring", "Expert", "used", "Tailoring", 125, 20, 5000 },
                    { "Tailoring", "Artisan", "unavailable", "Tailoring", 200, 35, 50000 },
                    { "Azure Silk Hood", "", "used", "Tailoring", 145, 0, 800 },
                    { "Trade Skills", "", "header" } }
  boot.scripts.OnEvent(boot, "TRAINER_SHOW")
  UnitGUID = real
end
-- the game's own class of an item: 9 is a recipe item, its subclass the profession (7 First Aid, 2 Tailoring, 5 Cooking)
function GetItemInfoInstant(id)
  local recipe = ({ [16084] = 7, [7090] = 2, [6325] = 5 })[id]
  return id, "x", "x", "", 1, recipe and 9 or 7, recipe or 0
end
QuestieDB.ItemPointers = {}
for id in pairs(rawItems) do QuestieDB.ItemPointers[id] = true end
QuestieDB.IsFriendlyToPlayer = function(side) return not side or side == "AH" or side == "A" end
QuestieDB.QueryItemSingle = function(id, key)
  if rawItems[id] then return rawItems[id][key] end
  return questieItems[id] and questieItems[id][key]
end
local QuestiePlayer = { HasRequiredRace = function(mask) return mask % 2 == 1 end,                   -- a human
                        HasRequiredClass = function(mask) return math.floor(mask / 128) % 2 == 1 end }   -- a mage
local QuestieCorrections = { hiddenQuests = { [9002] = true } }
C_AddOns = { GetAddOnMetadata = function(name, field) if name == "Questie" and field == "Version" then return "11.9.0" end end }
C_AddOns.LoadAddOn = function(name)   -- a load-on-demand addon: its file is read now, and only once per UI session
  if not ADDONS or loaded[name] then return false end
  loaded[name] = true
  local chunk = loadfile(ADDONS .. "/" .. name .. "/In.lua")
  if chunk then chunk() end
  return chunk ~= nil
end
if ADDONS then local cursor = loadfile(ADDONS .. "/Wisp_Inbox/Cursor.lua"); if cursor then cursor() end end   -- read at every UI load
-- what it draws its map icons from: quest id -> true; 34 follows a quest in the log, 9999 is one it has no record of
local AvailableQuests = { __availableQuests = { [34] = true, [40] = true, [84] = true, [176] = true, [314] = true, [386] = true, [9999] = true } }
if flags:find("oldquestie") then AvailableQuests = {} end
if not flags:find("noquestie") then
  local modules = { QuestieDB = QuestieDB, ZoneDB = ZoneDB, AvailableQuests = AvailableQuests, QuestXP = QuestXP,
                    QuestiePlayer = QuestiePlayer, QuestieCorrections = QuestieCorrections }
  if flags:find("ranks") then   -- Questie's townsfolk: who trains what (skill line -> NPCs), one of them the other faction's
    npcs[9001] = { name = "Mara Stitchwell", subName = "Artisan Tailor", spawns = { [1519] = { { 43.5, 73.5 } } } }
    npcs[9002] = { name = "Old Tom Reel", subName = "Fishing Trainer", spawns = { [12] = { { 47.6, 62.1 } } } }
    npcs[9003] = { name = "Grukk the Stitcher", subName = "Expert Tailor", friendlyToFaction = "H", spawns = { [12] = { { 1, 1 } } } }
    modules.Townsfolk = { professionTrainers = { [197] = { 9001, 9003 }, [356] = { 9002 }, [129] = {} } }
  end
  QuestieLoader = { ImportModule = function(_, name) return modules[name] or {} end }
end
-- DungeonJournal, in the shape of its own tables (made-up entries, not its data)
local completedIds = { [65] = true, [132] = true }
C_QuestLog.IsQuestFlaggedCompleted = function(id) return completedIds[id] == true end
C_QuestLog.GetTitleForQuestID = function(id) return ({ [33] = "Волки на границе" })[id] end   -- the game's own word, where it has one
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
if flags:find("russian") then itemNames[2321] = "Тонкая нить" end   -- as a Russian game names what it has at hand
C_Item = {
  IsItemDataCachedByID = function(id) return itemNames[id] ~= nil end,
  GetItemNameByID = function(id) return itemNames[id] or (asked[id] and late[id]) or nil end,
  RequestLoadItemDataByID = function(id) asked[id] = true end,
  -- rarity, for an item the game has described: a hood is uncommon, a recipe too, a shovel is poor; one is told only once asked for
  GetItemQualityByID = function(id)
    if id == 2321 and not asked[id] then return nil end
    return ({ [7048] = 2, [6325] = 2, [16084] = 2, [7090] = 3, [750] = 0 })[id] or 1
  end,
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
  GetRecipeSourceText = function(id) return id == 8791 and "|cffffd100Vendor:|r Alexandra Bolero" or nil end,
}
-- Every recipe in the game by its place among the skill abilities, whoever has the profession: the tailor's own
-- above, and a cook's that this character cannot see in any window. Herb Baked Egg needs an egg and spices.
recipes[8604] = { info = { name = "Herb Baked Egg", learned = false, maxTrivialLevel = 90 },
                  plan = { outputItemID = 6888, quantityMin = 1, quantityMax = 1, reagentSlotSchematics = { slot(6889, 1), slot(2678, 1) } } }
recipes[2538] = { info = { name = "Charred Wolf Meat", learned = false, maxTrivialLevel = 85 },
                  plan = { outputItemID = 2679, quantityMin = 1, quantityMax = 1, reagentSlotSchematics = { slot(750, 1) } } }
local abilities = { [11] = 3839, [12] = 8760, [13] = 3813, [14] = 8791, [15] = 1, [20] = 8604, [21] = 2538, [22] = 2538 }
for id, recipe in pairs(recipes) do recipe.info.recipeID = id end
C_TradeSkillUI.GetRecipeInfoForSkillLineAbility = function(ability) return abilities[ability] and recipes[abilities[ability]].info or nil end
C_TradeSkillUI.GetProfessionInfoByRecipeID = function(id)
  if id == 8604 or id == 2538 then return { professionName = "Classic Cooking", parentProfessionName = "Cooking" } end
  return { professionName = "Tailoring" }
end
if flags:find("norecipelist") then C_TradeSkillUI.GetRecipeInfoForSkillLineAbility = function() return nil end end
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
  local today = math.floor((clock - day0) / 86400)   -- by the game's clock, as Auctionator counts
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
function UnitGUID(unit) return unit == "player" and "Player-1-ME" or unit == "target" and targetGuid or nil end
-- The combat log: one line at a time, as the game hands it over. A fight with another player and the death it ends in.
if flags:find("secretvalues") then function issecretvalue() return false end end   -- a game that hides combat from addons
local uptime, health, logged = 1000, 1400, {}
function GetTime() return uptime end
function UnitHealth() return health end
function UnitHealthMax() return 1400 end
function CombatLogGetCurrentEventInfo() return (table.unpack or unpack)(logged, 1, 21) end
function GetPlayerInfoByGUID(guid)
  if guid == "Player-1-ROGUE" then return "Rogue", "ROGUE", "Undead", "Scourge", 2, "Gankmaster", "Other Realm" end
end
local function killedByAWolf(boot)   -- a long fight with a creature: only its last half minute is filed
  local ME, WOLF, beast, mine = "Player-1-ME", "Creature-0-1-2-3-118-Y", 0xa48, 0x511
  for i = 1, 6 do
    uptime, health, logged = uptime + 10, 1400 - i * 200, { 0, "SWING_DAMAGE", false, WOLF, "Prowler", beast, 0, ME, "x", mine, 0, 200, -1, 1 }
    boot.scripts.OnEvent(boot, "COMBAT_LOG_EVENT_UNFILTERED")
  end
  boot.scripts.OnEvent(boot, "PLAYER_DEAD")
end
local function killedByAPlayer(boot)
  local ME, ROGUE, WOLF = "Player-1-ME", "Player-1-ROGUE", "Creature-0-1-2-3-118-X"
  local foe, beast, mine = 0x548, 0xa48, 0x511   -- an enemy player, an enemy creature, the character
  local function line(after, left, ...)
    uptime, health, logged = uptime + after, left, { 0, ... }
    boot.scripts.OnEvent(boot, "COMBAT_LOG_EVENT_UNFILTERED")
  end
  line(0, 1400, "SWING_DAMAGE", false, WOLF, "Prowler", beast, 0, ME, "x", mine, 0, 40, -1, 1)   -- a minute earlier: another fight
  -- the rogue's first try, well over half a minute before the death: the same fight, since it never went quiet for long
  line(60, 1300, "SPELL_DAMAGE", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 8676, "Ambush", 1, 100, -1, 1, nil, nil, nil, false)
  line(10, 1300, "SPELL_CAST_SUCCESS", false, ME, "x", mine, 0, ME, "x", mine, 0, 1953, "Blink", 64)
  line(15, 1200, "SWING_DAMAGE", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 100, -1, 1, nil, nil, nil, false)
  line(15, 1400, "SPELL_AURA_APPLIED", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 1833, "Cheap Shot", 1, "DEBUFF")
  line(0.5, 1100, "SWING_DAMAGE", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 300, -1, 1, nil, nil, nil, false)
  line(1, 500, "SPELL_DAMAGE", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 1752, "Sinister Strike", 1, 600, -1, 1, nil, nil, nil, true)
  line(1, 500, "SPELL_CAST_SUCCESS", false, ME, "x", mine, 0, ROGUE, "Gankmaster", foe, 0, 122, "Frost Nova", 16)
  line(0.5, 500, "SPELL_AURA_REMOVED", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 1833, "Cheap Shot", 1, "DEBUFF")
  line(1, 650, "SPELL_HEAL", false, ME, "x", mine, 0, ME, "x", mine, 0, 439, "Healing Potion", 1, 150, 0)
  line(1, 0, "SPELL_DAMAGE", false, ROGUE, "Gankmaster", foe, 0, ME, "x", mine, 0, 2098, "Eviscerate", 1, 900, 250, 1, nil, nil, nil, false)
  boot.scripts.OnEvent(boot, "PLAYER_DEAD")
end
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
  if flags:find("healthenfish") then   -- a heal on a party member, then a bobber looted: his name is not the bobber's
    boot.scripts.OnEvent(boot, "UNIT_SPELLCAST_SENT", "player", "Васян", "cast-3", 2050)
    lootWindow(boot, 5, 0, { { 6291, { "GameObject-0-1-2-3-35591-B1", 1 } } })
  end
end
-- The mailbox. Auction mails carry an invoice; three of them say exactly the same (three stacks bought in one go).
local function bought(name, id, count, paid, days) return { "buyer", name, "Bob", paid, paid, 0, 0, count, id = id, days = days } end
local inbox = {
  bought("Silk Cloth", 4306, 20, 6000, 29.9), bought("Silk Cloth", 4306, 20, 6000, 29.9), bought("Silk Cloth", 4306, 20, 6000, 29.9),
  -- every auction mail is under four hours old: that far back the tests' clock keeps clear of midnight
  { "seller", "Bolt of Silk Cloth", "Ann", 10000, 10000, 150, 500, 5, days = 29.9 },
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

if flags:find("alts") then   -- what an alt left in the saved data at its own logout: a cook, and a second tailor
  WispData = WispData or {}
  WispData.chars = { ["Test Realm-Cook"] = { class = "WARRIOR", level = 30, name = "Кук",   -- and what the cook carries and keeps
    bags = { { id = 6260, name = "Blue Dye", count = 4 }, { id = 4306, name = "Silk Cloth", count = 7, bound = true } } } }
  WispData.bank = { ["Test Realm-Cook"] = { seenAt = os.time() - 3600, items = {
    { id = 4305, name = "Bolt of Silk Cloth", count = 1 }, { id = 2321, name = "Fine Thread", count = 5 },
    { id = 6260, name = "Blue Dye", count = 1, shared = true } } } }
  WispData.recipes = {
    ["Test Realm-Cook"] = { Cooking = { seenAt = os.time() - 86400, skill = 75, max = 150, recipes = {
      r2538 = { id = 2538, name = "Charred Wolf Meat", learned = true, colour = "green", greyAt = 85, skill = 75, makes = 2679,
                makesMin = 1, makesMax = 1, reagents = { { id = 750, count = 1 } } },
      r8604 = { id = 8604, name = "Herb Baked Egg", learned = false, greyAt = 90, skill = 75, makes = 6888, makesMin = 1, makesMax = 1,
                reagents = { { id = 6889, count = 1 }, { id = 2678, count = 1 } } } } } },
    ["Other Realm-Tailor"] = { Tailoring = { seenAt = os.time() - 86400, skill = 200, max = 225, recipes = {
      r8791 = { id = 8791, name = "Not Learned Yet", learned = true, colour = "yellow", greyAt = 200, skill = 200, makes = 7049,
                makesMin = 1, makesMax = 1, reagents = { { id = 4305, count = 5 } } },
      r3839 = { id = 3839, name = "Bolt of Silk Cloth", learned = true, colour = "grey", greyAt = 170, skill = 200, makes = 4305,
                makesMin = 1, makesMax = 1, reagents = { { id = 4306, count = 4 } } } } } },
  }
end

if flags:find("brokenindex") then   -- a game whose encoder gives up on the quest list
  local encode = C_EncodingUtil.SerializeJSON
  C_EncodingUtil.SerializeJSON = function(value)
    if type(value) == "table" and value.quests and value.npcs then error("out of memory") end
    return encode(value)
  end
end

-- What a quest of the log gives, as the game says. Usually the stand-in game says nothing (0, none), so that
-- Questie's own reckoning shows; with the "rewards" flag it speaks: a little more experience for the wolves'
-- quest than Questie has, and for the mine quest money, a cloak, and a choice of two.
local speaks = flags:find("rewards")
GetQuestLogRewardXP = function(id) return speaks and id == 33 and 460 or 0 end
GetQuestLogRewardMoney = function(id) return speaks and id == 76 and 350 or 0 end
GetNumQuestLogRewards = function(id) return speaks and id == 76 and 1 or 0 end
GetQuestLogRewardInfo = function(i, id) return "Miner's Cape", 134, 1, 1, true, 5444 end
GetNumQuestLogChoices = function(id) return speaks and id == 76 and 2 or 0 end
GetQuestLogChoiceInfo = function(i, id) if i == 1 then return "Silk Cloth", 134, 5, 1, true, 4306 end return "Kobold Mining Shovel", 134, 1, 2, true, 1195 end

assert(loadfile(here .. "/../addon/Wisp/Wisp.lua"))()

if mode == "snapshot" then
  local boot
  for _, f in ipairs(FRAMES) do if f.scripts.OnEvent then boot = f end end
  if not flags:find("noxp") then xpNow = 3000 end
  boot.scripts.OnEvent(boot, "PLAYER_LOGIN")
  if flags:find("questindex") then   -- the session goes on long enough for Questie's quests to be read
    runTimers()
    local kept = WispQuests
    WispQuests = "KEPT"
    boot.scripts.OnEvent(boot, "PLAYER_LOGIN")   -- the next session: what was read is kept, not read again
    runTimers()
    out("QUESTSAGAIN " .. tostring(WispQuests))
    WispQuests = kept
  end
  if not flags:find("noxp") then goLeveling(boot) end
  if flags:find("visitauction") then   -- names are learned after the auction house closes, a pass at a time
    boot.scripts.OnEvent(boot, "AUCTION_HOUSE_CLOSED")
    runTimers()
  end
  if not flags:find("noloot") then goLooting(boot) end
  if flags:find("pvpdeath") then killedByAPlayer(boot) end
  if flags:find("ranks") then visitTrainers(boot) end
  if flags:find("wolfdeath") then killedByAWolf(boot) end
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
  if WispQuests then out("QUESTS " .. WispQuests .. " " .. WispQuestsKey) end
  if WispItems then out("ITEMS " .. WispItems .. " " .. WispItemsKey) end
  if WispRecipes then out("RECIPES " .. WispRecipes .. " " .. (WispRecipesKey:gsub(" ", "_"))) end
  if SAVED then
    local days = {}
    for _, d in ipairs(SAVED.market.days) do days[#days + 1] = number(d.day - SAVED.market.today) .. ":" .. number(#d.rows / 4) end
    out("DAYS " .. table.concat(days, " "))
  end
  for market, upTo in pairs(type(WispData) == "table" and WispData.marketSent or {}) do
    out("SENT " .. market .. " " .. upTo.to - upTo.from)
  end
elseif mode == "inbox" then   -- a session: login, a few seconds, then the files change on disk, a few seconds more
  if os.getenv("WISP_DELIVERED") then WispData = { delivered = tonumber(os.getenv("WISP_DELIVERED") .. ".0") } end
  local boot
  for _, f in ipairs(FRAMES) do if f.scripts.OnEvent then boot = f end end
  boot.scripts.OnEvent(boot, "PLAYER_LOGIN")
  local function seconds(n)
    for _ = 1, n do
      for _, f in ipairs(FRAMES) do if f.scripts.OnUpdate then f.scripts.OnUpdate(f, 1) end end
    end
  end
  seconds(3)
  out("ATLOGIN " .. #WAYS)
  if os.getenv("WISP_ADDONS_LATER") then ADDONS = os.getenv("WISP_ADDONS_LATER"); seconds(3) end
  for _, w in ipairs(WAYS) do out("WAY " .. w) end
  local used = 0
  for _ in pairs(loaded) do used = used + 1 end
  out("SLOTS " .. used)
  out("DELIVERED " .. tostring(type(WispData) == "table" and WispData.delivered))
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
