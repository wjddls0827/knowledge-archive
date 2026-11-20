"""채용공고(JD) 마감일 문자열 정규화 — app.py · cli.py 공용(단일 진실).

반환값은 세 가지 중 하나 또는 None:
  'YYYY-MM-DD'  날짜 마감
  '상시'        상시·수시채용(날짜 없음)
  '마감'        수동 종료(마감/종료 표시)
  None          불명/없음
"""
from __future__ import annotations

import re

_CLOSED = re.compile(r"^\s*(마감|종료|마감됨|closed)\s*$", re.I)
_ROLLING = re.compile(r"상시|수시|채용\s*시|마감\s*시|rolling|until\s*filled", re.I)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_deadline(raw: object) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if _CLOSED.fullmatch(s):
        return "마감"
    if _ROLLING.search(s):
        return "상시"
    m = _DATE.search(s)
    return m.group(0) if m else None
