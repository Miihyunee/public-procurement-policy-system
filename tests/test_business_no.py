"""
사업자등록번호 정규화 테스트.

정규화 규칙은 ``docs/PURCHASE_IMPORT_DESIGN.md`` 4장을 따릅니다.
결합키이므로 입력 형태별 처리를 빠짐없이 검증합니다.
"""

from __future__ import annotations

import pytest

from procurement.matchers.business_no import (
    BusinessNoStatus,
    is_valid_checksum,
    normalize_business_no,
)

#: 체크섬을 만족하는 실제 형식의 번호(검증 알고리즘 기준)
VALID_CHECKSUM_NO = "1018116293"


class TestNormalizeValidInput:
    """정상적으로 정규화되는 입력을 검증합니다."""

    def test_removes_hyphen(self) -> None:
        result = normalize_business_no("123-45-67890")
        assert result.value == "1234567890"
        assert result.is_valid

    def test_plain_ten_digits(self) -> None:
        result = normalize_business_no("1234567890")
        assert result.value == "1234567890"
        assert result.status is BusinessNoStatus.VALID

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_business_no("  1234567890  ").value == "1234567890"

    def test_removes_inner_whitespace(self) -> None:
        assert normalize_business_no("123 45 67890").value == "1234567890"

    def test_removes_dots(self) -> None:
        assert normalize_business_no("123.45.67890").value == "1234567890"

    def test_accepts_integer_input(self) -> None:
        """숫자형으로 들어와도 문자열로 정규화됩니다."""
        assert normalize_business_no(1234567890).value == "1234567890"

    def test_accepts_float_input(self) -> None:
        """Excel 에서 실수형으로 넘어오는 경우를 처리합니다."""
        assert normalize_business_no(1234567890.0).value == "1234567890"

    def test_hyphenated_and_plain_are_equal(self) -> None:
        """두 표기가 동일한 값으로 정규화되어야 합니다(PM 요구사항)."""
        assert (
            normalize_business_no("123-45-67890").value
            == normalize_business_no("1234567890").value
        )


class TestNormalizeMissing:
    """값이 없는 경우를 검증합니다."""

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_missing_values(self, value: object) -> None:
        result = normalize_business_no(value)
        assert result.status is BusinessNoStatus.MISSING
        assert result.value is None
        assert not result.is_valid

    def test_separators_only_is_missing(self) -> None:
        """구분자만 있는 값도 누락으로 처리합니다."""
        assert normalize_business_no("--").status is BusinessNoStatus.MISSING


class TestNormalizeInvalidFormat:
    """형식이 잘못된 경우를 검증합니다."""

    def test_too_long(self) -> None:
        result = normalize_business_no("12345678901")
        assert result.status is BusinessNoStatus.INVALID_FORMAT
        assert result.value is None

    @pytest.mark.parametrize("value", ["1", "12345678", "123456789"])
    def test_too_short(self, value: str) -> None:
        """10자리 미만은 **자동 보정하지 않고** 형식 오류로 처리합니다."""
        result = normalize_business_no(value)
        assert result.status is BusinessNoStatus.INVALID_FORMAT
        assert result.value is None

    def test_contains_letters(self) -> None:
        result = normalize_business_no("12345abcde")
        assert result.status is BusinessNoStatus.INVALID_FORMAT
        assert result.warnings

    def test_invalid_format_message_includes_original(self) -> None:
        result = normalize_business_no("12345678901")
        assert "12345678901" in " ".join(result.warnings)


class TestNineDigitIsNotAutoCorrected:
    """9자리 값을 **자동 보정하지 않는지** 검증합니다 (PM 결정).

    사업자등록번호는 핵심 결합 키이므로 근거 없이 값을 만들면 **다른 기업과
    잘못 연결될 위험**이 있습니다. 따라서 보정하지 않고 형식 오류로 처리합니다.
    """

    def test_is_invalid_format(self) -> None:
        result = normalize_business_no("123456789")
        assert result.status is BusinessNoStatus.INVALID_FORMAT
        assert not result.is_valid

    def test_does_not_generate_a_value(self) -> None:
        """앞에 0 을 채운 값을 만들어내지 않습니다."""
        result = normalize_business_no("123456789")
        assert result.value is None
        assert result.value != "0123456789"

    def test_explains_possible_cause(self) -> None:
        """원인을 안내하되 보정하지 않음을 명시합니다."""
        messages = " ".join(normalize_business_no("123456789").warnings)
        assert "앞자리 0" in messages
        assert "자동 보정하지 않으므로" in messages

    def test_original_is_preserved_for_tracing(self) -> None:
        """원본 값은 추적할 수 있도록 보존합니다."""
        result = normalize_business_no("123456789")
        assert result.original == "123456789"

    def test_hyphenated_nine_digit_also_rejected(self) -> None:
        """구분자를 제거한 뒤 9자리여도 동일하게 거부합니다."""
        result = normalize_business_no("123-45-6789")
        assert result.status is BusinessNoStatus.INVALID_FORMAT


class TestChecksum:
    """체크섬은 경고로만 사용합니다(PM 결정 D-002)."""

    def test_valid_checksum(self) -> None:
        assert is_valid_checksum(VALID_CHECKSUM_NO)

    def test_invalid_checksum_is_detected(self) -> None:
        assert not is_valid_checksum("1234567890")

    def test_checksum_failure_does_not_reject(self) -> None:
        """체크섬이 틀려도 값은 그대로 사용합니다(차단하지 않음)."""
        result = normalize_business_no("1234567890")
        assert result.is_valid
        assert result.value == "1234567890"
        assert any("체크섬" in message for message in result.warnings)

    def test_valid_checksum_has_no_warning(self) -> None:
        result = normalize_business_no(VALID_CHECKSUM_NO)
        assert result.is_valid
        assert not result.has_warning

    @pytest.mark.parametrize("value", ["123456789", "12345678901", "abcdefghij"])
    def test_checksum_returns_false_for_malformed(self, value: str) -> None:
        assert not is_valid_checksum(value)


class TestOriginalPreserved:
    """원본 값을 리포트에 남기는지 확인합니다."""

    def test_keeps_original_text(self) -> None:
        assert normalize_business_no("123-45-67890").original == "123-45-67890"
