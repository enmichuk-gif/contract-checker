"""Утилиты работы с текстом: нормализация, даты, суммы, границы цитат, кодировки.

Важный инвариант проекта: все regex-поиски выполняются по НОРМАЛИЗОВАННОЙ копии
текста (lower + ё->е), которая имеет ту же длину, что и оригинал. Поэтому
символьные смещения (offsets), найденные в нормализованном тексте, валидны для
оригинала — цитата text[start:end] всегда является точной подстрокой исходного
документа. Это гарантирует проверяемость каждого значения.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional


def norm(text: str) -> str:
    """Нормализация для поиска: регистр и ё. Длина строки не меняется."""
    return text.lower().replace("ё", "е")


# ---------------------------------------------------------------------------
# Даты
# ---------------------------------------------------------------------------

_RU_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def parse_ru_date(s: str) -> Optional[date]:
    """'15.04.2026' -> date. Недействительные даты (31.02) -> None."""
    m = _RU_DATE_RE.fullmatch(s.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_any_date(s: Optional[str]) -> Optional[date]:
    """Принимает ISO (2026-03-10) и русский (10.03.2026) форматы."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def format_ru_date(d) -> str:
    """date | 'YYYY-MM-DD' -> 'DD.MM.YYYY'."""
    if d is None:
        return ""
    if isinstance(d, str):
        d = parse_any_date(d)
        if d is None:
            return ""
    return d.strftime("%d.%m.%Y")


def iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


# ---------------------------------------------------------------------------
# Суммы
# ---------------------------------------------------------------------------

def parse_amount(s: str) -> Optional[float]:
    """'1 200 000', '10 500,50', '1\u00a0380\u00a0000' -> число. Мусор -> None."""
    cleaned = re.sub(r"[\s\u00a0\u2009\u202f]", "", s)
    cleaned = cleaned.replace(",", ".")
    if not cleaned or not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    value = float(cleaned)
    return int(value) if value.is_integer() else value


def format_amount(v) -> str:
    """1380000 -> '1 380 000' (разделитель — неразрывный пробел)."""
    if v is None:
        return ""
    if isinstance(v, float) and not v.is_integer():
        s = f"{v:,.2f}"
    else:
        s = f"{int(v):,d}"
    return s.replace(",", "\u00a0")


# ---------------------------------------------------------------------------
# Границы цитат
# ---------------------------------------------------------------------------

def expand_to_line(text: str, start: int, end: int) -> tuple[int, int, int]:
    """Расширить диапазон [start, end) до целой строки (абзаца) текста.

    Возвращает (line_start, line_end, line_number_1based). Цитата — это
    text[line_start:line_end], гарантированно точная подстрока исходника.
    """
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    raw = text[ls:le]
    lead = len(raw) - len(raw.lstrip())
    trail = len(raw) - len(raw.rstrip())
    line_no = text.count("\n", 0, start) + 1
    return ls + lead, le - trail, line_no


# ---------------------------------------------------------------------------
# Кодировки
# ---------------------------------------------------------------------------

def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Декодировать байты файла. Возвращает (текст, использованная кодировка).

    Цепочка: utf-8 -> windows-1251 (частый случай для русских TXT) -> latin-1
    с заменой проблемных символов. Информация о кодировке показывается
    пользователю, если файл был не в UTF-8 (прозрачность, а не магия).
    """
    for enc in ("utf-8", "windows-1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1 (с потерями)"


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------

CLAUSE_LINE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]\s")


def clause_of_line(line_text: str) -> Optional[str]:
    """Номер пункта по началу строки: '2.1. Общая стоимость...' -> '2.1'."""
    m = CLAUSE_LINE_RE.match(line_text)
    return m.group(1) if m else None
