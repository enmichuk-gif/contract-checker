"""Машина ответов на вопросы закупщика.

Три вопроса закупщика реализованы как параметризуемые шаблоны:
номер договора и пороговая дата — ПАРАМЕТРЫ, значения берутся из результата
анализа. Ответы не зашиты: любой ответ строится из разрешённых значений
(Resolution) и содержит проверяемые основания (файл, пункт, цитата, позиция).

  В1. Какой срок поставки действует по договору X на дату проверки?
  В2. Какова сумма договора X?
  В3. По каким договорам поставка запланирована раньше даты D и есть ли
      несогласованные изменения срока?
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from . import textutils as tu
from .models import AnalysisResult, ContractView, FieldValue

log = logging.getLogger(__name__)

QUESTION_TEMPLATES = {
    "delivery_as_of": "Какой срок поставки действует по договору на дату проверки?",
    "amount": "Какова сумма договора?",
    "delivery_before": "По каким договорам поставка запланирована раньше указанной даты и есть ли несогласованные изменения срока?",
}


# ---------------------------------------------------------------------------
# Метаданные вопросов (для UI): параметры + значения по умолчанию
# ---------------------------------------------------------------------------

def _nth_contract(result: AnalysisResult, n: int) -> Optional[str]:
    if n < len(result.contracts):
        return result.contracts[n].number
    return result.contracts[0].number if result.contracts else None


def _default_contract(result: AnalysisResult, preferred: str, n: int) -> Optional[str]:
    """Номер из текста вопроса, если такой договор загружен, иначе n-й по счёту."""
    numbers = [c.number for c in result.contracts]
    if preferred in numbers:
        return preferred
    return _nth_contract(result, n)


def _next_month_start(as_of: date) -> date:
    """Первое число месяца, следующего за датой проверки (порог по умолчанию для В3)."""
    return (as_of.replace(day=1) + timedelta(days=32)).replace(day=1)


def build_questions_meta(result: AnalysisResult) -> list[dict]:
    as_of = tu.parse_any_date(result.as_of) or date.today()
    options = [{"value": c.number, "label": c.number} for c in result.contracts]
    return [
        {
            "id": "q1",
            "template": "delivery_as_of",
            "title": QUESTION_TEMPLATES["delivery_as_of"],
            "params": [{
                "name": "contract", "type": "contract", "label": "Договор",
                "options": options,
                "default": _default_contract(result, "STN-101", 0),
            }],
        },
        {
            "id": "q2",
            "template": "amount",
            "title": QUESTION_TEMPLATES["amount"],
            "params": [{
                "name": "contract", "type": "contract", "label": "Договор",
                "options": options,
                "default": _default_contract(result, "STN-102", 1),
            }],
        },
        {
            "id": "q3",
            "template": "delivery_before",
            "title": QUESTION_TEMPLATES["delivery_before"],
            "params": [{
                "name": "date", "type": "date", "label": "Поставка раньше",
                "default": _next_month_start(as_of).isoformat(),
            }],
        },
    ]


# ---------------------------------------------------------------------------
# Ответы
# ---------------------------------------------------------------------------

def _find_contract(result: AnalysisResult, number: str) -> Optional[ContractView]:
    for c in result.contracts:
        if c.number == number:
            return c
    return None


def _field_answer(result: AnalysisResult, contract_no: Optional[str], field_key: str,
                  field_title: str) -> dict:
    """Общий конструктор ответа «значение поля договора на дату проверки»."""
    as_of = tu.format_ru_date(result.as_of)
    if not contract_no:
        log.warning("question: не указан номер договора")
        return {"status": "error", "answer": "Не указан номер договора.", "field_key": field_key}
    contract = _find_contract(result, contract_no)
    if contract is None:
        log.warning("question: договор «%s» не найден среди загруженных", contract_no)
        return {
            "status": "error",
            "answer": f"Договор «{contract_no}» не найден среди загруженных документов.",
            "field_key": field_key,
        }
    fv: FieldValue = contract.fields[field_key]
    notes: list[str] = []
    if fv.status == "changed":
        notes.append(f"Значение изменено документом «{fv.changed_by}» (подписанное допсоглашение).")
    if fv.status == "no_data":
        answer = (f"По договору {contract_no} на {as_of}: {field_title.lower()} — нет данных. "
                  f"{fv.no_data_reason or ''}".strip())
    elif fv.status == "needs_review":
        answer = (f"По договору {contract_no} на {as_of}: {field_title.lower()} — "
                  f"{fv.display}, НО требуется проверка: {fv.review_reason}")
    else:
        answer = f"По договору {contract_no} на {as_of} действует {field_title.lower()}: {fv.display}."
    for step in fv.pending:
        notes.append(f"{step.note} («{step.source_file}», новое значение {step.display}).")
    if contract.needs_review and fv.status != "needs_review":
        for reason in contract.review_reasons:
            notes.append(f"Внимание: {reason}.")
    related_proposals = [p.to_dict() for p in contract.proposals if p.field_key == field_key]
    for p in contract.proposals:
        if p.field_key == field_key:
            notes.append(
                f"Есть несогласованное предложение: {p.field_title.lower()} → "
                f"{p.proposed_display} («{p.source_file}»: {p.status_note.lower()})."
            )
    return {
        "status": fv.status,
        "question": f"{field_title} по договору {contract_no} на {as_of}",
        "answer": answer,
        "field_key": field_key,
        "contract_no": contract_no,
        "value": fv.value,
        "value_display": fv.display,
        "history": [s.to_dict() for s in fv.history],
        "pending": [s.to_dict() for s in fv.pending],
        "no_data_reason": fv.no_data_reason,
        "no_data_evidence": fv.no_data_evidence.to_dict() if fv.no_data_evidence else None,
        "proposals": related_proposals,
        "notes": notes,
    }


def _delivery_before_answer(result: AnalysisResult, date_param: Optional[str]) -> dict:
    as_of = tu.format_ru_date(result.as_of)
    threshold = tu.parse_any_date(date_param)
    if threshold is None:
        log.warning("question: некорректная пороговая дата «%s»", date_param)
        return {"status": "error", "answer": "Не указана корректная пороговая дата."}
    matched, no_data, review = [], [], []
    for c in result.contracts:
        fv: FieldValue = c.fields["delivery_date"]
        if fv.status == "no_data":
            no_data.append(c.number)
            continue
        if fv.status == "needs_review":
            review.append(c.number)
        d = tu.parse_any_date(str(fv.value)) if fv.value else None
        if d is not None and d < threshold:
            matched.append({
                "contract_no": c.number,
                "value_display": fv.display,
                "status": fv.status,
                "history": [s.to_dict() for s in fv.history],
                "proposals": [p.to_dict() for p in c.proposals
                              if p.field_key == "delivery_date"],
            })
    lines = [f"Проверка на {as_of}; порог: раньше {tu.format_ru_date(threshold)}."]
    if matched:
        lines.append("Поставка запланирована раньше порога:")
        for m in matched:
            prop_txt = ""
            if m["proposals"]:
                props = "; ".join(
                    f"{p['proposed_display']} — {p['status_note']} («{p['source_file']}»)"
                    for p in m["proposals"]
                )
                prop_txt = f" Есть несогласованное изменение срока: {props}"
            lines.append(f"• {m['contract_no']} — {m['value_display']}.{prop_txt}")
    else:
        lines.append("Договоров с поставкой раньше указанной даты не найдено.")
    if no_data:
        lines.append(f"Нет данных о сроке (проверить вручную): {', '.join(no_data)}.")
    if review:
        lines.append(f"Требуют проверки (значение под сомнением): {', '.join(review)}.")
    return {
        "status": "ok",
        "question": f"Поставка раньше {tu.format_ru_date(threshold)} — список договоров и несогласованные изменения срока",
        "answer": "\n".join(lines),
        "matched": matched,
        "no_data_contracts": no_data,
        "review_contracts": review,
    }


def answer_question(result: AnalysisResult, question_id: str, params: dict) -> dict:
    template = {"q1": "delivery_as_of", "q2": "amount",
                "q3": "delivery_before"}.get(question_id, question_id)
    log.info("question: question_id=%s, template=%s, params=%s", question_id, template, params)
    if template == "delivery_as_of":
        answer = _field_answer(result, params.get("contract"), "delivery_date", "Срок поставки")
    elif template == "amount":
        answer = _field_answer(result, params.get("contract"), "amount", "Сумма")
    elif template == "delivery_before":
        answer = _delivery_before_answer(result, params.get("date"))
    else:
        log.warning("question: неизвестный вопрос id=%s", question_id)
        answer = {"status": "error", "answer": f"Неизвестный вопрос: {question_id}"}
    log.info("question: ответ status=%s", answer.get("status", "?"))
    return answer
