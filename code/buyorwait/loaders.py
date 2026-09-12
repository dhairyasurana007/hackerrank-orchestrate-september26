"""Strict CSV loaders for the nine dataset inputs (TASKS.md M1).

Two rules govern this module:

* **A malformed row fails loudly.** Every field goes through a typed parser that raises
  ``DatasetError`` naming the file, row and column. Nothing defaults silently — a blank
  where a number is required and an unrecognised status are both errors, because a quiet
  default there would surface later as a wrong recommendation rather than as a crash.
* **A blank is only ever absence where absence is meaningful.** Blank ``amount``, blank
  ``max_installment_months``, blank ``minimum_allowed_amount``, blank ``linked_event_id``,
  blank ``settlement_date`` and blank preference lists each mean something specific, and
  each maps to ``None`` or an empty tuple rather than to zero.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .records import (
    CURRENCIES,
    DIRECTIONS,
    EVENT_TYPES,
    FLEXIBILITIES,
    REQUEST_TYPES,
    SOURCE_TYPES,
    STATUSES,
    DatasetError,
    Event,
    ImageRef,
    Message,
    PaymentOption,
    Profile,
    Request,
    SampleTruth,
)


class _Row:
    """One CSV row, with field parsers that report where a failure came from."""

    __slots__ = ("data", "source", "line")

    def __init__(self, data: dict, source: str, line: int):
        self.data = data
        self.source = source
        self.line = line

    def _fail(self, column: str, detail: str) -> DatasetError:
        value = self.data.get(column)
        return DatasetError(f"{self.source}:{self.line}: column {column!r}: {detail} (got {value!r})")

    def _raw(self, column: str) -> str:
        if column not in self.data:
            raise self._fail(column, "column missing from the file")
        value = self.data[column]
        if value is None:
            raise self._fail(column, "row has fewer fields than the header")
        return value.strip()

    def text(self, column: str, *, allow_blank: bool = False) -> str:
        value = self._raw(column)
        if not value and not allow_blank:
            raise self._fail(column, "required value is blank")
        return value

    def optional_text(self, column: str) -> str | None:
        return self._raw(column) or None

    def enum(self, column: str, allowed: tuple[str, ...]) -> str:
        value = self.text(column)
        if value not in allowed:
            raise self._fail(column, f"not one of {allowed}")
        return value

    def number(self, column: str) -> float:
        value = self.text(column)
        try:
            return float(value)
        except ValueError:
            raise self._fail(column, "not a number") from None

    def optional_number(self, column: str) -> float | None:
        value = self._raw(column)
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            raise self._fail(column, "not a number") from None

    def integer(self, column: str) -> int:
        value = self.text(column)
        try:
            return int(value)
        except ValueError:
            raise self._fail(column, "not an integer") from None

    def optional_integer(self, column: str) -> int | None:
        value = self._raw(column)
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            raise self._fail(column, "not an integer") from None

    def date(self, column: str) -> dt.date:
        value = self.text(column)
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise self._fail(column, "not an ISO YYYY-MM-DD date") from None

    def optional_date(self, column: str) -> dt.date | None:
        value = self._raw(column)
        if not value:
            return None
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise self._fail(column, "not an ISO YYYY-MM-DD date") from None

    def timestamp(self, column: str) -> dt.datetime:
        value = self.text(column).replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            raise self._fail(column, "not an ISO 8601 timestamp") from None

    def boolean(self, column: str) -> bool:
        value = self.text(column).lower()
        if value not in ("true", "false"):
            raise self._fail(column, "not 'true' or 'false'")
        return value == "true"

    def pipe_list(self, column: str) -> tuple[str, ...]:
        """A ``|``-delimited list. Blank is an empty tuple; one element is a one-tuple."""
        value = self._raw(column)
        if not value:
            return ()
        return tuple(part.strip() for part in value.split("|") if part.strip())


def _rows(path: Path):
    if not path.is_file():
        raise DatasetError(f"{path} is missing")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DatasetError(f"{path} has no header row")
        for line, data in enumerate(reader, start=2):
            yield _Row(data, path.name, line)


def load_profiles(path: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for row in _rows(path):
        profile = Profile(
            user_id=row.text("user_id"),
            home_currency=row.enum("home_currency", CURRENCIES),
            current_available_balance=row.number("current_available_balance"),
            minimum_balance_to_keep=row.number("minimum_balance_to_keep"),
            financial_priorities=row.pipe_list("financial_priorities"),
            expense_categories_to_protect=row.pipe_list("expense_categories_to_protect"),
            categories_willing_to_reduce=row.pipe_list("expense_categories_user_is_willing_to_reduce"),
            categories_willing_to_stop=row.pipe_list("expense_categories_user_is_willing_to_stop"),
            payment_methods_user_will_consider=row.pipe_list("payment_methods_user_will_consider"),
            max_installment_months=row.optional_integer("max_installment_months"),
        )
        if not profile.payment_methods_user_will_consider:
            raise row._fail("payment_methods_user_will_consider", "a user must consider some method")
        if profile.user_id in profiles:
            raise row._fail("user_id", "duplicate user")
        profiles[profile.user_id] = profile
    return profiles


def load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for row in _rows(path):
        event = Event(
            event_id=row.text("event_id"),
            user_id=row.text("user_id"),
            event_type=row.enum("event_type", EVENT_TYPES),
            description=row.text("description", allow_blank=True),
            category=row.text("category"),
            direction=row.enum("direction", DIRECTIONS),
            # Blank amount is never zero: it is recovered from the linked image, or, in the
            # MVP, from the conservative estimate for its recurring group.
            amount=row.optional_number("amount"),
            currency=row.enum("currency", CURRENCIES),
            event_date=row.date("event_date"),
            settlement_date=row.optional_date("settlement_date"),
            status=row.enum("status", STATUSES),
            linked_event_id=row.optional_text("linked_event_id"),
            flexibility=row.enum("flexibility", FLEXIBILITIES),
            minimum_allowed_amount=row.optional_number("minimum_allowed_amount"),
        )
        if event.amount is not None and event.amount < 0:
            raise row._fail("amount", "amounts are unsigned; direction carries the sign")
        events.append(event)
    return events


def load_rates(path: Path) -> dict[tuple[dt.date, str, str], float]:
    rates: dict[tuple[dt.date, str, str], float] = {}
    for row in _rows(path):
        key = (
            row.date("rate_date"),
            row.enum("from_currency", CURRENCIES),
            row.enum("to_currency", CURRENCIES),
        )
        rate = row.number("rate")
        if rate <= 0:
            raise row._fail("rate", "must be positive")
        if key in rates and rates[key] != rate:
            raise row._fail("rate", f"conflicting rate for {key}")
        rates[key] = rate
    return rates


def _request_from(row: _Row) -> Request:
    request = Request(
        request_id=row.text("request_id"),
        user_id=row.text("user_id"),
        request_date=row.date("request_date"),
        request_type=row.enum("request_type", REQUEST_TYPES),
        requested_amount=row.number("requested_amount"),
        desired_completion_date=row.date("desired_completion_date"),
        allows_partial_payment=row.boolean("allows_partial_payment"),
        request_text=row.text("request_text", allow_blank=True),
    )
    if request.requested_amount <= 0:
        raise row._fail("requested_amount", "must be positive")
    if request.desired_completion_date < request.request_date:
        raise row._fail("desired_completion_date", "is before the request date")
    return request


def load_requests(path: Path) -> list[Request]:
    return [_request_from(row) for row in _rows(path)]


def load_samples(path: Path) -> tuple[list[Request], dict[str, SampleTruth]]:
    requests: list[Request] = []
    truth: dict[str, SampleTruth] = {}
    for row in _rows(path):
        request = _request_from(row)
        requests.append(request)
        truth[request.request_id] = SampleTruth(
            request_id=request.request_id,
            amount_safe_to_pay=row.number("amount_safe_to_pay"),
            affordability_status=row.text("affordability_status"),
            recommended_payment_method=row.text("recommended_payment_method"),
            payment_plan=row.text("payment_plan"),
            earliest_date_for_full_payment=row.text("earliest_date_for_full_payment", allow_blank=True),
            spending_changes_needed=row.text("spending_changes_needed"),
            decision_explanation=row.text("decision_explanation", allow_blank=True),
        )
    return requests, truth


def load_payment_options(path: Path) -> list[PaymentOption]:
    options: list[PaymentOption] = []
    for row in _rows(path):
        option = PaymentOption(
            payment_option_id=row.text("payment_option_id"),
            request_id=row.text("request_id"),
            payment_method=row.enum("payment_method", ("full_payment", "installments")),
            payment_amount=row.number("payment_amount"),
            number_of_payments=row.integer("number_of_payments"),
            first_payment_date=row.date("first_payment_date"),
            payment_frequency_days=row.optional_integer("payment_frequency_days"),
            financing_fee=row.number("financing_fee"),
            total_payable_amount=row.number("total_payable_amount"),
        )
        if option.number_of_payments < 1:
            raise row._fail("number_of_payments", "must be at least 1")
        # A multi-payment option without a frequency has no schedule to build.
        if option.number_of_payments > 1 and not option.payment_frequency_days:
            raise row._fail("payment_frequency_days", "required when there is more than one payment")
        options.append(option)
    return options


def load_messages(path: Path) -> list[Message]:
    return [
        Message(
            message_id=row.text("message_id"),
            user_id=row.text("user_id"),
            # Blank request_id means the message attaches to the user, not to one request;
            # blank related_event_id means no single supplied event row describes it.
            request_id=row.optional_text("request_id"),
            related_event_id=row.optional_text("related_event_id"),
            sent_at=row.timestamp("sent_at"),
            source_type=row.enum("source_type", SOURCE_TYPES),
            message_text=row.text("message_text", allow_blank=True),
        )
        for row in _rows(path)
    ]


def load_images(path: Path) -> list[ImageRef]:
    return [
        ImageRef(
            image_id=row.text("image_id"),
            user_id=row.text("user_id"),
            request_id=row.text("request_id"),
            related_event_id=row.text("related_event_id"),
        )
        for row in _rows(path)
    ]


@dataclass(frozen=True)
class Dataset:
    """Everything the pipeline reads, indexed the way the pipeline reads it."""

    root: Path
    profiles: dict[str, Profile]
    events_by_id: dict[str, Event]
    events_by_user: dict[str, tuple[Event, ...]]
    rates: dict[tuple[dt.date, str, str], float]
    requests: tuple[Request, ...]
    samples: tuple[Request, ...]
    sample_truth: dict[str, SampleTruth]
    requests_by_id: dict[str, Request]
    options_by_request: dict[str, tuple[PaymentOption, ...]]
    messages_by_user: dict[str, tuple[Message, ...]]
    images_by_event: dict[str, ImageRef]

    def request(self, request_id: str) -> Request:
        return self.requests_by_id[request_id]

    def options(self, request_id: str) -> tuple[PaymentOption, ...]:
        return self.options_by_request.get(request_id, ())

    def events(self, user_id: str) -> tuple[Event, ...]:
        return self.events_by_user.get(user_id, ())

    def messages(self, user_id: str) -> tuple[Message, ...]:
        return self.messages_by_user.get(user_id, ())


def load_dataset(root: Path) -> Dataset:
    root = Path(root)
    profiles = load_profiles(root / "financial_profiles.csv")
    events = load_events(root / "financial_events.csv")
    requests = load_requests(root / "requests.csv")
    samples, sample_truth = load_samples(root / "sample_requests.csv")
    options = load_payment_options(root / "request_payment_options.csv")
    messages = load_messages(root / "messages.csv")
    images = load_images(root / "images.csv")

    events_by_id: dict[str, Event] = {}
    for event in events:
        if event.event_id in events_by_id:
            raise DatasetError(f"financial_events.csv: duplicate event_id {event.event_id!r}")
        events_by_id[event.event_id] = event

    events_by_user: dict[str, list[Event]] = {}
    for event in events:
        if event.user_id not in profiles:
            raise DatasetError(f"{event.event_id}: unknown user_id {event.user_id!r}")
        events_by_user.setdefault(event.user_id, []).append(event)
    # Chronological by the date cash moves, then by id, so every downstream pass over a
    # user's history sees the same deterministic order.
    ordered = {
        user_id: tuple(sorted(rows, key=lambda e: (e.cash_date, e.event_id)))
        for user_id, rows in events_by_user.items()
    }

    for request in requests + samples:
        if request.user_id not in profiles:
            raise DatasetError(f"{request.request_id}: unknown user_id {request.user_id!r}")

    options_by_request: dict[str, list[PaymentOption]] = {}
    for option in options:
        options_by_request.setdefault(option.request_id, []).append(option)
    grouped_options = {
        request_id: tuple(sorted(rows, key=lambda o: o.payment_option_id))
        for request_id, rows in options_by_request.items()
    }

    messages_by_user: dict[str, list[Message]] = {}
    for message in messages:
        if message.user_id not in profiles:
            raise DatasetError(f"{message.message_id}: unknown user_id {message.user_id!r}")
        messages_by_user.setdefault(message.user_id, []).append(message)
    grouped_messages = {
        user_id: tuple(sorted(rows, key=lambda m: (m.sent_at, m.message_id)))
        for user_id, rows in messages_by_user.items()
    }

    images_by_event = {image.related_event_id: image for image in images}

    return Dataset(
        root=root,
        profiles=profiles,
        events_by_id=events_by_id,
        events_by_user=ordered,
        rates=load_rates(root / "exchange_rates.csv"),
        requests=tuple(requests),
        samples=tuple(samples),
        sample_truth=sample_truth,
        requests_by_id={r.request_id: r for r in requests + samples},
        options_by_request=grouped_options,
        messages_by_user=grouped_messages,
        images_by_event=images_by_event,
    )
