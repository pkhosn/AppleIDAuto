import threading
import time
import unittest

from app.jobs import CaptchaChallengeManager


class CaptchaChallengeManagerTest(unittest.TestCase):
    def test_answer_is_one_time_and_bound_to_challenge(self):
        manager = CaptchaChallengeManager()
        result = []
        thread = threading.Thread(
            target=lambda: result.append(manager.wait_for_answer("job-1", b"png", 1, False, 1))
        )
        thread.start()
        for _ in range(100):
            challenge = manager.metadata("job-1")
            if challenge:
                break
            time.sleep(0.001)
        self.assertEqual(b"png", manager.image("job-1", challenge["challenge_id"]))
        self.assertTrue(manager.answer("job-1", challenge["challenge_id"], "ab12"))
        self.assertFalse(manager.answer("job-1", challenge["challenge_id"], "AB12"))
        thread.join(1)
        self.assertEqual(["AB12"], result)
        self.assertIsNone(manager.metadata("job-1"))

    def test_wrong_job_invalid_and_expired_answers_are_rejected(self):
        manager = CaptchaChallengeManager()
        with self.assertRaises(ValueError):
            manager.answer("job", "challenge", "bad!")
        self.assertFalse(manager.answer("other", "challenge", "AB12"))
        with self.assertRaises(TimeoutError):
            manager.wait_for_answer("job", b"png", 1, False, 0.001)


if __name__ == "__main__":
    unittest.main()
