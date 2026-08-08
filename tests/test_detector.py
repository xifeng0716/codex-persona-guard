import json
import os
import unittest

from persona_guard.detector import Detector, DetectorError, build_detector_input, parse_decision
from persona_guard.state_machine import GuardSnapshot


class FakeResponse:
    status = 200

    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode("utf-8")

    def close(self):
        pass


class DetectorTests(unittest.TestCase):
    def test_request_uses_confirmed_deepseek_contract(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                {"choices": [{"message": {"content": '{"result":"HIT","type":"emotion"}'}}]}
            )

        detector = Detector(environ={"DEEPSEEK_API_KEY": "secret"}, opener=opener)
        decision = detector.classify(
            [{"role": "assistant", "content": "hello"}],
            "I am sad",
            GuardSnapshot.normal(),
            "policy",
        )
        self.assertEqual(decision.as_dict(), {"result": "HIT", "type": "emotion"})
        body = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-v4-flash")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 100)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(captured["timeout"], 4.0)
        self.assertEqual(captured["request"].get_header("Authorization"), "Bearer secret")

    def test_primary_key_precedes_portable_key_and_missing_key_fails(self):
        self.assertTrue(Detector(environ={"GMEM_DEEPSEEK_API_KEY": "a", "DEEPSEEK_API_KEY": "b"}).api_key_present)
        detector = Detector(environ={})
        with self.assertRaises(DetectorError) as raised:
            detector.classify([], "prompt", "NORMAL", "policy")
        self.assertEqual(raised.exception.category, "missing_api_key")

    def test_enum_validation_is_strict(self):
        for value in (
            {"result": "hit", "type": "emotion"},
            {"result": "HIT", "type": "unknown"},
            {"result": "NONE"},
        ):
            with self.assertRaises(DetectorError):
                parse_decision(value)

    def test_detector_input_does_not_repeat_current_prompt(self):
        text = build_detector_input(
            [{"role": "user", "content": "old"}],
            "current",
            GuardSnapshot(state="ARMED", previous_watch_type="uncertainty"),
        )
        self.assertEqual(text.count("current"), 1)
        self.assertIn("previous_watch_type: uncertainty", text)


if __name__ == "__main__":
    unittest.main()
