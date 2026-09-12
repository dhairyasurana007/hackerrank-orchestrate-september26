"""Spending-change search (TASKS.md M10)."""

from __future__ import annotations

import datetime as dt
import unittest

from buyorwait import forecast, loaders, paths, recurrence, spending
from buyorwait.contract import MAX_SPENDING_CHANGES
from buyorwait.fx import RateTable
from buyorwait.records import Event, Profile, Request

DATASET = paths.find_dataset()
START = dt.date(2026, 1, 5)
RATES = RateTable({})


def _profile(*, protect=(), reduce=(), stop=(), methods=("full_payment",), minimum=1000.0,
            balance=10000.0) -> Profile:
    return Profile(
        user_id="user_x",
        home_currency="EUR",
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=("savings",),
        expense_categories_to_protect=tuple(protect),
        categories_willing_to_reduce=tuple(reduce),
        categories_willing_to_stop=tuple(stop),
        payment_methods_user_will_consider=tuple(methods),
        max_installment_months=None,
    )


def _request(requested=2000.0, completion_days=40) -> Request:
    return Request(
        request_id="request_x",
        user_id="user_x",
        request_date=START,
        request_type="purchase",
        requested_amount=requested,
        desired_completion_date=START + dt.timedelta(days=completion_days),
        allows_partial_payment=False,
        request_text="",
    )


def _event(event_id, category, amount, *, flexibility="fixed", minimum=None, currency="EUR") -> Event:
    return Event(
        event_id=event_id,
        user_id="user_x",
        event_type="subscription",
        description=f"{category} plan",
        category=category,
        direction="debit",
        amount=amount,
        currency=currency,
        event_date=START - dt.timedelta(days=25),
        settlement_date=START - dt.timedelta(days=25),
        status="settled",
        linked_event_id=None,
        flexibility=flexibility,
        minimum_allowed_amount=minimum,
    )


def _series(event, amount, *, projects=True) -> recurrence.Series:
    return recurrence.Series(
        key=("debit", event.category, ""),
        direction="debit",
        category=event.category,
        description_key="",
        cadence="monthly",
        interval_days=30,
        anchor_day=event.event_date.day,
        last_date=event.event_date,
        next_expected=event.event_date + dt.timedelta(days=30),
        amount=amount,
        occurrences=5,
        projects=projects,
        reason="recurring and ongoing" if projects else "not recurring",
        representative=event,
    )


def _curve(series_list, *, balance=10000.0, days=90):
    """A flat curve carrying the given series, with one projected occurrence each per cycle."""
    projected = []
    for series in series_list:
        if not series.projects:
            continue
        for when in recurrence.occurrences_in_window(
            series, START, START + dt.timedelta(days=days - 1)
        ):
            projected.append(
                forecast.ProjectedMovement(
                    when=when, amount=-series.amount, series_key=series.key, category=series.category
                )
            )
    deltas = {}
    for movement in projected:
        offset = (movement.when - START).days
        deltas[offset] = deltas.get(offset, 0.0) + movement.amount
    values = []
    running = balance
    for index in range(days):
        running += deltas.get(index, 0.0)
        values.append(running)
    return forecast.Curve(
        start=START,
        opening=balance,
        values=values,
        explicit=(),
        projected=tuple(projected),
        series=tuple(series_list),
    )


class TestEligibility(unittest.TestCase):
    def test_a_protected_category_is_never_selectable(self):
        event = _event("event_1", "rent", 500.0, flexibility="reducible_or_stoppable", minimum=100.0)
        curve = _curve([_series(event, 500.0)])
        profile = _profile(protect=("rent",), reduce=("rent",), stop=("rent",))
        self.assertEqual(spending.eligible_actions(profile, curve, RATES), ())

    def test_a_category_outside_the_willingness_lists_is_not_selectable(self):
        event = _event("event_1", "gym", 50.0, flexibility="reducible_or_stoppable", minimum=10.0)
        curve = _curve([_series(event, 50.0)])
        self.assertEqual(spending.eligible_actions(_profile(), curve, RATES), ())

    def test_flexibility_gates_the_action_even_when_the_category_is_permitted(self):
        fixed = _event("event_1", "gym", 50.0, flexibility="fixed", minimum=10.0)
        curve = _curve([_series(fixed, 50.0)])
        profile = _profile(reduce=("gym",), stop=("gym",))
        self.assertEqual(spending.eligible_actions(profile, curve, RATES), ())

    def test_stoppable_yields_only_a_stop_and_reducible_only_a_reduce(self):
        stoppable = _event("event_1", "gym", 50.0, flexibility="stoppable")
        reducible = _event("event_2", "dining", 80.0, flexibility="reducible", minimum=20.0)
        curve = _curve([_series(stoppable, 50.0), _series(reducible, 80.0)])
        profile = _profile(reduce=("dining", "gym"), stop=("dining", "gym"))
        kinds = {(a.kind, a.event_id) for a in spending.eligible_actions(profile, curve, RATES)}
        self.assertEqual(kinds, {("stop", "event_1"), ("reduce_to", "event_2")})

    def test_reducible_or_stoppable_yields_both_actions_for_one_event(self):
        event = _event("event_1", "streaming", 47.0, flexibility="reducible_or_stoppable", minimum=23.5)
        curve = _curve([_series(event, 47.0)])
        profile = _profile(reduce=("streaming",), stop=("streaming",))
        actions = spending.eligible_actions(profile, curve, RATES)
        self.assertEqual({a.kind for a in actions}, {"stop", "reduce_to"})

    def test_a_non_recurring_series_is_never_selectable(self):
        """2,505 expense rows are reducible without being recurring."""
        event = _event("event_1", "shopping", 500.0, flexibility="reducible", minimum=100.0)
        curve = _curve([_series(event, 500.0, projects=False)])
        profile = _profile(reduce=("shopping",), stop=("shopping",))
        self.assertEqual(spending.eligible_actions(profile, curve, RATES), ())

    def test_a_reduce_target_at_or_above_the_current_amount_is_not_a_reduction(self):
        event = _event("event_1", "dining", 80.0, flexibility="reducible", minimum=120.0)
        curve = _curve([_series(event, 80.0)])
        profile = _profile(reduce=("dining",))
        self.assertEqual(spending.eligible_actions(profile, curve, RATES), ())

    def test_a_foreign_currency_floor_is_converted_for_simulation_but_emitted_verbatim(self):
        event = _event("event_1", "dining", 100.0, flexibility="reducible", minimum=40.0, currency="USD")
        curve = _curve([_series(event, 92.0)])  # 100 USD priced into EUR
        rates = RateTable({(event.cash_date, "USD", "EUR"): 0.92})
        action = spending.eligible_actions(_profile(reduce=("dining",)), curve, rates)[0]
        self.assertAlmostEqual(action.new_amount, 36.8)
        self.assertAlmostEqual(action.emitted_amount, 40.0)
        self.assertIn("reduce_to:event_1:40", action.render())


class TestReduceRespectsTheFloor(unittest.TestCase):
    def test_reduce_to_targets_the_minimum_allowed_amount_exactly(self):
        event = _event("event_1", "streaming", 47.0, flexibility="reducible", minimum=23.5)
        curve = _curve([_series(event, 47.0)])
        action = spending.eligible_actions(_profile(reduce=("streaming",)), curve, RATES)[0]
        self.assertEqual(action.new_amount, 23.5)
        self.assertEqual(action.render(), "reduce_to:event_1:23.50")

    def test_the_emitted_amount_follows_the_plan_amount_format(self):
        whole = _event("event_1", "dining", 1000000.0, flexibility="reducible", minimum=665950.0)
        curve = _curve([_series(whole, 1000000.0)])
        action = spending.eligible_actions(_profile(reduce=("dining",)), curve, RATES)[0]
        self.assertEqual(action.render(), "reduce_to:event_1:665950")


class TestSearchBehaviour(unittest.TestCase):
    def _setup(self, *, balance, minimum, requested, events_and_amounts, profile_kw):
        series = [_series(event, amount) for event, amount in events_and_amounts]
        curve = _curve(series, balance=balance)
        profile = _profile(balance=balance, minimum=minimum, **profile_kw)
        return profile, curve, _request(requested=requested)

    def test_it_returns_none_when_no_permitted_change_is_enough(self):
        profile, curve, request = self._setup(
            balance=10000.0,
            minimum=1000.0,
            requested=9500.0,
            events_and_amounts=[(_event("event_1", "gym", 20.0, flexibility="stoppable"), 20.0)],
            profile_kw=dict(stop=("gym",)),
        )
        self.assertIsNone(
            spending.search(request=request, profile=profile, curve=curve, amount_safe=0.0, rates=RATES)
        )

    def test_it_returns_none_when_the_user_will_not_pay_in_full(self):
        profile, curve, request = self._setup(
            balance=10000.0,
            minimum=1000.0,
            requested=8900.0,
            events_and_amounts=[(_event("event_1", "gym", 500.0, flexibility="stoppable"), 500.0)],
            profile_kw=dict(stop=("gym",), methods=("installments",)),
        )
        self.assertIsNone(
            spending.search(request=request, profile=profile, curve=curve, amount_safe=0.0, rates=RATES)
        )

    def test_a_sufficient_change_yields_the_affordable_with_plan_full_payment_shape(self):
        profile, curve, request = self._setup(
            balance=10000.0,
            minimum=1000.0,
            requested=8900.0,
            events_and_amounts=[(_event("event_1", "gym", 200.0, flexibility="stoppable"), 200.0)],
            profile_kw=dict(stop=("gym",)),
        )
        candidate = spending.search(
            request=request, profile=profile, curve=curve, amount_safe=8800.0, rates=RATES
        )
        self.assertIsNotNone(candidate)
        self.assertEqual((candidate.status, candidate.method), ("affordable_with_plan", "full_payment"))
        self.assertEqual(candidate.payments, ((START, 8900.0),))
        self.assertEqual(candidate.spending_changes, ("stop:event_1",))

    def test_the_smallest_total_cut_wins_even_when_it_needs_more_actions(self):
        """request_21's shape: two gentle cuts beat one larger one."""
        cloud = _event("event_1", "cloud_storage", 11.0, flexibility="stoppable")
        streaming = _event("event_2", "streaming", 47.0, flexibility="reducible_or_stoppable", minimum=23.5)
        profile, curve, request = self._setup(
            balance=1000.0,
            minimum=500.0,
            # Two occurrences of each series fall inside the deadline, so the window cuts are
            # double the per-cycle figures: 22, 47, 69 and 94. The request is sized so that
            # only 69 or more clears the floor.
            requested=450.0,
            events_and_amounts=[(cloud, 11.0), (streaming, 47.0)],
            profile_kw=dict(reduce=("streaming",), stop=("streaming", "cloud_storage")),
        )
        candidate = spending.search(
            request=request, profile=profile, curve=curve, amount_safe=440.0, rates=RATES
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(
            candidate.spending_changes, ("stop:event_1", "reduce_to:event_2:23.50")
        )

    def test_the_smaller_of_two_single_action_cuts_wins(self):
        """request_11's shape: dining beats entertainment because the cut is smaller."""
        dining = _event("event_1", "dining", 1200.0, flexibility="reducible", minimum=700.0)
        entertainment = _event("event_2", "entertainment", 1600.0, flexibility="reducible", minimum=800.0)
        profile, curve, request = self._setup(
            balance=10000.0,
            minimum=1000.0,
            # Base trough is 4,400 with both series running. Reducing dining clears the
            # floor for this request; so does reducing entertainment, at a larger cut.
            requested=4000.0,
            events_and_amounts=[(dining, 1200.0), (entertainment, 1600.0)],
            profile_kw=dict(reduce=("dining", "entertainment")),
        )
        candidate = spending.search(
            request=request, profile=profile, curve=curve, amount_safe=3400.0, rates=RATES
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.spending_changes, ("reduce_to:event_1:700",))

    def test_never_more_than_three_actions(self):
        events = [
            (_event(f"event_{i}", f"cat_{i}", 5.0, flexibility="stoppable"), 5.0) for i in range(8)
        ]
        profile, curve, request = self._setup(
            balance=1000.0,
            minimum=500.0,
            # Sized so two stops are not enough and three are exactly enough, which is what
            # makes the cap the thing under test rather than an untested upper bound.
            requested=445.0,
            events_and_amounts=events,
            profile_kw=dict(stop=tuple(f"cat_{i}" for i in range(8))),
        )
        candidate = spending.search(
            request=request, profile=profile, curve=curve, amount_safe=440.0, rates=RATES
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(len(candidate.spending_changes), MAX_SPENDING_CHANGES)

    def test_a_shortfall_needing_four_actions_is_refused_rather_than_capped_silently(self):
        events = [
            (_event(f"event_{i}", f"cat_{i}", 5.0, flexibility="stoppable"), 5.0) for i in range(8)
        ]
        profile, curve, request = self._setup(
            balance=1000.0,
            minimum=500.0,
            requested=460.0,  # needs 40 of cuts; three stops give only 30
            events_and_amounts=events,
            profile_kw=dict(stop=tuple(f"cat_{i}" for i in range(8))),
        )
        self.assertIsNone(
            spending.search(
                request=request, profile=profile, curve=curve, amount_safe=420.0, rates=RATES
            )
        )

    def test_stop_and_reduce_never_target_the_same_event(self):
        event = _event("event_1", "streaming", 47.0, flexibility="reducible_or_stoppable", minimum=23.5)
        profile, curve, request = self._setup(
            balance=1000.0,
            minimum=500.0,
            requested=460.0,
            events_and_amounts=[(event, 47.0)],
            profile_kw=dict(reduce=("streaming",), stop=("streaming",)),
        )
        candidate = spending.search(
            request=request, profile=profile, curve=curve, amount_safe=400.0, rates=RATES
        )
        if candidate is not None:
            targets = [change.split(":")[1] for change in candidate.spending_changes]
            self.assertEqual(len(targets), len(set(targets)))


class TestRankingAgainstChangeFreePlans(unittest.TestCase):
    def test_a_plan_needing_changes_loses_to_an_equal_plan_needing_none(self):
        from buyorwait.planner import Candidate

        with_changes = Candidate(
            method="full_payment",
            status="affordable_with_plan",
            payments=((START, 100.0),),
            total_payable=100.0,
            spending_changes=("stop:event_1",),
        )
        without = Candidate(
            method="installments",
            status="affordable_with_plan",
            payments=((START, 100.0),),
            total_payable=100.0,
        )
        request = _request(requested=100.0)
        self.assertIs(
            min([with_changes, without], key=lambda c: c.rank_key(request)), without
        )


class TestAgainstTheRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from buyorwait.pipeline import Engine

        cls.data = loaders.load_dataset(DATASET)
        cls.engine = Engine.build(cls.data, use_llm=False)

    def test_every_emitted_change_names_a_real_event_of_that_user(self):
        for request in self.data.requests + self.data.samples:
            decision = self.engine.decision_for(request)
            for change in decision.chosen.spending_changes:
                event_id = change.split(":")[1]
                with self.subTest(request=request.request_id, change=change):
                    self.assertIn(event_id, self.data.events_by_id)
                    self.assertEqual(self.data.events_by_id[event_id].user_id, request.user_id)

    def test_every_emitted_change_respects_the_profile_and_the_events_flexibility(self):
        for request in self.data.requests + self.data.samples:
            profile = self.data.profiles[request.user_id]
            for change in self.engine.decision_for(request).chosen.spending_changes:
                kind, event_id = change.split(":")[0], change.split(":")[1]
                event = self.data.events_by_id[event_id]
                with self.subTest(request=request.request_id, change=change):
                    self.assertNotIn(event.category, profile.expense_categories_to_protect)
                    if kind == "stop":
                        self.assertTrue(event.is_stoppable)
                        self.assertIn(event.category, profile.categories_willing_to_stop)
                    else:
                        self.assertTrue(event.is_reducible)
                        self.assertIn(event.category, profile.categories_willing_to_reduce)
                        self.assertIsNotNone(event.minimum_allowed_amount)
                        self.assertAlmostEqual(
                            float(change.split(":")[2]), event.minimum_allowed_amount, places=2
                        )

    def test_no_row_ever_carries_more_than_three_changes(self):
        for request in self.data.requests + self.data.samples:
            with self.subTest(request=request.request_id):
                changes = self.engine.decision_for(request).chosen.spending_changes
                self.assertLessEqual(len(changes), MAX_SPENDING_CHANGES)

    def test_changes_only_ever_appear_on_the_affordable_with_plan_full_payment_branch(self):
        for request in self.data.requests + self.data.samples:
            chosen = self.engine.decision_for(request).chosen
            if chosen.spending_changes:
                with self.subTest(request=request.request_id):
                    self.assertEqual(
                        (chosen.status, chosen.method), ("affordable_with_plan", "full_payment")
                    )


if __name__ == "__main__":
    unittest.main()
