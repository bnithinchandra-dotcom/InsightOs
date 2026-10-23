from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from training.evaluation.ollama_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    OLLAMA_SYSTEM_PROMPT,
    _chat_payload,
    _generator,
    _has_reasoning_leak,
    _request_json,
    _request_stream_json,
    _status_counts,
    _select_cases,
    _validate_local_base_url,
    OllamaConnectionFailure,
    OllamaHTTPFailure,
    OllamaMalformedResponse,
    OllamaMissingContent,
    OllamaRequestTimeout,
    OllamaUnexpectedResponse,
    _token_limit_profile,
)
from training.evaluation.hf_runner import (
    _safe_error,
    evaluate_cases_with_retries,
    load_suite,
)


class OllamaEvaluationTests(unittest.TestCase):
    def test_ollama_default_output_limit_allows_complete_answers(self):
        self.assertEqual(DEFAULT_MAX_OUTPUT_TOKENS, 512)

    def test_reasoning_leak_detects_tags_and_reasoning_style_openings(self):
        for text in (
            "Internal answer. </think> Final answer.",
            "<think>reasoning</think>Final answer.",
            "Okay, the user wants a plain-language explanation.",
            "Hmm, the user is asking for an exact token.",
            "Let me check the requirements.",
        ):
            with self.subTest(text=text):
                self.assertTrue(_has_reasoning_leak(text))

        for text in (
            "Revenue is income before costs.",
            '{"metric":"profit_margin","value":25,"unit":"%"}',
        ):
            with self.subTest(text=text):
                self.assertFalse(_has_reasoning_leak(text))

    def test_status_counts_report_generation_leak_rate(self):
        counts = _status_counts(
            [
                {
                    "status": "passed",
                    "generations": [
                        {"reasoning_leak": True, "raw_leak": True, "final_leak": True},
                        {"reasoning_leak": False, "raw_leak": True, "final_leak": False},
                    ],
                },
                {
                    "status": "failed",
                    "generations": [
                        {"reasoning_leak": True, "raw_leak": False, "final_leak": True}
                    ],
                },
            ]
        )
        self.assertEqual(counts["raw_leak_generations"], 2)
        self.assertAlmostEqual(counts["raw_leak_rate"], 2 / 3)
        self.assertEqual(counts["final_leak_generations"], 2)
        self.assertAlmostEqual(counts["final_leak_rate"], 2 / 3)
        self.assertEqual(counts["reasoning_leak_generations"], 2)
        self.assertAlmostEqual(counts["reasoning_leak_rate"], 2 / 3)

    def test_model_token_limit_profiles_preserve_instruct_limits_and_expand_thinking(self):
        instruct_profile, instruct_limits = _token_limit_profile("qwen3:4b-instruct")
        thinking_profile, thinking_limits = _token_limit_profile("qwen3:4b")
        self.assertEqual(instruct_profile, "instruct")
        self.assertEqual(instruct_limits["repeat_exact_token"], 64)
        self.assertEqual(instruct_limits["json_profit_margin"], 64)
        self.assertEqual(instruct_limits["instruction_exactly_two_sentences"], 256)
        self.assertEqual(instruct_limits["analysis_plan_profit_margin"], 768)
        self.assertEqual(thinking_profile, "thinking_capable")
        self.assertEqual(thinking_limits["analysis_plan_profit_margin"], 2048)
        self.assertGreater(
            thinking_limits["repeat_exact_token"],
            instruct_limits["repeat_exact_token"],
        )

    def test_evaluation_endpoint_rejects_non_local_hosts(self):
        for url in (
            "https://localhost:11434",
            "http://example.com:11434",
            "http://localhost:11434/api",
            "http://user@localhost:11434",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _validate_local_base_url(url)
        self.assertEqual(_validate_local_base_url(DEFAULT_BASE_URL), DEFAULT_BASE_URL)

    def test_chat_payload_disables_thinking_and_limits_output(self):
        cases = {item.id: item for item in load_suite()[1]}
        json_payload = _chat_payload(
            cases["json_profit_margin"],
            model="qwen3:4b",
            max_output_tokens=192,
        )
        self.assertFalse(json_payload["think"])
        self.assertTrue(json_payload["stream"])
        self.assertEqual(json_payload["messages"][0]["content"], OLLAMA_SYSTEM_PROMPT)
        self.assertIn("Do not reveal internal reasoning", OLLAMA_SYSTEM_PROMPT)
        self.assertEqual(json_payload["format"], "json")
        self.assertEqual(json_payload["options"], {"num_predict": 192, "temperature": 0})

        text_payload = _chat_payload(
            cases["instruction_exactly_two_sentences"],
            model="qwen3:4b",
            max_output_tokens=192,
            temperature=0.3,
            feedback="Return exactly two sentences and no analysis.",
        )
        self.assertNotIn("format", text_payload)
        self.assertEqual(
            text_payload["messages"][-1],
            {
                "role": "user",
                "content": "Return exactly two sentences and no analysis.",
            },
        )
        self.assertEqual(text_payload["options"]["temperature"], 0.3)

    def test_analysis_planning_prompt_states_schema_and_validation_requirements(self):
        cases = {item.id: item for item in load_suite()[1]}
        payload = _chat_payload(
            cases["analysis_plan_profit_margin"],
            model="qwen3:4b-instruct",
            max_output_tokens=768,
        )
        system_prompt = payload["messages"][0]["content"]
        user_prompt = payload["messages"][1]["content"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        for instruction in (
            "no actual file or schema",
            "actual schema was not provided",
            "date field exists and parses as dates",
            "validate required financial fields as numeric",
            "invalid dates, missing values, and nonnumeric values would be flagged or excluded",
            "Group valid records by calendar month",
            "profit = revenue - expenses",
            "only where revenue is valid and positive",
            "Zero revenue makes margin undefined, not 0%",
            "Flag negative revenue for validation or business-policy review",
            "defer its treatment until the applicable policy is known",
            "do not automatically classify it as invalid or exclude it without that policy",
            "Do not claim division by a negative number is mathematically undefined",
            "negative margin",
            "Do not claim a file was opened",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction.casefold(), system_prompt.casefold())
        self.assertIn("monthly profit margin", user_prompt)

    def test_case_selection_is_optional_validated_and_preserves_suite_order(self):
        cases = load_suite()[1]
        self.assertEqual(_select_cases(cases, None), cases)
        selected = _select_cases(
            cases,
            ["analysis_plan_profit_margin", "business_revenue_profit"],
        )
        self.assertEqual(
            [case.id for case in selected],
            ["analysis_plan_profit_margin", "business_revenue_profit"],
        )
        with self.assertRaisesRegex(ValueError, "At least one"):
            _select_cases(cases, [])
        with self.assertRaisesRegex(ValueError, "unique"):
            _select_cases(cases, ["analysis_plan_profit_margin"] * 2)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            _select_cases(cases, ["not-a-case"])

    def test_generator_uses_final_content_and_never_records_thinking_text(self):
        cases = {item.id: item for item in load_suite()[1]}
        usage = {}
        generate = _generator(
            base_url=DEFAULT_BASE_URL,
            model="qwen3:4b",
            timeout_seconds=17,
            max_output_tokens=192,
            api_usage=usage,
        )
        with patch(
            "training.evaluation.ollama_runner._request_stream_json",
            return_value={
                "message": {
                    "role": "assistant",
                    "content": "private reasoning</think>Final answer.",
                    "thinking": "private reasoning must not be persisted",
                },
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 3,
                "total_duration": 100,
                "stream_chunks": 2,
            },
        ):
            result = generate(cases["business_revenue_profit"])

        self.assertEqual(result.text, "private reasoning</think>Final answer.")
        self.assertEqual(result.raw_text, result.text)
        self.assertEqual(result.final_text, "Final answer.")
        self.assertTrue(result.raw_leak)
        self.assertFalse(result.final_leak)
        self.assertFalse(result.reasoning_leak)
        self.assertEqual(
            usage["business_revenue_profit"][0],
            {
                "prompt_tokens": 42,
                "output_tokens": 3,
                "total_duration_ns": 100,
                "load_duration_ns": None,
                "prompt_eval_duration_ns": None,
                "eval_duration_ns": None,
                "stream_chunks": 2,
                "separate_thinking_field_present": True,
                "separate_thinking_char_count": len(
                    "private reasoning must not be persisted"
                ),
            },
        )
        self.assertNotIn(
            "private reasoning must not be persisted",
            str(usage["business_revenue_profit"][0]),
        )

    def test_request_uses_timeout_and_parses_json(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = [b'{"version":"fixture"}', b""]
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        connection_factory = Mock(return_value=connection)
        with patch(
            "training.evaluation.ollama_runner.http.client.HTTPConnection",
            connection_factory,
        ):
            result = _request_json(
                "http://localhost:11434/api/version",
                timeout_seconds=17,
            )
        self.assertEqual(result, {"version": "fixture"})
        self.assertEqual(connection_factory.call_args.kwargs["timeout"], 17)
        self.assertEqual(connection.request.call_args.args[:2], ("GET", "/api/version"))
        response.close.assert_called()
        connection.close.assert_called()

    def test_request_rejects_oversized_response(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.return_value = b"x" * (1024 * 1024 + 1)
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with patch(
            "training.evaluation.ollama_runner.http.client.HTTPConnection",
            return_value=connection,
        ), self.assertRaises(OllamaUnexpectedResponse):
            _request_json("http://localhost:11434/api/version")

    def test_stream_request_deadline_uses_injected_clock_without_sleeping(self):
        class FakeClock:
            current = 0.0

            def __call__(self):
                return self.current

            def advance(self, seconds):
                self.current += seconds

        class FakeReader:
            def __init__(self, clock):
                self.clock = clock
                self.closed = False

            def read1(self, _limit):
                self.clock.advance(0.06)
                return b"partial response bytes"

            def close(self):
                self.closed = True

        clock = FakeClock()
        reader = FakeReader(clock)
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = reader.read1
        response.close.side_effect = reader.close
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(OllamaRequestTimeout) as raised:
            _request_stream_json(
                "http://localhost:11434/api/version",
                payload={"stream": True},
                timeout_seconds=0.05,
                http_connection_factory=lambda *_args, **_kwargs: connection,
                clock=clock,
            )
        self.assertIn("0.05-second deadline", str(raised.exception))
        self.assertTrue(reader.closed)
        connection.close.assert_called()

    def test_stream_request_reports_connection_refusal(self):
        connection = Mock()
        connection.sock = None
        connection.request.side_effect = ConnectionRefusedError("connection refused")
        with self.assertRaises(OllamaConnectionFailure) as raised:
            _request_stream_json(
                "http://localhost:11434/api/version",
                payload={"stream": True},
                timeout_seconds=17,
                http_connection_factory=lambda *_args, **_kwargs: connection,
            )
        self.assertIn(
            "(ConnectionRefusedError): connection refused",
            str(raised.exception),
        )
        error = _safe_error(raised.exception)
        self.assertEqual(error["category"], "connection_error")
        self.assertEqual(error["type"], "ConnectionRefusedError")
        self.assertLessEqual(len(error["message"]), 200)
        connection.request.assert_called_once()
        connection.close.assert_called()

    def test_stream_request_reports_http_errors_with_status_and_closes_response(self):
        response = Mock()
        response.status = 503
        response.reason = "Unavailable"
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(OllamaHTTPFailure) as raised:
            _request_stream_json(
                "http://localhost:11434/api/version",
                payload={"stream": True},
                http_connection_factory=lambda *_args, **_kwargs: connection,
            )
        self.assertIn("503 Unavailable", str(raised.exception))
        self.assertEqual(raised.exception.http_status, 503)
        error = _safe_error(raised.exception)
        self.assertEqual(error["category"], "http_error")
        self.assertEqual(error["http_status"], 503)
        response.close.assert_called()
        connection.close.assert_called()

    def test_stream_request_rejects_invalid_json(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = [b"{invalid\n"]
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(OllamaMalformedResponse) as raised:
            _request_stream_json(
                "http://localhost:11434/api/chat",
                payload={"stream": True},
                http_connection_factory=lambda *_args, **_kwargs: connection,
            )
        self.assertEqual(raised.exception.category, "malformed_json")
        self.assertEqual(_safe_error(raised.exception)["type"], "JSONDecodeError")
        response.close.assert_called()
        connection.close.assert_called()

    def test_stream_request_classifies_missing_done_record_as_unexpected(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = [
            b'{"message":{"content":"partial"},"done":false}\n',
            b"",
        ]
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(OllamaUnexpectedResponse) as raised:
            _request_stream_json(
                "http://localhost:11434/api/chat",
                payload={"stream": True},
                http_connection_factory=lambda *_args, **_kwargs: connection,
            )
        self.assertEqual(raised.exception.category, "unexpected")
        response.close.assert_called()
        connection.close.assert_called()

    def test_stream_request_rejects_missing_message_content(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = [
            b'{"message":{"content":""},"done":true}\n'
        ]
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(OllamaMissingContent):
            _request_stream_json(
                "http://localhost:11434/api/chat",
                payload={"stream": True},
                http_connection_factory=lambda *_args, **_kwargs: connection,
            )
        response.close.assert_called()
        connection.close.assert_called()

    def test_stream_request_reassembles_content_and_done_metadata(self):
        response = Mock()
        response.status = 200
        response.reason = "OK"
        response.read1.side_effect = [
            (
                b'{"message":{"content":"Ans"},"done":false}\n'
                b'{"message":{"content":"wer"},"done":false}\n'
                b'{"message":{"content":""},"done":true,"done_reason":"stop",'
                b'"prompt_eval_count":5,"eval_count":2,"total_duration":100}\n'
            ),
        ]
        connection = Mock()
        connection.sock = Mock()
        connection.getresponse.return_value = response
        result = _request_stream_json(
            "http://localhost:11434/api/chat",
            payload={"stream": True},
            http_connection_factory=lambda *_args, **_kwargs: connection,
        )
        self.assertEqual(result["message"]["content"], "Answer")
        self.assertEqual(result["done_reason"], "stop")
        self.assertEqual(result["eval_count"], 2)
        self.assertEqual(result["total_duration"], 100)
        self.assertEqual(result["stream_chunks"], 3)
        response.close.assert_called()
        connection.close.assert_called()

    def test_generator_separates_raw_and_final_reasoning_leaks(self):
        cases = {item.id: item for item in load_suite()[1]}
        generate = _generator(
            base_url=DEFAULT_BASE_URL,
            model="qwen3:4b-instruct",
            timeout_seconds=17,
            max_output_tokens=192,
            api_usage={},
        )
        with patch(
            "training.evaluation.ollama_runner._request_stream_json",
            return_value={
                "message": {
                    "content": "<think>private reasoning</think>Revenue is income.",
                },
                "done_reason": "stop",
                "eval_count": 6,
                "stream_chunks": 2,
            },
        ):
            result = generate(cases["business_revenue_profit"])
        self.assertTrue(result.raw_leak)
        self.assertFalse(result.final_leak)
        self.assertFalse(result.reasoning_leak)
        self.assertEqual(result.final_text, "Revenue is income.")


    def test_transport_error_is_not_retried_by_evaluation_loop(self):
        cases = {item.id: item for item in load_suite()[1]}
        generate = _generator(
            base_url=DEFAULT_BASE_URL,
            model="qwen3:4b",
            timeout_seconds=17,
            max_output_tokens=192,
            api_usage={},
        )
        request = Mock(side_effect=OllamaConnectionFailure("refused"))
        with patch(
            "training.evaluation.ollama_runner._request_stream_json",
            request,
        ):
            results = evaluate_cases_with_retries(
                (cases["business_revenue_profit"],),
                generate,
                max_attempts=3,
                stop_on_generation_error=True,
            )
        request.assert_called_once()
        self.assertEqual(results[0]["error"]["category"], "connection_error")


if __name__ == "__main__":
    unittest.main()
