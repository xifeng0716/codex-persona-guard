"""Local HTTP API, hook handler, and static file server."""

import json
import mimetypes
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .defaults import DEEPSEEK_MODEL, HOOK_PORT
from .detector import Detector, DetectorError, has_api_key
from .state_machine import GuardSnapshot, transition
from .storage import Binding, Storage, normalize_cwd
from .transcript import TranscriptError, read_history


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class PersonaGuardApp:
    """Application services shared by threaded HTTP request handlers."""

    def __init__(
        self,
        storage: Optional[Storage] = None,
        detector: Optional[Detector] = None,
        transcript_reader: Callable[..., list[dict]] = read_history,
        static_dir: Optional[os.PathLike | str] = None,
    ):
        self.storage = Storage() if storage is None else storage
        self.detector = Detector() if detector is None else detector
        self.transcript_reader = transcript_reader
        self.static_dir = (
            Path(static_dir)
            if static_dir is not None
            else Path(__file__).resolve().parent / "static"
        )
        self._session_locks: dict[str, threading.RLock] = {}
        self._session_locks_lock = threading.Lock()

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._session_locks_lock:
            return self._session_locks.setdefault(session_id, threading.RLock())

    @property
    def model(self) -> str:
        return getattr(self.detector, "model", DEEPSEEK_MODEL)

    def status(self) -> dict:
        policy = self.storage.get_policy()
        return {
            "healthy": True,
            "enabled": self.storage.is_enabled(),
            "key_present": has_api_key(getattr(self.detector, "environ", None)),
            "model": self.model,
            "policy_revision": policy["revision"],
            "binding_count": self.storage.count_bindings(),
            "record_count": self.storage.count_records(),
        }

    def discoveries(self) -> dict:
        return {
            "threads": self.storage.list_discoveries(),
            "workspaces": self.storage.list_workspaces(),
        }

    def bindings(self) -> list[dict]:
        return [binding.as_dict() for binding in self.storage.list_bindings()]

    def policy(self) -> dict:
        return self.storage.get_policy()

    def records(
        self,
        *,
        binding_id: Optional[int] = None,
        result: Optional[str] = None,
        limit: int = 50,
        before_id: Optional[int] = None,
    ) -> list[dict]:
        return self.storage.list_records(
            binding_id=binding_id,
            result=result,
            limit=limit,
            before_id=before_id,
        )

    @staticmethod
    def _prompt_from_payload(payload: dict) -> Optional[str]:
        prompt = payload.get("prompt")
        if prompt is None:
            prompt = payload.get("user_prompt")
        return prompt if isinstance(prompt, str) else None

    @staticmethod
    def _session_and_cwd(payload: dict) -> tuple[Optional[str], Optional[str]]:
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if not isinstance(session_id, str) or not session_id:
            return None, None
        if not isinstance(cwd, str) or not cwd:
            return None, None
        try:
            return session_id, normalize_cwd(cwd)
        except ValueError:
            return None, None

    def _record_failure(
        self,
        *,
        session_id: str,
        history: list[dict],
        current_prompt: str,
        policy: dict,
        state: GuardSnapshot,
        binding: Binding,
        category: str,
        latency_ms: float = 0.0,
    ) -> None:
        self.storage.record_attempt(
            session_id=session_id,
            history=history,
            current_prompt=current_prompt,
            policy_text=policy["text"],
            policy_revision=policy["revision"],
            result="ERROR",
            decision_type=None,
            error_category=category,
            state_before=state,
            state_after=state,
            binding=binding,
            injected=False,
            model=self.model,
            latency_ms=latency_ms,
        )

    def handle_hook(self, payload: Any) -> dict:
        """Process a hook payload; every failure intentionally returns ``{}``."""

        if not isinstance(payload, dict):
            return {}
        session_id, cwd = self._session_and_cwd(payload)
        if session_id is None or cwd is None:
            return {}
        with self._session_lock(session_id):
            try:
                # Discovery is deliberately the first durable action. It is
                # metadata only and never contains the submitted prompt.
                self.storage.upsert_discovery(session_id, cwd)
                binding = self.storage.find_binding(session_id, cwd)
                if not self.storage.is_enabled() or binding is None or not binding.enabled:
                    return {}

                current_prompt = self._prompt_from_payload(payload)
                policy = self.storage.get_policy()
                state_before = self.storage.get_state(session_id)
                if current_prompt is None:
                    self._record_failure(
                        session_id=session_id,
                        history=[],
                        current_prompt="",
                        policy=policy,
                        state=state_before,
                        binding=binding,
                        category="invalid_hook",
                    )
                    return {}

                transcript_path = payload.get("transcript_path")
                try:
                    history = list(self.transcript_reader(transcript_path, current_prompt, 6))
                except TranscriptError as exc:
                    self._record_failure(
                        session_id=session_id,
                        history=[],
                        current_prompt=current_prompt,
                        policy=policy,
                        state=state_before,
                        binding=binding,
                        category=exc.category,
                    )
                    return {}
                except Exception:
                    self._record_failure(
                        session_id=session_id,
                        history=[],
                        current_prompt=current_prompt,
                        policy=policy,
                        state=state_before,
                        binding=binding,
                        category="transcript_unparseable",
                    )
                    return {}

                started = time.monotonic()
                try:
                    decision = self.detector.classify(
                        history,
                        current_prompt,
                        state_before,
                        policy["text"],
                    )
                except DetectorError as exc:
                    self._record_failure(
                        session_id=session_id,
                        history=history,
                        current_prompt=current_prompt,
                        policy=policy,
                        state=state_before,
                        binding=binding,
                        category=exc.category,
                        latency_ms=(time.monotonic() - started) * 1000.0,
                    )
                    return {}
                except Exception:
                    self._record_failure(
                        session_id=session_id,
                        history=history,
                        current_prompt=current_prompt,
                        policy=policy,
                        state=state_before,
                        binding=binding,
                        category="detector_error",
                        latency_ms=(time.monotonic() - started) * 1000.0,
                    )
                    return {}

                result = transition(state_before, decision.result, decision.type)
                self.storage.record_attempt(
                    session_id=session_id,
                    history=history,
                    current_prompt=current_prompt,
                    policy_text=policy["text"],
                    policy_revision=policy["revision"],
                    result=decision.result,
                    decision_type=decision.type,
                    error_category=None,
                    state_before=result.before,
                    state_after=result.after,
                    binding=binding,
                    injected=result.inject,
                    model=self.model,
                    latency_ms=(time.monotonic() - started) * 1000.0,
                )
                # Persist the calibration record before changing Guard State.
                # If recording fails, the outer fail-soft boundary returns no
                # injection and the existing state remains untouched.
                self.storage.save_state(session_id, result.after)
                if result.inject:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": binding.reminder,
                        }
                    }
                return {}
            except Exception:
                # The hook protocol is intentionally fail-soft. No exception,
                # database detail, or secret can reach Codex as hook output.
                return {}

    def create_binding(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise APIError(400, "invalid_json", "request body must be a JSON object")
        required = ("name", "target_type", "target_value", "reminder")
        missing = [field for field in required if field not in data]
        if missing:
            raise APIError(400, "missing_field", "missing binding field")
        enabled = data.get("enabled", True)
        try:
            binding = self.storage.create_binding(
                data["name"],
                data["target_type"],
                data["target_value"],
                enabled,
                data["reminder"],
            )
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "duplicate_binding", "that target already has a binding") from exc
        except (TypeError, ValueError) as exc:
            raise APIError(400, "invalid_binding", str(exc)) from exc
        return binding.as_dict()

    def update_binding(self, binding_id: int, data: dict) -> dict:
        if not isinstance(data, dict):
            raise APIError(400, "invalid_json", "request body must be a JSON object")
        try:
            binding = self.storage.update_binding(binding_id, **data)
        except KeyError as exc:
            raise APIError(404, "not_found", "binding not found") from exc
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "duplicate_binding", "that target already has a binding") from exc
        except (TypeError, ValueError) as exc:
            raise APIError(400, "invalid_binding", str(exc)) from exc
        return binding.as_dict()


class PersonaGuardServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler_class, app: PersonaGuardApp):
        self.app = app
        super().__init__(address, handler_class)


class PersonaGuardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> PersonaGuardApp:
        return self.server.app  # type: ignore[attr-defined]

    def _body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(400, "invalid_length", "invalid request body length") from exc
        if length < 0 or length > 2 * 1024 * 1024:
            raise APIError(413, "body_too_large", "request body is too large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise APIError(400, "invalid_json", "request body is not valid JSON") from exc
        return value

    def _send_json(self, value: Any, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_api_error(self, error: APIError) -> None:
        self._send_json({"error": {"code": error.code, "message": error.message}}, error.status)

    def _api_call(self, callback: Callable[[], Any]) -> None:
        try:
            self._send_json(callback())
        except APIError as exc:
            self._send_api_error(exc)
        except KeyError:
            self._send_api_error(APIError(404, "not_found", "resource not found"))
        except (TypeError, ValueError) as exc:
            self._send_api_error(APIError(400, "invalid_request", str(exc)))
        except Exception:
            self._send_api_error(APIError(500, "server_error", "internal server error"))

    def _path(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(self.path)
        return parsed.path, parse_qs(parsed.query, keep_blank_values=True)

    @staticmethod
    def _one(query: dict[str, list[str]], key: str) -> Optional[str]:
        values = query.get(key)
        if not values or values[0] == "":
            return None
        return values[0]

    @staticmethod
    def _integer(value: Optional[str], field: str) -> Optional[int]:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise APIError(400, "invalid_query", f"{field} must be an integer") from exc
        if parsed < 1:
            raise APIError(400, "invalid_query", f"{field} must be positive")
        return parsed

    def do_GET(self) -> None:
        path, query = self._path()
        if path == "/api/status":
            return self._api_call(self.app.status)
        if path == "/api/discoveries":
            return self._api_call(self.app.discoveries)
        if path == "/api/bindings":
            return self._api_call(lambda: {"bindings": self.app.bindings()})
        if path == "/api/policy":
            return self._api_call(self.app.policy)
        if path == "/api/records":
            def get_records() -> dict:
                binding_id = self._integer(self._one(query, "binding_id"), "binding_id")
                before_id = self._integer(self._one(query, "before_id"), "before_id")
                result = self._one(query, "result")
                if result is not None and result not in {"HIT", "WATCH", "NONE", "ERROR"}:
                    raise APIError(400, "invalid_query", "invalid result")
                limit = self._integer(self._one(query, "limit"), "limit") or 50
                return {"records": self.app.records(
                    binding_id=binding_id,
                    result=result,
                    limit=limit,
                    before_id=before_id,
                )}
            return self._api_call(get_records)
        if path.startswith("/api/"):
            return self._send_api_error(APIError(404, "not_found", "endpoint not found"))
        self._serve_static(path)

    def do_POST(self) -> None:
        path, _ = self._path()
        if path == "/api/hook":
            try:
                payload = self._body()
            except Exception:
                payload = None
            # This endpoint is a hook transport boundary: malformed input,
            # detector failures, and all server failures are HTTP 200 {}.
            try:
                value = self.app.handle_hook(payload)
            except Exception:
                value = {}
            return self._send_json(value if isinstance(value, dict) else {})
        if path == "/api/bindings":
            return self._api_call(lambda: {"binding": self.app.create_binding(self._body())})
        if path.startswith("/api/"):
            return self._send_api_error(APIError(404, "not_found", "endpoint not found"))
        self._send_api_error(APIError(405, "method_not_allowed", "method not allowed"))

    def do_PUT(self) -> None:
        path, _ = self._path()
        if path == "/api/status":
            def update_status() -> dict:
                data = self._body()
                if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
                    raise APIError(400, "invalid_status", "enabled must be a boolean")
                self.app.storage.set_enabled(data["enabled"])
                return self.app.status()
            return self._api_call(update_status)
        if path == "/api/policy":
            def update_policy() -> dict:
                data = self._body()
                if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                    raise APIError(400, "invalid_policy", "text must be a string")
                try:
                    return self.app.storage.update_policy(data["text"])
                except ValueError as exc:
                    raise APIError(400, "invalid_policy", str(exc)) from exc
            return self._api_call(update_policy)
        if path.startswith("/api/bindings/"):
            binding_id = self._resource_id(path, "binding")
            return self._api_call(lambda: {"binding": self.app.update_binding(binding_id, self._body())})
        if path.startswith("/api/"):
            return self._send_api_error(APIError(404, "not_found", "endpoint not found"))
        self._send_api_error(APIError(405, "method_not_allowed", "method not allowed"))

    def do_DELETE(self) -> None:
        path, query = self._path()
        if path == "/api/records":
            def clear_records() -> dict:
                binding_id = self._integer(self._one(query, "binding_id"), "binding_id")
                self.app.storage.clear_records(binding_id)
                return {}
            return self._api_call(clear_records)
        if path.startswith("/api/bindings/"):
            binding_id = self._resource_id(path, "binding")
            def delete_binding() -> dict:
                try:
                    self.app.storage.delete_binding(binding_id)
                except KeyError as exc:
                    raise APIError(404, "not_found", "binding not found") from exc
                return {}
            return self._api_call(delete_binding)
        if path.startswith("/api/"):
            return self._send_api_error(APIError(404, "not_found", "endpoint not found"))
        self._send_api_error(APIError(405, "method_not_allowed", "method not allowed"))

    @staticmethod
    def _resource_id(path: str, resource: str) -> int:
        value = path.rsplit("/", 1)[-1]
        try:
            parsed = int(value)
        except ValueError as exc:
            raise APIError(400, "invalid_id", f"invalid {resource} id") from exc
        if parsed < 1:
            raise APIError(400, "invalid_id", f"invalid {resource} id")
        return parsed

    def _serve_static(self, path: str) -> None:
        relative = unquote(path).lstrip("/")
        if not relative:
            relative = "index.html"
        root = self.app.static_dir.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return self._send_api_error(APIError(404, "not_found", "file not found"))
        if not candidate.is_file():
            return self._send_api_error(APIError(404, "not_found", "file not found"))
        try:
            body = candidate.read_bytes()
        except OSError:
            return self._send_api_error(APIError(404, "not_found", "file not found"))
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Hook payloads and paths can contain conversation content. Keep the
        # local service quiet instead of writing them to stderr.
        return None


def create_server(
    *,
    storage: Optional[Storage] = None,
    detector: Optional[Detector] = None,
    host: str = "127.0.0.1",
    port: int = HOOK_PORT,
    transcript_reader: Callable[..., list[dict]] = read_history,
    static_dir: Optional[os.PathLike | str] = None,
) -> PersonaGuardServer:
    app = PersonaGuardApp(
        storage=storage,
        detector=detector,
        transcript_reader=transcript_reader,
        static_dir=static_dir,
    )
    return PersonaGuardServer((host, int(port)), PersonaGuardHandler, app)


def run_server(
    *,
    storage: Optional[Storage] = None,
    detector: Optional[Detector] = None,
    host: str = "127.0.0.1",
    port: int = HOOK_PORT,
) -> None:
    server = create_server(storage=storage, detector=detector, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        pass
