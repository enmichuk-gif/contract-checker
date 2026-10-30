"""Конструкторы синтетических документов для тестов.

Синтетика нужна, чтобы доказать: ядро работает с ФОРМАТОМ, а не с конкретными
значениями из учебных файлов. Номера, суммы, даты и поставщики здесь другие.
"""
from __future__ import annotations


def make_contract(
    number: str,
    supplier: str = 'ООО «Тестовый Поставщик»',
    buyer: str = 'ООО «Тестовый Покупатель»',
    amount: str | None = "500 000",
    delivery: str | None = "01.06.2026",
    doc_date: str = "01.02.2026",
    signed: bool = True,
    amount_absent: bool = False,
    payment_deadline: str | None = "15.06.2026",
) -> str:
    status = (f"подписан обеими сторонами {doc_date}." if signed
              else "не подписан, проект на согласовании.")
    lines = [
        f"ДОГОВОР ПОСТАВКИ № {number}",
        f"Дата: {doc_date}",
        f"Статус: {status}",
        "",
        f"1.1. Покупатель: {buyer}. Поставщик: {supplier}.",
        "1.2. Предмет: поставка тестового оборудования.",
        "",
    ]
    if amount_absent:
        lines.append(
            "2.1. Общая стоимость определяется спецификацией в приложении № 1. "
            "Приложение не предоставлено. Числовое значение стоимости в тексте "
            "договора не указано."
        )
    else:
        lines.append(
            f"2.1. Общая стоимость поставки составляет {amount} "
            f"(тестовая пропись) рублей, включая НДС."
        )
    if payment_deadline:
        lines.append(
            f"2.2. Оплата оставшейся суммы производится не позднее {payment_deadline}. "
            f"Этот срок является сроком оплаты, а не поставки."
        )
    lines.append("")
    if delivery:
        lines.append(
            f"3.1. Поставщик обязуется поставить оборудование не позднее {delivery}."
        )
        lines.append(
            "3.2. Дата приемки может отличаться от даты поставки и не изменяет "
            "срок по пункту 3.1."
        )
    lines += [
        "",
        "4.1. Неустойка за просрочку поставки составляет 0,2% стоимости "
        "просроченной части поставки за каждый день.",
        "",
        "5.1. Изменения условий действуют после подписания дополнительного "
        "соглашения обеими сторонами и наступления указанной в нем даты "
        "вступления в силу.",
        "",
    ]
    return "\n".join(lines)


def make_amendment(
    agreement_no: int,
    contract_no: str,
    doc_date: str = "01.03.2026",
    effective: str | None = "01.03.2026",
    signed: bool = True,
    new_amount: str | None = None,
    new_delivery: str | None = None,
    amount_clause: str = "2.1",
    delivery_clause: str = "3.1",
    unparsed_clause: str | None = None,
    unparsed_text: str = "Условия определяются отдельным графиком, согласуемым сторонами",
    draft: bool = False,
) -> str:
    title = ("ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ" if draft else "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ")
    if draft:
        status = "проект на согласовании, не подписан ни одной стороной."
    elif signed:
        status = f"подписано обеими сторонами {doc_date}."
    else:
        status = "не подписано ни одной стороной."
    lines = [
        f"{title} № {agreement_no} К ДОГОВОРУ {contract_no}",
        f"Дата: {doc_date}",
        f"Статус: {status}",
        "Покупатель: ООО «Тестовый Покупатель». Поставщик: ООО «Тестовый Поставщик».",
        "",
    ]
    item = 1
    if new_amount:
        lines.append(
            f"{item}. Пункт {amount_clause} договора изложить в следующей редакции: "
            f"«Общая стоимость поставки составляет {new_amount} (пропись) рублей, "
            f"включая НДС»."
        )
        item += 1
    if new_delivery:
        lines.append(
            f"{item}. Пункт {delivery_clause} договора изложить в следующей редакции: "
            f"«Поставщик обязуется поставить оборудование не позднее {new_delivery}»."
        )
        item += 1
    if unparsed_clause:
        lines.append(
            f"{item}. Пункт {unparsed_clause} договора изложить в следующей редакции: "
            f"«{unparsed_text}»."
        )
        item += 1
    lines.append(f"{item}. Остальные условия договора остаются без изменений.")
    item += 1
    if effective and not draft:
        lines.append(f"{item}. Настоящее соглашение вступает в силу {effective}.")
    lines.append("")
    return "\n".join(lines)


def make_email(
    contract_no: str,
    supplier: str = 'ООО «Тестовый Поставщик»',
    new_delivery: str | None = "10.06.2026",
    old_delivery: str | None = "01.06.2026",
    doc_date: str = "05.03.2026",
) -> str:
    lines = [
        "ПИСЬМО ПОСТАВЩИКА О ПРЕДЛАГАЕМОМ ПЕРЕНОСЕ",
        f"Дата: {doc_date}",
        f"От: {supplier}",
        "Кому: ООО «Тестовый Покупатель»",
        f"Тема: договор {contract_no}",
        "",
    ]
    if new_delivery and old_delivery:
        lines.append(
            f"Коллеги, по договору {contract_no} предлагаем перенести срок поставки "
            f"с {old_delivery} на {new_delivery} по производственным причинам. "
            f"Просим подтвердить согласие и оформить дополнительное соглашение."
        )
    lines.append("")
    return "\n".join(lines)


def to_files(*pairs: tuple[str, str]) -> list[tuple[str, bytes]]:
    """[(имя, текст)] -> [(имя, байты utf-8)] — формат входа resolution.analyze."""
    return [(name, text.encode("utf-8")) for name, text in pairs]


# ---------------------------------------------------------------------------
# Учебные документы из поставки задания
# ---------------------------------------------------------------------------

import os

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "documents")


def load_samples() -> list[tuple[str, bytes]]:
    """Шесть учебных TXT-файлов как (имя, байты)."""
    out = []
    for name in sorted(os.listdir(SAMPLES_DIR)):
        if name.lower().endswith(".txt"):
            with open(os.path.join(SAMPLES_DIR, name), "rb") as f:
                out.append((name, f.read()))
    return out


def sample_text(name: str) -> str:
    with open(os.path.join(SAMPLES_DIR, name), encoding="utf-8") as f:
        return f.read()
