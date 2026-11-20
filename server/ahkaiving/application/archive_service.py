"""ArchiveService — 수집 흐름 오케스트레이션(수직 슬라이스판).

설계서 7단계 중 이 슬라이스가 구현하는 것: ①내용중복 ②유형판별 ④원본수집 ⑥보관확정.
(③저장가능·⑤수집후검증·이벤트 워커는 다음 단계에서 붙인다.)
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse


def _ssl_context() -> ssl.SSLContext:
    # 아나콘다/윈도우에서 CA 번들을 못 찾아 HTTPS가 깨지는 문제 → certifi 사용.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

from ..domain.events import ItemPreserved
from ..domain.model import ArchiveItem, ContentHash, Format, ItemMeta, ItemStatus, Kind, SourceRef
from ..infra.extract import extract_from_bytes


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in = False

    def handle_data(self, data):
        if self._in and len(self.title) < 300:
            self.title += data


# URL 도메인 → kind(무엇인가) 추정
_DOMAIN_KIND = [
    ("youtube.com", "video"), ("youtu.be", "video"),
    ("github.com", "repo"),
    ("reddit.com", "forum"), ("news.ycombinator.com", "forum"),
    ("news.hada.io", "news"),
    ("x.com", "social"), ("twitter.com", "social"), ("linkedin.com", "social"),
    ("threads.com", "social"), ("threads.net", "social"), ("instagram.com", "social"),
    ("arxiv.org", "paper"),
    ("medium.com", "article"),
]


def _kind_for_url(url: str) -> str:
    u = url.lower()
    for dom, kind in _DOMAIN_KIND:
        if dom in u:
            return kind
    return "article"


def _short_title(t: str) -> str:
    """제목을 간결하게 — 공백 정리 후 60자 경계에서 컷. (진짜 요약 제목은 enrich가 생성)"""
    t = re.sub(r"\s+", " ", (t or "").strip())
    if len(t) <= 60:
        return t
    cut = t[:60]
    sp = cut.rfind(" ")
    if sp > 30:
        cut = cut[:sp]
    return cut + "…"


def _slug(name: str) -> str:
    base = name.rsplit(".", 1)[0] if "." in name else name
    s = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", base).strip("-").lower()
    return (s or "item")[:40]


def _safe_filename(name: str) -> str:
    # 경로 조작·숨김 방지. 확장자 유지.
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9가-힣._-]+", "_", name).lstrip(".")
    return name or "file"


class IngestResult:
    def __init__(self, item: ArchiveItem, duplicate: bool):
        self.item = item
        self.duplicate = duplicate


class ArchiveService:
    def __init__(self, repo, workspace, registry, events=None):
        self.repo = repo
        self.workspace = workspace
        self.registry = registry
        self.events = events  # EventBus (옵션) — 없으면 수집된 이벤트를 버퍼에 담음
        self.emitted: list = []

    def _new_id(self, filename: str) -> str:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        import uuid

        return f"{day}-{_slug(filename)}-{uuid.uuid4().hex[:6]}"

    def ingest_upload(self, filename: str, data: bytes) -> IngestResult:
        # ① 내용 중복
        h = ContentHash.of_bytes(data)
        existing = self.repo.find_by_hash(h)
        if existing is not None:
            return IngestResult(existing, duplicate=True)

        # ② 유형 판별
        kind, fmt, handler_ref = self.registry.detect(filename, data)

        # ④ 원본 수집 → ⑥ 보관 확정 (원자적)
        safe = _safe_filename(filename)
        with self.workspace.staging() as s:
            orig = s / "original"
            orig.mkdir(parents=True)
            (orig / safe).write_bytes(data)
            item = ArchiveItem(
                id=self._new_id(filename),
                kind=kind,
                format=fmt,
                source=SourceRef(uploaded_name=filename),
                hash=h,
                meta=ItemMeta(title=filename.rsplit(".", 1)[0]),
                original_files=[f"original/{safe}"],
                handler_ref=handler_ref,
            )
            item.mark_preserved()  # 원본 보존됨 → 안전
            txt = extract_from_bytes(data, fmt.value, safe)  # ⑤ 추출(동기, 가능한 형식만)
            if txt:
                item.extensions["text"] = txt  # 원문 전체 보존(잘림 없음)
                item.status = ItemStatus.EXTRACTED
            self.repo.add(item, s)

        self._emit(ItemPreserved(item.id))
        return IngestResult(item, duplicate=False)

    def ingest_link(self, url: str) -> IngestResult:
        """링크 수집 — 출처 등록 + 원문 스냅샷(가능하면). 전체 재현 스냅샷(SingleFile)은 ④단계.

        현재: 서버가 best-effort 로 페이지를 받아 snapshot.html 보존 + <title> 추출.
        실패(비HTML·차단)해도 링크 자체는 보관(외부 열기).
        """
        url = url.strip()
        # ① 출처 URL 중복
        existing = self.repo.find_by_source(SourceRef(url=url))
        if existing is not None:
            return IngestResult(existing, duplicate=True)

        kind_name = _kind_for_url(url)
        content: bytes | None = None
        ctype = ""
        fetch_err = ""
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (AH!kaiving archiver)"}
            )
            # certifi CA 번들로 HTTPS 인증서 검증(아나콘다/윈도우 CA 미탐 문제 해결).
            with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                content = resp.read(30_000_000)  # 30MB 상한
        except Exception as e:  # 404·403(봇차단)·타임아웃 등 → 링크만 보관 + 사유 기록
            content = None
            fetch_err = f"{type(e).__name__}: {e}"[:200]

        # 받은 내용 분류: PDF / HTML / (그 외는 링크만)
        path = urlparse(url).path.lower()
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        is_pdf = content is not None and (content[:5] == b"%PDF-" or "pdf" in ctype or ext == "pdf")
        is_html = content is not None and not is_pdf and ("html" in ctype or b"<html" in content[:2000].lower())
        pdf = content if is_pdf else None
        html = content if is_html else None
        title = ""
        fmt = "pdf" if is_pdf else ("html" if is_html else "link")
        if is_pdf:
            kind_name = "paper"
            try:
                import pymupdf
                title = ((pymupdf.open(stream=pdf, filetype="pdf").metadata or {}).get("title") or "").strip()
            except Exception:
                title = ""
        elif is_html:
            p = _TitleParser()
            try:
                p.feed(html.decode("utf-8", "ignore"))
            except Exception:
                pass
            title = p.title.strip()

        # 내용(또는 URL) 해시로 2차 중복
        h = ContentHash.of_bytes(content if content is not None else url.encode("utf-8"))
        by_hash = self.repo.find_by_hash(h)
        if by_hash is not None:
            return IngestResult(by_hash, duplicate=True)

        fname = _safe_filename(path.rsplit("/", 1)[-1] or "download")
        label = title or (fname if is_pdf else urlparse(url).netloc) or "link"
        with self.workspace.staging() as s:
            originals: list[str] = []
            to_extract = None  # (bytes, fmt)
            if is_pdf:
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                orig = s / "original"
                orig.mkdir(parents=True)
                (orig / fname).write_bytes(pdf)
                originals = [f"original/{fname}"]
                to_extract = (pdf, "pdf")
            elif is_html:
                orig = s / "original"
                orig.mkdir(parents=True)
                (orig / "snapshot.html").write_bytes(html)
                originals = ["original/snapshot.html"]
                to_extract = (html, "html")
            item = ArchiveItem(
                id=self._new_id(label),
                kind=Kind(kind_name),
                format=Format(fmt),
                source=SourceRef(url=url),
                hash=h,
                meta=ItemMeta(title=_short_title(title or label)),
                original_files=originals,
                handler_ref=f"{fmt}@0",
            )
            item.mark_preserved()
            if fmt == "link":  # 내용을 못 받았음 → UI가 안내할 수 있게 사유 기록
                item.extensions["fetch_error"] = (
                    fetch_err or "자동 수집 실패 — 이 사이트가 봇 접근을 막거나(로그인·차단), 미리보기 불가 형식일 수 있어요."
                )
            if to_extract is not None:  # ⑤ 추출
                txt = extract_from_bytes(to_extract[0], to_extract[1])
                if txt:
                    item.extensions["text"] = txt[:20000]
                    item.status = ItemStatus.EXTRACTED
            self.repo.add(item, s)

        self._emit(ItemPreserved(item.id))
        return IngestResult(item, duplicate=False)

    def _emit(self, event) -> None:
        self.emitted.append(event)
        if self.events is not None:
            self.events.publish(event)
