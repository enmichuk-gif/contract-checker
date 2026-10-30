"""Tests for E2E validation cases TC-03, TC-08, TC-11, TC-13, TC-14."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import date
from app import resolution
from tests.synthetic import load_samples

AS_OF = date(2026, 3, 10)


class TestTC03(unittest.TestCase):
    """TC-03: STN-101 amount=1 380 000 and delivery_date=30.04.2026."""

    def test_stn101_amount_from_amendment(self):
        r = resolution.analyze(load_samples(), as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        self.assertEqual(c101.fields["amount"].value, 1_380_000)
        self.assertEqual(c101.fields["amount"].status, "changed")

    def test_stn101_delivery_date_present(self):
        r = resolution.analyze(load_samples(), as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        dd = c101.fields["delivery_date"]
        self.assertEqual(dd.status, "changed")
        self.assertEqual(dd.value, "2026-04-30")
        self.assertIn("30.04.2026", dd.display)


class TestTC08(unittest.TestCase):
    """TC-08: Evidence for STN-101 amount includes exact quote from DS."""

    def test_amount_history_has_amendment_quote(self):
        r = resolution.analyze(load_samples(), as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        amt = c101.fields["amount"]
        self.assertEqual(len(amt.history), 2)
        amend_step = amt.history[1]
        self.assertEqual(amend_step.source_file, "02_amendment_signed.txt")
        self.assertIn("1 380 000", amend_step.evidence.quote)


class TestTC11(unittest.TestCase):
    """TC-11: Mutation - amount in DS changed -> result recalculates."""

    def test_mutated_amount_reflected(self):
        samples = load_samples()
        modified = []
        for name, raw in samples:
            if name == "02_amendment_signed.txt":
                text = raw.decode("utf-8")
                text = text.replace("1 380 000", "1 390 000")
                modified.append((name, text.encode("utf-8")))
            else:
                modified.append((name, raw))
        r = resolution.analyze(modified, as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        self.assertEqual(c101.fields["amount"].value, 1_390_000)
        self.assertEqual(c101.fields["delivery_date"].value, "2026-04-30")


class TestTC13(unittest.TestCase):
    """TC-13: Without signed DS, STN-101 reverts to contract values."""

    def test_no_amendment_returns_contract_values(self):
        samples = load_samples()
        filtered = [(n, r) for n, r in samples if n != "02_amendment_signed.txt"]
        r = resolution.analyze(filtered, as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        self.assertEqual(c101.fields["amount"].value, 1_200_000)
        self.assertEqual(c101.fields["amount"].status, "ok")
        self.assertEqual(c101.fields["delivery_date"].value, "2026-04-15")
        self.assertEqual(c101.fields["delivery_date"].status, "ok")


class TestTC14(unittest.TestCase):
    """TC-14: Future amendment not applied."""

    def test_future_amendment_not_applied(self):
        samples = load_samples()
        modified = []
        for name, raw in samples:
            if name == "02_amendment_signed.txt":
                text = raw.decode("utf-8")
                text = text.replace("вступает в силу 01.03.2026", "вступает в силу 20.03.2026")
                modified.append((name, text.encode("utf-8")))
            else:
                modified.append((name, raw))
        r = resolution.analyze(modified, as_of=AS_OF)
        c101 = [c for c in r.contracts if c.number == "STN-101"][0]
        self.assertEqual(c101.fields["amount"].value, 1_200_000)
        self.assertEqual(c101.fields["delivery_date"].value, "2026-04-15")
        self.assertTrue(
            len(c101.fields["amount"].pending) >= 1 or 
            len(c101.fields["delivery_date"].pending) >= 1
        )


if __name__ == "__main__":
    unittest.main()

