import shutil
import tempfile
import unittest
from pathlib import Path

from ahkaiving.infra.site_meta import download_icon, fetch_site_meta


class SiteMetaDefensiveTest(unittest.TestCase):
    """네트워크 없는 '방어 경로'만 검증(해피패스는 /api/favicon 통합 스모크로 확인).
    잘못된 URL이면 예외를 삼키고 안전한 기본값/빈 문자열을 돌려줘야 한다."""

    def test_fetch_bad_url_returns_empty_title_and_fallback_icon(self):
        title, icon = fetch_site_meta("not-a-real-url")  # scheme 없음 → urlopen 즉시 실패
        self.assertEqual(title, "")
        self.assertTrue(icon.endswith("/favicon.ico"))   # 폴백 파비콘 경로

    def test_download_bad_url_returns_empty(self):
        tmp = Path(tempfile.mkdtemp(prefix="ahk_icon_"))
        try:
            self.assertEqual(download_icon("not-a-url", "base", tmp), "")
            # 실패 시 파일을 만들지 않아야
            self.assertEqual(list(tmp.glob("base*")), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
