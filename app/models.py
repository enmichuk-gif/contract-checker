"""Модели данных прототипа.

Все сущности сериализуются в plain dict (JSON-safe): даты хранятся как ISO-строки
или DD.MM.YYYY в display-полях, значения — int/float/str/None. Никаких
«выдуманных» значений: отсутствие данных — это status='no_data' + reason,
сомнительная ситуация — status='needs_review' + reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Evidence:
    """Проверяемое основание значения: файл + точная цитата + позиция в тексте."""
    file_name: str
    doc_type: str
    quote: str              # ТОЧНАЯ подстрока текста файла: text[char_start:char_end]
    char_start: int
    char_end: int
    line: int               # номер строки (1-based) — для перехода в тексте
    clause: Optional[str] = None   # пункт документа, например '2.1'
    label: Optional[str] = None    # человекочитаемое пояснение, что доказывает цитата

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValueStep:
    """Шаг истории значения: исходное из договора или изменение допсоглашением."""
    source_file: str
    doc_type: str
    clause: Optional[str]
    value: Any                     # int/float для сумм, ISO-строка для дат, None
    display: str
    effective: Optional[str]       # ISO-дата вступления шага в силу
    note: str                      # 'исходное значение по договору', 'изменение ...'
    evidence: Evidence

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FieldValue:
    """Итоговое значение поля договора на дату проверки."""
    key: str                       # 'amount' | 'delivery_date' | ...
    title: str
    unit: str
    status: str                    # 'ok' | 'changed' | 'no_data' | 'needs_review'
    value: Any                     # сырое значение (для сравнений/фильтров) или None
    display: str                   # '1 380 000 ₽' | '30.04.2026' | 'нет данных'
    history: list                  # list[ValueStep] — цепочка оснований значения
    changed_by: Optional[str]      # файл допсоглашения, изменившего значение
    no_data_reason: Optional[str]
    no_data_evidence: Optional[Evidence]
    pending: list                  # подписанные, но ещё не вступившие в силу изменения
    review_reason: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Proposal:
    """НЕСОГЛАСОВАННОЕ предложение об изменении условий (проект/письмо)."""
    contract_no: Optional[str]
    field_key: str
    field_title: str
    proposed_value: Any
    proposed_display: str
    current_display: Optional[str]  # действующее значение — заполняется при разрешении
    source_file: str
    doc_type: str                   # 'amendment_draft' | 'amendment_unsigned' | 'email'
    status_note: str                # почему не применяется
    evidence: Evidence

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentMeta:
    """Реквизиты документа: тип, номер, статус подписания, даты, связь с договором."""
    file_name: str
    doc_type: str        # contract | amendment_signed | amendment_draft |
                         # amendment_unsigned | amendment_unknown | email | unknown
    doc_type_title: str  # человекочитаемое название типа
    title: str           # первая содержательная строка
    number: Optional[str]         # собственный номер (договора/соглашения)
    contract_no: Optional[str]    # к какому договору относится (для не-договоров)
    doc_date: Optional[str]       # ISO
    signed: Optional[bool]        # None = статус не определён
    status_text: Optional[str]
    effective_date: Optional[str]  # ISO, для допсоглашений
    effective_date_assumed: bool = False  # дата вступления в силу принята = дате подписания (допущение)
    encoding: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Warning:
    level: str             # 'warn' | 'info'
    text: str
    file_name: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ContractView:
    """Договор + разрешённые значения полей + связанные документы."""
    number: str
    supplier: Optional[str]
    buyer: Optional[str]
    file_name: str
    doc_date: Optional[str]
    signed: Optional[bool]
    fields: dict                      # key -> FieldValue (порядок = порядок реестра полей)
    documents: list                   # list[DocumentMeta] — все связанные документы
    proposals: list                   # list[Proposal] — несогласованные изменения
    needs_review: bool
    review_reasons: list

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisResult:
    as_of: str                        # ISO дата проверки
    fields_meta: list                 # [{key,title,unit}] — порядок колонок таблицы
    contracts: list                   # list[ContractView]
    proposals: list                   # все несогласованные предложения (сводно)
    documents: list                   # реестр всех загруженных документов
    unlinked_documents: list          # документы, которые не удалось связать
    warnings: list                    # list[Warning]
    questions_meta: list = field(default_factory=list)
    session_id: Optional[str] = None
    # Расшифрованные тексты документов: {file_name: text}. Нужны фронтенду для
    # просмотрщика с подсветкой цитат (offsets доказательств валидны именно для
    # этих текстов — сервер уже учёл кодировку файла).
    document_texts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
