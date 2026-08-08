from __future__ import annotations

import io
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest import mock


CLIENT_PATH = Path(__file__).resolve().parent.parent / "persona_guard" / "hook_client.py"
CLIENT_SPEC = importlib.util.spec_from_file_location("persona_guard_hook_client", CLIENT_PATH)
if CLIENT_SPEC is None or CLIENT_SPEC.loader is None:  # pragma: no cover - import setup failure.
    raise RuntimeError(f"cannot load {CLIENT_PATH}")
hook_client = importlib.util.module_from_spec(CLIENT_SPEC)
CLIENT_SPEC.loader.exec_module(hook_client)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.closed = False

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


class HookClientTests(unittest.TestCase):
    def run_client(self, payload: str) -> tuple[int, str]:
        output = io.StringIO()
        code = hook_client.main(io.StringIO(payload), output)
        return code, output.getvalue()

    def test_posts_one_object_and_forwards_service_json_unchanged(self) -> None:
        response = FakeResponse(b'{"hookSpecificOutput": {"x": "\xe7\x94\xa8"}}\n')
        with mock.patch.object(hook_client.urllib.request, "urlopen", return_value=response) as urlopen:
            code, output = self.run_client('{"prompt":"hello","n":1}\n')

        self.assertEqual(code, 0)
        self.assertEqual(output, '{"hookSpecificOutput": {"x": "用"}}\n')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, hook_client.HOOK_URL)
        self.assertEqual(request.data, b'{"prompt":"hello","n":1}\n')
        self.assertEqual(request.get_header("Content-type"), "application/json")
        timeout = urlopen.call_args.kwargs["timeout"]
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, hook_client.TOTAL_TIMEOUT_SECONDS)

    def test_malformed_or_non_object_input_is_fail_soft(self) -> None:
        with mock.patch.object(hook_client.urllib.request, "urlopen") as urlopen:
            for payload in ("", "not json", "[]", "{} {}"):
                code, output = self.run_client(payload)
                self.assertEqual(code, 0)
                self.assertEqual(output, "")
        urlopen.assert_not_called()

    def test_local_error_and_invalid_response_are_fail_soft(self) -> None:
        with mock.patch.object(
            hook_client.urllib.request,
            "urlopen",
            side_effect=OSError("service unavailable"),
        ):
            code, output = self.run_client("{}")
        self.assertEqual((code, output), (0, ""))

        with mock.patch.object(
            hook_client.urllib.request,
            "urlopen",
            return_value=FakeResponse(b"not json"),
        ):
            code, output = self.run_client("{}")
        self.assertEqual((code, output), (0, ""))

        with mock.patch.object(
            hook_client.urllib.request,
            "urlopen",
            return_value=FakeResponse(b"[]"),
        ):
            code, output = self.run_client("{}")
        self.assertEqual((code, output), (0, ""))

    def test_non_success_http_status_is_fail_soft(self) -> None:
        with mock.patch.object(
            hook_client.urllib.request,
            "urlopen",
            return_value=FakeResponse(b"{}", status=503),
        ):
            code, output = self.run_client("{}")
        self.assertEqual((code, output), (0, ""))

    def test_request_timeout_is_total(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocked_urlopen(*args: object, **kwargs: object) -> FakeResponse:
            started.set()
            release.wait(1.0)
            return FakeResponse(b"{}")

        with mock.patch.object(
            hook_client.urllib.request,
            "urlopen",
            side_effect=blocked_urlopen,
        ):
            self.assertIsNone(hook_client._post(b"{}", timeout=0.05))
        self.assertTrue(started.is_set())
        release.set()


if __name__ == "__main__":
    unittest.main()
