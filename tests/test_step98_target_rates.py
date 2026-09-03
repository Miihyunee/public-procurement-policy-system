"""
STEP 98 — 고객 확정 목표비율 등록과 그 한계.

이 파일이 지키는 것은 두 가지입니다.

1. **확정된 값이 그대로 저장된다** — 반올림·보정 없이(§2, §20).
2. **틀린 달성률을 내지 않는다** — 여성기업과 국가유공자자활용사촌은 분모를
   구할 수 없으므로 달성률을 계산하지 않는다(§2 중요, §13, §14).

⚠️ **2026-09-03 · STEP 99 로 바뀐 것.** 이 파일을 처음 쓸 때는 두 정책의 목표를
**저장조차 하지 않았다** — 단일 ``target_rate`` 로는 담을 수 없었기 때문이다.
STEP 99 §2 에서 목표비율에 **분모 기준(scope)** 축이 생기면서 여덟 정책의 목표를
모두 저장할 수 있게 되었다(§0.25). 지키려던 것 — *분모 없이 달성률을 지어내지
않는다* — 은 그대로이며, 경계가 «저장하지 않는다» 에서 «계산하지 않는다» 로
옮겨졌다. 그 경계는 :data:`~procurement.core.target_scope.CALCULABLE_SCOPES` 다.

⛔ 실제 고객 데이터는 쓰지 않습니다. 여기의 사업자등록번호·거래처명은 전부
합성값이며, 실데이터 검증을 대신하지 않습니다(§17).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.calculators import ProcurementAchievementCalculator
from procurement.database.bootstrap import MVP_POLICY_SEEDS, bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase
from procurement.policy import (
    CONFIRMED_TARGETS,
    ON_HOLD_REASONS,
    STORABLE_TARGET_RATES,
)

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아닙니다.
_BNO_A = "1112233445"
_BNO_B = "9998877665"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step98.db"
    bootstrap(path)
    return path


@pytest.fixture
def policy_repo(db_path: Path) -> PolicyRepository:
    return PolicyRepository(db_path)


def _policy_id(repo: PolicyRepository, code: str) -> int:
    policy = repo.find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


# ======================================================================
# §2  확정된 목표비율이 그대로 기록되었는가
# ======================================================================
class TestTheConfirmedRatesAreRecordedVerbatim:
    """⛔ 고객이 말한 숫자를 바꾸지 않았다."""

    @pytest.mark.parametrize(
        ("code", "rate"),
        [
            ("SMALL_BUSINESS", "50"),
            ("STARTUP", "3.4"),
            ("SOCIAL_ENTERPRISE", "3"),
            ("SOCIAL_COOPERATIVE", "0.1"),
            ("DISABLED", "1"),
            ("DISABLED_STANDARD_WORKPLACE", "0.8"),
        ],
    )
    def test_the_rate_matches_the_customer_value(self, code: str, rate: str) -> None:
        assert STORABLE_TARGET_RATES[code] == Decimal(rate)

    def test_no_rate_was_rounded(self) -> None:
        """⛔ §20 — 3.4 를 3 으로, 0.1 을 0 으로 줄이지 않았다."""
        assert STORABLE_TARGET_RATES["STARTUP"] == Decimal("3.4")
        assert STORABLE_TARGET_RATES["SOCIAL_COOPERATIVE"] == Decimal("0.1")
        assert STORABLE_TARGET_RATES["DISABLED_STANDARD_WORKPLACE"] == Decimal("0.8")

    def test_the_thousandths_notation_is_read_as_zero_point_eight(self) -> None:
        """「1000분의 8」 = 0.8% — 지시서 §3-1 예시와 같은 값으로 읽었다."""
        assert Decimal("8") / Decimal("1000") * Decimal("100") == Decimal("0.8")
        assert STORABLE_TARGET_RATES["DISABLED_STANDARD_WORKPLACE"] == Decimal("0.8")

    def test_every_confirmed_policy_is_covered(self) -> None:
        """8개 정책이 계산 가능/보류 어느 쪽으로든 **빠짐없이** 분류되었다."""
        covered = set(STORABLE_TARGET_RATES) | set(ON_HOLD_REASONS)
        active = {seed.policy_code for seed in MVP_POLICY_SEEDS if seed.is_active}

        assert covered == active
        assert len(covered) == 8


# ======================================================================
# §2 중요 · §13 · §14  담을 수 없는 것은 담지 않았는가
# ======================================================================
class TestTheUnrepresentableTargetsWereNotInvented:
    """⛔ 분모 없이 달성률을 지어내지 않았다.

    .. note::
        **기대값이 바뀐 이유** — STEP 99 §2 로 분모 기준 축이 생겨 두 정책의
        목표도 **저장**된다(§0.25). 그래서 "저장되지 않았다" 대신 "계산에
        쓰이지 않는다" 를 지킨다. 모듈 docstring 참조.
    """

    @pytest.mark.parametrize("code", ["WOMAN", "SELF_SUPPORT_VILLAGE"])
    def test_the_rate_never_reaches_the_calculator(self, code: str) -> None:
        assert code not in STORABLE_TARGET_RATES
        assert code in ON_HOLD_REASONS

    @pytest.mark.parametrize("code", ["WOMAN", "SELF_SUPPORT_VILLAGE"])
    def test_the_reason_is_written_down(self, code: str) -> None:
        """조용히 빠진 것이 아니라 **이유가 남아 있다.**"""
        assert len(ON_HOLD_REASONS[code]) > 30

    def test_woman_keeps_both_customer_targets(self) -> None:
        """공사 3% · 용역·물품 5% 를 한쪽만 적고 버리지 않았다."""
        woman = [t for t in CONFIRMED_TARGETS if t.policy_code == "WOMAN"]

        assert {t.target_rate for t in woman} == {Decimal("3"), Decimal("5")}
        assert all(not t.calculable for t in woman)

    def test_self_support_village_keeps_its_own_denominator(self) -> None:
        """분모가 전체 구매금액이 아님을 기록했다 — 7%×전체로 계산하지 않는다."""
        village = next(t for t in CONFIRMED_TARGETS if t.policy_code == "SELF_SUPPORT_VILLAGE")

        assert village.target_rate == Decimal("7")
        assert village.scope == "PRODUCIBLE_ITEMS"
        assert not village.calculable

    def test_the_calculator_still_has_only_one_denominator(self) -> None:
        """⛔ §20 — 특수 분모를 만들려고 계산기를 건드리지 않았다.

        계산기가 분모를 구하는 경로는 여전히 ``calculate_total_purchase`` 하나다.
        구매유형별·품목별 분모가 생겼다면 이 시험이 실패해야 한다.
        """
        methods = {
            name for name in dir(ProcurementAchievementCalculator) if name.startswith("calculate")
        }
        assert methods == {
            "calculate_total_purchase",
            "calculate_policy_purchase",
            "calculate_achievement",
            "calculate_all",
        }


# ======================================================================
# §13  여성기업 — 구매유형을 자동 분류하지 않았는가
# ======================================================================
class TestPurchaseTypeIsNotDecidedAutomatically:
    """⛔ 적요·거래처명을 보고 공사/용역/물품을 자동 판정하지 않는다."""

    def test_the_purchase_row_carries_no_purchase_type(self) -> None:
        """지출 원본 자체에는 구매유형 칸이 없다 — 그래서 자동 판정도 못 한다."""
        assert not hasattr(
            Purchase(business_no=_BNO_A, company_name="가", amount=Decimal("1")), "purchase_type"
        )

    def test_the_type_is_a_reviewer_decision(self) -> None:
        """구매유형은 담당자가 검토 화면에서 확정하는 값이다(``final_purchase_type``)."""
        from procurement.database import review_repository

        source = Path(review_repository.__file__).read_text(encoding="utf-8")
        assert "final_purchase_type" in source

    def test_no_keyword_classifier_was_added(self) -> None:
        """⛔ 「공사」 같은 낱말로 유형을 정하는 코드를 넣지 않았다."""
        src = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for area in ("calculators", "dashboard"):
            for path in (src / area).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                assert "CONSTRUCTION" not in text, path.name


# ======================================================================
# §3  저장 구조 — 정책·연도 독립성
# ======================================================================
class TestTargetsAreIndependent:
    """한 칸을 고쳐도 옆 칸이 흔들리지 않아야 한다."""

    def test_changing_one_policy_leaves_the_others_alone(
        self, db_path: Path, policy_repo: PolicyRepository
    ) -> None:
        """§3-1 — 창업기업만 바꿨을 때 나머지 다섯은 그대로다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        repository = PolicyTargetRepository(db_path)
        startup = _policy_id(policy_repo, "STARTUP")

        before = dict(repository.rates_by_policy_id(2026))
        repository.upsert(2026, startup, Decimal("9.9"))
        after = repository.rates_by_policy_id(2026)

        assert after[startup] == Decimal("9.9")
        assert {pid: rate for pid, rate in after.items() if pid != startup} == {
            pid: rate for pid, rate in before.items() if pid != startup
        }

    def test_years_do_not_bleed_into_each_other(
        self, db_path: Path, policy_repo: PolicyRepository
    ) -> None:
        """§3-3 — 2025 년 값을 넣어도 2026 년은 그대로다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        repository = PolicyTargetRepository(db_path)
        small = _policy_id(policy_repo, "SMALL_BUSINESS")

        repository.upsert(2025, small, Decimal("40"))

        assert repository.rates_by_policy_id(2025)[small] == Decimal("40")
        assert repository.rates_by_policy_id(2026)[small] == Decimal("50")

    def test_a_year_with_no_targets_stays_empty(self, db_path: Path) -> None:
        """⛔ 2026 년에 등록했다고 2024 년이 저절로 채워지지 않는다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])

        assert PolicyTargetRepository(db_path).rates_by_policy_id(2024) == {}

    def test_zero_is_rejected_by_the_existing_rule(
        self, db_path: Path, policy_repo: PolicyRepository
    ) -> None:
        """§3-2 — 목표비율 0 은 기존 validation 이 거부한다. ⛔ 규칙을 바꾸지 않았다."""
        repository = PolicyTargetRepository(db_path)
        small = _policy_id(policy_repo, "SMALL_BUSINESS")

        with pytest.raises(PolicyValidationError):
            repository.upsert(2026, small, Decimal("0"))


# ======================================================================
# §3-2  목표 미설정과 0% 는 다르다
# ======================================================================
class TestUnsetIsNotZero:
    """목표가 없다는 것과 목표가 0 이라는 것은 다른 말이다."""

    def test_an_unset_policy_still_reports_its_performance(self, db_path: Path) -> None:
        """§0.23 — 목표가 없어도 실적은 센다. ⛔ 실적을 0 으로 만들지 않는다."""
        _register_company(db_path, "WOMAN", _BNO_A)
        _add_purchase(db_path, _BNO_A, Decimal("1000000"), date(2026, 3, 5))

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")
        woman = _policy_item(client, "WOMAN")

        assert woman["target_rate"] is None
        assert woman["purchase_amount"] == "1000000"
        assert woman["achievement_rate"] is None
        assert woman["status_label"] == "목표율 미설정"


# ======================================================================
# §8  조회불가는 0 원이 아니다
# ======================================================================
class TestNotRegisteredMeansUnknown:
    """기업정보를 받은 적이 없으면 «모른다» 고 말한다."""

    def test_an_unregistered_policy_reports_null_not_zero(self, db_path: Path) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _register_company(db_path, "SMALL_BUSINESS", _BNO_A)
        _add_purchase(db_path, _BNO_A, Decimal("1000000"), date(2026, 3, 5))

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")
        social = _policy_item(client, "SOCIAL_ENTERPRISE")

        assert social["purchase_amount"] is None
        assert social["achievement_rate"] is None
        assert social["status_label"] == "기업정보 미등록"

    def test_a_registered_policy_with_no_match_reports_zero(self, db_path: Path) -> None:
        """⛔ 반대쪽도 확인한다 — 등록했는데 아무도 안 걸리면 0 원이다(NULL 아님)."""
        _register_company(db_path, "SMALL_BUSINESS", _BNO_A)
        _add_purchase(db_path, _BNO_B, Decimal("500000"), date(2026, 3, 5))

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")

        assert _policy_item(client, "SMALL_BUSINESS")["purchase_amount"] == "0"


# ======================================================================
# §9  정책 간 중복 매칭은 정상 동작이다
# ======================================================================
class TestOnePurchaseCountsForEveryPolicyItQualifiesFor:
    """정책 대상 집합은 서로 독립이다 — 한쪽에서 다른 쪽을 빼지 않는다."""

    def test_the_same_spend_lands_in_three_policies(self, db_path: Path) -> None:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        for code in ("SMALL_BUSINESS", "STARTUP", "WOMAN"):
            _register_company(db_path, code, _BNO_A)
        _add_purchase(db_path, _BNO_A, Decimal("1000000"), date(2026, 3, 5))

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")

        for code in ("SMALL_BUSINESS", "STARTUP", "WOMAN"):
            assert _policy_item(client, code)["purchase_amount"] == "1000000", code

    def test_the_policy_sum_may_exceed_the_total(self, db_path: Path) -> None:
        """⛔ 합이 전체보다 커지는 것은 오류가 아니다 — 고치지 않는다."""
        for code in ("SMALL_BUSINESS", "STARTUP", "WOMAN"):
            _register_company(db_path, code, _BNO_A)
        _add_purchase(db_path, _BNO_A, Decimal("1000000"), date(2026, 3, 5))

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")
        body = client.get("/dashboard/summary?year=2026").json()

        matched = sum(
            Decimal(item["purchase_amount"])
            for item in body["policies"]
            if item["purchase_amount"] is not None
        )
        assert matched == Decimal("3000000")
        assert matched > Decimal(body["total_purchase_amount"])


# ======================================================================
# §10  연도 귀속은 결의일자로 결정된다
# ======================================================================
class TestTheYearComesFromTheResolutionDate:
    """⛔ 신고기준일로 되돌리지 않는다."""

    def test_a_row_straddling_the_year_boundary_belongs_to_the_resolution_year(
        self, db_path: Path
    ) -> None:
        """신고기준일 2026-01-05 · 결의일자 2025-12-28 → **2025 년** 실적."""
        _register_company(db_path, "SMALL_BUSINESS", _BNO_A)
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=_BNO_A,
                company_name="가나상사",
                amount=Decimal("777000"),
                payment_date=date(2026, 1, 15),
                contract_date=date(2025, 12, 20),
                resolution_date=date(2025, 12, 28),
                issue_date=date(2026, 1, 5),
            )
        )

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")

        assert (
            client.get("/dashboard/summary?year=2025").json()["total_purchase_amount"] == "777000"
        )
        assert client.get("/dashboard/summary?year=2026").json()["total_purchase_amount"] == "0"


# ======================================================================
# §18  CLI 등록 경로
# ======================================================================
class TestTheRegistrationCommand:
    """``python -m procurement targets`` 의 동작."""

    def test_it_registers_every_confirmed_target(self, db_path: Path) -> None:
        """STEP 99 §1 — 여덟 정책 10행(여성기업 3행)을 모두 등록한다."""
        assert main(["targets", "--year", "2026", "--db", str(db_path)]) == 0
        assert PolicyTargetRepository(db_path).count() == 10

    def test_running_it_twice_changes_nothing(self, db_path: Path) -> None:
        """운영자가 두 번 실행해도 값이 늘거나 흔들리지 않는다(멱등)."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        first = PolicyTargetRepository(db_path).rates_by_policy_id(2026)

        main(["targets", "--year", "2026", "--db", str(db_path)])

        assert PolicyTargetRepository(db_path).rates_by_policy_id(2026) == first

    def test_an_uninitialised_db_gets_guidance_not_a_stack_trace(self, tmp_path: Path) -> None:
        """DB 가 준비되지 않았을 때 sqlite 오류를 그대로 내보내지 않는다."""
        assert main(["targets", "--year", "2026", "--db", str(tmp_path / "missing.db")]) == 1

    def test_it_names_the_policies_it_skipped(
        self, db_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """⛔ 두 정책을 말없이 건너뛰지 않는다 — 이유까지 출력한다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        out = capsys.readouterr().out

        assert "WOMAN" in out
        assert "SELF_SUPPORT_VILLAGE" in out
        assert "계산 보류" in out


# ======================================================================
# 보조 — 합성 데이터 준비
# ======================================================================
def _register_company(db_path: Path, policy_code: str, business_no: str) -> None:
    """정책 하나에 합성 기업 한 곳을 인증과 함께 등록합니다."""
    repository = CompanyRepository(db_path)
    company = repository.find_by_business_no(business_no)
    if company is None:
        company = repository.insert(
            Company(business_no=business_no, company_name="가나상사", representative_name="홍길동")
        )
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(PolicyRepository(db_path), policy_code),
            valid_from=date(2025, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )


def _add_purchase(db_path: Path, business_no: str, amount: Decimal, when: date) -> None:
    PurchaseRepository(db_path).insert(
        Purchase(
            business_no=business_no,
            company_name="가나상사",
            amount=amount,
            payment_date=when,
            contract_date=when,
            resolution_date=when,
        )
    )


def _policy_item(client: TestClient, policy_code: str) -> dict[str, object]:
    body = client.get("/dashboard/summary?year=2026").json()
    return next(item for item in body["policies"] if item["policy_code"] == policy_code)
