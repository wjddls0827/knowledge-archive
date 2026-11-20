import unittest

from ahkaiving.infra.deadline import normalize_deadline


class DeadlineTest(unittest.TestCase):
    def test_date(self):
        self.assertEqual(normalize_deadline("2026-12-01"), "2026-12-01")
        self.assertEqual(normalize_deadline("마감 2026-12-01 까지"), "2026-12-01")

    def test_rolling(self):
        for s in ["상시", "상시채용", "수시채용", "채용 시", "rolling", "until filled"]:
            self.assertEqual(normalize_deadline(s), "상시", s)

    def test_closed(self):
        for s in ["마감", "종료", "마감됨", "closed", " 마감 "]:
            self.assertEqual(normalize_deadline(s), "마감", s)

    def test_none_or_unknown(self):
        for s in ["", None, "미정", "날짜 없음"]:
            self.assertIsNone(normalize_deadline(s), repr(s))

    def test_closed_vs_rolling_precedence(self):
        # '마감' 단독 = 수동 종료 / '마감 시'·'채용 시'는 상시(수시채용)
        self.assertEqual(normalize_deadline("마감"), "마감")
        self.assertEqual(normalize_deadline("마감 시"), "상시")
        self.assertEqual(normalize_deadline("채용 시 마감"), "상시")


if __name__ == "__main__":
    unittest.main()
