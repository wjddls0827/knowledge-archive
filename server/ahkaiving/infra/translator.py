"""번역기 — 원문을 한국어로. Anthropic Claude API(공식 SDK).

모델: 환경변수 AHK_TRANSLATE_MODEL (기본 claude-haiku-5-5 — 경량/저비용).
      품질이 더 필요하면 AHK_TRANSLATE_MODEL=claude-opus-5-5 로 올린다.
인증: ANTHROPIC_API_KEY 또는 `ant auth login` 프로필을 SDK가 자동 사용.
네트워크/키가 없으면 예외 → API가 503으로 돌려줌.
"""
from __future__ import annotations

import os

MODEL = os.environ.get("AHK_TRANSLATE_MODEL", "claude-haiku-5-5")
_MAX_CHARS = 40000  # 1회 번역 상한(초과분은 잘라서 — 긴 문서 분할은 추후)

_PROMPT = (
    "다음 글을 자연스럽고 정확한 한국어로 번역해줘. "
    "번역문만 출력하고 설명·머리말·코드펜스는 넣지 마. 원문의 문단 구분은 유지해.\n\n"
    "---- 원문 ----\n"
)


def translate_to_korean(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    from anthropic import Anthropic

    client = Anthropic()  # 키/프로필 자동 해석
    # 긴 출력 대비 스트리밍 + 낮은 effort(번역은 단순 작업)
    with client.messages.stream(
        model=MODEL,
        max_tokens=64000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": _PROMPT + text[:_MAX_CHARS]}],
    ) as stream:
        msg = stream.get_final_message()
    return "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    ).strip()
