#!/usr/bin/env python3
"""Emit the static decision bundle consumed by the results explorer.

The browser must not recompute affordability and must never call a model provider. This
script does the one allowed dynamic step ahead of time: load the dataset, reconstruct the
diagnostic curve/candidate data offline, and bind it to the submitted ``output.csv`` row.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait import paths
from buyorwait.contract import OUTPUT_COLUMNS
from buyorwait.loaders import Dataset, load_dataset
from buyorwait.pipeline import Engine
from buyorwait.records import Event, Message, PaymentOption, Request
from buyorwait.writer import OutputRow
from evaluation import drawdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=None, help="path to the dataset/ directory")
    parser.add_argument("--output-csv", default=None, help="submitted output.csv to bind decisions to")
    parser.add_argument("--output-dir", default="site", help="static site directory to write into")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset = paths.find_dataset(args.dataset)
    output_csv = Path(args.output_csv) if args.output_csv else paths.default_output(dataset)
    output_dir = Path(args.output_dir)
    data = load_dataset(dataset)
    engine = Engine.build(data, use_llm=False)
    bundle = build_bundle(data, engine, output_csv=output_csv)
    write_bundle(bundle, output_dir)
    print(f"wrote static explorer data for {len(bundle['requests'])} requests to {output_dir}")
    return 0


def build_bundle(data: Dataset, engine: Engine, *, output_csv: Path) -> dict[str, Any]:
    output_rows = read_output_rows(output_csv)
    requests = []
    mismatches = []
    for request in data.requests:
        if request.request_id not in output_rows:
            raise ValueError(f"{output_csv} is missing {request.request_id}")
        item = request_bundle(data, engine, request, final_row=output_rows[request.request_id])
        if item["finalDecision"]["request_id"] != request.request_id:
            mismatches.append(request.request_id)
        requests.append(item)
    extras = sorted(set(output_rows) - {request.request_id for request in data.requests})
    if extras:
        raise ValueError(f"{output_csv} contains request_id values not in dataset/requests.csv: {extras}")
    if mismatches:
        raise ValueError(f"decision bundle request mismatch: {mismatches}")
    drawdown_report = drawdown.grade(engine.predicted_drawdown, data)
    usage_report = Path(__file__).resolve().parents[1] / "evaluation" / "usage_report.md"
    return {
        "metadata": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "dataset_root": str(data.root),
            "output_csv": str(output_csv),
            "request_count": len(requests),
            "browser_contract": "static-data-only; no live recomputation; no model calls",
            "diagnostic_mode": "offline deterministic engine diagnostics; final decisions are loaded from output.csv",
        },
        "summary": summarise(requests),
        "drawdown": {
            "summary": drawdown_report.as_dict(),
            "samples": [sample_result(result) for result in sorted(drawdown_report.results, key=lambda r: r.request_id)],
        },
        "usageReportMarkdown": usage_report.read_text(encoding="utf-8") if usage_report.is_file() else "",
        "requests": requests,
    }


def read_output_rows(path: Path) -> dict[str, dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
            raise ValueError(f"{path} does not have the required output header")
        return {row["request_id"]: {column: row[column] for column in OUTPUT_COLUMNS} for row in reader}


def request_bundle(data: Dataset, engine: Engine, request: Request, *, final_row: dict[str, str]) -> dict[str, Any]:
    profile = data.profiles[request.user_id]
    state = engine.state_for(request)
    decision = engine.decision_for(request)
    curve = decision.curve
    assert curve is not None
    trough_index = curve.trough_index(request.desired_completion_date)
    diagnostic_row = OutputRow(
        request_id=request.request_id,
        amount_safe_to_pay=decision.amount_safe_to_pay,
        affordability_status=decision.chosen.status,
        recommended_payment_method=decision.chosen.method,
        payment_plan=decision.chosen.payments,
        earliest_date_for_full_payment=decision.emitted_earliest,
        spending_changes_needed=decision.chosen.spending_changes,
        decision_explanation="",
    ).as_csv_dict()
    event_ids = {movement.event.event_id for movement in curve.explicit}
    event_ids.update(change.split(":")[1] for change in decision.chosen.spending_changes if ":" in change)
    evidence_messages = [
        message
        for message in data.messages(request.user_id)
        if message.sent_on <= request.request_date
        and (message.request_id in (None, request.request_id) or message.related_event_id in event_ids)
    ]
    return {
        "request": {
            "request_id": request.request_id,
            "user_id": request.user_id,
            "request_date": request.request_date.isoformat(),
            "request_type": request.request_type,
            "requested_amount": money(request.requested_amount),
            "desired_completion_date": request.desired_completion_date.isoformat(),
            "allows_partial_payment": request.allows_partial_payment,
            "request_text": request.request_text,
        },
        "profile": {
            "home_currency": profile.home_currency,
            "current_available_balance": money(profile.current_available_balance),
            "minimum_balance_to_keep": money(profile.minimum_balance_to_keep),
            "financial_priorities": list(profile.financial_priorities),
            "payment_methods_user_will_consider": list(profile.payment_methods_user_will_consider),
            "max_installment_months": profile.max_installment_months,
        },
        "floor": money(profile.minimum_balance_to_keep),
        "trough": {
            "date": curve.date_at(trough_index).isoformat(),
            "balance": money(curve.values[trough_index]),
            "drawdown": money(curve.opening - curve.values[trough_index]),
            "through": request.desired_completion_date.isoformat(),
        },
        "curve": [
            {"date": curve.date_at(index).isoformat(), "balance": money(value)}
            for index, value in enumerate(curve.values)
        ],
        "movements": {
            "explicit": [explicit_movement(movement) for movement in curve.explicit],
            "projected": [
                {
                    "date": movement.when.isoformat(),
                    "amount": money(movement.amount),
                    "category": movement.category,
                    "series_key": list(movement.series_key),
                }
                for movement in curve.projected
            ],
        },
        "options": [payment_option(option) for option in data.options(request.request_id)],
        "candidates": [candidate_item(candidate) for candidate in decision.considered],
        "rejected": [{"item": item, "reason": reason} for item, reason in decision.rejected],
        "appliedAmendments": amendments_for_request(engine, request.request_id),
        "evidence": {
            "messages": [message_item(message) for message in evidence_messages],
            "images": [image_item(data, event_id) for event_id in sorted(event_ids) if event_id in data.images_by_event],
        },
        "diagnosticDecision": diagnostic_row,
        "finalDecision": final_row,
        "decisionMatchesOutput": final_row["request_id"] == request.request_id,
    }


def summarise(requests: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    methods: dict[str, int] = {}
    currencies: dict[str, int] = {}
    total_safe = 0.0
    for item in requests:
        row = item["finalDecision"]
        profile = item["profile"]
        statuses[row["affordability_status"]] = statuses.get(row["affordability_status"], 0) + 1
        methods[row["recommended_payment_method"]] = methods.get(row["recommended_payment_method"], 0) + 1
        currencies[profile["home_currency"]] = currencies.get(profile["home_currency"], 0) + 1
        total_safe += float(row["amount_safe_to_pay"] or 0)
    return {
        "statuses": statuses,
        "methods": methods,
        "currencies": currencies,
        "total_amount_safe_to_pay": money(total_safe),
    }


def candidate_item(item) -> dict[str, Any]:
    return {
        "method": item.method,
        "status": item.status,
        "payments": payments(item.payments),
        "total_payable": money(item.total_payable),
        "option_id": item.option_id,
        "spending_changes": list(item.spending_changes),
        "completes_by_deadline": item.completes_by_deadline,
        "payment_count": item.payment_count,
        "note": item.note,
    }


def payments(rows) -> list[dict[str, Any]]:
    return [{"date": when.isoformat(), "amount": money(amount)} for when, amount in rows]


def payment_option(option: PaymentOption) -> dict[str, Any]:
    return {
        "payment_option_id": option.payment_option_id,
        "payment_method": option.payment_method,
        "payment_amount": money(option.payment_amount),
        "number_of_payments": option.number_of_payments,
        "first_payment_date": option.first_payment_date.isoformat(),
        "payment_frequency_days": option.payment_frequency_days,
        "financing_fee": money(option.financing_fee),
        "total_payable_amount": money(option.total_payable_amount),
        "schedule": payments(option.schedule()),
    }


def explicit_movement(movement) -> dict[str, Any]:
    event: Event = movement.event
    return {
        "date": movement.when.isoformat(),
        "amount": money(movement.amount),
        "event_id": event.event_id,
        "category": event.category,
        "direction": event.direction,
        "status": event.status,
        "description": event.description,
        "source_amount": None if event.amount is None else money(event.amount),
        "currency": event.currency,
    }


def message_item(message: Message) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "sent_at": message.sent_at.isoformat(),
        "source_type": message.source_type,
        "request_id": message.request_id,
        "related_event_id": message.related_event_id,
        "message_text": message.message_text,
    }


def image_item(data: Dataset, event_id: str) -> dict[str, Any]:
    image = data.images_by_event[event_id]
    return {
        "image_id": image.image_id,
        "request_id": image.request_id,
        "related_event_id": image.related_event_id,
        "path": f"../dataset/media/images/{image.image_id}.png",
    }


def amendments_for_request(engine: Engine, request_id: str) -> list[dict[str, Any]]:
    for logged_request_id, amendments in engine.amendment_log:
        if logged_request_id != request_id:
            continue
        return [
            {key: serialise(value) for key, value in getattr(amendment, "__dict__", {}).items()}
            for amendment in amendments
        ]
    return []


def sample_result(result) -> dict[str, Any]:
    return {
        "request_id": result.request_id,
        "kind": result.target.kind,
        "target_drawdown": money(result.target.target_drawdown),
        "predicted_drawdown": money(result.predicted_drawdown),
        "relative_error": None if result.error is None else round(result.error, 6),
        "bound_violated": result.bound_violated,
    }


def write_bundle(bundle: dict[str, Any], output_dir: Path) -> None:
    data_dir = Path(output_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "results.json").write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def serialise(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [serialise(item) for item in value]
    return value


def money(value: float) -> float:
    return round(float(value), 2)


if __name__ == "__main__":
    raise SystemExit(main())
