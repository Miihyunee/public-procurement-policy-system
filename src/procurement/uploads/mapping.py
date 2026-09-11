"""
procurement.uploads.mapping

**검증 계층과 기존 적재 계층을 잇는 얇은 연결자**입니다.

::

    ValidatedRow.values  →  [ 이 모듈 ]  →  PurchaseImporter 가 읽는 행

.. warning::
    ⛔ **업무 판정도, 값 변환도 하지 않습니다.**

    검증 계층이 만드는 키가 적재 계층이 읽는 키와 **이름까지 그대로
    일치**하므로, 이 모듈이 할 일은 "그대로 넘긴다" 뿐입니다. 여기서 값을
    고치면 검증 규칙이 두 곳에 생기고, 어느 쪽이 진짜인지 알 수 없게 됩니다.

    빈 값을 채우거나, 날짜를 다른 날짜로 대체하거나, 금액을 조정하지 않습니다.

.. note::
    이 모듈이 **존재해야 하는 이유**는 값을 바꾸기 위해서가 아니라, 두 계층이
    직접 의존하지 않게 하기 위해서입니다. 검증 계층은 저장을 모르고, 적재
    계층은 엑셀을 모릅니다. 그 사이를 이 모듈 하나가 잇습니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from procurement.uploads.validation import ValidatedRow

#: 적재 계층으로 넘기는 키. :mod:`procurement.uploads.format` 의 컬럼 ``key`` 와
#: :class:`~procurement.importers.purchase_importer.PurchaseImporter` 가 읽는 키가
#: **같은 이름**이라는 사실을 명시적으로 고정한다.
#:
#: 이 목록이 어느 한쪽과 어긋나면 ``tests/test_upload_mapping.py`` 가 먼저 깨진다.
MAPPED_KEYS: Final[tuple[str, ...]] = (
    "resolution_date",
    "contract_date",
    "payment_date",
    "issue_date",
    "company_name",
    "business_no",
    "amount",
    "description",
    "budget_account",
)


def to_import_row(row: ValidatedRow) -> dict[str, object]:
    """검증된 행 하나를 적재 계층이 읽는 형태로 만듭니다.

    검증이 만들어 낸 값을 **그대로** 담습니다. 검증 단계에서 값이 없었던 키는
    담지 않으며, 없는 값을 임의로 채우지 않습니다.

    Args:
        row: 검증을 통과한 행.

    Returns:
        ``PurchaseImporter.import_rows()`` 에 넣을 수 있는 매핑.
    """
    return {key: row.values[key] for key in MAPPED_KEYS if key in row.values}


def to_import_rows(rows: Iterable[ValidatedRow]) -> list[dict[str, object]]:
    """검증된 행 목록을 적재 계층이 읽는 형태로 만듭니다.

    Args:
        rows: 검증을 통과한 행 목록.

    Returns:
        행 매핑 목록. 입력 순서를 그대로 유지합니다(오류 메시지의 행 번호와
        저장 순서가 어긋나지 않게 하기 위함).
    """
    return [to_import_row(row) for row in rows]


def mapped_keys() -> Sequence[str]:
    """적재 계층으로 넘기는 키 목록을 반환합니다(문서·테스트용)."""
    return MAPPED_KEYS
