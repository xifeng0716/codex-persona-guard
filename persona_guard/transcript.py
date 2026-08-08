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


def _json_objects(path: str) -> Iterable[dict]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines = stream.readlines()
    except (OSError, UnicodeError) as exc:
        raise TranscriptError("transcript_unreadable", "unable to read transcript") from exc

    nonempty = [line for line in lines if line.strip()]
    if not nonempty:
        return []

    # Codex writes JSONL. Accepting a single JSON array costs little and keeps
    # the reader harmlessly useful for exported local transcripts in tests.
    if len(nonempty) == 1 and nonempty[0].lstrip().startswith("["):
        try:
            parsed = json.loads(nonempty[0])
        except (TypeError, ValueError) as exc:
            raise TranscriptError("transcript_unparseable", "invalid transcript JSON") from exc
        if not isinstance(parsed, list):
            raise TranscriptError("transcript_unparseable", "transcript JSON must be an object list")
        return [item for item in parsed if isinstance(item, dict)]

    objects = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise TranscriptError("transcript_unparseable", "invalid transcript JSONL") from exc
        if not isinstance(value, dict):
            raise TranscriptError("transcript_unparseable", "transcript lines must be JSON objects")
        objects.append(value)
    return objects


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

    candidates: list[TranscriptMessage] = []
    for obj in _json_objects(path):
        for candidate in _extract_candidates(obj):
            # Codex can emit the same completed message in adjacent JSONL
            # records. Keep repeated wording when it is a real later turn.
            if candidates and candidates[-1] == candidate:
                continue
            candidates.append(candidate)

    if isinstance(current_prompt, str):
        for index in range(len(candidates) - 1, -1, -1):
            candidate = candidates[index]
            if candidate.role == "user" and candidate.content == current_prompt:
                del candidates[index]
                break

    if max_messages == 1:
        return [item.as_dict() for item in candidates[-1:]]

    per_role = min(3, max_messages // 2)
    selected_indices: set[int] = set()
    for role in ("user", "assistant"):
        role_indices = [index for index, item in enumerate(candidates) if item.role == role]
        selected_indices.update(role_indices[-per_role:])
    selected = [candidates[index] for index in sorted(selected_indices)]
    return [item.as_dict() for item in selected[-max_messages:]]
