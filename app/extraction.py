"""Извлечение значений полей из документов — детерминированные правила + реестр полей.

Ключевая идея расширяемости: каждое поле (сумма, срок поставки, ...) описывается
одной декларацией FieldSpec в FIELD_REGISTRY. Добавление нового поля (например,
«ставка неустойки») = добавить FieldSpec; UI и вопросы подхватят его автоматически,
потому что колонки таблицы и сериализация строятся из реестра.

Все правила ищут по нормализованному тексту (см. textutils.norm), но цитаты и
смещения берутся из оригинала: text[char_start:char_end] == quote (проверяется
в тестах). Значение извлекается только из устойчивых формулировок шаблона;
если формулировка не распознана — поле получает status='no_data', а не выдуманное
значение.

Границы применимости правил описаны в README («Записка», п. про LLM): при
появлении разнородных шаблонов к реестру можно подключить LLM-экстрактор как
генератор гипотез с обязательной substring-верификацией цитаты — контракты
моделей (Evidence/FieldValue) при этом не меняются.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from . import textutils as tu
from .models import Evidence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Реестр полей
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternRule:
    """Позитивное правило: regex (по нормализованному тексту) + группа со значением."""
    regex: str
    group: int = 1


@dataclass(frozen=True)
class NegativeRule:
    """Маркер явного отсутствия значения + причина для пользователя."""
    regex: str
    reason: str


@dataclass(frozen=True)
class ProposalRule:
    """Правило извлечения НЕСОГЛАСОВАННОГО предложения (проект/письмо)."""
    regex: str
    group: int = 1


@dataclass(frozen=True)
class FieldSpec:
    key: str
    title: str
    unit: str
    kind: str                              # 'money' | 'date'
    positive: tuple[PatternRule, ...]      # для текста договора / новой редакции пункта
    negative: tuple[NegativeRule, ...]     # явные маркеры «значения нет»
    proposal_rules: tuple[ProposalRule, ...]  # для проектов и писем
    parse: Callable[[str], object]         # строка захвата -> значение или None

    def to_meta(self) -> dict:
        return {"key": self.key, "title": self.title, "unit": self.unit, "kind": self.kind}


# --- Сумма договора ---------------------------------------------------------
# Формулировки шаблона: «...стоимость ... составляет N (пропись) рублей...».
# Якорь «стоимость...составляет...руб» отличает согласованную сумму от
# посторонних чисел (лимиты бюджета, авансы «входит в общую стоимость», оплату).
_AMOUNT_POSITIVE = (
    PatternRule(
        r"стоимость[^.\n]{0,120}?составляет\s+(\d[\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)"
        r"\s*(?:\([^)]{0,150}\)\s*)?руб"
    ),
)

_AMOUNT_NEGATIVE = (
    NegativeRule(
        r"стоимость[^.\n]{0,100}?определяется\s+спецификацией",
        "Стоимость определяется спецификацией (приложением), которая не входит в комплект документов",
    ),
    NegativeRule(
        r"стоимост\w*[^.\n]{0,80}?не\s+указан\w*",
        "Числовое значение стоимости не указано в тексте документа",
    ),
)

_AMOUNT_PROPOSALS = (
    # Проект допсоглашения: «Предлагается изменить общую стоимость поставки на N рублей»
    ProposalRule(
        r"предлагается\s+изменить\s+общую\s+стоимость[^.\n]{0,80}?на\s+"
        r"(\d[\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*(?:\([^)]{0,150}\)\s*)?руб"
    ),
    # Письмо: «предлагаем изменить/установить стоимость ... на N рублей»
    ProposalRule(
        r"предлага\w+\s+(?:изменить|установить|снизить|повысить)\s+(?:общую\s+)?стоимост\w*"
        r"[^.\n]{0,80}?на\s+(\d[\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*(?:\([^)]{0,150}\)\s*)?руб"
    ),
)

AMOUNT_FIELD = FieldSpec(
    key="amount",
    title="Сумма",
    unit="₽",
    kind="money",
    positive=_AMOUNT_POSITIVE,
    negative=_AMOUNT_NEGATIVE,
    proposal_rules=_AMOUNT_PROPOSALS,
    parse=tu.parse_amount,
)

# --- Срок поставки ----------------------------------------------------------
# Якорь: корень «постав» + «не позднее/до» + дата — в пределах одной строки и не
# через точку. Так дата поставки отличается от дат оплаты («Оплата ... не позднее
# 10.04.2026. Этот срок является сроком оплаты») и приёмки.
_DELIVERY_POSITIVE = (
    PatternRule(
        r"постав[\w]*[^.\n]{0,120}?(?:не\s+позднее|до)\s+(\d{1,2}\.\d{1,2}\.\d{4})"
    ),
)

_DELIVERY_NEGATIVE = (
    NegativeRule(
        r"срок\s+поставки[^.\n]{0,80}?не\s+(?:установлен|указан|определен)",
        "Срок поставки не установлен в тексте документа",
    ),
)

_DELIVERY_PROPOSALS = (
    # Проект: «Предлагается изменить срок поставки на ДАТУ»
    ProposalRule(r"предлагается\s+изменить\s+срок\s+поставки\s+на\s+(\d{1,2}\.\d{1,2}\.\d{4})"),
    # Письмо: «предлагаем перенести срок поставки с ДАТЫ на ДАТУ» (берём новую дату)
    ProposalRule(
        r"предлага\w+\s+перенести\s+срок\s+поставки\s+с\s+(\d{1,2}\.\d{1,2}\.\d{4})"
        r"\s+на\s+(\d{1,2}\.\d{1,2}\.\d{4})",
        group=2,
    ),
    # Письмо/проект, общий случай: «предлагаем ... срок поставки ... на ДАТУ»
    ProposalRule(
        r"предлага\w+[^.\n]{0,80}?срок\s+поставки[^.\n]{0,40}?на\s+(\d{1,2}\.\d{1,2}\.\d{4})"
    ),
)

DELIVERY_FIELD = FieldSpec(
    key="delivery_date",
    title="Срок поставки",
    unit="",
    kind="date",
    positive=_DELIVERY_POSITIVE,
    negative=_DELIVERY_NEGATIVE,
    proposal_rules=_DELIVERY_PROPOSALS,
    parse=tu.parse_ru_date,
)

# Порядок реестра = порядок колонок в таблице.
# Пример расширения (задание со встречи): добавить FieldSpec('penalty_rate', ...)
# с паттерном r"неустойка[^.\n]{0,80}?составляет\s+(\d+(?:[.,]\d+)?)\s*%" —
# остальная система (таблица, доказательства, вопросы, тесты) подхватит поле сама.
FIELD_REGISTRY: tuple[FieldSpec, ...] = (AMOUNT_FIELD, DELIVERY_FIELD)


def get_field_spec(key: str) -> Optional[FieldSpec]:
    for spec in FIELD_REGISTRY:
        if spec.key == key:
            return spec
    return None


# ---------------------------------------------------------------------------
# Вспомогательное: значение + отображение
# ---------------------------------------------------------------------------

def parse_and_display(spec: FieldSpec, raw: str) -> tuple[Optional[object], str]:
    value = spec.parse(raw)
    if value is None:
        return None, ""
    if spec.kind == "money":
        return value, f"{tu.format_amount(value)} {spec.unit}".strip()
    if spec.kind == "date":
        return tu.iso(value), tu.format_ru_date(value)
    return str(value), str(value)


def make_evidence(text: str, file_name: str, doc_type: str,
                  match_start: int, match_end: int,
                  clause: Optional[str] = None, label: Optional[str] = None) -> Evidence:
    """Цитата = целая строка (абзац) исходного текста, содержащая совпадение.

    Гарантии: quote == text[char_start:char_end] (точная подстрока), line —
    номер строки для перехода в просмотрщике документа.
    """
    ls, le, line_no = tu.expand_to_line(text, match_start, match_end)
    return Evidence(
        file_name=file_name,
        doc_type=doc_type,
        quote=text[ls:le],
        char_start=ls,
        char_end=le,
        line=line_no,
        clause=clause,
        label=label,
    )


def _clause_at(text: str, pos: int) -> Optional[str]:
    ls = text.rfind("\n", 0, pos) + 1
    le = text.find("\n", pos)
    if le == -1:
        le = len(text)
    return tu.clause_of_line(text[ls:le])


# ---------------------------------------------------------------------------
# Базовые поля документа (договор или новая редакция пункта соглашения)
# ---------------------------------------------------------------------------

@dataclass
class BaseFinding:
    spec_key: str
    status: str                       # 'ok' | 'no_data'
    value: object = None
    display: str = ""
    evidence: Optional[Evidence] = None
    no_data_reason: Optional[str] = None
    no_data_evidence: Optional[Evidence] = None


def extract_fields_from_text(
    text: str,
    file_name: str,
    doc_type: str,
    label_prefix: str = "",
) -> dict[str, BaseFinding]:
    """Извлечь значения всех полей реестра из фрагмента текста.

    Используется и для текста договора, и для «новой редакции пункта» из
    подписанного допсоглашения (фрагмент в кавычках после «изложить в редакции»).
    Логика: сначала позитивные правила (первое совпавшее побеждает); если значения
    нет — негативные маркеры дают причину «нет данных»; иначе причина общая.
    """
    log.debug("extraction: извлечение полей из «%s» (тип=%s)", file_name, doc_type)
    ntext = tu.norm(text)
    findings: dict[str, BaseFinding] = {}
    for spec in FIELD_REGISTRY:
        found: Optional[BaseFinding] = None
        for rule in spec.positive:
            for m in re.finditer(rule.regex, ntext):
                value, display = parse_and_display(spec, m.group(rule.group))
                if value is None:
                    continue  # совпадение есть, но значение не парсится — ищем дальше
                clause = _clause_at(text, m.start())
                found = BaseFinding(
                    spec_key=spec.key,
                    status="ok",
                    value=value,
                    display=display,
                    evidence=make_evidence(
                        text, file_name, doc_type, m.start(), m.end(),
                        clause=clause,
                        label=(label_prefix + f"{spec.title.lower()}: исходная формулировка") or None,
                    ),
                )
                break
            if found:
                break
        if found is None:
            # Явный маркер отсутствия — показываем причину с цитатой.
            for neg in spec.negative:
                m = re.search(neg.regex, ntext)
                if m:
                    found = BaseFinding(
                        spec_key=spec.key,
                        status="no_data",
                        no_data_reason=neg.reason,
                        no_data_evidence=make_evidence(
                            text, file_name, doc_type, m.start(), m.end(),
                            clause=_clause_at(text, m.start()),
                            label="обоснование отсутствия значения",
                        ),
                    )
                    break
        if found is None:
            found = BaseFinding(
                spec_key=spec.key,
                status="no_data",
                no_data_reason="Значение не найдено в тексте (известные формулировки не встретились)",
            )
        log.debug("extraction: «%s» поле %s -> status=%s, value=%s",
                  file_name, spec.key, found.status,
                  found.display if found.status == "ok" else found.no_data_reason)
        findings[spec.key] = found
    return findings


# ---------------------------------------------------------------------------
# Новая редакция пунктов в допсоглашениях
# ---------------------------------------------------------------------------

# «Пункт 2.1 договора изложить в следующей редакции: «...»» (кавычки «» или "")
_REPLACEMENT_RE = re.compile(
    r"пункт\s+(\d+(?:\.\d+)?)\s*(?:договора)?\s*изложить\s+в\s+следующей\s+редакции"
    r"\s*[:—\-]?\s*(?:«([^»]+)»|\"([^\"]+)\")"
)


@dataclass
class Replacement:
    """Распознанное изменение пункта договора из допсоглашения."""
    clause_ref: str                        # пункт договора, который меняется ('2.1')
    inner_text: str                        # новая редакция пункта
    fields: dict[str, BaseFinding]         # какие поля реестра найдены в новой редакции
    evidence: Evidence                     # цитата = весь пункт соглашения


def extract_replacements(text: str, file_name: str, doc_type: str) -> list[Replacement]:
    ntext = tu.norm(text)
    out: list[Replacement] = []
    for m in _REPLACEMENT_RE.finditer(ntext):
        clause_ref = m.group(1)
        inner_group = 2 if m.group(2) is not None else 3
        inner_start, inner_end = m.start(inner_group), m.end(inner_group)
        # КРИТИЧНО: извлекаем из среза ОРИГИНАЛЬНОГО текста, чтобы цитаты
        # сохраняли регистр, а смещения — пересчитываем на уровень документа.
        inner_orig = text[inner_start:inner_end]
        log.debug("extraction: замена п. %s в «%s»", clause_ref, file_name)
        findings = extract_fields_from_text(
            inner_orig, file_name, doc_type,
            label_prefix=f"новая редакция п. {clause_ref} договора: ",
        )
        parsed: dict[str, BaseFinding] = {}
        for key, f in findings.items():
            if f.status != "ok":
                continue
            if f.evidence is not None:
                # Пересобираем доказательство в координатах всего документа:
                # цитата = строка пункта соглашения, offsets валидны для text.
                f.evidence = make_evidence(
                    text, file_name, doc_type,
                    inner_start + f.evidence.char_start,
                    inner_start + f.evidence.char_end,
                    clause=_clause_at(text, m.start()),
                    label=f.evidence.label,
                )
            parsed[key] = f
        out.append(Replacement(
            clause_ref=clause_ref,
            inner_text=inner_orig,
            fields=parsed,
            evidence=make_evidence(
                text, file_name, doc_type, m.start(), m.end(),
                clause=_clause_at(text, m.start()),
                label=f"изменение п. {clause_ref} договора",
            ),
        ))
    log.info("extraction: «%s» — найдено %d замен(ы) пунктов договора", file_name, len(out))
    return out


# ---------------------------------------------------------------------------
# Предложения (проекты и письма) — несогласованные изменения
# ---------------------------------------------------------------------------

def extract_proposal_findings(
    text: str,
    file_name: str,
    doc_type: str,
) -> list[tuple[FieldSpec, object, str, Evidence]]:
    """Найти в проекте/письме предложения об изменении полей реестра.

    Возвращает список (spec, value, display, evidence). Дедупликация и привязка
    к договору выполняются на уровне resolution.
    """
    ntext = tu.norm(text)
    out: list[tuple[FieldSpec, object, str, Evidence]] = []
    seen: set[tuple[str, str]] = set()
    for spec in FIELD_REGISTRY:
        for rule in spec.proposal_rules:
            for m in re.finditer(rule.regex, ntext):
                value, display = parse_and_display(spec, m.group(rule.group))
                if value is None:
                    continue
                dedupe_key = (spec.key, str(value))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                evidence = make_evidence(
                    text, file_name, doc_type, m.start(), m.end(),
                    clause=_clause_at(text, m.start()),
                    label="несогласованное предложение об изменении",
                )
                out.append((spec, value, display, evidence))
    log.info("extraction: «%s» — найдено %d несогласованных предложени(й/я)", file_name, len(out))
    return out
