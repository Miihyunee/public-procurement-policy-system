"""
procurement.reviews.export

확정 이력을 **CSV 로 내보냅니다** — 담당자가 엑셀에서 직접 검증하도록.

.. warning::
    ⛔ **분석기 평가용이 아닙니다.** 담당자가 확정한 값을 그대로 옮길 뿐이며,
    정확도를 재거나 자동 확정을 돕는 기능이 아닙니다.

.. warning::
    ⛔ **과거 이력이 확정값을 대신하지 않습니다.**

    ``최종 유형`` 열에는 :attr:`~procurement.models.review.PurchaseReview.final_purchase_type`
    만 들어갑니다. ``past_labels.dominant`` 를 대신 넣으면 **참고 정보가 확정값으로
    둔갑**합니다. 과거 이력은 ``과거 …`` 열에 **따로** 실립니다.

.. note::
    **엑셀에서 한글이 깨지지 않도록** UTF-8 BOM(``utf-8-sig``)으로 씁니다.
    BOM 이 없으면 엑셀이 한글을 로컬 인코딩으로 읽어 깨집니다.

.. note::
    **CSV 인젝션 대비.** ``=`` · ``+`` · ``-`` · ``@`` 로 시작하는 값은 엑셀이
    수식으로 해석합니다. 적요·메모는 사람이 입력한 자유 문자열이므로 앞에
    작은따옴표를 붙여 **문자열로 고정**합니다(§:func:`_safe`).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from procurement.reviews.review_service import ReviewTarget

#: 열 순서. **고정**입니다 — 담당자가 만든 엑셀 수식이 깨지지 않도록.
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "구매ID",
    "신고기준일",
    "결의일자",
    "적요",
    "거래처명",
    "사업자번호",
    "금액",
    "예산과목",
    "현재 상태",
    "최종 유형",
    "확정자",
    "확정일시",
    "검토 메모",
    "분석 방법",
    "분석 1순위",
    "과거 확정 건수",
    "과거 확정 최다 유형",
    "과거 최다 유형 비율",
    "과거 확정 유형 수",
    "과거 이력 일관성",
)

#: 값이 없을 때 넣는 문자열. 빈 칸과 "0" 을 구분하기 위해 **빈 문자열**로 통일합니다.
EMPTY: Final = ""

#: 엑셀이 수식으로 해석하는 시작 문자들.
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: str) -> str:
    """수식으로 해석될 수 있는 값을 문자열로 고정합니다.

    Examples:
        >>> _safe("=1+1")
        "'=1+1"
        >>> _safe("정상 적요")
        '정상 적요'
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _text(value: object) -> str:
    """어떤 값이든 CSV 한 칸으로 만듭니다.

    - ``None`` → 빈 칸
    - 날짜 → ``YYYY-MM-DD``
    - 일시 → ``YYYY-MM-DD HH:MM:SS`` (초 단위까지, 마이크로초 제외)
    - ``Decimal`` → 지수 표기 없이 그대로
    """
    if value is None:
        return EMPTY
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return _safe(str(value))


def export_row(target: ReviewTarget) -> tuple[str, ...]:
    """검토 한 건을 CSV 한 줄로 만듭니다.

    Args:
        target: 검토 대상.

    Returns:
        :data:`EXPORT_COLUMNS` 와 **같은 순서**의 문자열 묶음.
    """
    purchase = target.purchase
    review = target.review
    past = target.past_labels
    top = review.top_candidate

    return (
        _text(purchase.purchase_id),
        _text(purchase.issue_date),
        _text(purchase.resolution_date),
        _text(purchase.description),
        _text(purchase.company_name),
        _text(purchase.business_no),
        _text(purchase.amount),
        _text(purchase.budget_account),
        _text(review.review_status),
        # ⛔ 담당자 확정값만. 과거 이력이나 분석 1순위를 넣지 않는다.
        _text(review.final_purchase_type_label),
        _text(review.reviewed_by),
        _text(review.reviewed_at),
        _text(review.review_note),
        _text(review.analyzer_name),
        _text(top.label if top else None),
        _text(past.total),
        _text(past.dominant.label if past.dominant else None),
        _text(past.dominant_ratio if past.total else None),
        _text(past.type_count),
        _text(past.consistency),
    )


def export_lines(targets: Iterable[ReviewTarget]) -> Iterator[str]:
    """CSV 를 **한 줄씩** 흘려보냅니다.

    전체를 메모리에 쌓지 않으므로 건수가 늘어도 사용량이 일정합니다. 첫 줄은
    UTF-8 BOM 이 붙은 머리글입니다.

    Args:
        targets: 내보낼 검토 대상들.

    Yields:
        줄바꿈이 포함된 CSV 한 줄.
    """
    yield "﻿" + _line(EXPORT_COLUMNS)
    for target in targets:
        yield _line(export_row(target))


def _line(values: tuple[str, ...]) -> str:
    """한 줄을 CSV 규칙(따옴표·이스케이프)에 맞게 만듭니다."""
    buffer = io.StringIO()
    # ⚠️ 엑셀은 CRLF 를 기대한다. 리눅스 기본값(LF)으로 쓰면 일부 버전에서
    #    줄이 밀린다.
    csv.writer(buffer, lineterminator="\r\n").writerow(values)
    return buffer.getvalue()
