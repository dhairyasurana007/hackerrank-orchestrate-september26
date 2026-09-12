"""Deterministic explanation templates (TASKS.md M16).

The grounding class is the load-bearing one: no template may emit a number it was not given.
``explain.figures_in`` re-reads the numbers back out of the rendered sentence, so that is
asserted rather than reviewed — and the same function is what will let FINAL's polish step
decide whether rewritten text may replace a template.
"""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import explain, loaders, paths
from buyorwait.pipeline import Engine

DATASET = paths.find_dataset()
DAY = dt.date(2025, 8, 8)


class TestMoneyFormatting(unittest.TestCase):
    """Thousands separators, and decimals only when fractional — as the samples do."""

    def test_whole_amounts_carry_separators_and_no_decimals(self):
        self.assertEqual(explain.money("ZAR", 25256), "ZAR 25,256")
        self.assertEqual(explain.money("IDR", 29158400), "IDR 29,158,400")
        self.assertEqual(explain.money("EUR", 600.0), "EUR 600")
        self.assertEqual(explain.money("INR", 68432), "INR 68,432")

    def test_fractional_amounts_render_at_two_places_with_separators(self):
        self.assertEqual(explain.money("IDR", 15952906.67), "IDR 15,952,906.67")
        self.assertEqual(explain.money("EUR", 620.4), "EUR 620.40")
        self.assertEqual(explain.money("EUR", 996.6), "EUR 996.60")
        self.assertEqual(explain.money("USD", 23.5), "USD 23.50")
        self.assertEqual(explain.money("EUR", 5414.2), "EUR 5,414.20")

    def test_dates_render_without_a_leading_zero(self):
        self.assertEqual(explain.long_date(dt.date(2025, 8, 8)), "8 August 2025")
        self.assertEqual(explain.long_date(dt.date(2019, 11, 15)), "15 November 2019")
        self.assertEqual(explain.long_date(dt.date(2026, 1, 1)), "1 January 2026")


class TestEachFamilyRenders(unittest.TestCase):
    """Every family, against the exact sample sentence it is modelled on."""

    def test_affordable_now(self):
        self.assertEqual(
            explain.affordable_now(currency="ZAR", requested=25256, minimum=18000),
            "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days.",
        )

    def test_wait(self):
        self.assertEqual(
            explain.wait_until(
                currency="EUR", requested=996.6, minimum=800, when=dt.date(2025, 4, 15)
            ),
            "Pay EUR 996.60 in full on 15 April 2025. "
            "Paying earlier would take the balance below the EUR 800 minimum.",
        )

    def test_installments(self):
        self.assertEqual(
            explain.installments(
                currency="IDR",
                count=3,
                payment=15952906.67,
                minimum=29158400,
                start=dt.date(2025, 8, 8),
            ),
            "Use 3 installments of IDR 15,952,906.67, starting 8 August 2025. "
            "This leaves at least IDR 29,158,400 available.",
        )

    def test_partial_payment(self):
        self.assertEqual(
            explain.partial_payment(
                currency="INR",
                today=28820,
                remainder=10840,
                minimum=92800,
                when=dt.date(2024, 9, 15),
            ),
            "Pay INR 28,820 today and the remaining INR 10,840 on 15 September 2024. "
            "This completes the full request and keeps the INR 92,800 minimum protected.",
        )

    def test_not_affordable_by_deadline(self):
        self.assertEqual(
            explain.not_affordable_by_deadline(
                currency="ZAR", minimum=13100, deadline=dt.date(2026, 1, 12)
            ),
            "Do not make this payment by 12 January 2026. "
            "None of the available options keeps the ZAR 13,100 minimum protected.",
        )

    def test_not_affordable_when_partial_was_the_only_avenue(self):
        self.assertEqual(
            explain.not_affordable_partial_only(currency="EUR", requested=5414.2, available=597.74),
            "Do not proceed with the EUR 5,414.20 request. Although EUR 597.74 is available "
            "today, the full amount cannot be completed safely within 90 days.",
        )


class TestSpendingChangePhrasing(unittest.TestCase):
    class _Event:
        def __init__(self, description):
            self.description = description

    def test_one_stop(self):
        self.assertEqual(
            explain.with_spending_changes(
                currency="EUR",
                requested=620.4,
                minimum=800,
                action_texts=("stop:event_476",),
                changed_events=(self._Event("Family streaming plan"),),
            ),
            "Stop the family streaming plan, then pay EUR 620.40 today. "
            "This leaves at least EUR 800 available.",
        )

    def test_one_reduction(self):
        self.assertEqual(
            explain.with_spending_changes(
                currency="IDR",
                requested=13110000,
                minimum=34140600,
                action_texts=("reduce_to:event_989:665950",),
                changed_events=(self._Event("Weekend food delivery"),),
            ),
            "Reduce the weekend food delivery to IDR 665,950, then pay IDR 13,110,000 today. "
            "This leaves at least IDR 34,140,600 available.",
        )

    def test_a_stop_and_a_reduction_together(self):
        self.assertEqual(
            explain.with_spending_changes(
                currency="USD",
                requested=1574.4,
                minimum=1800,
                action_texts=("stop:event_1815", "reduce_to:event_1816:23.50"),
                changed_events=(
                    self._Event("Online backup subscription"),
                    self._Event("Streaming subscription"),
                ),
            ),
            "Stop the online backup subscription and reduce the streaming subscription to "
            "USD 23.50, then pay USD 1,574.40 today. This leaves at least USD 1,800 available.",
        )

    def test_three_actions_are_comma_joined_with_a_final_and(self):
        text = explain.with_spending_changes(
            currency="EUR",
            requested=100,
            minimum=50,
            action_texts=("stop:e1", "stop:e2", "stop:e3"),
            changed_events=(self._Event("A plan"), self._Event("B plan"), self._Event("C plan")),
        )
        self.assertTrue(text.startswith("Stop the a plan, stop the b plan and stop the c plan,"))


class TestGrounding(unittest.TestCase):
    """No template may emit a number it was not given."""

    def test_figures_in_reads_every_number_back_out(self):
        self.assertEqual(
            explain.figures_in("Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available."),
            {"25256", "18000"},
        )
        self.assertEqual(explain.figures_in("IDR 15,952,906.67"), {"15952906.67"})
        self.assertEqual(explain.figures_in("no numbers here"), set())

    def test_each_template_emits_only_its_inputs_plus_the_documented_constants(self):
        """The window length (90) and the installment count are the only bare numbers."""
        cases = [
            (explain.affordable_now(currency="ZAR", requested=25256, minimum=18000),
             {"25256", "18000", "90"}),
            (explain.wait_until(currency="EUR", requested=996.6, minimum=800, when=DAY),
             {"996.6", "800", "8", "2025"}),
            (explain.installments(currency="IDR", count=3, payment=15952906.67,
                                  minimum=29158400, start=DAY),
             {"3", "15952906.67", "29158400", "8", "2025"}),
            (explain.partial_payment(currency="INR", today=28820, remainder=10840,
                                     minimum=92800, when=DAY),
             {"28820", "10840", "92800", "8", "2025"}),
            (explain.not_affordable_by_deadline(currency="ZAR", minimum=13100, deadline=DAY),
             {"13100", "8", "2025"}),
            (explain.not_affordable_partial_only(currency="EUR", requested=5414.2, available=597.74),
             {"5414.2", "597.74", "90"}),
        ]
        for text, allowed in cases:
            with self.subTest(text=text[:40]):
                self.assertEqual(explain.figures_in(text), allowed)

    def test_no_emitted_row_contains_a_figure_the_engine_did_not_compute(self):
        """Asserted across all 250 rows, not only on fixtures."""
        data = loaders.load_dataset(DATASET)
        engine = Engine.build(data, use_llm=False)
        for request in data.requests:
            decision = engine.decision_for(request)
            profile = data.profiles[request.user_id]
            text = explain.render(decision, request, profile)
            allowed = {
                *explain.figures_in(explain.money("X", request.requested_amount)),
                *explain.figures_in(explain.money("X", profile.minimum_balance_to_keep)),
                *explain.figures_in(explain.money("X", decision.amount_safe_to_pay)),
                "90",
            }
            for when, amount in decision.chosen.payments:
                allowed |= explain.figures_in(explain.money("X", amount))
                allowed |= {str(when.day), str(when.year)}
            if decision.chosen.option is not None:
                allowed |= {str(decision.chosen.option.number_of_payments)}
                allowed |= explain.figures_in(
                    explain.money("X", decision.chosen.option.payment_amount)
                )
                allowed |= {
                    str(decision.chosen.option.first_payment_date.day),
                    str(decision.chosen.option.first_payment_date.year),
                }
            allowed |= {
                str(request.desired_completion_date.day),
                str(request.desired_completion_date.year),
            }
            for change in decision.chosen.spending_changes:
                if change.startswith("reduce_to:"):
                    allowed |= explain.figures_in(
                        explain.money("X", float(change.split(":")[2]))
                    )
            with self.subTest(request=request.request_id):
                self.assertEqual(
                    explain.figures_in(text) - allowed, set(), f"{request.request_id}: {text}"
                )


class TestAgainstTheSolvedSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.engine = Engine.build(cls.data, use_llm=False)

    def test_every_row_gets_a_non_empty_explanation(self):
        for request in self.data.requests:
            with self.subTest(request=request.request_id):
                self.assertTrue(self.engine.decide(request).decision_explanation.strip())

    def test_fifteen_of_the_25_sample_explanations_match_byte_for_byte(self):
        """A floor, not a target: it moves up as the decisions themselves get more right."""
        matches = sum(
            1
            for request_id, truth in self.data.sample_truth.items()
            if self.engine.decide(self.data.request(request_id)).decision_explanation
            == truth.decision_explanation
        )
        self.assertGreaterEqual(matches, 15)

    def test_the_partial_only_variant_fires_on_exactly_the_two_samples_that_use_it(self):
        """The separator inferred in the module docstring, checked against all seven rows."""
        expected = {"request_14", "request_24"}
        fired = set()
        for request_id, truth in self.data.sample_truth.items():
            if truth.recommended_payment_method != "not_recommended":
                continue
            request = self.data.request(request_id)
            profile = self.data.profiles[request.user_id]
            if (
                tuple(profile.payment_methods_user_will_consider) == ("partial_payment",)
                and request.allows_partial_payment
            ):
                fired.add(request_id)
        self.assertEqual(fired, expected)
        for request_id in expected:
            self.assertIn("Do not proceed with the", self.data.sample_truth[request_id].decision_explanation)

    def test_the_two_known_unmatched_variants_are_the_documented_ones(self):
        """request_04 and request_09 use a second wording nothing in the inputs separates."""
        for request_id in ("request_04", "request_09"):
            truth = self.data.sample_truth[request_id].decision_explanation
            ours = self.engine.decide(self.data.request(request_id)).decision_explanation
            with self.subTest(request=request_id):
                self.assertNotEqual(ours, truth)
                # Same figures, different sentence: the substance is right.
                self.assertEqual(explain.figures_in(ours), explain.figures_in(truth))


if __name__ == "__main__":
    unittest.main()
