"""Тесты извлечения значений: суммы, сроки, «нет данных», точность цитат."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import extraction as ext
from app import textutils as tu
from tests.synthetic import make_amendment, make_contract, make_email, sample_text


class TestTextUtils(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(tu.parse_amount("1 200 000"), 1_200_000)
        self.assertEqual(tu.parse_amount("1\u00a0380\u00a0000"), 1_380_000)
        self.assertEqual(tu.parse_amount("10 500,50"), 10500.50)
        self.assertIsNone(tu.parse_amount("много"))
        self.assertIsNone(tu.parse_amount("12a"))

    def test_parse_ru_date(self):
        self.assertEqual(tu.format_ru_date(tu.parse_ru_date("15.04.2026")), "15.04.2026")
        self.assertIsNone(tu.parse_ru_date("31.02.2026"))   # несуществующая дата
        self.assertIsNone(tu.parse_ru_date("не дата"))

    def test_norm_preserves_length(self):
        s = "ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ № 2 — ёЖИК"
        self.assertEqual(len(tu.norm(s)), len(s))

    def test_expand_to_line(self):
        text = "первая\nвторая строка\nтретья"
        start = text.index("строка")
        ls, le, line = tu.expand_to_line(text, start, start + 6)
        self.assertEqual(text[ls:le], "вторая строка")
        self.assertEqual(line, 2)

    def test_decode_bytes_fallbacks(self):
        text, enc = tu.decode_bytes("Привет".encode("utf-8"))
        self.assertEqual((text, enc), ("Привет", "utf-8"))
        text, enc = tu.decode_bytes("Привет".encode("cp1251"))
        self.assertEqual(text, "Привет")
        self.assertEqual(enc, "windows-1251")


class TestBaseExtraction(unittest.TestCase):
    def _extract(self, text, name="t.txt"):
        return ext.extract_fields_from_text(text, name, "contract")

    def test_amount_extracted_with_exact_quote(self):
        text = sample_text("01_contract.txt")
        f = self._extract(text, "01_contract.txt")["amount"]
        self.assertEqual(f.status, "ok")
        self.assertEqual(f.value, 1_200_000)
        self.assertEqual(f.evidence.clause, "2.1")
        # Цитата — ТОЧНАЯ подстрока исходного текста по указанным смещениям.
        self.assertEqual(text[f.evidence.char_start:f.evidence.char_end], f.evidence.quote)
        self.assertIn("1 200 000", f.evidence.quote)

    def test_delivery_date_not_confused_with_payment_or_acceptance(self):
        text = sample_text("03_contract.txt")
        f = self._extract(text, "03_contract.txt")["delivery_date"]
        self.assertEqual(f.value, "2026-03-20")  # не 10.04.2026 (оплата!)
        self.assertEqual(f.evidence.clause, "3.1")
        self.assertIn("поставить комплектующие", f.evidence.quote)
        self.assertNotIn("10.04.2026", f.evidence.quote)

    def test_budget_limit_is_not_contract_amount(self):
        # 600 000 — внутренний лимит покупателя, а НЕ сумма договора.
        text = sample_text("04_contract.txt")
        f = self._extract(text, "04_contract.txt")["amount"]
        self.assertEqual(f.status, "no_data")
        self.assertIsNone(f.value)
        self.assertNotIn("600", f.no_data_evidence.quote if f.no_data_evidence else "")
        self.assertIn("спецификац", f.no_data_reason.lower())

    def test_no_data_has_reason_and_quote(self):
        text = sample_text("04_contract.txt")
        f = self._extract(text, "04_contract.txt")["amount"]
        self.assertEqual(f.status, "no_data")
        self.assertIsNotNone(f.no_data_reason)
        self.assertIsNotNone(f.no_data_evidence)
        self.assertEqual(text[f.no_data_evidence.char_start:f.no_data_evidence.char_end],
                         f.no_data_evidence.quote)

    def test_synthetic_contract_other_values(self):
        # Значения, которых нет в учебных файлах, извлекаются так же.
        text = make_contract("XYZ-777", amount="2 345 678", delivery="15.11.2027")
        f = self._extract(text)
        self.assertEqual(f["amount"].value, 2_345_678)
        self.assertEqual(f["delivery_date"].value, "2027-11-15")

    def test_unknown_wording_yields_no_data(self):
        text = make_contract("XYZ-888", amount_absent=True).replace(
            "2.1. Общая стоимость определяется спецификацией в приложении № 1. "
            "Приложение не предоставлено. Числовое значение стоимости в тексте "
            "договора не указано.",
            "2.1. Цена согласуется сторонами дополнительно в рабочем порядке.")
        f = self._extract(text)
        self.assertEqual(f["amount"].status, "no_data")
        self.assertIsNone(f["amount"].value)


class TestReplacements(unittest.TestCase):
    def test_amendment_replacements_parsed(self):
        text = sample_text("02_amendment_signed.txt")
        reps = ext.extract_replacements(text, "02_amendment_signed.txt", "amendment_signed")
        self.assertEqual(len(reps), 2)
        by_clause = {r.clause_ref: r for r in reps}
        self.assertIn("2.1", by_clause)
        self.assertIn("3.1", by_clause)
        self.assertEqual(by_clause["2.1"].fields["amount"].value, 1_380_000)
        self.assertEqual(by_clause["3.1"].fields["delivery_date"].value, "2026-04-30")
        # Цитаты — точные подстроки ОРИГИНАЛЬНОГО документа (с сохранением регистра).
        for r in reps:
            self.assertEqual(text[r.evidence.char_start:r.evidence.char_end], r.evidence.quote)
            self.assertIn("изложить в следующей редакции", r.evidence.quote)
            for f in r.fields.values():
                self.assertEqual(text[f.evidence.char_start:f.evidence.char_end], f.evidence.quote)
                if f.spec_key == "amount":
                    self.assertIn("1 380 000", f.evidence.quote)

    def test_unparsed_replacement_detected(self):
        text = make_amendment(9, "XYZ-777", unparsed_clause="7.4")
        reps = ext.extract_replacements(text, "s.txt", "amendment_signed")
        self.assertEqual(len(reps), 1)
        self.assertEqual(reps[0].fields, {})  # значение не распознано — не выдумываем

    def test_replacement_with_straight_quotes(self):
        text = ('ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ Q-1\n'
                'Дата: 01.03.2026\nСтатус: подписано обеими сторонами 01.03.2026.\n\n'
                '1. Пункт 2.1 договора изложить в следующей редакции: '
                '"Общая стоимость поставки составляет 777 000 рублей, включая НДС".\n\n'
                '2. Настоящее соглашение вступает в силу 01.03.2026.\n')
        reps = ext.extract_replacements(text, "q.txt", "amendment_signed")
        self.assertEqual(len(reps), 1)
        self.assertEqual(reps[0].fields["amount"].value, 777_000)


class TestProposals(unittest.TestCase):
    def test_email_proposal(self):
        text = sample_text("05_supplier_email.txt")
        found = ext.extract_proposal_findings(text, "05_supplier_email.txt", "email")
        values = {(f[0].key, f[1]) for f in found}
        self.assertIn(("delivery_date", "2026-03-27"), values)
        self.assertEqual(len(found), 1)  # дедупликация: одно предложение, не два
        ev = found[0][3]
        self.assertEqual(text[ev.char_start:ev.char_end], ev.quote)

    def test_draft_proposals(self):
        text = sample_text("06_amendment_draft.txt")
        found = ext.extract_proposal_findings(text, "06_amendment_draft.txt", "amendment_draft")
        values = {(f[0].key, f[1]) for f in found}
        self.assertIn(("amount", 1_450_000), values)
        self.assertIn(("delivery_date", "2026-05-10"), values)

    def test_contract_text_has_no_proposals(self):
        # В подписанном договоре «предлагается» не встречается — предложений нет.
        found = ext.extract_proposal_findings(
            sample_text("01_contract.txt"), "01_contract.txt", "contract")
        self.assertEqual(found, [])

    def test_synthetic_email_proposal(self):
        text = make_email("XYZ-777", new_delivery="25.12.2027", old_delivery="15.12.2027")
        found = ext.extract_proposal_findings(text, "e.txt", "email")
        values = {(f[0].key, f[1]) for f in found}
        self.assertIn(("delivery_date", "2027-12-25"), values)
        self.assertNotIn(("delivery_date", "2027-12-15"), values)  # старая дата — не предложение


if __name__ == "__main__":
    unittest.main()
