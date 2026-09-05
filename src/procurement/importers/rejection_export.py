"""
procurement.importers.rejection_export

적재되지 않은 원본 행을 **CSV 로 내보냅니다** — 담당자가 원본 엑셀과 직접
대조하도록.

.. warning::
    ⛔ **"제외 목록" 이 아닙니다.** 원본에는 있으나 현재 검토 대상에 포함되지
    않은 행일 뿐이며, 어떻게 처리할지는 고객 확인 사항입니다(Q5-8). 열 이름과
    사유 표기에 확정 표현을 쓰지 않습니다.

.. note::
    **규약은 검토 이력 CSV(**:mod:`procurement.reviews.export`\\ **)와 같습니다.**
    UTF-8 BOM · CRLF · 수식 인젝션 방어 · generator 스트리밍. 담당자가 두 파일을
    같은 방식으로 열 수 있어야 하기 때문입니다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from procurement.models.import_rejection import ImportRejection

#: 열 순서. **고정**입니다.
#:
#: 앞쪽 여덟 열은 원본 엑셀(``삭제표기`` 시트)의 컬럼 순서를 그대로 따릅니다 —
#: 담당자가 두 파일을 나란히 놓고 눈으로 대조하기 때문입니다. 사유는 뒤에
#: 붙입니다.
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "원본 행 번호",
    "신고기준일",
    "적요",
    "거래처명",
    "사업자번호",
    "금액",
    "결의일자",
    "예산과목",
    "미적재 사유 코드",
    "미적재 사유",
    "원문 메시지",
    "업로드 배치 ID",
)

#: 값이 없을 때 넣는 문자열.
EMPTY: Final = ""

#: 엑셀이 수식으로 해석하는 시작 문자들.
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: str) -> str:
    """수식으로 해석될 수 있는 값을 문자열로 고정합니다.

    ⚠️ **금액 열에는 쓰지 않습니다.** 음수 금액(``-1841700``)은 ``-`` 로
    시작하지만 숫자로 읽혀야 하고, 여기에 따옴표를 붙이면 엑셀에서 문자열이
    되어 합계를 낼 수 없습니다.

    Examples:
        >>> _safe("=1+1")
        "'=1+1"
        >>> _safe("1월 임대료")
        '1월 임대료'
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _text(value: object) -> str:
    """자유 입력 값을 CSV 한 칸으로 만듭니다(수식 방어 포함)."""
    if value is None:
        return EMPTY
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return _safe(str(value))


def _number(value: object) -> str:
    """숫자 칸. **지수 표기 없이** 그대로 쓰고, 음수 부호를 보존합니다."""
    if value is None:
        return EMPTY
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def export_row(rejection: ImportRejection) -> tuple[str, ...]:
    """미적재 행 하나를 CSV 한 줄로 만듭니다.

    Args:
        rejection: 미적재 기록.

    Returns:
        :data:`EXPORT_COLUMNS` 와 **같은 순서**의 문자열 묶음.
    """
    return (
        _number(rejection.row_number),
        _text(rejection.issue_date),
        _text(rejection.description),
        _text(rejection.company_name),
        # 사업자번호는 원본 표기를 그대로 둡니다 — 하이픈을 넣거나 빼지 않습니다.
        _text(rejection.business_no),
        _number(rejection.amount),
        _text(rejection.resolution_date),
        _text(rejection.budget_account),
        _text(rejection.reason),
        _text(rejection.reason_label),
        # 줄바꿈이 들어 있어도 csv 모듈이 따옴표로 감쌉니다.
        _text(rejection.message),
        _number(rejection.batch_id),
    )


def export_lines(rejections: Iterable[ImportRejection]) -> Iterator[str]:
    """CSV 를 **한 줄씩** 흘려보냅니다.

    전체를 메모리에 쌓지 않으므로 건수가 늘어도 사용량이 일정합니다. 첫 줄은
    UTF-8 BOM 이 붙은 머리글입니다.

    Args:
        rejections: 내보낼 미적재 기록들.

    Yields:
        줄바꿈이 포함된 CSV 한 줄.
    """
    yield "﻿" + _line(EXPORT_COLUMNS)
    for rejection in rejections:
        yield _line(export_row(rejection))


def _line(values: tuple[str, ...]) -> str:
    """한 줄을 CSV 규칙(따옴표·이스케이프)에 맞게 만듭니다."""
    buffer = io.StringIO()
    # ⚠️ 엑셀은 CRLF 를 기대한다.
    csv.writer(buffer, lineterminator="\r\n").writerow(values)
    return buffer.getvalue()
