"""수집 러너 (창 1개 = 작업 1개) — /api/collect 가 수집마다 이 러너를 전용 창으로 띄운다.

왜: 큐를 한 창에서 순차로 돌리면 다건이 느리고, 여러 워커가 같은 인박스를 두고
   경쟁하면 '하나만 수집됨' 같은 레이스가 난다. 대신 수집마다 독립된 창에서 claude 를
   1회 실행하고, 끝나면 창이 닫힌다. 창끼리 공유 상태가 없다.

- 작업: data/collect_inbox/<job_id>.json (프롬프트 carrier; 이 창은 자기 job_id 파일만 읽는다)
- 생존신호: data/collect_beats/<job_id> 에 현재 시각을 계속 써서 '이 수집 창이 살아있음'을 알린다.
  창을 닫으면 beat 가 멎고, 서버 _prune_jobs 가 ~25초 내 해당 잡을 치운다.
- claude 가 끝나면(성공/실패 무관) 작업파일과 beat 파일을 지우고 종료 → 창이 닫힌다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
DATA = SERVER_DIR / "data"
INBOX = DATA / "collect_inbox"
BEATS = DATA / "collect_beats"


def _run(job_id: str) -> None:
    task_path = INBOX / f"{job_id}.json"
    beat_path = BEATS / job_id
    BEATS.mkdir(parents=True, exist_ok=True)

    def beat() -> None:
        try:
            beat_path.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass

    beat()
    try:
        data = json.loads(task_path.read_text(encoding="utf-8"))
    except Exception:
        print("작업 파일을 읽지 못했어요. 이 창은 닫아도 됩니다.", flush=True)
        return
    prompt = data.get("prompt") or ""
    title = data.get("title") or "AH!chiving 수집"
    label = data.get("label") or job_id
    print("=" * 64)
    print(f"▶ {title}  —  {label}")
    print("=" * 64, flush=True)
    if not prompt.strip():
        return
    # 기존 /api/collect 와 동일한 이스케이프로 임시 .cmd 를 만들어 이 창에서 실행.
    # claude 가 도는 동안에도 beat 를 계속 써서 서버가 '이 수집 창 살아있음'을 안다.
    prompt_cmd = prompt.replace("%", "%%").replace('"', "'")
    fd, path = tempfile.mkstemp(suffix=".cmd", prefix="ahk_task_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("@echo off\r\nchcp 65001 >nul\r\n")
            f.write(f"title {title}\r\n")
            f.write(f'cd /d "{SERVER_DIR}"\r\n')
            f.write(f'claude "{prompt_cmd}"\r\n')
        p = subprocess.Popen(["cmd", "/c", path])
        while p.poll() is None:
            beat()
            time.sleep(2)
        beat()
    except FileNotFoundError:
        print("claude 명령을 찾지 못했어요(PATH). Claude Code CLI 설치 후 다시 시도하세요.", flush=True)
    except Exception as e:
        print(f"수집 중 오류({e.__class__.__name__}) — 이 창은 닫아도 됩니다.", flush=True)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def main() -> None:
    if len(sys.argv) < 2:
        print("job_id 인자가 필요해요.", flush=True)
        return
    job_id = sys.argv[1]
    try:
        _run(job_id)
    finally:
        # 작업·생존신호 정리 → 서버가 '이 수집은 끝났음'을 안다(잡바에서 사라짐).
        for p in (INBOX / f"{job_id}.json", BEATS / job_id):
            try:
                p.unlink()
            except Exception:
                pass
    print("\n수집 완료. 이 창은 닫아도 됩니다.", flush=True)


if __name__ == "__main__":
    try:
        if os.name == "nt":
            os.system("chcp 65001 >nul")
    except Exception:
        pass
    main()
