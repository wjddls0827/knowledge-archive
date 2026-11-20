"""FastAPI 앱 — 수직 슬라이스: 업로드 → 원본 보존 → 원문 그대로 서빙.

실행:  cd server && python -m uvicorn ahkaiving.api.app:app --port 8800
데이터 루트:  환경변수 AHK_ARCHIVE (기본 server/data/archive)
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..application.archive_service import ArchiveService
from ..domain.ports import ItemNotFound
from ..infra.deadline import normalize_deadline
from ..infra.fs_repository import FsItemRepository
from ..infra.jobs import prune_jobs as _prune_jobs_impl
from ..infra.registry import SimpleTypeRegistry
from ..infra.site_meta import download_icon as _dl_icon
from ..infra.site_meta import fetch_site_meta as _fetch_meta
from ..infra.sites_store import find_site_node as _find_node
from ..infra.sites_store import iter_site_dicts as _iter_dicts
from ..infra.sites_store import safe_icon as _safe_icon_impl
from ..infra.sites_store import san_node as _san_impl
from ..infra.workspace import FsWorkspace
from ..prompts import load_prompt

SERVER_DIR = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = Path(os.environ.get("AHK_ARCHIVE", SERVER_DIR / "data" / "archive"))
WEB_DIR = SERVER_DIR / "ahkaiving" / "web"

ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
_repo = FsItemRepository(ARCHIVE_ROOT)
_ws = FsWorkspace(ARCHIVE_ROOT)
_ws.cleanup_leftovers()  # 시작 시 남은 staging 정리
_service = ArchiveService(_repo, _ws, SimpleTypeRegistry())

# 자주 가는 사이트(바로가기) — 아카이빙 자료와 분리된 가벼운 북마크 저장소.
SITES_FILE = ARCHIVE_ROOT / "sites.json"
SITES_ICONS = ARCHIVE_ROOT / "sites_assets"
FAVICONS = ARCHIVE_ROOT / "favicons"   # 수집 자료 출처 사이트의 파비콘 캐시(서버가 직접 받아 로컬 제공)

app = FastAPI(title="AH!카이빙", version="0.1.0")


# ── 수집 진행 상황(인메모리·비영속) ───────────────────────────────────
# LLM 토큰과 무관한 가벼운 상태판. /api/collect 가 띄운 작업을 여기 등록하고,
# 대시보드가 /api/jobs 를 폴링해 "수집 중 …"으로 표시한다. 서버 재시작하면 비워진다.
_JOBS: dict[str, dict] = {}
_JOB_TTL = 900  # 초. 15분 지나면 자동 소멸(창을 닫았거나 실패해 콜백이 없는 경우).


def _prune_jobs() -> None:
    """만료·죽은 창·고아 파일 정리 — infra/jobs.prune_jobs 로 위임(상태판 _JOBS·의존성 주입, 동작 동일)."""
    _prune_jobs_impl(_JOBS, SERVER_DIR, _repo, _JOB_TTL)


@app.get("/api/jobs")
def list_jobs() -> JSONResponse:
    _prune_jobs()
    jobs = sorted(_JOBS.values(), key=lambda j: j["started"], reverse=True)
    return JSONResponse(jobs)


@app.delete("/api/jobs/{job_id}")
def dismiss_job(job_id: str) -> JSONResponse:
    _JOBS.pop(job_id, None)
    return JSONResponse({"ok": True})


class StageIn(BaseModel):
    stage: str = ""


@app.post("/api/jobs/{job_id}/stage")
def job_stage(job_id: str, body: StageIn) -> JSONResponse:
    # 수집 창(에이전트)이 현재 단계를 보고 → 대시보드가 진행 상황으로 표시.
    j = _JOBS.get(job_id)
    if j is not None:
        import time as _t
        j["stage"] = (body.stage or "").strip()[:60]
        j["started"] = j.get("started") or _t.time()  # 보고가 오면 살아있음
    return JSONResponse({"ok": True})


def _viewer_type(fmt: str) -> str:
    return {
        "pdf": "pdf",
        "image": "image-gallery",
        "html": "html-sandbox",
        "link": "link",
        "text": "raw",
        "markdown": "raw",
    }.get(fmt, "raw")


def _item_json(item) -> dict:
    d = item.to_meta_dict()
    d["viewer"] = _viewer_type(item.format.value)
    d["kind_label"] = item.kind.value
    return d


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/materials")
def list_materials() -> JSONResponse:
    items = sorted(_repo.iter_all(), key=lambda i: i.created_at, reverse=True)
    return JSONResponse([_item_json(i) for i in items])


@app.get("/api/materials/{item_id}")
def get_material(item_id: str) -> JSONResponse:
    try:
        return JSONResponse(_item_json(_repo.get(item_id)))
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")


@app.post("/api/materials")
async def upload_material(file: UploadFile) -> JSONResponse:
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일")
    result = _service.ingest_upload(file.filename or "file", data)
    body = _item_json(result.item)
    body["duplicate"] = result.duplicate
    return JSONResponse(body, status_code=200 if result.duplicate else 201)


class LinkIn(BaseModel):
    url: str


class EditIn(BaseModel):
    title: str | None = None
    kind: str | None = None                # 문서 분류(문서타입) — paper/news/article/jd/social/forum/video/visual/repo/book/doc
    summary: str | None = None             # 요약
    category: str | None = None            # 내용 분류(trend|concept|case|opinion). 사용자가 지정하면 추정 해제
    freshness: str | None = None           # 최신성 high|mid|low
    tags: list[str] | None = None          # 키워드(검색용)
    notes: str | None = None               # 사용자 메모(에이전트는 쓰지 않음 — 사용자 전용)
    provenance: str | None = None          # 수집 경위(에이전트 자동 메모)
    deadline: str | None = None            # 마감일(주로 채용공고 JD) YYYY-MM-DD — 캘린더가 이 날짜로 표기
    interpretation: str | None = None      # 내 해석(사용자)
    translation: str | None = None         # 한국어 번역(자동/수동)
    glossary: list[dict] | None = None     # 단어장 [{term, meaning}]
    segments: list[dict] | None = None     # 원문↔번역 문단 쌍 [{o, k}] (정렬 리더용)
    memos: list[dict] | None = None        # 사용자 메모 스레드(댓글식) [{id, html, created_at, updated_at?}]
    related: list[dict] | None = None      # 관련 자료 연결 [{id, rel, title}] — 같은 주제의 원 출처·2차 자료


@app.patch("/api/materials/{item_id}")
def edit_material(item_id: str, body: EditIn) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    if body.kind is not None and body.kind.strip():
        from ..domain.model import Kind
        item.kind = Kind(body.kind.strip())
    if body.title is not None:
        item.meta.title = body.title
    if body.summary is not None:
        item.meta.summary = body.summary
    if body.category is not None:
        item.meta.category = (body.category.strip() or None)
        item.meta.category_guess = False  # 사용자가 직접 지정 → 확정
    if body.freshness is not None:
        item.meta.freshness = (body.freshness.strip() or None)
    if body.tags is not None:
        item.meta.tags = [t.strip() for t in body.tags if t.strip()]
    if body.notes is not None:
        item.extensions["notes"] = body.notes
    if body.provenance is not None:
        item.extensions["provenance"] = body.provenance
    if body.deadline is not None:
        val = normalize_deadline(body.deadline)  # 'YYYY-MM-DD' | '상시' | '마감' | None (공용: infra/deadline.py)
        if val:
            item.extensions["deadline"] = val
        else:
            item.extensions.pop("deadline", None)
    if body.interpretation is not None:
        item.extensions["interpretation"] = body.interpretation
    if body.translation is not None:
        item.extensions["translation"] = body.translation
    if body.glossary is not None:
        item.extensions["glossary"] = [
            {"term": g.get("term", ""), "meaning": g.get("meaning", "")}
            for g in body.glossary if g.get("term")
        ]
    if body.segments is not None:
        item.extensions["segments"] = [
            {"o": (s.get("o") or s.get("orig") or ""), "k": (s.get("k") or s.get("ko") or "")}
            for s in body.segments if (s.get("o") or s.get("orig") or s.get("k") or s.get("ko"))
        ]
    if body.memos is not None:
        item.extensions["memos"] = [
            {"id": str(m.get("id") or ""), "html": str(m.get("html") or ""),
             "created_at": str(m.get("created_at") or ""), "updated_at": str(m.get("updated_at") or "")}
            for m in body.memos if (m.get("html") or "").strip()
        ]
    if body.related is not None:
        item.extensions["related"] = [
            {"id": str(r.get("id") or ""), "rel": str(r.get("rel") or "관련"), "title": str(r.get("title") or "")}
            for r in body.related if (r.get("id") or "").strip()
        ]
    # 요약이 채워지면(자동이든 직접이든) '분류·요약' 단계로 전진
    from ..domain.model import ItemStatus
    if (item.meta.summary or "").strip() and item.status in (ItemStatus.PRESERVED, ItemStatus.EXTRACTED):
        item.status = ItemStatus.ENRICHED
    item.user_confirmed = True
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.post("/api/materials/link")
def add_link(body: LinkIn) -> JSONResponse:
    if not body.url.strip():
        raise HTTPException(400, "빈 URL")
    result = _service.ingest_link(body.url)
    out = _item_json(result.item)
    out["duplicate"] = result.duplicate
    return JSONResponse(out, status_code=200 if result.duplicate else 201)


class CollectIn(BaseModel):
    url: str | None = None
    item_id: str | None = None


@app.post("/api/collect")
def collect(body: CollectIn) -> JSONResponse:
    """구조 B: 대시보드의 '수집'이 로컬에서 Claude Code(CLI) 창을 띄워 수집·번역·요약을 맡긴다.

    브라우저는 터미널을 못 열지만, 이 서버는 사용자 PC에서 돌기에 새 콘솔을 띄울 수 있다.
    url=새 링크 수집 / item_id=이미 올린 파일의 번역·요약 보강(둘 다 CLI가 처리).
    """
    url = (body.url or "").strip()
    iid = (body.item_id or "").strip()
    if not url and not iid:
        raise HTTPException(400, "url 또는 item_id가 필요해요")
    if sys.platform != "win32":
        raise HTTPException(400, "CLI 수집 창은 지금 Windows에서만 열려요. 링크/파일을 Claude Code 채팅에서 처리해도 같은 결과예요.")
    if shutil.which("claude") is None:
        raise HTTPException(400, "claude 명령을 찾지 못했어요(PATH). Claude Code CLI 설치 후 다시 시도하세요.")
    import time as _t
    import uuid as _u
    job_id = _u.uuid4().hex[:8]
    stage_hint = (
        f"진행 상황을 사용자가 보도록, 단계가 바뀔 때마다 POST http://127.0.0.1:8800/api/jobs/{job_id}/stage 에 "
        'JSON {"stage":"<현재 단계>"} 를 보내 짧게 보고해줘'
        "(예: '본문 가져오는 중' → '번역 중 (3/10)' → '요약·정리 중' → '저장 중'). "
    )
    if iid:
        # 프롬프트 본문은 외부화: ahkaiving/prompts/collect_enrich.md ({{IID}}·{{STAGE_HINT}} 치환)
        prompt = load_prompt("collect_enrich.md", IID=iid, STAGE_HINT=stage_hint)
        title = "AH!chiving 보강"
    else:
        # 프롬프트 본문은 외부화: ahkaiving/prompts/collect_new.md ({{STAGE_HINT}}·{{URL}} 치환)
        prompt = load_prompt("collect_new.md", STAGE_HINT=stage_hint, URL=url)
        title = "AH!chiving 수집"
    # 수집 1건 = 창 1개. 각 수집을 전용 콘솔에서 claude 1회로 독립 실행 → 다건이 병렬로 진행된다.
    # 창끼리 공유 큐가 없어 '하나만 수집됨'/stuck 같은 레이스가 구조적으로 없다. 창을 닫으면 그 수집만 멈춘다.
    import json as _json
    inbox = SERVER_DIR / "data" / "collect_inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        task = {"job_id": job_id, "title": title, "label": iid or url or "", "prompt": prompt}
        (inbox / f"{job_id}.json").write_text(_json.dumps(task, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"수집 작업을 준비하지 못했어요: {e.__class__.__name__}")
    # 창 제목에 쓸 짧은 라벨(cmd 파싱을 깨는 문자 제거). job_id 는 hex 라 안전.
    _wt = "".join(c for c in (iid or url or "수집") if c not in '"&<>|^%()')[:38]
    try:
        subprocess.Popen(
            f'start "AH!chiving 수집: {_wt}" cmd /c "chcp 65001>nul & python -m ahkaiving.collect_runner {job_id}"',
            shell=True, cwd=str(SERVER_DIR),
        )
    except Exception as e:
        raise HTTPException(500, f"수집 창을 열지 못했어요: {e.__class__.__name__}")
    # 진행 상황판에 등록(대시보드가 "수집 중"으로 표시). 완료되면 /api/jobs 가 자동으로 치움.
    _JOBS[job_id] = {
        "id": job_id,
        "kind": "enrich" if iid else "collect",
        "url": url or None,
        "item_id": iid or None,
        "title": title,
        "label": iid or url or "",
        "stage": "수집 시작",
        "started": _t.time(),
    }
    return JSONResponse({"ok": True, "launched": True, "job": job_id})


@app.post("/api/materials/{item_id}/extract")
def extract_material(item_id: str) -> JSONResponse:
    # 원본에서 본문 텍스트 추출(기존 자료 재처리용). 수집 시엔 자동 수행됨.
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    from ..infra.extract import extract_text
    from ..domain.model import ItemStatus
    text = extract_text(item, _repo)
    if not text.strip():
        raise HTTPException(422, "추출할 텍스트가 없어요 (이미지, 또는 스냅샷 없는 링크)")
    item.extensions["text"] = text  # 원문 전체 보존(잘림 없음)
    if item.status == ItemStatus.PRESERVED:
        item.status = ItemStatus.EXTRACTED
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.post("/api/materials/{item_id}/enrich")
def enrich_material(item_id: str, force: bool = False) -> JSONResponse:
    # ⑤ 분류·요약: 추출 텍스트 → 요약 + 키워드(자동). 잡텍스트 정리.
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    from ..domain.model import ItemStatus
    # 토큰 절약: 이미 요약·분류가 끝났으면 생략. 다시 하려면 ?force=1.
    if not force and item.status == ItemStatus.ENRICHED and (item.meta.summary or "").strip():
        body = _item_json(item)
        body["skipped"] = "이미 분류·요약됨 (다시 하려면 force=1)"
        return JSONResponse(body)
    from ..infra.extract import extract_text
    text = (item.extensions.get("text") or "").strip() or extract_text(item, _repo)
    if not text.strip():
        raise HTTPException(422, "요약할 텍스트가 없어요 (이미지, 또는 스냅샷 없는 링크)")
    try:
        from ..infra.summarizer import summarize_and_tag
        result = summarize_and_tag(text)
    except Exception as e:
        raise HTTPException(503, f"분류·요약은 Claude API 키가 필요해요. 터미널에서 ANTHROPIC_API_KEY 설정 후 서버 재시작하세요. (사유: {e.__class__.__name__})")
    if result.get("title"):
        item.meta.title = result["title"]
    if result["summary"]:
        item.meta.summary = result["summary"]
    # 기존 키워드 + 생성 키워드 병합(중복 제거)
    merged = list(dict.fromkeys((item.meta.tags or []) + result["tags"]))
    item.meta.tags = merged[:12]
    item.status = ItemStatus.ENRICHED
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.post("/api/materials/{item_id}/translate")
def translate_material(item_id: str, force: bool = False) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    # 토큰 절약: 이미 번역이 있으면 재번역(재과금) 생략. 다시 하려면 ?force=1.
    if not force and (item.extensions.get("translation") or "").strip():
        body = _item_json(item)
        body["skipped"] = "이미 번역이 있어요 (재번역하려면 force=1)"
        return JSONResponse(body)
    from ..infra.extract import extract_text
    text = extract_text(item, _repo)
    if not text.strip():
        raise HTTPException(422, "원문 텍스트를 추출할 수 없어요 (이미지·링크 스냅샷 없음은 아직 미지원)")
    try:
        from ..infra.translator import translate_to_korean
        ko = translate_to_korean(text)
    except Exception as e:  # 키·네트워크 없음 등
        raise HTTPException(503, f"번역은 Claude API 키가 필요해요. 터미널에서 ANTHROPIC_API_KEY 설정 후 서버 재시작하세요. (사유: {e.__class__.__name__})")
    if not ko:
        raise HTTPException(502, "번역 결과가 비었어요")
    item.extensions["translation"] = ko
    item.meta.freshness = item.meta.freshness
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.delete("/api/materials/{item_id}")
def delete_material(item_id: str) -> JSONResponse:
    # 영구삭제가 아니라 .trash/ 로 이동(복구 가능).
    try:
        _repo.trash(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    return JSONResponse({"ok": True, "id": item_id})


@app.get("/api/materials/{item_id}/original/{rel_path:path}")
def get_original(item_id: str, rel_path: str) -> FileResponse:
    try:
        fh = _repo.open_file(item_id, f"original/{rel_path}")
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    except (PermissionError, FileNotFoundError):
        raise HTTPException(404, "없는 파일")
    path = Path(fh.name)
    fh.close()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # 원문 그대로 브라우저에 — inline 표시
    return FileResponse(path, media_type=mime, content_disposition_type="inline")


@app.get("/api/materials/{item_id}/derived/{rel_path:path}")
def get_derived(item_id: str, rel_path: str) -> FileResponse:
    # 파생본(표지·페이지 이미지 등). 원본과 달리 재생성 가능한 캐시.
    try:
        fh = _repo.open_file(item_id, f"derived/{rel_path}")
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    except (PermissionError, FileNotFoundError):
        raise HTTPException(404, "없는 파일")
    path = Path(fh.name)
    fh.close()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime, content_disposition_type="inline")


# ── 사용자 첨부자료 ───────────────────────────────────────────────────
# 원문에 안 잡힌 이미지·영상·파일을 사용자가 직접 붙이는 보조 자료.
# 파일은 아이템의 attachments/ 에 저장(원본과 분리·쓰기 가능), 영상/링크는 URL만 기록.
# extensions.attachments = [{id, kind:image|video|audio|file|link, name, path?|url?, mime?, added_at}]

class AttachLinkIn(BaseModel):
    url: str
    title: str | None = None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _attachments(item) -> list:
    return list(item.extensions.get("attachments") or [])


@app.post("/api/materials/{item_id}/attachments")
async def add_attachment(item_id: str, file: UploadFile) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일")
    if len(data) > 200_000_000:  # 200MB 상한
        raise HTTPException(413, "파일이 너무 커요(200MB 초과)")
    from ..application.archive_service import _safe_filename
    import uuid as _uu
    safe = _safe_filename(file.filename or "file")
    rel = _repo.attach_file(item_id, safe, data)
    mime = file.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
    kind = ("image" if mime.startswith("image/") else "video" if mime.startswith("video/")
            else "audio" if mime.startswith("audio/") else "file")
    att = _attachments(item)
    att.append({"id": _uu.uuid4().hex[:8], "kind": kind, "name": file.filename or safe,
                "path": rel, "mime": mime, "added_at": _now_iso()})
    item.extensions["attachments"] = att
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)), status_code=201)


@app.post("/api/materials/{item_id}/attachments/link")
def add_attachment_link(item_id: str, body: AttachLinkIn) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "빈 URL")
    import uuid as _uu
    low = url.lower()
    kind = "video" if ("youtube.com" in low or "youtu.be" in low or "vimeo.com" in low) else "link"
    att = _attachments(item)
    att.append({"id": _uu.uuid4().hex[:8], "kind": kind, "name": (body.title or "").strip() or url,
                "url": url, "added_at": _now_iso()})
    item.extensions["attachments"] = att
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)), status_code=201)


class AttachRenameIn(BaseModel):
    name: str


@app.patch("/api/materials/{item_id}/attachments/{att_id}")
def rename_attachment(item_id: str, att_id: str, body: AttachRenameIn) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "빈 이름")
    att = _attachments(item)
    found = False
    for a in att:
        if a.get("id") == att_id:
            a["name"] = name[:200]  # 표시 이름만 변경(디스크 파일은 그대로)
            found = True
    if not found:
        raise HTTPException(404, "없는 첨부")
    item.extensions["attachments"] = att
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.delete("/api/materials/{item_id}/attachments/{att_id}")
def del_attachment(item_id: str, att_id: str) -> JSONResponse:
    try:
        item = _repo.get(item_id)
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    att = _attachments(item)
    removed = next((a for a in att if a.get("id") == att_id), None)
    if removed is None:
        raise HTTPException(404, "없는 첨부")
    if removed.get("path"):
        try:
            _repo.delete_attachment(item_id, removed["path"])
        except Exception:
            pass  # 파일이 이미 없어도 메타에서는 제거
    item.extensions["attachments"] = [a for a in att if a.get("id") != att_id]
    _repo.update(item)
    return JSONResponse(_item_json(_repo.get(item_id)))


@app.get("/api/materials/{item_id}/attachments/{rel_path:path}")
def get_attachment(item_id: str, rel_path: str) -> FileResponse:
    try:
        fh = _repo.open_file(item_id, f"attachments/{rel_path}")
    except ItemNotFound:
        raise HTTPException(404, "없는 자료")
    except (PermissionError, FileNotFoundError):
        raise HTTPException(404, "없는 파일")
    path = Path(fh.name)
    fh.close()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime, content_disposition_type="inline")


# ── 자주 가는 사이트(바로가기) ────────────────────────────────────────
def _sites_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _load_sites() -> list:
    try:
        d = json.loads(SITES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_sites(sites: list) -> None:
    SITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SITES_FILE.with_name("sites.json.tmp")
    tmp.write_text(json.dumps(sites, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SITES_FILE)


def _fetch_site_meta(url: str):
    """사이트 <title>·favicon URL 추출 — infra/site_meta.fetch_site_meta 로 위임(동작 동일)."""
    return _fetch_meta(url)


def _download_icon(icon_url: str, base: str, dest: Path | None = None) -> str:
    """아이콘 받아 저장 — infra/site_meta.download_icon 로 위임. dest 기본값은 SITES_ICONS."""
    return _dl_icon(icon_url, base, dest or SITES_ICONS)


@app.get("/api/favicon")
def get_favicon(domain: str = "") -> FileResponse:
    """출처 사이트의 파비콘을 서버가 직접 받아 캐시하고 로컬로 돌려준다(제3자 안 거침).
    없으면 404 → 프론트가 기본 아이콘으로 폴백. 실패는 '.none' 음성캐시로 반복 요청 억제."""
    host = re.sub(r"[^a-z0-9.-]", "", (domain or "").strip().lower())[:80]
    if not host or "." not in host or host.startswith(".") or ".." in host:
        raise HTTPException(404, "bad domain")
    FAVICONS.mkdir(parents=True, exist_ok=True)
    # 캐시 조회(확장자 후보를 정확히 확인 — glob 충돌 방지)
    for ext in (".ico", ".png", ".svg", ".jpg", ".gif", ".webp"):
        f = FAVICONS / f"{host}{ext}"
        if f.is_file() and f.stat().st_size > 0:
            return FileResponse(str(f), media_type=mimetypes.guess_type(f.name)[0] or "image/x-icon")
    if (FAVICONS / f"{host}.none").exists():
        raise HTTPException(404, "no favicon (cached)")
    # 미스 → 사이트에서 직접 받아 캐시
    _, icon_url = _fetch_site_meta(f"https://{host}/")
    saved = _download_icon(icon_url, host, FAVICONS)
    if not saved:
        try:
            (FAVICONS / f"{host}.none").write_bytes(b"")
        except Exception:
            pass
        raise HTTPException(404, "no favicon")
    f = FAVICONS / saved
    return FileResponse(str(f), media_type=mimetypes.guess_type(f.name)[0] or "image/x-icon")


class SiteIn(BaseModel):
    url: str | None = None
    title: str | None = None


def _iter_site_dicts(nodes):
    """infra/sites_store.iter_site_dicts 로 위임(동작 동일)."""
    return _iter_dicts(nodes)


def _safe_icon(name: str) -> str:
    """infra/sites_store.safe_icon 로 위임(경로 탈출 아이콘명 거부)."""
    return _safe_icon_impl(name)


def _san_node(n: dict):
    """infra/sites_store.san_node 로 위임(클라이언트 입력 정규화)."""
    return _san_impl(n)


@app.get("/api/sites")
def list_sites() -> JSONResponse:
    return JSONResponse(_load_sites())


@app.post("/api/sites")
def add_site(body: SiteIn) -> JSONResponse:
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "빈 URL")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    import uuid as _uu
    sid = _uu.uuid4().hex[:8]
    meta_title, icon_url = _fetch_site_meta(url)
    title = (body.title or "").strip() or meta_title or urlparse(url).netloc
    icon = _download_icon(icon_url, sid)
    sites = _load_sites()
    sites.append({"id": sid, "kind": "site", "url": url, "title": title[:120],
                  "icon": icon, "added_at": _now_iso()})
    _save_sites(sites)
    return JSONResponse(sites, status_code=201)


@app.put("/api/sites")
def replace_sites(body: list = Body(...)) -> JSONResponse:
    """드래그 재정렬·폴더 묶기 결과(전체 구조)를 통째로 저장. 클라이언트가 트리를 관리한다."""
    nodes = [_san_node(n) for n in (body or [])]
    _save_sites([n for n in nodes if n])
    return JSONResponse(_load_sites())


@app.patch("/api/sites/{sid}")
def edit_site(sid: str, body: SiteIn) -> JSONResponse:
    sites = _load_sites()
    found = [False]

    def walk(nodes):
        for n in nodes:
            if n.get("id") == sid:
                if body.title is not None:
                    n["title"] = body.title.strip()[:120]
                if body.url and body.url.strip() and n.get("kind") != "folder":
                    u = body.url.strip()
                    if not u.startswith(("http://", "https://")):
                        u = "https://" + u
                    n["url"] = u
                found[0] = True
            if n.get("kind") == "folder":
                walk(n.get("items") or [])
    walk(sites)
    if not found[0]:
        raise HTTPException(404, "없는 사이트")
    _save_sites(sites)
    return JSONResponse(sites)


@app.delete("/api/sites/{sid}")
def del_site(sid: str) -> JSONResponse:
    sites = _load_sites()
    removed = [None]

    def rm(nodes):
        out = []
        for n in nodes:
            if n.get("id") == sid:
                removed[0] = n
                continue
            if n.get("kind") == "folder":
                n["items"] = rm(n.get("items") or [])
            out.append(n)
        return out
    new = rm(sites)
    if removed[0] is None:
        raise HTTPException(404, "없는 사이트")
    for s in _iter_site_dicts([removed[0]]):  # 폴더면 안의 사이트들, 사이트면 자기 자신
        if s.get("icon"):
            try:
                (SITES_ICONS / _safe_icon(s["icon"])).unlink()
            except Exception:
                pass
    _save_sites(new)
    return JSONResponse(new)


@app.get("/api/sites/{sid}/icon")
def site_icon(sid: str) -> FileResponse:
    s = next((x for x in _iter_site_dicts(_load_sites()) if x.get("id") == sid), None)
    icon = _safe_icon(s.get("icon")) if s else ""
    if not icon:
        raise HTTPException(404, "없음")
    path = SITES_ICONS / icon
    if not path.is_file():
        raise HTTPException(404, "없음")
    mime = mimetypes.guess_type(path.name)[0] or "image/x-icon"
    return FileResponse(path, media_type=mime, content_disposition_type="inline")


def _find_site_node(sid: str, sites: list):
    """infra/sites_store.find_site_node 로 위임(id로 노드 찾기)."""
    return _find_node(sid, sites)


def _set_icon_bytes(node: dict, data: bytes, mime: str, ext_hint: str = "") -> None:
    import uuid as _uu
    low = (mime or "").lower()
    ext = (".svg" if "svg" in low else ".png" if "png" in low else ".jpg" if "jpeg" in low
           else ".gif" if "gif" in low else ".webp" if "webp" in low
           else (ext_hint if ext_hint.startswith(".") else ".png"))
    SITES_ICONS.mkdir(parents=True, exist_ok=True)
    name = f"{node['id']}-{_uu.uuid4().hex[:4]}{ext}"
    (SITES_ICONS / name).write_bytes(data)
    old = _safe_icon(node.get("icon"))
    node["icon"] = name
    if old and old != name:
        try:
            (SITES_ICONS / old).unlink()
        except Exception:
            pass


@app.post("/api/sites/{sid}/icon")
async def upload_site_icon(sid: str, file: UploadFile) -> JSONResponse:
    sites = _load_sites()
    node = _find_site_node(sid, sites)
    if node is None:
        raise HTTPException(404, "없는 사이트")
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일")
    if len(data) > 5_000_000:
        raise HTTPException(413, "이미지가 너무 커요(5MB 초과)")
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    _set_icon_bytes(node, data, mime)
    _save_sites(sites)
    return JSONResponse(sites)


@app.post("/api/sites/{sid}/icon/refetch")
def refetch_site_icon(sid: str) -> JSONResponse:
    sites = _load_sites()
    node = _find_site_node(sid, sites)
    if node is None:
        raise HTTPException(404, "없는 사이트")
    _, icon_url = _fetch_site_meta(node.get("url") or "")
    import uuid as _uu
    icon = _download_icon(icon_url, f"{sid}-{_uu.uuid4().hex[:4]}")
    if icon:
        old = _safe_icon(node.get("icon"))
        node["icon"] = icon
        _save_sites(sites)
        if old and old != icon:
            try:
                (SITES_ICONS / old).unlink()
            except Exception:
                pass
    return JSONResponse(sites)
