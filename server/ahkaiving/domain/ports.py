"""포트(인터페이스) — 도메인이 바깥에 요구하는 계약. 구현(어댑터)은 infra/ 에.

의존 방향은 항상 바깥 → 안. 도메인은 이 ABC만 알고, 파일시스템·네트워크는 모른다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .model import ArchiveItem, ContentHash, SourceRef


# ── 예외 ──────────────────────────────────────────────────────────────
class RepositoryError(Exception):
    pass


class ItemNotFound(RepositoryError):
    pass


class ItemAlreadyExists(RepositoryError):
    pass


class ConcurrentModification(RepositoryError):
    """version 낙관적 잠금 충돌."""


class CorruptedItem(RepositoryError):
    """meta.json 깨짐 또는 원본 해시 불일치."""


# ── 저장소 ────────────────────────────────────────────────────────────
class ItemRepository(ABC):
    """'자료를 잃어버리지 않게 보관하고 다시 찾아주는 것' 하나만 책임진다.

    검색은 SearchIndex, 수집은 Handler, 경로는 Workspace, 이벤트는 서비스가 맡는다.
    """

    @abstractmethod
    def add(self, item: ArchiveItem, staging_dir: Path) -> ArchiveItem:
        """staging_dir(완성된 아이템 폴더)을 원자적으로 보관소에 들인다.

        같은 id가 이미 있으면 ItemAlreadyExists. 성공 시 '완전히 있거나 아예 없다'.
        """

    @abstractmethod
    def get(self, item_id: str) -> ArchiveItem: ...

    @abstractmethod
    def find_by_hash(self, h: ContentHash) -> Optional[ArchiveItem]: ...

    @abstractmethod
    def find_by_source(self, source: SourceRef) -> Optional[ArchiveItem]: ...

    @abstractmethod
    def iter_all(self) -> Iterator[ArchiveItem]: ...

    @abstractmethod
    def update(self, item: ArchiveItem) -> ArchiveItem:
        """meta·derived만 갱신(원본 불가). version 낙관적 잠금 — 어긋나면 ConcurrentModification."""

    @abstractmethod
    def open_file(self, item_id: str, rel_path: str):
        """원본/파생 파일을 **읽기 전용**으로 연다. '..' 탈출 거부."""

    @abstractmethod
    def trash(self, item_id: str) -> None:
        """영구 삭제가 아니라 .trash/ 로 이동(복구 가능). 영구삭제는 인터페이스에 없음."""


# ── 작업 공간 ─────────────────────────────────────────────────────────
class Workspace(ABC):
    """수집 중 쓰는 임시 공간. 보관소와 같은 파일시스템이라 최종 이동이 원자적이다."""

    @abstractmethod
    @contextmanager
    def staging(self) -> Iterator[Path]:
        """새 staging 폴더를 열고 경로를 준다. 실패하면 흔적 없이 정리."""

    @abstractmethod
    def cleanup_leftovers(self) -> int:
        """시작 시 남은 staging 잔여물 정리. 정리한 개수 반환."""
