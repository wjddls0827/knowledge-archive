import unittest

from ahkaiving.infra.sites_store import (
    find_site_node,
    iter_site_dicts,
    safe_icon,
    san_node,
)


class SitesStoreTest(unittest.TestCase):
    def test_safe_icon_rejects_path_traversal(self):
        self.assertEqual(safe_icon("ok.png"), "ok.png")
        for bad in ["../x", "a/b", "a\\b", "..", "../../etc/passwd"]:
            self.assertEqual(safe_icon(bad), "", bad)

    def test_san_node_strips_unknown_and_traversal(self):
        n = san_node({"id": "a1", "kind": "site", "url": "https://x",
                      "title": "T", "icon": "../evil.png", "hack": "drop"})
        self.assertEqual(n["icon"], "")        # 경로탈출 아이콘 제거
        self.assertNotIn("hack", n)            # 미지 필드 제거
        self.assertEqual(n["kind"], "site")

    def test_san_node_requires_id(self):
        self.assertIsNone(san_node({"kind": "site"}))
        self.assertIsNone(san_node("not a dict"))

    def test_folder_nesting_limited_to_one_level(self):
        f = san_node({"id": "f1", "kind": "folder", "title": "F", "items": [
            {"id": "c1", "kind": "site", "url": "u"},
            {"id": "f2", "kind": "folder", "items": []},   # 중첩 폴더 → 제거돼야
        ]})
        self.assertEqual(len(f["items"]), 1)
        self.assertEqual(f["items"][0]["id"], "c1")

    def test_iter_and_find_across_folders(self):
        tree = [
            san_node({"id": "f1", "kind": "folder", "items": [{"id": "c1", "kind": "site", "url": "u"}]}),
            san_node({"id": "s1", "kind": "site", "url": "u"}),
        ]
        ids = sorted(x["id"] for x in iter_site_dicts(tree))
        self.assertEqual(ids, ["c1", "s1"])
        self.assertEqual(find_site_node("c1", tree)["id"], "c1")   # 폴더 안까지 검색
        self.assertIsNone(find_site_node("nope", tree))


if __name__ == "__main__":
    unittest.main()
