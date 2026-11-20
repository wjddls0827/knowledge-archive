"""분류·요약 — 추출 텍스트에서 핵심 요약 + 키워드. Anthropic Claude API.

잡텍스트(로그인·메뉴·푸터)는 무시하고 본문 핵심만. 설계서 ⑤ Enricher 의 최소 구현.
모델: 환경변수 AHK_SUMMARY_MODEL (기본 claude-haiku-5-5 — 경량/저비용).
      품질이 더 필요하면 AHK_SUMMARY_MODEL=claude-opus-5-5 로 올린다.
"""
from __future__ import annotations

import json
import os
import re

MODEL = os.environ.get("AHK_SUMMARY_MODEL", "claude-haiku-5-5")

_PROMPT = (
    "다음 자료를 분석해서 JSON만 출력해줘(설명·코드펜스 금지). "
    '형식: {"title": "20자 내외 핵심 제목", "summary": "<아래 구조의 HTML 문자열>", '
    '"keywords": ["핵심 키워드 5~8개(한글/영문 가능)"]}. '
    "summary는 '핵심→근거→함의' 3단 구조의 HTML 문자열로, 이 틀을 정확히 지켜: "
    "<p><b>한 줄 요약</b><br>이 자료가 말하는 핵심을 딱 한 문장으로.</p>"
    "<p><b>핵심 포인트</b></p><ul><li>…</li><li>…</li></ul> "
    "(핵심 포인트는 2~4개 불릿, 주요 주장·발견·논점을 사실 중심으로.) "
    "<p><b>왜 중요한가</b><br>시사점·함의를 1~2문장으로.</p> "
    "로그인·메뉴·쿠키·'Report this post' 같은 잡텍스트는 무시하고 본문 핵심만. "
    "근거 없는 단정·과장 금지, 원문을 왜곡하지 말 것.\n\n---- 자료 ----\n"
)


def summarize_and_tag(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"summary": "", "tags": []}
    from anthropic import Anthropic

    client = Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=2000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": _PROMPT + text[:30000]}],
    ) as stream:
        msg = stream.get_final_message()
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0) if m else raw)
    tags = [str(t).strip() for t in (data.get("keywords") or []) if str(t).strip()]
    return {
        "title": (data.get("title") or "").strip()[:80],
        "summary": (data.get("summary") or "").strip(),
        "tags": tags[:10],
    }
