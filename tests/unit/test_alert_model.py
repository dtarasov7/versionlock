"""Executable specification for scan observations; not exporter implementation."""
import unittest


def persistent_flags(observations, interval=300, deadline=60):
    streak = 0
    started_at = 0
    previous_at = None
    flags = []
    for completed_at, state in observations:
        contiguous = (previous_at is not None and
                      0 < completed_at - previous_at <= interval + deadline + 2)
        if state != "unheld":
            streak = 0
            started_at = 0
        elif not contiguous or streak == 0:
            streak = 1
            started_at = completed_at
        else:
            streak += 1
        persistent = streak >= 3 and completed_at - started_at > interval
        flags.append(persistent)
        previous_at = completed_at
    return flags


class AlertModelTests(unittest.TestCase):
    def test_one_observation_does_not_become_persistent_by_polling(self):
        flag = persistent_flags([(10, "unheld")])[0]
        self.assertFalse(any([flag] * 1000))

    def test_transient_and_persistent(self):
        self.assertEqual(persistent_flags([(10, "unheld"), (310, "held")]),
                         [False, False])
        self.assertEqual(persistent_flags([(10, "unheld"), (310, "unheld"),
                                           (610, "unheld")]), [False, False, True])

    def test_unknown_and_gap_break_streak(self):
        self.assertFalse(any(persistent_flags([(10, "unheld"), (310, "unknown"),
                                                (610, "unheld")])))
        self.assertFalse(any(persistent_flags([(10, "unheld"), (310, "unheld"),
                                                (1000, "unheld")])))

    def test_configurable_interval_and_strict_span(self):
        self.assertEqual(persistent_flags([(10, "unheld"), (70, "unheld"),
                                           (130, "unheld")], 60, 10),
                         [False, False, True])
        self.assertFalse(any(persistent_flags([(10, "unheld"), (20, "unheld"),
                                                (30, "unheld")], 60, 10)))


if __name__ == "__main__":
    unittest.main()
