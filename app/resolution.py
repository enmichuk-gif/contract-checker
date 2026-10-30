"""Разрешение условий договоров «по состоянию на дату проверки».

Правило предметной области (из постановки задачи): условия изменяет ТОЛЬКО
подписанное сторонами допсоглашение, вступившее в силу к дате проверки.
Письма и проекты — сведения для проверки (несогласованные предложения).
Имена файлов и даты загрузки приоритета не дают.

Порядок работы analyze():
  1. распознать все загруженные документы (documents.parse_document);
  2. сгруппировать договоры по номерам (дубликаты -> предупреждение);
  3. связать допсоглашения/письма с договорами по номеру договора в тексте;
  4. для каждого договора: базовые поля из текста + последовательное применение
     подписанных и вступивших в силу соглашений (сортировка по дате вступления
     в силу); подписанные, но будущие изменения -> pending; проекты/письма ->
     proposals; нераспознанные изменения пунктов -> needs_review;
  5. несвязанные документы и подозрительные ситуации -> warnings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import documents as docs
from . import extraction as ext
from . import textutils as tu
from .models import (
    AnalysisResult, ContractView, DocumentMeta, FieldValue, Proposal,
    ValueStep, Warning,
)

log = logging.getLogger(__name__)


@dataclass
class Doc:
    meta: DocumentMeta
    text: str


def analyze(files: list[tuple[str, bytes]], as_of: Optional[date] = None) -> AnalysisResult:
    """Главная точка входа ядра. files = [(имя файла, байты содержимого)]."""
    if as_of is None:
        as_of = date.today()

    log.info("analyze: начало анализа %d файл(ов), дата проверки=%s", len(files), as_of)

    warnings: list[Warning] = []
    doc_list: list[Doc] = []

    # Дедупликация повторно загруженных файлов (одно имя = одна копия).
    seen_names: dict[str, int] = {}
    deduped: list[tuple[str, bytes]] = []
    for name, raw in files:
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] == 1:
            deduped.append((name, raw))
    for name, count in seen_names.items():
        if count > 1:
            log.info("analyze: дедупликация — «%s» загружен %d раз(а)", name, count)
            warnings.append(Warning(
                "info",
                f"Файл «{name}» загружен {count} раз(а) — использована одна копия.",
                name,
            ))

    for name, raw in deduped:
        text, encoding = tu.decode_bytes(raw)
        if encoding != "utf-8":
            log.info("analyze: «%s» — кодировка %s (не UTF-8)", name, encoding)
            warnings.append(Warning(
                "info",
                f"«{name}»: использована кодировка {encoding} (файл не в UTF-8).",
                name,
            ))
        meta = docs.parse_document(name, text, encoding)
        log.debug("analyze: «%s» -> тип=%s, номер=%s, договор=%s",
                  name, meta.doc_type, meta.number, meta.contract_no)
        doc_list.append(Doc(meta=meta, text=text))

    # --- 1. Договоры и дубликаты номеров -----------------------------------
    contract_docs = [d for d in doc_list if d.meta.doc_type == "contract"]
    by_number: dict[str, list[Doc]] = {}
    for d in contract_docs:
        key = d.meta.number or f"__noname__{d.meta.file_name}"
        by_number.setdefault(key, []).append(d)

    log.info("analyze: найдено %d договор(ов) (%d уникальных номеров), "
             "%d прочих документ(ов)",
             len(contract_docs), len(by_number), len(doc_list) - len(contract_docs))

    for number, group in by_number.items():
        if len(group) > 1:
            names = ", ".join(g.meta.file_name for g in group)
            log.warning("analyze: дубликат номера «%s» — %d файл(ов): %s", number, len(group), names)
            warnings.append(Warning(
                "warn",
                f"Загружено несколько договоров с номером «{number}» ({names}). "
                f"В таблицу взят документ с более поздней датой — требуется проверка.",
                group[0].meta.file_name,
            ))

    def _sort_key(d: Doc):
        return (d.meta.doc_date or "", d.meta.file_name)

    # --- 2. Связывание документов с договорами ------------------------------
    related: dict[str, list[Doc]] = {n: [] for n in by_number}
    unlinked: list[Doc] = []
    for d in doc_list:
        if d.meta.doc_type == "contract":
            continue
        if d.meta.doc_type == "unknown":
            # Предупреждение о нераспознанном типе добавляется в шаге 4.
            unlinked.append(d)
            continue
        target = d.meta.contract_no
        if target and target in related:
            related[target].append(d)
        else:
            unlinked.append(d)
            if target:
                warnings.append(Warning(
                    "warn",
                    f"«{d.meta.file_name}»: ссылка на договор «{target}», "
                    f"но такой договор не загружен — документ не учтён в таблице.",
                    d.meta.file_name,
                ))
            else:
                warnings.append(Warning(
                    "warn",
                    f"«{d.meta.file_name}»: не удалось определить, к какому договору "
                    f"относится документ — требуется проверка.",
                    d.meta.file_name,
                ))

    # --- 3. Разрешение значений по каждому договору -------------------------
    contracts: list[ContractView] = []
    all_proposals: list[Proposal] = []

    for number in sorted(by_number):
        group = sorted(by_number[number], key=_sort_key)
        current = group[-1]
        view, proposals, w = _resolve_contract(
            number, current, related.get(number, []), as_of,
        )
        contracts.append(view)
        all_proposals.extend(proposals)
        warnings.extend(w)

    # --- 4. Документы без типа ----------------------------------------------
    for d in doc_list:
        if d.meta.doc_type == "unknown":
            warnings.append(Warning(
                "warn",
                f"«{d.meta.file_name}»: тип документа не распознан — "
                f"содержимое не учтено в расчёте. Требуется проверка.",
                d.meta.file_name,
            ))

    result = AnalysisResult(
        as_of=as_of.isoformat(),
        fields_meta=[spec.to_meta() for spec in ext.FIELD_REGISTRY],
        contracts=contracts,
        proposals=all_proposals,
        documents=[d.meta for d in doc_list],
        unlinked_documents=[d.meta for d in unlinked],
        warnings=warnings,
        document_texts={d.meta.file_name: d.text for d in doc_list},
    )

    log.info(
        "analyze: готово — %d договор(ов), %d предупреждений, %d несогласованных "
        "предложений, %d несвязанных документ(ов)",
        len(contracts), len(warnings), len(all_proposals), len(unlinked),
    )
    return result


# ---------------------------------------------------------------------------

def _resolve_contract(
    number: str,
    contract_doc: Doc,
    related_docs: list[Doc],
    as_of: date,
) -> tuple[ContractView, list[Proposal], list[Warning]]:
    log.info("resolve: договор «%s» (%s) — %d связанных документ(ов)",
             number, contract_doc.meta.file_name, len(related_docs))
    warnings: list[Warning] = []
    meta = contract_doc.meta
    text = contract_doc.text

    supplier = docs.extract_party(text, "supplier")
    buyer = docs.extract_party(text, "buyer")
    log.debug("resolve: «%s» — поставщик=%s, покупатель=%s", number, supplier, buyer)

    base_findings = ext.extract_fields_from_text(
        text, meta.file_name, meta.doc_type,
    )

    # Карта «пункт договора -> ключ поля» по базовым извлечениям: нужна, чтобы
    # понять, какое поле задевает нераспознанное изменение допсоглашения.
    clause_to_field: dict[str, str] = {}
    for spec_key, finding in base_findings.items():
        ev = finding.evidence or finding.no_data_evidence
        if ev and ev.clause:
            clause_to_field[ev.clause] = spec_key

    # --- допсоглашения, письма, проекты -------------------------------------
    signed_amendments = [d for d in related_docs if d.meta.doc_type == "amendment_signed"]
    nonbinding = [d for d in related_docs
                  if d.meta.doc_type in ("amendment_draft", "amendment_unsigned",
                                         "amendment_unknown", "email")]

    for d in related_docs:
        if d.meta.doc_type == "amendment_unknown":
            warnings.append(Warning(
                "warn",
                f"«{d.meta.file_name}»: статус подписания допсоглашения не определён — "
                f"изменения НЕ применены, требуется проверка.",
                d.meta.file_name,
            ))
        if d.meta.doc_type == "amendment_unsigned":
            warnings.append(Warning(
                "info",
                f"«{d.meta.file_name}»: допсоглашение не подписано — учтено как "
                f"несогласованное предложение.",
                d.meta.file_name,
            ))

    # Подписанные соглашения: делим на вступившие в силу и будущие.
    effective_amendments: list[Doc] = []
    future_amendments: list[Doc] = []
    for d in signed_amendments:
        eff = tu.parse_any_date(d.meta.effective_date)
        if eff is None:
            warnings.append(Warning(
                "warn",
                f"«{d.meta.file_name}»: подписанное соглашение без даты вступления "
                f"в силу — изменения НЕ применены, требуется проверка.",
                d.meta.file_name,
            ))
            continue
        if d.meta.effective_date_assumed:
            warnings.append(Warning(
                "info",
                f"«{d.meta.file_name}»: дата вступления в силу явно не указана — "
                f"принята дата подписания ({tu.format_ru_date(d.meta.effective_date)}). "
                f"Рекомендуется проверить.",
                d.meta.file_name,
            ))
        (effective_amendments if eff <= as_of else future_amendments).append(d)

    effective_amendments.sort(
        key=lambda d: (d.meta.effective_date or "", d.meta.doc_date or "", d.meta.file_name)
    )
    future_amendments.sort(
        key=lambda d: (d.meta.effective_date or "", d.meta.doc_date or "", d.meta.file_name)
    )

    log.info("resolve: «%s» — %d вступивших в силу допсоглашений, %d будущих, "
             "%d несогласованных",
             number, len(effective_amendments), len(future_amendments), len(nonbinding))

    # --- пошаговое применение вступивших в силу изменений --------------------
    history: dict[str, list[ValueStep]] = {spec.key: [] for spec in ext.FIELD_REGISTRY}
    review: dict[str, str] = {}
    changed_by: dict[str, Optional[str]] = {spec.key: None for spec in ext.FIELD_REGISTRY}
    
    # Детекция конфликта: два ДС с одинаковой датой вступления, но разными значениями
    # Для каждого поля храним: {effective_date: (value, source_file)}
    last_value_by_effective: dict[str, dict[str, tuple[Any, str]]] = {
        spec.key: {} for spec in ext.FIELD_REGISTRY
    }

    for spec in ext.FIELD_REGISTRY:
        finding = base_findings[spec.key]
        if finding.status == "ok":
            history[spec.key].append(ValueStep(
                source_file=meta.file_name,
                doc_type=meta.doc_type,
                clause=finding.evidence.clause if finding.evidence else None,
                value=finding.value,
                display=finding.display,
                effective=meta.doc_date,
                note="Исходное значение по договору",
                evidence=finding.evidence,
            ))
            # Запоминаем исходное значение
            if meta.doc_date:
                last_value_by_effective[spec.key][meta.doc_date] = (finding.value, meta.file_name)

    for d in effective_amendments:
        eff_iso = d.meta.effective_date
        replacements = ext.extract_replacements(d.text, d.meta.file_name, d.meta.doc_type)
        if not replacements:
            warnings.append(Warning(
                "warn",
                f"«{d.meta.file_name}»: подписанное соглашение не содержит распознанных "
                f"изменений пунктов («изложить в следующей редакции») — проверьте документ вручную.",
                d.meta.file_name,
            ))
        for rep in replacements:
            if not rep.fields:
                field_key = clause_to_field.get(rep.clause_ref)
                reason = (
                    f"Соглашение «{d.meta.file_name}» меняет п. {rep.clause_ref} договора, "
                    f"но новое значение не распознано автоматически"
                )
                if field_key:
                    review[field_key] = reason + " — требуется проверка"
                warnings.append(Warning("warn", reason + ".", d.meta.file_name))
                continue
            for field_key, finding in rep.fields.items():
                spec = ext.get_field_spec(field_key)
                
                # Проверяем конфликт: уже есть значение с той же датой вступления?
                if eff_iso in last_value_by_effective[field_key]:
                    prev_value, prev_file = last_value_by_effective[field_key][eff_iso]
                    if prev_value != finding.value:
                        # Конфликт! Два ДС с одинаковой датой, но разными значениями
                        conflict_reason = (
                            f"Конфликт: два подписанных соглашения с датой вступления "
                            f"{tu.format_ru_date(eff_iso)} задают разные значения для поля "
                            f"«{spec.title}»: «{prev_file}» → {prev_value} и "
                            f"«{d.meta.file_name}» → {finding.value}. Требуется проверка."
                        )
                        review[field_key] = conflict_reason
                        warnings.append(Warning("warn", conflict_reason, d.meta.file_name))
                        log.warning("resolve: конфликт для «%s» поле %s: %s", 
                                   number, field_key, conflict_reason)
                
                # Запоминаем новое значение
                last_value_by_effective[field_key][eff_iso] = (finding.value, d.meta.file_name)
                
                history[field_key].append(ValueStep(
                    source_file=d.meta.file_name,
                    doc_type=d.meta.doc_type,
                    clause=rep.clause_ref,
                    value=finding.value,
                    display=finding.display,
                    effective=eff_iso,
                    note=(f"Изменение подписанным допсоглашением (п. {rep.clause_ref} договора "
                          f"в новой редакции), вступило в силу {tu.format_ru_date(eff_iso)}"),
                    evidence=finding.evidence,
                ))
                changed_by[field_key] = d.meta.file_name
                # Не удаляем review при конфликте — пусть remains
                if field_key not in review or not review[field_key].startswith("Конфликт"):
                    review.pop(field_key, None)

    # --- подписанные, но ещё не вступившие в силу (pending) -------------------
    pending: dict[str, list[ValueStep]] = {spec.key: [] for spec in ext.FIELD_REGISTRY}
    for d in future_amendments:
        eff_iso = d.meta.effective_date
        for rep in ext.extract_replacements(d.text, d.meta.file_name, d.meta.doc_type):
            for field_key, finding in rep.fields.items():
                pending[field_key].append(ValueStep(
                    source_file=d.meta.file_name,
                    doc_type=d.meta.doc_type,
                    clause=rep.clause_ref,
                    value=finding.value,
                    display=finding.display,
                    effective=eff_iso,
                    note=(f"Подписанное соглашение, вступает в силу "
                          f"{tu.format_ru_date(eff_iso)} — на дату проверки НЕ действует"),
                    evidence=finding.evidence,
                ))

    # --- сборка FieldValue ----------------------------------------------------
    fields: dict[str, FieldValue] = {}
    for spec in ext.FIELD_REGISTRY:
        finding = base_findings[spec.key]
        steps = history[spec.key]
        last = steps[-1] if steps else None
        if last is not None:
            status = "changed" if changed_by[spec.key] else "ok"
            fv = FieldValue(
                key=spec.key, title=spec.title, unit=spec.unit,
                status=status,
                value=last.value, display=last.display,
                history=steps,
                changed_by=changed_by[spec.key],
                no_data_reason=None, no_data_evidence=None,
                pending=pending[spec.key],
                review_reason=review.get(spec.key),
            )
            if review.get(spec.key):
                fv.status = "needs_review"
        else:
            fv = FieldValue(
                key=spec.key, title=spec.title, unit=spec.unit,
                status="no_data",
                value=None, display="нет данных",
                history=[],
                changed_by=None,
                no_data_reason=finding.no_data_reason,
                no_data_evidence=finding.no_data_evidence,
                pending=pending[spec.key],
                review_reason=review.get(spec.key),
            )
            if review.get(spec.key):
                fv.status = "needs_review"
        fields[spec.key] = fv

    # --- несогласованные предложения (проекты, неподписанные соглашения, письма)
    proposals: list[Proposal] = []
    for d in nonbinding:
        status_note = _proposal_status_note(d)
        found = ext.extract_proposal_findings(d.text, d.meta.file_name, d.meta.doc_type)
        # Проекты допсоглашений могут формулировать изменения как «новую редакцию»
        # пункта — учитываем и такой вариант, чтобы не потерять предложение.
        if d.meta.doc_type != "email":
            seen_keys = {(f[0].key, str(f[1])) for f in found}
            for rep in ext.extract_replacements(d.text, d.meta.file_name, d.meta.doc_type):
                for field_key, finding in rep.fields.items():
                    if (field_key, str(finding.value)) in seen_keys:
                        continue
                    spec = ext.get_field_spec(field_key)
                    found.append((spec, finding.value, finding.display, rep.evidence))
        for spec, value, display, evidence in found:
            current = fields[spec.key]
            proposals.append(Proposal(
                contract_no=number,
                field_key=spec.key,
                field_title=spec.title,
                proposed_value=value,
                proposed_display=display,
                current_display=current.display,
                source_file=d.meta.file_name,
                doc_type=d.meta.doc_type,
                status_note=status_note,
                evidence=evidence,
            ))

    # --- предупреждения по статусу договора ----------------------------------
    contract_review_reasons: list[str] = []
    if meta.signed is False:
        warnings.append(Warning(
            "warn",
            f"Договор «{number}» ({meta.file_name}) не подписан — условия могут быть "
            f"недействующими. Требуется проверка.",
            meta.file_name,
        ))
        contract_review_reasons.append("Договор не подписан")
    elif meta.signed is None:
        warnings.append(Warning(
            "warn",
            f"Договор «{number}» ({meta.file_name}): статус подписания не определён — "
            f"требуется проверка.",
            meta.file_name,
        ))
        contract_review_reasons.append("Статус подписания договора не определён")

    for spec_key, reason in review.items():
        contract_review_reasons.append(reason)

    document_metas = [meta] + [d.meta for d in
                                sorted(related_docs, key=lambda x: x.meta.file_name)]

    view = ContractView(
        number=number if meta.number else "без номера",
        supplier=supplier,
        buyer=buyer,
        file_name=meta.file_name,
        doc_date=meta.doc_date,
        signed=meta.signed,
        fields=fields,
        documents=document_metas,
        proposals=proposals,
        needs_review=bool(contract_review_reasons) or
                     any(f.status == "needs_review" for f in fields.values()),
        review_reasons=contract_review_reasons,
    )
    return view, proposals, warnings


def _proposal_status_note(d: Doc) -> str:
    t = d.meta.doc_type
    if t == "amendment_draft":
        return "Проект допсоглашения — не подписан сторонами, условия не изменяет"
    if t == "amendment_unsigned":
        return "Допсоглашение не подписано — условия не изменяет"
    if t == "amendment_unknown":
        return "Статус подписания не определён — требуется проверка, условия не изменены"
    if t == "email":
        return "Письмо поставщика — предложение, требует согласия покупателя и подписания допсоглашения"
    return "Статус документа не определён — требуется проверка"
