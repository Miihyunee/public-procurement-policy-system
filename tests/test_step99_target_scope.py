"""
STEP 99 — 목표비율의 **분모 기준**(scope)과 계산 보류.

이 파일이 지키는 것
===================
1. 여덟 정책의 목표가 **모두 저장된다** — 여성기업의 3%/5% 도 각각(§1 · §2).
2. 저장과 계산은 **다른 일**이다 — 분모를 못 구하면 달성률만 «계산 보류»(§1 중요).
3. ⛔ 분모가 없다고 전체 구매금액으로 바꿔치기하지 않는다(§6 · §18).
4. 기존 여섯 정책의 동작이 **그대로**다(§19).

⛔ 실제 고객 데이터는 쓰지 않습니다. 사업자등록번호·거래처명은 전부 합성값이며,
실데이터 검증을 대신하지 않습니다(§23).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.core.target_scope import (
    CALCULABLE_SCOPES,
    PRODUCIBLE_ITEMS,
    TARGET_SCOPES,
    TOTAL,
    is_calculable,
)
from procurement.dashboard.models import DashboardStatus
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase
from procurement.policy import CONFIRMED_TARGETS, ON_HOLD_REASONS

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아닙니다.
_BNO = "1112233445"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step99.db"
    bootstrap(path)
    return path


@pytest.fixture
def targets(db_path: Path) -> PolicyTargetRepository:
    return PolicyTargetRepository(db_path)


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


# ======================================================================
# §1 · §2  여덟 정책의 목표가 모두, 각각 저장되는가
# ======================================================================
class TestEveryConfirmedTargetIsStored:
    """⛔ 어느 목표도 버리거나 합치지 않았다."""

    def test_all_ten_rows_are_registered(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """8개 정책 · 10행(여성기업이 3행)."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        assert targets.count() == 10

    @pytest.mark.parametrize(
        ("scope", "rate"),
        [(CONSTRUCTION, "3"), (SERVICE, "5"), (GOODS, "5")],
    )
    def test_woman_keeps_each_target_verbatim(
        self, db_path: Path, targets: PolicyTargetRepository, scope: str, rate: str
    ) -> None:
        """공사 3% · 용역 5% · 물품 5% 가 각각 그대로 저장된다.

        ⛔ §2 금지사항 전부에 해당하지 않는다 — 하나를 고르지도, 평균 4% 를
        만들지도, 문자열 ``"3/5"`` 를 넣지도 않았다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        target = targets.get(2026, _policy_id(db_path, "WOMAN"), scope)

        assert target is not None
        assert target.target_rate == Decimal(rate)

    def test_woman_has_no_single_total_target(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """⛔ 「전체 구매금액 기준 여성기업 목표」 를 만들어 내지 않았다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])

        assert targets.get(2026, _policy_id(db_path, "WOMAN"), TOTAL) is None

    def test_no_average_was_taken(self) -> None:
        """⛔ 3 과 5 의 평균 4 는 어디에도 없다."""
        woman = [t for t in CONFIRMED_TARGETS if t.policy_code == "WOMAN"]
        rates = {t.target_rate for t in woman}

        assert rates == {Decimal("3"), Decimal("5")}
        assert Decimal("4") not in rates

    def test_self_support_village_target_is_stored(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """§5 — 7% 자체는 저장한다. 못 하는 것은 달성률뿐이다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        target = targets.get(2026, _policy_id(db_path, "SELF_SUPPORT_VILLAGE"), PRODUCIBLE_ITEMS)

        assert target is not None
        assert target.target_rate == Decimal("7")

    def test_the_thousandths_reading_was_not_changed(self) -> None:
        """§7 — 「1000분의 8」 해석을 이번 STEP 에서 바꾸지 않았다."""
        workplace = next(
            t for t in CONFIRMED_TARGETS if t.policy_code == "DISABLED_STANDARD_WORKPLACE"
        )
        assert workplace.target_rate == Decimal("0.8")
        assert "확인" in workplace.note


# ======================================================================
# §1 중요 · §6 · §18  저장과 계산은 다른 일이다
# ======================================================================
class TestStoringIsNotCalculating:
    """목표가 있다고 달성률이 나오는 것은 아니다."""

    def test_only_total_scope_reaches_the_calculator(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """⭐ 분모를 못 구하는 목표는 계산기에 **가지 않는다.**

        여기가 무너지면 여성기업 3% 가 전체 구매금액 기준으로 계산되어 틀린
        달성률이 화면에 나간다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        reaching = targets.rates_by_policy_id(2026)

        assert _policy_id(db_path, "WOMAN") not in reaching
        assert _policy_id(db_path, "SELF_SUPPORT_VILLAGE") not in reaching
        assert len(reaching) == 6

    def test_the_on_hold_policies_are_named(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])

        assert targets.on_hold_policy_ids(2026) == {
            _policy_id(db_path, "WOMAN"),
            _policy_id(db_path, "SELF_SUPPORT_VILLAGE"),
        }

    def test_only_total_is_calculable_for_now(self) -> None:
        """⛔ 분모를 구하는 코드가 없는데 계산 가능 목록을 넓히지 않았다."""
        assert CALCULABLE_SCOPES == {TOTAL}
        assert not is_calculable(CONSTRUCTION)
        assert not is_calculable(PRODUCIBLE_ITEMS)

    def test_the_reasons_are_recorded(self) -> None:
        """왜 못 내는지가 적혀 있다 — 조용히 비어 있지 않다."""
        assert set(ON_HOLD_REASONS) == {"WOMAN", "SELF_SUPPORT_VILLAGE"}
        assert "구매유형" in ON_HOLD_REASONS["WOMAN"]
        assert "생산가능품목" in ON_HOLD_REASONS["SELF_SUPPORT_VILLAGE"]


# ======================================================================
# §12 · §16  화면이 세 가지 «못 낸다» 를 구분하는가
# ======================================================================
class TestTheDashboardTellsTheStatesApart:
    """«기업정보 미등록» · «목표율 미설정» · «계산 보류» 는 서로 다른 말이다."""

    def test_on_hold_shows_performance_but_no_rate(self, db_path: Path) -> None:
        """§16 — 여성기업은 실적까지 보여 주고 달성률만 «계산 보류»."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _register(db_path, "WOMAN")
        _purchase(db_path, Decimal("1000000"))

        item = _policy_item(db_path, "WOMAN")

        assert item["purchase_amount"] == "1000000"
        assert item["achievement_rate"] is None
        assert item["status_label"] == "계산 보류"

    def test_on_hold_differs_from_target_not_set(self, db_path: Path) -> None:
        """⭐ 목표를 등록하지 않은 정책은 «목표율 미설정» 이다 — 다른 말이다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _register(db_path, "WOMAN")
        _register(db_path, "SMALL_BUSINESS")
        PolicyTargetRepository(db_path).delete(2026, _policy_id(db_path, "SMALL_BUSINESS"))
        _purchase(db_path, Decimal("1000000"))

        assert _policy_item(db_path, "WOMAN")["status_label"] == "계산 보류"
        assert _policy_item(db_path, "SMALL_BUSINESS")["status_label"] == "목표율 미설정"

    def test_unregistered_is_still_unknown(self, db_path: Path) -> None:
        """§12 — 기업정보를 못 받은 정책은 여전히 실적까지 ``null`` 이다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _register(db_path, "SMALL_BUSINESS")
        _purchase(db_path, Decimal("1000000"))

        item = _policy_item(db_path, "SOCIAL_ENTERPRISE")

        assert item["purchase_amount"] is None
        assert item["status_label"] == "기업정보 미등록"

    def test_the_three_states_are_distinct_values(self) -> None:
        labels = {
            DashboardStatus.COMPANY_DATA_NOT_REGISTERED.label,
            DashboardStatus.TARGET_RATE_NOT_SET.label,
            DashboardStatus.CALCULATION_ON_HOLD.label,
        }
        assert labels == {"기업정보 미등록", "목표율 미설정", "계산 보류"}

    def test_on_hold_is_never_a_judgement(self) -> None:
        """⛔ 달성률 판정이 이 상태를 내지 않는다 — 새 판정 등급이 아니다."""
        for rate in (Decimal("0"), Decimal("50"), Decimal("100")):
            assert (
                DashboardStatus.from_achievement_rate(rate)
                is not DashboardStatus.CALCULATION_ON_HOLD
            )


# ======================================================================
# §19  기존 여섯 정책과 기존 API 가 그대로인가
# ======================================================================
class TestTheExistingSixAreUnaffected:
    """⛔ 구조를 넓히면서 기존 동작을 흔들지 않았다."""

    def test_a_normal_policy_still_calculates(self, db_path: Path) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _register(db_path, "SMALL_BUSINESS")
        _purchase(db_path, Decimal("1000000"))

        item = _policy_item(db_path, "SMALL_BUSINESS")

        assert item["target_rate"] == "50"
        assert item["achievement_rate"] == "200.00"
        assert item["status_label"] == "정상"

    def test_upsert_without_a_scope_still_means_total(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """기존 호출부는 인자를 주지 않아도 예전과 같은 행을 쓴다."""
        small = _policy_id(db_path, "SMALL_BUSINESS")
        targets.upsert(2026, small, Decimal("50"))

        stored = targets.get(2026, small)
        assert stored is not None
        assert stored.scope == TOTAL

    def test_delete_only_removes_the_named_scope(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """⛔ 공사 목표를 지운다고 용역·물품 목표가 함께 사라지지 않는다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        woman = _policy_id(db_path, "WOMAN")

        assert targets.delete(2026, woman, CONSTRUCTION) is True

        remaining = {t.scope for t in targets.list_for_policy(2026, woman)}
        assert remaining == {SERVICE, GOODS}

    def test_the_admin_api_shows_every_target(self, db_path: Path) -> None:
        """⛔ 여성기업의 세 목표 중 하나만 보여 주지 않는다(§2 금지)."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        client = TestClient(create_app(db_path))
        body = client.get("/policy-targets?year=2026").json()

        woman = next(item for item in body["items"] if item["policy_code"] == "WOMAN")
        rates = {(s["scope"], s["target_rate"]) for s in woman["scoped_targets"]}

        assert rates == {(CONSTRUCTION, "3"), (SERVICE, "5"), (GOODS, "5")}
        # 전체 구매금액 기준 목표는 없으므로 기존 필드는 null 이다.
        assert woman["target_rate"] is None

    def test_a_normal_policy_reports_one_scoped_target(self, db_path: Path) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        client = TestClient(create_app(db_path))
        body = client.get("/policy-targets?year=2026").json()

        small = next(item for item in body["items"] if item["policy_code"] == "SMALL_BUSINESS")

        assert small["target_rate"] == "50"
        assert [(s["scope"], s["target_rate"]) for s in small["scoped_targets"]] == [(TOTAL, "50")]


# ======================================================================
# §3-1 · §3-3  독립성은 그대로인가
# ======================================================================
class TestIndependenceSurvivedTheChange:
    """축이 하나 늘어도 서로 간섭하지 않는다."""

    def test_years_stay_independent(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        small = _policy_id(db_path, "SMALL_BUSINESS")
        targets.upsert(2025, small, Decimal("40"))

        assert targets.rates_by_policy_id(2025)[small] == Decimal("40")
        assert targets.rates_by_policy_id(2026)[small] == Decimal("50")

    def test_policies_stay_independent(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        startup = _policy_id(db_path, "STARTUP")

        before = dict(targets.rates_by_policy_id(2026))
        targets.upsert(2026, startup, Decimal("9.9"))
        after = targets.rates_by_policy_id(2026)

        assert {k: v for k, v in after.items() if k != startup} == {
            k: v for k, v in before.items() if k != startup
        }

    def test_zero_is_still_rejected(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        """§20 — 기존 validation 을 바꾸지 않았다."""
        with pytest.raises(PolicyValidationError):
            targets.upsert(2026, _policy_id(db_path, "SMALL_BUSINESS"), Decimal("0"))

    def test_an_unknown_scope_is_rejected(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """⛔ 아무 문자열이나 분모 기준으로 들어오지 못한다."""
        with pytest.raises(PolicyValidationError):
            targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("3"), "WHATEVER")

    def test_the_scope_vocabulary_is_closed(self) -> None:
        assert TARGET_SCOPES == {TOTAL, CONSTRUCTION, SERVICE, GOODS, PRODUCIBLE_ITEMS}


# ======================================================================
# §19  옛 DB 마이그레이션
# ======================================================================
class TestAnOlderDatabaseMigrates:
    """STEP 99 이전 DB 에 저장된 목표를 잃지 않는다."""

    def test_existing_rows_become_total_scope(self, tmp_path: Path) -> None:
        """⛔ 행을 지우지 않고 컬럼만 덧붙인다."""
        path = tmp_path / "old.db"
        bootstrap(path)
        small = _policy_id(path, "SMALL_BUSINESS")

        # STEP 99 이전 모양으로 되돌린다 — scope 컬럼이 없는 테이블.
        now = datetime.now().isoformat(sep=" ")
        with sqlite3.connect(str(path)) as conn:
            conn.execute("DROP TABLE policy_target")
            conn.execute(
                "CREATE TABLE policy_target ("
                "policy_target_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, "
                "policy_id INTEGER NOT NULL, target_rate TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "UNIQUE (year, policy_id))"
            )
            conn.execute(
                "INSERT INTO policy_target (year, policy_id, target_rate, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (2026, small, "50", now, now),
            )

        repository = PolicyTargetRepository(path)
        repository.create_table()

        stored = repository.get(2026, small)
        assert stored is not None
        assert stored.target_rate == Decimal("50")
        assert stored.scope == TOTAL

    def test_the_migrated_table_accepts_multiple_scopes(self, tmp_path: Path) -> None:
        """옛 ``UNIQUE (year, policy_id)`` 가 남아 여성기업을 막지 않는다."""
        path = tmp_path / "old2.db"
        bootstrap(path)
        now = datetime.now().isoformat(sep=" ")
        with sqlite3.connect(str(path)) as conn:
            conn.execute("DROP TABLE policy_target")
            conn.execute(
                "CREATE TABLE policy_target ("
                "policy_target_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, "
                "policy_id INTEGER NOT NULL, target_rate TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "UNIQUE (year, policy_id))"
            )
            conn.execute(
                "INSERT INTO policy_target (year, policy_id, target_rate, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (2026, _policy_id(path, "SMALL_BUSINESS"), "50", now, now),
            )

        repository = PolicyTargetRepository(path)
        repository.create_table()
        woman = _policy_id(path, "WOMAN")
        repository.upsert(2026, woman, Decimal("3"), CONSTRUCTION)
        repository.upsert(2026, woman, Decimal("5"), SERVICE)

        assert len(repository.list_for_policy(2026, woman)) == 2


# ======================================================================
# §18  하지 않기로 한 것
# ======================================================================
class TestForbidden:
    """⛔ 금지 목록."""

    def test_no_purchase_type_classifier_exists(self) -> None:
        """⛔ 적요·거래처명으로 구매유형을 정하는 코드가 없다(§3 · §18)."""
        src = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for area in ("calculators", "dashboard"):
            for path in (src / area).rglob("*.py"):
                assert "CONSTRUCTION" not in path.read_text(encoding="utf-8"), path.name

    def test_the_calculator_denominator_is_unchanged(self) -> None:
        """⛔ §19 — 계산기를 건드리지 않았다."""
        from procurement.calculators import ProcurementAchievementCalculator

        names = {n for n in dir(ProcurementAchievementCalculator) if n.startswith("calculate")}
        assert names == {
            "calculate_total_purchase",
            "calculate_policy_purchase",
            "calculate_achievement",
            "calculate_all",
        }

    def test_no_producible_item_guessing(self) -> None:
        """⛔ §6 — 품목을 추정하는 코드를 넣지 않았다."""
        src = Path(__file__).resolve().parents[1] / "src" / "procurement"
        hits = [
            path.name
            for path in src.rglob("*.py")
            if "생산가능품목" in path.read_text(encoding="utf-8")
            and path.parent.name in {"calculators", "matchers"}
        ]
        assert hits == []


# ======================================================================
# 보조 — 합성 데이터
# ======================================================================
def _register(db_path: Path, policy_code: str) -> None:
    repository = CompanyRepository(db_path)
    company = repository.find_by_business_no(_BNO)
    if company is None:
        company = repository.insert(
            Company(business_no=_BNO, company_name="가나상사", representative_name="홍길동")
        )
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db_path, policy_code),
            valid_from=date(2025, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )


def _purchase(db_path: Path, amount: Decimal) -> None:
    PurchaseRepository(db_path).insert(
        Purchase(
            business_no=_BNO,
            company_name="가나상사",
            amount=amount,
            payment_date=date(2026, 3, 5),
            contract_date=date(2026, 3, 5),
            resolution_date=date(2026, 3, 5),
        )
    )


def _policy_item(db_path: Path, policy_code: str) -> dict[str, object]:
    client = TestClient(create_app(db_path))
    client.post("/purchases/rematch")
    body = client.get("/dashboard/summary?year=2026").json()
    return next(item for item in body["policies"] if item["policy_code"] == policy_code)
