"""Recorded extraction responses for the 17 sample requests that carry messages (M15).

Each entry is the amendment list a correct extraction reports for that request's message,
written by reading the message text. They are played back through an injected transport, so
the suite exercises the full path — schema validation, re-validation against the user's
history, and application to the forecast — without ever making a live call.

Both languages are represented (request_02, request_03, request_04 and request_11 are
Indonesian), as is every amendment kind and the negative case of a message that states no
actionable financial fact.
"""

from __future__ import annotations

#: request_id -> the `amendments` array a correct extraction returns.
RECORDED: dict[str, list[dict]] = {
    # "Gaji bulanan Anda naik menjadi IDR 42750000. Perubahan ini berlaku mulai 2025-08-15."
    "request_02": [
        {
            "kind": "income_amount",
            "message_id": "message_01",
            "amount": 42750000,
            "currency": "IDR",
            "effective_date": "2025-08-15",
            "scope": "ongoing",
            "confidence": 0.95,
        }
    ],
    # Regular salary confirmed, with a one-time adjustment shown separately. No amount is
    # stated for either, so there is nothing to report.
    "request_03": [],
    # "Bonus kuartalan Anda masih menunggu hasil akhir penilaian kinerja."
    "request_04": [
        {
            "kind": "income_suppressed",
            "message_id": "message_03",
            "target_description": "quarterly bonus",
            "confidence": 0.9,
        }
    ],
    # "Your temporary monthly pay is EUR 1037.52. The reduced amount continues..."
    "request_06": [
        {
            "kind": "income_amount",
            "message_id": "message_04",
            "amount": 1037.52,
            "currency": "EUR",
            "scope": "ongoing",
            "confidence": 0.92,
        }
    ],
    # "Your confirmed salary is now expected on 2024-09-23. This replaces the payroll date..."
    "request_07": [
        {
            "kind": "income_date",
            "message_id": "message_05",
            "effective_date": "2024-09-23",
            "confidence": 0.94,
        }
    ],
    # "Your next salary is reduced to EUR 1422.85. The adjustment is due to approved unpaid
    # leave." — a single payment, hence next_only.
    "request_08": [
        {
            "kind": "income_amount",
            "message_id": "message_06",
            "amount": 1422.85,
            "currency": "EUR",
            "scope": "next_only",
            "confidence": 0.9,
        }
    ],
    # "The next QuickCrew payout is still pending... isn't withdrawable until the payout
    # shows as completed."
    "request_10": [
        {
            "kind": "income_suppressed",
            "message_id": "message_07",
            "target_description": "platform payout earnings",
            "confidence": 0.88,
        }
    ],
    # "Gaji pokok yang dikonfirmasi adalah IDR 38760000. Komisi ... belum disetujui."
    "request_11": [
        {
            "kind": "income_amount",
            "message_id": "message_08",
            "amount": 38760000,
            "currency": "IDR",
            "target_description": "base salary",
            "scope": "ongoing",
            "confidence": 0.93,
        },
        {
            "kind": "income_suppressed",
            "message_id": "message_08",
            "target_description": "commission",
            "confidence": 0.9,
        },
    ],
    # "The current seasonal contract has ended. No off-season income or renewal confirmed."
    "request_12": [
        {
            "kind": "income_stopped",
            "message_id": "message_09",
            "target_description": "seasonal contract",
            "confidence": 0.91,
        }
    ],
    # "Regular salary of EUR 2717 resumes on 2025-08-15." The childcare payment that begins
    # in the same month has no stated amount, so it is not reportable.
    "request_14": [
        {
            "kind": "income_amount",
            "message_id": "message_10",
            "amount": 2717,
            "currency": "EUR",
            "effective_date": "2025-08-15",
            "scope": "ongoing",
            "confidence": 0.93,
        }
    ],
    # "Your first salary will be EUR 1661. The confirmed credit date is 2026-01-15."
    "request_15": [
        {
            "kind": "income_amount",
            "message_id": "message_11",
            "amount": 1661,
            "currency": "EUR",
            "effective_date": "2026-01-15",
            "scope": "ongoing",
            "confidence": 0.94,
        }
    ],
    # "The renewed lease increases monthly rent by 12%."
    "request_16": [
        {
            "kind": "expense_change_pct",
            "message_id": "message_12",
            "percent_change": 12,
            "target_category": "rent",
            "confidence": 0.93,
        }
    ],
    # An internal transfer between the user's own accounts: both halves are settled history.
    "request_18": [],
    # "Your refund has been initiated but has not reached your account yet."
    "request_20": [
        {
            "kind": "income_suppressed",
            "message_id": "message_14",
            "target_description": "refund",
            "confidence": 0.9,
        }
    ],
    # Displayed market value moved; no units sold, no cash generated.
    "request_22": [],
    # "Your prize claim has been verified and is still in payment processing."
    "request_23": [
        {
            "kind": "income_suppressed",
            "message_id": "message_16",
            "target_description": "prize",
            "confidence": 0.9,
        }
    ],
    # Proceeds already credited and the claim is closed: nothing forward-looking.
    "request_24": [],
}
