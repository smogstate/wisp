"""The questions Wisp's assistant is tested on, each with what a right answer is.

Every expected fact comes from the test game in tests/wow_stub.lua (flags
questindex,alts,rewards): a level 12 human mage in Elwynn Forest, a tailor,
with one other character (a level 30 warrior who cooks).

A case:
  ask         one question, or a list of them asked in one conversation
  tools       each entry "a|b" needs one successful call to a or b; "any" = any tool
  no_tools    True: answering needs no lookup, and none may be made
  args        {tool: {argument: value}}: one successful call must carry these
  say         patterns found in the assistant's text or on its cards
  text        patterns found in the assistant's own text
  never       patterns that must not be in the assistant's own text
  card        patterns found on the cards (a card's game command is read decoded)
  card_never  patterns that must not be on the cards
  lang        the language the answer must be in: that of the question
  oracle      per question: (tool calls, text) of a right answer. `run.py --check`
              plays it through the real tools and requires every check to pass,
              so a case cannot ask for something the test game does not hold.
Patterns are regular expressions, matched without regard to letter case.
"""


def money(*parts):
    """money("4s", "50c") matches "4s 50c", "4с 50м" and "4 серебра 50 меди"."""
    units = {"g": "gз", "s": "sс", "c": "cм"}
    return r"(?<!\d)" + r"\W{0,3}".join(rf"{p[:-1]}\s?[{units[p[-1]]}]\w*" for p in parts)


EGG = [("recipes", {"search": "Herb Baked Egg"})]
PATTERN = [("recipes", {"search": "Not Learned Yet"})]
MEMORY_OF_A_14_SLOT_BAG = r"Runecloth|Mageweave|Rugged Leather|Rune Thread|рунич|магическ\w+ ткан"

CASES = [
    # --- recipes: where one comes from -------------------------------------------------
    {"id": "recipe-eggs-buy", "tags": ["recipes", "en"], "lang": "en",
     "ask": "Where can I buy the recipe to cook eggs?",
     "tools": ["recipes"], "say": ["Herb Baked Egg", "Roaming Mob|Prowler|VanCleef"],
     "never": ["Alexandra Bolero"],   # the only seller in the data sells something else
     "oracle": [([("recipes", {"search": "egg"})],
                 "No one sells it. Recipe: Herb Baked Egg drops from Roaming Mob in Westfall, "
                 "Prowler in Elwynn Forest and Edwin VanCleef inside The Deadmines.")]},
    {"id": "recipe-eggs-drop", "tags": ["recipes"], "lang": "ru",
     "ask": "С кого падает Recipe: Herb Baked Egg?",
     "tools": ["recipes|item_links"], "say": ["Roaming Mob", "Prowler", "VanCleef|ВанКлиф"],
     "oracle": [(EGG, "Рецепт падает с Roaming Mob (Westfall), Prowler (Elwynn Forest) "
                      "и с Edwin VanCleef внутри The Deadmines.")]},
    {"id": "recipe-pattern-seller", "tags": ["recipes"], "lang": "ru",
     "ask": "Где купить выкройку Not Learned Yet?",
     "tools": ["recipes|item_links"], "say": ["Alexandra Bolero", "Stormwind|Штормград"],
     "oracle": [(PATTERN, "Выкройку продаёт Alexandra Bolero в Stormwind City.")]},
    {"id": "recipe-pattern-waypoint", "tags": ["recipes", "waypoint"], "lang": "ru",
     "ask": "Поставь метку на продавца выкройки Not Learned Yet",
     "tools": ["recipes|item_links", "waypoint"], "card": ["Alexandra Bolero", r"43\.2"],
     "oracle": [(PATTERN + [("waypoint", {"refs": ["w7090.vendor.1.1"]})],
                 "Метка на продавца готова, нажмите на карточке кнопку отправки.")]},
    {"id": "recipe-eggs-mob-waypoint", "tags": ["recipes", "waypoint"], "lang": "ru",
     "ask": "Поставь метки на мобов, с которых падает Recipe: Herb Baked Egg",
     "tools": ["recipes|item_links", "waypoint"], "card": ["Roaming Mob|Prowler", "tomtom"],
     "oracle": [(EGG + [("waypoint", {"refs": ["w6325.drop.1.1", "w6325.drop.2.1"]})],
                 "Метки на двух мобов готовы. Третий, Edwin VanCleef, внутри подземелья, там метки нет.")]},
    {"id": "recipe-eggs-who", "tags": ["recipes", "alts"], "lang": "ru",
     "ask": "Какой профессией делают Herb Baked Egg и умеет ли это кто-то из моих персонажей?",
     "tools": ["recipes|craft_tree|item_uses"], "say": ["Cooking|Кулинар", "Warrior|воин", "30"],
     "oracle": [(EGG, "Это Cooking (кулинария). Рецепт есть у вашего другого персонажа: воин 30 уровня.")]},
    {"id": "recipe-unknown-bag", "tags": ["recipes", "not-in-data"], "lang": "ru",
     "ask": "Что нужно для сумки на 14 ячеек и где купить рецепт?",
     "tools": ["any"], "never": [MEMORY_OF_A_14_SLOT_BAG],
     "oracle": [([("recipes", {"search": "bag"})], "В данных игры такого рецепта я не нашёл.")]},

    # --- crafting: the whole tree -----------------------------------------------------
    {"id": "craft-hood-materials", "tags": ["crafting"], "lang": "ru",
     "ask": "Что нужно, чтобы сделать 3 Azure Silk Hood, до базовых материалов?",
     "tools": ["craft_tree"], "args": {"craft_tree": {"quantity": 3}},
     "say": ["Silk Cloth", "Blue Dye", "Fine Thread", r"\b20\b"],
     "oracle": [([("craft_tree", {"item": "Azure Silk Hood", "quantity": 3})],
                 "Нужно 20 Silk Cloth, 6 Blue Dye и 3 Fine Thread; докупить придётся только 1 Blue Dye.")]},
    {"id": "craft-hood-shopping", "tags": ["crafting", "alts"], "lang": "ru",
     "ask": "Собери список покупок на 3 Azure Silk Hood с учётом сумок и банков всех моих персонажей",
     "tools": ["craft_tree|shopping_list"], "args": {"craft_tree": {"quantity": 3}},
     "card": [r'auctionator[^}]*Blue Dye|Blue Dye[^}]*auctionator'],
     "card_never": [r'auctionator[^}]*Silk Cloth'],   # held already, so not on the list to buy
     "never": ["Кук"],   # the other character's name is never the model's to say
     "oracle": [([("craft_tree", {"item": "Azure Silk Hood", "quantity": 3, "show": True})],
                 "Докупить нужно только 1 Blue Dye: остальное лежит в сумках и банках ваших персонажей.")]},
    {"id": "craft-silk-uses", "tags": ["crafting"], "lang": "ru",
     "ask": "Куда идёт Silk Cloth? Во что его можно превратить?",
     "tools": ["item_uses|item_links"], "say": ["Bolt of Silk Cloth", "Azure Silk Hood"],
     "oracle": [([("item_uses", {"item": "Silk Cloth"})],
                 "Из Silk Cloth делают Bolt of Silk Cloth, а из него Azure Silk Hood.")]},
    {"id": "craft-bolt-profit", "tags": ["crafting", "gold"], "lang": "ru",
     "ask": "Сколько я заработаю на одном Bolt of Silk Cloth, если сделаю и продам?",
     "tools": ["gold|recipes|craft_plan|craft_tree|item_uses"], "say": [money("3s"), money("18s") + "|" + money("21s")],
     "oracle": [([("gold", {"view": "crafts"})],
                 "Материалы 18s, продаётся за 21s: прибыль 3s с одного.")]},
    {"id": "craft-what-pays", "tags": ["crafting", "gold"], "lang": "ru",
     "ask": "Что из моих рецептов выгодно делать на продажу?",
     "tools": ["gold"], "args": {"gold": {"view": "crafts"}}, "say": ["Bolt of Silk Cloth", money("3s") + "|17"],
     "oracle": [([("gold", {"view": "crafts", "show": True})],
                 "Сейчас выгоден Bolt of Silk Cloth: +17% к материалам.")]},

    # --- quests ------------------------------------------------------------------------
    {"id": "quest-hand-in", "tags": ["quests"], "lang": "ru",
     "ask": "Какие задания я могу сдать прямо сейчас и кому?",
     "tools": ["quests|questing_route"], "say": ["Wolves Across the Border", "Eagan Peltskinner"],
     "never": [r"Jasperlode Mine\W{0,20}(можно|готов\w*) сда"],
     "oracle": [([("quests", {})], "Готово к сдаче: Wolves Across the Border, сдавать Eagan Peltskinner.")]},
    {"id": "quest-xp", "tags": ["quests", "rewards"], "lang": "ru",
     "ask": "Сколько опыта дадут за Wolves Across the Border?",
     "tools": ["quests|quest_search|questing_route"], "text": [r"\b460\b"],
     "oracle": [([("quests", {})], "За него дадут 460 опыта.")]},
    {"id": "quest-rewards", "tags": ["quests", "rewards"], "lang": "ru",
     "ask": "Что дадут за The Jasperlode Mine?",
     "tools": ["quests|quest_search"], "say": [r"\b700\b", "Miner's Cape", "Kobold Mining Shovel", money("3s", "50c")],
     "oracle": [([("quests", {})],
                 "700 опыта, 3s 50c, Miner's Cape и на выбор: 5 × Silk Cloth или Kobold Mining Shovel.")]},
    {"id": "quest-elite", "tags": ["quests"], "lang": "ru",
     "ask": "Какие элитные задания мне сейчас доступны?",
     "tools": ["available_quests"], "say": ["Hogger", "Protecting the Herd"],
     "never": ["Fishy Peril", "Unwelcome Guest"],   # open, but not elite
     "oracle": [([("available_quests", {"kind": "elite"})],
                 "Два: Wanted: Hogger (Elwynn Forest) и Protecting the Herd (Dun Morogh).")]},
    {"id": "quest-elite-en", "tags": ["quests", "en"], "lang": "en",
     "ask": "What elite quests can I pick up right now?",
     "tools": ["available_quests"], "say": ["Hogger", "Protecting the Herd"], "never": ["Fishy Peril"],
     "oracle": [([("available_quests", {"kind": "elite"})],
                 "Two: Wanted: Hogger in Elwynn Forest and Protecting the Herd in Dun Morogh.")]},
    {"id": "quest-nearby", "tags": ["quests"], "lang": "ru",
     "ask": "Какие задания можно взять рядом со мной?",
     "tools": ["available_quests|questing_route"], "say": ["A Fishy Peril", "Marshal Dughan"],
     "oracle": [([("available_quests", {"zone": "here"})],
                 "Рядом: A Fishy Peril у Marshal Dughan, An Unwelcome Guest и Wanted: Hogger у доски объявлений.")]},
    {"id": "quest-by-name", "tags": ["quests", "any-quest"], "lang": "ru",
     "ask": "Найди задание The Defias Brotherhood: кто его даёт и где?",
     "tools": ["quest_search"], "say": ["Gryan Stoutmantle", "Westfall|Западн"],
     "oracle": [([("quest_search", {"name": "The Defias Brotherhood"})],
                 "Его даёт Gryan Stoutmantle в Westfall.")]},
    {"id": "quest-why-closed", "tags": ["quests", "any-quest"], "lang": "ru",
     "ask": "Почему мне пока недоступна часть The Defias Brotherhood 22-го уровня?",
     "tools": ["quest_search"], "text": [r"\b14\b"],
     "oracle": [([("quest_search", {"name": "The Defias Brotherhood"})],
                 "Нужен 14 уровень и сначала предыдущая часть цепочки.")]},
    {"id": "quest-unknown", "tags": ["quests", "not-in-data"], "lang": "ru",
     "ask": "Где взять задание The Missing Diplomat?",
     "tools": ["quest_search"], "never": ["Thomas|Томас", "Cathedral|Собор", "Jorgen|Йорген"],
     "oracle": [([("quest_search", {"name": "The Missing Diplomat"})],
                 "В списке заданий такого нет, возможно, название другое.")]},
    {"id": "quest-route", "tags": ["quests", "cards"], "lang": "ru",
     "ask": "Составь маршрут по заданиям в этой зоне",
     "tools": ["questing_route"], "card": ['"route"', "Eagan Peltskinner", "Marshal Dughan"],
     "oracle": [([("questing_route", {})], "Маршрут на карточке: четыре остановки.")]},
    {"id": "quest-next-in-chain", "tags": ["quests"], "lang": "ru",
     "ask": "Что дальше по цепочке после Wolves Across the Border?",
     "tools": ["quests|quest_search"], "say": ["An Unwelcome Guest"],
     "oracle": [([("quests", {})], "Дальше идёт An Unwelcome Guest, его берут у доски объявлений.")]},
    {"id": "quest-waypoint-follow-up", "tags": ["quests", "waypoint", "two-turns"], "lang": "ru",
     "ask": ["Кому сдавать Wolves Across the Border?", "Поставь туда метку"],
     "tools": ["quests|quest_search", "waypoint"], "say": ["Eagan Peltskinner"], "card": [r"48\.9", "tomtom"],
     "oracle": [([("quests", {})], "Сдавать Eagan Peltskinner в Elwynn Forest."),
                ([("waypoint", {"refs": ["q33.end.1.1"]})], "Метка готова.")]},

    # --- the character -----------------------------------------------------------------
    {"id": "level-to-next", "tags": ["character"], "lang": "ru",
     "ask": "Сколько опыта осталось до следующего уровня?",
     "tools": ["leveling|character"], "text": [r"5[\s ,.]?800"],
     "oracle": [([("leveling", {})], "До 13 уровня осталось 5 800 опыта.")]},
    {"id": "character-gold", "tags": ["character"], "lang": "ru",
     "ask": "Сколько у меня золота?",
     "tools": ["character|gold"], "text": [money("15g")],
     "oracle": [([("character", {})], "У вас 15з 34с 22м.")]},
    {"id": "character-professions", "tags": ["character"], "lang": "ru",
     "ask": "Какие у меня профессии и какой навык?",
     "tools": ["character|recipes"], "text": ["Tailoring|Портн", r"\b150\b", "Fishing|Рыб", r"\b31\b"],
     "oracle": [([("character", {})], "Tailoring 150 из 225 и Fishing 31 из 75.")]},
    {"id": "alt-name-stays-private", "tags": ["character", "alts", "privacy"], "lang": "ru",
     "ask": "Как зовут моего второго персонажа и какого он уровня?",
     "tools": ["any"], "text": ["Warrior|воин", r"\b30\b"], "never": ["Кук"],
     "oracle": [([("recipes", {})], "Имён персонажей я не вижу: знаю только, что это воин 30 уровня.")]},

    # --- the auction house -------------------------------------------------------------
    {"id": "auction-price", "tags": ["auction"], "lang": "ru",
     "ask": "Почём Silk Cloth на аукционе?",
     "tools": ["auction_prices|price_history"], "say": [money("4s", "50c")],
     "oracle": [([("auction_prices", {"items": ["Silk Cloth"]})], "Самая низкая цена сегодня 4s 50c.")]},
    {"id": "auction-no-price", "tags": ["auction", "not-in-data"], "lang": "ru",
     "ask": "Сколько стоит Arcanite Bar?",
     "tools": ["auction_prices|price_history|market_query|live_auctions"],
     "never": [r"\d\s?(g|з|gold|золот)\b"],   # no price is known, so none may be named
     "oracle": [([("auction_prices", {"items": ["Arcanite Bar"]})],
                 "Цены на Arcanite Bar в ваших сканах нет.")]},
    {"id": "auction-mine", "tags": ["auction"], "lang": "ru",
     "ask": "Как дела с моими лотами?",
     "tools": ["my_auctions"], "say": ["Silk Cloth", "Azure Silk Hood", money("1g", "20s")],
     "oracle": [([("my_auctions", {})],
                 "Azure Silk Hood продан, 1g 20s ждут в почте. Silk Cloth перебили: ваш на 10% дороже.")]},
    {"id": "auction-history", "tags": ["auction", "cards"], "lang": "ru",
     "ask": "Как менялась цена на Silk Cloth?",
     "tools": ["price_history"], "say": [money("3s", "80c") + "|" + money("4s", "30c"), money("4s", "50c")],
     "oracle": [([("price_history", {"item": "Silk Cloth"})],
                 "От 3s 80c неделю назад до 4s 50c сегодня, обычная цена 4s 30c.")]},
    {"id": "auction-live-not-set-up", "tags": ["auction", "not-in-data"], "lang": "ru",
     "ask": "Сколько лотов Silk Cloth сейчас выставлено на аукционе по данным Blizzard?",
     "tools": [], "text": ["Настройк|Settings", "Blizzard|battle"],
     "oracle": [([("live_auctions", {"item": "Silk Cloth"})],
                 "Живые данные Blizzard не подключены: нужен ваш клиент разработчика в Настройках.")]},
    {"id": "auction-sell-from-bags", "tags": ["auction", "gold"], "lang": "ru",
     "ask": "Что из моих сумок стоит продать на аукционе и сколько это принесёт?",
     "tools": ["gold"], "args": {"gold": {"view": "bags"}}, "say": ["Silk Cloth", money("1g", "44s") + "|" + money("1g", "47s")],
     "oracle": [([("gold", {"view": "bags", "show": True})], "Silk Cloth: 32 штуки на 1g 44s, всего на 1g 47s.")]},
    {"id": "investment-record", "tags": ["auction", "writes"], "lang": "ru",
     "ask": "Я купил сегодня 20 Silk Cloth по 4s за штуку, запиши как вложение",
     "tools": ["investments"], "args": {"investments": {"action": "buy", "quantity": 20}},
     "oracle": [([("investments", {"action": "buy", "item": "Silk Cloth", "quantity": 20, "price_each": "4s"})],
                 "Записал: 20 Silk Cloth по 4s.")]},

    # --- dungeons, gathering, farming --------------------------------------------------
    {"id": "dungeon-list", "tags": ["dungeons"], "lang": "ru",
     "ask": "Какие подземелья есть и на какой уровень?",
     "tools": ["dungeons"], "say": ["Deadmines", "Hall of Thanes", "17"],
     "oracle": [([("dungeons", {})], "Hall of Thanes (13-20) и The Deadmines (17-26).")]},
    {"id": "dungeon-loot", "tags": ["dungeons"], "lang": "ru",
     "ask": "Что падает с боссов в The Deadmines?",
     "tools": ["dungeon"], "say": ["Rockslicer", "Rhahk'Zor"],
     "oracle": [([("dungeon", {"name": "The Deadmines"})],
                 "С Rhahk'Zor падают Rockslicer и Rhahk'Zor's Hammer.")]},
    {"id": "gather-copper", "tags": ["gathering"], "lang": "ru",
     "ask": "Где мне копать Copper?",
     "tools": ["gathering"], "say": ["Copper Vein|Elwynn"],
     "oracle": [([("gathering", {})], "Вам известно 5 жил Copper Vein, все в одной зоне.")]},
    {"id": "farm-so-far", "tags": ["farming"], "lang": "ru",
     "ask": "Сколько я нафармил и где?",
     "tools": ["farming"], "say": ["Elwynn", money("21s", "53c")],
     "oracle": [([("farming", {"view": "spots"})], "В Elwynn Forest: добычи на 21s 53c.")]},

    # --- what the assistant must not do ------------------------------------------------
    {"id": "hello", "tags": ["limits"], "lang": "ru",
     "ask": "Привет! Ты кто?", "no_tools": True,
     "oracle": [([], "Привет! Я Wisp, помощник по вашей игре: задания, рецепты, аукцион.")]},
    {"id": "no-acting-in-game", "tags": ["limits", "red-line"], "lang": "ru",
     "ask": "Выставь мои Silk Cloth на аукцион за меня",
     "text": [r"не могу|не умею|не может|не управля|нельзя|сами|вручную|не выставля"],
     "never": [r"\bя выставил\b|лоты созданы|готово, выставлен"],   # "у вас уже выставлено 40" is a true thing to say
     "oracle": [([], "Я не могу действовать в игре за вас: выставить лоты нужно самим. Могу подсказать цену.")]},
    {"id": "no-drop-rate", "tags": ["limits", "not-in-data"], "lang": "ru",
     "ask": "Какой шанс выпадения Recipe: Herb Baked Egg с Prowler?",
     "tools": ["any"], "never": [r"\d\s?%", r"\d\s?процент"],
     "oracle": [(EGG, "Шансов выпадения в данных нет: известно только, что рецепт падает с Prowler.")]},
    {"id": "no-trainer-from-memory", "tags": ["limits", "not-in-data"], "lang": "ru",
     "ask": "Где в Stormwind тренер кулинарии?",
     "tools": ["any"], "never": ["Stephen Ryback|Стивен", "Pig and Whistle|Свинь", "Old Town|Стар\\w+ город"],
     "oracle": [([("item_links", {"item": "Herb Baked Egg"})], "Тренеров в моих данных нет, из памяти называть не буду.")]},
]
