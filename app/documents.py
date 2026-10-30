"""Классификация документов и извлечение реквизитов.

Типы документов:
  contract           — договор поставки;
  amendment_signed   — подписанное допсоглашение (единственный источник изменений,
                       если оно вступило в силу к дате проверки);
  amendment_draft    — проект допсоглашения (не подписан ни одной стороной);
  amendment_unsigned — допсоглашение без подтверждённого подписания;
  amendment_unknown  — допсоглашение, статус подписания которого не определён;
  email              — письмо (сведения для проверки, не изменяет условия);
  unknown            — не распознан.

Приоритет документа НЕ определяется именем файла или датой загрузки — только
содержанием: заголовком и строкой статуса. Дата в имени файла игнорируется.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from . import textutils as tu
from .models import DocumentMeta

log = logging.getLogger(__name__)

DOC_TYPE_TITLES = {
    "contract": "Договор",
    "amendment_signed": "Допсоглашение (подписано)",
    "amendment_draft": "Проект допсоглашения",
    "amendment_unsigned": "Допсоглашение (не подписано)",
    "amendment_unknown": "Допсоглашение (статус не определён)",
    "email": "Письмо",
    "unknown": "Не распознан",
}

# Номер вида STN-101 / ПР-45/26 / 123 — латиница/кириллица/цифры/дефис/слэш.
_NUM_CLASS = r"[a-zа-я0-9][a-zа-я0-9\-/]{1,}"

_CONTRACT_NO_RE = re.compile(rf"договор(?:\s+поставки)?\s*№\s*({_NUM_CLASS})")
# Основа «дополнительн* соглашен*» — заголовок может стоять в любом падеже
# («ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ», «ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ»).
_AMENDMENT_STEM_RE = re.compile(r"дополнительн\w+\s+соглашен\w+")
_AMENDMENT_NO_RE = re.compile(rf"дополнительн\w+\s+соглашен\w+\s*№\s*(\d+)")
_AMENDMENT_TO_CONTRACT_RE = re.compile(rf"к\s+договору\s*(?:№\s*)?({_NUM_CLASS})")
_LETTER_CONTRACT_RE = re.compile(rf"договор(?:у|а|е)?\s*№?\s*({_NUM_CLASS})")
_DOC_DATE_RE = re.compile(r"дата(?:\s+проекта)?\s*[:—\-]\s*(\d{1,2}\.\d{1,2}\.\d{4})")
_EFFECTIVE_RE = re.compile(r"вступает\s+в\s+силу\s+(\d{1,2}\.\d{1,2}\.\d{4})")
_STATUS_LINE_RE = re.compile(r"статус\s*[:—\-]\s*([^\n]+)")
_SIGNED_DATE_RE = re.compile(r"подписан\w*\s+обеими\s+сторонами\s+(\d{1,2}\.\d{1,2}\.\d{4})")
_SUPPLIER_RE = re.compile(r"поставщик\s*[:—\-]\s*([^.\n]+)")
_BUYER_RE = re.compile(r"покупатель\s*[:—\-]\s*([^.\n]+)")
_EMAIL_FROM_RE = re.compile(r"от\s*[:—\-]\s*([^.\n]+)")
_PROJECT_RE = re.compile(r"проект\w*")


def _first_lines(text: str, n: int = 5) -> str:
    lines = [ln for ln in tu.norm(text).splitlines() if ln.strip()]
    return "\n".join(lines[:n])


def classify(text: str) -> str:
    """Определить тип документа по заголовку (первые непустые строки).

    Приоритет заголовка важен: письмо может УПОМИНАТЬ «дополнительное соглашение»
    в теле («просим оформить дополнительное соглашение»), но от этого оно не
    становится допсоглашением. Если заголовок не дал ответа, маркеры ищутся
    в первых 600 символах.
    """
    head = _first_lines(text)
    fallback = tu.norm(text[:600])
    for zone in (head, fallback):
        if _AMENDMENT_STEM_RE.search(zone):
            if _PROJECT_RE.search(zone):
                return "amendment_draft"
            signed, _status, _d = parse_signature(text)
            if signed is True:
                return "amendment_signed"
            if signed is False:
                return "amendment_unsigned"
            return "amendment_unknown"
        if "письмо" in zone or ("от:" in zone and "кому:" in zone):
            return "email"
        if re.search(r"договор\w*\s+поставки", zone) or _CONTRACT_NO_RE.search(zone):
            return "contract"
        # Слово «договор» само по себе договором не делает (его может упомянуть
        # любой документ) — без явных маркеров тип не определяется.
    return "unknown"


def parse_signature(text: str) -> tuple[Optional[bool], Optional[str], Optional[str]]:
    """(signed, status_text, signed_date_iso).

    signed: True — подписан; False — явно не подписан / проект;
    None — статус не определён (требуется проверка человеком).
    Отрицание («не подписан») проверяется раньше положительной формы.
    """
    ntext = tu.norm(text)
    status_text = None
    m = _STATUS_LINE_RE.search(ntext)
    status_zone = m.group(1) if m else ntext[:600]
    if m:
        ls = text.rfind("\n", 0, m.start(1)) + 1
        le = text.find("\n", m.end(1))
        status_text = text[ls:(le if le != -1 else len(text))].strip()
    if ("не подписан" in status_zone) or ("проект" in status_zone and "на согласовании" in status_zone):
        return False, status_text, None
    if "подписан" in status_zone:
        d = None
        dm = _SIGNED_DATE_RE.search(ntext)
        if dm:
            d = tu.iso(tu.parse_ru_date(dm.group(1)))
        return True, status_text, d
    return None, status_text, None


def _upper(m: Optional[re.Match], group: int = 1) -> Optional[str]:
    if not m:
        return None
    return m.group(group).upper().rstrip(".-,;")


def parse_document(file_name: str, text: str, encoding: str) -> DocumentMeta:
    """Собрать реквизиты документа: тип, номера, даты, статус, стороны."""
    ntext = tu.norm(text)
    doc_type = classify(text)
    log.info("documents: «%s» — классифицирован как %s", file_name, doc_type)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), file_name)

    number: Optional[str] = None
    contract_no: Optional[str] = None

    if doc_type == "contract":
        number = _upper(_CONTRACT_NO_RE.search(ntext[:400]))
        contract_no = number
    elif doc_type.startswith("amendment"):
        number = _upper(_AMENDMENT_NO_RE.search(ntext[:400]))
        contract_no = _upper(_AMENDMENT_TO_CONTRACT_RE.search(ntext[:400]))
    elif doc_type == "email":
        contract_no = _upper(_LETTER_CONTRACT_RE.search(ntext))

    doc_date = None
    dm = _DOC_DATE_RE.search(ntext[:400])
    if dm:
        doc_date = tu.iso(tu.parse_ru_date(dm.group(1)))

    signed, status_text, signed_date = parse_signature(text)
    log.debug("documents: «%s» — signed=%s, status_text=%s, signed_date=%s",
              file_name, signed, status_text, signed_date)
    # Письмо по природе не является подписанным документом об изменении условий.
    if doc_type == "email":
        signed = None
        status_text = status_text or "письмо — предложение/уведомление, не изменяет условия договора"

    effective_date = None
    effective_date_assumed = False
    if doc_type.startswith("amendment"):
        em = _EFFECTIVE_RE.search(ntext)
        if em:
            effective_date = tu.iso(tu.parse_ru_date(em.group(1)))
        if signed is True and effective_date is None:
            # Подписано, но дата вступления в силу явно не указана. По умолчанию
            # соглашение действует с момента подписания (аналог п.1 ст.425 ГК РФ);
            # допущение фиксируется флагом и показывается пользователю.
            effective_date = signed_date or doc_date
            effective_date_assumed = effective_date is not None

    result = DocumentMeta(
        file_name=file_name,
        doc_type=doc_type,
        doc_type_title=DOC_TYPE_TITLES.get(doc_type, doc_type),
        title=first_line[:160],
        number=number,
        contract_no=contract_no,
        doc_date=doc_date,
        signed=signed,
        status_text=status_text,
        effective_date=effective_date,
        effective_date_assumed=effective_date_assumed,
        encoding=encoding,
    )
    log.debug("documents: «%s» — номер=%s, договор=%s, дата=%s, effective=%s",
              file_name, number, contract_no, doc_date, effective_date)
    return result


def extract_party(text: str, which: str) -> Optional[str]:
    """Поставщик/покупатель из текста договора; для письма — отправитель.

    Поиск по нормализованному тексту, значение вырезается из ОРИГИНАЛА
    (нормализация сохраняет длину и offsets) — чтобы сохранить регистр имени.
    """
    ntext = tu.norm(text)
    regex = _SUPPLIER_RE if which == "supplier" else _BUYER_RE
    m = regex.search(ntext)
    if not m and which == "supplier":
        m = _EMAIL_FROM_RE.search(ntext)  # письмо: «От: ООО ...»
    if not m:
        return None
    value = text[m.start(1):m.end(1)].strip().rstrip(".-,;").strip()
    return value or None
