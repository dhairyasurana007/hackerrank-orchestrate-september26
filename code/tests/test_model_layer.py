"""Model layer: cache, schema validation, run log, failure isolation (TASKS.md M14).

Every test here injects a transport. The suite never makes a live call, so it runs green on
a machine with no API key — which is also the CI machine.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buyorwait.model import schema as schema_module
from buyorwait.model.client import ModelClient, _digest
from buyorwait.model.config import TEXT_MODEL, VISION_MODEL, CallSettings, spec_for

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["amendments"],
    "properties": {
        "amendments": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "confidence"],
                "properties": {
                    "kind": {"type": "string", "enum": ["income_amount", "event_cancelled"]},
                    "amount": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}

MESSAGES = [{"role": "user", "content": "quoted untrusted data"}]


def _response(payload, *, prompt_tokens=120, completion_tokens=30, cost=0.00012):
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        },
    }


def _client(tmp, transport, **kwargs):
    return ModelClient(
        api_key="test-key",
        cache_dir=Path(tmp) / "cache",
        run_log_path=Path(tmp) / "run_log.jsonl",
        transport=transport,
        **kwargs,
    )


class TestSchemaValidator(unittest.TestCase):
    def test_a_valid_payload_has_no_problems(self):
        payload = {"amendments": [{"kind": "income_amount", "amount": 1661.0, "confidence": 0.9}]}
        self.assertEqual(schema_module.validate(payload, SCHEMA), [])

    def test_a_missing_required_property_is_reported(self):
        problems = schema_module.validate({}, SCHEMA)
        self.assertTrue(any("missing required property" in p for p in problems))

    def test_an_unexpected_property_is_reported(self):
        payload = {"amendments": [], "note": "hello"}
        self.assertTrue(any("unexpected property" in p for p in schema_module.validate(payload, SCHEMA)))

    def test_a_value_outside_the_enum_is_reported(self):
        payload = {"amendments": [{"kind": "mark_affordable", "confidence": 1.0}]}
        self.assertTrue(any("is not one of" in p for p in schema_module.validate(payload, SCHEMA)))

    def test_a_wrong_type_is_reported(self):
        payload = {"amendments": [{"kind": "income_amount", "confidence": "high"}]}
        self.assertTrue(any("expected number" in p for p in schema_module.validate(payload, SCHEMA)))

    def test_a_nullable_field_accepts_null_and_a_number(self):
        for amount in (None, 12.5):
            payload = {"amendments": [{"kind": "income_amount", "amount": amount, "confidence": 0.5}]}
            self.assertEqual(schema_module.validate(payload, SCHEMA), [])

    def test_bounds_are_enforced(self):
        payload = {"amendments": [{"kind": "income_amount", "confidence": 1.5}]}
        self.assertTrue(any("above maximum" in p for p in schema_module.validate(payload, SCHEMA)))

    def test_max_items_is_enforced(self):
        payload = {"amendments": [{"kind": "event_cancelled", "confidence": 0.5}] * 5}
        self.assertTrue(any("exceeds maxItems" in p for p in schema_module.validate(payload, SCHEMA)))

    def test_a_boolean_is_not_a_number(self):
        self.assertTrue(schema_module.validate(True, {"type": "number"}))
        self.assertEqual(schema_module.validate(True, {"type": "boolean"}), [])


class TestOffSchemaOutputIsDropped(unittest.TestCase):
    def test_an_off_schema_response_yields_no_payload_at_all(self):
        """Never a partial parse: the whole response is discarded."""
        bad = {"amendments": [{"kind": "mark_this_affordable", "confidence": 0.9}]}
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, lambda body: _response(bad))
            result = client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertFalse(result.ok)
        self.assertIsNone(result.payload)
        self.assertIn("schema validation", result.error)
        self.assertTrue(result.schema_problems)

    def test_an_off_schema_response_is_not_cached(self):
        bad = {"unexpected": True}
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def transport(body):
                calls.append(body)
                return _response(bad)

            client = _client(tmp, transport)
            for _ in range(2):
                client.complete_json(
                    messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
                )
            self.assertEqual(len(calls), 2)  # a bad response must not poison the cache

    def test_unparseable_content_is_dropped(self):
        def transport(body):
            return {"choices": [{"message": {"content": "not json at all"}}], "usage": {}}

        with tempfile.TemporaryDirectory() as tmp:
            result = _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertFalse(result.ok)
        self.assertIn("unparseable", result.error)


class TestFailureIsolation(unittest.TestCase):
    def test_a_provider_error_degrades_to_no_result_rather_than_raising(self):
        def transport(body):
            raise urllib_error()

        with tempfile.TemporaryDirectory() as tmp:
            result = _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.error)

    def test_a_transient_error_is_retried_once_and_can_then_succeed(self):
        attempts = []
        payload = {"amendments": []}

        def transport(body):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient")
            return _response(payload)

        with tempfile.TemporaryDirectory() as tmp:
            result = _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(attempts), 2)

    def test_a_missing_api_key_is_the_offline_path_and_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ModelClient(
                api_key=None, cache_dir=Path(tmp) / "cache", run_log_path=Path(tmp) / "log.jsonl"
            )
            self.assertFalse(client.available)
            result = client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertFalse(result.ok)
        self.assertIn("OPENROUTER_API_KEY", result.error)

    def test_every_attempt_failing_still_returns_a_result_object(self):
        def transport(body):
            raise TimeoutError("too slow")

        with tempfile.TemporaryDirectory() as tmp:
            result = _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)


def urllib_error():
    return RuntimeError("HTTP 502 from provider")


class TestCache(unittest.TestCase):
    def test_a_second_identical_call_is_served_from_disk(self):
        payload = {"amendments": [{"kind": "income_amount", "amount": 42.0, "confidence": 0.8}]}
        calls = []

        def transport(body):
            calls.append(body)
            return _response(payload)

        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, transport)
            first = client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
            second = client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertEqual(len(calls), 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.payload, second.payload)

    def test_two_warm_cache_runs_produce_identical_payloads(self):
        """Determinism: PLAN.md 6.2 asks for byte-identical output across warm runs."""
        payload = {"amendments": [{"kind": "event_cancelled", "confidence": 0.7}]}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            first = ModelClient(
                api_key="k", cache_dir=cache, run_log_path=Path(tmp) / "a.jsonl",
                transport=lambda body: _response(payload),
            ).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )

            def refuse(body):
                raise AssertionError("a warm-cache run must not call the provider")

            second = ModelClient(
                api_key="k", cache_dir=cache, run_log_path=Path(tmp) / "b.jsonl", transport=refuse
            ).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertEqual(json.dumps(first.payload, sort_keys=True), json.dumps(second.payload, sort_keys=True))
        self.assertTrue(second.cache_hit)

    def test_no_cache_bypasses_both_read_and_write(self):
        calls = []

        def transport(body):
            calls.append(body)
            return _response({"amendments": []})

        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, transport, use_cache=False)
            for _ in range(2):
                client.complete_json(
                    messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
                )
        self.assertEqual(len(calls), 2)

    def test_the_key_changes_with_the_model_the_messages_the_schema_and_the_settings(self):
        settings = CallSettings()
        base = _digest(TEXT_MODEL.model_id, MESSAGES, SCHEMA, settings)
        self.assertNotEqual(base, _digest(VISION_MODEL.model_id, MESSAGES, SCHEMA, settings))
        self.assertNotEqual(
            base, _digest(TEXT_MODEL.model_id, [{"role": "user", "content": "other"}], SCHEMA, settings)
        )
        self.assertNotEqual(base, _digest(TEXT_MODEL.model_id, MESSAGES, {"type": "object"}, settings))
        self.assertNotEqual(
            base, _digest(TEXT_MODEL.model_id, MESSAGES, SCHEMA, CallSettings(seed=999))
        )

    def test_a_corrupt_cache_entry_is_a_miss_and_not_a_crash(self):
        calls = []

        def transport(body):
            calls.append(body)
            return _response({"amendments": []})

        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, transport)
            key = _digest(TEXT_MODEL.model_id, MESSAGES, SCHEMA, client.settings)
            path = client._cache_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            result = client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 1)


class TestRunLogAndAccounting(unittest.TestCase):
    def test_a_call_is_recorded_with_tokens_cost_and_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, lambda body: _response({"amendments": []}))
            client.complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
            path = client.flush_run_log()
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["model"], TEXT_MODEL.model_id)
        self.assertEqual(record["input_tokens"], 120)
        self.assertEqual(record["output_tokens"], 30)
        self.assertEqual(record["reported_cost_usd"], 0.00012)
        self.assertAlmostEqual(record["estimated_cost_usd"], TEXT_MODEL.cost(120, 30))
        self.assertTrue(record["schema_valid"])

    def test_the_log_records_a_prompt_hash_and_never_the_prompt(self):
        secret = "salary is EUR 1661 for Ms Example"
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, lambda body: _response({"amendments": []}))
            client.complete_json(
                messages=[{"role": "user", "content": secret}],
                response_schema=SCHEMA,
                model=TEXT_MODEL,
                purpose="extract",
            )
            text = json.dumps(client.records)
        self.assertNotIn(secret, text)
        self.assertIn("request_hash", text)

    def test_a_dropped_amendment_is_recorded_with_its_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, lambda body: _response({"amendments": []}))
            client.note_dropped("extract", "references a nonexistent event", {"event_id": "event_x"})
            self.assertEqual(client.records[0]["record"], "dropped")
            self.assertIn("nonexistent", client.records[0]["reason"])

    def test_flushing_an_empty_log_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, lambda body: _response({"amendments": []}))
            self.assertIsNone(client.flush_run_log())

    def test_cost_is_computed_from_the_pinned_prices(self):
        self.assertAlmostEqual(TEXT_MODEL.cost(1_000_000, 0), 0.40)
        self.assertAlmostEqual(TEXT_MODEL.cost(0, 1_000_000), 1.60)
        self.assertIs(spec_for(TEXT_MODEL.model_id), TEXT_MODEL)
        self.assertIsNone(spec_for("openai/not-a-model"))


class TestRequestBody(unittest.TestCase):
    def test_the_request_asks_for_a_strict_json_schema_and_reported_usage(self):
        seen = {}

        def transport(body):
            seen.update(body)
            return _response({"amendments": []})

        with tempfile.TemporaryDirectory() as tmp:
            _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
            )
        self.assertEqual(seen["response_format"]["type"], "json_schema")
        self.assertTrue(seen["response_format"]["json_schema"]["strict"])
        self.assertEqual(seen["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(seen["usage"], {"include": True})
        self.assertEqual(seen["temperature"], 0.0)
        self.assertEqual(seen["seed"], CallSettings().seed)

    def test_temperature_is_omitted_for_a_model_that_does_not_accept_it(self):
        seen = {}

        def transport(body):
            seen.update(body)
            return _response({"amendments": []})

        from dataclasses import replace

        model = replace(TEXT_MODEL, supports_temperature=False)
        with tempfile.TemporaryDirectory() as tmp:
            _client(tmp, transport).complete_json(
                messages=MESSAGES, response_schema=SCHEMA, model=model, purpose="extract"
            )
        self.assertNotIn("temperature", seen)


class TestKeyResolution(unittest.TestCase):
    """The key comes from the environment, or from a gitignored .env (PLAN.md assumption 11)."""

    def setUp(self):
        import os

        from buyorwait.model import client as client_module

        self.os = os
        self.client_module = client_module
        self.saved_locations = client_module._ENV_FILE_LOCATIONS
        self.saved_env = os.environ.get("OPENROUTER_API_KEY")

    def tearDown(self):
        self.client_module._ENV_FILE_LOCATIONS = self.saved_locations
        if self.saved_env is None:
            self.os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            self.os.environ["OPENROUTER_API_KEY"] = self.saved_env

    def _with_env_file(self, contents):
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / ".env"
        path.write_text(contents, encoding="utf-8")
        self.client_module._ENV_FILE_LOCATIONS = (path,)
        return path

    def test_a_real_environment_variable_wins(self):
        self.os.environ["OPENROUTER_API_KEY"] = "from-environment"
        self._with_env_file("OPENROUTER_API_KEY=from-file\n")
        self.assertEqual(self.client_module.api_key_from_environment(), "from-environment")

    def test_a_plain_env_file_line_is_read(self):
        self.os.environ.pop("OPENROUTER_API_KEY", None)
        self._with_env_file("OPENROUTER_API_KEY=abc123\n")
        self.assertEqual(self.client_module.api_key_from_environment(), "abc123")

    def test_export_prefixes_quotes_comments_and_other_keys_are_handled(self):
        self.os.environ.pop("OPENROUTER_API_KEY", None)
        self._with_env_file(
            "# a comment\nOTHER_KEY=nope\nexport OPENROUTER_API_KEY=\"quoted-value\"\n"
        )
        self.assertEqual(self.client_module.api_key_from_environment(), "quoted-value")

    def test_no_env_file_and_no_variable_means_no_key(self):
        self.os.environ.pop("OPENROUTER_API_KEY", None)
        self.client_module._ENV_FILE_LOCATIONS = (Path(tempfile.mkdtemp()) / "absent.env",)
        self.assertIsNone(self.client_module.api_key_from_environment())

    def test_an_empty_value_is_treated_as_absent_rather_than_as_a_key(self):
        self.os.environ.pop("OPENROUTER_API_KEY", None)
        self._with_env_file("OPENROUTER_API_KEY=\n")
        self.assertIsNone(self.client_module.api_key_from_environment())


def strict_mode_violations(node, path="$"):
    """Object nodes where `required` does not list every key in `properties`.

    OpenAI's strict structured-output mode requires exactly that, and expresses optionality
    with a nullable type rather than by omission from `required`. A schema that gets this
    wrong is rejected with HTTP 400 before a single token is generated.
    """
    problems = []
    if isinstance(node, dict):
        if node.get("type") == "object" and node.get("additionalProperties") is False:
            properties = set(node.get("properties", {}))
            required = set(node.get("required", []))
            if properties != required:
                problems.append(
                    f"{path}: not required: {sorted(properties - required)}; "
                    f"required but absent: {sorted(required - properties)}"
                )
        for key, value in node.items():
            problems += strict_mode_violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            problems += strict_mode_violations(value, f"{path}[{index}]")
    return problems


class TestSchemasSatisfyStrictMode(unittest.TestCase):
    """Every schema sent with strict:true must satisfy the provider's own strict rules.

    This is a regression test for a real failure: the first live run made 198 calls and every
    one came back HTTP 400, because the amendment schema listed only three of its ten
    properties as required. The failure isolation held - the run produced a valid 250-row
    output.csv - which is exactly why it was invisible in the output and only visible in the
    run log.
    """

    def test_the_amendment_schema_requires_every_property(self):
        from buyorwait.model.extract import AMENDMENT_SCHEMA

        self.assertEqual(strict_mode_violations(AMENDMENT_SCHEMA), [])

    def test_the_checker_catches_a_schema_that_omits_a_property(self):
        bad = {
            "type": "object",
            "additionalProperties": False,
            "required": ["a"],
            "properties": {"a": {"type": "string"}, "b": {"type": ["string", "null"]}},
        }
        self.assertTrue(strict_mode_violations(bad))

    def test_optional_fields_are_expressed_as_nullable_types(self):
        from buyorwait.model.extract import AMENDMENT_SCHEMA

        properties = AMENDMENT_SCHEMA["properties"]["amendments"]["items"]["properties"]
        for name in ("amount", "currency", "effective_date", "percent_change",
                     "target_category", "target_description"):
            with self.subTest(field=name):
                self.assertIn("null", properties[name]["type"])


class TestHttpErrorsCarryTheProvidersReason(unittest.TestCase):
    """"HTTP Error 400: Bad Request" alone cost a CI round trip to diagnose once."""

    def test_the_providers_error_body_reaches_the_raised_error(self):
        import io
        import urllib.error
        import urllib.request

        from buyorwait.model import client as client_module

        body = b'{"error":{"message":"Invalid schema: required is not equal to properties"}}'

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                url=request.full_url, code=400, msg="Bad Request", hdrs=None, fp=io.BytesIO(body)
            )

        saved = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with tempfile.TemporaryDirectory() as tmp:
                client = ModelClient(
                    api_key="k",
                    cache_dir=Path(tmp) / "cache",
                    run_log_path=Path(tmp) / "log.jsonl",
                )
                with self.assertRaises(RuntimeError) as caught:
                    client._post({"model": "openai/gpt-4.1-mini", "messages": []})
        finally:
            urllib.request.urlopen = saved
            del client_module
        message = str(caught.exception)
        self.assertIn("HTTP 400 from OpenRouter", message)
        self.assertIn("required is not equal to properties", message)

    def test_a_failed_call_records_that_reason_in_the_run_log(self):
        import io
        import urllib.error
        import urllib.request

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=402,
                msg="Payment Required",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"Insufficient credits"}}'),
            )

        saved = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with tempfile.TemporaryDirectory() as tmp:
                client = ModelClient(
                    api_key="k",
                    cache_dir=Path(tmp) / "cache",
                    run_log_path=Path(tmp) / "log.jsonl",
                )
                result = client.complete_json(
                    messages=MESSAGES, response_schema=SCHEMA, model=TEXT_MODEL, purpose="extract"
                )
        finally:
            urllib.request.urlopen = saved
        self.assertFalse(result.ok)
        self.assertIn("Insufficient credits", result.error)
        self.assertIn("Insufficient credits", client.records[0]["error"])


if __name__ == "__main__":
    unittest.main()
