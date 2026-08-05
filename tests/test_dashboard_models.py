"""
대시보드 요약 모델/상태 단위 테스트.

DB 없이 순수 값만으로 상태 판정과 DTO 구조를 검증합니다.
"""

from __future__ import annotations

from decimal import Decimal

from procurement.dashboard import DashboardStatus, DashboardSummary, PolicySummary


class TestDashboardStatusFromRate:
    """달성률 기준 상태 판정(정상 ≥100 / 주의 80~ / 부족 <80)을 검증합니다."""

    def test_at_100_is_normal(self) -> None:
        assert DashboardStatus.from_achievement_rate(Decimal("100")) is DashboardStatus.NORMAL

    def test_above_100_is_normal(self) -> None:
        assert DashboardStatus.from_achievement_rate(Decimal("250.5")) is DashboardStatus.NORMAL

    def test_just_below_100_is_warning(self) -> None:
        assert DashboardStatus.from_achievement_rate(Decimal("99.99")) is DashboardStatus.WARNING

    def test_at_80_is_warning(self) -> None:
        """80 은 주의 구간에 포함(경계 포함)됩니다."""
        assert DashboardStatus.from_achievement_rate(Decimal("80")) is DashboardStatus.WARNING

    def test_just_below_80_is_shortage(self) -> None:
        assert DashboardStatus.from_achievement_rate(Decimal("79.99")) is DashboardStatus.SHORTAGE

    def test_zero_is_shortage(self) -> None:
        assert DashboardStatus.from_achievement_rate(Decimal("0")) is DashboardStatus.SHORTAGE


class TestDashboardStatusLabel:
    """상태의 한글 표시명을 검증합니다."""

    def test_labels(self) -> None:
        assert DashboardStatus.NORMAL.label == "정상"
        assert DashboardStatus.WARNING.label == "주의"
        assert DashboardStatus.SHORTAGE.label == "부족"


class TestDtoStructure:
    """DTO 가 값을 그대로 보관하는지 확인합니다."""

    def test_policy_summary_holds_values(self) -> None:
        summary = PolicySummary(
            policy_id=1,
            policy_code="SMALL_BUSINESS",
            policy_name="중소기업",
            purchase_amount=Decimal("3000000"),
            total_purchase_amount=Decimal("10000000"),
            target_rate=Decimal("50"),
            achievement_rate=Decimal("60.00"),
            shortage_rate=Decimal("40.00"),
            status=DashboardStatus.SHORTAGE,
        )
        assert summary.policy_code == "SMALL_BUSINESS"
        assert summary.target_rate == Decimal("50")
        assert summary.status is DashboardStatus.SHORTAGE

    def test_dashboard_summary_holds_list(self) -> None:
        summary = DashboardSummary(total_purchase_amount=Decimal("0"), policy_summaries=[])
        assert summary.total_purchase_amount == Decimal("0")
        assert summary.policy_summaries == []
