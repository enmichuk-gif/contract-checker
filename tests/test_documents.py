"""Тесты классификации документов, статусов подписания и связывания."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import documents as docs
from tests.synthetic import make_amendment, make_contract, make_email, sample_text


class TestClassifySamples(unittest.TestCase):
    """Типы определяются по содержимому, а не по имени файла."""

    def test_sample_documents_classified(self):
        expected = {
            "01_contract.txt": "contract",
            "02_amendment_signed.txt": "amendment_signed",
            "03_contract.txt": "contract",
            "04_contract.txt": "contract",
            "05_supplier_email.txt": "email",
            "06_amendment_draft.txt": "amendment_draft",
        }
        for name, want in expected.items():
            meta = docs.parse_document(name, sample_text(name), "utf-8")
            self.assertEqual(meta.doc_type, want, f"{name}: тип {meta.doc_type}, ожидался {want}")

    def test_classification_ignores_file_names(self):
        # Те же содержимое под «случайными» именами — результат тот же.
        renames = {
            "01_contract.txt": "zzz_2020_final.txt",
            "02_amendment_signed.txt": "a b.txt",
            "05_supplier_email.txt": "документ_без_имени_1.txt",
            "06_amendment_draft.txt": "01_contract.txt",  # провокационное имя
        }
        for src, new_name in renames.items():
            meta = docs.parse_document(new_name, sample_text(src), "utf-8")
            orig = docs.parse_document(src, sample_text(src), "utf-8")
            self.assertEqual(meta.doc_type, orig.doc_type, src)
            self.assertEqual(meta.contract_no, orig.contract_no, src)

    def test_email_mentioning_amendment_is_email(self):
        # Письмо, в теле которого есть слова «дополнительное соглашение»,
        # должно остаться письмом (маркер заголовка приоритетнее).
        meta = docs.parse_document("x.txt", sample_text("05_supplier_email.txt"), "utf-8")
        self.assertEqual(meta.doc_type, "email")


class TestSignatureStatus(unittest.TestCase):
    def test_signed_contract(self):
        signed, status, signed_date = docs.parse_signature(sample_text("01_contract.txt"))
        self.assertTrue(signed)
        self.assertEqual(signed_date, "2026-02-05")
        self.assertIn("подписан", status)

    def test_signed_amendment_and_effective_date(self):
        meta = docs.parse_document("02.txt", sample_text("02_amendment_signed.txt"), "utf-8")
        self.assertEqual(meta.signed, True)
        self.assertEqual(meta.effective_date, "2026-03-01")
        self.assertEqual(meta.contract_no, "STN-101")
        self.assertEqual(meta.number, "1")

    def test_draft_is_unsigned(self):
        meta = docs.parse_document("06.txt", sample_text("06_amendment_draft.txt"), "utf-8")
        self.assertEqual(meta.signed, False)
        self.assertEqual(meta.contract_no, "STN-101")

    def test_unsigned_status_detected_before_positive(self):
        # «не подписан» должно распознаваться раньше, чем «подписан».
        signed, _, _ = docs.parse_signature("Статус: не подписан сторонами.")
        self.assertIs(signed, False)

    def test_unknown_signature_status(self):
        signed, _, _ = docs.parse_signature(make_contract("X-1").replace(
            "Статус: подписан обеими сторонами 01.02.2026.", "Статус: обсуждается."))
        self.assertIsNone(signed)


class TestRequisites(unittest.TestCase):
    def test_contract_number_and_parties(self):
        meta = docs.parse_document("01.txt", sample_text("01_contract.txt"), "utf-8")
        self.assertEqual(meta.number, "STN-101")
        self.assertEqual(meta.doc_date, "2026-02-05")
        supplier = docs.extract_party(sample_text("01_contract.txt"), "supplier")
        buyer = docs.extract_party(sample_text("01_contract.txt"), "buyer")
        self.assertEqual(supplier, "ООО «Вектор Прибор»")   # регистр сохранён
        self.assertEqual(buyer, "ООО «СеверМет»")

    def test_email_contract_reference(self):
        meta = docs.parse_document("05.txt", sample_text("05_supplier_email.txt"), "utf-8")
        self.assertEqual(meta.contract_no, "STN-102")

    def test_synthetic_documents(self):
        meta = docs.parse_document("s1.txt", make_contract("XYZ-777"), "utf-8")
        self.assertEqual(meta.doc_type, "contract")
        self.assertEqual(meta.number, "XYZ-777")
        meta = docs.parse_document("s2.txt", make_amendment(3, "XYZ-777", draft=True), "utf-8")
        self.assertEqual(meta.doc_type, "amendment_draft")
        self.assertEqual(meta.contract_no, "XYZ-777")
        meta = docs.parse_document("s3.txt", make_email("XYZ-777"), "utf-8")
        self.assertEqual(meta.doc_type, "email")
        self.assertEqual(meta.contract_no, "XYZ-777")

    def test_unrecognized_document(self):
        meta = docs.parse_document("w.txt", "Справка о погоде за март.\nСолнечно.", "utf-8")
        self.assertEqual(meta.doc_type, "unknown")


if __name__ == "__main__":
    unittest.main()
