"""Vision extraction for the 16 blank-amount document events (TASKS.md F1)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from buyorwait import loaders, paths
from buyorwait.model.client import ModelClient
from buyorwait.model.config import VISION_MODEL
from buyorwait.model import vision
from buyorwait.pipeline import Engine

DATASET = paths.find_dataset()

EXPECTED = {
    "event_253": ("image_01", 4_365_000.0, "IDR", "salary"),
    "event_1442": ("image_02", 100_000.0, "INR", "rent"),
    "event_1545": ("image_03", 41_272.0, "INR", "groceries"),
    "event_1700": ("image_04", 2_854.0, "INR", "groceries"),
    "event_1786": ("image_05", 704.05, "INR", "utilities"),
    "event_3051": ("image_06", 1_995.0, "INR", "groceries"),
    "event_3231": ("image_07", 8_528.10, "INR", "dining"),
    "event_4535": ("image_08", 15_339.0, "INR", "housing"),
    "event_5170": ("image_09", 723.0, "INR", "utilities"),
    "event_6033": ("image_10", 79_679.26, "INR", "groceries"),
    "event_6859": ("image_11", 3_650.0, "INR", "healthcare"),
    "event_7307": ("image_12", 33.50, "USD", "transport"),
    "event_7941": ("image_13", 2_298.0, "INR", "shopping"),
    "event_9421": ("image_14", 4_593.0, "INR", "healthcare"),
    "event_9806": ("image_15", 9_968.0, "INR", "transport"),
    "event_10521": ("image_16", 393.22, "INR", "transport"),
}

_EVENT_ID = re.compile(r"event_id: (event_\d+)")


def _response(payload):
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 1800, "completion_tokens": 80, "cost": 0.0042},
    }


def _transport(overrides=None):
    overrides = overrides or {}

    def transport(body):
        text = body["messages"][1]["content"][0]["text"]
        event_id = _EVENT_ID.search(text).group(1)
        image_id, amount, currency, category = EXPECTED[event_id]
        payload = {
            "event_id": event_id,
            "amount": amount,
            "currency": currency,
            "category": category,
            "confidence": 0.98,
        }
        payload.update(overrides.get(event_id, {}))
        return _response(payload)

    return transport


def _client(tmp, transport=None):
    return ModelClient(
        api_key="fixture-key",
        cache_dir=Path(tmp) / "cache",
        run_log_path=Path(tmp) / "run_log.jsonl",
        transport=transport or _transport(),
    )


class TestVisionExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = loaders.load_dataset(DATASET)

    def test_all_sixteen_blank_amount_images_extract_the_document_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp)
            for event_id, (image_id, expected_amount, currency, category) in EXPECTED.items():
                with self.subTest(event_id=event_id):
                    event = self.data.events_by_id[event_id]
                    request = self.data.request(self.data.images_by_event[event_id].request_id)
                    amount = vision.extract_amount(
                        request=request,
                        event=event,
                        image_ref=self.data.images_by_event[event_id],
                        dataset_root=self.data.root,
                        client=client,
                    )
                    self.assertIsNotNone(amount)
                    self.assertEqual(amount.image_id, image_id)
                    self.assertAlmostEqual(amount.amount, expected_amount, places=2)
                    self.assertEqual(amount.currency, currency)
                    self.assertEqual(amount.category, category)

    def test_the_call_uses_the_pinned_vision_model_and_sends_an_image(self):
        seen = {}

        def transport(body):
            seen.update(body)
            return _transport()(body)

        with tempfile.TemporaryDirectory() as tmp:
            event = self.data.events_by_id["event_1442"]
            request = self.data.request("request_16")
            vision.extract_amount(
                request=request,
                event=event,
                image_ref=self.data.images_by_event[event.event_id],
                dataset_root=self.data.root,
                client=_client(tmp, transport),
            )
        self.assertEqual(seen["model"], VISION_MODEL.model_id)
        content = seen["messages"][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_contradicting_currency_or_category_is_rejected(self):
        cases = (
            {"currency": "USD"},
            {"category": "shopping"},
            {"confidence": 0.1},
        )
        for override in cases:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                event = self.data.events_by_id["event_1442"]
                request = self.data.request("request_16")
                client = _client(tmp, _transport({"event_1442": override}))
                amount = vision.extract_amount(
                    request=request,
                    event=event,
                    image_ref=self.data.images_by_event[event.event_id],
                    dataset_root=self.data.root,
                    client=client,
                )
                self.assertIsNone(amount)
                self.assertTrue(any(record["record"] == "dropped" for record in client.records))

    def test_request_16_rent_enters_the_curve_when_vision_succeeds(self):
        request = self.data.request("request_16")
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine.build(self.data, use_llm=True, client=_client(tmp))
            recovered = engine.recovered_amounts_for(request)
            curve = engine.curve_for(request)
        self.assertAlmostEqual(recovered["event_1442"], 100_000.0)
        movement = next(m for m in curve.explicit if m.event_id == "event_1442")
        self.assertAlmostEqual(movement.amount, -100_000.0)

    def test_a_bad_request_16_result_keeps_the_mvp_fallback_path(self):
        request = self.data.request("request_16")
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine.build(
                self.data,
                use_llm=True,
                client=_client(tmp, _transport({"event_1442": {"currency": "USD"}})),
            )
            self.assertEqual(engine.recovered_amounts_for(request), {})


if __name__ == "__main__":
    unittest.main()
