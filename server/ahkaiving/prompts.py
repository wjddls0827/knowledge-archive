"""수집 프롬프트 외부화 — app.py의 collect()가 쓰는 프롬프트 템플릿 로더.

프롬프트 본문은 `prompts/*.md` 파일에 두고 코드와 분리한다(실험·버전관리 쉽게).
값 주입은 `{{TOKEN}}` 치환 — 프롬프트 안의 JSON 중괄호 `{...}` 와 충돌하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str, **tokens: str) -> str:
    text = (_DIR / name).read_text(encoding="utf-8").strip()
    for key, val in tokens.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text
