from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-hook"
UNINSTALLER = ROOT / "scripts" / "uninstall-hook"
RUN_SERVER = ROOT / "scripts" / "run-server"
HOOK_COMMAND = 'python3 "$HOME/.codex/persona-guard/hook_client.py"'


class HookInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env.pop("PYTHONPATH", None)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @property
    def codex_dir(self) -> Path:
        return self.home / ".codex"

    @property
    def hooks_path(self) -> Path:
        return self.codex_dir / "hooks.json"

    def run_script(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_hooks(self) -> dict[str, object]:
        return json.loads(self.hooks_path.read_text(encoding="utf-8"))

    @staticmethod
    def persona_handlers(config: dict[str, object]) -> list[dict[str, object]]:
        hooks = config.get("hooks", {})
        if not isinstance(hooks, dict):
            return []
        entries = hooks.get("UserPromptSubmit", [])
        if not isinstance(entries, list):
            return []
        found: list[dict[str, object]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            children = entry.get("hooks")
            if isinstance(children, list):
                found.extend(
                    child
                    for child in children
                    if isinstance(child, dict) and child.get("command") == HOOK_COMMAND
                )
        return found

    def test_install_merges_unrelated_hooks_and_creates_backup(self) -> None:
        original = {
            "hooks": {
                "PostCompact": [{"hooks": [{"type": "command", "command": "keep-post"}]}],
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "keep-prompt"}]}
                ],
            },
            "unrelated": {"preserve": True},
        }
        self.codex_dir.mkdir(parents=True)
        self.hooks_path.write_text(json.dumps(original), encoding="utf-8")

        result = self.run_script(INSTALLER)

        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.read_hooks()
        self.assertEqual(installed["hooks"]["PostCompact"], original["hooks"]["PostCompact"])
        self.assertEqual(installed["hooks"]["UserPromptSubmit"][0], original["hooks"]["UserPromptSubmit"][0])
        self.assertEqual(installed["unrelated"], original["unrelated"])
        handlers = self.persona_handlers(installed)
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]["type"], "command")
        self.assertEqual(handlers[0]["timeout"], 5)
        self.assertIn("$HOME/.codex/persona-guard/hook_client.py", handlers[0]["command"])

        target = self.home / ".codex" / "persona-guard" / "hook_client.py"
        self.assertTrue(target.is_file())
        self.assertTrue(stat.S_IMODE(target.stat().st_mode) & stat.S_IXUSR)
        backups = sorted(self.codex_dir.glob("hooks.json.persona-guard.bak*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), json.dumps(original))

    def test_install_is_idempotent_and_deduplicates_own_handlers(self) -> None:
        first = self.run_script(INSTALLER)
        self.assertEqual(first.returncode, 0, first.stderr)
        config = self.read_hooks()
        entries = config["hooks"]["UserPromptSubmit"]
        self.assertIsInstance(entries, list)
        entries.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
        self.hooks_path.write_text(json.dumps(config), encoding="utf-8")
        backup_count_before = len(list(self.codex_dir.glob("hooks.json.persona-guard.bak*")))

        second = self.run_script(INSTALLER)

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.persona_handlers(self.read_hooks())), 1)
        self.assertEqual(
            len(list(self.codex_dir.glob("hooks.json.persona-guard.bak*"))),
            backup_count_before + 1,
        )
        third = self.run_script(INSTALLER)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(len(self.persona_handlers(self.read_hooks())), 1)
        self.assertEqual(
            len(list(self.codex_dir.glob("hooks.json.persona-guard.bak*"))),
            backup_count_before + 1,
        )

    def test_uninstall_removes_only_persona_guard_and_is_idempotent(self) -> None:
        self.assertEqual(self.run_script(INSTALLER).returncode, 0)
        config = self.read_hooks()
        entries = config["hooks"]["UserPromptSubmit"]
        self.assertIsInstance(entries, list)
        entries[0]["hooks"].append({"type": "command", "command": "added-after-install"})
        config["hooks"]["SessionStart"] = [
            {"hooks": [{"type": "command", "command": "keep-session"}]}
        ]
        self.hooks_path.write_text(json.dumps(config), encoding="utf-8")
        keep_file = self.home / ".codex" / "persona-guard" / "keep.txt"
        keep_file.write_text("unrelated", encoding="utf-8")

        result = self.run_script(UNINSTALLER)

        self.assertEqual(result.returncode, 0, result.stderr)
        remaining = self.read_hooks()
        self.assertEqual(
            remaining["hooks"]["UserPromptSubmit"],
            [{"hooks": [{"type": "command", "command": "added-after-install"}]}],
        )
        self.assertEqual(
            remaining["hooks"]["SessionStart"],
            [{"hooks": [{"type": "command", "command": "keep-session"}]}],
        )
        self.assertTrue(keep_file.is_file())
        self.assertFalse((self.home / ".codex" / "persona-guard" / "hook_client.py").exists())

        second = self.run_script(UNINSTALLER)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.read_hooks(), remaining)
        self.assertTrue(keep_file.is_file())

    def test_invalid_hooks_file_is_not_overwritten(self) -> None:
        self.codex_dir.mkdir(parents=True)
        self.hooks_path.write_text("{not json", encoding="utf-8")

        result = self.run_script(INSTALLER)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hooks_path.read_text(encoding="utf-8"), "{not json")
        self.assertFalse((self.home / ".codex" / "persona-guard").exists())

    def test_run_server_keeps_inherited_keys_and_uses_localhost_port(self) -> None:
        fake_bin = self.home / "bin"
        fake_bin.mkdir()
        args_file = self.home / "run-server-args.txt"
        fake_python = fake_bin / "python3"
        fake_python.write_text(
            "#!/bin/sh\n"
            "{\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf '%s\\n' \"${GMEM_DEEPSEEK_API_KEY:-}\"\n"
            "  printf '%s\\n' \"${DEEPSEEK_API_KEY:-}\"\n"
            f"}} > {shlex.quote(str(args_file))}\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        test_env = self.env.copy()
        test_env["PATH"] = f"{fake_bin}{os.pathsep}{test_env['PATH']}"
        test_env["GMEM_DEEPSEEK_API_KEY"] = "compat-key"
        test_env["DEEPSEEK_API_KEY"] = "portable-key"

        result = subprocess.run(
            [str(RUN_SERVER)],
            cwd=ROOT,
            env=test_env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        args = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(args, ["-m", "persona_guard.server", "compat-key", "portable-key"])


if __name__ == "__main__":
    unittest.main()
