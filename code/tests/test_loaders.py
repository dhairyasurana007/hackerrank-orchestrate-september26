"""Loaders: every column parses, every meaningful blank is preserved, bad rows fail loudly
(TASKS.md M1).
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from buyorwait import loaders, paths
from buyorwait.records import DatasetError

DATASET = paths.find_dataset()


def _csv(tmp: Path, name: str, header: str, *rows: str) -> Path:
    path = tmp / name
    path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8")
    return path


class TestRealDatasetParses(unittest.TestCase):
    """Every column of every file, against the dataset the run will actually read."""

    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)

    def test_every_file_loads_at_its_known_size(self):
        self.assertEqual(len(self.data.profiles), 275)
        self.assertEqual(len(self.data.events_by_id), 25342)
        self.assertEqual(len(self.data.requests), 250)
        self.assertEqual(len(self.data.samples), 25)
        self.assertEqual(len(self.data.sample_truth), 25)
        self.assertEqual(len(self.data.rates), 134)
        self.assertEqual(len(self.data.images_by_event), 16)
        self.assertEqual(sum(len(m) for m in self.data.messages_by_user.values()), 215)
        self.assertEqual(sum(len(o) for o in self.data.options_by_request.values()), 790)

    def test_every_request_has_two_to_four_options_exactly_one_of_them_full_payment(self):
        for request in self.data.requests:
            options = self.data.options(request.request_id)
            self.assertIn(len(options), (2, 3, 4), request.request_id)
            full = [o for o in options if o.payment_method == "full_payment"]
            self.assertEqual(len(full), 1, request.request_id)

    def test_blank_amount_is_none_and_never_zero(self):
        blank = [e for e in self.data.events_by_id.values() if e.amount is None]
        self.assertEqual(len(blank), 16)
        self.assertTrue(all(e.event_id in self.data.images_by_event for e in blank))

    def test_blank_max_installment_months_is_none_and_suppresses_installments(self):
        blank = [p for p in self.data.profiles.values() if p.max_installment_months is None]
        self.assertEqual(len(blank), 119)
        self.assertTrue(all(not p.considers_installments for p in blank))

    def test_blank_minimum_allowed_amount_is_none(self):
        present = [e for e in self.data.events_by_id.values() if e.minimum_allowed_amount is not None]
        self.assertEqual(len(present), 2907)

    def test_blank_linked_event_id_is_none_and_every_link_resolves(self):
        linked = [e for e in self.data.events_by_id.values() if e.linked_event_id]
        self.assertEqual(len(linked), 58)
        for event in linked:
            self.assertIn(event.linked_event_id, self.data.events_by_id, event.event_id)

    def test_blank_settlement_date_falls_back_to_the_event_date(self):
        blank = [e for e in self.data.events_by_id.values() if e.settlement_date is None]
        self.assertEqual(len(blank), 10)
        for event in blank:
            self.assertEqual(event.cash_date, event.event_date)
            # All ten are non-cash portfolio valuations, which is why they have no settlement.
            self.assertEqual(event.direction, "non_cash")

    def test_empty_and_single_element_pipe_lists_both_parse(self):
        empty = [p for p in self.data.profiles.values() if not p.categories_willing_to_reduce]
        self.assertEqual(len(empty), 39)
        self.assertEqual(len([p for p in self.data.profiles.values() if not p.categories_willing_to_stop]), 62)
        single = self.data.profiles["user_01"]
        self.assertEqual(single.categories_willing_to_reduce, ("dining",))
        self.assertEqual(single.payment_methods_user_will_consider, ("full_payment",))
        multi = self.data.profiles["user_02"]
        self.assertEqual(multi.payment_methods_user_will_consider, ("partial_payment", "installments"))

    def test_message_blanks_distinguish_user_level_from_request_level(self):
        messages = [m for group in self.data.messages_by_user.values() for m in group]
        self.assertEqual(len([m for m in messages if m.request_id is None]), 87)
        self.assertEqual(len([m for m in messages if m.related_event_id is None]), 176)

    def test_timestamps_and_dates_are_typed(self):
        message = self.data.messages_by_user["user_02"][0]
        self.assertIsInstance(message.sent_at, dt.datetime)
        self.assertIsInstance(message.sent_on, dt.date)
        self.assertIsInstance(self.data.request("request_01").request_date, dt.date)

    def test_image_ids_resolve_to_existing_png_files(self):
        for image in self.data.images_by_event.values():
            self.assertTrue(image.path(DATASET).is_file(), image.image_id)
        self.assertEqual(
            self.data.images_by_event["event_253"].path(DATASET).name,
            "image_01.png",
        )

    def test_payment_option_schedule_matches_the_solved_sample(self):
        option = next(
            o for o in self.data.options("request_02") if o.payment_method == "installments" and o.number_of_payments == 3
        )
        self.assertEqual(
            option.schedule(),
            (
                (dt.date(2025, 8, 8), 15952906.67),
                (dt.date(2025, 9, 7), 15952906.67),
                (dt.date(2025, 10, 7), 15952906.67),
            ),
        )

    def test_events_are_ordered_by_the_date_cash_moves(self):
        for events in self.data.events_by_user.values():
            dates = [e.cash_date for e in events]
            self.assertEqual(dates, sorted(dates))


class TestMalformedRowsFailLoudly(unittest.TestCase):
    """A bad row must raise, not default. Each case is one deliberate corruption."""

    def test_unrecognised_status_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "financial_events.csv",
                "event_id,user_id,event_type,description,category,direction,amount,currency,"
                "event_date,settlement_date,status,linked_event_id,flexibility,minimum_allowed_amount",
                "event_01,user_01,expense,Rent,rent,debit,100,ZAR,2024-01-01,2024-01-01,"
                "probably_settled,,fixed,",
            )
            with self.assertRaises(DatasetError) as caught:
                loaders.load_events(path)
        self.assertIn("status", str(caught.exception))

    def test_non_numeric_amount_raises_rather_than_becoming_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "financial_events.csv",
                "event_id,user_id,event_type,description,category,direction,amount,currency,"
                "event_date,settlement_date,status,linked_event_id,flexibility,minimum_allowed_amount",
                "event_01,user_01,expense,Rent,rent,debit,about 100,ZAR,2024-01-01,2024-01-01,"
                "settled,,fixed,",
            )
            with self.assertRaises(DatasetError):
                loaders.load_events(path)

    def test_malformed_date_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "requests.csv",
                "request_id,user_id,request_date,request_type,requested_amount,"
                "desired_completion_date,allows_partial_payment,request_text",
                "request_01,user_01,03/03/2024,purchase,100,2024-03-20,true,text",
            )
            with self.assertRaises(DatasetError) as caught:
                loaders.load_requests(path)
        self.assertIn("request_date", str(caught.exception))

    def test_blank_required_number_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "financial_profiles.csv",
                "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
                "financial_priorities,expense_categories_to_protect,"
                "expense_categories_user_is_willing_to_reduce,"
                "expense_categories_user_is_willing_to_stop,payment_methods_user_will_consider,"
                "max_installment_months",
                "user_01,ZAR,,18000,education,rent,dining,gym,full_payment,",
            )
            with self.assertRaises(DatasetError) as caught:
                loaders.load_profiles(path)
        self.assertIn("current_available_balance", str(caught.exception))

    def test_non_boolean_allows_partial_payment_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "requests.csv",
                "request_id,user_id,request_date,request_type,requested_amount,"
                "desired_completion_date,allows_partial_payment,request_text",
                "request_01,user_01,2024-03-03,purchase,100,2024-03-20,yes,text",
            )
            with self.assertRaises(DatasetError):
                loaders.load_requests(path)

    def test_multi_payment_option_without_a_frequency_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(
                Path(tmp),
                "request_payment_options.csv",
                "payment_option_id,request_id,payment_method,payment_amount,number_of_payments,"
                "first_payment_date,payment_frequency_days,financing_fee,total_payable_amount",
                "payment_option_01,request_01,installments,10,3,2024-03-06,,1,30",
            )
            with self.assertRaises(DatasetError) as caught:
                loaders.load_payment_options(path)
        self.assertIn("payment_frequency_days", str(caught.exception))

    def test_a_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DatasetError):
                loaders.load_events(Path(tmp) / "nope.csv")

    def test_an_event_for_an_unknown_user_raises(self):
        """Cross-file integrity, not just per-row shape."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in paths.DATASET_FILES:
                (root / name).write_bytes((DATASET / name).read_bytes())
            events = (root / "financial_events.csv").read_text(encoding="utf-8").splitlines()
            events.append(
                "event_99999,user_ghost,expense,Rent,rent,debit,100,ZAR,2024-01-01,2024-01-01,settled,,fixed,"
            )
            (root / "financial_events.csv").write_text("\n".join(events) + "\n", encoding="utf-8")
            with self.assertRaises(DatasetError) as caught:
                loaders.load_dataset(root)
        self.assertIn("user_ghost", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
