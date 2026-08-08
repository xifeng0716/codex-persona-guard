import os
import stat
import tempfile
import unittest
from pathlib import Path

from persona_guard.defaults import DEFAULT_DETECTOR_PROMPT
from persona_guard.state_machine import GuardSnapshot
from persona_guard.storage import Storage, normalize_cwd, state_db_path


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state" / "persona-guard" / "guard.db"
        self.storage = Storage(self.db_path)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_path_and_permissions_are_user_only(self):
        self.assertEqual(
            state_db_path({"XDG_STATE_HOME": "/tmp/state", "HOME": "/tmp/home"}),
            Path("/tmp/state/persona-guard/guard.db"),
        )
        self.assertEqual(stat.S_IMODE(self.db_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)

    def test_policy_update_resets_state_and_records_keep_snapshots(self):
        self.storage.save_state("thread", GuardSnapshot(state="ARMED", previous_watch_type="other"))
        old = self.storage.get_policy()
        self.storage.record_attempt(
            session_id="thread",
            history=[{"role": "user", "content": "old"}],
            current_prompt="current",
            policy_text=old["text"],
            policy_revision=old["revision"],
            result="NONE",
            decision_type="other",
            error_category=None,
            state_before=GuardSnapshot.normal(),
            state_after=GuardSnapshot.normal(),
            binding=None,
            injected=False,
            model="deepseek-v4-flash",
            latency_ms=1,
        )
        updated = self.storage.update_policy("new policy")
        self.assertEqual(updated["revision"], old["revision"] + 1)
        self.assertEqual(self.storage.get_state("thread"), GuardSnapshot.normal())
        self.assertEqual(self.storage.list_records()[0]["policy_revision"], old["revision"])
        self.assertEqual(self.storage.get_policy()["text"], "new policy")

    def test_binding_precedence_and_thread_deletion_state(self):
        cwd = normalize_cwd(self.temp_dir.name)
        workspace = self.storage.create_binding("workspace", "workspace", cwd, True, "workspace reminder")
        self.storage.save_state("thread", GuardSnapshot(state="HOT", hot_remaining=4))
        thread = self.storage.create_binding("thread", "thread", "thread", True, "thread reminder")
        self.assertEqual(self.storage.find_binding("thread", cwd).id, thread.id)
        self.storage.delete_binding(thread.id)
        self.assertEqual(self.storage.get_state("thread"), GuardSnapshot.normal())
        self.assertEqual(self.storage.find_binding("thread", cwd).id, workspace.id)

    def test_discovery_never_has_prompt_and_deleted_binding_records_remain(self):
        binding = self.storage.create_binding("thread", "thread", "s1", True, "reminder")
        self.storage.upsert_discovery("s1", ".")
        self.assertEqual(self.storage.list_discoveries()[0].keys(), {"session_id", "cwd", "last_seen"})
        self.storage.record_attempt(
            session_id="s1",
            history=[],
            current_prompt="private",
            policy_text=DEFAULT_DETECTOR_PROMPT,
            policy_revision=1,
            result="ERROR",
            decision_type=None,
            error_category="timeout",
            state_before=GuardSnapshot.normal(),
            state_after=GuardSnapshot.normal(),
            binding=binding,
            injected=False,
            model="deepseek-v4-flash",
            latency_ms=4,
        )
        self.storage.delete_binding(binding.id)
        records = self.storage.list_records()
        self.assertEqual(records[0]["binding_id"], binding.id)
        self.assertEqual(records[0]["binding_snapshot"]["target_value"], "s1")


if __name__ == "__main__":
    unittest.main()
