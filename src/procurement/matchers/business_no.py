"""
procurement.matchers.business_no

사업자등록번호 정규화를 담당합니다.

사업자등록번호(``business_no``)는 이 시스템에서 **모든 데이터 연결의 기준
키**이므로, 표기 형식이 다르면 동일한 사업자라도 연결되지 않습니다.
기관·파일마다 ``123-45-67890`` / ``1234567890`` 처럼 표기가 달라
**저장 전 정규화가 필수**입니다.

정규화 규칙은 ``docs/PURCHASE_IMPORT_DESIGN.md`` 4장을 따릅니다.

.. important::
    본 모듈은 **구매데이터 Import 와 외부 인증데이터 수집 양쪽에서 함께
    사용해야 합니다.** 한쪽만 정규화하면 형식이 어긋나 매칭이 실패합니다.

사용 예::

    from procurement.matchers.business_no import normalize_business_no

    result = normalize_business_no("123-45-67890")
    result.value    # "1234567890"
    result.is_valid # True
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

#: 사업자등록번호 자릿수
BUSINESS_NO_LENGTH = 10

#: 체크섬 계산에 사용하는 가중치 (앞 9자리에 대응)
_CHECKSUM_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)

#: 제거 대상 구분자 — 하이픈·공백·마침표 등
_SEPARATOR_PATTERN = re.compile(r"[\s\-.‐-―]")

#: 숫자형이 문자열로 변환되며 붙는 소수부 (예: ``1234567890.0``)
_TRAILING_DECIMAL_PATTERN = re.compile(r"\.0+$")


class BusinessNoStatus(Enum):
    """사업자등록번호 정규화 결과 상태.

    - ``VALID``: 정규화에 성공했으며 결합키로 사용할 수 있음
    - ``MISSING``: 값이 없음(``None``·빈 문자열·공백만)
    - ``INVALID_FORMAT``: 숫자가 아니거나 자릿수를 맞출 수 없음
    """

    VALID = "VALID"
    MISSING = "MISSING"
    INVALID_FORMAT = "INVALID_FORMAT"


@dataclass(frozen=True, kw_only=True)
class NormalizedBusinessNo:
    """정규화 결과.

    Attributes:
        value: 정규화된 10자리 문자열. 실패 시 ``None``.
        status: 정규화 결과 상태.
        warnings: 저장은 하되 확인이 필요한 사항(자릿수 보정·체크섬 불일치).
            **경고가 있어도 데이터를 버리지 않습니다.**
        original: 입력 원본을 문자열로 표현한 값(리포트·추적용).
    """

    value: str | None
    status: BusinessNoStatus
    warnings: list[str] = field(default_factory=list)
    original: str = ""

    @property
    def is_valid(self) -> bool:
        """결합키로 사용할 수 있는지 여부."""
        return self.status is BusinessNoStatus.VALID

    @property
    def has_warning(self) -> bool:
        """확인이 필요한 경고가 있는지 여부."""
        return bool(self.warnings)


def normalize_business_no(value: object) -> NormalizedBusinessNo:
    """사업자등록번호를 10자리 숫자 문자열로 정규화합니다.

    처리 순서는 다음과 같습니다.

    1. 문자열로 변환(숫자형·실수형 대응)
    2. 앞뒤 공백 제거
    3. 하이픈·공백·마침표 등 구분자 제거
    4. 숫자만 남았는지 확인
    5. 정확히 10자리인지 확인 (**자릿수를 자동 보정하지 않음**)
    6. 체크섬 검증(불일치해도 통과, 경고만)

    .. warning::
        사업자등록번호는 **시스템의 핵심 결합 키**이므로 근거 없이 값을
        만들어내지 않습니다. 9자리 값(Excel 에서 앞자리 ``0`` 이 사라진 경우로
        추정)도 **자동으로 보정하지 않고 형식 오류로 처리**합니다.
        임의 보정은 **다른 기업과 잘못 연결될 위험**이 있기 때문입니다.
        원본 값은 :attr:`NormalizedBusinessNo.original` 에 보존되어 추적할 수
        있습니다.

    Args:
        value: 정규화할 값. 문자열·정수·실수·``None`` 을 받을 수 있습니다.

    Returns:
        :class:`NormalizedBusinessNo`. 실패해도 예외를 발생시키지 않고
        상태로 알려줍니다(대량 처리 중 한 행 때문에 중단되지 않도록).
    """
    original = "" if value is None else str(value)

    if value is None:
        return NormalizedBusinessNo(
            value=None, status=BusinessNoStatus.MISSING, original=original
        )

    text = _to_text(value).strip()
    if not text:
        return NormalizedBusinessNo(
            value=None, status=BusinessNoStatus.MISSING, original=original
        )

    digits = _SEPARATOR_PATTERN.sub("", text)
    if not digits:
        return NormalizedBusinessNo(
            value=None, status=BusinessNoStatus.MISSING, original=original
        )

    if not digits.isdigit():
        return NormalizedBusinessNo(
            value=None,
            status=BusinessNoStatus.INVALID_FORMAT,
            warnings=[f"숫자가 아닌 문자가 포함되어 있습니다: {original!r}"],
            original=original,
        )

    if len(digits) != BUSINESS_NO_LENGTH:
        return NormalizedBusinessNo(
            value=None,
            status=BusinessNoStatus.INVALID_FORMAT,
            warnings=[_length_error_message(original, digits)],
            original=original,
        )

    warnings: list[str] = []

    # 체크섬 불일치는 경고로만 처리한다(PM 결정 D-002). 데이터를 버리지 않는다.
    if not is_valid_checksum(digits):
        warnings.append(f"사업자등록번호 체크섬이 일치하지 않습니다: {digits}")

    return NormalizedBusinessNo(
        value=digits,
        status=BusinessNoStatus.VALID,
        warnings=warnings,
        original=original,
    )


def is_valid_checksum(business_no: str) -> bool:
    """사업자등록번호 체크섬(마지막 자리)이 유효한지 확인합니다.

    앞 9자리에 가중치 ``(1,3,7,1,3,7,1,3,5)`` 를 곱해 더하고, 9번째 자리와 5를
    곱한 값의 십의 자리를 추가로 더한 뒤, ``(10 - 합 % 10) % 10`` 이 마지막
    자리와 같은지 비교합니다.

    Args:
        business_no: 정규화된 10자리 숫자 문자열.

    Returns:
        체크섬이 일치하면 ``True``. 자릿수·형식이 맞지 않으면 ``False``.

    .. note::
        검증 실패가 곧 무효를 뜻하지는 않습니다. 본 프로젝트에서는 **경고로만
        사용**하며 데이터를 거부하지 않습니다(PM 결정 D-002).
    """
    if len(business_no) != BUSINESS_NO_LENGTH or not business_no.isdigit():
        return False

    digits = [int(char) for char in business_no]
    total = sum(digit * weight for digit, weight in zip(digits, _CHECKSUM_WEIGHTS, strict=False))
    total += (digits[8] * 5) // 10
    return (10 - total % 10) % 10 == digits[9]


def _length_error_message(original: str, digits: str) -> str:
    """자릿수 오류 메시지를 만듭니다.

    9자리인 경우, Excel 에서 앞자리 ``0`` 이 사라졌을 가능성이 높으므로 그
    가능성을 안내합니다. **다만 값을 자동으로 보정하지는 않습니다.**
    """
    base = (
        f"사업자등록번호는 {BUSINESS_NO_LENGTH}자리여야 합니다: "
        f"{original!r} (정규화 후 {len(digits)}자리)"
    )
    if len(digits) == BUSINESS_NO_LENGTH - 1:
        return (
            f"{base}. Excel 에서 앞자리 0 이 사라졌을 수 있습니다. "
            "자동 보정하지 않으므로 원본을 확인해 주세요."
        )
    return base


def _to_text(value: object) -> str:
    """입력값을 문자열로 변환합니다(숫자형 표기 보정 포함)."""
    if isinstance(value, float):
        # 1234567890.0 → "1234567890" (지수 표기 방지)
        return _TRAILING_DECIMAL_PATTERN.sub("", f"{value:.1f}")
    text = str(value)
    if isinstance(value, str):
        return text
    return _TRAILING_DECIMAL_PATTERN.sub("", text)


def business_no_search_key(value: object) -> str:
    """**검색어 비교용** 키 — 구분자만 지웁니다.

    ``123-45-67890`` 과 ``1234567890`` 은 같은 사업자등록번호이지만, 지출결의서
    · 세금계산서에는 **하이픈이 있는 형태로 인쇄**되고 시스템에는 숫자만
    저장됩니다. 담당자가 종이에 있는 대로 옮겨 적으면 아무것도 찾지 못하고,
    **0건은 "그런 거래가 없다" 로 읽힙니다.**

    :func:`normalize_business_no` 와 **다릅니다.** 그쪽은 결합키를 만드는
    함수라 10자리·숫자만을 요구하지만, 검색은 앞 몇 자리만 넣는 일이 흔해
    자릿수를 따지지 않습니다.

    ⛔ **값을 저장하거나 기업을 연결하는 데 쓰지 않습니다.** 오직 "보여줄지
    말지" 를 정하는 비교용입니다.

    Args:
        value: 검색어 또는 저장된 사업자등록번호.

    Returns:
        구분자를 지운 문자열. 입력이 비면 빈 문자열.

    Examples:
        >>> business_no_search_key("123-45-67890")
        '1234567890'
        >>> business_no_search_key("123 45")
        '12345'
        >>> business_no_search_key(None)
        ''
    """
    if value is None:
        return ""
    return _SEPARATOR_PATTERN.sub("", _to_text(value)).strip()
