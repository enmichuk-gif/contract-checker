"""Тесты разрешения условий «на дату»: подписанные соглашения, проекты, письма,
статусы документов, предупреждения. Включая синтетические документы со
значениями, которых нет в учебных файлах (доказательство отсутствия «зашивки»).
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import resolution
from tests.synthetic import (
    load_samples, make_amendment, make_contract, make_email, sample_text, to_files,
)

AS_OF = date(2026, 3, 10)


def by_number(result, number):
    for c in result.contracts:
        if c.number == number:
            return c
    return None


class TestSamplesResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = resolution.analyze(load_samples(), as_of=AS_OF)

    def test_three_contracts_only(self):
        self.assertEqual(len(self.r.contracts), 3)
        self.assertEqual([c.number for c in self.r.contracts], ["STN-101", "STN-102", "STN-103"])

    def test_stn101_amended_by_signed_amendment(self):
        c = by_number(self.r, "STN-101")
        self.assertEqual(c.fields["amount"].value, 1_380_000)
        self.assertEqual(c.fields["amount"].status, "changed")
        self.assertEqual(c.fields["amount"].changed_by, "02_amendment_signed.txt")
        self.assertEqual(c.fields["delivery_date"].value, "2026-04-30")
        self.assertEqual(len(c.fields["amount"].history), 2)  # договор -> соглашение
        self.assertEqual(c.fields["amount"].history[0].value, 1_200_000)

    def test_stn102_untouched_email_is_proposal_only(self):
        c = by_number(self.r, "STN-102")
        self.assertEqual(c.fields["amount"].value, 840_000)
        self.assertEqual(c.fields["amount"].status, "ok")
        self.assertEqual(c.fields["delivery_date"].value, "2026-03-20")
        self.assertEqual(c.fields["delivery_date"].status, "ok")
        props = [p for p in c.proposals if p.field_key == "delivery_date"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].proposed_value, "2026-03-27")
        self.assertEqual(props[0].source_file, "05_supplier_email.txt")
        self.assertEqual(props[0].doc_type, "email")

    def test_stn103_no_amount_data(self):
        c = by_number(self.r, "STN-103")
        f = c.fields["amount"]
        self.assertEqual(f.status, "no_data")
        self.assertIsNone(f.value)
        self.assertEqual(f.display, "нет данных")
        self.assertIsNotNone(f.no_data_reason)
        self.assertEqual(c.fields["delivery_date"].value, "2026-04-30")

    def test_draft_amendment_becomes_proposals_not_values(self):
        c = by_number(self.r, "STN-101")
        props = {(p.field_key, p.proposed_value) for p in c.proposals}
        self.assertIn(("amount", 1_450_000), props)
        self.assertIn(("delivery_date", "2026-05-10"), props)
        for p in c.proposals:
            self.assertEqual(p.source_file, "06_amendment_draft.txt")
            self.assertIn("не подписан", p.status_note.lower())
        # значения проекта НЕ попали в таблицу
        self.assertNotEqual(c.fields["amount"].value, 1_450_000)
        self.assertNotEqual(c.fields["delivery_date"].value, "2026-05-10")

    def test_all_documents_linked_no_warnings(self):
        self.assertEqual(self.r.unlinked_documents, [])
        self.assertEqual([w for w in self.r.warnings if w.level == "warn"], [])

    def test_every_evidence_is_exact_substring(self):
        texts = {name: raw.decode("utf-8") for name, raw in load_samples()}

        def all_evidence(r):
            for c in r.contracts:
                for fv in c.fields.values():
                    for s in fv.history + fv.pending:
                        yield s.evidence
                    if fv.no_data_evidence:
                        yield fv.no_data_evidence
            for p in r.proposals:
                yield p.evidence

        count = 0
        for ev in all_evidence(self.r):
            t = texts[ev.file_name]
            self.assertEqual(t[ev.char_start:ev.char_end], ev.quote,
                             f"цитата не точная подстрока в {ev.file_name}")
            self.assertGreaterEqual(ev.line, 1)
            count += 1
        self.assertGreaterEqual(count, 10)

    def test_results_independent_of_file_names(self):
        # Переименование файлов не меняет результат (имена не определяют приоритет).
        renamed = [(f"doc_{i:02d}.txt", raw) for i, (_, raw) in enumerate(load_samples())]
        r2 = resolution.analyze(renamed, as_of=AS_OF)
        self.assertEqual(len(r2.contracts), 3)
        c = by_number(r2, "STN-101")
        self.assertEqual(c.fields["amount"].value, 1_380_000)
        self.assertEqual(c.fields["delivery_date"].value, "2026-04-30")


class TestAsOfDate(unittest.TestCase):
    def test_before_amendment_effective_date(self):
        r = resolution.analyze(load_samples(), as_of=date(2026, 2, 28))
        c = by_number(r, "STN-101")
        # Соглашение ещё не вступило в силу -> действуют значения договора.
        self.assertEqual(c.fields["amount"].value, 1_200_000)
        self.assertEqual(c.fields["amount"].status, "ok")
        self.assertEqual(c.fields["delivery_date"].value, "2026-04-15")
        # Но оно подписано и показано как предстоящее изменение (pending).
        self.assertEqual(len(c.fields["amount"].pending), 1)
        self.assertEqual(c.fields["amount"].pending[0].value, 1_380_000)

    def test_on_effective_date_inclusive(self):
        r = resolution.analyze(load_samples(), as_of=date(2026, 3, 1))
        c = by_number(r, "STN-101")
        self.assertEqual(c.fields["amount"].value, 1_380_000)


class TestSyntheticScenarios(unittest.TestCase):
    """Сценарии на синтетических документах — значения не из учебных файлов."""

    def test_full_cycle_with_other_values(self):
        files = to_files(
            ("c1.txt", make_contract("XYZ-777", amount="2 345 678", delivery="15.11.2027")),
            ("a1.txt", make_amendment(1, "XYZ-777", doc_date="01.01.2028",
                                      effective="10.01.2028", new_amount="3 000 000")),
        )
        r = resolution.analyze(files, as_of=date(2028, 1, 20))
        c = by_number(r, "XYZ-777")
        self.assertEqual(c.fields["amount"].value, 3_000_000)
        self.assertEqual(c.fields["amount"].status, "changed")
        self.assertEqual(c.fields["delivery_date"].value, "2027-11-15")  # не менялось

    def test_two_amendments_applied_in_effective_order(self):
        files = to_files(
            ("c.txt", make_contract("M-1", amount="100 000", delivery="01.03.2026")),
            ("a2.txt", make_amendment(2, "M-1", doc_date="01.02.2026", effective="20.02.2026",
                                      new_amount="300 000")),
            ("a1.txt", make_amendment(1, "M-1", doc_date="15.01.2026", effective="01.02.2026",
                                      new_amount="200 000")),
        )
        r = resolution.analyze(files, as_of=date(2026, 3, 1))
        c = by_number(r, "M-1")
        self.assertEqual(c.fields["amount"].value, 300_000)  # последнее по вступлению в силу
        self.assertEqual(len(c.fields["amount"].history), 3)

    def test_unsigned_amendment_is_not_applied(self):
        files = to_files(
            ("c.txt", make_contract("M-2", amount="100 000")),
            ("a.txt", make_amendment(1, "M-2", new_amount="999 999", signed=False)),
        )
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-2")
        self.assertEqual(c.fields["amount"].value, 100_000)
        props = [p for p in c.proposals if p.field_key == "amount"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].proposed_value, 999_999)
        self.assertEqual(props[0].doc_type, "amendment_unsigned")

    def test_draft_amendment_with_redaction_wording_is_proposal(self):
        # Проект в формулировке «изложить в редакции» — тоже только предложение.
        files = to_files(
            ("c.txt", make_contract("M-3", amount="100 000", delivery="01.05.2026")),
            ("d.txt", make_amendment(1, "M-3", new_delivery="30.06.2026", draft=True)),
        )
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-3")
        self.assertEqual(c.fields["delivery_date"].value, "2026-05-01")
        props = [p for p in c.proposals if p.field_key == "delivery_date"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].proposed_value, "2026-06-30")

    def test_unparsed_signed_change_flags_needs_review(self):
        files = to_files(
            ("c.txt", make_contract("M-4", amount="100 000")),
            ("a.txt", make_amendment(1, "M-4", effective="01.03.2026", unparsed_clause="2.1")),
        )
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-4")
        self.assertEqual(c.fields["amount"].status, "needs_review")
        self.assertTrue(c.needs_review)
        self.assertTrue(any("M-4" in w.text or "не распознано" in w.text
                            for w in r.warnings if w.level == "warn"))

    def test_signed_amendment_without_effective_date_uses_signing_date(self):
        # Дата вступления в силу не указана явно -> применяется дата подписания
        # (допущение по п.1 ст.425 ГК РФ), но пользователь ОБЯЗАН увидеть warning.
        text = make_amendment(1, "M-5", doc_date="20.02.2026", new_amount="555 000",
                              effective=None)
        self.assertNotIn("вступает в силу", text)
        files = to_files(("c.txt", make_contract("M-5", amount="100 000")), ("a.txt", text))
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-5")
        self.assertEqual(c.fields["amount"].value, 555_000)
        self.assertTrue(any("дата подписания" in w.text for w in r.warnings))

    def test_signed_amendment_without_any_date_not_applied(self):
        # Нет ни явной даты вступления в силу, ни даты подписания/документа —
        # применять нельзя, показываем предупреждение.
        text = make_amendment(1, "M-8", new_amount="555 000", effective=None)
        text = text.replace("Дата: 01.03.2026\n", "")
        text = text.replace("Статус: подписано обеими сторонами 01.03.2026.",
                            "Статус: подписано обеими сторонами.")
        files = to_files(("c.txt", make_contract("M-8", amount="100 000")), ("a.txt", text))
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-8")
        self.assertEqual(c.fields["amount"].value, 100_000)
        self.assertTrue(any("вступления в силу" in w.text for w in r.warnings))

    def test_unlinked_amendment_and_unknown_document_warned(self):
        files = to_files(
            ("c.txt", make_contract("M-6")),
            ("orphan.txt", make_amendment(1, "NO-SUCH", new_amount="1")),
            ("junk.txt", "Какая-то справка без признаков договора."),
        )
        r = resolution.analyze(files, as_of=AS_OF)
        self.assertEqual(len(r.unlinked_documents), 2)
        warns = " ".join(w.text for w in r.warnings)
        self.assertIn("NO-SUCH", warns)
        self.assertIn("не распознан", warns)

    def test_unsigned_contract_warned(self):
        files = to_files(("c.txt", make_contract("M-7", signed=False)))
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "M-7")
        self.assertTrue(c.needs_review)
        self.assertTrue(any("не подписан" in w.text for w in r.warnings))

    def test_duplicate_contract_number_warned(self):
        files = to_files(
            ("old.txt", make_contract("D-1", amount="100 000", doc_date="01.01.2026")),
            ("new.txt", make_contract("D-1", amount="200 000", doc_date="01.02.2026")),
        )
        r = resolution.analyze(files, as_of=AS_OF)
        self.assertEqual(len(r.contracts), 1)
        c = by_number(r, "D-1")
        self.assertEqual(c.fields["amount"].value, 200_000)   # взят более поздний
        self.assertTrue(any("нескольк" in w.text for w in r.warnings))

    def test_changed_field_in_same_format(self):
        # Сценарий демонстрации: в договоре изменено одно поле, формат тот же.
        original = sample_text("01_contract.txt")
        modified = original.replace("1 200 000", "9 876 543").replace(
            "один миллион двести тысяч", "девять миллионов восемьсот семьдесят шесть тысяч пятьсот сорок три")
        self.assertNotEqual(modified, original)
        files = to_files(("new_name.txt", modified))
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "STN-101")
        self.assertEqual(c.fields["amount"].value, 9_876_543)
        self.assertEqual(c.fields["delivery_date"].value, "2026-04-15")

    def test_no_documents(self):
        r = resolution.analyze([], as_of=AS_OF)
        self.assertEqual(r.contracts, [])

    def test_cp1251_file_supported(self):
        text = make_contract("W-1", amount="42 000")
        files = [("win.txt", text.encode("cp1251"))]
        r = resolution.analyze(files, as_of=AS_OF)
        c = by_number(r, "W-1")
        self.assertEqual(c.fields["amount"].value, 42_000)
        self.assertEqual(c.fields["amount"].history[0].evidence.quote.count("42 000"), 1)
        self.assertTrue(any("windows-1251" in w.text for w in r.warnings))


if __name__ == "__main__":
    unittest.main()
