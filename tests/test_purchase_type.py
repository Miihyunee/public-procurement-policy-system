"""
tests.test_purchase_type

구매유형 값과 **고객 확정 예산과목 매핑**을 검증합니다.

이 파일의 핵심 목적은 하나입니다.

    **고객이 확정하지 않은 예산과목을 자동으로 분류하지 않는다.**

확정된 것은 3건뿐이고(``docs/DECISIONS.md`` §0.5.3), 나머지를 추측으로 채우면
여성기업 달성률이 조용히 왜곡됩니다. 아래 테스트는 나중에 누군가 "도서가 들어가면
물품" 같은 부분 문자열 규칙을 넣으면 **반드시 깨지도록** 만들었습니다.
"""

from __future__ import annotations

import pytest

from procurement.core.purchase_type import (
    CONFIRMED_BUDGET_ACCOUNT_TYPES,
    CONSTRUCTION,
    GOODS,
    PURCHASE_TYPE_LABELS,
    PURCHASE_TYPES,
    SERVICE,
    classify_budget_account,
    is_valid_purchase_type,
)


class TestConfirmedMapping:
    """고객이 확정한 3건만 분류된다."""

    @pytest.mark.parametrize(
        ("budget_account", "expected"),
        [
            ("도서인쇄비", GOODS),
            ("소모성물품구입비", GOODS),
            ("임차료", SERVICE),
        ],
    )
    def test_confirmed_accounts_are_classified(
        self, budget_account: str, expected: str
    ) -> None:
        """2026-08-14 고객 확정 매핑 3건."""
        assert classify_budget_account(budget_account) == expected

    def test_exactly_three_confirmed_entries(self) -> None:
        """확정 매핑은 **정확히 3건**이다.

        항목이 늘어났다면 고객 확인을 거친 것인지 확인해야 합니다.
        """
        assert dict(CONFIRMED_BUDGET_ACCOUNT_TYPES) == {
            "도서인쇄비": GOODS,
            "소모성물품구입비": GOODS,
            "임차료": SERVICE,
        }

    def test_mapping_is_read_only(self) -> None:
        """매핑표를 실행 중에 바꿀 수 없다."""
        with pytest.raises(TypeError):
            CONFIRMED_BUDGET_ACCOUNT_TYPES["외주용역비"] = SERVICE  # type: ignore[index]


class TestUnconfirmedAccountsAreNotClassified:
    """⛔ 고객이 확정하지 않은 예산과목은 분류하지 않는다."""

    @pytest.mark.parametrize(
        "budget_account",
        [
            "외주용역비",  # 샘플 매입 금액 최대(약 42.4억). 확정되지 않았다
            "통신비",
            "수도광열비",
            "각종수수료",
            "행사운영비",
            "차량유지비",
            "자산취득비",
            "시설장비유지비",
            "교육훈련비",
            "부대경비",
            "광고료",
            "수선비",
            "국내여비",
            "의료비",
        ],
    )
    def test_unconfirmed_budget_accounts_return_none(self, budget_account: str) -> None:
        """샘플에 실제로 존재하지만 **확정되지 않은** 예산과목은 미분류다."""
        assert classify_budget_account(budget_account) is None

    @pytest.mark.parametrize(
        "budget_account",
        ["낙동강유역환경청", "기후에너지환경부"],
    )
    def test_agency_names_in_budget_account_are_not_classified(
        self, budget_account: str
    ) -> None:
        """예산과목 자리에 기관명이 들어간 값도 추측하지 않는다.

        샘플에서 실제로 관찰된 값입니다(데이터 오류인지도 확인 대상).
        """
        assert classify_budget_account(budget_account) is None

    @pytest.mark.parametrize("budget_account", [None, "", "   "])
    def test_missing_budget_account_is_unclassified(
        self, budget_account: str | None
    ) -> None:
        """결측값은 미분류다. 샘플 매입행의 15.4% 가 여기에 해당한다."""
        assert classify_budget_account(budget_account) is None


class TestNoSubstringMatching:
    """⛔ 부분 문자열 규칙을 만들지 않는다.

    "도서가 들어가면 물품", "임대가 들어가면 용역" 같은 확장은 고객 확정 범위를
    벗어납니다. 그런 규칙이 들어오면 아래 테스트가 깨집니다.
    """

    @pytest.mark.parametrize(
        "budget_account",
        [
            "도서구입비",  # '도서' 포함이지만 확정 항목이 아니다
            "인쇄비",
            "도서인쇄",  # 뒤의 '비' 가 없다
            "도서인쇄비용",  # 확정 항목의 접두사이지만 다른 값이다
            "임대료",  # '임차료' 와 다른 값이다
            "차량임차료",
            "소모품비",
            "물품구입비",
        ],
    )
    def test_partial_matches_are_not_classified(self, budget_account: str) -> None:
        """확정 항목과 **완전히 같지 않으면** 분류하지 않는다."""
        assert classify_budget_account(budget_account) is None

    def test_whitespace_is_trimmed_but_value_must_match_exactly(self) -> None:
        """앞뒤 공백만 정리하며, 값 자체는 완전 일치해야 한다."""
        assert classify_budget_account("  임차료  ") == SERVICE
        assert classify_budget_account("임 차 료") is None


class TestPurchaseTypeValues:
    """구매유형 값 정의."""

    def test_three_types_are_defined(self) -> None:
        assert PURCHASE_TYPES == {CONSTRUCTION, SERVICE, GOODS}

    def test_labels_cover_every_type(self) -> None:
        assert set(PURCHASE_TYPE_LABELS) == PURCHASE_TYPES
        assert PURCHASE_TYPE_LABELS[CONSTRUCTION] == "공사"
        assert PURCHASE_TYPE_LABELS[SERVICE] == "용역"
        assert PURCHASE_TYPE_LABELS[GOODS] == "물품"

    def test_none_is_a_valid_state(self) -> None:
        """``None`` 은 오류가 아니라 '아직 확인되지 않음' 이다."""
        assert is_valid_purchase_type(None) is True

    @pytest.mark.parametrize("value", [CONSTRUCTION, SERVICE, GOODS])
    def test_defined_types_are_valid(self, value: str) -> None:
        assert is_valid_purchase_type(value) is True

    @pytest.mark.parametrize("value", ["물품", "goods", "ETC", "UNKNOWN", ""])
    def test_other_values_are_rejected(self, value: str) -> None:
        assert is_valid_purchase_type(value) is False

    def test_classified_results_are_always_valid_types(self) -> None:
        """분류 결과는 항상 허용 값이거나 ``None`` 이다."""
        for account in CONFIRMED_BUDGET_ACCOUNT_TYPES:
            assert is_valid_purchase_type(classify_budget_account(account))


class TestNotWiredIntoCalculation:
    """이 모듈은 아직 **계산에 연결되지 않았다**.

    여성기업 공사 3% / 용역·물품 5% 판정은 Calculator 구조 변경이 필요하며,
    PM 승인 전까지 진행하지 않습니다. 이 테스트는 그 경계를 명시합니다.
    """

    def test_purchase_model_has_no_purchase_type_field_yet(self) -> None:
        """``Purchase`` 에 구매유형 필드를 아직 추가하지 않았다.

        필드 추가는 스키마 변경을 동반하므로 PM 승인 대상입니다. 승인 후 이 테스트를
        **삭제하고** 실제 필드 테스트로 대체합니다.
        """
        import dataclasses

        from procurement.models import Purchase

        field_names = {f.name for f in dataclasses.fields(Purchase)}
        assert "purchase_type" not in field_names
