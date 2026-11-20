"""1단계 검증 — 원자성·불변성·중복·낙관적잠금·경로탈출. 외부 의존성 0, 설치 없이 실행.

실행:  cd server && python -m unittest -v
"""
import tempfile
import unittest
from pathlib import Path

from ahkaiving.domain.model import (
    ArchiveItem,
    ContentHash,
    Format,
    ItemMeta,
    ItemStatus,
    Kind,
    SourceRef,
)
from ahkaiving.domain.ports import (
    ConcurrentModification,
    ItemAlreadyExists,
    ItemNotFound,
)
from ahkaiving.infra.fs_repository import FsItemRepository
from ahkaiving.infra.workspace import FsWorkspace


def build_item(staging: Path, body: bytes = b"hello original") -> ArchiveItem:
    """staging 안에 original/ 을 꾸미고 그에 맞는 ArchiveItem 을 만든다(보존 상태로)."""
    orig = staging / "original"
    orig.mkdir(parents=True)
    f = orig / "doc.txt"
    f.write_bytes(body)
    item = ArchiveItem(
        id="20261110-test-doc-ab12",
        kind=Kind("article"),
        format=Format("txt"),
        source=SourceRef(url="https://example.com/doc"),
        hash=ContentHash.of_file(f),
        meta=ItemMeta(title="테스트 문서", tags=["demo"]),
        original_files=["original/doc.txt"],
        handler_ref="text@1",
    )
    item.mark_preserved()
    return item


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "archive"
        self.repo = FsItemRepository(root)
        self.ws = FsWorkspace(root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_get_preserved(self):
        with self.ws.staging() as s:
            item = build_item(s)
            saved = self.repo.add(item, s)
            self.assertFalse(s.exists(), "staging 은 이동 후 사라져야 한다")
        got = self.repo.get(saved.id)
        self.assertEqual(got.status, ItemStatus.PRESERVED)
        self.assertEqual(got.meta.title, "테스트 문서")
        with self.repo.open_file(got.id, "original/doc.txt") as fh:
            self.assertEqual(fh.read(), b"hello original")

    def test_atomic_leftover_cleaned_on_failure(self):
        try:
            with self.ws.staging() as s:
                (s / "original").mkdir()
                raise RuntimeError("수집 도중 실패")
        except RuntimeError:
            pass
        self.assertEqual(list(self.ws.staging_base.iterdir()), [], "실패 staging 은 흔적 없이 정리")

    def test_duplicate_id_rejected(self):
        with self.ws.staging() as s:
            self.repo.add(build_item(s), s)
        with self.ws.staging() as s2:
            with self.assertRaises(ItemAlreadyExists):
                self.repo.add(build_item(s2), s2)

    def test_find_by_hash_and_source(self):
        with self.ws.staging() as s:
            item = self.repo.add(build_item(s), s)
        self.assertIsNotNone(self.repo.find_by_hash(item.hash))
        self.assertIsNotNone(self.repo.find_by_source(SourceRef(url="https://example.com/doc")))
        self.assertIsNone(self.repo.find_by_source(SourceRef(url="https://nope.com")))

    def test_update_optimistic_lock_and_immutability(self):
        with self.ws.staging() as s:
            item = self.repo.add(build_item(s), s)
        before = ContentHash.of_file(
            next(self.repo._find_dir(item.id).glob("original/doc.txt"))
        )
        # 메타 수정은 통과하고 version 이 전진한다.
        item.meta.tags.append("추가됨")
        updated = self.repo.update(item)
        self.assertEqual(updated.version, 1)
        self.assertIn("추가됨", self.repo.get(item.id).meta.tags)
        # 원본은 그대로(해시 불변).
        after = ContentHash.of_file(
            next(self.repo._find_dir(item.id).glob("original/doc.txt"))
        )
        self.assertEqual(str(before), str(after), "원본은 수정되면 안 된다")
        # 낡은 version 으로 다시 쓰면 충돌.
        stale = self.repo.get(item.id)
        stale.version = 0
        with self.assertRaises(ConcurrentModification):
            self.repo.update(stale)

    def test_open_file_rejects_escape(self):
        with self.ws.staging() as s:
            item = self.repo.add(build_item(s), s)
        with self.assertRaises(PermissionError):
            self.repo.open_file(item.id, "../../../secret.txt")

    def test_trash_removes_from_catalog(self):
        with self.ws.staging() as s:
            item = self.repo.add(build_item(s), s)
        self.repo.trash(item.id)
        with self.assertRaises(ItemNotFound):
            self.repo.get(item.id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
