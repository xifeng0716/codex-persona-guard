"""The small, explicit Guard State state machine."""

from dataclasses import dataclass
from typing import Optional


STATES = frozenset({"NORMAL", "ARMED", "HOT"})
DECISIONS = frozenset({"HIT", "WATCH", "NONE"})


@dataclass(frozen=True)
class GuardSnapshot:
    state: str = "NORMAL"
    hot_remaining: int = 0
    clean_none_streak: int = 0
    previous_watch_type: Optional[str] = None
    recent_hit_type: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("invalid guard state")
        if self.hot_remaining < 0 or self.clean_none_streak < 0:
            raise ValueError("state counters cannot be negative")
        if self.state != "HOT" and self.hot_remaining != 0:
            raise ValueError("only HOT may have hot_remaining")
        if self.state != "HOT" and self.clean_none_streak != 0:
            raise ValueError("only HOT may have clean_none_streak")

    @classmethod
    def normal(cls) -> "GuardSnapshot":
        return cls()

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "hot_remaining": self.hot_remaining,
            "clean_none_streak": self.clean_none_streak,
            "previous_watch_type": self.previous_watch_type,
            "recent_hit_type": self.recent_hit_type,
        }

    def __getitem__(self, key: str):
        return self.as_dict()[key]


@dataclass(frozen=True)
class Transition:
    before: GuardSnapshot
    after: GuardSnapshot
    inject: bool = False

    @property
    def injected(self) -> bool:
        return self.inject


def _hot(result_type: Optional[str]) -> GuardSnapshot:
    return GuardSnapshot(
        state="HOT",
        hot_remaining=4,
        clean_none_streak=0,
        recent_hit_type=result_type,
    )


def transition(
    before: GuardSnapshot,
    result: Optional[str],
    result_type: Optional[str] = None,
) -> Transition:
    """Apply one validated detector result.

    ``result=None`` is the explicit fail-soft path: it does not represent a
    detector decision and therefore leaves the state byte-for-byte unchanged.
    """

    if not isinstance(before, GuardSnapshot):
        raise TypeError("before must be a GuardSnapshot")
    if result is None:
        return Transition(before=before, after=before, inject=False)
    if result not in DECISIONS:
        raise ValueError("invalid detector result")

    if result == "HIT":
        return Transition(before=before, after=_hot(result_type), inject=True)

    if before.state == "NORMAL":
        if result == "WATCH":
            after = GuardSnapshot(state="ARMED", previous_watch_type=result_type)
        else:
            after = GuardSnapshot.normal()
    elif before.state == "ARMED":
        if result == "WATCH":
            after = GuardSnapshot(state="ARMED", previous_watch_type=result_type)
        else:
            after = GuardSnapshot.normal()
    else:
        remaining = before.hot_remaining - 1
        if result == "WATCH":
            if remaining <= 0:
                after = GuardSnapshot(state="ARMED", previous_watch_type=result_type)
            else:
                after = GuardSnapshot(
                    state="HOT",
                    hot_remaining=remaining,
                    clean_none_streak=0,
                    recent_hit_type=before.recent_hit_type,
                )
        else:
            clean_streak = before.clean_none_streak + 1
            if clean_streak >= 2 or remaining <= 0:
                after = GuardSnapshot.normal()
            else:
                after = GuardSnapshot(
                    state="HOT",
                    hot_remaining=remaining,
                    clean_none_streak=clean_streak,
                    recent_hit_type=before.recent_hit_type,
                )
    return Transition(before=before, after=after, inject=False)


def apply_result(
    before: GuardSnapshot,
    result: Optional[str],
    result_type: Optional[str] = None,
) -> GuardSnapshot:
    """Convenience wrapper returning only the next snapshot."""

    return transition(before, result, result_type).after


def no_transition(before: GuardSnapshot) -> Transition:
    return transition(before, None)
