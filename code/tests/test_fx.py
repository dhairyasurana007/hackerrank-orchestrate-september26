"""Dated FX normalisation (TASKS.md M2)."""

from __future__ import annotations

import datetime as dt
import itertools
import unittest

from buyorwait import loaders, paths
from buyorwait.fx import MissingRateError, RateTable
from buyorwait.records import CURRENCIES, Event

DATASET = paths.find_dataset()
DAY = dt.date(2024, 1, 15)
OTHER_DAY = dt.date(2024, 2, 15)


def _event(**overrides) -> Event:
    base = dict(
        event_id="event_x",
        user_id="user_x",
        event_type="expense",
        description="Something",
        category="shopping",
        direction="debit",
        amount=100.0,
        currency="USD",
        event_date=dt.date(2024, 1, 10),
        settlement_date=DAY,
        status="settled",
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
    )
    base.update(overrides)
    return Event(**base)


class _CountingTable(RateTable):
    """Records lookups, so 'performs no lookup' can be asserted rather than assumed."""

    def __init__(self, rates):
        super().__init__(rates)
        self.lookups = 0

    def rate(self, from_currency, to_currency, on):
        self.lookups += 1
        return super().rate(from_currency, to_currency, on)


class TestSameCurrency(unittest.TestCase):
    def test_passthrough_performs_no_lookup(self):
        table = _CountingTable({})
        self.assertEqual(table.convert(1234.56, "ZAR", "ZAR", DAY), 1234.56)
        self.assertEqual(table.lookups, 0)

    def test_parity_is_returned_without_a_rate_row(self):
        self.assertEqual(RateTable({}).rate("EUR", "EUR", DAY), 1.0)


class TestAllFiveCurrencies(unittest.TestCase):
    """Every ordered pair converts when the table states that direction.

    The real dataset only ever needs five directions, so this uses a fixture supplying all
    twenty — which is the only way to assert full coverage without inverting or chaining,
    both of which this module deliberately refuses to do.
    """

    def setUp(self):
        self.factors = {
            ("EUR", "USD"): 1.09,
            ("USD", "EUR"): 0.92,
            ("EUR", "ZAR"): 20.0,
            ("USD", "IDR"): 15833.33,
            ("USD", "INR"): 83.33,
        }
        rates = {}
        for index, (source, target) in enumerate(itertools.permutations(CURRENCIES, 2), start=2):
            rates[(DAY, source, target)] = self.factors.get((source, target), float(index))
        self.table = RateTable(rates)

    def test_every_ordered_pair_converts(self):
        for source, target in itertools.permutations(CURRENCIES, 2):
            with self.subTest(pair=(source, target)):
                self.assertGreater(self.table.convert(10.0, source, target, DAY), 0)

    def test_the_dataset_factors_are_applied_as_stated(self):
        self.assertAlmostEqual(self.table.convert(100, "EUR", "ZAR", DAY), 2000.0)
        self.assertAlmostEqual(self.table.convert(100, "USD", "INR", DAY), 8333.0)
        self.assertAlmostEqual(self.table.convert(2, "USD", "IDR", DAY), 31666.66)


class TestDirectionAndDate(unittest.TestCase):
    def test_a_rate_is_never_inverted_to_serve_the_other_direction(self):
        table = RateTable({(DAY, "EUR", "ZAR"): 20.0})
        self.assertAlmostEqual(table.convert(5, "EUR", "ZAR", DAY), 100.0)
        with self.assertRaises(MissingRateError):
            table.convert(100, "ZAR", "EUR", DAY)

    def test_a_rate_is_never_chained_through_a_third_currency(self):
        table = RateTable({(DAY, "EUR", "USD"): 1.09, (DAY, "USD", "INR"): 83.33})
        with self.assertRaises(MissingRateError):
            table.convert(10, "EUR", "INR", DAY)

    def test_the_rate_is_selected_by_settlement_date_not_event_date(self):
        table = RateTable({(DAY, "USD", "INR"): 83.33, (OTHER_DAY, "USD", "INR"): 1.0})
        event = _event(event_date=OTHER_DAY, settlement_date=DAY, currency="USD", amount=10.0)
        # The event date has a rate of its own; picking it up would give 10.0.
        self.assertAlmostEqual(table.convert_event_amount(event, "INR"), 833.3)

    def test_a_settlement_date_with_no_rate_raises_even_when_the_event_date_has_one(self):
        table = RateTable({(OTHER_DAY, "USD", "INR"): 83.33})
        event = _event(event_date=OTHER_DAY, settlement_date=DAY)
        with self.assertRaises(MissingRateError):
            table.convert_event_amount(event, "INR")

    def test_a_missing_settlement_date_falls_back_to_the_event_date(self):
        table = RateTable({(OTHER_DAY, "USD", "EUR"): 0.92})
        event = _event(event_date=OTHER_DAY, settlement_date=None, amount=50.0)
        self.assertAlmostEqual(table.convert_event_amount(event, "EUR"), 46.0)


class TestMissingRatesFailLoudly(unittest.TestCase):
    def test_a_missing_rate_raises_rather_than_becoming_one(self):
        table = RateTable({})
        with self.assertRaises(MissingRateError):
            table.convert(100, "USD", "ZAR", DAY)

    def test_a_blank_amount_cannot_be_converted_silently(self):
        table = RateTable({(DAY, "USD", "EUR"): 0.92})
        with self.assertRaises(Exception):
            table.convert_event_amount(_event(amount=None), "EUR")

    def test_an_override_amount_converts_where_the_event_has_none(self):
        table = RateTable({(DAY, "USD", "EUR"): 0.92})
        self.assertAlmostEqual(table.convert_event_amount(_event(amount=None), "EUR", amount=200.0), 184.0)


class TestAgainstTheRealRateTable(unittest.TestCase):
    """Strict lookup must be sufficient for every conversion the dataset actually needs."""

    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)
        cls.table = RateTable(cls.data.rates)

    def test_every_foreign_currency_event_converts_without_a_missing_rate(self):
        foreign = [
            event
            for event in self.data.events_by_id.values()
            if event.currency != self.data.profiles[event.user_id].home_currency
        ]
        self.assertEqual(len(foreign), 140)
        # One of the 140 is a blank-amount event (event_7307, USD for an INR user), whose
        # figure has to come from evidence before it can be converted at all.
        priced = [event for event in foreign if event.amount is not None]
        self.assertEqual(len(priced), 139)
        self.assertEqual([e.event_id for e in foreign if e.amount is None], ["event_7307"])
        for event in priced:
            home = self.data.profiles[event.user_id].home_currency
            self.assertGreater(self.table.convert_event_amount(event, home), 0, event.event_id)

    def test_the_five_supplied_directions_are_the_only_ones_present(self):
        directions = {(source, target) for _, source, target in self.data.rates}
        self.assertEqual(
            directions,
            {("EUR", "ZAR"), ("USD", "EUR"), ("USD", "IDR"), ("USD", "INR"), ("EUR", "USD")},
        )


if __name__ == "__main__":
    unittest.main()
