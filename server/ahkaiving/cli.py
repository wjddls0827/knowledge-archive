"""moa — Claude Code가 직접 수집한 자료를 보관소에 밀어넣는 통로 (구조 B).

역할 분리 설계(B안): 수집·번역·요약은 Claude Code(에이전트)가 자기 도구로 하고,
웹앱은 읽기 전용 대시보드로만 쓴다. 이 CLI가 그 "쓰기 입구"다.

API 키가 필요 없다 — 번역/요약은 에이전트가 이미 만들어 payload 로 넘긴다.

사용:
  python -m ahkaiving.cli ingest <payload.json>     # 완성 자료 1건 저장
  python -m ahkaiving.cli ingest -                  # stdin 으로 payload JSON
  python -m ahkaiving.cli list                       # 보관 자료 목록(점검용)

payload(JSON) 필드 — 전부 선택이지만 url 또는 original_file 중 하나는 권장:
  url            출처 URL
  kind           매체 유형(article/news/paper/social/forum/video/repo/...) — 없으면 URL로 추정. 꼬리표·뷰어용
  category       내용 분류(메인 축): trend(트렌드)·concept(기본개념)·case(사례)·opinion(관점)
  category_guess 위 분류가 에이전트 추정값이면 true(UI에 '추정' 표시) — 확신하면 생략
  title          제목(없으면 URL 도메인)
  authors        ["저자", ...]
  published_at   "2026-11-10" 등
  deadline       지원 마감일(주로 채용공고 JD) "2026-12-01" — 캘린더가 이 날짜로 표기. 상시채용이면 "상시". 불명이면 생략.
  freshness      high | mid | low
  original_file  내가 내려받은 원본 파일 경로(PDF·이미지 등) → 원본 불변 보존
  thumbnail      화면 스크린샷 이미지 파일 경로 → derived/cover.png(썸네일). 모든 수집물에 권장(PDF는 1페이지 커버 자동).
  original_html  내가 가져온 원문 HTML 문자열 → original/snapshot.html 로 보존
  text           추출한 본문 텍스트(검색·열람용)
  translation    한국어 번역(에이전트가 작성)
  summary        요약(에이전트가 작성)
  tags           ["키워드", ...]
  interpretation 내 해석(선택)
  notes          사용자 메모(선택) — 에이전트는 여기에 쓰지 않는다(사용자 전용 칸).
  provenance     수집 경위 메모(선택) — 번역 생략 이유·날짜 추정 근거·댓글 수집 통계 등 에이전트 주석은 여기에.
  glossary       [{"term","meaning"}, ...]
  segments       원문↔번역 문단 쌍(정렬 리더용): [{"o":원문문단,"k":번역문단}, ...] — 문단 수·순서 일치.
  related        관련 자료 연결: [{"id":상대자료ID, "rel":"원 출처"|"소개한 글"|..., "title":표시명}, ...]
                 같은 주제의 원 출처·2차 자료를 서로 잇는다(대시보드 상세 '관련 자료' 블록). 양쪽에 각각 넣어야 상호 연결.
  attachments    수집 중 '본문 안에서 발견한' 원문 출처·참고/영상 링크: [{"kind":"link"|"video","name":표시명,"url":주소}, ...]
                 첨부 탭에 북마크 카드(제목·도메인)·영상 미리보기로 뜬다. kind 생략 시 YouTube/Vimeo는 video로 자동 판별.
                 광고·내비·추천 링크 말고 본문이 실제로 가리키는 원문/참고 자료만. (사용자가 직접 올린 첨부는 별도 — 덮어쓰지 말 것)
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .application.archive_service import _kind_for_url, _safe_filename, _short_title, _slug
from .domain.model import (
    ArchiveItem,
    ContentHash,
    Format,
    ItemMeta,
    ItemStatus,
    Kind,
    SourceRef,
)
from .infra.fs_repository import FsItemRepository
from .infra.registry import SimpleTypeRegistry
from .infra.workspace import FsWorkspace


def _archive_root() -> Path:
    server_dir = Path(__file__).resolve().parents[1]
    return Path(os.environ.get("AHK_ARCHIVE", server_dir / "data" / "archive"))


def _new_id(label: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{day}-{_slug(label)}-{uuid.uuid4().hex[:6]}"


def _load_payload(arg: str) -> dict:
    raw = sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("payload 는 JSON 객체여야 해요.")
    return data


def cmd_ingest(arg: str) -> int:
    p = _load_payload(arg)
    url = (p.get("url") or "").strip() or None
    registry = SimpleTypeRegistry()

    repo = FsItemRepository(_archive_root())
    workspace = FsWorkspace(_archive_root())
    workspace.cleanup_leftovers()

    # 출처 URL 중복 차단(이미 있으면 재수집 대신 교체하라고 안내)
    if url is not None:
        dup = repo.find_by_source(SourceRef(url=url))
        if dup is not None:
            print(f"! 같은 URL 자료가 이미 있어요: {dup.id} (status={dup.status.value})")
            print("  다시 넣으려면 먼저 삭제: python -m ahkaiving.cli trash " + dup.id)
            return 2

    # ── 원본 결정: 파일 > HTML 문자열 > 링크만 ──
    kind_name = p.get("kind") or (_kind_for_url(url) if url else "article")
    fmt = "link"
    original_bytes: bytes | None = None
    original_rel: str | None = None
    orig_filename = "snapshot.html"

    if p.get("original_file"):
        src = Path(p["original_file"])
        if not src.is_file():
            raise SystemExit(f"original_file 없음: {src}")
        original_bytes = src.read_bytes()
        k, f, _ = registry.detect(src.name, original_bytes)
        kind_name = p.get("kind") or k.value
        fmt = f.value
        orig_filename = _safe_filename(src.name)
        original_rel = f"original/{orig_filename}"
    elif p.get("original_html"):
        original_bytes = p["original_html"].encode("utf-8")
        fmt = "html"
        orig_filename = "snapshot.html"
        original_rel = "original/snapshot.html"

    # 해시: 원본 > 본문 > URL
    if original_bytes is not None:
        h = ContentHash.of_bytes(original_bytes)
    elif p.get("text"):
        h = ContentHash.of_bytes(p["text"].encode("utf-8"))
    elif url:
        h = ContentHash.of_bytes(url.encode("utf-8"))
    else:
        h = ContentHash.of_bytes(uuid.uuid4().hex.encode("utf-8"))

    # 내용 해시 중복도 조사 — 같은 내용이 URL 없이/다른 경로로 또 들어오는 것 차단.
    # (URL·본문·원본이 모두 없을 때만 랜덤 해시라 오탐 없음.)
    dup_h = repo.find_by_hash(h)
    if dup_h is not None:
        print(f"! 같은 내용 자료가 이미 있어요: {dup_h.id} (status={dup_h.status.value})")
        print("  다시 넣으려면 먼저 삭제: python -m ahkaiving.cli trash " + dup_h.id)
        return 2

    title = p.get("title") or (url and SourceRef(url=url).url) or "무제"
    label = p.get("title") or orig_filename or (url or "item")

    with workspace.staging() as s:
        originals: list[str] = []
        if original_bytes is not None and original_rel is not None:
            orig_dir = s / "original"
            orig_dir.mkdir(parents=True)
            (orig_dir / orig_filename).write_bytes(original_bytes)
            originals = [original_rel]

        derived: list[str] = []
        if fmt == "pdf" and original_bytes is not None:
            try:
                import pymupdf
                doc = pymupdf.open(stream=original_bytes, filetype="pdf")
                pix = doc.load_page(0).get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4))
                ddir = s / "derived"
                ddir.mkdir(parents=True, exist_ok=True)
                pix.save(str(ddir / "cover.png"))
                derived = ["derived/cover.png"]
            except Exception:
                derived = []
        # 모든 수집물 공통 썸네일: 에이전트가 넘긴 화면 스크린샷 이미지 → derived/cover.png (PDF는 이미 1페이지 커버가 있으면 유지)
        if p.get("thumbnail") and "derived/cover.png" not in derived:
            try:
                import shutil as _sh
                tsrc = Path(p["thumbnail"])
                if tsrc.is_file():
                    ddir = s / "derived"
                    ddir.mkdir(parents=True, exist_ok=True)
                    _sh.copyfile(tsrc, ddir / "cover.png")
                    derived.append("derived/cover.png")
            except Exception:
                pass

        item = ArchiveItem(
            id=_new_id(label),
            kind=Kind(kind_name),
            format=Format(fmt),
            source=SourceRef(url=url),
            hash=h,
            meta=ItemMeta(
                title=_short_title(title),
                authors=list(p.get("authors") or []),
                published_at=p.get("published_at"),
                freshness=p.get("freshness"),
                summary=(p.get("summary") or "").strip(),
                tags=[t.strip() for t in (p.get("tags") or []) if str(t).strip()][:12],
                category=(p.get("category") or None),
                category_guess=bool(p.get("category_guess", False)),
            ),
            original_files=originals,
            derived_files=derived,
            handler_ref=f"{fmt}@0",
        )
        item.mark_preserved()  # 원본 보존됨 → 안전

        # 본문/번역/해석/메모/단어장 → extensions
        if p.get("text"):
            item.extensions["text"] = p["text"]  # 원문 전체 보존(잘림 없음)
            item.status = ItemStatus.EXTRACTED
        if p.get("translation"):
            item.extensions["translation"] = p["translation"]
        if p.get("interpretation"):
            item.extensions["interpretation"] = p["interpretation"]
        if p.get("notes"):
            item.extensions["notes"] = p["notes"]            # 사용자 전용 메모(에이전트는 쓰지 않음)
        if p.get("provenance"):
            item.extensions["provenance"] = p["provenance"]  # 수집 경위(에이전트 자동 메모)
        _dl = str(p.get("deadline") or "").strip()  # 마감일(JD) — 날짜 또는 '상시'
        if _dl:
            import re as _re_dl
            if _re_dl.search(r"상시|수시|채용\s*시|마감\s*시|rolling|until\s*filled", _dl, _re_dl.I):
                item.extensions["deadline"] = "상시"
            else:
                _m_dl = _re_dl.search(r"\d{4}-\d{2}-\d{2}", _dl)
                if _m_dl:
                    item.extensions["deadline"] = _m_dl.group(0)
        if p.get("glossary"):
            item.extensions["glossary"] = [
                {"term": g.get("term", ""), "meaning": g.get("meaning", "")}
                for g in p["glossary"] if g.get("term")
            ]
        if p.get("segments"):  # 원문↔번역 문단 쌍(정렬 리더용): [{o, k}]
            item.extensions["segments"] = [
                {"o": (s.get("o") or s.get("orig") or ""), "k": (s.get("k") or s.get("ko") or "")}
                for s in p["segments"] if (s.get("o") or s.get("orig") or s.get("k") or s.get("ko"))
            ]
        if p.get("related"):  # 관련 자료 연결: [{id, rel, title}] — 같은 주제의 원 출처·2차 자료를 잇는다
            item.extensions["related"] = [
                {"id": r.get("id", ""), "rel": (r.get("rel") or "관련"), "title": r.get("title", "")}
                for r in p["related"] if r.get("id")
            ]
        if p.get("attachments"):  # 수집 중 발견한 원문 출처·참고/영상 링크 → 첨부(첨부 탭에서 북마크처럼 열람·미리보기)
            _att_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _atts = []
            for a in (p["attachments"] or []):
                u = (a.get("url") or "").strip()
                if not u:
                    continue
                low = u.lower()
                akind = a.get("kind") or ("video" if ("youtube.com" in low or "youtu.be" in low or "vimeo.com" in low) else "link")
                _atts.append({"id": uuid.uuid4().hex[:8], "kind": akind,
                              "name": (a.get("name") or "").strip() or u, "url": u, "added_at": _att_now})
            if _atts:
                item.extensions["attachments"] = _atts
        if url is None and original_bytes is None:
            item.extensions["fetch_error"] = "원본 없이 메타만 저장됨"

        # 요약이 있으면 분류·요약 단계로 전진(스테퍼 4단계 완주)
        if item.meta.summary:
            item.status = ItemStatus.ENRICHED

        item.user_confirmed = True
        repo.add(item, s)

    print(f"✓ 저장됨  {item.id}")
    print(f"  종류={item.kind.value}/{item.format.value}  상태={item.status.value}")
    print(f"  제목={item.meta.title}")
    if item.meta.tags:
        print(f"  키워드={', '.join(item.meta.tags)}")
    print(f"  번역={'있음' if item.extensions.get('translation') else '없음'}"
          f"  본문={'있음' if item.extensions.get('text') else '없음'}")
    return 0


def cmd_list() -> int:
    repo = FsItemRepository(_archive_root())
    items = sorted(repo.iter_all(), key=lambda i: i.created_at, reverse=True)
    print(f"보관 자료 {len(items)}건:")
    CATLBL = {"trend": "트렌드", "concept": "기본개념", "case": "사례", "opinion": "관점"}
    for it in items:
        tr = "번역O" if it.extensions.get("translation") else "번역X"
        cat = it.meta.category
        cstr = (CATLBL.get(cat, cat) + ("?" if it.meta.category_guess else "")) if cat else "미분류"
        print(f"  [{it.status.value:>9}] {cstr:>6} | {it.kind.value}/{it.format.value}  {tr}  {it.meta.title[:42]}")
    return 0


def cmd_trash(item_id: str) -> int:
    from .domain.ports import ItemNotFound
    repo = FsItemRepository(_archive_root())
    try:
        repo.trash(item_id)
    except ItemNotFound:
        print(f"! 없는 자료: {item_id}")
        return 2
    print(f"✓ 휴지통으로 이동: {item_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "ingest":
        if not rest:
            raise SystemExit("사용: python -m ahkaiving.cli ingest <payload.json | ->")
        return cmd_ingest(rest[0])
    if cmd == "list":
        return cmd_list()
    if cmd == "trash":
        if not rest:
            raise SystemExit("사용: python -m ahkaiving.cli trash <item_id>")
        return cmd_trash(rest[0])
    print(f"모르는 명령: {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
