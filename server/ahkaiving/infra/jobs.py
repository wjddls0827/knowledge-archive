"""수집 작업(인메모리) 정리 로직 — app.py 라우팅에서 분리.

진행 상태판(_JOBS dict) 자체는 app에 두고, '만료·죽은 수집창·고아 파일' 정리 로직만 여기로 옮긴다.
app 전역(SERVER_DIR·repo·ttl)에 숨어 의존하지 않도록 전부 인자로 받는다.
"""
from __future__ import annotations

import time
from pathlib import Path


def beat_fresh(server_dir: Path, jid: str, now: float | None = None) -> bool:
    """수집 창(러너)이 beat 파일에 최근 시각을 쓰고 있으면 True(그 창이 살아서 수집 중)."""
    now = time.time() if now is None else now
    bp = server_dir / "data" / "collect_beats" / jid
    try:
        return bp.exists() and (now - float((bp.read_text(encoding="utf-8").strip() or "0"))) < 25
    except Exception:
        return False


def prune_jobs(jobs: dict, server_dir: Path, repo, ttl: int) -> None:
    """만료(ttl 초과)·완료(같은 URL 자료 생김)·죽은 창(beat 끊김) 작업을 치우고, 고아 inbox/beat 파일 정리."""
    from ..domain.model import SourceRef as _SR
    now = time.time()
    beats = server_dir / "data" / "collect_beats"
    for jid in list(jobs):
        j = jobs[jid]
        if now - j["started"] > ttl:
            jobs.pop(jid, None)
            continue
        # 새 URL 수집: 같은 URL 자료가 생겼으면 완료로 보고 치운다.
        if j.get("url") and repo.find_by_source(_SR(url=j["url"])) is not None:
            jobs.pop(jid, None)
            continue
        # 이 수집 창이 죽었으면(창 닫힘/종료) 해당 잡만 정리. spawn 직후 몇 초는 유예(오탐 방지).
        if now - j["started"] > 12 and not beat_fresh(server_dir, jid, now):
            jobs.pop(jid, None)
    # 창이 강제 종료돼 러너가 못 지운 고아 작업·beat 파일 정리(살아있는 창 것은 건드리지 않음).
    try:
        inbox = server_dir / "data" / "collect_inbox"
        for f in inbox.glob("*.json"):
            if f.stem not in jobs and now - f.stat().st_mtime > 30 and not beat_fresh(server_dir, f.stem, now):
                f.unlink()
        if beats.exists():
            for f in beats.iterdir():
                if f.name not in jobs and not beat_fresh(server_dir, f.name, now) and now - f.stat().st_mtime > 30:
                    f.unlink()
    except Exception:
        pass
