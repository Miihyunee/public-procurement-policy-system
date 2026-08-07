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

    def test_too_short(self) -> None:
        """8자리 이하는 보정하지 않고 형식 오류로 처리합니다."""
        result = normalize_business_no("12345678")
        assert result.status is BusinessNoStatus.INVALID_FORMAT

    def test_contains_letters(self) -> None:
        result = normalize_business_no("12345abcde")
        assert result.status is BusinessNoStatus.INVALID_FORMAT
        assert result.warnings

    def test_invalid_format_message_includes_original(self) -> None:
        result = normalize_business_no("12345678901")
        assert "12345678901" in " ".join(result.warnings)


class TestNineDigitPadding:
    """Excel 앞자리 0 소실 복구를 검증합니다."""

    def test_pads_to_ten_digits(self) -> None:
        result = normalize_business_no("123456789")
        assert result.value == "0123456789"
        assert result.is_valid

    def test_padding_emits_warning(self) -> None:
        """보정은 하되 확인할 수 있도록 경고를 남깁니다."""
        result = normalize_business_no("123456789")
        assert result.has_warning
        assert any("0 을 채웠습니다" in message for message in result.warnings)


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
