"""Small Codex JSONL transcript reader used by the detector."""

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional


class TranscriptError(Exception):
    """A transcript could not be read or parsed safely."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class TranscriptMessage:
    role: str
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    def __getitem__(self, key: str):
        return self.as_dict()[key]


_ROLES = {"user", "assistant"}
_INCOMPLETE_STATUSES = {"in_progress", "incomplete", "streaming", "started", "pending"}


def _text_content(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), (str, list, dict)):
            return _text_content(value["content"])
        if isinstance(value.get("message"), (str, list, dict)):
            return _text_content(value["message"])
        return None
    if isinstance(value, list):
        pieces = []
        for item in value:
            text = _text_content(item)
            if text is not None:
                pieces.append(text)
        return "\n".join(pieces) if pieces else None
    return None


def _status_is_incomplete(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in _INCOMPLETE_STATUSES
    return False


def _role_for_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.lower()
    if normalized in {"user", "user_message", "user_prompt", "prompt"}:
        return "user"
    if normalized in {
        "assistant",
        "assistant_message",
        "assistant_response",
        "agent_message",
        "response",
    }:
        return "assistant"
    return None


def _extract_candidates(value: Any) -> list[TranscriptMessage]:
    if not isinstance(value, dict):
        return []
    if value.get("completed") is False or _status_is_incomplete(value.get("status")):
        return []

    candidates: list[TranscriptMessage] = []
    role = value.get("role") if value.get("role") in _ROLES else _role_for_type(value.get("type"))
    content_value = value.get("content")
    if content_value is None and "message" in value:
        content_value = value.get("message")
    if role is not None and content_value is not None:
        content = _text_content(content_value)
        if content is not None:
            candidates.append(TranscriptMessage(role, content))

    for key in ("message", "payload", "item", "data"):
        nested = value.get(key)
        if isinstance(nested, dict):
            candidates.extend(_extract_candidates(nested))
    return candidates


def _json_objects_reverse(path: str, block_size: int = 64 * 1024) -> Iterable[dict]:
    """Yield JSONL objects newest first without loading the whole file."""

    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            position = stream.tell()
            remainder = b""
            while position > 0:
                size = min(block_size, position)
                position -= size
                stream.seek(position)
                parts = (stream.read(size) + remainder).split(b"\n")
                remainder = parts[0]
                for line in reversed(parts[1:]):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise TranscriptError(
                            "transcript_unparseable",
                            "transcript lines must be JSON objects",
                        )
                    yield value
            if remainder.strip():
                value = json.loads(remainder)
                if not isinstance(value, dict):
                    raise TranscriptError(
                        "transcript_unparseable",
                        "transcript lines must be JSON objects",
                    )
                yield value
    except TranscriptError:
        raise
    except (TypeError, ValueError) as exc:
        raise TranscriptError("transcript_unparseable", "invalid transcript JSONL") from exc
    except (OSError, UnicodeError) as exc:
        raise TranscriptError("transcript_unreadable", "unable to read transcript") from exc


def read_history(
    path: str,
    current_prompt: Optional[str],
    max_messages: int = 6,
) -> list[dict]:
    """Read at most three historical messages of each supported role.

    The newest exact user message matching ``current_prompt`` is removed once,
    which handles the normal hook/transcript duplication without deleting a
    legitimate earlier repetition of the same words.
    """

    if not isinstance(path, str) or not path:
        raise TranscriptError("transcript_unreadable", "transcript path is missing")
    if max_messages < 1:
        return []
    max_messages = min(int(max_messages), 6)

    per_role = 1 if max_messages == 1 else min(3, max_messages // 2)
    target_count = 1 if max_messages == 1 else per_role * 2
    counts = {"user": 0, "assistant": 0}
    selected: list[TranscriptMessage] = []
    previous: Optional[TranscriptMessage] = None
    current_removed = False

    for obj in _json_objects_reverse(path):
        for candidate in reversed(_extract_candidates(obj)):
            # Traversal is newest-first, so adjacent duplicate Codex records
            # are still collapsed before selecting the recent window.
            if candidate == previous:
                continue
            previous = candidate
            if (
                not current_removed
                and isinstance(current_prompt, str)
                and candidate.role == "user"
                and candidate.content == current_prompt
            ):
                current_removed = True
                continue
            if max_messages == 1:
                return [candidate.as_dict()]
            if counts[candidate.role] >= per_role:
                continue
            counts[candidate.role] += 1
            selected.append(candidate)
            if len(selected) == target_count:
                return [item.as_dict() for item in reversed(selected)]

    return [item.as_dict() for item in reversed(selected)]
