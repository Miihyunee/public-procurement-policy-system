"""
procurement.importers.rejection_trace

적재 실패한 행을 **추적 기록**으로 바꿉니다.

.. warning::
    ⛔ **업무 판단을 하지 않습니다.** 여기서 하는 일은 Importer 가 남긴 사유
    문장을 코드로 분류하고, 원본 값을 그대로 옮겨 담는 것뿐입니다. 어떤 행을
    실적으로 인정할지는 고객 확인 사항입니다(Q5-8).

.. note::
    **왜 필요한가.** STEP 11 실데이터 리허설에서 원본 2,292행 중 130행이
    DB-1 에 적재되지 않았고, 그 사실이 업로드 응답에만 잠깐 나타난 뒤 사라져
    검토 화면에서는 보이지 않았습니다. 담당자가 "전체를 검토했다" 고 판단해도
    실제로는 보지 못한 행이 남습니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from procurement.importers.purchase_importer import ImportReport, ImportRowResult
from procurement.models.import_rejection import (
    REASON_MISSING_REQUIRED,
    REASON_NON_POSITIVE_AMOUNT,
    REASON_OTHER,
    REASON_UNPARSABLE,
    ImportRejection,
)

#: 사유 문장에서 코드를 알아내는 표지. 위에서부터 먼저 맞는 것을 씁니다.
#:
#: ⚠️ Importer·Repository 가 실제로 만들어 내는 문장을 그대로 적어 둔 것입니다.
#: 문장이 바뀌면 :data:`REASON_OTHER` 로 떨어질 뿐, 기록 자체는 남습니다.
_REASON_MARKERS: tuple[tuple[str, str], ...] = (
    ("구매금액은 0 보다 커야 합니다", REASON_NON_POSITIVE_AMOUNT),
    ("필수값이 누락되었습니다", REASON_MISSING_REQUIRED),
    ("비어 있습니다", REASON_MISSING_REQUIRED),
    ("확인할 수 없습니다", REASON_MISSING_REQUIRED),
    ("변환할 수 없습니다", REASON_UNPARSABLE),
    ("올바르지 않습니다", REASON_UNPARSABLE),
    ("형식", REASON_UNPARSABLE),
)


def classify_reason(messages: Sequence[str]) -> str:
    """사유 문장들에서 사유 코드를 고릅니다.

    Args:
        messages: Importer 가 남긴 사유 문장.

    Returns:
        :data:`~procurement.models.import_rejection.REJECTION_REASONS` 중 하나.
        아는 표지가 없으면 ``OTHER`` — **버리지 않고** 원문을 함께 남깁니다.
    """
    joined = " ".join(messages)
    for marker, reason in _REASON_MARKERS:
        if marker in joined:
            return reason
    return REASON_OTHER


def build_rejections(
    rows: Sequence[Mapping[str, Any]],
    report: ImportReport,
    *,
    batch_id: int | None = None,
) -> list[ImportRejection]:
    """적재되지 않은 행들의 추적 기록을 만듭니다.

    Args:
        rows: Importer 에 넘긴 **원본 행 목록**. ``report`` 의 ``row_number``
            (1부터)로 되짚습니다.
        report: 적재 결과.
        batch_id: 이 적재가 속한 배치 ID.

    Returns:
        기록할 :class:`ImportRejection` 목록. 실패한 행이 없으면 빈 목록.
    """
    return [_rejection(rows, result, batch_id=batch_id) for result in report.failed_rows()]


def _rejection(
    rows: Sequence[Mapping[str, Any]], result: ImportRowResult, *, batch_id: int | None
) -> ImportRejection:
    """실패한 행 하나를 기록으로 옮깁니다. 원본 값은 **손대지 않고** 그대로."""
    index = result.row_number - 1
    row: Mapping[str, Any] = rows[index] if 0 <= index < len(rows) else {}
    return ImportRejection(
        batch_id=batch_id,
        row_number=result.row_number,
        reason=classify_reason(result.messages),
        message="\n".join(result.messages),
        business_no=_text(row.get("business_no")) or result.business_no,
        company_name=_text(row.get("company_name")),
        description=_text(row.get("description")),
        budget_account=_text(row.get("budget_account")),
        amount=_amount(row.get("amount")),
        resolution_date=_date(row.get("resolution_date")),
        issue_date=_date(row.get("issue_date")),
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _amount(value: object) -> Decimal | None:
    """원본 금액을 그대로 보존합니다 — **음수도 그대로**."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = str(value).replace(",", "").replace("₩", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        # 숫자로 못 읽는 값이라 금액 칸은 비우지만, 원문은 message 에 남는다.
        return None


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip().replace(".", "-").replace("/", "-")
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
