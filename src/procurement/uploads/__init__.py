"""
procurement.uploads

**표준 업로드 양식** 계층입니다.

기관마다 다른 원본 엑셀을 추측해서 해석하지 않고, 우리가 지정한 양식을
사용자가 내려받아 채워 올리는 방식을 담당합니다::

    표준 Excel  →  Validation  →  Mapping  →  Purchase  →  Repository  →  Calculator
                    ↑ 여기까지 구현        ↑ 미구현(결의일자 물리 필드 미확정)

.. note::
    이 패키지는 **외부 라이브러리에 의존하지 않습니다.** 엑셀 파일 입출력은
    ``openpyxl`` 의존성 추가 승인 후 별도 어댑터로 붙입니다. 그때도 이 패키지의
    정의·검증 로직은 그대로 재사용합니다.
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
