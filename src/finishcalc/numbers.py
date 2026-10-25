"""Разбор номеров помещений, русских чисел и заголовков этажей."""

import re

# Латинские буквы, неотличимые на глаз от кириллических.
_LATIN_TO_CYRILLIC = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
_PLAIN_NUMBER = re.compile(r"\d+(\.\d+)?")
_FLOOR_NUMBER = re.compile(r"(\d+)-?го")


def normalize_room_number(s: str) -> str:
    """Номер помещения к единому виду: верхний регистр, без пробелов,
    латиница-двойники заменены кириллицей; «-» (нет номера) → пустая строка."""
    result = re.sub(r"\s+", "", s.upper()).translate(_LATIN_TO_CYRILLIC)
    return "" if result == "-" else result


def parse_ru_float(s: str) -> float | None:
    """Число в русской записи («1 848,8») → float; всё остальное → None."""
    text = re.sub(r"\s+", "", s).replace(",", ".")
    if _PLAIN_NUMBER.fullmatch(text):
        return float(text)
    return None


def floor_key(title: str) -> str | None:
    """Ключ этажа по заголовку экспликации: «1-го этажа» → "1",
    «подвального этажа» → "подвал", иначе None."""
    text = title.lower()
    match = _FLOOR_NUMBER.search(text)
    if match:
        return match.group(1)
    if "подвал" in text:
        return "подвал"
    return None
