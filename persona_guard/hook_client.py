#!/usr/bin/env python3
"""Fail-soft Codex UserPromptSubmit client for Persona Guard.

This file is intentionally standalone.  The installer copies it into the
user's Codex directory, where it must continue to work without the project
source tree or third-party packages.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import BinaryIO, TextIO


HOOK_URL = "http://127.0.0.1:43821/api/hook"
TOTAL_TIMEOUT_SECONDS = 7.0


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _read_json_object(stream: BinaryIO | TextIO) -> bytes:
    """Read and validate exactly one JSON object, returning its original bytes."""

    source = getattr(stream, "buffer", stream)
    raw = source.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)) or not raw.strip():
        raise ValueError("hook input is empty")

    raw_bytes = bytes(raw)
    value = json.loads(
        raw_bytes.decode("utf-8"),
        parse_constant=_reject_non_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return raw_bytes


def _post(payload: bytes, timeout: float = TOTAL_TIMEOUT_SECONDS) -> bytes | None:
    """POST *payload* with a wall-clock timeout covering the whole request.

    ``urllib`` applies its timeout to individual socket operations.  Running
    the request in a daemon thread and joining it against one monotonic
    deadline adds the required total timeout as well.  A stuck daemon thread
    cannot keep the hook process alive after this function returns.
    """

    if timeout <= 0:
        return None

    request = urllib.request.Request(
        HOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    deadline = time.monotonic() + timeout
    result: list[bytes | Exception] = []

    def worker() -> None:
        response = None
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
            status_value = getattr(response, "status", None)
            status = status_value if isinstance(status_value, int) else None
            if status is None:
                getcode = getattr(response, "getcode", None)
                code_value = getcode() if callable(getcode) else None
                status = code_value if isinstance(code_value, int) else None
            if status is not None and not 200 <= int(status) < 300:
                raise urllib.error.HTTPError(
                    HOOK_URL,
                    int(status),
                    "local service returned a non-success status",
                    hdrs=None,
                    fp=None,
                )
            body = response.read()
            if isinstance(body, str):
                body = body.encode("utf-8")
            if not isinstance(body, (bytes, bytearray)):
                raise ValueError("local service response is not bytes")
            result.append(bytes(body))
        except Exception as error:  # noqa: BLE001 - hook failures are fail-soft.
            result.append(error)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    thread = threading.Thread(target=worker, name="persona-guard-hook", daemon=True)
    thread.start()
    thread.join(max(0.0, deadline - time.monotonic()))
    if thread.is_alive() or not result:
        return None
    if isinstance(result[0], Exception):
        return None
    return result[0]


def _valid_service_json(body: bytes) -> str | None:
    """Return the response text only when it is a valid hook JSON object."""

    try:
        text = body.decode("utf-8")
        value = json.loads(text, parse_constant=_reject_non_json_constant)
    except (UnicodeDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return text


def main(stdin: BinaryIO | TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the hook and always return zero so Codex can continue."""

    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout

    try:
        payload = _read_json_object(stdin)
        response = _post(payload)
        if response is None:
            return 0
        response_text = _valid_service_json(response)
        if response_text is None:
            return 0
        stdout.write(response_text)
        stdout.flush()
    except Exception:  # noqa: BLE001 - every client-side failure is fail-soft.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
