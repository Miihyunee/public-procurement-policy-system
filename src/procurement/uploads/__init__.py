"""
procurement.uploads

**표준 업로드 양식** 계층입니다.

기관마다 다른 원본 엑셀을 추측해서 해석하지 않고, 우리가 지정한 양식을
사용자가 내려받아 채워 올리는 방식을 담당합니다::

    표준 Excel  →  Adapter  →  Validation  →  Mapping  →  Purchase  →  Repository
                   ↑ 구현      ↑ 구현        ↑ 미구현(지급일 처리 미확정)

.. note::
    :mod:`~procurement.uploads.format` 과 :mod:`~procurement.uploads.validation`
    은 **외부 라이브러리에 의존하지 않습니다.** 엑셀 파일 입출력만
    :mod:`~procurement.uploads.excel_adapter` · :mod:`~procurement.uploads.template`
    이 ``openpyxl`` 을 사용하며, 이 둘은 여기서 재노출하지 않습니다. 덕분에
    검증 계층은 엑셀 라이브러리 없이도 그대로 쓸 수 있습니다.
"""

from procurement.uploads.format import (
    COLUMNS_BY_HEADER,
    COLUMNS_BY_KEY,
    PENDING_COLUMNS,
    REQUIRED_HEADERS,
    STANDARD_COLUMNS,
    StandardColumn,
    example_row,
    guide_lines,
    header_row,
)
from procurement.uploads.validation import (
    RowIssue,
    ValidatedRow,
    ValidationReport,
    validate_headers,
    validate_rows,
)

__all__ = [
    "COLUMNS_BY_HEADER",
    "COLUMNS_BY_KEY",
    "PENDING_COLUMNS",
    "REQUIRED_HEADERS",
    "STANDARD_COLUMNS",
    "RowIssue",
    "StandardColumn",
    "ValidatedRow",
    "ValidationReport",
    "example_row",
    "guide_lines",
    "header_row",
    "validate_headers",
    "validate_rows",
]
