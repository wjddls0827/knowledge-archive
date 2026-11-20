import unittest

from ahkaiving.domain.payload import validate_payload


class PayloadValidateTest(unittest.TestCase):
    def test_valid_payload_no_errors(self):
        e, w = validate_payload({
            "url": "https://x", "title": "t", "tags": ["a"],
            "segments": [{"o": "x", "k": "y"}], "category_guess": True,
        })
        self.assertEqual(e, [])
        self.assertEqual(w, [])

    def test_list_field_wrong_type_is_error(self):
        e, w = validate_payload({"tags": "문자열아님"})
        self.assertTrue(any("tags" in x for x in e))

    def test_unknown_field_is_warning_not_error(self):
        e, w = validate_payload({"due_date": "2026-12-01"})  # 오타(deadline 의도)
        self.assertEqual(e, [])
        self.assertTrue(any("due_date" in x for x in w))

    def test_scalar_wrong_type_is_warning(self):
        e, w = validate_payload({"title": 123})
        self.assertEqual(e, [])
        self.assertTrue(any("title" in x for x in w))

    def test_none_values_skipped(self):
        e, w = validate_payload({"url": None, "tags": None, "summary": None})
        self.assertEqual(e, [])
        self.assertEqual(w, [])

    def test_non_dict_is_error(self):
        e, w = validate_payload(["not", "a", "dict"])
        self.assertTrue(e)


if __name__ == "__main__":
    unittest.main()
