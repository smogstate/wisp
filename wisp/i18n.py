"""The language of what Wisp itself writes: cards, tables, messages.

The model answers in the player's language by itself. This is for everything
that is put together in code. English text is the key; a language that has no
entry for a text shows the English, so nothing is ever blank.

    t("Questing in {zone}", zone=zone)          one text
    tn(n, "{n} stop", "{n} stops")              a text with a count, in the right form

The language is one setting for the whole program: Wisp serves one player.
"""

LANG = "en"
LANGUAGES = ("en", "ru")


def set_language(code) -> str:
    global LANG
    LANG = code if code in LANGUAGES else "en"
    return LANG


def language_of(locale) -> str:
    """'ruRU', 'ru_RU', 'Russian_Russia' -> 'ru'; anything else -> 'en'."""
    return "ru" if isinstance(locale, str) and locale.lower().startswith("ru") else "en"


def t(text: str, **values) -> str:
    out = RU.get(text, text) if LANG == "ru" else text
    return out.format(**values) if values else out


def tn(n: int, one: str, many: str, **values) -> str:
    """The form of a counted text for n. Russian has three: 1 лот, 2 лота, 5 лотов."""
    if LANG == "ru" and many in RU_COUNTED:
        forms = RU_COUNTED[many]
        last, last2 = abs(n) % 10, abs(n) % 100
        text = forms[0] if last == 1 and last2 != 11 else forms[1] if 2 <= last <= 4 and not 12 <= last2 <= 14 else forms[2]
    else:
        text = one if n == 1 else many
    return text.format(n=num(n), **values)


def num(n) -> str:
    """1200 -> '1,200', or '1 200' in Russian."""
    text = f"{n:,}"
    return text.replace(",", " ") if LANG == "ru" else text


def coin_units() -> tuple[str, str, str]:
    return ("з", "с", "м") if LANG == "ru" else ("g", "s", "c")


RU_COUNTED = {
    "the fight took {n} seconds": ("бой длился {n} секунду", "бой длился {n} секунды", "бой длился {n} секунд"),
    "to buy: {n} items, prices unknown": ("купить: {n} предмет, цена неизвестна", "купить: {n} предмета, цены неизвестны", "купить: {n} предметов, цены неизвестны"),
    "{n} kinds of craft": ("{n} вид изготовления", "{n} вида изготовления", "{n} видов изготовления"),
    "{n} recipes use it directly": ("{n} рецепт использует напрямую", "{n} рецепта используют напрямую", "{n} рецептов используют напрямую"),
    "{n} products in all": ("всего {n} изделие", "всего {n} изделия", "всего {n} изделий"),
    "{n} hours": ("{n} час", "{n} часа", "{n} часов"),
    "{n} days": ("{n} день", "{n} дня", "{n} дней"),
    "{n} quests finished": ("Выполнено {n} задание", "Выполнено {n} задания", "Выполнено {n} заданий"),
    "{n} stops": ("{n} остановка", "{n} остановки", "{n} остановок"),
    "{n} known nodes": ("{n} известная точка", "{n} известные точки", "{n} известных точек"),
    "{n} items": ("{n} предмет", "{n} предмета", "{n} предметов"),
    "{n} auctions up": ("выставлен {n} лот", "выставлено {n} лота", "выставлено {n} лотов"),
    "{n} scan days": ("{n} день сканирования", "{n} дня сканирования", "{n} дней сканирования"),
    "{n} web pages": ("{n} веб-страница", "{n} веб-страницы", "{n} веб-страниц"),
    "{n} auctions": ("{n} лоте", "{n} лотах", "{n} лотах"),
}

RU = {
    # how a profession goes on (profession_path)
    "{profession} trainer": "учитель: {profession}", "Book: {item}": "Книга: {item}", "Quest: {name}": "Задание: {name}", "Profession": "Профессия",
    "{profession}: how it goes on": "{profession}: как учить дальше", "your skill: {skill} of {cap}": "ваш навык: {skill} из {cap}",
    "your character does not have it": "у вашего персонажа её нет", "Where to learn": "Где учить", "Zone": "Зона", "Needs": "Нужно",
    "Trainers, books and quests as Questie lists them. A trainer's title often says the rank it teaches.": "Учителя, книги и задания по данным Questie. Звание учителя часто говорит, какому рангу он учит.",
    # how the character died (wisp/deaths.py)
    "Deaths on record": "Записано смертей",
    "What happened": "Что произошло",
    "none yet: the addon files the last half minute of a fight when the character dies": "пока нет: аддон записывает последние полминуты боя, когда персонаж погибает",
    "a player": "игрок",
    "level {n}": "уровень {n}",
    "a player's pet": "питомец игрока",
    "a creature": "существо",
    "melee": "ближний бой",
    "critical": "крит",
    "over time": "периодический",
    "missed": "промах",
    "healing: {spell}": "исцеление: {spell}",
    "on you: {spell}": "на вас: {spell}",
    "{spell} ends": "{spell} спадает",
    "your {spell} is interrupted": "ваше {spell} прервано",
    "cast": "заклинание",
    "you cast {spell}": "вы применили {spell}",
    "you hit with {spell}": "вы ударили: {spell}",
    "{time} ago": "{time} назад",
    "the world": "окружение",
    "unknown": "неизвестно",
    "s": "с",
    "Damage taken: {total}": "Получено урона: {total}",
    "Held by: {list}": "Удерживали: {list}",
    "From the game's combat log: the whole fight when a player killed you (up to 3 minutes), the last 30 seconds otherwise. Other players are shown by class and race only.": "Из журнала боя игры: весь бой, если вас убил игрок (до 3 минут), иначе последние 30 секунд. Другие игроки показаны только классом и расой.",
    "{n} earlier lines of the fight are not shown; the sums count them.": "Более ранних строк боя не показано: {n}; в суммах они учтены.",
    "Killed by: {who}": "Убил: {who}",
    "Before death": "До смерти",
    "Who": "Кто",
    "What": "Что",
    "Amount": "Сколько",
    "Health": "Здоровье",
    "How you died": "Как вы погибли",
    "Warrior": "Воин",
    "Paladin": "Паладин",
    "Hunter": "Охотник",
    "Rogue": "Разбойник",
    "Priest": "Жрец",
    "Shaman": "Шаман",
    "Mage": "Маг",
    "Warlock": "Чернокнижник",
    "Druid": "Друид",
    "Death Knight": "Рыцарь смерти",
    "Monk": "Монах",
    "Human": "Человек",
    "Dwarf": "Дворф",
    "Night Elf": "Ночной эльф",
    "Gnome": "Гном",
    "Orc": "Орк",
    "Undead": "Нежить",
    "Tauren": "Таурен",
    "Troll": "Тролль",
    "Blood Elf": "Эльф крови",
    "Draenei": "Дреней",
    "Goblin": "Гоблин",
    "Worgen": "Ворген",
    # -- since last time
    "less than an hour": "меньше часа", "Level {a} → {b}": "Уровень {a} → {b}", " and {n} more": " и ещё {n}",
    "Picked up: ": "Взято: ", "Dropped: ": "Брошено: ", "{name}: learned": "{name}: изучено", "Money {change}": "Деньги {change}",
    "Now in {zone}": "Сейчас: {zone}",
    # -- what went wrong, said to the player
    "no game folder was given": "папка игры не указана",
    "no Wisp snapshot found: enable the Wisp addon, log in, then log out once": "снимок Wisp не найден: включите аддон Wisp, войдите в игру и один раз выйдите",
    "the Wisp snapshot is damaged: log in and out once more to write a new one": "снимок Wisp повреждён: войдите в игру и выйдите ещё раз, чтобы записать новый",
    "The {company} key was rejected. Check it in Settings.": "Ключ {company} отклонён. Проверьте его в настройках.",
    "{company} is rate limiting this key. Wait a moment and ask again.": "{company} ограничивает частоту запросов по этому ключу. Подождите немного и спросите снова.",
    "Could not reach {company}. Check the connection and ask again.": "Не удалось связаться с {company}. Проверьте соединение и спросите снова.",
    "{company} answered with an error ({code}): {message}": "{company} ответил ошибкой ({code}): {message}",
    "The model sent a tool call that could not be read. Ask again.": "Модель прислала вызов инструмента, который не удалось прочитать. Спросите снова.",
    "The model declined to answer that.": "Модель отказалась отвечать на это.",
    "The answer was cut off before it finished. Ask again.": "Ответ оборвался, не закончившись. Спросите снова.",
    "That took too many lookups. Try a narrower question.": "Понадобилось слишком много запросов. Попробуйте более узкий вопрос.",
    "The keyring package is missing, so the key cannot be saved.": "Не хватает пакета keyring, поэтому ключ нельзя сохранить.",
    "This machine's credential store refused the key: {why}": "Хранилище учётных данных этого компьютера не приняло ключ: {why}",
    "Could not write to {target}: {why}": "Не удалось записать в {target}: {why}",
    "There is no downloaded update to restart into.": "Нет загруженного обновления для перезапуска.",
    "The downloaded update is no longer there. Wisp will fetch it again.": "Загруженного обновления уже нет. Wisp загрузит его снова.",
    "Could not update: {why}. Wisp will try again when it next starts.": "Не удалось обновиться: {why}. Wisp попробует ещё раз при следующем запуске.",
    "Wisp installed its addon into the game. Restart the game so it loads.": "Wisp установил свой аддон в игру. Перезапустите игру, чтобы он загрузился.",
    "Wisp updated its addon in the game. Restart the game so the new version loads.": "Wisp обновил свой аддон в игре. Перезапустите игру, чтобы загрузилась новая версия.",
    "The addon is installed. Restart the game so it loads.": "Аддон установлен. Перезапустите игру, чтобы он загрузился.",
    "No {company} key yet. Open Settings and paste one.": "Ключа {company} пока нет. Откройте настройки и вставьте его.",
    "A package is missing: {name}": "Не хватает пакета: {name}", "Cannot start the model: {why}": "Не удалось запустить модель: {why}",
    "scripted test model": "тестовая модель по сценарию", "scripted model, no key needed": "тестовая модель, ключ не нужен",
    "That folder has no Interface\\AddOns inside. Choose the World of Warcraft folder or its _forever_ folder.": "В этой папке нет Interface\\AddOns. Выберите папку World of Warcraft или её папку _forever_.",
    "Choose the game folder first.": "Сначала выберите папку игры.", "Type or paste the folder path.": "Введите или вставьте путь к папке.",
    "Today's spending limit of ${cap} is reached (${spent} spent). It starts again tomorrow; the limit is in Settings.": "Сегодняшний лимит расходов ${cap} достигнут (потрачено ${spent}). Завтра он начнётся заново; лимит задаётся в настройках.",
    "Stopped here: today's spending limit of ${cap} is reached. It starts again tomorrow; the limit is in Settings.": "Остановлено: сегодняшний лимит расходов ${cap} достигнут. Завтра он начнётся заново; лимит задаётся в настройках.",
    "That conversation could not be opened.": "Этот разговор не удалось открыть.",
    "That conversation was held with {label}. Add a {company} key in Settings to continue it.": "Этот разговор вёлся с {label}. Добавьте ключ {company} в настройках, чтобы продолжить его.",
    "There is nothing to fork yet.": "Пока нечего ответвлять.", "Wait for the answer to finish.": "Дождитесь конца ответа.",
    "Ask something, up to 4000 characters.": "Спросите что-нибудь, до 4000 символов.",
    # -- Check setup
    "Game folder": "Папка игры", "not chosen yet": "ещё не выбрана", "Wisp addon installed": "Аддон Wisp установлен", "not installed": "не установлен",
    "an older version is installed: press Update addon": "установлена старая версия: нажмите «Обновить аддон»",
    "Questie installed": "Questie установлен", "without it Wisp knows almost no locations": "без него Wisp почти не знает, где что находится",
    "TomTom installed": "TomTom установлен", "without it the built-in map pin is used": "без него используется встроенная метка на карте",
    "DungeonJournal installed": "DungeonJournal установлен",
    "without it Wisp knows nothing about dungeons: their quests, what those need first, the loot": "без него Wisp ничего не знает о подземельях: их задания, что нужно сделать до них, добычу",
    "Snapshot file": "Файл снимка", "install the addon first": "сначала установите аддон",
    "the game has not noticed the addon yet: close the game completely, start it again, and on the character screen open AddOns and tick Wisp": "игра ещё не заметила аддон: полностью закройте игру, запустите снова и на экране выбора персонажа откройте «Модификации» и отметьте Wisp",
    "Wisp is switched off in the game: on the character screen open AddOns and tick Wisp": "Wisp выключен в игре: на экране выбора персонажа откройте «Модификации» и отметьте Wisp",
    "the game has the addon but has not saved yet: log in with your character, then log out to the character screen (the game only writes addon data at logout)": "игра видит аддон, но ещё не сохраняла данные: войдите персонажем, затем выйдите на экран выбора персонажа (игра записывает данные аддонов только при выходе)",
    "restart the game so it sees the addon, log in with your character, then log out to the character screen (the game only writes addon data at logout)": "перезапустите игру, чтобы она увидела аддон, войдите персонажем, затем выйдите на экран выбора персонажа (игра записывает данные аддонов только при выходе)",
    "Snapshot readable": "Снимок читается", "{quests} quests, saved {minutes} minutes ago": "заданий: {quests}, сохранён {minutes} мин назад",
    "Questie data in the snapshot": "Данные Questie в снимке",
    "Questie was not ready at logout; play a little, then log out again": "Questie не был готов при выходе; поиграйте немного и выйдите ещё раз",
    "Open quests in the snapshot": "Доступные задания в снимке", "{n} the character could pick up": "персонаж может взять: {n}",
    "none: Questie's list of available quests was not found; tell Wisp's author which Questie version this is": "нет: список доступных заданий Questie не найден; сообщите автору Wisp, какая это версия Questie",
    "Snapshots kept for this character": "Сохранено снимков этого персонажа", "{n}: one per logout Wisp has seen": "{n}: по одному на каждый выход, который видел Wisp",
    "Dungeon data in the snapshot": "Данные о подземельях в снимке", "{n} dungeons": "подземелий: {n}",
    "not yet: log in and out once more with the new Wisp addon": "пока нет: войдите и выйдите ещё раз с новым аддоном Wisp",
    "Recipes read from the game": "Рецепты, прочитанные из игры",
    "none yet: open your profession window in the game once, then log out": "пока нет: откройте в игре окно профессии, затем выйдите",
    "Auction prices in the snapshot": "Цены аукциона в снимке", "for {items} items, {named} of them with a name so far": "предметов: {items}, из них с названием: {named}",
    "none yet: scan the auction house with Auctionator, then log out": "пока нет: просканируйте аукцион через Auctionator, затем выйдите",
    "Price history kept by Wisp": "История цен в Wisp", "{rows} prices of {items} items, from {first} to {last}": "цен: {rows}, предметов: {items}, с {first} по {last}",
    "none yet: it grows with every day you scan the auction house": "пока нет: она растёт с каждым днём, когда вы сканируете аукцион",
    "Gathering nodes in the snapshot": "Места сбора в снимке", "{places} places of {kinds} kinds": "мест: {places}, видов: {kinds}",
    "none yet: GatherMate2 notes each herb, vein and pool as you gather; log out after gathering": "пока нет: GatherMate2 запоминает каждую траву, жилу и косяк рыбы при сборе; выйдите из игры после сбора",
    "Experience measured by Wisp": "Опыт, измеренный Wisp", "{n} days of play": "дней игры: {n}",
    "none yet: it starts with your next gain": "пока нет: начнётся со следующего получения опыта",
    "Auction listings in the snapshot": "Лоты аукциона в снимке", "the price ladders of {n} items, from the last session": "ценовые лестницы предметов: {n}, из последней сессии",
    "none from the last session: they come from a full Auctionator scan or a search for an item": "из последней сессии нет: они появляются после полного сканирования Auctionator или поиска предмета",
    "Bank contents in the snapshot": "Содержимое банка в снимке", "{n} kinds of item": "видов предметов: {n}",
    "not yet: open your bank once, then log out": "пока нет: откройте банк, затем выйдите",
    "Your auctions in the snapshot": "Ваши лоты в снимке", "{n} as the Auctions tab last showed them": "{n}: как их последний раз показала вкладка «Аукционы»",
    "not yet: look at the Auctions tab at the auction house, then log out": "пока нет: загляните на вкладку «Аукционы» на аукционе, затем выйдите",
    "Auction mail noted by Wisp": "Почта аукциона, учтённая Wisp", "{n} purchases and sales in the last two months": "покупок и продаж за два месяца: {n}",
    "none yet: open a mailbox that holds auction mail, then log out": "пока нет: откройте почтовый ящик с письмами аукциона, затем выйдите",
    "Looting measured by Wisp": "Добыча, измеренная Wisp", "{looted} corpses and nodes over {days} days": "трупов и мест сбора: {looted}, дней: {days}",
    "none yet: it starts with the first thing you loot after this update": "пока нет: начнётся с первой добычи после этого обновления",
    "Every recipe in the game": "Все рецепты игры", "{n} recipes beyond your own professions": "рецептов сверх ваших профессий: {n}",
    "the game gave none: tell Wisp's author": "игра не выдала ни одного: сообщите автору Wisp",
    "not yet: Wisp's addon reads them in the background about a minute after login; then /wisp sync": "пока нет: аддон Wisp читает их в фоне примерно через минуту после входа; затем /wisp sync",
    "turns grey at skill {n}": "серый с навыка {n}", "Nothing can be shown for that item.": "Для этого предмета показать нечего.",
    "Shopping list, down to raw materials": "Список покупок: до сырья", "Buy": "Купить",
    "bags": "сумки", "bank": "банк", "shared bank": "общий банк", "{who}: bags": "{who}: сумки", "{who}: bank": "{who}: банк",
    "To craft {n} × {item}": "Чтобы изготовить {n} × {item}", "nothing to buy: you hold it all": "покупать нечего: всё уже есть",
    "to buy: about {sum}": "купить: около {sum}", "to buy: {sum} for what has a price": "купить: {sum} за то, что имеет цену",
    "Held elsewhere: {where}": "Лежит в других местах: {where}",
    "Lowest listings from your own scans. What you and your other characters held at each one's last logout.": "Минимальные цены из ваших сканирований. Запасы — ваши и других ваших персонажей, на момент последнего выхода каждого.", "Where it is used": "Где используется",
    "Raw materials for {n} × {item}": "Сырьё для {n} × {item}", "about {sum} at auction": "около {sum} на аукционе",
    "{sum} for what has a price": "{sum} за то, что имеет цену", "Need": "Нужно", "Cost": "Цена",
    "In this order: {steps}": "По порядку: {steps}",
    "Lowest listings from your own scans. Bags and bank as of your last logout.": "Минимальные цены из ваших сканирований. Сумки и банк — на момент последнего выхода.",
    "What {item} goes into": "Во что идёт {item}", "Product": "Изделие", "Profession": "Профессия", "Takes": "Расход", "Listed for": "Цена лота",
    "Takes: how many {item} one of the product needs in all, through every craft on the way.": "Расход: сколько {item} нужно на одно изделие всего, через все промежуточные изготовления.",
    "One-click cards": "Карточки в один клик", "the game reads Wisp's inbox": "игра читает входящие Wisp",
    "not yet: restart the game once, keep its sound enabled (the volume may be zero), then /wisp sync": "пока нет: перезапустите игру один раз, не выключайте в ней звук (громкость может быть нулевой), затем /wisp sync",
    "Wisp added what one-click cards need. Restart the game once so it sees the new files.": "Wisp добавил то, что нужно для карточек в один клик. Перезапустите игру один раз, чтобы она увидела новые файлы.",
    "Could not send it to the game: {why}": "Не удалось отправить в игру: {why}",
    "Every item from Questie": "Все предметы из Questie", "{n} items with who sells or drops them": "предметов с продавцами и источниками: {n}",
    "the game could not read it: {why}": "игра не смогла прочитать: {why}",
    "Every quest from Questie": "Все задания из Questie", "{n} quests this character could take": "заданий, доступных этому персонажу: {n}",
    "not yet: Wisp's addon reads them in the background about a minute after login; log out after that": "пока нет: аддон Wisp читает их в фоне примерно через минуту после входа; после этого выйдите из игры",
    "Quest locations found": "Найдено мест заданий",
    "Blizzard API": "API Blizzard", "not set up: optional, in Settings": "не настроен: необязательно, в настройках",
    "no connection: {why}": "нет связи: {why}", "Blizzard did not accept the client: {why}": "Blizzard не принял клиента: {why}",
    "Blizzard sent no token": "Blizzard не прислал токен", "not JSON": "ответ не JSON",
    "Blizzard API: the client": "API Blizzard: клиент", "accepted ({region})": "принят ({region})",
    "Blizzard API: WoW Forever": "API Blizzard: WoW Forever", "no rulesets listed": "наборы правил не перечислены",
    "{n} rulesets: {names}": "наборов правил: {n}: {names}", "no auction houses listed": "аукционы не перечислены",
    "Blizzard API: auction houses": "API Blizzard: аукционы", "Blizzard API: items": "API Blizzard: предметы",
    "Blizzard API: game data": "API Blizzard: данные игры", "answers as {namespace}": "отвечает как {namespace}",
    "game data answers under none of: {tried}": "данные игры не отвечают ни под одним именем: {tried}",
    "item 4306 is {name}; an item has: {fields}": "предмет 4306 — {name}; поля предмета: {fields}",
    "Blizzard API: skill a recipe needs": "API Blizzard: навык, нужный для рецепта",
    "fields that may hold it: {fields}": "поля, где он может быть: {fields}", "no such field; a tier has: {fields}": "такого поля нет; поля ступени: {fields}",
    "Blizzard API: lists of names": "API Blizzard: списки названий",
    "Blizzard API: quests": "API Blizzard: задания", "quest {id}: {why}": "задание {id}: {why}",
    "quest {id} is {name}; it has: {fields}; its rewards have: {rewards}": "задание {id} — {name}; поля: {fields}; поля наград: {rewards}",
    "Blizzard API: professions": "API Blizzard: профессии", "no professions listed": "профессии не перечислены", "{n}: {names}": "{n}: {names}",
    "Blizzard API: recipes": "API Blizzard: рецепты",
    "{profession} has no skill tiers; it has: {fields}": "у профессии «{profession}» нет ступеней навыка; есть поля: {fields}",
    "{profession} lists no recipes; its tier has: {fields}": "у профессии «{profession}» нет списка рецептов; в ступени есть поля: {fields}",
    "{profession}: {tiers} tiers, {n} recipes in the first; {recipe} needs {needs}; a recipe has: {fields}": "{profession}: ступеней {tiers}, в первой рецептов {n}; для «{recipe}» нужно: {needs}; поля рецепта: {fields}",
    "item 4306 is {name}": "предмет 4306 — {name}", "Blizzard API: listings": "API Blizzard: лоты", "house {house}: {why}": "аукцион {house}: {why}",
    "house {house}: {n} auctions, {goods} trade goods; fields: {fields}": "аукцион {house}: лотов {n}, товаров {goods}; поля: {fields}",
    "Blizzard API: your auction house": "API Blizzard: ваш аукцион",
    "not known yet: put an auction up, look at the Auctions tab, log out, then check again": "пока неизвестен: выставьте лот, загляните на вкладку «Аукционы», выйдите из игры и проверьте снова",
    "house {house} {name}: {found} of your {mine} auctions are in it": "аукцион {house} {name}: в нём {found} из ваших {mine} лотов",
    "Blizzard API: price per item or per stack": "API Blizzard: цена за штуку или за стопку",
    "the API's buyout is the game's price for one ({n} auctions agree)": "выкуп в API — цена за одну штуку, как в игре (совпало лотов: {n})",
    "the API's buyout is for the whole stack ({n} auctions agree)": "выкуп в API — за всю стопку (совпало лотов: {n})",
    "neither: the prices differ from the game's": "ни то ни другое: цены отличаются от игровых",
    "your auctions are in none of the {n} houses": "ваших лотов нет ни в одном из {n} аукционов", "{n} for quest givers and turn-ins": "{n}: где берут и сдают задания",
    "none: is your quest log empty?": "нет: журнал заданий пуст?",
    "{company} key": "Ключ {company}", "from environment": "из переменной окружения", "saved on this PC": "сохранён на этом ПК",
    "not entered yet": "ещё не введён", "A real call to the model": "Настоящий запрос к модели", "a package is missing: {name}": "не хватает пакета: {name}",
    "{model} answered: {reply}": "{model} ответила: {reply}", "the model sent no text": "модель не прислала текста",
    # quest places, as they are named on waypoints
    "quest giver": "выдаёт задание", "turn in": "принимает задание", "next quest": "следующее задание",
    "{title} (next step)": "{title} (следующий шаг)", "{name} entrance": "{name}: вход", "{name} (quest start)": "{name} (начало задания)",
    "sells {item}": "продаёт: {item}", "drops {item}": "добыча: {item}", "holds {item}": "внутри: {item}",
    "Waypoint: {title}": "Метка: {title}", "Route: {title}": "Маршрут: {title}",
    # leveling
    "Leveling": "Прокачка", "{xp} XP an hour": "{xp} опыта в час", "Your leveling": "Ваша прокачка",
    "level {level} · {share} in": "уровень {level} · пройдено {share}",
    "about {h}h {m}m of play at this pace": "примерно {h} ч {m} мин игры в таком темпе",
    "about {m} minutes of play at this pace": "примерно {m} мин игры в таком темпе",
    "Day": "День", "Gained": "Опыт", "Time": "Время", "Per hour": "В час", "Quests": "Задания",
    "{share} of it from handing in quests.": "{share} — за сдачу заданий.",
    "{xp} XP of rested bonus ({share} of a level)": "{xp} опыта бонуса отдыха ({share} уровня)",
    "Time between gains, breaks over five minutes left out.": "Время между получением опыта; перерывы дольше пяти минут не считаются.",
    "{h}h {m}m": "{h} ч {m} мин", "too little time to say": "мало данных",
    # the questing route
    "Questing in {zone}": "Задания: {zone}", "level {level}": "уровень {level}",
    "{n} to hand in": "сдать: {n}", "{n} to pick up": "взять: {n}", "{n} to go on with": "продолжить: {n}",
    "Hand in: {quest}": "Сдать: {quest}", " ({xp} XP)": " ({xp} опыта)",
    "Pick up: {quest} (level {level}{kind}{xp})": "Взять: {quest} ({level} ур.{kind}{xp})", ", {xp} XP": ", {xp} опыта",
    "elite": "элитное", "dungeon": "подземелье", "raid": "рейд", "pvp": "PvP",
    "Go on: {what}": "Продолжить: {what}", " ({xp} XP when handed in)": " ({xp} опыта за сдачу)",
    "{xp} XP to hand in ({share})": "{xp} опыта за сдачу сейчас ({share})",
    "{xp} XP in the quests picked up ({share})": "{xp} опыта во взятых заданиях ({share})",
    "{share} of a level": "{share} уровня",
    "As of your last logout. Quest givers first, then the quests under way, each time the nearest.":
        "На момент последнего выхода из игры. Сначала те, кто выдаёт задания, затем начатые задания — каждый раз ближайшее.",
    " Left out: {grey} grey": " Пропущено: серых — {grey}", " and {red} red": ", красных — {red}",
    "Waypoints for TomTom: Copy, then in the game Ctrl+V twice": "Метки для TomTom: «Копировать», затем в игре дважды Ctrl+V",
    # market depth
    "Market depth": "Глубина рынка",
    "{total} listed in {auctions} · from {lowest} · seen {seen}": "{total} шт. в {auctions} · от {lowest} · данные: {seen}",
    "in the last session": "в последней сессии", "{days} days before the last logout": "за {days} дн. до последнего выхода",
    "Up to": "До цены", "Listed": "Выставлено", "Above the lowest": "Выше минимума",
    "How many are listed up to each price, counted from the cheapest.": "Сколько выставлено до каждой цены, считая от самой низкой.",
    " Yours: {mine} from {price}, with {less} listed for less.": " Ваши: {mine} от {price}; дешевле выставлено {less}.",
    # own auctions
    "Your auctions": "Ваши лоты", "listed for {sum}": "на сумму {sum}", " · {n} undercut": " · перебито: {n}",
    " · {sum} sold, in the mail": " · продано, деньги на почте: {sum}",
    "Item": "Предмет", "Qty": "Кол-во", "Each": "За шт.", "Lowest": "Минимум", "Status": "Статус", "you": "вы",
    "sold": "продано", "time left unknown": "срок неизвестен", "run out": "срок истёк", "{h}h left": "осталось {h} ч",
    "under an hour left": "меньше часа", ", has a bid": ", есть ставка",
    "As your Auctions tab last showed them. Lowest is from your own scans and may be older.":
        "По вкладке «Аукционы» на момент последнего просмотра. Минимум — из ваших сканирований и может быть старше.",
    # the ledger
    "Auction house": "Аукцион", "Auction house, in and out": "Аукцион: приход и расход",
    "last {days} days · received {received} · paid {paid} · balance {balance}": "за {days} дн. · получено {received} · потрачено {paid} · итог {balance}",
    "Sold": "Продано", "Received": "Получено", "Paid": "Заплачено", "Days ago": "Дней назад",
    "Real sales from your auction mail, after the house's cut{rate}.": "Реальные продажи по письмам аукциона, за вычетом комиссии{rate}.",
    "What you sold": "Что вы продали", "What you bought": "Что вы купили",
    "last {days} days, from your auction mail": "за {days} дн., по письмам аукциона",
    "Received is the sale price less the auction house's cut.": "«Получено» — цена продажи за вычетом комиссии аукциона.",
    "What each auction really cost.": "Сколько на самом деле стоил каждый лот.",
    # investments
    "Investment": "Вложение", "Investments": "Вложения", "Your investments": "Ваши вложения",
    "{held} held · bought {days} days ago": "в наличии {held} · куплено {days} дн. назад", "paid": "покупка",
    "Now": "Сейчас", "Gain": "Прирост", "Unsold gain": "Непроданная прибыль", "Held": "В наличии", "Profit": "Прибыль",
    "Bought at": "Куплено по",
    "The line is the day's lowest listing from your own scans; the level marked is what you paid. Listed worth, before the auction house's cut.":
        "Линия — минимальная цена дня по вашим сканированиям; отмеченный уровень — цена покупки. Оценка по выставленным ценам, без учёта комиссии аукциона.",
    "paid {paid} · worth {worth} · made {made} in all": "вложено {paid} · стоит {worth} · всего заработано {made}",
    "sold: {made}": "продано: {made}", "no listing known": "нет данных о цене",
    "At the lowest listing from your own scans, before the auction house's cut. \"Made in all\" adds what your sales brought.":
        "По минимальной выставленной цене из ваших сканирований, без учёта комиссии аукциона. «Всего заработано» включает выручку от продаж.",
    # gold
    "What your bags and bank would fetch": "Сколько стоят ваши сумки и банк", "What your bags would fetch": "Сколько стоят ваши сумки",
    "about {sum} in all, at auction or from a vendor": "всего около {sum} — на аукционе или у торговца",
    "Have": "Есть", "Worth": "Стоимость",
    "Lowest listings from your own scans, or what a vendor pays. Listed worth, not sales.":
        "Минимальные цены из ваших сканирований или цена торговца. Это цены выставленных лотов, а не продажи.",
    "Crafting for profit": "Ремесло ради прибыли", "your recipes, at your auction prices": "ваши рецепты по вашим ценам аукциона",
    "Recipe": "Рецепт", "Materials": "Материалы", "Sells for": "Цена продажи",
    "Per craft, materials bought at auction. Listed prices, before the auction house's cut.":
        "За одно изготовление, материалы — с аукциона. Цены выставленных лотов, без учёта комиссии аукциона.",
    # market scans
    "Cheaper than usual": "Дешевле обычного", "Dearer than usual": "Дороже обычного", "Rising": "Растут в цене", "Falling": "Падают в цене",
    "Biggest swings": "Сильнее всего колеблются", "Most listed": "Больше всего на аукционе",
    "last {days} days": "за {days} дн.", " · my bags": " · мои сумки", " · my materials": " · мои материалы", " · what i craft": " · что я изготавливаю",
    "Usual": "Обычно", "Against usual": "К обычной",
    "Lowest listings from your own Auctionator scans. A listing is not a sale.":
        "Минимальные цены из ваших сканирований Auctionator. Выставленный лот — ещё не продажа.",
    # research
    "Your own game data": "Ваши игровые данные", " and {pages}": " и {pages}", ", no web pages": ", без веб-страниц",
    "Kept in your notes": "Сохранено в заметках",
    # gathering and farming
    "{name}, {stops}": "{name}, {stops}", "{zone} · {nodes}": "{zone} · {nodes}", "{name} {n}": "{name} {n}",
    "What known nodes are worth": "Сколько стоят известные точки сбора", "every zone on record": "все известные зоны",
    "Node": "Ресурс", "Zone": "Зона", "Nodes": "Точек", "All": "Всего",
    "Every known node gathered once, at your auction prices. Not an hourly rate.":
        "Каждая известная точка собрана один раз, по вашим ценам аукциона. Это не доход в час.",
    "Your farming spots": "Ваши места фарма", "last {days} days, measured from your own loot": "за {days} дн., по вашей собственной добыче",
    "Looted": "Добыто", "Total": "Всего",
    "Coins plus loot at your auction prices, or what a vendor pays. Listed worth, not sales.":
        "Монеты плюс добыча по вашим ценам аукциона или по цене торговца. Это цены лотов, а не продажи.",
    "What you looted from what": "Что и с кого вы добыли", "everywhere": "везде", "{where}, last {days} days": "{where}, за {days} дн.",
    "Source": "Источник", "Per loot": "За добычу",
    "Worth per corpse or node looted, at your auction prices.": "Стоимость с одного трупа или точки сбора, по вашим ценам аукциона.",
    # shopping lists and crafting plans
    "Shopping list: {title}": "Список покупок: {title}", "{items} · {about}": "{items} · {about}", "about {sum}": "около {sum}",
    " · {n} without a price": " · без цены: {n}", "prices unknown": "цены неизвестны", "{price} each": "{price} за шт.",
    "Shopping list for Auctionator: Copy, then in the game Ctrl+V twice": "Список покупок для Auctionator: «Копировать», затем в игре дважды Ctrl+V",
    "grey: crafting it gives no skill": "серый: навык не растёт",
    "turns grey at skill {grey}: at most the first {n} crafts can give skill": "становится серым на навыке {grey}: навык дадут не больше первых {n} изготовлений",
    "not learned yet": "ещё не изучен", "{name}, skill {skill}": "{name}, навык {skill}",
    "Nothing to buy": "Покупать нечего", "To buy: about {sum}": "Купить: около {sum}", "To buy": "Купить",
    " · {n} without an auction price": " · без цены на аукционе: {n}",
    "As of your last logout; the bank as it was when you last opened it.": "На момент последнего выхода; банк — каким он был при последнем открытии.",
    "As of your last logout; the bank is not known: open it once and Wisp counts it.":
        "На момент последнего выхода; банк неизвестен: откройте его один раз, и Wisp его учтёт.",
    " Only orange gives a point on every craft: the rest may take more.": " Только оранжевые рецепты дают навык каждый раз: остальных может понадобиться больше.",
    # price charts
    "last {days} days · {scans}": "за {days} дн. · {scans}", "Next week": "Через неделю",
    "Lowest listing on each day you scanned, from your own Auctionator scans. A listing is not a sale.":
        "Минимальная цена за каждый день сканирования, по вашим данным Auctionator. Выставленный лот — ещё не продажа.",
    " The estimate rests on {based}: confidence {confidence}.": " Оценка основана на: {based}; уверенность — {confidence}.",
    "{n} days of scans over {span} days": "{n} дн. сканирований за {span} дн.",
    "weak": "низкая", "moderate": "средняя", "fair": "неплохая",
}
