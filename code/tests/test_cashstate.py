"""Cash-state semantics: one case per status x direction, plus the collapses (TASKS.md M3)."""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import loaders, paths
from buyorwait.cashstate import (
    FORWARD,
    HISTORICAL,
    IGNORED,
    disposition,
    explicit_movements,
    settled_history,
    sign_of,
)
from buyorwait.fx import RateTable
from buyorwait.records import Event

DATASET = paths.find_dataset()
SETTLE = dt.date(2025, 6, 10)
RATES = RateTable({})


def _event(event_id="event_1", **overrides) -> Event:
    base = dict(
        event_id=event_id,
        user_id="user_1",
        event_type="expense",
        description="Something",
        category="shopping",
        direction="debit",
        amount=100.0,
        currency="EUR",
        event_date=dt.date(2025, 6, 5),
        settlement_date=SETTLE,
        status="settled",
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
    )
    base.update(overrides)
    return Event(**base)


class TestStatusByDirection(unittest.TestCase):
    """One case per status and direction, as the gate requires."""

    def test_settled_debit_and_credit_are_historical(self):
        for direction in ("debit", "credit"):
            with self.subTest(direction=direction):
                self.assertEqual(disposition(_event(direction=direction)).code, HISTORICAL)

    def test_pending_debit_is_reserved_on_its_settlement_date(self):
        verdict = disposition(_event(status="pending", direction="debit"))
        self.assertEqual(verdict.code, FORWARD)
        self.assertEqual(verdict.sign, -1)
        movements = explicit_movements(
            [_event(status="pending", direction="debit", event_date=dt.date(2025, 6, 1))],
            home_currency="EUR",
            rates=RATES,
        )
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].when, SETTLE)  # not the event date
        self.assertEqual(movements[0].amount, -100.0)

    def test_pending_credit_is_ignored(self):
        verdict = disposition(_event(status="pending", direction="credit"))
        self.assertEqual(verdict.code, IGNORED)
        self.assertEqual(
            explicit_movements(
                [_event(status="pending", direction="credit")], home_currency="EUR", rates=RATES
            ),
            (),
        )

    def test_scheduled_debit_is_applied(self):
        verdict = disposition(_event(status="scheduled", direction="debit"))
        self.assertEqual((verdict.code, verdict.sign), (FORWARD, -1))

    def test_scheduled_credit_is_applied_as_confirmed_income(self):
        verdict = disposition(
            _event(status="scheduled", direction="credit", event_type="income", category="salary")
        )
        self.assertEqual((verdict.code, verdict.sign), (FORWARD, 1))
        movements = explicit_movements(
            [_event(status="scheduled", direction="credit", event_type="income", amount=2500.0)],
            home_currency="EUR",
            rates=RATES,
        )
        self.assertEqual(movements[0].amount, 2500.0)

    def test_cancelled_failed_and_unrealized_never_move_cash(self):
        for status in ("cancelled", "failed", "unrealized"):
            for direction in ("debit", "credit"):
                with self.subTest(status=status, direction=direction):
                    self.assertEqual(disposition(_event(status=status, direction=direction)).code, IGNORED)

    def test_non_cash_never_moves_cash_whatever_the_status(self):
        for status in ("settled", "pending", "scheduled", "unrealized"):
            with self.subTest(status=status):
                self.assertEqual(disposition(_event(status=status, direction="non_cash")).code, IGNORED)

    def test_direction_carries_the_sign(self):
        self.assertEqual((sign_of("debit"), sign_of("credit"), sign_of("non_cash")), (-1, 1, 0))


class TestCollapses(unittest.TestCase):
    def test_a_settled_reversal_pair_nets_to_zero(self):
        expense = _event("event_1", direction="debit", amount=583.0)
        refund = _event(
            "event_2", event_type="refund", direction="credit", amount=583.0, linked_event_id="event_1"
        )
        index = {e.event_id: e for e in (expense, refund)}
        self.assertEqual(disposition(refund, index).code, HISTORICAL)
        self.assertIn("nets to zero", disposition(refund, index).reason)
        # Neither half reaches the forward curve, so the net effect is zero by construction.
        self.assertEqual(explicit_movements([expense, refund], home_currency="EUR", rates=RATES), ())

    def test_an_authorisation_settled_by_its_linked_row_is_not_reserved_twice(self):
        """The authorisation precedes the settlement; the pair is one movement, not two."""
        auth = _event(
            "event_2",
            status="pending",
            direction="debit",
            amount=250.0,
            event_date=dt.date(2025, 6, 2),
            settlement_date=dt.date(2025, 6, 12),
            linked_event_id="event_1",
        )
        settlement = _event(
            "event_1",
            status="settled",
            direction="debit",
            amount=250.0,
            event_date=dt.date(2025, 6, 2),
            settlement_date=dt.date(2025, 6, 4),
        )
        index = {e.event_id: e for e in (auth, settlement)}
        self.assertEqual(disposition(auth, index).code, IGNORED)
        self.assertEqual(explicit_movements([settlement, auth], home_currency="EUR", rates=RATES), ())

    def test_a_later_duplicate_charge_is_still_reserved(self):
        """The dataset's 'Possible duplicate card charge' shape: a second charge, not a re-sighting.

        It links *back* to an original settled eleven days earlier, so the ordering test
        that identifies an authorisation does not match it. The bank message on every one of
        these rows says the dispute is open with no reversal posted, so reserving it is both
        the evidenced and the financially safer reading.
        """
        original = _event(
            "event_1",
            status="settled",
            direction="debit",
            amount=134.75,
            event_date=dt.date(2026, 3, 26),
            settlement_date=dt.date(2026, 3, 27),
            description="Original card charge",
        )
        duplicate = _event(
            "event_2",
            status="pending",
            direction="debit",
            amount=134.75,
            event_date=dt.date(2026, 4, 6),
            settlement_date=dt.date(2026, 4, 10),
            linked_event_id="event_1",
            description="Possible duplicate card charge",
        )
        index = {e.event_id: e for e in (original, duplicate)}
        self.assertEqual(disposition(duplicate, index).code, FORWARD)
        movements = explicit_movements([original, duplicate], home_currency="EUR", rates=RATES)
        self.assertEqual([(m.event_id, m.amount) for m in movements], [("event_2", -134.75)])

    def test_a_scheduled_retry_after_a_failed_payment_applies_once(self):
        failed = _event("event_1", status="failed", direction="debit", amount=13800.0, event_type="debt_payment")
        retry = _event(
            "event_2",
            status="scheduled",
            direction="debit",
            amount=13800.0,
            event_type="debt_payment",
            linked_event_id="event_1",
        )
        movements = explicit_movements([failed, retry], home_currency="EUR", rates=RATES)
        self.assertEqual([(m.event_id, m.amount) for m in movements], [("event_2", -13800.0)])


class TestInvestmentLifecycle(unittest.TestCase):
    """The brief calls this out separately; the status rules already cover it."""

    def test_purchases_and_sales_move_cash_but_valuations_do_not(self):
        purchase = _event("event_1", event_type="investment_purchase", direction="debit", category="investment")
        sale = _event("event_2", event_type="investment_sale", direction="credit", category="investment")
        valuation = _event(
            "event_3",
            event_type="investment_valuation",
            direction="non_cash",
            status="unrealized",
            settlement_date=None,
            linked_event_id="event_1",
        )
        self.assertEqual(disposition(purchase).code, HISTORICAL)
        self.assertEqual(disposition(sale).code, HISTORICAL)
        self.assertEqual(disposition(valuation).code, IGNORED)

    def test_unrealized_value_is_not_available_cash_even_when_scheduled_looking(self):
        valuation = _event("event_3", event_type="investment_valuation", direction="non_cash", status="unrealized")
        self.assertEqual(
            explicit_movements([valuation], home_currency="EUR", rates=RATES), ()
        )


class TestForeignCurrencyMovements(unittest.TestCase):
    def test_a_movement_is_converted_at_its_settlement_date(self):
        rates = RateTable({(SETTLE, "USD", "INR"): 83.33})
        event = _event(status="pending", direction="debit", currency="USD", amount=10.0)
        movements = explicit_movements([event], home_currency="INR", rates=rates)
        self.assertAlmostEqual(movements[0].amount, -833.3)

    def test_a_blank_amount_is_skipped_rather_than_treated_as_zero(self):
        event = _event(status="scheduled", direction="debit", amount=None)
        self.assertEqual(explicit_movements([event], home_currency="EUR", rates=RATES), ())

    def test_a_recovered_amount_is_used_where_the_event_is_blank(self):
        event = _event(status="scheduled", direction="debit", amount=None)
        movements = explicit_movements(
            [event], home_currency="EUR", rates=RATES, amounts={"event_1": 1200.0}
        )
        self.assertEqual(movements[0].amount, -1200.0)


class TestAgainstTheRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.rates = RateTable(cls.data.rates)

    def test_the_documented_status_counts_classify_as_documented(self):
        counts = {}
        index = self.data.events_by_id
        for event in index.values():
            verdict = disposition(event, index)
            counts[(event.status, event.direction, verdict.code)] = (
                counts.get((event.status, event.direction, verdict.code), 0) + 1
            )
        self.assertEqual(counts[("pending", "debit", FORWARD)], 63)
        self.assertEqual(counts[("pending", "credit", IGNORED)], 8)
        self.assertEqual(counts[("scheduled", "debit", FORWARD)], 23)
        self.assertEqual(counts[("scheduled", "credit", FORWARD)], 47)
        self.assertEqual(counts[("cancelled", "debit", IGNORED)], 22)
        self.assertEqual(counts[("failed", "debit", IGNORED)], 21)
        self.assertEqual(counts[("unrealized", "non_cash", IGNORED)], 10)
        self.assertEqual(counts[("settled", "debit", HISTORICAL)], 23480)

    def test_every_forward_movement_falls_after_its_request_date(self):
        """Confirms assumption 2: the balance is complete as of request_date."""
        for request in self.data.requests + self.data.samples:
            profile = self.data.profiles[request.user_id]
            movements = explicit_movements(
                self.data.events(request.user_id),
                home_currency=profile.home_currency,
                rates=self.rates,
                events_by_id=self.data.events_by_id,
            )
            for movement in movements:
                self.assertGreater(movement.when, request.request_date, movement.event_id)

    def test_all_scheduled_credits_are_salary(self):
        """Assumption 3 treats them as confirmed income; that rests on this being true."""
        scheduled_credits = [
            e for e in self.data.events_by_id.values() if e.status == "scheduled" and e.direction == "credit"
        ]
        self.assertEqual(len(scheduled_credits), 47)
        self.assertEqual({e.category for e in scheduled_credits}, {"salary"})

    def test_settled_history_excludes_non_cash_rows(self):
        history = settled_history(self.data.events_by_id.values())
        self.assertTrue(all(e.status == "settled" for e in history))
        self.assertTrue(all(e.direction != "non_cash" for e in history))
        self.assertEqual(len(history), 25148)


if __name__ == "__main__":
    unittest.main()
