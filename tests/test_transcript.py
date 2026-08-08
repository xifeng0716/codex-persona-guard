import json
import tempfile
import unittest
from pathlib import Path

from persona_guard.transcript import TranscriptError, read_history


class TranscriptTests(unittest.TestCase):
    def write_lines(self, values):
        path = Path(self.temp_dir.name) / "rollout.jsonl"
        path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")
        return str(path)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_latest_three_of_each_role_and_excludes_current_duplicate(self):
        values = []
        for index in range(5):
            values.append({"role": "user", "content": f"u{index}"})
            values.append({"role": "assistant", "content": f"a{index}"})
        values.append({"type": "event_msg", "payload": {"type": "user_message", "message": "current"}})
        values.append({"role": "user", "content": "current"})
        history = read_history(self.write_lines(values), "current")
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
                {"role": "user", "content": "u4"},
                {"role": "assistant", "content": "a4"},
            ],
        )

    def test_supports_codex_payload_shapes_and_deduplicates_adjacent_records(self):
        path = self.write_lines(
            [
                {"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hi"}],
                    },
                },
            ]
        )
        self.assertEqual(
            read_history(path, "new"),
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        )

    def test_insufficient_history_is_valid_and_bad_json_fails(self):
        path = self.write_lines([{"role": "user", "content": "one"}])
        self.assertEqual(read_history(path, "different"), [{"role": "user", "content": "one"}])
        bad = Path(self.temp_dir.name) / "bad.jsonl"
        bad.write_text('{"role":"user"\n', encoding="utf-8")
        with self.assertRaises(TranscriptError) as raised:
            read_history(str(bad), "current")
        self.assertEqual(raised.exception.category, "transcript_unparseable")

    def test_six_total_messages_still_caps_each_role_at_three(self):
        path = self.write_lines(
            [
                {"role": "user", "content": "u0"},
                {"role": "user", "content": "u1"},
                {"role": "user", "content": "u2"},
                {"role": "user", "content": "u3"},
                {"role": "user", "content": "u4"},
                {"role": "assistant", "content": "a0"},
            ]
        )
        self.assertEqual(
            read_history(path, "current"),
            [
                {"role": "user", "content": "u2"},
                {"role": "user", "content": "u3"},
                {"role": "user", "content": "u4"},
                {"role": "assistant", "content": "a0"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
