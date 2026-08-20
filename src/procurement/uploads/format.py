"""
procurement.uploads.format

**표준 업로드 양식**의 컬럼을 정의합니다.

기관마다 다른 원본 엑셀 컬럼명을 시스템이 추측해서 해석하지 않습니다. 대신
우리가 지정한 양식을 사용자가 내려받아 채워 올립니다.

::

    표준 Excel  →  Validation  →  Mapping  →  Purchase  →  Repository  →  Calculator
     (이 모듈)    (validation)    (미구현)

.. warning::
    **확정된 컬럼만 정의합니다.** 구매유형 · 대표자명 · 거래구분은 아직 확정되지
    않아 :data:`PENDING_COLUMNS` 에 이유와 함께 따로 두었고, 양식에 넣지
    않았습니다.

.. note::
    **2026-08-20 — 신고기준일 · 적요 · 예산과목 추가.** 음수 상계 업무규칙이
    확정되면서(``DECISIONS.md`` §0.6.3.4) 세금계산서 발행일자(``신고기준일``)가
    상계 판정에 필요해졌고, 적요·예산과목도 담당자가 함께 확인하는 정보로
    확인되었습니다. 세 컬럼 모두 **원본 엑셀에 이미 존재**하므로 사용자가 새로
    만들어야 하는 값이 아닙니다.

.. note::
    이 모듈은 **엑셀 파일을 읽거나 쓰지 않습니다.** 컬럼 정의만 담당하므로
    외부 라이브러리에 의존하지 않습니다. 엑셀 입출력(``openpyxl``)은 의존성
    추가 승인 후 별도 어댑터로 붙입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, kw_only=True)
class StandardColumn:
    """표준 양식의 컬럼 하나.

    Attributes:
        key: 시스템 내부에서 쓰는 식별자. 엑셀 컬럼명이 바뀌어도 이 값은 유지된다.
        header: 사용자가 보는 엑셀 컬럼명(1행 머리글).
        required: 값이 반드시 있어야 하는지 여부.
        description: 양식 안내에 쓸 설명.
        example: 사용자에게 보여줄 입력 예시.
    """

    key: str
    header: str
    required: bool
    description: str
    example: str


#: 🟢 **고객 확정 컬럼** (2026-08-14 회신 · `DECISIONS.md` §0.6).
#:
#: 순서가 곧 엑셀 열 순서입니다.
STANDARD_COLUMNS: Final[tuple[StandardColumn, ...]] = (
    StandardColumn(
        key="resolution_date",
        header="결의일자",
        required=True,
        description="구매실적의 연도 귀속 기준일입니다.",
        example="2026-03-15",
    ),
    StandardColumn(
        key="contract_date",
        header="계약일자",
        required=True,
        description="창업기업 판정에 함께 사용합니다.",
        example="2026-02-20",
    ),
    StandardColumn(
        key="payment_date",
        header="지급일",
        required=True,
        description=(
            "대금 지급일(지출완료일)입니다. **결의일자와 다른 날짜**이며, "
            "중소기업·여성기업·장애인기업 인증 유효기간 판정에 사용합니다."
        ),
        example="2026-04-01",
    ),
    StandardColumn(
        key="company_name",
        header="기업명",
        required=True,
        description="거래처명입니다. 음수 거래 상계 판정에도 사용합니다.",
        example="한빛산업개발",
    ),
    StandardColumn(
        key="business_no",
        header="사업자등록번호",
        required=True,
        description="기업을 식별하는 핵심 키입니다. 하이픈은 있어도 됩니다.",
        example="220-81-62517",
    ),
    StandardColumn(
        key="amount",
        header="계",
        required=True,
        description="부가가치세가 포함된 총 구매금액입니다. 공급가액이 아닙니다.",
        example="54648000",
    ),
    # ── 2026-08-20 추가 (음수 상계 업무규칙 확정 · DECISIONS §0.6.3.4) ──
    StandardColumn(
        key="issue_date",
        header="신고기준일",
        required=True,
        description=(
            "세금계산서 발행일자입니다. **음수 거래 상계**에서 (+)/(−) 를 짝지을 때 "
            "이 날짜의 차이가 가장 작은 건을 매칭합니다. 결의일자와 다른 날짜입니다."
        ),
        example="2026-03-10",
    ),
    StandardColumn(
        key="description",
        header="적요",
        # ⛔ 값이 없어도 막지 않는다. 실측 2,292행 중 1행이 공란이었다.
        required=False,
        description=(
            "거래 내용입니다. 상계 후보를 좁힐 때 참고합니다. "
            "**적요가 다르다는 이유만으로 상계에서 제외하지 않습니다.**"
        ),
        example="사무용품 구매",
    ),
    StandardColumn(
        key="budget_account",
        header="예산과목",
        # ⛔ 값이 없어도 막지 않는다. (−) 세금계산서는 실제 지출이 발생하지 않아
        # 공란인 경우가 많다(실측: 음수 129건 중 128건 공란). 공란을 오류로
        # 처리하면 상계 대상 자체를 받을 수 없다.
        required=False,
        description=(
            "지출 예산과목입니다. 공란일 수 있으며, **공란이라고 해서 자동으로 "
            "삭제·상계하지 않습니다**(참고 정보)."
        ),
        example="소모성물품구입비",
    ),
)

#: 헤더 → 컬럼 정의 (조회용).
COLUMNS_BY_HEADER: Final[MappingProxyType[str, StandardColumn]] = MappingProxyType(
    {column.header: column for column in STANDARD_COLUMNS}
)

#: 내부 키 → 컬럼 정의 (조회용).
COLUMNS_BY_KEY: Final[MappingProxyType[str, StandardColumn]] = MappingProxyType(
    {column.key: column for column in STANDARD_COLUMNS}
)

#: 반드시 있어야 하는 **헤더**.
#:
#: .. note::
#:     **"헤더가 있어야 한다" 와 "값이 있어야 한다" 는 다른 문제입니다.**
#:     표준 양식의 컬럼은 **전부** 있어야 하므로 여기에는 모든 헤더가 들어갑니다.
#:     개별 행의 값이 반드시 채워져야 하는지는 :attr:`StandardColumn.required`
#:     가 따로 정합니다(적요·예산과목은 공란을 허용).
REQUIRED_HEADERS: Final[tuple[str, ...]] = tuple(column.header for column in STANDARD_COLUMNS)

#: 값이 비어 있으면 오류인 헤더(행 단위 필수값).
REQUIRED_VALUE_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in STANDARD_COLUMNS if column.required
)

#: 🔴 **아직 양식에 넣지 않은 컬럼과 그 이유.**
#:
#: 고객이 확정하지 않았으므로 양식에 넣지 않습니다. 넣어 두면 사용자가 채우고,
#: 그 값을 시스템이 해석하게 되어 확정되지 않은 규칙이 생깁니다.
#:
#: .. note::
#:     ``신고기준일`` · ``적요`` · ``예산과목`` 은 2026-08-20 음수 상계 업무규칙
#:     확정으로 표준 양식에 편입되어 이 목록에서 빠졌습니다(§0.6.3.4).
#:     다만 ``예산과목`` 을 **구매유형 자동 분류에 쓰는 것은 여전히 금지**입니다.
PENDING_COLUMNS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "구매유형": "사용자가 직접 고를지, 예산과목에서 유도할지 미정",
        "대표자명": "Company 필수 제약 관련. 스키마 변경 승인 대상",
        "거래구분": "표준 양식은 구매만 담으므로 필요 여부 미정",
    }
)


def header_row() -> tuple[str, ...]:
    """엑셀 1행에 넣을 머리글을 순서대로 반환합니다."""
    return tuple(column.header for column in STANDARD_COLUMNS)


def example_row() -> tuple[str, ...]:
    """양식에 넣을 입력 예시 한 줄을 반환합니다."""
    return tuple(column.example for column in STANDARD_COLUMNS)


def guide_lines() -> tuple[str, ...]:
    """양식 안내 문구를 반환합니다(엑셀 안내 시트·화면 공용).

    Returns:
        각 줄이 한 항목인 안내 문구.
    """
    lines = [
        "■ 작성 안내",
        "",
        "· 1행의 머리글은 지우거나 바꾸지 마세요.",
        "· 2행의 예시는 지우고 실제 데이터를 입력하세요.",
        "· 머리글은 모두 있어야 합니다. 하나라도 없으면 업로드할 수 없습니다.",
        "· 적요 · 예산과목은 값이 비어 있어도 됩니다. 그 밖의 항목은 필수입니다.",
        "",
        "■ 항목 설명",
        "",
    ]
    lines.extend(f"· {column.header} — {column.description}" for column in STANDARD_COLUMNS)
    lines.extend(
        [
            "",
            "■ 입력 형식",
            "",
            "· 날짜 — 2026-03-15 또는 2026/03/15",
            "· 사업자등록번호 — 10자리. 하이픈은 있어도 됩니다",
            "· 금액 — 숫자만. 쉼표는 있어도 됩니다",
        ]
    )
    return tuple(lines)
