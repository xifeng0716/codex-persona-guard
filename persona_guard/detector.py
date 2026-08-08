"""DeepSeek detector transport and strict result parsing."""

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .defaults import (
    DEEPSEEK_ENDPOINT,
    DEEPSEEK_MODEL,
    DETECTOR_TIMEOUT_SECONDS,
    RESULTS,
    RISK_TYPES,
)
from .state_machine import GuardSnapshot


class DetectorError(Exception):
    """A detector attempt failed and must not change Guard State."""

    def __init__(self, category: str, message: str = "detector failure"):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class DetectorDecision:
    result: str
    type: str

    def as_dict(self) -> dict:
        return {"result": self.result, "type": self.type}


def get_api_key(environ: Optional[dict] = None) -> Optional[str]:
    variables = os.environ if environ is None else environ
    primary = variables.get("GMEM_DEEPSEEK_API_KEY")
    if primary:
        return primary
    portable = variables.get("DEEPSEEK_API_KEY")
    return portable if portable else None


def has_api_key(environ: Optional[dict] = None) -> bool:
    return get_api_key(environ) is not None


def build_detector_input(
    history: Iterable[dict],
    current_prompt: str,
    guard_state: GuardSnapshot | dict | str,
) -> str:
    if isinstance(guard_state, GuardSnapshot):
        state = guard_state.as_dict()
    elif isinstance(guard_state, dict):
        state = guard_state
    elif isinstance(guard_state, str):
        state = {"state": guard_state}
    else:
        raise TypeError("guard_state must be a GuardSnapshot, dict, or string")

    state_name = state.get("state")
    if state_name not in {"NORMAL", "ARMED", "HOT"}:
        raise ValueError("invalid guard state")
    lines = ["【门卫状态】", f"guard_state: {state_name}"]
    if state_name == "ARMED":
        lines.append(f"previous_watch_type: {state.get('previous_watch_type') or 'other'}")
    elif state_name == "HOT":
        lines.append(f"recent_hit_type: {state.get('recent_hit_type') or 'other'}")

    lines.extend(["", "【最近对话】", ""])
    for message in history:
        if not isinstance(message, dict):
            raise ValueError("history messages must be objects")
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ValueError("history messages must have user/assistant string content")
        lines.append(f"{role.upper()}:")
        lines.append(content)
        lines.append("")
    lines.extend(["【用户最新一句】", "", current_prompt])
    return "\n".join(lines)


def _response_content(response_json: Any) -> str:
    if not isinstance(response_json, dict):
        raise DetectorError("invalid_response", "detector response is not an object")
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise DetectorError("invalid_response", "detector response has no choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise DetectorError("invalid_response", "detector response has no JSON content")
    content = message["content"].strip()
    if not content:
        raise DetectorError("invalid_response", "detector response content is empty")
    return content


def parse_decision(value: Any) -> DetectorDecision:
    if not isinstance(value, dict):
        raise DetectorError("invalid_response", "detector decision is not an object")
    result = value.get("result")
    decision_type = value.get("type")
    if not isinstance(result, str) or result not in RESULTS:
        raise DetectorError("invalid_response", "invalid detector result enum")
    if not isinstance(decision_type, str) or decision_type not in RISK_TYPES:
        raise DetectorError("invalid_response", "invalid detector type enum")
    return DetectorDecision(result=result, type=decision_type)


class Detector:
    def __init__(
        self,
        *,
        endpoint: str = DEEPSEEK_ENDPOINT,
        model: str = DEEPSEEK_MODEL,
        timeout: float = DETECTOR_TIMEOUT_SECONDS,
        environ: Optional[dict] = None,
        opener: Optional[Callable[..., Any]] = None,
    ):
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.environ = os.environ if environ is None else environ
        self.opener = urllib.request.urlopen if opener is None else opener

    @property
    def api_key_present(self) -> bool:
        return has_api_key(self.environ)

    def classify(
        self,
        history: Iterable[dict],
        current_prompt: str,
        guard_state: GuardSnapshot | dict | str,
        policy_text: str,
    ) -> DetectorDecision:
        if not isinstance(current_prompt, str):
            raise DetectorError("invalid_input", "current prompt must be a string")
        if not isinstance(policy_text, str) or not policy_text:
            raise DetectorError("invalid_input", "policy text must be a string")
        api_key = get_api_key(self.environ)
        if api_key is None:
            raise DetectorError("missing_api_key", "DeepSeek API key is not configured")

        detector_input = build_detector_input(history, current_prompt, guard_state)
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": policy_text},
                {"role": "user", "content": detector_input},
            ],
            "temperature": 0,
            "max_tokens": 100,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self.opener(request, timeout=self.timeout)
            try:
                raw = response.read()
                status = getattr(response, "status", None)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            if isinstance(status, int) and status >= 400:
                category = "rate_limit" if status == 429 else "server_error" if status >= 500 else "api_error"
                raise DetectorError(category, "DeepSeek returned an HTTP error")
        except DetectorError:
            raise
        except urllib.error.HTTPError as exc:
            category = "rate_limit" if exc.code == 429 else "server_error" if exc.code >= 500 else "api_error"
            raise DetectorError(category, "DeepSeek returned an HTTP error") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise DetectorError("timeout", "DeepSeek detector timed out") from exc
        except urllib.error.URLError as exc:
            raise DetectorError("network_error", "DeepSeek detector network failure") from exc
        except (OSError, UnicodeError) as exc:
            raise DetectorError("network_error", "DeepSeek detector network failure") from exc
        except Exception as exc:
            # The hook must remain fail-soft even when a platform transport
            # implementation raises a non-urllib exception.
            raise DetectorError("network_error", "DeepSeek detector failure") from exc

        try:
            response_json = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            content = _response_content(response_json)
            decision_json = json.loads(content)
        except DetectorError:
            raise
        except (TypeError, ValueError, UnicodeError) as exc:
            raise DetectorError("invalid_response", "DeepSeek returned invalid JSON") from exc
        return parse_decision(decision_json)
