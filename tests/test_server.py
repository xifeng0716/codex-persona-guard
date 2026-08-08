import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from persona_guard.detector import DetectorError
from persona_guard.server import PersonaGuardApp, create_server
from persona_guard.state_machine import GuardSnapshot
from persona_guard.storage import Storage, normalize_cwd


class FakeDetector:
    model = "deepseek-v4-flash"

    def __init__(self, result="HIT", decision_type="emotion", error=None):
        self.result = result
        self.decision_type = decision_type
        self.error = error
        self.calls = []
        self.environ = {}

    def classify(self, history, current_prompt, guard_state, policy_text):
        self.calls.append((history, current_prompt, guard_state, policy_text))
        if self.error:
            raise DetectorError(self.error)
        return type("Decision", (), {"result": self.result, "type": self.decision_type})()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "guard.db")
        self.detector = FakeDetector()
        self.cwd = normalize_cwd(self.temp_dir.name)
        self.binding = self.storage.create_binding("thread", "thread", "s1", True, "REMINDER")
        self.transcript = Path(self.temp_dir.name) / "history.jsonl"
        self.transcript.write_text(
            json.dumps({"role": "assistant", "content": "old"}) + "\n", encoding="utf-8"
        )
        self.app = PersonaGuardApp(
            storage=self.storage,
            detector=self.detector,
            transcript_reader=lambda path, prompt, maximum: [
                {"role": "assistant", "content": "old"}
            ],
        )

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def payload(self, session="s1", prompt="hello"):
        return {
            "session_id": session,
            "cwd": self.cwd,
            "prompt": prompt,
            "transcript_path": str(self.transcript),
        }

    def test_hook_injects_only_hit_and_records_exact_bound_attempt(self):
        output = self.app.handle_hook(self.payload(prompt="I am sad"))
        self.assertEqual(
            output,
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "REMINDER",
                }
            },
        )
        self.assertEqual(len(self.detector.calls), 1)
        self.assertEqual(self.detector.calls[0][1], "I am sad")
        self.assertEqual(self.storage.get_state("s1").state, "HOT")
        record = self.storage.list_records()[0]
        self.assertEqual(record["current_prompt"], "I am sad")
        self.assertEqual(record["binding_id"], self.binding.id)
        self.assertTrue(record["injected"])

    def test_unbound_and_disabled_hooks_only_update_discovery(self):
        output = self.app.handle_hook(self.payload(session="new", prompt="private"))
        self.assertEqual(output, {})
        self.assertEqual(self.detector.calls, [])
        self.assertEqual(self.storage.count_records(), 0)
        self.assertEqual(self.storage.list_discoveries()[0]["session_id"], "new")

        self.storage.set_enabled(False)
        self.assertEqual(self.app.handle_hook(self.payload(prompt="disabled")), {})
        self.assertEqual(len(self.detector.calls), 0)
        self.assertEqual(self.storage.count_records(), 0)

    def test_failure_records_error_and_preserves_state(self):
        self.storage.save_state("s1", GuardSnapshot(state="ARMED", previous_watch_type="other"))
        self.detector.error = "timeout"
        self.assertEqual(self.app.handle_hook(self.payload(prompt="private")), {})
        self.assertEqual(
            self.storage.get_state("s1"),
            GuardSnapshot(state="ARMED", previous_watch_type="other"),
        )
        record = self.storage.list_records()[0]
        self.assertEqual(record["result"], "ERROR")
        self.assertEqual(record["error_category"], "timeout")
        self.assertFalse(record["injected"])

    def test_recording_failure_does_not_change_state_or_inject(self):
        def fail_record(**kwargs):
            raise OSError("database write failed")

        self.storage.record_attempt = fail_record
        self.assertEqual(self.app.handle_hook(self.payload(prompt="I am sad")), {})
        self.assertEqual(self.storage.get_state("s1"), GuardSnapshot.normal())

    def test_thread_binding_precedes_workspace_binding(self):
        self.storage.create_binding("workspace", "workspace", self.cwd, True, "WORKSPACE")
        self.assertEqual(self.app.handle_hook(self.payload()), {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "REMINDER",
            }
        })

    def test_http_api_and_static_serving(self):
        static_dir = Path(self.temp_dir.name) / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("ok", encoding="utf-8")
        try:
            server = create_server(storage=self.storage, detector=self.detector, host="127.0.0.1", port=0, static_dir=static_dir)
        except PermissionError:
            self.skipTest("the test environment does not permit local sockets")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/api/status")
            response = connection.getresponse()
            status = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertIn("key_present", status)
            connection.close()

            connection = HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
