"""Test TC-15: Conflicting amendments should trigger needs_review warning."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import date
from app import resolution
from tests.synthetic import make_contract, make_amendment, to_files


class TestConflictingAmendments(unittest.TestCase):
    def test_two_conflicting_amendments_same_effective_date(self):
        """Two signed amendments with same effective date but different values
        should trigger needs_review, not silently apply last-write-wins."""
        contract = make_contract("STN-102", amount="840 000", delivery="20.03.2026")
        # Two amendments, same effective date (02.03.2026), different delivery dates
        amend1 = make_amendment(
            agreement_no=1, contract_no="STN-102",
            new_amount="840 000",
            new_delivery="25.03.2026",
            effective="02.03.2026",
            doc_date="02.03.2026",
            signed=True,
        )
        amend2 = make_amendment(
            agreement_no=1, contract_no="STN-102",
            new_amount="840 000",
            new_delivery="28.03.2026",
            effective="02.03.2026",
            doc_date="02.03.2026",
            signed=True,
        )
        files = to_files(
            ("contract.txt", contract),
            ("amend1.txt", amend1),
            ("amend2.txt", amend2),
        )
        result = resolution.analyze(files, as_of=date(2026, 3, 10))

        # Find STN-102 contract
        c102 = [c for c in result.contracts if c.number == "STN-102"][0]

        # The delivery_date field should have needs_review status
        # because two amendments conflict
        dd = c102.fields["delivery_date"]
        self.assertEqual(dd.status, "needs_review",
                         "Two conflicting amendments should trigger needs_review")
        self.assertIsNotNone(dd.review_reason)
        self.assertIn("Конфликт", dd.review_reason)

    def test_two_amendments_different_dates_no_conflict(self):
        """Two amendments with different effective dates should NOT conflict."""
        contract = make_contract("STN-102", amount="840 000", delivery="20.03.2026")
        amend1 = make_amendment(
            agreement_no=1, contract_no="STN-102",
            new_amount="900 000",
            new_delivery="25.03.2026",
            effective="01.03.2026",
            doc_date="01.03.2026",
            signed=True,
        )
        amend2 = make_amendment(
            agreement_no=2, contract_no="STN-102",
            new_amount="950 000",
            new_delivery="28.03.2026",
            effective="05.03.2026",
            doc_date="05.03.2026",
            signed=True,
        )
        files = to_files(
            ("contract.txt", contract),
            ("amend1.txt", amend1),
            ("amend2.txt", amend2),
        )
        result = resolution.analyze(files, as_of=date(2026, 3, 10))

        c102 = [c for c in result.contracts if c.number == "STN-102"][0]
        dd = c102.fields["delivery_date"]

        # Should be "changed" (last amendment wins), not "needs_review"
        self.assertEqual(dd.status, "changed")
        self.assertEqual(dd.value, "2026-03-28")  # Last amendment's value


if __name__ == "__main__":
    unittest.main()
