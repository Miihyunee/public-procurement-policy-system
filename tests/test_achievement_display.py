"""
달성률 **표시 전용** 구간 테스트.

가장 중요한 검증은 **기존 상태 체계와 섞이지 않는다**는 점입니다.
기존 ``DashboardStatus.WARNING``(달성률 80~99%)과 표시 기준의 "주의"(40~59%)는
의미가 전혀 다르므로, 두 체계가 서로 침범하지 않아야 합니다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.dashboard.models import DashboardStatus
from procurement.web.achievement_display import (
    DEFAULT_THRESHOLDS,
    LABEL_NOT_CALCULATED,
    LEVEL_CODES,
    LEVEL_LABELS,
    LEVEL_NOT_CALCULATED,
    ThresholdConfigError,
    describe_levels,
    parse_thresholds,
    resolve_level,
)


class TestSeparationFromExistingStatus:
    """기존 상태 체계와의 분리 — 이번 설계의 핵심."""

    def test_level_codes_do_not_collide_with_dashboard_status(self) -> None:
        """코드값이 겹치지 않는다."""
        existing = {status.value for status in DashboardStatus}
        assert existing.isdisjoint(set(LEVEL_CODES))

    def test_not_calculated_code_differs_from_target_rate_not_set(self) -> None:
        """'미계산'과 기존 '목표율 미설정'은 다른 코드다."""
        assert LEVEL_NOT_CALCULATED != DashboardStatus.TARGET_RATE_NOT_SET.value

    def test_warning_label_means_different_range(self) -> None:
        """기존 WARNING(80~99)과 표시 '주의'(40~59)는 다른 구간이다.

        라벨 문자열이 같아 보여도 코드값이 달라 혼동되지 않는다.
        """
        assert DashboardStatus.WARNING.label == "주의"
        display = resolve_level(Decimal("50"))
        assert display.label == "주의"
        assert display.code == "LEVEL_3"
        # 기존 WARNING 구간(80~99)은 표시 기준으로는 '충족 임박'이다.
        assert resolve_level(Decimal("85")).label == "충족 임박"


class TestResolveLevel:
    """구간 판정."""

    @pytest.mark.parametrize(
        ("rate", "code", "label"),
        [
            ("0", "LEVEL_1", "위험"),
            ("19.99", "LEVEL_1", "위험"),
            ("20", "LEVEL_2", "미달"),
            ("39.99", "LEVEL_2", "미달"),
            ("40", "LEVEL_3", "주의"),
            ("60", "LEVEL_4", "적정"),
            ("80", "LEVEL_5", "충족 임박"),
            ("99.99", "LEVEL_5", "충족 임박"),
            ("100", "LEVEL_6", "충족"),
            ("250", "LEVEL_6", "충족"),
        ],
    )
    def test_boundaries(self, rate: str, code: str, label: str) -> None:
        level = resolve_level(Decimal(rate))
        assert level.code == code
        assert level.label == label

    def test_not_calculated_is_not_zero_percent(self) -> None:
        """계산되지 않은 정책을 0% 구간(위험)으로 내려보내지 않는다."""
        level = resolve_level(None)
        assert level.code == LEVEL_NOT_CALCULATED
        assert level.label == LABEL_NOT_CALCULATED
        assert level.index is None

    def test_index_is_ordered(self) -> None:
        assert resolve_level(Decimal("0")).index == 0
        assert resolve_level(Decimal("100")).index == len(LEVEL_CODES) - 1

    def test_custom_thresholds_change_result(self) -> None:
        """경계를 바꾸면 결과가 바뀐다 — 하드코딩되어 있지 않다."""
        custom = parse_thresholds("30,50,70,90,100")
        assert resolve_level(Decimal("25"), custom).code == "LEVEL_1"
        assert resolve_level(Decimal("25")).code == "LEVEL_2"


class TestParseThresholds:
    """설정값 파싱."""

    def test_none_uses_default(self) -> None:
        assert parse_thresholds(None) == DEFAULT_THRESHOLDS

    def test_empty_uses_default(self) -> None:
        assert parse_thresholds("   ") == DEFAULT_THRESHOLDS

    def test_parses_csv(self) -> None:
        assert parse_thresholds("30,50,70,90,100") == (
            Decimal("30"),
            Decimal("50"),
            Decimal("70"),
            Decimal("90"),
            Decimal("100"),
        )

    def test_rejects_wrong_count(self) -> None:
        with pytest.raises(ThresholdConfigError):
            parse_thresholds("20,40,60")

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(ThresholdConfigError):
            parse_thresholds("20,40,육십,80,100")

    def test_rejects_unsorted(self) -> None:
        with pytest.raises(ThresholdConfigError):
            parse_thresholds("20,60,40,80,100")

    def test_rejects_duplicates(self) -> None:
        with pytest.raises(ThresholdConfigError):
            parse_thresholds("20,20,60,80,100")


class TestDescribeLevels:
    """구간표 — 화면이 경계를 하드코딩하지 않도록 서버가 제공."""

    def test_has_six_levels(self) -> None:
        assert len(describe_levels()) == len(LEVEL_CODES)

    def test_ranges_are_contiguous(self) -> None:
        items = describe_levels()
        assert items[0]["min_rate"] == "0"
        assert items[0]["max_rate"] == "19"
        assert items[1]["min_rate"] == "20"

    def test_last_level_has_no_upper_bound(self) -> None:
        assert describe_levels()[-1]["max_rate"] == ""

    def test_labels_match(self) -> None:
        assert [item["label"] for item in describe_levels()] == list(LEVEL_LABELS)


class TestAchievementLevelsApi:
    """``GET /dashboard/achievement-levels``."""

    def test_returns_200(self, tmp_path: object) -> None:
        client = TestClient(create_app())
        assert client.get("/dashboard/achievement-levels").status_code == 200

    def test_returns_six_levels(self) -> None:
        body = TestClient(create_app()).get("/dashboard/achievement-levels").json()
        assert len(body["items"]) == len(LEVEL_CODES)

    def test_notice_says_not_legal_basis(self) -> None:
        """법정 기준이 아님을 응답이 스스로 밝힌다."""
        body = TestClient(create_app()).get("/dashboard/achievement-levels").json()
        assert "법정" in body["notice"]

    def test_exposes_not_calculated_code(self) -> None:
        body = TestClient(create_app()).get("/dashboard/achievement-levels").json()
        assert body["not_calculated_code"] == LEVEL_NOT_CALCULATED

    def test_summary_response_is_unchanged(self) -> None:
        """표시 구간을 추가해도 기존 요약 응답 구조는 그대로다."""
        schema = TestClient(create_app()).get("/openapi.json").json()
        properties = schema["components"]["schemas"]["PolicySummaryResponseModel"][
            "properties"
        ]
        assert "display_level" not in properties
        assert "status" in properties and "status_label" in properties
