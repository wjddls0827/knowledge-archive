"""FsItemRepository — 파일시스템 보관소.

레이아웃(설계서 「저장소 설계」):
  archive/items/YYYY/MM/<id>/
    meta.json            자료 메타(원본 데이터)
    original/            업로드 원본(읽기 전용, 불변)
    derived/             파생본(이미지·PDF변환·content.md…)
규칙: 원자성(staging→rename) · 불변성(original 금지) · 낙관적 잠금(version).
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Iterator, Optional

from ..domain.model import ArchiveItem, ContentHash, SourceRef
from ..domain.ports import (
    ConcurrentModification,
    CorruptedItem,
    ItemAlreadyExists,
    ItemNotFound,
    ItemRepository,
)

_RESERVED = {".staging", ".trash"}


class FsItemRepository(ItemRepository):
    def __init__(self, archive_root: Path):
        self.root = Path(archive_root)
        self.items_dir = self.root / "items"
        self.trash_dir = self.items_dir / ".trash"
        self.items_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    # ── 경로 ──
    def _bucket(self, item: ArchiveItem) -> str:
        ts = item.archived_at or item.created_at  # ISO8601
        return f"{ts[0:4]}/{ts[5:7]}"

    def _dir_for(self, item: ArchiveItem) -> Path:
        return self.items_dir / self._bucket(item) / item.id

    def _iter_meta_files(self) -> Iterator[Path]:
        if not self.items_dir.exists():
            return
        for year in self.items_dir.iterdir():
            if not year.is_dir() or year.name in _RESERVED:
                continue
            for month in year.iterdir():
                if not month.is_dir():
                    continue
                for item_dir in month.iterdir():
                    meta = item_dir / "meta.json"
                    if meta.is_file():
                        yield meta

    def _find_dir(self, item_id: str) -> Optional[Path]:
        for meta in self._iter_meta_files():
            if meta.parent.name == item_id:
                return meta.parent
        return None

    def _load(self, meta_path: Path) -> ArchiveItem:
        try:
            d = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise CorruptedItem(f"meta 손상: {meta_path} ({e})")
        return ArchiveItem.from_meta_dict(d)

    def _write_meta(self, item_dir: Path, item: ArchiveItem) -> None:
        # tmp 에 쓰고 rename 으로 교체 — 반쯤 쓰인 meta 가 보이지 않게.
        tmp = item_dir / "meta.json.tmp"
        data = json.dumps(item.to_meta_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # 쓰기 핸들에서 fsync (Windows 호환)
        os.replace(tmp, item_dir / "meta.json")

    def _make_readonly(self, directory: Path) -> None:
        if not directory.exists():
            return
        for p in directory.rglob("*"):
            if p.is_file():
                try:
                    os.chmod(p, stat.S_IREAD)
                except OSError:
                    pass  # 윈도우 등에서 best-effort

    # ── ItemRepository ──
    def add(self, item: ArchiveItem, staging_dir: Path) -> ArchiveItem:
        if self._find_dir(item.id) is not None:
            raise ItemAlreadyExists(item.id)
        staging_dir = Path(staging_dir)
        # 1) 완성된 아이템 폴더(staging) 안에 meta 를 쓴다.
        self._write_meta(staging_dir, item)
        # 2) 디렉터리 fsync(best-effort)
        try:
            fd = os.open(staging_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            pass  # 윈도우는 디렉터리 fsync 미지원 — 무시
        # 3) 원자적 이동: 완전히 있거나 아예 없다.
        target = self._dir_for(item)
        if target.exists():
            raise ItemAlreadyExists(item.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging_dir, target)
        # 4) 원본 읽기전용(불변) — best-effort
        self._make_readonly(target / "original")
        return item

    def get(self, item_id: str) -> ArchiveItem:
        d = self._find_dir(item_id)
        if d is None:
            raise ItemNotFound(item_id)
        return self._load(d / "meta.json")

    def find_by_hash(self, h: ContentHash) -> Optional[ArchiveItem]:
        target = str(h)
        for meta in self._iter_meta_files():
            item = self._load(meta)
            if str(item.hash) == target:
                return item
        return None

    def find_by_source(self, source: SourceRef) -> Optional[ArchiveItem]:
        for meta in self._iter_meta_files():
            item = self._load(meta)
            s = item.source
            if source.url and s.url == source.url:
                return item
            if source.git_commit and s.git_commit == source.git_commit:
                return item
        return None

    def iter_all(self) -> Iterator[ArchiveItem]:
        for meta in self._iter_meta_files():
            yield self._load(meta)

    def update(self, item: ArchiveItem) -> ArchiveItem:
        d = self._find_dir(item.id)
        if d is None:
            raise ItemNotFound(item.id)
        current = self._load(d / "meta.json")
        if current.version != item.version:
            raise ConcurrentModification(
                f"{item.id}: 기대 version {item.version}, 실제 {current.version}"
            )
        item.version += 1  # 낙관적 잠금 전진
        self._write_meta(d, item)
        return item

    def open_file(self, item_id: str, rel_path: str):
        d = self._find_dir(item_id)
        if d is None:
            raise ItemNotFound(item_id)
        base = d.resolve()
        target = (base / rel_path).resolve()
        if base != target and base not in target.parents:
            raise PermissionError(f"경로 탈출 거부: {rel_path}")
        return open(target, "rb")

    def attach_file(self, item_id: str, safe_name: str, data: bytes) -> str:
        """사용자 첨부자료를 아이템의 attachments/ 에 저장하고 상대경로를 돌려준다.

        original/ 과 달리 쓰기 가능한 영역이다(사용자가 나중에 추가·삭제). safe_name 은
        app 계층에서 이미 정제된 파일명. 이름이 겹치면 임의 접미사로 피한다.
        """
        d = self._find_dir(item_id)
        if d is None:
            raise ItemNotFound(item_id)
        att = d / "attachments"
        att.mkdir(parents=True, exist_ok=True)
        name = safe_name or "file"
        target = att / name
        if target.exists():
            stem = target.stem or "file"
            target = att / f"{stem}-{os.urandom(3).hex()}{target.suffix}"
        target.write_bytes(data)
        return f"attachments/{target.name}"

    def delete_attachment(self, item_id: str, rel_path: str) -> None:
        """attachments/ 안의 첨부 파일 1개 삭제(경로 탈출·다른 폴더 거부)."""
        d = self._find_dir(item_id)
        if d is None:
            raise ItemNotFound(item_id)
        base = d.resolve()
        target = (base / rel_path).resolve()
        if base not in target.parents or target.parent.name != "attachments":
            raise PermissionError(f"첨부 경로만 삭제 가능: {rel_path}")
        if target.is_file():
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
            target.unlink()

    def trash(self, item_id: str) -> None:
        d = self._find_dir(item_id)
        if d is None:
            raise ItemNotFound(item_id)
        dest = self.trash_dir / item_id
        if dest.exists():
            dest = self.trash_dir / f"{item_id}-{os.urandom(3).hex()}"
        # trash 안은 복구 위해 쓰기 가능해야 함.
        for p in d.rglob("*"):
            if p.is_file():
                try:
                    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        os.rename(d, dest)
