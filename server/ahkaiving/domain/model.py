"""도메인 모델 — 순수 파이썬(의존성 0). 설계서 「객체 설계」·「저장소 설계」 기준.

원칙: 도메인은 바깥(파일시스템·네트워크·LLM)을 모른다. 값 객체 + 애그리거트 루트만.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 값 객체 ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Kind:
    """무엇인가 — 분류·학습·공유용. **열린 값 객체**(Enum 아님): 새 값 추가해도 구조 불변."""
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Format:
    """어떤 파일 형태인가 — 저장·추출·뷰어를 결정."""
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ContentHash:
    """내용 지문 — 2차 중복 판정·원본 무결성 검증에 사용."""
    algo: str
    digest: str

    def __str__(self) -> str:
        return f"{self.algo}:{self.digest}"

    @classmethod
    def parse(cls, s: str) -> "ContentHash":
        algo, _, digest = s.partition(":")
        return cls(algo, digest)

    @classmethod
    def of_file(cls, path: Path, algo: str = "sha256") -> "ContentHash":
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return cls(algo, h.hexdigest())

    @classmethod
    def of_bytes(cls, data: bytes, algo: str = "sha256") -> "ContentHash":
        return cls(algo, hashlib.new(algo, data).hexdigest())


@dataclass(frozen=True)
class SourceRef:
    """자료의 출처 — 어느 것도 없을 수 있다(업로드 원본 등)."""
    url: Optional[str] = None
    git_commit: Optional[str] = None
    uploaded_name: Optional[str] = None


class ItemStatus(str, Enum):
    PENDING = "pending"      # 수집 시작 전
    PRESERVED = "preserved"  # 원본 보존 완료(동기 경로 끝) — 안전
    EXTRACTED = "extracted"  # 본문 텍스트 추출됨
    ENRICHED = "enriched"    # 분류·요약·태그 보강됨
    FAILED = "failed"


@dataclass
class ItemMeta:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: Optional[str] = None
    freshness: Optional[str] = None  # high | mid | low
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    category: Optional[str] = None   # 내용 분류(메인 축): trend | concept | case | opinion
    category_guess: bool = False     # 에이전트 추정값(미확정) → UI에 '추정' 표시, 사용자가 고치면 False


# ── 애그리거트 루트 ───────────────────────────────────────────────────
@dataclass
class ArchiveItem:
    """자료 한 건. 모든 쓰기는 이 객체를 통해서만 — 불변식(원본 불변) 보호."""
    id: str
    kind: Kind
    format: Format
    source: SourceRef
    hash: ContentHash
    status: ItemStatus = ItemStatus.PENDING
    meta: ItemMeta = field(default_factory=ItemMeta)
    original_files: list[str] = field(default_factory=list)  # 아이템 폴더 기준 상대경로(읽기전용)
    derived_files: list[str] = field(default_factory=list)   # 파생본(이미지·PDF변환·content.md…)
    handler_ref: str = ""          # "pptx@1" — 재처리 대상 선별
    version: int = 0               # 낙관적 잠금
    extensions: dict = field(default_factory=dict)  # 다음 단계 슬롯: learning / sharing
    user_confirmed: bool = False
    created_at: str = field(default_factory=now_iso)
    archived_at: Optional[str] = None

    # 행위 ──
    def mark_preserved(self) -> None:
        if self.status is ItemStatus.FAILED:
            raise ValueError("실패한 자료는 보존 완료로 바꿀 수 없다")
        self.status = ItemStatus.PRESERVED
        self.archived_at = now_iso()

    def set_extension(self, slot: str, data: dict) -> None:
        """learning / sharing 등 다음 단계 데이터. 원본·식별 필드는 못 건드림."""
        self.extensions[slot] = data

    # 직렬화 (meta.json) ──
    def to_meta_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind.value,
            "format": self.format.value,
            "handler": self.handler_ref,
            "status": self.status.value,
            "source": {
                "url": self.source.url,
                "uploaded_name": self.source.uploaded_name,
                "git_commit": self.source.git_commit,
            },
            "hash": str(self.hash),
            "title": self.meta.title,
            "authors": self.meta.authors,
            "published_at": self.meta.published_at,
            "freshness": self.meta.freshness,
            "category": self.meta.category,
            "category_guess": self.meta.category_guess,
            "summary": self.meta.summary,
            "tags": self.meta.tags,
            "original_files": self.original_files,
            "derived_files": self.derived_files,
            "user_confirmed": self.user_confirmed,
            "created_at": self.created_at,
            "archived_at": self.archived_at,
            "extensions": self.extensions,
        }

    @classmethod
    def from_meta_dict(cls, d: dict) -> "ArchiveItem":
        src = d.get("source") or {}
        return cls(
            id=d["id"],
            kind=Kind(d["kind"]),
            format=Format(d["format"]),
            source=SourceRef(
                url=src.get("url"),
                git_commit=src.get("git_commit"),
                uploaded_name=src.get("uploaded_name"),
            ),
            hash=ContentHash.parse(d["hash"]),
            status=ItemStatus(d.get("status", "pending")),
            meta=ItemMeta(
                title=d.get("title", ""),
                authors=list(d.get("authors") or []),
                published_at=d.get("published_at"),
                freshness=d.get("freshness"),
                summary=d.get("summary", ""),
                tags=list(d.get("tags") or []),
                category=d.get("category"),
                category_guess=bool(d.get("category_guess", False)),
            ),
            original_files=list(d.get("original_files") or []),
            derived_files=list(d.get("derived_files") or []),
            handler_ref=d.get("handler", ""),
            version=int(d.get("version", 0)),
            extensions=dict(d.get("extensions") or {}),
            user_confirmed=bool(d.get("user_confirmed", False)),
            created_at=d.get("created_at") or now_iso(),
            archived_at=d.get("archived_at"),
        )
