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
    "{n} stops": ("{n} остановка", "{n} остановки", "{n} остановок"),
    "{n} known nodes": ("{n} известная точка", "{n} известные точки", "{n} известных точек"),
    "{n} items": ("{n} предмет", "{n} предмета", "{n} предметов"),
    "{n} auctions up": ("выставлен {n} лот", "выставлено {n} лота", "выставлено {n} лотов"),
    "{n} scan days": ("{n} день сканирования", "{n} дня сканирования", "{n} дней сканирования"),
    "{n} web pages": ("{n} веб-страница", "{n} веб-страницы", "{n} веб-страниц"),
    "{n} auctions": ("{n} лоте", "{n} лотах", "{n} лотах"),
}

RU = {
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
