"""Тесты машины ответов на три вопроса закупщика."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import questions as q
from app import resolution
from tests.synthetic import load_samples, make_contract, to_files

AS_OF = date(2026, 3, 10)


class TestQuestionsOnSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = resolution.analyze(load_samples(), as_of=AS_OF)
        cls.r.questions_meta = q.build_questions_meta(cls.r)

    def test_questions_meta_defaults(self):
        metas = {m["id"]: m for m in self.r.questions_meta}
        # Три вопроса: q1 (delivery_as_of), q2 (amount), q3 (delivery_before)
        self.assertEqual(len(metas), 3)
        self.assertEqual(metas["q1"]["template"], "delivery_as_of")
        self.assertEqual(metas["q1"]["params"][0]["default"], "STN-101")
        self.assertEqual(metas["q2"]["template"], "amount")
        self.assertEqual(metas["q2"]["params"][0]["default"], "STN-102")
        self.assertEqual(metas["q3"]["template"], "delivery_before")
        self.assertEqual(metas["q3"]["params"][0]["default"], "2026-04-01")  # 1-е число след. месяца

    def test_q1_delivery_of_stn101(self):
        a = q.answer_question(self.r, "q1", {"contract": "STN-101"})
        self.assertEqual(a["status"], "changed")
        self.assertEqual(a["value"], "2026-04-30")
        self.assertIn("30.04.2026", a["answer"])
        self.assertEqual(len(a["history"]), 2)
        self.assertIn("02_amendment_signed.txt", a["history"][-1]["source_file"])
        # несогласованное предложение из проекта упомянуто в примечаниях
        self.assertTrue(any("10.05.2026" in n for n in a["notes"]))

    def test_q2_amount_of_stn102(self):
        a = q.answer_question(self.r, "q2", {"contract": "STN-102"})
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["value"], 840_000)
        self.assertIn("840", a["answer"])

    def test_q3_before_april(self):
        a = q.answer_question(self.r, "q3", {"date": "2026-04-01"})
        self.assertEqual([m["contract_no"] for m in a["matched"]], ["STN-102"])
        m = a["matched"][0]
        self.assertEqual(m["value_display"], "20.03.2026")
        self.assertEqual(len(m["proposals"]), 1)
        self.assertEqual(m["proposals"][0]["proposed_value"], "2026-03-27")
        self.assertIn("STN-102", a["answer"])
        self.assertIn("27.03.2026", a["answer"])

    def test_q3_other_threshold(self):
        a = q.answer_question(self.r, "q3", {"date": "2026-05-01"})
        self.assertEqual({m["contract_no"] for m in a["matched"]},
                         {"STN-101", "STN-102", "STN-103"})

    def test_q3_threshold_before_all(self):
        a = q.answer_question(self.r, "q3", {"date": "2026-01-01"})
        self.assertEqual(a["matched"], [])
        self.assertIn("не найдено", a["answer"])

    def test_unknown_contract_error(self):
        a = q.answer_question(self.r, "q1", {"contract": "NO-SUCH"})
        self.assertEqual(a["status"], "error")
        self.assertIn("не найден", a["answer"])

    def test_question_is_parameterized_not_hardcoded(self):
        # Тот же шаблон вопроса на синтетическом договоре с другими значениями.
        r = resolution.analyze(
            to_files(("c.txt", make_contract("ABC-42", amount="77 777", delivery="05.07.2030"))),
            as_of=AS_OF)
        a = q.answer_question(r, "q2", {"contract": "ABC-42"})
        self.assertEqual(a["value"], 77_777)
        a = q.answer_question(r, "q1", {"contract": "ABC-42"})
        self.assertEqual(a["value"], "2030-07-05")

    def test_unknown_question_id(self):
        a = q.answer_question(self.r, "unknown_q", {"contract": "STN-101"})
        self.assertEqual(a["status"], "error")
        self.assertIn("Неизвестный вопрос", a["answer"])

    def test_as_of_changes_answer(self):
        r = resolution.analyze(load_samples(), as_of=date(2026, 2, 15))
        a = q.answer_question(r, "q1", {"contract": "STN-101"})
        self.assertEqual(a["value"], "2026-04-15")   # соглашение ещё не действует
        self.assertEqual(len(a["pending"]), 1)      # но показано как предстоящее


if __name__ == "__main__":
    unittest.main()
