"""Список городов РФ: для (а) фан-аута поисковых запросов по регионам,
(б) определения города компании из текста сайта."""
from __future__ import annotations

import re
from functools import lru_cache

# Города-миллионники и крупные центры — используются и как модификаторы поиска,
# и как словарь для детекта города на сайте. Порядок ~ по убыванию населения.
CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Красноярск", "Самара", "Уфа",
    "Ростов-на-Дону", "Краснодар", "Омск", "Воронеж", "Пермь",
    "Волгоград", "Саратов", "Тюмень", "Тольятти", "Барнаул",
    "Ижевск", "Ульяновск", "Иркутск", "Хабаровск", "Махачкала",
    "Ярославль", "Владивосток", "Томск", "Оренбург", "Кемерово",
    "Новокузнецк", "Рязань", "Астрахань", "Набережные Челны", "Пенза",
    "Киров", "Липецк", "Чебоксары", "Балашиха", "Калининград",
    "Тула", "Курск", "Сочи", "Ставрополь", "Улан-Удэ",
    "Тверь", "Магнитогорск", "Иваново", "Брянск", "Белгород",
    "Сургут", "Владимир", "Архангельск", "Чита", "Калуга",
    "Смоленск", "Волжский", "Якутск", "Саранск", "Череповец",
    "Курган", "Орёл", "Вологда", "Владикавказ", "Подольск",
    "Грозный", "Мурманск", "Тамбов", "Стерлитамак", "Петрозаводск",
    "Кострома", "Нижневартовск", "Новороссийск", "Йошкар-Ола", "Таганрог",
    "Комсомольск-на-Амуре", "Сыктывкар", "Нижнекамск", "Нальчик", "Шахты",
    "Дзержинск", "Орск", "Братск", "Ангарск", "Энгельс",
    "Благовещенск", "Старый Оскол", "Великий Новгород", "Псков", "Бийск",
    "Прокопьевск", "Балаково", "Армавир", "Рыбинск", "Северодвинск",
    "Абакан", "Норильск", "Сызрань", "Каменск-Уральский", "Уссурийск",
    "Назрань", "Волгодонск", "Новочеркасск", "Златоуст", "Электросталь",
    "Альметьевск", "Салават", "Миасс", "Находка", "Копейск",
    "Пятигорск", "Рубцовск", "Березники", "Коломна", "Майкоп",
    "Одинцово", "Хасавюрт", "Ковров", "Кисловодск", "Домодедово",
    "Нефтекамск", "Нефтеюганск", "Новочебоксарск", "Серпухов", "Щёлково",
]


def _norm(t: str) -> str:
    return t.lower().replace("ё", "е")


# падежные окончания названий: «в ПереславлЕ-ЗалесскОМ», «из МосквЫ», «в ЯрославлЕ»
_CITY_ENDINGS = ("ого", "ому", "ым", "ий", "ый", "ой", "ая", "ое", "ые", "ь", "а", "ы", "е", "о", "и", "й", "у", "ю")


def _stem(word: str) -> str:
    """Основа названия города: отрезаем окончание, если остаётся не короче 5 букв.

    Без этого «Переславль-Залесский» не находится в тексте «в Переславле-Залесском»,
    и город определяется по частоте упоминаний — а там побеждает Москва из шапки.
    """
    w = _norm(word)
    for end in _CITY_ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 5:
            return w[: -len(end)]
    return w


@lru_cache(maxsize=1024)
def _city_pattern(city: str) -> re.Pattern:
    """Название с учётом падежей: каждое слово — по основе, разделители свободные."""
    words = [w for w in re.split(r"[^а-яa-z0-9]+", _norm(city)) if w]
    if not words:
        return re.compile(r"(?!)")
    parts = [re.escape(_stem(w)) + r"[а-я]{0,4}" for w in words]
    return re.compile(r"(?<!\w)" + r"[\s\-]{0,3}".join(parts))


# латиница в поддоменах: pereslavl-zalesskii.saiding77.ru, yaroslavl.stroyportal.ru
_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
             "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
             "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
             "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "-": "", " ": ""}


def _latin(city: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in _norm(city))


def city_from_url(url: str, cities: list[str] | None = None) -> str:
    """Город из адреса сайта: поддомен или первый сегмент пути.

    Самый надёжный признак: если фирма завела `pereslavl-zalesskii.saiding77.ru`,
    это её город, что бы ни писали в шапке сайта. Сам домен (`yaroslavl-okna.ru`)
    засчитываем только при точном совпадении слова — иначе ловятся ложные.
    """
    if not url:
        return ""
    host, _, path = re.sub(r"^https?://", "", url.lower()).partition("/")
    parts = [x for x in host.split(".") if x and x != "www"]
    words = lambda s: [t for t in re.split(r"[^a-z0-9]+", s) if t]
    strong = words(" ".join(parts[:-2] + [x for x in path.split("/") if x][:1]))
    domain = words(" ".join(parts[-2:-1]))
    joined = "".join(strong)
    best = ""
    for city in (cities if cities is not None else CITIES):
        lat = _latin(city)
        if len(lat) < 4:
            continue
        common = 0
        while common < min(len(lat), len(joined)) and lat[common] == joined[common]:
            common += 1
        # точное слово засчитываем любому городу, а «по началу» — только длинным
        # названиям: pereslavlzalesskii ~ pereslavlzalesskij, но не rostov ~ rostovvelikii
        hit = (joined == lat or lat in strong or lat in domain
               or (len(lat) >= 7 and common >= 7 and common >= len(lat) - 3))
        if hit and len(lat) > len(_latin(best)):
            best = city
    return best


@lru_cache(maxsize=16)
def _norm_pool(cities: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(cities, key=len, reverse=True))


def detect_city(text: str, hint: str = "", cities: list[str] | None = None, url: str = "") -> str:
    """Город компании: сначала адрес сайта, потом город запроса, потом частота упоминаний.

    hint — город из поискового запроса. На федеральных сайтах упоминается куча городов,
    и самый частый там ≠ город компании, поэтому частота — последний вариант.
    """
    pool = _norm_pool(tuple(cities)) if cities is not None else _norm_pool(tuple(CITIES))
    from_url = city_from_url(url, list(pool)) if url else ""
    if from_url:
        return from_url
    t = _norm(text)
    if hint and _city_pattern(hint).search(t):
        return hint
    counts: dict[str, int] = {}
    for city in pool:
        c = len(_city_pattern(city).findall(t))
        if c:
            counts[city] = c
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    return hint
