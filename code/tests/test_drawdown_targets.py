"""Drawdown target derivation (TASKS.md M5a).

The last test here is the one that keeps this piece genuinely independent: the module must
import and run with no forecaster present at all.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from buyorwait import loaders, paths
from evaluation import drawdown_targets as dt

DATASET = paths.find_dataset()


class TestTheKnownSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.targets = dt.targets_from(cls.data)

    def test_the_split_is_21_exact_and_4_capped(self):
        exact, capped = dt.split(self.targets)
        self.assertEqual(len(self.targets), 25)
        self.assertEqual(len(exact), 21)
        self.assertEqual(len(capped), 4)

    def test_the_four_capped_samples_are_the_documented_ones(self):
        _, capped = dt.split(self.targets)
        self.assertEqual(
            sorted(t.request_id for t in capped),
            ["request_01", "request_09", "request_12", "request_16"],
        )

    def test_a_sample_is_capped_exactly_when_the_safe_amount_equals_the_request(self):
        for target in self.targets:
            with self.subTest(request=target.request_id):
                equal = abs(target.amount_safe_to_pay - target.requested_amount) < 1e-9
                self.assertEqual(equal, not target.is_exact)

    def test_targets_match_hand_computed_values_across_currencies(self):
        """Three samples in three currencies, each arithmetic checked by hand."""
        by_id = {t.request_id: t for t in self.targets}
        # ZAR: 58481.10 - (25256 + 18000)
        self.assertAlmostEqual(by_id["request_01"].target_drawdown, 15225.10, places=2)
        # IDR: 60383889.20 - (17229139.20 + 29158400)
        self.assertAlmostEqual(by_id["request_02"].target_drawdown, 13996350.00, places=2)
        # EUR: 2789.52 - (433.40 + 1300)
        self.assertAlmostEqual(by_id["request_13"].target_drawdown, 1056.12, places=2)
        # INR: 362370 - (122500 + 122400), a capped sample
        self.assertAlmostEqual(by_id["request_16"].target_drawdown, 117470.00, places=2)

    def test_every_target_is_positive_which_confirms_the_balance_is_not_stale(self):
        """Assumption 2: amount_safe_to_pay + min_keep <= balance, with strict slack."""
        for target in self.targets:
            with self.subTest(request=target.request_id):
                self.assertGreater(target.target_drawdown, 0.0)


class TestErrorAndBoundSemantics(unittest.TestCase):
    def _target(self, kind, target=1000.0):
        return dt.DrawdownTarget(
            request_id="request_x",
            user_id="user_x",
            currency="EUR",
            opening_balance=5000.0,
            minimum_balance_to_keep=1000.0,
            amount_safe_to_pay=3000.0,
            requested_amount=3000.0 if kind == dt.CAPPED else 9000.0,
            target_drawdown=target,
            kind=kind,
        )

    def test_relative_error_is_signed_and_scaled_by_the_target(self):
        target = self._target(dt.EXACT, target=1000.0)
        self.assertAlmostEqual(target.relative_error(1200.0), 0.2)
        self.assertAlmostEqual(target.relative_error(800.0), -0.2)
        self.assertAlmostEqual(target.relative_error(1000.0), 0.0)

    def test_a_zero_target_falls_back_to_an_absolute_comparison(self):
        target = self._target(dt.EXACT, target=0.0)
        self.assertAlmostEqual(target.relative_error(50.0), 50.0)

    def test_a_capped_sample_is_only_bound_checked(self):
        target = self._target(dt.CAPPED, target=1000.0)
        self.assertFalse(target.violates_bound(999.0))
        self.assertFalse(target.violates_bound(1000.0))
        self.assertTrue(target.violates_bound(1000.5))


class TestIndependenceFromTheForecaster(unittest.TestCase):
    """The assertion that keeps M5a unblocked by M7."""

    def test_importing_the_module_does_not_pull_in_a_forecaster(self):
        # A subprocess, because the rest of the suite may already have imported it.
        script = (
            "import sys; sys.path.insert(0, %r);"
            "from evaluation import drawdown_targets;"
            "assert not [m for m in sys.modules if 'forecast' in m], "
            "[m for m in sys.modules if 'forecast' in m];"
            "print('clean')" % str(paths.code_dir())
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_targets_derive_with_no_forecaster_argument_at_all(self):
        data = loaders.load_dataset(DATASET)
        self.assertEqual(len(dt.targets_from(data)), 25)


if __name__ == "__main__":
    unittest.main()
