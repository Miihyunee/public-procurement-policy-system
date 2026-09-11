"""
procurement.dashboard.missing_resolution_export

**결의일자가 입력되지 않은 구매**를 CSV 로 내보냅니다 — 담당자가 내려받아
업무 확인·고객 확인에 쓰도록.

.. warning::
    ⛔ **처리 방식을 정하는 파일이 아닙니다.** 결의일자가 비어 있다는 **사실만**
    그대로 옮깁니다. 이 행들을 어떻게 할지는 아직 정해지지 않았습니다.

.. warning::
    ⛔ **빈 결의일자를 채우지 않습니다.** ``resolution_date`` 가 ``None`` 이면
    CSV 에서도 **빈 칸**입니다. 신고기준일·지급일·계약일 어느 것으로도
    대체하지 않습니다 — 비어 있다는 사실이 이 파일의 존재 이유이기 때문입니다.

.. note::
    **규약은 기존 CSV 두 개(**:mod:`procurement.reviews.export` ·
    :mod:`procurement.importers.rejection_export`\\ **)와 같습니다.** UTF-8 BOM ·
    CRLF · 수식 인젝션 방어 · generator 스트리밍. 담당자가 세 파일을 같은
    방식으로 열 수 있어야 하기 때문입니다. **새 CSV 규칙을 만들지 않습니다.**

.. note::
    열 구성은 화면 목록과 같습니다. **원본 행 번호 · 공급가액 · 세액은 넣지
    않습니다** — ``purchase`` 테이블에 없는 값이므로 지어내지 않습니다
    (``docs/DECISIONS.md`` §0.7.5).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import date
from decimal import Decimal
from typing import Final

from procurement.models.purchase import Purchase

#: 열 순서. **고정**입니다.
#:
#: 화면 목록(``index.html`` 의 결의일자 미기재 표)과 **같은 순서**입니다 —
#: 담당자가 화면에서 보던 것과 파일이 달라 보이면 안 되기 때문입니다.
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "구매ID",
    "적요",
    "거래처명",
    "사업자번호",
    "계",
    "결의일자",
    "예산과목",
)

#: 값이 없을 때 넣는 문자열. **결의일자가 비어 있으면 이 값이 나갑니다.**
EMPTY: Final = ""

#: 엑셀이 수식으로 해석하는 시작 문자들.
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: str) -> str:
    """수식으로 해석될 수 있는 값을 문자열로 고정합니다.

    ⚠️ **금액 열에는 쓰지 않습니다.** 숫자로 읽혀야 합계를 낼 수 있습니다.

    Examples:
        >>> _safe("=1+1")
        "'=1+1"
        >>> _safe("사무용품 구매")
        '사무용품 구매'
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _text(value: object) -> str:
    """자유 입력 값을 CSV 한 칸으로 만듭니다(수식 방어 포함).

    ``None`` 은 **빈 칸**입니다 — 다른 값으로 대체하지 않습니다.
    """
    if value is None:
        return EMPTY
    if isinstance(value, date):
        return value.isoformat()
    return _safe(str(value))


def _number(value: object) -> str:
    """숫자 칸. **지수 표기 없이** 그대로 씁니다."""
    if value is None:
        return EMPTY
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def export_row(purchase: Purchase) -> tuple[str, ...]:
    """구매 한 건을 CSV 한 줄로 만듭니다.

    Args:
        purchase: 결의일자가 비어 있는 구매 행.

    Returns:
        :data:`EXPORT_COLUMNS` 와 **같은 순서**의 문자열 묶음.
    """
    return (
        _number(purchase.purchase_id),
        _text(purchase.description),
        _text(purchase.company_name),
        # 사업자번호는 저장된 표기를 그대로 둡니다 — 하이픈을 넣거나 빼지 않습니다.
        _text(purchase.business_no),
        _number(purchase.amount),
        # ⛔ 비어 있으면 비어 있는 채로 나갑니다. 다른 날짜를 넣지 않습니다.
        _text(purchase.resolution_date),
        _text(purchase.budget_account),
    )


def export_lines(purchases: Iterable[Purchase]) -> Iterator[str]:
    """CSV 를 **한 줄씩** 흘려보냅니다.

    전체를 메모리에 쌓지 않으므로 건수가 늘어도 사용량이 일정합니다. 첫 줄은
    UTF-8 BOM 이 붙은 머리글입니다.

    Args:
        purchases: 내보낼 구매 행들.

    Yields:
        줄바꿈이 포함된 CSV 한 줄.
    """
    yield "﻿" + _line(EXPORT_COLUMNS)
    for purchase in purchases:
        yield _line(export_row(purchase))


def _line(values: tuple[str, ...]) -> str:
    """한 줄을 CSV 규칙(따옴표·이스케이프)에 맞게 만듭니다."""
    buffer = io.StringIO()
    # ⚠️ 엑셀은 CRLF 를 기대한다.
    csv.writer(buffer, lineterminator="\r\n").writerow(values)
    return buffer.getvalue()
