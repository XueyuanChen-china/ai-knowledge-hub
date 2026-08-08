import unittest

from app.services.context_gap_detector import detect_context_gap


class ContextGapDetectorTests(unittest.TestCase):
    def test_explicit_history_reference_requires_recovery(self) -> None:
        decision = detect_context_gap("之前说过的金额门槛是什么？", {})

        self.assertTrue(decision.need_recovery)
        self.assertIn("explicit_history_reference", decision.triggers)

    def test_short_follow_up_without_context_requires_recovery(self) -> None:
        decision = detect_context_gap("那它呢？", {"recent_messages": []})

        self.assertTrue(decision.need_recovery)
        self.assertIn("pronoun_without_antecedent", decision.triggers)

    def test_recent_context_avoids_unnecessary_recovery(self) -> None:
        decision = detect_context_gap(
            "它的金额是多少？",
            {"recent_messages": [{"role": "user", "content": "采购复核门槛"}]},
        )

        self.assertFalse(decision.need_recovery)


if __name__ == "__main__":
    unittest.main()
