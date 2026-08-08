import unittest

from persona_guard.state_machine import GuardSnapshot, apply_result, transition


class StateMachineTests(unittest.TestCase):
    def test_normal_watch_hit_and_clear(self):
        normal = GuardSnapshot.normal()
        armed = transition(normal, "WATCH", "uncertainty")
        self.assertEqual(armed.after.state, "ARMED")
        self.assertEqual(armed.after.previous_watch_type, "uncertainty")
        self.assertFalse(armed.inject)

        clean = transition(armed.after, "NONE", "other")
        self.assertEqual(clean.after, normal)

        hit = transition(normal, "HIT", "emotion")
        self.assertTrue(hit.inject)
        self.assertEqual(hit.after.state, "HOT")
        self.assertEqual(hit.after.hot_remaining, 4)
        self.assertEqual(hit.after.recent_hit_type, "emotion")

    def test_armed_watch_updates_type_and_hit_resets_hot(self):
        state = apply_result(GuardSnapshot.normal(), "WATCH", "other")
        state = apply_result(state, "WATCH", "uncertainty")
        self.assertEqual(state.previous_watch_type, "uncertainty")
        hit = transition(state, "HIT", "feedback")
        self.assertTrue(hit.inject)
        self.assertEqual(hit.after.hot_remaining, 4)
        self.assertEqual(hit.after.clean_none_streak, 0)

    def test_hot_watch_expires_to_armed(self):
        state = GuardSnapshot(
            state="HOT", hot_remaining=1, clean_none_streak=0, recent_hit_type="emotion"
        )
        result = transition(state, "WATCH", "uncertainty")
        self.assertFalse(result.inject)
        self.assertEqual(result.after.state, "ARMED")
        self.assertEqual(result.after.previous_watch_type, "uncertainty")

    def test_hot_two_none_or_four_turns_clears(self):
        state = GuardSnapshot(
            state="HOT", hot_remaining=4, clean_none_streak=0, recent_hit_type="sharing"
        )
        state = apply_result(state, "NONE", "other")
        self.assertEqual((state.state, state.hot_remaining, state.clean_none_streak), ("HOT", 3, 1))
        state = apply_result(state, "NONE", "other")
        self.assertEqual(state, GuardSnapshot.normal())

        state = GuardSnapshot(
            state="HOT", hot_remaining=4, clean_none_streak=0, recent_hit_type="sharing"
        )
        state = apply_result(state, "NONE", "other")
        state = apply_result(state, "WATCH", "uncertainty")
        self.assertEqual((state.state, state.hot_remaining, state.clean_none_streak), ("HOT", 2, 0))
        state = apply_result(state, "NONE", "other")
        state = apply_result(state, "WATCH", "uncertainty")
        self.assertEqual(state.state, "ARMED")

    def test_detector_failure_has_no_transition(self):
        before = GuardSnapshot(
            state="HOT", hot_remaining=2, clean_none_streak=1, recent_hit_type="feedback"
        )
        result = transition(before, None)
        self.assertEqual(result.before, before)
        self.assertEqual(result.after, before)
        self.assertFalse(result.inject)


if __name__ == "__main__":
    unittest.main()
