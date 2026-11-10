// Runs the real addon/Wisp/Wisp.lua in a Lua VM against a faked game, Questie
// and TomTom. Used by tests/test_mvp.py.
//   node tests/wow_stub.js snapshot          -> prints the saved WispSnapshot
//   node tests/wow_stub.js paste <message>   -> prints what TomTom and chat received
//   npm install   (once, for the Lua VM)
const fs = require("fs");
const path = require("path");
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require("fengari");

const game = `
LOG, FRAMES, TIMERS, WAYS = {}, {}, {}, {}
local out = print
print = function(s) LOG[#LOG + 1] = (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
function OUT(s) out(s) end
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
function CreateFrame(_, name) local f = widget(); FRAMES[#FRAMES + 1] = f; if name then _G[name] = f end; return f end
UIParent, SlashCmdList = widget(), {}
C_Timer = { After = function(_, f) TIMERS[#TIMERS + 1] = f end }
function RUNTIMERS() while #TIMERS > 0 do table.remove(TIMERS, 1)() end end
function SetOverrideBindingClick() end

-- the client's JSON and base64, re-implemented for the test
local b = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local function b64enc(d) return ((d:gsub(".", function(x) local r, c = "", x:byte() for i = 8, 1, -1 do r = r .. (c % 2 ^ i - c % 2 ^ (i - 1) > 0 and "1" or "0") end return r end) .. "0000"):gsub("%d%d%d?%d?%d?%d?", function(x) if #x < 6 then return "" end local c = 0 for i = 1, 6 do c = c + (x:sub(i, i) == "1" and 2 ^ (6 - i) or 0) end return b:sub(c + 1, c + 1) end) .. ({ "", "==", "=" })[#d % 3 + 1]) end
local function b64dec(d) d = d:gsub("[^" .. b .. "=]", "") return (d:gsub(".", function(x) if x == "=" then return "" end local r, f = "", (b:find(x, 1, true) - 1) for i = 6, 1, -1 do r = r .. (f % 2 ^ i - f % 2 ^ (i - 1) > 0 and "1" or "0") end return r end):gsub("%d%d%d?%d?%d?%d?%d?%d?", function(x) if #x ~= 8 then return "" end local c = 0 for i = 1, 8 do c = c + (x:sub(i, i) == "1" and 2 ^ (8 - i) or 0) end return string.char(math.floor(c)) end)) end
local function enc(v) local t = type(v)
  if t == "table" then
    if #v > 0 or next(v) == nil then local p = {} for _, x in ipairs(v) do p[#p + 1] = enc(x) end return "[" .. table.concat(p, ",") .. "]" end
    local p = {} for k, x in pairs(v) do p[#p + 1] = '"' .. tostring(k) .. '":' .. enc(x) end return "{" .. table.concat(p, ",") .. "}"
  elseif t == "string" then return '"' .. v:gsub('[\\\\"]', "\\\\%0") .. '"'
  else return tostring(v) end end
local function dec(s) local i = 1
  local function ws() i = s:find("%S", i) or #s + 1 end
  local val
  local function str() local j, o = i + 1, {} while true do local c = s:sub(j, j) if c == "" then error("bad json") elseif c == '"' then break elseif c == "\\\\" then j = j + 1; o[#o + 1] = s:sub(j, j) else o[#o + 1] = c end j = j + 1 end i = j + 1 return table.concat(o) end
  function val() ws() local c = s:sub(i, i)
    if c == "{" then local t = {} i = i + 1 ws() if s:sub(i, i) == "}" then i = i + 1 return t end while true do ws() local k = str() ws() i = i + 1 t[k] = val() ws() local d = s:sub(i, i) i = i + 1 if d == "}" then return t elseif d ~= "," then error("bad json") end end
    elseif c == "[" then local t = {} i = i + 1 ws() if s:sub(i, i) == "]" then i = i + 1 return t end while true do t[#t + 1] = val() ws() local d = s:sub(i, i) i = i + 1 if d == "]" then return t elseif d ~= "," then error("bad json") end end
    elseif c == '"' then return str()
    elseif s:sub(i, i + 3) == "true" then i = i + 4 return true elseif s:sub(i, i + 4) == "false" then i = i + 5 return false
    else local n = s:match("^-?[%d.eE+-]+", i) if not n then error("bad json") end i = i + #n return tonumber(n) end end
  return val() end
C_EncodingUtil = { SerializeJSON = enc, DeserializeJSON = dec, EncodeBase64 = b64enc, DecodeBase64 = b64dec }

-- the character: a level 12 human in Elwynn with two quests
function GetBuildInfo() return "1.60.1", "70862", "Nov 1 2026", 16001 end
function GetLocale() return "ruRU" end
function UnitClass() return "Маг", "MAGE" end
function UnitRace() return "Человек", "Human" end
function UnitLevel() return 12 end
function UnitFactionGroup() return "Alliance", "Альянс" end
function GetMoney() return 153422 end
function GetSubZoneText() return "Златоземье" end
local maps = { [1429] = "Elwynn Forest", [1453] = "Stormwind City", [1436] = "Westfall" }
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

-- Questie: quest 33 starts and ends at Eagan (npc 196) and leads to quest 34, given by a wanted poster
local quests = {
  [33] = { name = "Wolves Across the Border", level = 7, requiredLevel = 2, nextQuestInChain = 34,
           Starts = { NPC = { 196 } }, Finisher = { NPC = { 196 } } },
  [34] = { name = "An Unwelcome Guest", level = 9, requiredLevel = 5, nextQuestInChain = 0,
           Starts = { GameObject = { 55 } }, Finisher = { NPC = { 196 } } },
  [76] = { name = "The Jasperlode Mine", level = 10, requiredLevel = 7,
           Starts = { NPC = { 240 } }, Finisher = { NPC = { 240 } } },
}
local npcs = {
  [196] = { name = "Eagan |cffff0000Peltskinner", spawns = { [12] = { { 48.94, 40.16 } } } },
  [240] = { name = "Marshal Dughan", spawns = { [12] = { { 42.1, 65.9 } }, [1519] = { { -1, -1 } } } },
}
local objects = { [55] = { name = "Wanted Poster", spawns = { [12] = { { 24.6, 74.7 } } } } }
local QuestieDB = {
  GetQuest = function(id) return quests[id] end,
  GetNPC = function(self, id) return npcs[id] end,
  GetObject = function(self, id) return objects[id] end,
}
local ZoneDB = { GetUiMapIdByAreaId = function(self, area) return ({ [12] = 1429, [1519] = 1453 })[area] end }
if not NO_QUESTIE then
  QuestieLoader = { ImportModule = function(self, name) return name == "QuestieDB" and QuestieDB or ZoneDB end }
end
if not NO_TOMTOM then
  TomTom = { AddWaypoint = function(self, m, x, y, o) WAYS[#WAYS + 1] = "tomtom " .. m .. " " .. x .. " " .. y .. " " .. o.title end }
end
`;

const [mode, arg] = process.argv.slice(2);
const flags = process.env.WISP_STUB || "";
const drivers = {
  snapshot: `
    local boot for _, f in ipairs(FRAMES) do if f.scripts.OnEvent then boot = f end end
    boot.scripts.OnEvent(boot, "PLAYER_LOGIN")
    SlashCmdList.WISP("save")
    boot.scripts.OnEvent(boot, "PLAYER_LOGOUT")
    OUT("SNAPSHOT " .. tostring(WispSnapshot))
    for _, l in ipairs(LOG) do OUT("CHAT " .. l) end`,
  paste: `
    SlashCmdList.WISP("")
    WispInbox.text = ARG
    WispInbox.scripts.OnTextChanged(WispInbox, true)
    RUNTIMERS()
    for _, w in ipairs(WAYS) do OUT("WAY " .. w) end
    for _, l in ipairs(LOG) do OUT("CHAT " .. l) end
    OUT("BOX [" .. WispInbox.text .. "]")`,
};

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);
const setString = (k, v) => { lua.lua_pushstring(L, to_luastring(v)); lua.lua_setglobal(L, to_luastring(k)); };
const setTrue = (k) => { lua.lua_pushboolean(L, 1); lua.lua_setglobal(L, to_luastring(k)); };
setString("ARG", arg || "");
if (flags.includes("noquestie")) setTrue("NO_QUESTIE");
if (flags.includes("notomtom")) setTrue("NO_TOMTOM");
const addon = fs.readFileSync(path.join(__dirname, "../addon/Wisp/Wisp.lua"), "utf8");
for (const [name, src] of [["game", game], ["Wisp.lua", addon], ["driver", drivers[mode]]]) {
  if (lauxlib.luaL_loadbuffer(L, to_luastring(src), null, to_luastring(name)) || lua.lua_pcall(L, 0, 0, 0)) {
    console.error("FAIL in " + name + ": " + to_jsstring(lua.lua_tostring(L, -1)));
    process.exit(1);
  }
}
