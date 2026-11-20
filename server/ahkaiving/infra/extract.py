"""원문 텍스트 추출 — 번역·검색·요약의 입력. 형식별 추출(설계서 ⑤ 처리 워커의 일부).

PDF=PyMuPDF. HTML=OpenGraph 메타(og:title/description) 우선 + 본문(잡영역 제거).
소셜(LinkedIn·X·Threads 등)은 og:description에 본문이 깨끗이 들어있어 이를 앞세운다.
이미지·링크(스냅샷 없음)는 미지원(빈 문자열).
"""
from __future__ import annotations

import html as _html
import re


def _meta(html_text: str, prop: str) -> str:
    """<meta property|name="og:xxx" content="..."> 에서 내용 추출(속성 순서 무관)."""
    p = re.escape(prop)
    for pat in (
        rf'<meta[^>]+(?:property|name)=["\']{p}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{p}["\']',
    ):
        m = re.search(pat, html_text, re.I)
        if m:
            return _html.unescape(m.group(1)).strip()
    return ""


def _strip_html(raw: str) -> str:
    # 본문 아닌 영역 통째 제거(스크립트·헤더·푸터·내비·폼 등 = 잡텍스트 원천)
    # 수집 제외 영역(보일러플레이트): 스크립트·스타일·네비·헤더·푸터·사이드바·폼·메뉴·대화상자 등
    raw = re.sub(
        r"(?is)<(script|style|head|nav|footer|header|aside|form|button|svg|noscript|menu|dialog)[^>]*>.*?</\1>",
        " ",
        raw,
    )
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = _html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
    return raw.strip()


def html_meta(text: str) -> dict:
    """구조화 메타 추출 — 제목·설명·작성자·날짜·대표이미지·사이트명(수집 스펙의 필드)."""
    return {
        "title": _meta(text, "og:title") or _meta(text, "twitter:title"),
        "description": _meta(text, "og:description"),
        "author": _meta(text, "author") or _meta(text, "article:author") or _meta(text, "og:article:author"),
        "date": _meta(text, "article:published_time") or _meta(text, "og:article:published_time") or _meta(text, "datePublished"),
        "image": _meta(text, "og:image") or _meta(text, "twitter:image"),
        "site": _meta(text, "og:site_name"),
    }


def _extract_html(text: str) -> str:
    og_title = _meta(text, "og:title")
    og_desc = _meta(text, "og:description")
    body = _strip_html(text)
    head = []
    if og_title and og_title[:40] not in body[:200]:
        head.append(og_title)
    # og:description(소셜 본문)이 본문 앞부분에 없으면 앞세운다.
    if og_desc and og_desc[:60] not in body[:1500]:
        head.append(og_desc)
    return ("\n\n".join(head) + "\n\n" + body).strip() if head else body


def extract_from_bytes(data: bytes, fmt: str, name: str = "") -> str:
    if not data:
        return ""
    if fmt == "pdf":
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc).strip()
    text = data.decode("utf-8", "ignore")
    if fmt == "html" or name.endswith(".html"):
        return _extract_html(text)
    if fmt in ("text", "markdown", "csv", "json") or name.rsplit(".", 1)[-1] in ("txt", "md", "csv", "json"):
        return text.strip()
    return ""  # 모르는 형식(이미지 등)은 추출 안 함


def extract_text(item, repo) -> str:
    if not item.original_files:
        return ""
    rel = item.original_files[0]
    try:
        with repo.open_file(item.id, rel) as f:
            data = f.read()
    except (FileNotFoundError, PermissionError):
        return ""
    return extract_from_bytes(data, item.format.value, rel)


def guess_lang(text: str) -> str:
    """아주 단순한 추정 — 한글이 있으면 ko, 아니면 en(=번역 대상)."""
    if re.search(r"[가-힣]", text):
        return "ko"
    return "en"
