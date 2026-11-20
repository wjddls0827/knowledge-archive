"""출처 사이트 메타·파비콘 best-effort 스크래핑 — 서버가 직접 받아 로컬 캐시에 쓴다(제3자 안 거침).

app.py 라우팅에서 분리한 네트워크·HTML 파싱 유틸. 아이콘 저장 위치(dest)는 호출자가 넘긴다
(앱의 경로 상수에 의존하지 않도록 — 숨은 결합 제거).
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

_UA = {"User-Agent": "Mozilla/5.0 (AH!kaiving)"}


def ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_site_meta(url: str) -> tuple[str, str]:
    """사이트 HTML을 best-effort로 받아 <title>과 favicon 절대 URL을 뽑는다. (title, icon_url)."""
    title, icon_url = "", ""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx()) as resp:
            html = resp.read(500_000).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
        for lm in re.finditer(r"<link[^>]+>", html, re.I):
            tag = lm.group(0)
            if re.search(r'rel=["\'][^"\']*icon', tag, re.I):
                hm = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
                if hm:
                    icon_url = urljoin(url, hm.group(1))
                    break
    except Exception:
        pass
    if not icon_url:
        p = urlparse(url)
        icon_url = f"{p.scheme}://{p.netloc}/favicon.ico"
    return title, icon_url


def download_icon(icon_url: str, base: str, dest: Path) -> str:
    """icon_url을 받아 dest/<base><ext> 로 저장하고 저장된 파일명을 반환. 실패 시 ''."""
    try:
        req = urllib.request.Request(icon_url, headers=_UA)
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx()) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read(2_000_000)
        if not data:
            return ""
        low = icon_url.lower()
        ext = (".svg" if "svg" in ctype else ".ico" if ("icon" in ctype or low.endswith(".ico"))
               else ".jpg" if "jpeg" in ctype else ".gif" if "gif" in ctype
               else ".webp" if "webp" in ctype else ".png")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{base}{ext}").write_bytes(data)
        return f"{base}{ext}"
    except Exception:
        return ""
