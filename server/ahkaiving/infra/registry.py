"""SimpleTypeRegistry — ② 유형 판별의 1차 구현(확장자 + 매직바이트).

설계서의 HandlerRegistry/TypeDetection 자리를 채우는 최소판. 나중에 Handler 레지스트리로 확장.
kind(무엇) × format(파일형태) 2축으로 돌려준다.
"""
from __future__ import annotations

from ..domain.model import Format, Kind

# 확장자 → (kind, format)
_EXT = {
    "pdf": ("paper", "pdf"),
    "png": ("visual", "image"),
    "jpg": ("visual", "image"),
    "jpeg": ("visual", "image"),
    "gif": ("visual", "image"),
    "webp": ("visual", "image"),
    "svg": ("visual", "image"),
    "pptx": ("slides", "pptx"),
    "key": ("slides", "keynote"),
    "docx": ("doc", "docx"),
    "hwp": ("doc", "hwp"),
    "hwpx": ("doc", "hwp"),
    "md": ("doc", "markdown"),
    "txt": ("doc", "text"),
    "csv": ("dataset", "csv"),
    "xlsx": ("dataset", "xlsx"),
    "json": ("dataset", "json"),
    "html": ("article", "html"),
    "htm": ("article", "html"),
    "ipynb": ("notebook", "ipynb"),
    "zip": ("bundle", "zip"),
    "mp3": ("audio", "mp3"),
    "mp4": ("video", "mp4"),
}


def _by_magic(data: bytes) -> tuple[str, str] | None:
    if data[:5] == b"%PDF-":
        return ("paper", "pdf")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ("visual", "image")
    if data[:3] == b"\xff\xd8\xff":
        return ("visual", "image")
    if data[:2] == b"PK":  # zip 계열(pptx·docx·xlsx·zip)
        return None  # 확장자로 판별 필요
    return None


class SimpleTypeRegistry:
    def detect(self, filename: str, data: bytes) -> tuple[Kind, Format, str]:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        guess = _EXT.get(ext)
        magic = _by_magic(data)
        # 매직바이트가 확장자와 달라도 '진짜 내용' 우선(확장자 위조 방지). zip계열은 확장자 신뢰.
        kind_fmt = magic or guess or ("other", ext or "bin")
        kind, fmt = kind_fmt
        handler_ref = f"{fmt}@1"
        return Kind(kind), Format(fmt), handler_ref
