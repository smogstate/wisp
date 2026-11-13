-- Which Wisp this addon came with. The build writes the version in place of "dev"; it goes into every snapshot, so
-- that Wisp can tell a snapshot written by the addon it has just installed from one written by the addon before
-- it: after an update the game keeps running the old addon until its interface is loaded again.
local ADDON_BUILD = "dev"

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

-- What Wisp says in the chat frame, in the game's own language. The English is the key.
local RU = GetLocale and GetLocale() == "ruRU" and {
  ["read %d %s recipes. They reach Wisp when you log out."] = "рецептов прочитано: %d (%s). Они попадут в Wisp, когда вы выйдете из игры.",
  ["that is not a Wisp message."] = "это не сообщение Wisp.",
  ["that message is too long."] = "это сообщение слишком длинное.",
  ["one-click cards are used up for this session: /wisp sync brings them back. Copy and Ctrl+V still work."] =
    "карточки в один клик на эту сессию закончились: /wisp sync вернёт их. «Копировать» и Ctrl+V работают как прежде.",
  ["Wisp: paste now (Ctrl+V). Esc closes."] = "Wisp: вставьте сейчас (Ctrl+V). Esc закрывает.",
  ["could not read that message."] = "не удалось прочитать это сообщение.",
  ["stopped after 50 commands."] = "остановлено после 50 команд.",
  ["skipped"] = "пропущено", ["unknown command"] = "неизвестная команда", ["failed"] = "ошибка",
  ["snapshot built: %d quests, Questie %s, DungeonJournal %s. It is written to disk when you log out."] =
    "снимок собран: заданий: %d, Questie %s, DungeonJournal %s. Он запишется на диск, когда вы выйдете из игры.",
  ["found"] = "найден", ["not found"] = "не найден", ["%d dungeons"] = "подземелий: %d",
  ["could not build the snapshot:"] = "не удалось собрать снимок:",
  ["this client has no JSON encoder"] = "в этом клиенте нет кодировщика JSON",
  ["bad waypoint"] = "неверная метка", ["cannot set a waypoint on that map"] = "на этой карте нельзя поставить метку",
  ["waypoint: "] = "метка: ", ["Auctionator is not installed"] = "Auctionator не установлен",
  ["Pawn is not installed"] = "Pawn не установлен", ["bad Pawn scale"] = "неверная шкала Pawn",
  ["Pawn did not take the scale"] = "Pawn не принял шкалу",
  ["Pawn has the scale \"%s\": item tooltips now show its value"] = "Шкала «%s» в Pawn: подсказки предметов теперь показывают её оценку",
  ["bad shopping list"] = "неверный список покупок", ["Auctionator refused "] = "Auctionator не принял ",
  ["shopping list \"%s\" with %d items is in Auctionator's Shopping tab"] = "список покупок «%s» (предметов: %d) — на вкладке «Покупки» в Auctionator",
} or {}
local function L(text) return RU[text] or text end

local function say(text) print("|cff7fd4ffWisp|r " .. text) end

------------------------------------------------------------ snapshot ----

local function round1(n) return math.floor(n * 10 + 0.5) / 10 end

-- A text no longer than so many bytes, cut between letters: a Russian letter is two bytes, and half of one
-- is not text any more (it made the whole quest list unreadable on a Russian client).
local function clip(text, most)
  if #text <= most then return text end
  local last = most
  while last > 0 do
    local following = text:byte(last + 1)
    if not following or following < 128 or following >= 192 then break end   -- the next byte starts a letter
    last = last - 1
  end
  return text:sub(1, last)
end

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
  local XP = questieModule("QuestXP")   -- what handing it in gives, as Questie reckons it for this character's level
  if type(XP) == "table" then
    if type(XP.GetQuestLogRewardXP) == "function" then
      local okX, xp = pcall(XP.GetQuestLogRewardXP, XP, questId, false)
      if okX and type(xp) == "number" and xp > 0 then out.xp = xp end
    end
    if type(XP.GetQuestRewardMoney) == "function" then
      local okM, money = pcall(XP.GetQuestRewardMoney, questId)
      if okM and type(money) == "number" and money > 0 then out.money = money end
    end
  end
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

-- What handing a quest of the log in gives, as the game itself says: experience, money, the items given
-- and the items to choose one of. Questie's own experience table is only a fallback (Forever's Questie
-- may have none).
local function logRewards(id)
  local out = {}
  local function number(fn, ...)
    if type(fn) ~= "function" then return nil end
    local ok, n = pcall(fn, ...)
    return ok and type(n) == "number" and n > 0 and n or nil
  end
  out.xp, out.money = number(GetQuestLogRewardXP, id), number(GetQuestLogRewardMoney, id)
  local function items(count, info, ...)
    local list = {}
    for i = 1, math.min(number(count, ...) or 0, 12) do
      local ok, name, _, amount, _, _, itemID = pcall(info, i, id)
      if ok and (type(name) == "string" or type(itemID) == "number") then
        list[#list + 1] = { id = type(itemID) == "number" and itemID or nil, name = type(name) == "string" and name or nil,
                            count = type(amount) == "number" and amount > 1 and amount or nil }
      end
    end
    return #list > 0 and list or nil
  end
  if type(GetQuestLogRewardInfo) == "function" then out.items = items(GetNumQuestLogRewards, GetQuestLogRewardInfo, id) end
  if type(GetQuestLogChoiceInfo) == "function" then out.choices = items(GetNumQuestLogChoices, GetQuestLogChoiceInfo, id, true) end
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
          quest.xp, quest.money = known.xp, known.money
        end
      end
      local okR, gives = pcall(logRewards, info.questID)   -- the game's own word comes first
      if okR then
        quest.xp, quest.money = gives.xp or quest.xp, gives.money or quest.money
        quest.rewardItems, quest.rewardChoices = gives.items, gives.choices
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
                        kind = kind, starts = known.starts, needs = known.needs, xp = known.xp, money = known.money }
    end
  end
  return out
end

-------------------------------------------------------- every quest ----
-- Questie's whole quest database, so that any quest can be asked about, not only the ones in
-- the log or in reach. Read once after login, a slice at a time so the game never stalls, and
-- kept in saved variables of their own (WispQuests, WispQuestsKey) that the game carries from
-- session to session. Read again only when Questie's data, the game's language or the
-- character's race or class is not what it was read for. Only quests this character could
-- ever take; for each, who gives it and takes it back and where, what comes before and after
-- it, and what it asks for. Wisp reads all this from Questie in the game; it carries no copy.

local QUESTS_PER_SLICE = 200

local function indexKey(DB, pointers, ...)
  local meta = (C_AddOns and C_AddOns.GetAddOnMetadata) or GetAddOnMetadata
  local okV, version = pcall(meta, "Questie", "Version")
  local count = 0
  for _ in pairs(pointers) do count = count + 1 end
  return table.concat({ "f7", okV and tostring(version) or "?", GetLocale(), count, ... }, "|")   -- f7: who trains each profession
end

local function questIndexKey(DB)
  local _, race = UnitRace("player")
  local _, class = UnitClass("player")
  return indexKey(DB, DB.QuestPointers, tostring(race), tostring(class))
end

-- An item's name as the game itself writes it, in the player's language, when the game has the item at hand
-- (it has, for anything it has shown: an auction scan covers most materials). Asks the server nothing.
local function ownItemName(id)
  local kept = type(WispData) == "table" and type(WispData.items) == "table" and WispData.items[id]
  if type(kept) == "string" and kept ~= "" then return kept end
  local I = C_Item
  if I and I.IsItemDataCachedByID and I.GetItemNameByID then
    local okC, cached = pcall(I.IsItemDataCachedByID, id)
    if okC and cached then
      local okN, name = pcall(I.GetItemNameByID, id)
      if okN and type(name) == "string" and name ~= "" then return name end
    end
  end
end

local function ask(fn, id, field)   -- one field of one Questie record, or nil
  if type(fn) ~= "function" then return nil end
  local ok, value = pcall(fn, id, field)
  if ok then return value end
end

local function ids(list)
  if type(list) ~= "table" then return nil end
  local out = {}
  for _, v in ipairs(list) do if type(v) == "number" then out[#out + 1] = v end end
  return #out > 0 and out or nil
end

-- Where best to look for a creature or an object, from all the points Questie has for it: the zone that
-- has most of them and, there, the real point nearest their middle (a waypoint in the thick of them, not
-- on a stray one at the edge). Returns the place and how many points that zone has; for something found
-- only inside an instance, no place and that instance's name.
local function bestPlace(spawns, ZoneDB)
  local best, bestCount, bestZone, inside = nil, 0, nil, nil
  for zone, points in pairs(type(spawns) == "table" and spawns or {}) do
    local open, sx, sy = {}, 0, 0
    for _, p in ipairs(type(points) == "table" and points or {}) do
      if type(p) == "table" and type(p[1]) == "number" and type(p[2]) == "number" then
        if p[1] >= 0 then   -- Questie marks "inside an instance" as -1
          open[#open + 1] = p
          sx, sy = sx + p[1], sy + p[2]
        elseif type(zone) == "number" and (inside == nil or zone < inside) then
          inside = zone
        end
      end
    end
    if #open > bestCount or (#open > 0 and #open == bestCount and type(zone) == "number" and zone < bestZone) then
      local ok, map = pcall(ZoneDB.GetUiMapIdByAreaId, ZoneDB, zone)
      if ok and map then
        local mx, my, near, nearest = sx / #open, sy / #open, nil, nil
        for _, p in ipairs(open) do
          local d = (p[1] - mx) ^ 2 + (p[2] - my) ^ 2
          if not nearest or d < nearest then near, nearest = p, d end
        end
        best, bestCount, bestZone = { map = map, zone = mapName(map), x = round1(near[1]), y = round1(near[2]) }, #open, zone
      end
    end
  end
  if best then return best, bestCount end
  local okA, area = pcall(C_Map.GetAreaInfo, inside)
  return nil, 0, inside and okA and type(area) == "string" and area or nil
end

-- Names for a set of ids, each with one place where Questie has one, and the maps those places are on.
local function namer(ZoneDB)
  local maps, mapSeen = {}, {}
  return function(fn, wanted, located)
    local out, n = {}, 0
    for id in pairs(wanted) do
      n = n + 1
      if n % 300 == 0 then coroutine.yield() end
      local name = ask(fn, id, "name")
      if type(name) == "string" and name ~= "" then
        local entry = { id = id, name = name }
        local okP, p, points, inside = pcall(bestPlace, located and ask(fn, id, "spawns"), ZoneDB)
        if okP and p then
          entry.map, entry.x, entry.y, entry.n = p.map, p.x, p.y, points
          if not mapSeen[p.map] then mapSeen[p.map] = true; maps[#maps + 1] = { id = p.map, name = p.zone } end
        elseif okP and inside then
          entry.at = inside   -- only inside an instance: its name, and no point on a map
        end
        out[#out + 1] = entry
      end
    end
    return out
  end, maps
end

-- Whose names Wisp shows, for Check setup: for a few of the character's own quests, the name Questie's
-- database gives next to the one the game itself gives, and the same for the people those quests go back
-- to. Names of quests and people come from Questie; if those are in another language than the game's, this
-- shows it, and shows whether the game could be asked instead. A look, not a fix.
local function nameProbe(DB)
  local out = { locale = GetLocale(), quests = {}, people = {},
                canAskTitle = (C_QuestLog and C_QuestLog.GetTitleForQuestID) and true or false,
                canAskPerson = (C_TooltipInfo and C_TooltipInfo.GetHyperlink) and true or false }
  local okL, l10n = pcall(questieModule, "l10n")
  if okL and type(l10n) == "table" and type(l10n.GetUILocale) == "function" then
    local okU, ui = pcall(l10n.GetUILocale, l10n)
    out.questieLocale = okU and type(ui) == "string" and ui or nil
  end
  if type(DB) ~= "table" then return out end
  local function person(id)
    if type(id) ~= "number" or #out.people >= 3 then return end
    local row = { id = id, questie = ask(DB.QueryNPCSingle, id, "name") }
    if out.canAskPerson then   -- what the game's own tooltip for that creature says, if the game knows it yet
      local ok, data = pcall(C_TooltipInfo.GetHyperlink, "unit:Creature-0-0-0-0-" .. id .. "-0000000000")
      local line = ok and type(data) == "table" and type(data.lines) == "table" and data.lines[1]
      row.game = type(line) == "table" and type(line.leftText) == "string" and clip(line.leftText, 60) or nil
    end
    if type(row.questie) == "string" then row.questie = clip((row.questie:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")), 60) end
    out.people[#out.people + 1] = row
  end
  for i = 1, C_QuestLog.GetNumQuestLogEntries() do
    local info = C_QuestLog.GetInfo(i)
    if info and not info.isHeader and not info.isHidden and #out.quests < 3 then
      local id = info.questID
      local row = { id = id, log = info.title, questie = ask(DB.QueryQuestSingle, id, "name") }
      if out.canAskTitle then
        local ok, title = pcall(C_QuestLog.GetTitleForQuestID, id)
        row.game = ok and type(title) == "string" and title or nil
      end
      if type(row.questie) == "string" then row.questie = clip(row.questie, 60) end
      out.quests[#out.quests + 1] = row
      local ends = ask(DB.QueryQuestSingle, id, "finishedBy")
      person(type(ends) == "table" and type(ends[1]) == "table" and ends[1][1] or nil)
    end
  end
  return out
end

local function buildQuestIndex(DB, ZoneDB, key)
  local Player, Corrections = questieModule("QuestiePlayer"), questieModule("QuestieCorrections")
  local XP = questieModule("QuestXP")
  if type(XP) ~= "table" or type(XP.GetQuestLogRewardXP) ~= "function" then XP = nil end
  local hidden = type(Corrections) == "table" and type(Corrections.hiddenQuests) == "table" and Corrections.hiddenQuests or {}
  local function allowed(check, mask)   -- a quest for another race or class is not this character's
    if type(mask) ~= "number" or mask == 0 or type(Player) ~= "table" or type(Player[check]) ~= "function" then return true end
    local ok, yes = pcall(Player[check], mask)
    return not ok or yes and true or false
  end
  local function firsts(list, at)   -- objective records: the id is in one slot of each
    local out = {}
    for _, o in ipairs(type(list) == "table" and list or {}) do
      if type(o) == "table" and type(o[at]) == "number" then out[#out + 1] = o[at] end
    end
    return out
  end
  local zoneOf = {}
  local function zoneName(area)
    if type(area) ~= "number" or area <= 0 then return nil end   -- below zero: a class or profession, not a place
    if zoneOf[area] == nil then
      local ok, map = pcall(ZoneDB.GetUiMapIdByAreaId, ZoneDB, area)
      zoneOf[area] = ok and map and mapName(map) or false
    end
    return zoneOf[area] or nil
  end
  local kinds = { { "elite", DB.IsGroupQuest }, { "dungeon", DB.IsDungeonQuest }, { "raid", DB.IsRaidQuest }, { "pvp", DB.IsPvPQuest } }
  local order = {}
  for id in pairs(DB.QuestPointers) do if type(id) == "number" then order[#order + 1] = id end end
  table.sort(order)
  local Q = DB.QueryQuestSingle
  local quests, wantNpc, wantObject, wantItem = {}, {}, {}, {}
  local function want(set, list) for _, id in ipairs(list or {}) do set[id] = true end end
  for i, id in ipairs(order) do
    if i % QUESTS_PER_SLICE == 0 then coroutine.yield() end
    local name = ask(Q, id, "name")
    if type(name) == "string" and name ~= "" and not hidden[id]
       and allowed("HasRequiredRace", ask(Q, id, "requiredRaces")) and allowed("HasRequiredClass", ask(Q, id, "requiredClasses")) then
      local startedBy, finishedBy = ask(Q, id, "startedBy"), ask(Q, id, "finishedBy")
      startedBy, finishedBy = type(startedBy) == "table" and startedBy or {}, type(finishedBy) == "table" and finishedBy or {}
      local objectives, text = ask(Q, id, "objectives"), ask(Q, id, "objectivesText")
      objectives = type(objectives) == "table" and objectives or {}
      local level, req = ask(Q, id, "questLevel"), ask(Q, id, "requiredLevel")
      local q = { id = id, name = name, level = type(level) == "number" and level or nil, req = type(req) == "number" and req or nil,
                  zone = zoneName(ask(Q, id, "zoneOrSort")),
                  sn = ids(startedBy[1]), so = ids(startedBy[2]), si = ids(startedBy[3]), en = ids(finishedBy[1]), eo = ids(finishedBy[2]),
                  pre = ids(ask(Q, id, "preQuestSingle")), preAll = ids(ask(Q, id, "preQuestGroup")), excl = ids(ask(Q, id, "exclusiveTo")),
                  text = type(text) == "table" and type(text[1]) == "string" and clip(text[1], 240) or nil }
      if XP then   -- what handing it in gives, as Questie reckons it: for the later parts of a chain, which the game does not show yet
        local okX, xp = pcall(XP.GetQuestLogRewardXP, XP, id, true)
        if okX and type(xp) == "number" and xp > 0 then q.xp = xp end
      end
      local skill = ask(Q, id, "requiredSkill")   -- { skill line, value }: how a profession's next rank is earned, where that is a quest
      if type(skill) == "table" and type(skill[1]) == "number" and type(skill[2]) == "number" then q.sk = { skill[1], skill[2] } end
      local nextId, flags = ask(Q, id, "nextQuestInChain"), ask(Q, id, "specialFlags")
      if type(nextId) == "number" and nextId > 0 then q.next = nextId end
      if type(flags) == "number" and flags % 2 == 1 then q.repeatable = true end
      local kill = firsts(objectives[1], 1)
      for _, credit in ipairs(firsts(objectives[5], 2)) do kill[#kill + 1] = credit end   -- kill credit: the creature it is named after
      q.kill, q.use, q.items = ids(kill), ids(firsts(objectives[2], 1)), ids(firsts(objectives[3], 1))
      for _, test in ipairs(kinds) do
        if not q.kind and type(test[2]) == "function" then
          local okT, yes = pcall(test[2], id)
          if okT and yes then q.kind = test[1] end
        end
      end
      want(wantNpc, q.sn); want(wantNpc, q.en); want(wantNpc, q.kill)
      want(wantObject, q.so); want(wantObject, q.eo); want(wantObject, q.use)
      want(wantItem, q.si); want(wantItem, q.items)
      quests[#quests + 1] = q
    end
  end
  local named, maps = namer(ZoneDB)   -- who and what the quests name, each with one place where Questie has one
  local npcs = named(DB.QueryNPCSingle, wantNpc, true)
  local objects = named(DB.QueryObjectSingle, wantObject, true)
  local items = named(DB.QueryItemSingle, wantItem, false)
  for _, entry in ipairs(items) do entry.name = ownItemName(entry.id) or entry.name end
  return { v = 1, key = key, quests = quests, npcs = npcs, objects = objects, items = items, maps = maps }
end

-- Questie's whole item list, the same way (WispItems, WispItemsKey): every item somebody sells,
-- something drops or something holds, so that "who sells this recipe" has an answer for an item
-- the character has never touched. Per item: vendors friendly to this character (up to four),
-- the first few creatures and objects it comes from and how many there are in all.
-- Which profession a recipe item or a skill book belongs to, by the game's own class of the item: a
-- profession's books are "recipe" items of that profession, a fishing pole is not. The skill line's
-- number, or 0 for a book of no one profession; nil for anything that is not a recipe item.
local RECIPE_CLASS = 9
local RECIPE_OF = { [0] = 0, [1] = 165, [2] = 197, [3] = 202, [4] = 164, [5] = 185, [6] = 171, [7] = 129, [8] = 333, [9] = 356, [10] = 755, [11] = 773 }
local function recipeProfession(id)
  local instant = (C_Item and C_Item.GetItemInfoInstant) or GetItemInfoInstant
  if type(instant) ~= "function" then return nil end
  local ok, _, _, _, _, _, class, subclass = pcall(instant, id)
  if ok and class == RECIPE_CLASS then return RECIPE_OF[subclass] end
end

-- An item's rarity, as the game colours its name: 0 poor, 1 common, 2 uncommon, 3 rare, 4 epic, 5 legendary.
-- The game tells it only for an item it has described, so it is learned in the background, a little at a
-- time, for every item Wisp knows by name or from Questie's list, and kept for good.
--   WispData.quality[item id] = 0..7
local function rarityOf(id)
  local I = C_Item
  if I and I.GetItemQualityByID then
    local ok, quality = pcall(I.GetItemQualityByID, id)
    if ok and type(quality) == "number" then return quality end
  end
end

local function noteRarity(id)
  if type(id) ~= "number" then return end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.quality = WispData.quality or {}
  if WispData.quality[id] == nil then WispData.quality[id] = rarityOf(id) end
end

local rarityIds, rarityAt, rarityAsked
local function learnRarity()
  if type(WispData) ~= "table" then WispData = {} end
  WispData.quality = WispData.quality or {}
  if not rarityIds then   -- once a session: what is not known yet
    rarityIds, rarityAt, rarityAsked = {}, 1, {}
    local seen = {}
    local function want(id)
      if type(id) == "number" and not seen[id] and WispData.quality[id] == nil then seen[id] = true; rarityIds[#rarityIds + 1] = id end
    end
    for id in pairs(type(WispData.items) == "table" and WispData.items or {}) do want(id) end
    local DB = questieModule("QuestieDB")
    for id in pairs(type(DB) == "table" and type(DB.ItemPointers) == "table" and DB.ItemPointers or {}) do want(id) end
    table.sort(rarityIds)
  end
  for _, id in ipairs(rarityAsked) do noteRarity(id) end   -- those asked for last time: the game has had its moment
  rarityAsked = {}
  local looked = 0
  while rarityAt <= #rarityIds and #rarityAsked < 150 and looked < 3000 do
    local id = rarityIds[rarityAt]
    rarityAt, looked = rarityAt + 1, looked + 1
    local quality = rarityOf(id)
    if quality then
      WispData.quality[id] = quality
    else
      rarityAsked[#rarityAsked + 1] = id
      if C_Item and C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, id) end
    end
  end
  if rarityAt <= #rarityIds or #rarityAsked > 0 then C_Timer.After(2, function() pcall(learnRarity) end) end
end

local function rarities()   -- every item that is not common (white), by rarity: the common ones need no colour
  local known = type(WispData) == "table" and WispData.quality
  if type(known) ~= "table" then return nil end
  local out, count = {}, 0
  for id, quality in pairs(known) do
    if type(id) == "number" and type(quality) == "number" and quality ~= 1 and quality >= 0 and quality <= 7 and count < 20000 then
      local key = tostring(quality)
      out[key] = out[key] or {}
      out[key][#out[key] + 1] = id
      count = count + 1
    end
  end
  for _, ids in pairs(out) do table.sort(ids) end   -- the same file whatever order the game walked them in
  return count > 0 and out or nil
end

-- The skill at which a profession's next step can be taken, from the game's own words for it.
-- A book or a recipe says it on itself ("Requires First Aid (125)"): the game's own format for that line
-- (ITEM_MIN_SKILL, in the player's language) is turned into a pattern, so nothing is guessed from wording.
--   WispData.itemSkill[item id] = the skill needed, 0 when the item states none
local skillLine
do
  local format = type(ITEM_MIN_SKILL) == "string" and ITEM_MIN_SKILL or nil
  if format then
    local text = format:gsub("%%%d?%$?s", "\1"):gsub("%%%d?%$?d", "\2")      -- set the two blanks aside
    text = text:gsub("[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0")                    -- the rest is literal
    skillLine = "^" .. text:gsub("\1", ".+"):gsub("\2", "(%%d+)") .. "$"
  end
end

local function skillNeededBy(id)   -- nil while the game has not described the item yet
  local T = C_TooltipInfo
  if not (skillLine and T and T.GetItemByID) then return nil end
  local ok, data = pcall(T.GetItemByID, id)
  if not ok or type(data) ~= "table" or type(data.lines) ~= "table" or #data.lines < 2 then return nil end
  for _, line in ipairs(data.lines) do
    local text = type(line) == "table" and line.leftText
    local need = type(text) == "string" and text:match(skillLine)
    if need then return tonumber(need) end
  end
  return 0
end

local function learnItemSkills(pass)   -- a couple of hundred at a time; an item the game must fetch first is read on a later pass
  local ids = type(WispData) == "table" and WispData.recipeItems
  if type(ids) ~= "table" then return end
  WispData.itemSkill = WispData.itemSkill or {}
  local waiting = 0
  for _, id in ipairs(ids) do
    if WispData.itemSkill[id] == nil then
      local need = skillNeededBy(id)
      if need then
        WispData.itemSkill[id] = need
      else
        waiting = waiting + 1
        if waiting > 200 then break end
        if C_Item and C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, id) end
      end
    end
  end
  pass = pass or 1
  if waiting > 0 and pass < 20 and skillLine and C_TooltipInfo then C_Timer.After(3, function() pcall(learnItemSkills, pass + 1) end) end
end

local function itemSkills()
  local known = type(WispData) == "table" and WispData.itemSkill
  if type(known) ~= "table" then return nil end
  local out = {}
  for id, need in pairs(known) do
    if type(id) == "number" and type(need) == "number" and need > 0 and #out < 3000 then out[#out + 1] = { id, need } end
  end
  return #out > 0 and out or nil
end

-- A trainer says it in its window: each thing it teaches, with the skill and the level it needs and what it
-- costs. Read while the window of a profession's trainer is open, as the player sees it (its filters are
-- left alone), and kept for every trainer visited, by any character.
--   WispData.trainers[npc id] = { at, skill = the profession's name, services = { { name, rank, skill needed, level, cost, known } } }
local function trainerSeen()
  if type(GetNumTrainerServices) ~= "function" or type(GetTrainerServiceInfo) ~= "function" then return end
  if type(IsTradeskillTrainer) == "function" and not IsTradeskillTrainer() then return end   -- a class's trainer, not a profession's
  local guid = UnitGUID("npc")
  local id = type(guid) == "string" and tonumber((select(6, strsplit("-", guid)))) or nil
  if not id then return end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.trainers = WispData.trainers or {}
  local kept = WispData.trainers[id] or { services = {} }
  local at, skills = {}, {}
  for index, service in ipairs(kept.services) do at[tostring(service[1]) .. "|" .. tostring(service[2])] = index end
  for i = 1, math.min(tonumber(GetNumTrainerServices()) or 0, 300) do
    local name, rank, state = GetTrainerServiceInfo(i)
    if type(name) == "string" and name ~= "" and state ~= "header" then
      local skill, need = nil, nil
      if type(GetTrainerServiceSkillReq) == "function" then skill, need = GetTrainerServiceSkillReq(i) end
      if type(skill) == "string" and skill ~= "" then skills[skill] = (skills[skill] or 0) + 1 end
      local row = { clip((name:gsub("|", "")), 60), type(rank) == "string" and rank ~= "" and clip((rank:gsub("|", "")), 30) or false,
                    tonumber(need) or 0, type(GetTrainerServiceLevelReq) == "function" and tonumber(GetTrainerServiceLevelReq(i)) or 0,
                    type(GetTrainerServiceCost) == "function" and tonumber(GetTrainerServiceCost(i)) or 0, state == "used" and 1 or 0 }
      local key = tostring(row[1]) .. "|" .. tostring(row[2])
      if at[key] then kept.services[at[key]] = row elseif #kept.services < 300 then kept.services[#kept.services + 1] = row; at[key] = #kept.services end
    end
  end
  if #kept.services == 0 then return end
  local most = 0
  for skill, count in pairs(skills) do if count > most then kept.skill, most = skill, count end end
  kept.at = time()
  WispData.trainers[id] = kept
end

local function trainerWindows()
  local seen = type(WispData) == "table" and WispData.trainers
  if type(seen) ~= "table" then return nil end
  local out = {}
  for id, one in pairs(seen) do
    if type(id) == "number" and type(one) == "table" and type(one.services) == "table" then
      out[#out + 1] = { id = id, at = one.at, skill = one.skill, services = one.services }
    end
  end
  table.sort(out, function(a, b) return (a.at or 0) > (b.at or 0) end)
  while #out > 12 do table.remove(out) end   -- the trainers seen last: a dozen is every profession a player keeps
  return #out > 0 and out or nil
end

local function buildItemIndex(DB, ZoneDB, key)
  local Q = DB.QueryItemSingle
  local friendly = {}
  local function sells(npc)   -- not the other faction's vendors
    if friendly[npc] == nil then
      local ok, yes = pcall(DB.IsFriendlyToPlayer, ask(DB.QueryNPCSingle, npc, "friendlyToFaction"))
      friendly[npc] = (not ok or yes) and true or false
    end
    return friendly[npc]
  end
  local walkable = {}
  local function reachable(fn, id)   -- has a point on a map one can walk to
    local key = (fn == DB.QueryNPCSingle and "n" or "o") .. id
    if walkable[key] == nil then
      local ok, place = pcall(bestPlace, ask(fn, id, "spawns"), ZoneDB)
      walkable[key] = ok and place ~= nil
    end
    return walkable[key]
  end
  -- A few of a long list: of its first twelve, those one can walk to before those inside an instance.
  local function first(list, most, fn)
    local out, rest = {}, {}
    for i = 1, math.min(#list, 12) do
      if #out < most and reachable(fn, list[i]) then out[#out + 1] = list[i] else rest[#rest + 1] = list[i] end
    end
    for _, id in ipairs(rest) do
      if #out < most then out[#out + 1] = id end
    end
    return out
  end
  local order = {}
  for id in pairs(DB.ItemPointers) do if type(id) == "number" then order[#order + 1] = id end end
  table.sort(order)
  local items, wantNpc, wantObject, sold = {}, {}, {}, {}
  for i, id in ipairs(order) do
    if i % QUESTS_PER_SLICE == 0 then coroutine.yield() end
    if #items >= 30000 then break end
    local name = ownItemName(id) or ask(Q, id, "name")   -- Questie's may be English where the game's is not
    if type(name) == "string" and name ~= "" then
      local vendors, drops, objects = {}, ids(ask(Q, id, "npcDrops")) or {}, ids(ask(Q, id, "objectDrops")) or {}
      for _, npc in ipairs(ids(ask(Q, id, "vendors")) or {}) do
        if #vendors < 4 and sells(npc) then vendors[#vendors + 1] = npc end
      end
      if #vendors + #drops + #objects > 0 then
        local entry = { id = id, name = name, b = recipeProfession(id) }
        if #vendors > 0 then entry.v = vendors end
        if entry.b and #vendors > 0 and #sold < 2000 then sold[#sold + 1] = id end
        if #drops > 0 then entry.d, entry.dn = first(drops, 3, DB.QueryNPCSingle), #drops end
        if #objects > 0 then entry.o, entry.on = first(objects, 2, DB.QueryObjectSingle), #objects end
        for _, npc in ipairs(vendors) do wantNpc[npc] = true end
        for _, npc in ipairs(entry.d or {}) do wantNpc[npc] = true end
        for _, object in ipairs(entry.o or {}) do wantObject[object] = true end
        items[#items + 1] = entry
      end
    end
  end
  -- Who trains each profession: Questie's own list of townsfolk, the ones this character can talk to.
  -- With the books (items, above) and the quests that need a skill, it is how every profession goes on.
  local trainers, trains = {}, {}
  local Townsfolk = questieModule("Townsfolk")
  local listed = type(Townsfolk) == "table" and type(Townsfolk.professionTrainers) == "table" and Townsfolk.professionTrainers or {}
  for profession, theirs in pairs(listed) do
    if type(profession) == "number" and type(theirs) == "table" then
      local those = {}
      for _, npc in ipairs(theirs) do
        if type(npc) == "number" and #those < 80 and sells(npc) then
          those[#those + 1], wantNpc[npc], trains[npc] = npc, true, true
        end
      end
      if #those > 0 then trainers[#trainers + 1] = { p = profession, n = those } end
    end
  end
  local named, maps = namer(ZoneDB)
  local npcs = named(DB.QueryNPCSingle, wantNpc, true)
  for _, npc in ipairs(npcs) do   -- a trainer's title often says the rank it teaches: "Expert Tailor"
    local title = trains[npc.id] and ask(DB.QueryNPCSingle, npc.id, "subName")
    if type(title) == "string" and title ~= "" then npc.title = clip((title:gsub("|", "")), 60) end
  end
  local objects = named(DB.QueryObjectSingle, wantObject, true)
  if type(WispData) ~= "table" then WispData = {} end
  WispData.recipeItems = sold   -- skill books and recipes someone sells: what each needs is read from the item itself, below
  return { v = 1, key = key, items = items, npcs = npcs, objects = objects, maps = maps, trainers = trainers }
end

-- Every recipe in the game, whatever professions this character has (WispRecipes, WispRecipesKey):
-- what each makes and from what, so an item can be followed up to everything it goes into and a
-- product down to its raw materials. The profession window lists only one's own professions, but
-- the client holds every recipe and gives one by its place in the list of skill abilities: those
-- numbers are walked once per game version, a slice per frame. A client that answers nothing
-- leaves an empty list behind, so it is not asked again until the game changes.
local ABILITIES, ABILITIES_PER_SLICE = 80000, 1000

local function recipeIndexKey()
  local version, build = GetBuildInfo()
  return table.concat({ "r1", tostring(version), tostring(build), GetLocale() }, "|")
end

local function buildRecipeIndex(key)
  local T = C_TradeSkillUI
  local basic = Enum and Enum.CraftingReagentType and Enum.CraftingReagentType.Basic
  local seen, recipes, professions, professionAt, found = {}, {}, {}, {}, 0
  for ability = 1, ABILITIES do
    if ability % ABILITIES_PER_SLICE == 0 then coroutine.yield() end
    local ok, r = pcall(T.GetRecipeInfoForSkillLineAbility, ability)
    if ok and type(r) == "table" and type(r.recipeID) == "number" and type(r.name) == "string" and r.name ~= "" and not seen[r.recipeID]
       and not (r.isDummyRecipe or r.isGatheringRecipe or r.isRecraft or r.isSalvageRecipe) then
      seen[r.recipeID] = true
      found = found + 1
      if found % 40 == 0 then coroutine.yield() end   -- a schematic costs more than a look
      local okS, plan = pcall(T.GetRecipeSchematic, r.recipeID, false)
      local reagents = {}
      for _, slot in ipairs(okS and type(plan) == "table" and plan.reagentSlotSchematics or {}) do
        local first = slot.reagents and slot.reagents[1]
        if first and first.itemID and (slot.quantityRequired or 0) > 0 and slot.required ~= false
           and (basic == nil or slot.reagentType == nil or slot.reagentType == basic) then
          reagents[#reagents + 1] = first.itemID
          reagents[#reagents + 1] = slot.quantityRequired
        end
      end
      if #reagents > 0 then
        local okP, info = pcall(T.GetProfessionInfoByRecipeID, r.recipeID)
        local name = okP and type(info) == "table" and (info.parentProfessionName or info.professionName) or "?"
        if not professionAt[name] then professions[#professions + 1] = name; professionAt[name] = #professions end
        recipes[#recipes + 1] = { id = r.recipeID, name = r.name, p = professionAt[name], makes = plan.outputItemID,
                                  n = plan.quantityMin, grey = r.maxTrivialLevel, r = reagents }
      end
    end
  end
  return { v = 1, key = key, professions = professions, recipes = recipes }
end

-- One index at a time, a slice per frame; what was kept before stays if anything goes wrong.
local indexRun, indexQueue, indexGoing, indexQueued = nil, {}, false, {}
local function indexStep()
  if not indexRun then
    indexRun = table.remove(indexQueue, 1)
    if not indexRun then indexGoing = false; return end
  end
  local ok, index = coroutine.resume(indexRun.run)
  if ok and coroutine.status(indexRun.run) ~= "dead" then return C_Timer.After(0, indexStep) end
  local keep, name = indexRun.keep, indexRun.name
  indexRun = nil
  local why = not ok and tostring(index) or nil   -- what went wrong, for Wisp's Check setup: nothing is said in chat
  if ok and type(index) == "table" then
    local okJ, text = pcall(function() return E.EncodeBase64(E.SerializeJSON(index)) end)
    if okJ and type(text) == "string" then keep(text, index.key) else why = "could not be written: " .. tostring(text) end
    if name == "items" then pcall(learnItemSkills) end   -- the list of sold recipe items is fresh: what each needs, next
  end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.indexFailed = WispData.indexFailed or {}
  WispData.indexFailed[name] = why and clip(why, 300) or nil
  C_Timer.After(0, indexStep)   -- the next one, if any
end

local function queueIndex(name, build, keep)   -- each index once per session
  if indexQueued[name] or not (E and E.SerializeJSON and E.EncodeBase64) then return end
  indexQueued[name] = true
  indexQueue[#indexQueue + 1] = { name = name, run = coroutine.create(build), keep = keep }
  if not indexGoing then indexGoing = true; indexStep() end
end

local function startRecipeIndex()
  local T = C_TradeSkillUI
  if not (T and T.GetRecipeInfoForSkillLineAbility and T.GetRecipeSchematic and T.GetProfessionInfoByRecipeID) then return end
  local key = recipeIndexKey()
  if key == WispRecipesKey and type(WispRecipes) == "string" then return end
  queueIndex("recipes", function() return buildRecipeIndex(key) end, function(text, built) WispRecipes, WispRecipesKey = text, built end)
end

local function startQuestIndex(tries)
  tries = tries or 0
  local DB, ZoneDB = questieModule("QuestieDB"), questieModule("ZoneDB")
  if not (DB and ZoneDB and type(DB.QueryQuestSingle) == "function" and type(DB.QuestPointers) == "table") then
    if tries < 12 and type(QuestieLoader) == "table" then   -- Questie may still be starting up
      C_Timer.After(10, function() startQuestIndex(tries + 1) end)
    end
    return
  end
  local okK, key = pcall(questIndexKey, DB)
  if okK and not (key == WispQuestsKey and type(WispQuests) == "string") then
    queueIndex("quests", function() return buildQuestIndex(DB, ZoneDB, key) end, function(text, built) WispQuests, WispQuestsKey = text, built end)
  end
  if type(DB.QueryItemSingle) == "function" and type(DB.ItemPointers) == "table" then
    local okI, itemsKey = pcall(indexKey, DB, DB.ItemPointers, tostring((UnitFactionGroup("player"))))
    if okI and not (itemsKey == WispItemsKey and type(WispItems) == "string") then
      queueIndex("items", function() return buildItemIndex(DB, ZoneDB, itemsKey) end, function(text, built) WispItems, WispItemsKey = text, built end)
    end
  end
end

----------------------------------------------------------- dungeons ----
-- From the DungeonJournal addon, when it is installed: every dungeon with its
-- entrance, its quests and what each needs first, its bosses and their loot.
-- Wisp reads the journal's own tables as they are loaded in the game; it does
-- not carry a copy of them.

-- The journal's tables are written in English; the addon translates them for the player through its own
-- functions (it ships the translations). Wisp asks through the same functions, so a dungeon, its quests,
-- its bosses and its places read here as they do in the journal's own window, in the journal's language.
local function journalSays(fn, fallback, ...)
  local J = _G.ForeverDungeonJournal_NS
  if type(J) == "table" and type(J[fn]) == "function" then
    local ok, text = pcall(J[fn], ...)
    if ok and type(text) == "string" and text ~= "" then return text end
  end
  return fallback
end
local function journalText(text) return journalSays("LocalizeFreeText", text, text) end

local function journalPlace(m)   -- the journal keeps positions as 0-1 on a map id
  if type(m) ~= "table" or type(m.mapID) ~= "number" or type(m.x) ~= "number" or type(m.y) ~= "number" then return nil end
  return { map = m.mapID, zone = mapName(m.mapID), x = round1(m.x * 100), y = round1(m.y * 100), label = journalText(m.targetName or m.label) }
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
      local dungeon = { name = journalSays("LocalizeDungeonName", name, name), level = d.level,
                        location = journalSays("LocalizeDungeonField", d.location, name, "location", d.location), quests = {}, bosses = {},
                        entrance = journalPlace(d.entrance), entranceHint = type(d.entrance) == "table" and journalText(d.entrance.detail) or nil }
      for _, q in ipairs(d.quests or {}) do
        local mine = q.faction == nil or q.faction == "Both" or q.faction == faction
        if type(q.id) == "number" and mine and not q.hideFromMainList then
          local said = function(field, text) return journalSays("LocalizeQuestField", text, q.id, field, text) end
          local quest = { id = q.id, name = said("name", q.name), level = q.level, requires = q.requires, class = q.classOnly,
                          objective = said("objective", q.objective), pickup = said("pickup", q.pickup), turnin = said("turnin", q.turnin),
                          rewards = journalText(q.rewards or q.rewardSummary), state = questState(q.id),
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
              quest.needs[#quest.needs + 1] = { id = step.id, name = journalSays("LocalizeQuestField", step.name, step.id, "name", step.name),
                                                state = questState(step.id), pickup = journalText(detail.pickup),
                                                start = journalPlace(detail.map or starts[step.id]) }
            end
          end
          dungeon.quests[#dungeon.quests + 1] = quest
        end
      end
      for _, b in ipairs(d.bosses or {}) do
        local boss = { name = journalSays("LocalizeBossName", b.name, b.name), rare = b.rare and true or nil, loot = {} }
        for _, item in ipairs(b.loot or {}) do
          if type(item) == "table" and type(item[1]) == "number" then
            -- id, name (the game's own for that item where it has one), slot, quality
            boss.loot[#boss.loot + 1] = { item[1], ownItemName(item[1]) or item[2], journalSays("LocalizeItemSlot", item[3], item[3]), item[4] }
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

-- ---- how the character died -------------------------------------------------------------------
-- The game's own combat log, as every damage meter reads it, and only what touches this character:
-- the last three minutes of it are kept in memory, and when the character dies the fight is filed as
-- one record: the whole fight when another player did it (back to its first blow, three minutes at most;
-- twenty seconds with nothing happening is where a fight began), the last half minute otherwise. Another player is written down by class and race, never by name: the name is not
-- kept anywhere, not even in the game's own file. A creature keeps its name.
--   WispData.deaths[character] = { { at, map, zone, level, maxHealth, pvp, who = { ... }, log = { ... } }, ... }
--   who[i] = { class, race, level, pet = owner's index, name = a creature's }      log[j] = { seconds before death, kind, who, spell, amount, extra, health }
-- A game with "secret values" (WoW Forever, Retail since 12.0) keeps the combat log from addons: there even
-- registering for it is a blocked action, and the player gets a warning for it. So where the game has secret
-- values the log is not asked for at all, nothing is recorded, and Wisp says that deaths cannot be read here.
local COMBAT_LOG_OPEN = type(issecretvalue) ~= "function"
local FIGHT_SECONDS, PVP_SECONDS, QUIET, FIGHT_EVENTS = 30, 180, 20, 400
local KEPT = { [true] = 12, [false] = 4 }   -- deaths to a player, and others: one kind does not push the other out
local fight, fighters, fighterOf = {}, {}, {}
local band = bit and bit.band   -- without it a flag cannot be read, and then nothing is recorded: never a player taken for a creature
local PLAYER_KIND, PLAYER_OWNED, HOSTILE = 0x400, 0x100, 0x40   -- the combat log's own flags: a player, something a player controls, an enemy

local function fighter(guid, name, flags)   -- who did something, as a small number; a player has no name here
  if type(guid) ~= "string" or guid == "" or guid == UnitGUID("player") then return 0 end   -- 0: the character itself, or nobody
  if fighterOf[guid] then return fighterOf[guid] end
  flags = tonumber(flags) or 0
  local one = { enemy = band(flags, HOSTILE) ~= 0 or nil }
  if band(flags, PLAYER_KIND) ~= 0 then
    local ok, _, class, _, race = pcall(GetPlayerInfoByGUID, guid)
    one.player, one.class, one.race = true, ok and class or nil, ok and race or nil
    for _, unit in ipairs({ "target", "mouseover", "focus" }) do   -- the level only shows on someone looked at
      if UnitGUID(unit) == guid then
        local level = UnitLevel(unit)
        one.level = type(level) == "number" and level > 0 and level or nil
      end
    end
  elseif band(flags, PLAYER_OWNED) ~= 0 then
    one.pet = true   -- a player's pet or totem: its name is the player's choice, so it is left out as well
  else
    one.name = type(name) == "string" and clip((name:gsub("|", "")), 40) or nil
  end
  fighters[#fighters + 1] = one
  fighterOf[guid] = #fighters
  return #fighters
end

local function fought(kind, who, spell, amount, extra)
  local now = GetTime()
  while fight[1] and (#fight >= FIGHT_EVENTS or now - fight[1].at > PVP_SECONDS) do table.remove(fight, 1) end
  fight[#fight + 1] = { at = GetTime(), kind = kind, who = who, spell = type(spell) == "string" and clip((spell:gsub("|", "")), 50) or nil,
                        amount = type(amount) == "number" and amount or nil, extra = extra, health = UnitHealth("player") }
end

local function combatLogged()
  local me = UnitGUID("player")
  local _, sub, _, srcGUID, srcName, srcFlags, _, dstGUID, _, _, _, a12, a13, a14, a15, a16, a17, a18, _, _, a21 = CombatLogGetCurrentEventInfo()
  if type(sub) ~= "string" or not me or not band then return end
  if srcGUID == me and sub == "SPELL_CAST_SUCCESS" then return fought("cast", 0, a13) end   -- what the character did, whoever it was aimed at
  if dstGUID == me then
    local who = fighter(srcGUID, srcName, srcFlags)
    if sub == "SWING_DAMAGE" then fought("hit", who, nil, a12, (a18 and "crit" or nil))
    elseif sub == "SPELL_DAMAGE" or sub == "RANGE_DAMAGE" then fought("hit", who, a13, a15, (a21 and "crit" or nil))
    elseif sub == "SPELL_PERIODIC_DAMAGE" then fought("tick", who, a13, a15)
    elseif sub == "ENVIRONMENTAL_DAMAGE" then fought("hit", 0, type(a12) == "string" and a12 or nil, a13)
    elseif sub == "SWING_MISSED" then fought("miss", who, nil, nil, type(a12) == "string" and a12:lower() or nil)
    elseif sub == "SPELL_MISSED" or sub == "RANGE_MISSED" then fought("miss", who, a13, nil, type(a15) == "string" and a15:lower() or nil)
    elseif (sub == "SPELL_HEAL" or sub == "SPELL_PERIODIC_HEAL") and type(a15) == "number" then fought("heal", who, a13, a15 - (tonumber(a16) or 0))
    elseif sub == "SPELL_AURA_APPLIED" and a15 == "DEBUFF" then fought("debuff", who, a13)
    elseif sub == "SPELL_AURA_REMOVED" and a15 == "DEBUFF" then fought("debuff_off", who, a13)
    elseif sub == "SPELL_INTERRUPT" then fought("interrupted", who, a16)
    end
  elseif srcGUID == me then
    if sub == "SWING_DAMAGE" then fought("dealt", fighter(dstGUID, nil, 0), nil, a12)
    elseif sub == "SPELL_DAMAGE" or sub == "RANGE_DAMAGE" or sub == "SPELL_PERIODIC_DAMAGE" then fought("dealt", fighter(dstGUID, nil, 0), a13, a15)
    end
  end
end

local function died()
  local now, log, seen, pvp = GetTime(), {}, {}, false
  -- where this fight began: back from the death until nothing had happened for a while, the last half minute always in
  local first, later = #fight + 1, now
  for i = #fight, 1, -1 do
    local e = fight[i]
    if now - e.at > PVP_SECONDS or (later - e.at > QUIET and now - e.at > FIGHT_SECONDS) then break end
    first, later = i, e.at
    local by = fighters[e.who]
    if by and by.enemy and (by.player or by.pet) and (e.kind == "hit" or e.kind == "tick" or e.kind == "debuff") then pvp = true end
  end
  for i = first, #fight do
    local e = fight[i]
    if pvp or now - e.at <= FIGHT_SECONDS then   -- a death to anything else: its last half minute is enough
      log[#log + 1] = { round1(now - e.at), e.kind, e.who, e.spell or false, e.amount or false, e.extra or false, e.health or false }
      seen[e.who] = true
    end
  end
  fight = {}
  if #log == 0 then return end   -- nothing was seen of it: a fall, or the log was not readable
  local who, renumbered = {}, {}
  for index, one in ipairs(fighters) do   -- only those of this fight, numbered afresh
    if seen[index] then
      who[#who + 1] = { class = one.class, race = one.race, level = one.level, player = one.player, pet = one.pet, enemy = one.enemy, name = one.name }
      renumbered[index] = #who
    end
  end
  for _, e in ipairs(log) do e[3] = renumbered[e[3]] or 0 end
  fighters, fighterOf = {}, {}
  if type(WispData) ~= "table" then WispData = {} end
  WispData.deaths = WispData.deaths or {}
  local mine = WispData.deaths[myKey()] or {}
  WispData.deaths[myKey()] = mine
  local map = C_Map.GetBestMapForUnit("player")
  mine[#mine + 1] = { at = time(), map = map, zone = mapName(map), level = UnitLevel("player"), maxHealth = UnitHealthMax("player"),
                      pvp = pvp or nil, who = who, log = log }
  local count = 0   -- the oldest of this kind go once there are too many of it
  for i = #mine, 1, -1 do
    if (mine[i].pvp == true) == pvp then
      count = count + 1
      if count > KEPT[pvp] then table.remove(mine, i) end
    end
  end
end

local function deathRecord()
  local mine = type(WispData) == "table" and type(WispData.deaths) == "table" and WispData.deaths[myKey()]
  return type(mine) == "table" and #mine > 0 and mine or nil
end


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
        source = (not r.learned and T.GetRecipeSourceText) and T.GetRecipeSourceText(id) or nil,
        greyAt = r.maxTrivialLevel, skillUps = r.numSkillUps, skill = info.skillLevel,
        makes = plan.outputItemID, makesMin = plan.quantityMin, makesMax = plan.quantityMax, reagents = reagents,
      }
    end
  end
  if read > 0 and not told[info.professionName] then
    told[info.professionName] = true
    say(L("read %d %s recipes. They reach Wisp when you log out."):format(read, info.professionName))
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
        if item.isBound then line.bound = true end   -- cannot be sold at auction
      end
    end
  end
  return out
end

-- The kept recipes of every character of this account, with item names, and the ids of every item
-- they mention: an alt's professions count too. A book that is not this character's says whose it
-- is by class and level, never by name.
local function crafting(wanted)
  local all = type(WispData) == "table" and type(WispData.recipes) == "table" and WispData.recipes or {}
  local chars = type(WispData) == "table" and type(WispData.chars) == "table" and WispData.chars or {}
  local books = {}
  for key, theirs in pairs(all) do
   local alt = key ~= myKey() and { class = type(chars[key]) == "table" and chars[key].class or "?",
                                    level = type(chars[key]) == "table" and chars[key].level or 0 } or nil
   for profession, book in pairs(type(theirs) == "table" and theirs or {}) do
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
                          makesMin = r.makesMin, makesMax = r.makesMax, reagents = reagents, source = r.source }
    end
    table.sort(list, function(a, b) return a.id < b.id end)
    books[#books + 1] = { profession = profession, seenAt = book.seenAt, skill = book.skill, max = book.max, recipes = list, alt = alt }
   end
  end
  table.sort(books, function(a, b)   -- this character's own first
    if (a.alt == nil) ~= (b.alt == nil) then return a.alt == nil end
    return a.profession < b.profession
  end)
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

--------------------------------------------------------------- experience ----
-- How fast the character levels: experience gained per day, the time it took, and what part
-- came from handing in quests. "secs" is time spent gaining: the gaps between one gain and the
-- next, when under five minutes. Kept for 60 days.
--   WispData.xp[character]["d" .. day] = { gained, secs, last, quests, questXp, questMoney, levels }

local lastXp   -- { xp, max }: where the bar stood at the last look

local function xpDay()
  if type(WispData) ~= "table" then WispData = {} end
  WispData.xp = WispData.xp or {}
  local mine = WispData.xp[myKey()] or {}
  WispData.xp[myKey()] = mine
  local day = "d" .. math.floor(time() / 86400)
  mine[day] = mine[day] or { gained = 0, secs = 0, quests = 0, questXp = 0, questMoney = 0, levels = 0 }
  return mine[day]
end

local function xpChanged()
  local xp, max = UnitXP("player"), UnitXPMax("player")
  if type(xp) ~= "number" or type(max) ~= "number" then return end
  if lastXp then
    local gained
    if max ~= lastXp.max then gained = (lastXp.max - lastXp.xp) + xp   -- a level was gained: the rest of the old bar, and the new one so far
    else gained = xp - lastXp.xp end
    if gained > 0 then
      local today, now = xpDay(), time()
      today.gained = today.gained + gained
      if max ~= lastXp.max then today.levels = today.levels + 1 end
      if today.last and now - today.last <= 300 then today.secs = today.secs + (now - today.last) end
      today.last = now
    end
  end
  lastXp = { xp = xp, max = max }
end

local function questHandedIn(_, xp, money)
  local today = xpDay()
  today.quests = today.quests + 1
  today.questXp = today.questXp + (tonumber(xp) or 0)
  today.questMoney = today.questMoney + (tonumber(money) or 0)
end

local function xpRecord()
  local mine = type(WispData) == "table" and type(WispData.xp) == "table" and WispData.xp[myKey()]
  local days, oldest = {}, math.floor(time() / 86400) - 60
  for key, d in pairs(type(mine) == "table" and mine or {}) do
    local day = tonumber(tostring(key):sub(2)) or 0
    if day < oldest then
      mine[key] = nil
    else
      days[#days + 1] = { day = day, gained = d.gained, secs = d.secs, quests = d.quests, questXp = d.questXp,
                          questMoney = d.questMoney, levels = d.levels }
    end
  end
  table.sort(days, function(a, b) return a.day < b.day end)
  return { rested = GetXPExhaustion and GetXPExhaustion() or nil, days = days }
end

------------------------------------------------------------------ the bank ----
-- What is in the bank, read while its window is open and kept, since it is shut at logout.
-- Forever's bank is a set of tabs, each a container: the character's own, and the account's,
-- which every character of the account shares.
--   WispData.bank[character] = { seenAt, items = { { id, name, count, bound, shared }, ... } }

local bankOpen = false

local function bankSeen()
  local C, B = C_Container, C_Bank
  if not (bankOpen and C and C.GetContainerNumSlots and C.GetContainerItemInfo and B and B.FetchPurchasedBankTabData) then return end
  local kinds = Enum and Enum.BankType or {}
  local seen, out = {}, {}
  for _, bank in ipairs({ { kinds.Character or 0, false }, { kinds.Account or 2, true } }) do
    local ok, tabs = pcall(B.FetchPurchasedBankTabData, bank[1])
    for _, tab in ipairs(ok and type(tabs) == "table" and tabs or {}) do
      for slot = 1, type(tab.ID) == "number" and C.GetContainerNumSlots(tab.ID) or 0 do
        local item = C.GetContainerItemInfo(tab.ID, slot)
        if item and item.itemID then
          local key = item.itemID .. (bank[2] and "s" or "c")
          local line = seen[key]
          if not line then
            line = { id = item.itemID, name = item.itemName or knownName(item.itemID), count = 0, shared = bank[2] or nil }
            seen[key] = line
            out[#out + 1] = line
          end
          line.count = line.count + (item.stackCount or 1)
          if item.isBound then line.bound = true end
        end
      end
    end
  end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.bank = WispData.bank or {}
  WispData.bank[myKey()] = { seenAt = time(), items = out }
end

local function bankRecord(wanted)
  local mine = type(WispData) == "table" and type(WispData.bank) == "table" and WispData.bank[myKey()]
  if type(mine) ~= "table" or type(mine.items) ~= "table" then return nil end
  for _, line in ipairs(mine.items) do
    wanted[line.id] = true
    line.name = knownName(line.id) or line.name
  end
  return mine
end

---------------------------------------------------------- auction listings ----
-- How deep the market is: not only the lowest price of an item but how many are listed at each
-- price above it. Wisp sends the auction house no query of its own. It reads what is already in
-- the client after somebody else asked:
--   a full scan (Auctionator's): every auction, read a thousand at a time so the game never stutters;
--   a search for one item (the player's, or a shopping list's): that item's whole ladder, and
--   where the player's own auctions stand in it.
-- Held for the session only and handed over in the snapshot; the overlay keeps it by day.
--   listings[item] = { prices = { [price for one] = how many }, auctions, mine, mineAt, partial, at }

local listings, replicateRun = {}, nil

local function readFullScan()
  local A = C_AuctionHouse
  if not (A and A.GetNumReplicateItems and A.GetReplicateItemInfo) then return end
  local total = A.GetNumReplicateItems() or 0
  if total <= 0 then return end
  local run, fresh, i, now = {}, {}, 0, time()
  replicateRun = run
  local function batch()
    if replicateRun ~= run or (A.GetNumReplicateItems() or 0) ~= total then return end   -- another scan began, or the list is gone
    local stop = math.min(total, i + 1000)
    while i < stop do
      local _, _, count, _, _, _, _, _, _, buyout, _, _, _, _, _, _, id = A.GetReplicateItemInfo(i)   -- the list counts from 0
      if type(id) == "number" and type(count) == "number" and count > 0 and type(buyout) == "number" and buyout > 0 then
        local price = math.floor(buyout / count + 0.5)
        local entry = fresh[id]
        if not entry then
          entry = { prices = {}, auctions = 0, at = now }
          fresh[id] = entry
        end
        entry.prices[price] = (entry.prices[price] or 0) + count
        entry.auctions = entry.auctions + 1
      end
      i = i + 1
    end
    if i < total then
      C_Timer.After(0.05, batch)
    else
      for id, entry in pairs(listings) do   -- what a search said about the player's own auctions outlives a scan, which cannot see them
        if fresh[id] and entry.mine then fresh[id].mine, fresh[id].mineAt = entry.mine, entry.mineAt end
      end
      listings = fresh
    end
  end
  batch()
end

local function searchSeen(id, count, result, partial)
  if type(id) ~= "number" then return end
  local entry = { prices = {}, auctions = 0, at = time(), partial = partial or nil }
  for i = 1, count do
    local price, quantity, own = result(i)
    if type(price) == "number" and price > 0 and type(quantity) == "number" and quantity > 0 then
      entry.prices[price] = (entry.prices[price] or 0) + quantity
      entry.auctions = entry.auctions + 1
      if own and own > 0 then
        entry.mine = (entry.mine or 0) + own
        if not entry.mineAt or price < entry.mineAt then entry.mineAt = price end
      end
    end
  end
  if entry.auctions > 0 then listings[id] = entry end
end

local function commoditySearched(id)
  local A = C_AuctionHouse
  if not (A and A.GetNumCommoditySearchResults and A.GetCommoditySearchResultInfo) or type(id) ~= "number" then return end
  searchSeen(id, A.GetNumCommoditySearchResults(id) or 0, function(i)
    local r = A.GetCommoditySearchResultInfo(id, i)
    if not r then return nil end
    return r.unitPrice, r.quantity, r.containsOwnerItem and (r.numOwnerItems or r.quantity) or 0
  end, A.HasFullCommoditySearchResults and not A.HasFullCommoditySearchResults(id))
end

local function itemSearched(key)
  local A = C_AuctionHouse
  if not (A and A.GetNumItemSearchResults and A.GetItemSearchResultInfo) or type(key) ~= "table" then return end
  searchSeen(key.itemID, A.GetNumItemSearchResults(key) or 0, function(i)
    local r = A.GetItemSearchResultInfo(key, i)
    if not r or type(r.buyoutAmount) ~= "number" then return nil end
    local quantity = r.quantity or 1
    return math.floor(r.buyoutAmount / quantity + 0.5), quantity, r.containsOwnerItem and quantity or 0
  end, A.HasFullItemSearchResults and not A.HasFullItemSearchResults(key))
end

-- The ladder of one item, small: the lowest price, then how many are listed up to 1%, 2%, 3%, 5%,
-- 7.5%, 10%, 15%, 25%, 50% and 100% above it, and in all. Rows of
--   { item, when seen, auctions, own quantity, own lowest price, partial 0/1, price, listed up to it, price, listed, ... }
local BANDS = { 1, 1.01, 1.02, 1.03, 1.05, 1.075, 1.1, 1.15, 1.25, 1.5, 2, math.huge }
local function listingsRecord(wanted)
  local out = {}
  for id, entry in pairs(listings) do
    if #out >= 12000 then break end
    local prices = {}
    for price in pairs(entry.prices) do prices[#prices + 1] = price end
    table.sort(prices)
    local row = { id, entry.at, entry.auctions, entry.mine or 0, entry.mineAt or 0, entry.partial and 1 or 0 }
    local lowest, band, sum, last = prices[1], 1, 0, nil
    for _, price in ipairs(prices) do
      while price > lowest * BANDS[band] do   -- a dearer band begins: close the one before it, if anything was in it
        if last then row[#row + 1], row[#row + 2], last = last, sum, nil end
        band = band + 1
      end
      sum, last = sum + entry.prices[price], price
    end
    if last then row[#row + 1], row[#row + 2] = last, sum end
    out[#out + 1] = row
    wanted[id] = true
  end
  table.sort(out, function(a, b) return a[1] < b[1] end)
  return #out > 0 and out or nil
end

-------------------------------------------------------------- own auctions ----
-- What the character has up for sale, as the auction house lists it whenever its Auctions tab
-- is looked at. Wisp asks the auction house nothing itself: it only reads what is already shown.
--   WispData.auctions[character] = { seenAt, list = { { item, name, quantity, buyout, bid, sold, secondsLeft, bidder } } }

local function ownAuctionsSeen()
  local A = C_AuctionHouse
  local list = A and A.GetOwnedAuctions and A.GetOwnedAuctions()
  if type(list) ~= "table" then return end
  local out = {}
  for _, auction in ipairs(list) do
    local id = type(auction.itemKey) == "table" and auction.itemKey.itemID
    if type(id) == "number" and #out < 300 then
      out[#out + 1] = { item = id, name = knownName(id), quantity = auction.quantity, buyout = auction.buyoutAmount,
                        bid = auction.bidAmount, sold = auction.status == 1 or nil, secondsLeft = auction.timeLeftSeconds,
                        bidder = auction.bidder and true or nil }
    end
  end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.auctions = WispData.auctions or {}
  WispData.auctions[myKey()] = { seenAt = time(), list = out }
end

local function ownAuctions(wanted)
  local mine = type(WispData) == "table" and type(WispData.auctions) == "table" and WispData.auctions[myKey()]
  if type(mine) ~= "table" or type(mine.list) ~= "table" then return nil end
  for _, auction in ipairs(mine.list) do
    wanted[auction.item] = true
    auction.name = knownName(auction.item) or auction.name
  end
  return mine
end

------------------------------------------------------- auction house mail ----
-- What was really bought and sold: every auction ends in a mail with an invoice (who paid what,
-- the deposit, the house's cut). The addon notes each one when the mailbox is open, once, and
-- keeps it after the mail itself is gone. Mails have no number of their own, so one is told from
-- another by what it says and by when it runs out; several that say the same and arrived together
-- (five stacks bought in one go) are counted.
--   WispData.mail[character][what it says] = { { id, expires, n, kind, name, item, count, bid, buyout, deposit, cut }, ... }
-- The other player's name is part of telling mails apart and stays in this file: it is never exported.

local function scanMail()
  if not (GetInboxNumItems and GetInboxInvoiceInfo and GetInboxHeaderInfo) then return end
  if type(WispData) ~= "table" then WispData = {} end
  WispData.mail = WispData.mail or {}
  local mine = WispData.mail[myKey()] or {}
  WispData.mail[myKey()] = mine
  local now, present = time(), {}
  for i = 1, GetInboxNumItems() do
    local kind, name, other, bid, buyout, deposit, cut, _, _, _, count = GetInboxInvoiceInfo(i)
    if (kind == "buyer" or kind == "seller") and type(name) == "string" and type(bid) == "number" and bid > 0 then
      local _, _, _, _, _, _, daysLeft = GetInboxHeaderInfo(i)
      local item
      if kind == "buyer" and GetInboxItem then   -- what was bought is still attached, and says which item it is
        local _, id, _, attached = GetInboxItem(i, 1)
        item, count = id, count or attached
      end
      local mail = { kind = kind, name = name, item = item, count = tonumber(count) or 1, bid = bid, buyout = buyout or 0,
                     deposit = deposit or 0, cut = cut or 0, expires = now + (tonumber(daysLeft) or 0) * 86400 }
      local says = table.concat({ kind, name, tostring(other), bid, mail.buyout, mail.deposit, mail.cut, mail.count }, "|")
      present[says] = present[says] or {}
      table.insert(present[says], mail)
    end
  end
  for says, mails in pairs(present) do
    table.sort(mails, function(a, b) return a.expires < b.expires end)
    local groups = {}   -- mails that say the same and run out within a minute and a half of each other
    for _, mail in ipairs(mails) do
      local last = groups[#groups]
      if last and mail.expires - last.expires < 90 then
        last.n = last.n + 1
      else
        mail.n = 1
        groups[#groups + 1] = mail
      end
    end
    local known = mine[says] or {}
    mine[says] = known
    for _, group in ipairs(groups) do
      local seen
      for _, record in ipairs(known) do
        if math.abs(record.expires - group.expires) < 90 then seen = record end
      end
      if seen then   -- seen before: there may be fewer left by now, never more that were missed
        if group.n > seen.n then seen.n = group.n end
        seen.item = seen.item or group.item
      else
        WispData.mailSeq = (WispData.mailSeq or 0) + 1
        group.id = WispData.mailSeq
        known[#known + 1] = group
      end
    end
  end
end

local MAIL_LIFE = 30 * 86400   -- an auction mail runs out thirty days after it was sent

-- The notes for the snapshot, and the housekeeping: a note is dropped sixty days after its auction.
local function auctionMail()
  local mine = type(WispData) == "table" and type(WispData.mail) == "table" and WispData.mail[myKey()]
  local out, now = {}, time()
  for says, records in pairs(type(mine) == "table" and mine or {}) do
    for i = #records, 1, -1 do
      local r = records[i]
      if r.expires + MAIL_LIFE < now then
        table.remove(records, i)
      else
        out[#out + 1] = { id = r.id, kind = r.kind == "buyer" and "buy" or "sell", name = r.name, item = r.item, count = r.count,
                          n = r.n, bid = r.bid, deposit = r.deposit, cut = r.cut, sent = math.floor(r.expires - MAIL_LIFE) }
      end
    end
    if #records == 0 then mine[says] = nil end
  end
  table.sort(out, function(a, b) return a.id < b.id end)
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

-- What the account's other characters hold, as each was at its own last logout (bags) and last look
-- at its bank: so a shopping list can count what is already there. The name is for the card the player
-- reads; the overlay tells the model only class and level.
local function otherCharacters(wanted)
  local out = {}
  for key, me in pairs(type(WispData) == "table" and type(WispData.chars) == "table" and WispData.chars or {}) do
    if key ~= myKey() and type(me) == "table" and #out < 40 then
      local bank = type(WispData.bank) == "table" and WispData.bank[key] or nil
      for _, line in ipairs(type(me.bags) == "table" and me.bags or {}) do wanted[line.id] = true end
      for _, line in ipairs(type(bank) == "table" and type(bank.items) == "table" and bank.items or {}) do wanted[line.id] = true end
      out[#out + 1] = { name = me.name, class = me.class, level = me.level, bags = me.bags, bank = bank }
    end
  end
  return out
end

-- ---- what the character wears and its talents ------------------------------------------------
-- As the game's own character window shows them, for a sim site: Wisp turns this into the text such
-- a site imports, and the player pastes it there. The slots are in the order those sites expect.
local SIM_SLOTS = { 1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18 }

local function gearRecord()
  local out = {}
  for place, slot in ipairs(SIM_SLOTS) do
    local link = GetInventoryItemLink("player", slot)
    local body = type(link) == "string" and link:match("item:([%-%d:]+)")
    if body then
      local f = {}   -- item id, enchant, four gems, random suffix: the fields of the game's item link
      for part in (body .. ":"):gmatch("([^:]*):") do f[#f + 1] = tonumber(part) or 0 end
      if f[1] and f[1] > 0 then
        out[#out + 1] = { slot = place, id = f[1], enchant = (f[2] or 0) ~= 0 and f[2] or nil,
                          suffix = (f[7] or 0) ~= 0 and f[7] or nil, name = link:match("%[(.-)%]") }
        pcall(noteRarity, f[1])
      end
    end
  end
  return out
end

-- The talents taken, each as { the game's number for the talent, points in it, its name }. This game
-- keeps talents in the newer trait system, so they are read the way the WoWSims exporter addon reads them.
local function talentRecord()
  if not C_Traits then return nil end
  local config = C_ClassTalents and C_ClassTalents.GetActiveConfigID and C_ClassTalents.GetActiveConfigID()
  local S = C_SpecializationInfo
  if not config and S and S.GetCombatConfigIDForSpecGroup then
    config = S.GetCombatConfigIDForSpecGroup(S.GetActiveSpecGroup and S.GetActiveSpecGroup() or 1)
  end
  local info = config and C_Traits.GetConfigInfo(config)
  if not info or not info.treeIDs then return nil end
  local out = {}
  for _, tree in ipairs(info.treeIDs) do
    for _, nodeID in ipairs(C_Traits.GetTreeNodes(tree) or {}) do
      local node = C_Traits.GetNodeInfo(config, nodeID)
      local rank = node and (node.currentRank or node.ranksPurchased) or 0
      if type(rank) == "number" and rank > 0 and node.activeEntry then
        local entry = C_Traits.GetEntryInfo(config, node.activeEntry.entryID)
        local id = entry and entry.definitionID
        if type(id) == "number" then
          local okN, name = pcall(function()
            local spell = C_Traits.GetDefinitionInfo(id).spellID
            return (C_Spell and C_Spell.GetSpellName and C_Spell.GetSpellName(spell)) or (GetSpellInfo(spell))
          end)
          out[#out + 1] = { id, rank, okN and type(name) == "string" and name or nil }
        end
      end
    end
  end
  table.sort(out, function(a, b) return a[1] < b[1] end)
  return out
end

-- What the Pawn addon says of the items Wisp may be asked about: the quest log's rewards, what is carried,
-- the dungeon journal's loot. Pawn knows the item stats and the player's scales; Wisp asks it, as its own
-- tooltip does, and keeps the answer: for each scale, how much of an upgrade an item is and what it replaces.
local PAWN_ITEMS = 150   -- at most this many asked, at logout

local function pawnVerdicts(log, carried, journal)
  if not (PawnIsInitialized and type(PawnGetItemData) == "function" and type(PawnIsItemAnUpgrade) == "function") then return nil end
  local scales, order = {}, {}
  if type(PawnGetAllScalesEx) == "function" then
    for _, s in ipairs(PawnGetAllScalesEx() or {}) do
      if s.IsVisible and #scales < 4 then scales[#scales + 1] = s.Name; order[s.Name] = #scales end
    end
  end
  local wanted, seen = {}, {}
  local function want(id) if type(id) == "number" and not seen[id] and #wanted < PAWN_ITEMS then seen[id] = true; wanted[#wanted + 1] = id end end
  for _, quest in ipairs(log or {}) do
    for _, list in ipairs({ quest.rewardItems or {}, quest.rewardChoices or {} }) do
      for _, reward in ipairs(list) do want(reward.id) end
    end
  end
  for _, line in ipairs(carried or {}) do want(line.id) end
  for _, dungeon in ipairs(journal or {}) do
    for _, boss in ipairs(dungeon.bosses or {}) do
      for _, drop in ipairs(boss.loot or {}) do want(drop[1]) end
    end
  end
  local items, unknown = {}, 0
  for _, id in ipairs(wanted) do
    local okI, item = pcall(PawnGetItemData, "item:" .. id)
    if okI and type(item) == "table" and item.Link then
      local entry = { id = id }
      local okU, upgrades = pcall(PawnIsItemAnUpgrade, item)
      if okU and type(upgrades) == "table" then
        for _, u in ipairs(upgrades) do
          if type(u) == "table" and order[u[1]] and type(u[3]) == "number" then
            entry.up = entry.up or {}
            entry.up[#entry.up + 1] = { s = order[u[1]], p = math.floor(u[3] * 1000 + 0.5) / 1000,
                                        r = type(u[4]) == "string" and u[4]:match("%[(.-)%]") or nil }
          end
        end
      end
      if type(PawnGetSingleValueFromItem) == "function" then
        for i, name in ipairs(scales) do
          local okV, value = pcall(PawnGetSingleValueFromItem, item, name)
          if okV and type(value) == "number" then entry.v = entry.v or {}; entry.v[i] = math.floor(value * 10 + 0.5) / 10 end
        end
      end
      if entry.up or entry.v then items[#items + 1] = entry end
    elseif okI and type(item) == "table" then
      unknown = unknown + 1   -- the game has not described it yet: Pawn has no stats for it
    end
  end
  local names = {}
  for i, name in ipairs(scales) do
    local okL, shown = pcall(PawnGetScaleLocalizedName, name)
    names[i] = okL and type(shown) == "string" and shown or name
  end
  return { scales = names, items = items, unknown = unknown > 0 and unknown or nil }
end

local inboxWorks = function() return false end   -- set by the one-click inbox, further down

local function buildSnapshot()
  local version, build, _, interface = GetBuildInfo()
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
  for _, line in ipairs(carried) do wanted[line.id] = true; pcall(noteRarity, line.id) end   -- what is carried is always described
  local okC, books = pcall(crafting, wanted)
  if not okC then books = {} end
  local log = questLog(DB, ZoneDB)
  local okQ, open = pcall(availableQuests, DB, ZoneDB)
  if not okQ then open = nil end
  for _, quest in ipairs(log) do
    for _, need in ipairs(quest.needs or {}) do wanted[need.id] = true end
    for _, list in ipairs({ quest.rewardItems or {}, quest.rewardChoices or {} }) do   -- what a reward is worth helps to choose one
      for _, reward in ipairs(list) do if reward.id then wanted[reward.id] = true end end
    end
  end
  for _, dungeon in ipairs(journal or {}) do
    for _, quest in ipairs(dungeon.quests) do
      for _, need in ipairs(quest.items or {}) do wanted[need.id] = true end
    end
  end
  local okD, ladders = pcall(listingsRecord, wanted)
  if not okD then ladders = nil end
  local okK, banked = pcall(bankRecord, wanted)
  local okE, others = pcall(otherCharacters, wanted)   -- before the prices are chosen: their things are worth pricing too
  if not okE then others = nil end
  if not okK then banked = nil end
  local okO, listed = pcall(ownAuctions, wanted)
  if not okO then listed = nil end
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
  local okT, deals = pcall(auctionMail)
  if not okT then deals = {} end
  local okX, progress = pcall(xpRecord)
  if not okX then progress = nil end
  local okD, deaths = pcall(deathRecord)
  if not okD then deaths = nil end
  local okN, named = pcall(nameProbe, DB)
  local okS, needs = pcall(itemSkills)
  local okQ, rare = pcall(rarities)
  local okT, taught = pcall(trainerWindows)
  local okW, worn = pcall(gearRecord)
  local okU, spent = pcall(talentRecord)
  local okPw, rated = pcall(pawnVerdicts, log, carried, journal)
  return {
    v = 1,
    takenAt = time(),
    client = { version = version, build = build, interface = interface, locale = GetLocale() },
    -- who this is: only so that Wisp can keep each character's history apart on this PC.
    -- It stays out of what the model is given.
    who = { name = UnitName("player"), realm = GetRealmName() },
    professions = skills,
    character = {
      xp = UnitXP and UnitXP("player") or nil, xpMax = UnitXPMax and UnitXPMax("player") or nil, money = GetMoney(),
      class = classFile, race = (UnitRace("player")), raceFile = select(2, UnitRace("player")), level = UnitLevel("player"),
      -- how many levels below the character a quest stays green before it turns grey: the game's own number
      greenRange = (GetQuestGreenRange and GetQuestGreenRange())
                   or (UnitQuestTrivialLevelRange and UnitQuestTrivialLevelRange("player")) or nil,
      faction = (UnitFactionGroup("player")), gold = math.floor(GetMoney() / 10000),
      map = map, zone = mapName(map), subzone = GetSubZoneText(),
      x = x and round1(x * 100), y = y and round1(y * 100),
    },
    addons = { inbox = inboxWorks(), questie = DB ~= nil, tomtom = type(TomTom) == "table" and type(TomTom.AddWaypoint) == "function",
               dungeonjournal = journal ~= nil, auctionator = auctionator() ~= nil, gathermate = nodes ~= nil,
               pawn = type(PawnImportScale) == "function" },
    dungeons = journal,
    bags = carried,
    bank = banked,
    alts = others,
    indexFailed = type(WispData) == "table" and type(WispData.indexFailed) == "table" and next(WispData.indexFailed) and WispData.indexFailed or nil,
    recipes = books,
    prices = market,
    market = record,
    gather = nodes,
    loot = looted,
    auctionMail = deals,
    ownAuctions = listed,
    listings = ladders,
    xp = progress,
    deaths = deaths,
    combatLog = COMBAT_LOG_OPEN,   -- whether this game lets an addon read the combat log at all
    addonBuild = ADDON_BUILD,
    itemSkill = okS and needs or nil,
    nameProbe = okN and named or nil,
    quality = okQ and rare or nil,
    trainerWindows = okT and taught or nil,
    gear = okW and worn or nil,
    talents = okU and spent or nil,
    pawn = okPw and rated or nil,
    talentsRead = okU and spent ~= nil,   -- an empty list alone would not say whether the game answered
    vendor = vendor,
    quests = log,
    available = open,
    sources = sources,
    completed = C_QuestLog.GetAllCompletedQuestIDs(),
  }
end

-- Returns the snapshot, or nil and the reason. On failure the previous snapshot is kept.
local function save()
  if not (E and E.SerializeJSON and E.EncodeBase64) then return nil, L("this client has no JSON encoder") end
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
  local map, x, y = a.map, a.x, a.y   -- numbers as numbers: no "0x7fffffff", and only a map the game has
  if not (type(map) == "number" and type(x) == "number" and type(y) == "number" and map > 0 and map == math.floor(map)
          and x >= 0 and x <= 100 and y >= 0 and y <= 100 and C_Map.GetMapInfo(map)) then
    return false, L("bad waypoint")
  end
  local title = "Wisp"
  if type(a.title) == "string" and a.title ~= "" then
    title = clip((a.title:gsub("[|%c]", "")), 80)   -- no colour, texture or link codes from outside, and no second line
  end
  if type(TomTom) == "table" and type(TomTom.AddWaypoint) == "function" then
    TomTom:AddWaypoint(map, x / 100, y / 100, { title = title, from = "Wisp" })
  elseif C_Map.CanSetUserWaypointOnMap and C_Map.CanSetUserWaypointOnMap(map) then
    C_Map.SetUserWaypoint(UiMapPoint.CreateFromCoordinates(map, x / 100, y / 100))
    C_SuperTrack.SetSuperTrackedUserWaypoint(true)
    title = title .. " (map pin: TomTom is not installed)"
  else
    return false, L("cannot set a waypoint on that map")
  end
  return true, L("waypoint: ") .. title
end

-- { name = list name, items = { { id = item id, name = text, quantity = n }, ... } }
-- Creates (or replaces) one of Auctionator's shopping lists through its public API.
commands["auctionator.shopping_list"] = function(a)
  local api = auctionator()
  if not (api and api.CreateShoppingList and api.ConvertToSearchString) then return false, L("Auctionator is not installed") end
  if type(a.items) ~= "table" or #a.items == 0 or #a.items > 60 then return false, L("bad shopping list") end
  local clean = function(text, limit) return (text:gsub("[|;%c]", "")):sub(1, limit) end
  local listName = "Wisp"
  if type(a.name) == "string" and clean(a.name, 40) ~= "" then listName = clean(a.name, 40) end
  local searches = {}
  for _, item in ipairs(a.items) do
    local quantity = type(item) == "table" and tonumber(item.quantity)
    if not (quantity and quantity >= 1 and quantity <= 100000) then return false, L("bad shopping list") end
    -- the game's own name for the item where it has one: it is in the player's language
    local name = tonumber(item.id) and itemName(tonumber(item.id))
    if not name and type(item.name) == "string" then name = clean(item.name, 80) end
    if not name or name == "" then return false, L("bad shopping list") end
    local ok, search = pcall(api.ConvertToSearchString, "Wisp",
                             { searchString = name, isExact = true, quantity = math.floor(quantity) })
    if not ok then return false, L("Auctionator refused ") .. name end
    searches[#searches + 1] = search
  end
  local ok, why = pcall(api.CreateShoppingList, "Wisp", listName, searches)
  if not ok then return false, "Auctionator: " .. tostring(why) end
  return true, L("shopping list \"%s\" with %d items is in Auctionator's Shopping tab"):format(listName, #searches)
end

-- { tag = a Pawn scale: '( Pawn: v1: "name": Class=Mage, Intellect=1.2 )' }
-- Handed to Pawn's own import, as if pasted into its Import window: the stat weights a sim worked out.
commands["pawn.scale"] = function(a)
  if type(PawnImportScale) ~= "function" then return false, L("Pawn is not installed") end
  local tag = a.tag   -- only the shape Wisp writes: a name, then stats with their numbers; nothing the game could read as a code
  if type(tag) ~= "string" or #tag > 2000 or not tag:find('^%( Pawn: v1: "[^"|%c]+": [%w=%.,%- ]+ %)$') then
    return false, L("bad Pawn scale")
  end
  local ok, status, name = pcall(PawnImportScale, tag, true)   -- true: a scale of the same name is replaced, as Pawn's own import does
  if not ok or status ~= (PawnImportScaleResultSuccess or 1) then return false, L("Pawn did not take the scale") end
  if type(PawnUIFrame_ScaleSelector_Refresh) == "function" then pcall(PawnUIFrame_ScaleSelector_Refresh) end   -- its window, when open
  return true, L("Pawn has the scale \"%s\": item tooltips now show its value"):format(tostring(name))
end

local function receive(text)
  if text:sub(1, 6) ~= "WISP1:" then return say(L("that is not a Wisp message.")) end
  if #text > 90000 then return say(L("that message is too long.")) end   -- Wisp's own are at most 60,000: a larger one is not decoded at all
  local ok, message = pcall(function() return E.DeserializeJSON(E.DecodeBase64(text:sub(7))) end)
  if not ok or type(message) ~= "table" or type(message.cmds) ~= "table" then
    return say(L("could not read that message."))
  end
  for i, cmd in ipairs(message.cmds) do
    if i > 50 then say(L("stopped after 50 commands.")); break end
    local run = type(cmd) == "table" and commands[cmd["do"]]
    if not run then
      -- its name is text from outside like any other: no colour, link or line codes, and not much of it
      local named = clip((tostring(type(cmd) == "table" and cmd["do"]):gsub("[|%c]", "")), 40)
      say("|cffff6060" .. L("skipped") .. "|r " .. L("unknown command") .. " " .. named)
    else
      local okRun, good, note = pcall(run, cmd)
      if okRun and good then
        say(note)
      else
        say("|cffff6060" .. L("failed") .. "|r " .. tostring(okRun and note or good))
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
hint:SetText(L("Wisp: paste now (Ctrl+V). Esc closes."))

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

------------------------------------------------------- one-click inbox ----
-- Cards without a paste (Denis, 2026-11-11). The overlay cannot talk to the running game, but the
-- game can look at files that were there when it started:
--   Wisp_In001 .. Wisp_In100   load-on-demand addons holding one data file each. Loading one reads
--                              its file from disk at that moment; each loads once per UI session.
--   Wisp_Inbox\NNN.wav          signals: an empty sound file will not play, a real (silent) one
--                              will, so "is there something" costs nothing and loads nothing.
--   Wisp_Inbox\Cursor.lua       read at every UI load: which signal comes next, and the newest message.
-- The overlay writes the same recent messages into every slot and then raises the next signal; the
-- addon sees the signal, loads the next unused slot, and hands each message it has not run yet to
-- receive(): the same strict reader a paste goes through, and the same whitelist. What is loaded
-- is a data file the overlay wrote: a table of numbers and WISP1 texts, nothing that runs.
local INBOX_SLOTS, INBOX_SIGNALS, INBOX_KEEPS = 100, 300, 900
local INBOX = "Interface\\AddOns\\Wisp_Inbox\\"
local inbox = { slot = 0, works = false, on = false, wasted = 0 }

local function playable(file)
  local ok, will = pcall(PlaySoundFile, INBOX .. file, "Master")
  return ok and will and true or false
end

-- Load the next slot and run what is new in it. True when something was.
local function inboxLoad()
  if inbox.slot >= INBOX_SLOTS then
    inbox.on = false
    say(L("one-click cards are used up for this session: /wisp sync brings them back. Copy and Ctrl+V still work."))
    return false
  end
  inbox.slot = inbox.slot + 1
  WispDelivery = nil
  pcall((C_AddOns and C_AddOns.LoadAddOn) or LoadAddOn, ("Wisp_In%03d"):format(inbox.slot))
  local got = WispDelivery
  if type(got) ~= "table" then return false end
  if type(WispData) ~= "table" then WispData = {} end
  local fresh = false
  for _, m in ipairs(type(got.msgs) == "table" and got.msgs or {}) do
    if type(m) == "table" and type(m.n) == "number" and type(m.text) == "string" and m.n > (WispData.delivered or 0) then
      WispData.delivered = m.n   -- n is when it was sent, in milliseconds: an old one is not run long after
      if time() - m.n / 1000 < INBOX_KEEPS then
        fresh = true
        receive(m.text)
      end
    end
  end
  if type(got.c) == "number" and got.c >= 1 and got.c <= INBOX_SIGNALS then inbox.signal = got.c end
  return fresh
end

local function inboxTick()
  if not inbox.on or not playable(("%03d.wav"):format(inbox.signal)) then return end
  local was = inbox.signal
  if inboxLoad() then
    inbox.wasted = 0
  else
    inbox.wasted = inbox.wasted + 1
    if inbox.wasted >= 3 then inbox.on = false end   -- signals that mean nothing: stop spending slots on them
  end
  if inbox.signal == was then inbox.signal = was % INBOX_SIGNALS + 1 end
end

inboxWorks = function() return inbox.works end

local function startInbox()
  -- the files must have been there when the game started, and its sound must tell an empty file from a real one
  inbox.works = type(WispCursor) == "number" and not playable("empty.wav") and playable("valid.wav")
  inbox.on, inbox.signal = inbox.works, WispCursor
  if not inbox.on then return end
  if type(WispLatest) == "number" and WispLatest > (type(WispData) == "table" and WispData.delivered or 0)
     and time() - WispLatest / 1000 < INBOX_KEEPS then
    inboxLoad()   -- sent while the game was closed or reloading
  end
  local waited = 0
  CreateFrame("Frame"):SetScript("OnUpdate", function(_, elapsed)
    waited = waited + (elapsed or 0)
    if waited >= 1 then waited = 0; pcall(inboxTick) end
  end)
end

---------------------------------------------------------------- boot ----

local frame = CreateFrame("Frame")
frame:RegisterEvent("TRAINER_SHOW")
frame:RegisterEvent("TRAINER_UPDATE")
if COMBAT_LOG_OPEN then   -- never on a game that forbids it: see "how the character died"
  frame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  frame:RegisterEvent("PLAYER_DEAD")
end
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:RegisterEvent("TRADE_SKILL_SHOW")
frame:RegisterEvent("TRADE_SKILL_LIST_UPDATE")
frame:RegisterEvent("AUCTION_HOUSE_CLOSED")
frame:RegisterEvent("PLAYER_XP_UPDATE")
frame:RegisterEvent("QUEST_TURNED_IN")
frame:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
frame:RegisterEvent("COMMODITY_SEARCH_RESULTS_UPDATED")
frame:RegisterEvent("ITEM_SEARCH_RESULTS_UPDATED")
frame:RegisterEvent("BANKFRAME_OPENED")
frame:RegisterEvent("BANKFRAME_CLOSED")
frame:RegisterEvent("PLAYERBANKSLOTS_CHANGED")
frame:RegisterEvent("PLAYER_ACCOUNT_BANK_TAB_SLOTS_CHANGED")
frame:RegisterEvent("OWNED_AUCTIONS_UPDATED")
frame:RegisterEvent("MAIL_SHOW")
frame:RegisterEvent("MAIL_INBOX_UPDATE")
frame:RegisterEvent("LOOT_OPENED")
frame:RegisterEvent("LOOT_CLOSED")
frame:RegisterEvent("UNIT_SPELLCAST_SENT")
frame:SetScript("OnEvent", function(_, event, ...)
  if event == "PLAYER_LOGOUT" then
    if type(WispData) ~= "table" then WispData = {} end
    WispData.chars = WispData.chars or {}   -- what an alt's recipes are labelled with: class and level, no name
    local _, classFile = UnitClass("player")
    local okB, carried = pcall(bags)   -- what this character holds, for when another one is asked what to buy
    WispData.chars[myKey()] = { class = classFile, level = UnitLevel("player"), name = UnitName("player"), bags = okB and carried or nil }
    local snapshot = save()
    if snapshot and snapshot.market then   -- these days are now handed over
      if type(WispData) ~= "table" then WispData = {} end
      WispData.marketSent = WispData.marketSent or {}
      WispData.marketSent[myMarket()] = { to = snapshot.market.today, from = snapshot.market.oldest }
    end
  elseif event == "AUCTION_HOUSE_CLOSED" then
    pcall(learnNames)   -- a scan has just shown the game these items
  elseif event == "TRAINER_SHOW" or event == "TRAINER_UPDATE" then
    pcall(trainerSeen)
  elseif event == "COMBAT_LOG_EVENT_UNFILTERED" then
    if COMBAT_LOG_OPEN then pcall(combatLogged) end
  elseif event == "PLAYER_DEAD" then
    if COMBAT_LOG_OPEN then pcall(died) end
  elseif event == "PLAYER_XP_UPDATE" then
    pcall(xpChanged)
  elseif event == "QUEST_TURNED_IN" then
    pcall(questHandedIn, ...)
  elseif event == "REPLICATE_ITEM_LIST_UPDATE" then
    pcall(readFullScan)
  elseif event == "COMMODITY_SEARCH_RESULTS_UPDATED" then
    pcall(commoditySearched, ...)
  elseif event == "ITEM_SEARCH_RESULTS_UPDATED" then
    pcall(itemSearched, ...)
  elseif event == "BANKFRAME_OPENED" then
    bankOpen = true
    pcall(bankSeen)
    C_Timer.After(1, function() pcall(bankSeen) end)   -- once more when the tabs have had a moment to fill
  elseif event == "BANKFRAME_CLOSED" then
    bankOpen = false
  elseif event == "PLAYERBANKSLOTS_CHANGED" or event == "PLAYER_ACCOUNT_BANK_TAB_SLOTS_CHANGED" then
    pcall(bankSeen)
  elseif event == "OWNED_AUCTIONS_UPDATED" then
    pcall(ownAuctionsSeen)
  elseif event == "MAIL_SHOW" or event == "MAIL_INBOX_UPDATE" then
    pcall(scanMail)
  elseif event == "LOOT_OPENED" then
    pcall(lootOpened)
  elseif event == "LOOT_CLOSED" then
    pcall(lootClosed)
  elseif event == "UNIT_SPELLCAST_SENT" then
    local unit, target = ...
    if unit == "player" then
      -- The name of what is being gathered, never of a person: after a heal on someone, a fishing bobber looted
      -- would otherwise be filed under their name. Any cast that names no node forgets the last one.
      local person = type(target) ~= "string" or target == "" or target == UnitName("player") or UnitIsPlayer(target)
        or (UnitName("target") == target and UnitIsPlayer("target"))
      lastCast = not person and { name = target, at = time() } or nil
    end
  elseif event ~= "PLAYER_LOGIN" then
    pcall(captureRecipes)
  else
    C_Timer.After(20, function() pcall(learnNames) end)   -- once the login rush is over
    C_Timer.After(60, function() pcall(learnItemSkills) end)
    C_Timer.After(75, function() pcall(learnRarity) end)
    C_Timer.After(30, function() pcall(startQuestIndex); pcall(startRecipeIndex) end)
    pcall(startInbox)
    pcall(pruneLoot)
    pcall(xpChanged)   -- where the bar stands as the session begins
    pcall(SetOverrideBindingClick, opener, false, PASTE_KEY, "WispInboxOpener")
  end
end)

SLASH_WISP1 = "/wisp"
SlashCmdList.WISP = function(msg)
  if msg:match("sync") then   -- a fresh snapshot for Wisp without logging out: a reload saves it
    return (C_UI and C_UI.Reload or ReloadUI)()
  elseif msg:match("save") then
    local snapshot, why = save()
    if snapshot then
      say(L("snapshot built: %d quests, Questie %s, DungeonJournal %s. It is written to disk when you log out.")
        :format(#snapshot.quests, L(snapshot.addons.questie and "found" or "not found"),
                snapshot.dungeons and L("%d dungeons"):format(#snapshot.dungeons) or L("not found")))
    else
      say("|cffff6060" .. L("could not build the snapshot:") .. "|r " .. tostring(why))
    end
  else
    openBox()
  end
end
