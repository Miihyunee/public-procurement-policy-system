"""사용자 매뉴얼(``docs/USER_MANUAL.md``) → 프로그램이 내려주는 PDF.

만들어지는 것::

    src/procurement/web/manual/user_manual.pdf

프로그램 안에서 **사용자 매뉴얼 내려받기** 를 누르면 백엔드가 그 파일을 그대로
보낸다(:mod:`procurement.web.manual`). 그래서 매뉴얼을 고칠 때마다 이 스크립트를
다시 돌려야 화면에서 받는 PDF 도 같이 바뀐다.

실행::

    python scripts/build_manual_pdf.py

.. note::
    **런타임 의존성이 아니다.** 아래 두 가지는 이 스크립트에만 필요하며
    ``requirements.txt`` 에 넣지 않는다 — 프로그램은 완성된 PDF만 읽는다.

      · ``markdown`` (``pip install markdown``)
      · Chromium/Chrome 실행파일 — ``--print-to-pdf`` 로 인쇄한다.
        ``CHROME`` 환경변수로 경로를 알려 주거나, 아래 후보 자리에 있어야 한다.

.. warning::
    ⛔ 매뉴얼 **내용을 이 스크립트가 만들지 않는다.** 문장·표·화면 캡처는
    ``docs/USER_MANUAL.md`` 와 ``docs/images/`` 가 원본이고, 여기서는 인쇄
    모양(A4·쪽 나눔·표 테두리)만 입힌다.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: 저장소 루트. 이 스크립트는 ``scripts/`` 안에 있다.
ROOT = Path(__file__).resolve().parent.parent

#: 원본 마크다운.
SOURCE = ROOT / "docs" / "USER_MANUAL.md"

#: 프로그램이 내려주는 PDF 의 자리.
#: ⚠️ 파일명은 **ASCII** 로 둔다 — 패키징 도구가 다루는 경로이기 때문이다.
#:    고객이 받는 한글 파일명은 응답 헤더에서 정한다.
TARGET = ROOT / "src" / "procurement" / "web" / "manual" / "user_manual.pdf"

#: Chromium 후보 자리. ``CHROME`` 환경변수가 있으면 그것을 먼저 쓴다.
CHROME_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
)

#: 인쇄 모양. ⛔ 화면(``static/index.html``)의 색 체계와는 별개다 — 종이는
#:    흰 바탕에 검은 글씨로 읽는다.
PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Malgun Gothic", "맑은 고딕", "Noto Sans KR", sans-serif;
  font-size: 10.5pt; line-height: 1.75; color: #1a1a1a; margin: 0;
}
h1 {
  font-size: 19pt; color: #1F3864; border-bottom: 3px solid #1F3864;
  padding-bottom: 7px; margin: 30px 0 18px; page-break-before: always;
  page-break-after: avoid;
}
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 14pt; color: #1F3864; margin: 26px 0 10px; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #2E5395; margin: 20px 0 8px; page-break-after: avoid; }
p { margin: 8px 0; }
table {
  border-collapse: collapse; width: 100%; margin: 12px 0;
  font-size: 9.8pt; page-break-inside: avoid;
}
th {
  background: #1F3864; color: #fff; text-align: left;
  padding: 7px 9px; border: 1px solid #1F3864; font-weight: 600;
}
td { padding: 6px 9px; border: 1px solid #C9D2E3; vertical-align: top; }
tr:nth-child(even) td { background: #F5F8FC; }
blockquote {
  margin: 12px 0; padding: 11px 15px; background: #F0F4FA;
  border-left: 4px solid #2E5395; page-break-inside: avoid;
}
blockquote p { margin: 5px 0; }
blockquote table { margin: 8px 0; background: #fff; }
code {
  background: #EDF1F7; padding: 1.5px 5px; border-radius: 3px;
  font-family: Consolas, "D2Coding", monospace; font-size: 9.5pt;
}
pre {
  background: #F5F8FC; border: 1px solid #C9D2E3; border-left: 4px solid #1F3864;
  padding: 12px 14px; overflow-x: auto; page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 9.5pt; line-height: 1.55; }
figure { margin: 14px 0; page-break-inside: avoid; text-align: center; }
img { max-width: 100%; border: 1px solid #C9D2E3; border-radius: 4px; }
ul, ol { margin: 8px 0; padding-left: 22px; }
li { margin: 4px 0; }
hr { border: none; border-top: 1px solid #D8DEE9; margin: 22px 0; }
a { color: #2E5395; text-decoration: none; }
div[align="center"] { text-align: center; }
div[align="center"] h1 {
  border: none; page-break-before: avoid; font-size: 26pt; margin-top: 60px;
}
div[align="center"] h2 { font-size: 17pt; color: #2E5395; }
"""


def find_chrome() -> str:
    """Chromium 실행파일을 찾습니다.

    Returns:
        실행 가능한 경로.

    Raises:
        SystemExit: 어느 후보도 없는 경우.
    """
    named = os.environ.get("CHROME")
    candidates = (named, *CHROME_CANDIDATES) if named else CHROME_CANDIDATES
    for candidate in candidates:
        found = candidate if Path(candidate).is_file() else shutil.which(candidate)
        if found:
            return found
    raise SystemExit("Chromium 을 찾지 못했습니다. CHROME 환경변수로 경로를 알려 주세요.")


def build_html(source: Path) -> str:
    """마크다운을 인쇄용 단일 HTML 로 바꿉니다.

    화면 캡처는 ``data:`` URI 로 **문서 안에 심습니다.** 그래야 임시 폴더에서
    인쇄해도 그림이 빠지지 않습니다.

    Args:
        source: 매뉴얼 마크다운 경로.

    Returns:
        완성된 HTML 문서 문자열.
    """
    import markdown  # 이 스크립트에만 필요한 의존성이라 여기서 부른다.

    def embed(match: re.Match[str]) -> str:
        alt, relative = match.group(1), match.group(2)
        image = source.parent / relative
        if not image.exists():
            return match.group(0)
        data = base64.b64encode(image.read_bytes()).decode()
        return f'<figure><img alt="{alt}" src="data:image/png;base64,{data}"></figure>'

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", embed, source.read_text(encoding="utf-8"))
    body = markdown.markdown(
        text,
        extensions=["tables", "toc", "sane_lists", "fenced_code", "md_in_html"],
    )
    return (
        "<!doctype html><html lang=ko><head><meta charset=utf-8>"
        "<title>공공구매정책관리시스템 사용자 매뉴얼</title>"
        f"<style>{PRINT_CSS}</style></head><body>{body}</body></html>"
    )


def main() -> int:
    """매뉴얼 PDF 를 다시 만듭니다.

    Returns:
        종료 코드. 0 이면 성공.
    """
    if not SOURCE.exists():
        print(f"[오류] 매뉴얼 원본이 없습니다: {SOURCE}", file=sys.stderr)
        return 1

    chrome = find_chrome()
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workspace:
        page = Path(workspace) / "manual.html"
        page.write_text(build_html(SOURCE), encoding="utf-8")
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                f"--user-data-dir={workspace}/profile",
                "--no-pdf-header-footer",
                f"--print-to-pdf={TARGET}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )

    size = TARGET.stat().st_size
    print(f"[PDF] {TARGET.relative_to(ROOT)}  ({size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
