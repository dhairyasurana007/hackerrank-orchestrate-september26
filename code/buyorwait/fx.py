"""Dated FX normalisation (TASKS.md M2).

``exchange_rates.csv`` supplies fixed rates on specific dates in a specific direction, and
the spec is precise about which row applies: *"For a foreign-currency cash event, use the
row for its settlement date and the stated from_currency to to_currency direction."* This
module does exactly that and nothing more:

* a same-currency conversion performs no lookup at all;
* the lookup key is the **settlement** date, never the event date;
* a rate is never inverted to serve the opposite direction, and rates are never chained
  through a third currency — either the stated direction exists on that date or it does not;
* a missing rate raises. Silently becoming 1.0 would turn a 15,833x error into a plausible
  number, which is the worst possible failure mode here.

Verified against the dataset: all 140 non-home-currency events have an exact rate row for
their settlement date and stated direction, so the strict lookup is sufficient as well as
correct.
"""

from __future__ import annotations

import datetime as dt

from .records import DatasetError


class MissingRateError(DatasetError):
    """No rate for this date and direction. Never silently treated as parity."""


class RateTable:
    """Dated, directed exchange rates."""

    __slots__ = ("_rates",)

    def __init__(self, rates: dict[tuple[dt.date, str, str], float]):
        self._rates = dict(rates)

    def __len__(self) -> int:
        return len(self._rates)

    def rate(self, from_currency: str, to_currency: str, on: dt.date) -> float:
        """The stated rate for this exact date and direction, or a raised error."""
        if from_currency == to_currency:
            return 1.0
        try:
            return self._rates[(on, from_currency, to_currency)]
        except KeyError:
            raise MissingRateError(
                f"no {from_currency}->{to_currency} rate on {on.isoformat()}; "
                "rates are not inverted, chained, or defaulted"
            ) from None

    def convert(self, amount: float, from_currency: str, to_currency: str, on: dt.date) -> float:
        """Convert ``amount`` on ``on``. Same-currency amounts are returned untouched."""
        if from_currency == to_currency:
            return amount
        return amount * self.rate(from_currency, to_currency, on)

    def convert_event_amount(self, event, home_currency: str, amount: float | None = None) -> float:
        """Convert an event's amount into the user's home currency on its settlement date.

        ``amount`` overrides the event's own figure, which is how a blank amount recovered
        from evidence is normalised through the same path as every other value.
        """
        value = event.amount if amount is None else amount
        if value is None:
            raise DatasetError(f"{event.event_id}: cannot convert a blank amount")
        return self.convert(value, event.currency, home_currency, event.cash_date)
