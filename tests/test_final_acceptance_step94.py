"""
tests.test_final_acceptance_step94

**최종 인수검증** (STEP 94). 새 기능을 만들지 않고, 확정된 규칙이 코드에서
그대로 지켜지는지 못 박습니다.

STEP 93 시험(`test_policy_target_rate.py`)이 이미 덮은 부분은 다시 쓰지 않고,
**지시서 §1-A 의 2×2 격리 행렬**과 **§2 의 fallback 4 케이스**를 표 그대로
고정하는 데 집중합니다.

.. warning::
    ⛔ **이 파일은 시험만 추가합니다.** 시험을 통과시키려고 제품 코드를 바꾸지
    않았습니다. 여기서 깨지는 것이 있으면 그것이 곧 구현 버그입니다.

.. warning::
    ⛔ **합성 데이터만 사용합니다.** 사업자등록번호는 체크섬을 만족하는
    형식값이며 실제 거래처가 아닙니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.period import ALLOWED_DATE_FIELDS, RESOLUTION_DATE, PeriodFilter
from procurement.dashboard.models import DashboardStatus
from procurement.database.bootstrap import MVP_POLICY_SEEDS, bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.certification import Certification
from procurement.models.company import Company
from procurement.models.purchase import Purchase

ADMIN_TOKEN = "step94-token-not-a-real-secret"

#: 합성 사업자등록번호(체크섬 만족).
BUSINESS_NO = "2208162517"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "acceptance.db"
    bootstrap(path)
    return path


@pytest.fixture
def targets(db_path: Path) -> PolicyTargetRepository:
    return PolicyTargetRepository(db_path)


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=db_path, admin_token=ADMIN_TOKEN))


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None
    assert policy.policy_id is not None
    return policy.policy_id


def _summary(client: TestClient, year: int) -> dict[str, dict[str, object]]:
    """대시보드 요약을 ``{정책코드: 항목}`` 으로 돌려줍니다."""
    body = client.get(f"/dashboard/summary?year={year}").json()
    return {item["policy_code"]: item for item in body["policies"]}


def _register_company_data(db_path: Path, *policy_codes: str) -> None:
    """정책의 기업정보를 **받았다는 사실**만 기록합니다(STEP 96 §8).

    ⚠️ 이 기록이 없으면 그 정책은 조회불가이며 목표비율까지 가지 못합니다.
    목표비율 규칙을 보려는 시험이므로 앞단을 열어 두는 것입니다.
    ⛔ 기업·인증을 만들지 않습니다 — 목록을 받았지만 우리 거래처가 없는 상태와
    같습니다.
    """
    registry = PolicyCompanySourceRepository(db_path)
    for code in policy_codes:
        registry.record(
            _policy_id(db_path, code), source="FILE", company_count=0, certification_count=0
        )


def _seed_one_certified_purchase(db_path: Path, *, policy_codes: tuple[str, ...]) -> None:
    """한 거래처에 지출 1건과 인증을 넣습니다(2026 · 2027 두 해)."""
    company = CompanyRepository(db_path).insert(
        Company(business_no=BUSINESS_NO, company_name="가나산업", representative_name="홍길동")
    )
    assert company.company_id is not None
    for code in policy_codes:
        CertificationRepository(db_path).insert(
            Certification(
                company_id=company.company_id,
                policy_id=_policy_id(db_path, code),
                valid_from=date(2026, 1, 1),
                valid_to=date(2027, 12, 31),
            )
        )
    for year in (2026, 2027):
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=BUSINESS_NO,
                company_name="가나산업",
                amount=Decimal("1000000"),
                resolution_date=date(year, 6, 1),
                company_id=company.company_id,
            )
        )


# ======================================================================
# §1-A  연도 × 정책 격리 행렬
# ======================================================================
class TestYearAndPolicyIsolation:
    """지시서 §1-A 의 2×2 표를 그대로 고정한다.

    ::

        2026 / WOMAN   = 60      2027 / WOMAN   = 70
        2026 / STARTUP = 10      2027 / STARTUP = 15
    """

    @pytest.fixture
    def matrix(self, db_path: Path, targets: PolicyTargetRepository) -> Path:
        _register_company_data(db_path, "WOMAN", "STARTUP")
        woman = _policy_id(db_path, "WOMAN")
        startup = _policy_id(db_path, "STARTUP")
        targets.upsert(2026, woman, Decimal("60"))
        targets.upsert(2027, woman, Decimal("70"))
        targets.upsert(2026, startup, Decimal("10"))
        targets.upsert(2027, startup, Decimal("15"))
        return db_path

    def test_2026_returns_only_2026(self, matrix: Path, client: TestClient) -> None:
        """① 2026 조회는 2026 값만 반환한다."""
        rates = {code: item["target_rate"] for code, item in _summary(client, 2026).items()}
        assert rates["WOMAN"] == "60"
        assert rates["STARTUP"] == "10"

    def test_2027_returns_only_2027(self, matrix: Path, client: TestClient) -> None:
        """② 2027 조회는 2027 값만 반환한다."""
        rates = {code: item["target_rate"] for code, item in _summary(client, 2027).items()}
        assert rates["WOMAN"] == "70"
        assert rates["STARTUP"] == "15"

    def test_2026_value_never_leaks_into_2027(self, matrix: Path, client: TestClient) -> None:
        """③ 2026 값을 2027 계산에 쓰지 않는다."""
        assert _summary(client, 2027)["WOMAN"]["target_rate"] != "60"
        assert _summary(client, 2027)["STARTUP"]["target_rate"] != "10"

    def test_changing_one_year_leaves_the_other(
        self, matrix: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """④ 같은 정책의 연도별 값이 서로 독립적이다."""
        targets.upsert(2026, _policy_id(matrix, "WOMAN"), Decimal("99"))

        assert _summary(client, 2026)["WOMAN"]["target_rate"] == "99"
        assert _summary(client, 2027)["WOMAN"]["target_rate"] == "70"

    def test_changing_one_policy_leaves_the_other(
        self, matrix: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """⑤ 다른 정책의 목표비율이 서로 영향을 주지 않는다."""
        targets.upsert(2026, _policy_id(matrix, "WOMAN"), Decimal("99"))

        assert _summary(client, 2026)["STARTUP"]["target_rate"] == "10"

    def test_deleting_one_leaves_the_rest(
        self, matrix: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """해제도 그 칸 하나에만 미친다."""
        targets.delete(2026, _policy_id(matrix, "WOMAN"))

        assert _summary(client, 2026)["WOMAN"]["target_rate"] is None
        assert _summary(client, 2026)["STARTUP"]["target_rate"] == "10"
        assert _summary(client, 2027)["WOMAN"]["target_rate"] == "70"


# ======================================================================
# §2  Policy.target_rate fallback 금지 — 4 케이스
# ======================================================================
class TestLegacyColumnIsNeverUsedAsFallback:
    """⭐ 지시서 §2 의 Case 1~4 를 표 그대로 고정한다.

    ``Policy.target_rate`` 는 하위호환으로 남아 있을 뿐이며, 신규 계산 경로가
    이 값을 **어떤 경우에도** 끌어 쓰지 않는다(DECISIONS §0.20 · 지시서 §8).

    ⚠️ **STEP 96 — 설정 보완.** 기업정보를 받지 못한 정책은 이제 **조회불가**다
    (STEP 96 §8). 이 시험들이 보려는 것은 목표비율 쪽이므로, 그 정책의 기업정보를
    받았다는 사실을 먼저 등록해 둔다.
    ⛔ 기대값은 바뀌지 않았다 — 목표비율 규칙을 그대로 검증한다.
    """

    def test_case1_year_target_wins_over_the_legacy_column(
        self, db_path: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """Case 1 — 구 컬럼 50 · 2026 목표 60 → **60** 이 이긴다."""
        _register_company_data(db_path, "WOMAN")
        PolicyRepository(db_path).update_target_rate("WOMAN", Decimal("50"))
        targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))

        item = _summary(client, 2026)["WOMAN"]

        assert item["target_rate"] == "60"

    def test_case2_legacy_column_alone_means_unset(self, db_path: Path, client: TestClient) -> None:
        """Case 2 — 구 컬럼 50 · 연도 목표 없음 → **미설정**. ⛔ 50 을 쓰지 않는다."""
        _register_company_data(db_path, "WOMAN")
        PolicyRepository(db_path).update_target_rate("WOMAN", Decimal("50"))

        item = _summary(client, 2026)["WOMAN"]

        assert item["target_rate"] is None
        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value
        assert item["achievement_rate"] is None

    def test_case3_another_year_is_not_borrowed(
        self, db_path: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """Case 3 — 2025 목표 50 · 2026 없음 → **미설정**. ⛔ 2025 를 끌어오지 않는다."""
        _register_company_data(db_path, "WOMAN")
        targets.upsert(2025, _policy_id(db_path, "WOMAN"), Decimal("50"))

        item = _summary(client, 2026)["WOMAN"]

        assert item["target_rate"] is None
        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value

    def test_case4_only_the_configured_policy_is_calculated(
        self, db_path: Path, client: TestClient, targets: PolicyTargetRepository
    ) -> None:
        """Case 4 — 2026 WOMAN 60 · STARTUP 없음 → WOMAN 만 계산, STARTUP 미설정."""
        _register_company_data(db_path, "WOMAN", "STARTUP")
        targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))

        summary = _summary(client, 2026)

        assert summary["WOMAN"]["target_rate"] == "60"
        assert summary["STARTUP"]["target_rate"] is None
        assert summary["STARTUP"]["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value

    def test_the_legacy_column_still_exists_and_still_holds_its_value(self, db_path: Path) -> None:
        """⛔ 구 컬럼을 **지우지 않았다**(지시서 §14). 값도 그대로 남는다."""
        PolicyRepository(db_path).update_target_rate("WOMAN", Decimal("50"))

        policy = PolicyRepository(db_path).find_by_policy_code("WOMAN")
        assert policy is not None
        assert policy.target_rate == Decimal("50")

    def test_the_legacy_endpoint_still_answers(self, client: TestClient) -> None:
        """⛔ 구 API 도 지우지 않았다(지시서 §14)."""
        response = client.put(
            "/policies/WOMAN/target-rate",
            json={"target_rate": "50"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 200

    def test_the_legacy_endpoint_does_not_move_the_dashboard(
        self, db_path: Path, client: TestClient
    ) -> None:
        """⭐ 구 API 로 넣어도 **달성률은 움직이지 않는다** — 정본이 아니기 때문이다."""
        _register_company_data(db_path, "WOMAN")
        client.put(
            "/policies/WOMAN/target-rate",
            json={"target_rate": "50"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

        item = _summary(client, 2026)["WOMAN"]

        assert item["target_rate"] is None
        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value


# ======================================================================
# §3  미설정은 0% 가 아니다
# ======================================================================
class TestUnsetIsNotZero:
    """⛔ ``null`` 을 0% 로 오해할 수 없어야 한다."""

    def test_unset_is_not_calculated(self, db_path: Path, client: TestClient) -> None:
        _seed_one_certified_purchase(db_path, policy_codes=("WOMAN",))

        item = _summary(client, 2026)["WOMAN"]

        assert item["target_rate"] is None
        assert item["achievement_rate"] is None
        assert item["shortage_rate"] is None
        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value

    def test_unset_is_never_rendered_as_zero(self, db_path: Path, client: TestClient) -> None:
        """⛔ 0 · "0" · 0.0 어느 모양으로도 나가지 않는다."""
        _seed_one_certified_purchase(db_path, policy_codes=("WOMAN",))

        item = _summary(client, 2026)["WOMAN"]

        for key in ("target_rate", "achievement_rate", "shortage_rate"):
            assert item[key] is None, key
            assert item[key] != "0"
            assert item[key] != 0

    def test_the_status_label_says_unset_not_zero(self, db_path: Path, client: TestClient) -> None:
        _register_company_data(db_path, "WOMAN")

        item = _summary(client, 2026)["WOMAN"]

        assert item["status_label"] == "목표율 미설정"

    def test_purchase_amount_is_still_reported(self, db_path: Path, client: TestClient) -> None:
        """목표비율이 없어도 **전체 구매액은** 그대로 나온다 — 데이터가 없는 것이 아니다."""
        _seed_one_certified_purchase(db_path, policy_codes=("WOMAN",))

        body = client.get("/dashboard/summary?year=2026").json()

        assert body["total_purchase_amount"] == "1000000"

    def test_zero_is_rejected_as_a_target(self, client: TestClient) -> None:
        """⛔ 0 은 목표비율로 저장할 수 없다 — "미설정" 과 섞이면 안 되기 때문이다."""
        response = client.put(
            "/policy-targets/2026/WOMAN",
            json={"target_rate": "0"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 422

    def test_set_and_not_set_are_distinguishable_in_the_listing(
        self, client: TestClient, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))

        body = client.get("/policy-targets?year=2026").json()
        status = {item["policy_code"]: item["target_rate_status"] for item in body["items"]}

        assert status["WOMAN"] == "SET"
        assert status["STARTUP"] == "NOT_SET"


# ======================================================================
# §4  입력값 — 저장 후 되읽기까지
# ======================================================================
class TestInputValuesRoundTrip:
    """⛔ 20/40/60/80/100 으로 제한하지 않는다. 넣은 값이 그대로 돌아온다."""

    @pytest.mark.parametrize("rate", ["0.01", "37", "37.5", "42.5", "100"])
    def test_allowed_values_survive_a_round_trip(self, client: TestClient, rate: str) -> None:
        client.put(
            "/policy-targets/2026/WOMAN",
            json={"target_rate": rate},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

        body = client.get("/policy-targets?year=2026").json()
        stored = {item["policy_code"]: item["target_rate"] for item in body["items"]}

        assert stored["WOMAN"] == rate  # ⭐ 정밀도가 깎이지 않는다

    @pytest.mark.parametrize("rate", ["0", "-1", "100.01"])
    def test_rejected_values(self, client: TestClient, rate: str) -> None:
        response = client.put(
            "/policy-targets/2026/WOMAN",
            json={"target_rate": rate},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 422

    def test_null_releases_the_target(self, client: TestClient, db_path: Path) -> None:
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        client.put("/policy-targets/2026/WOMAN", json={"target_rate": "60"}, headers=headers)

        client.put("/policy-targets/2026/WOMAN", json={"target_rate": None}, headers=headers)

        assert PolicyTargetRepository(db_path).get(2026, _policy_id(db_path, "WOMAN")) is None

    def test_a_missing_key_changes_nothing(self, client: TestClient, db_path: Path) -> None:
        """키가 아예 없으면 422 로 거부하고 **기존 값을 바꾸지 않는다.**"""
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        client.put("/policy-targets/2026/WOMAN", json={"target_rate": "60"}, headers=headers)

        response = client.put("/policy-targets/2026/WOMAN", json={}, headers=headers)

        assert response.status_code == 422
        saved = PolicyTargetRepository(db_path).get(2026, _policy_id(db_path, "WOMAN"))
        assert saved is not None
        assert saved.target_rate == Decimal("60")  # ⭐ 그대로다

    def test_json_number_is_rejected(self, client: TestClient) -> None:
        """⛔ float 를 거치면 37.5 의 정밀도가 깨진다."""
        response = client.put(
            "/policy-targets/2026/WOMAN",
            json={"target_rate": 60},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert response.status_code == 422

    def test_no_threshold_whitelist_exists_in_the_source(self) -> None:
        """⛔ 목표비율을 표시 구간 값으로 제한하는 코드가 **없다.**"""
        import inspect

        from procurement.database import policy_repository

        source = inspect.getsource(policy_repository.validate_target_rate)
        assert "20" not in source
        assert "40" not in source
        assert "80" not in source


# ======================================================================
# §5  정책 간 중복 집계 — 합계가 전체를 넘는 것이 정상
# ======================================================================
class TestPolicyOverlapIsAllowed:
    """PM 확정 규칙. STEP 93 시험이 금액을 고정했고, 여기서는 **합계 초과**를 못 박는다."""

    @pytest.fixture
    def seeded(self, db_path: Path, targets: PolicyTargetRepository) -> Path:
        rows = (
            ("2208162517", "A기업", 600000, ("WOMAN", "STARTUP", "SMALL_BUSINESS")),
            ("1048124017", "B기업", 400000, ("WOMAN", "SMALL_BUSINESS")),
            ("1108114429", "C기업", 200000, ("STARTUP", "SMALL_BUSINESS")),
        )
        for business_no, name, amount, codes in rows:
            company = CompanyRepository(db_path).insert(
                Company(business_no=business_no, company_name=name, representative_name="홍길동")
            )
            assert company.company_id is not None
            for code in codes:
                CertificationRepository(db_path).insert(
                    Certification(
                        company_id=company.company_id,
                        policy_id=_policy_id(db_path, code),
                        valid_from=date(2026, 1, 1),
                        valid_to=date(2026, 12, 31),
                    )
                )
            PurchaseRepository(db_path).insert(
                Purchase(
                    business_no=business_no,
                    company_name=name,
                    amount=Decimal(amount),
                    resolution_date=date(2026, 6, 1),
                    company_id=company.company_id,
                )
            )
        for code, rate in (("SMALL_BUSINESS", "50"), ("WOMAN", "60"), ("STARTUP", "10")):
            targets.upsert(2026, _policy_id(db_path, code), Decimal(rate))
        return db_path

    def test_the_confirmed_table(self, seeded: Path, client: TestClient) -> None:
        """PM 확정 표 그대로."""
        summary = _summary(client, 2026)

        assert summary["SMALL_BUSINESS"]["purchase_amount"] == "1200000"
        assert summary["SMALL_BUSINESS"]["achievement_rate"] == "200.00"
        assert summary["WOMAN"]["purchase_amount"] == "1000000"
        assert summary["WOMAN"]["achievement_rate"] == "138.89"
        assert summary["STARTUP"]["purchase_amount"] == "800000"
        assert summary["STARTUP"]["achievement_rate"] == "666.67"

    def test_policy_sum_exceeds_the_total_and_that_is_correct(
        self, seeded: Path, client: TestClient
    ) -> None:
        """⭐ 정책 실적 합(300만) > 기관 전체(120만). **이것이 정상이다.**

        ⛔ 정책 간 금액을 차감하거나 배타적으로 나누지 않는다.
        """
        body = client.get("/dashboard/summary?year=2026").json()
        total = Decimal(body["total_purchase_amount"])
        summed = sum(
            Decimal(item["purchase_amount"])
            for item in body["policies"]
            if item["purchase_amount"] is not None
        )

        assert total == Decimal("1200000")
        assert summed == Decimal("3000000")
        assert summed > total

    def test_one_purchase_counts_in_every_policy_it_qualifies_for(
        self, seeded: Path, client: TestClient
    ) -> None:
        """A기업 60만원이 세 정책 실적에 **모두** 들어간다."""
        summary = _summary(client, 2026)
        # A(60만)만 창업+여성+중소 전부에 해당한다. 세 실적 모두 60만 이상이다.
        for code in ("SMALL_BUSINESS", "WOMAN", "STARTUP"):
            assert Decimal(str(summary[code]["purchase_amount"])) >= Decimal("600000")

    def test_the_denominator_is_shared(self, seeded: Path, client: TestClient) -> None:
        """모든 정책이 **같은 분모**(기관 전체 지출)를 쓴다."""
        summary = _summary(client, 2026)
        totals = {item["total_purchase_amount"] for item in summary.values()}
        assert totals == {"1200000"}


# ======================================================================
# §6  기존 계산 규칙 회귀
# ======================================================================
class TestExistingRulesAreUnchanged:
    """⛔ 확정된 업무규칙이 그대로인지 못 박는다."""

    def test_the_year_basis_is_the_resolution_date(self) -> None:
        from procurement.core.config import settings

        assert settings.PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    def test_issue_date_cannot_even_be_chosen_as_a_period_field(self) -> None:
        """⛔ 신고기준일은 기간 필터 후보에 **들어 있지도 않다.**"""
        assert ALLOWED_DATE_FIELDS == {"payment_date", "contract_date", "resolution_date"}
        assert "issue_date" not in ALLOWED_DATE_FIELDS

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_general_policies_judge_on_the_resolution_date(self, code: str) -> None:
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == code)
        assert seed.evaluation_basis == "RESOLUTION_DATE"

    def test_startup_keeps_the_or_rule(self) -> None:
        """창업기업은 결의일자 OR 계약일자 — 고객 확정(2026-08-14)."""
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")
        assert seed.evaluation_basis == "RESOLUTION_OR_CONTRACT_DATE"

    def test_the_policy_set_is_unchanged(self) -> None:
        """⛔ 사회적기업 등 신규 정책을 만들지 않았다."""
        assert {s.policy_code for s in MVP_POLICY_SEEDS} == {
            "SMALL_BUSINESS",
            "WOMAN",
            "DISABLED",
            "STARTUP",
            "GREEN",
        }

    def test_direct_production_is_not_used_for_performance(self) -> None:
        """⛔ 직접생산확인은 실적 집계에 쓰지 않는다 — 계산 경로에 낱말조차 없다."""
        from pathlib import Path as _Path

        src = _Path(__file__).resolve().parents[1] / "src" / "procurement"
        for area in ("calculators", "dashboard"):
            hits = [
                path.name
                for path in (src / area).rglob("*.py")
                if "DIRECT_PRODUCTION" in path.read_text(encoding="utf-8")
            ]
            assert hits == [], (area, hits)

    def test_the_calculator_signature_is_unchanged(self) -> None:
        """⭐ 계산기는 여전히 목표비율을 **인자로** 받는다 — 저장 구조를 모른다."""
        import inspect

        from procurement.calculators import ProcurementAchievementCalculator

        params = inspect.signature(ProcurementAchievementCalculator.calculate_all).parameters
        assert "target_rates" in params
        source = inspect.getsource(ProcurementAchievementCalculator)
        assert "PolicyTarget" not in source
        assert "policy_target" not in source


# ======================================================================
# §8  기업정보 FILE/API 회귀
# ======================================================================
class TestCompanySourcesAreUnchanged:
    """FILE 과 API 가 **같은 흐름**으로 모이는지 다시 확인한다."""

    def test_both_methods_are_offered(self, client: TestClient) -> None:
        body = client.get("/companies/sources").json()
        assert body["methods"] == ["FILE", "API"]

    def test_company_insert_happens_in_exactly_one_place(self) -> None:
        """⭐ FILE·API 가 서로 다른 저장 로직을 쓰지 않는다."""
        from pathlib import Path as _Path

        src = _Path(__file__).resolve().parents[1] / "src" / "procurement"
        hits = [
            path.name
            for path in src.rglob("*.py")
            if "self._companies.insert(" in path.read_text(encoding="utf-8")
        ]
        assert hits == ["company_importer.py"], hits

    def test_company_size_is_never_used_for_judgement(self) -> None:
        """⛔ 중소기업 판정에 업체규모를 쓰지 않는다 — 제외 목록에만 나타난다."""
        from pathlib import Path as _Path

        src = _Path(__file__).resolve().parents[1] / "src" / "procurement"
        hits = {
            path.name
            for path in src.rglob("*.py")
            if "업체규모" in path.read_text(encoding="utf-8")
        }
        assert hits == {"company_format.py"}, hits

    def test_matching_is_exact_business_number_only(self) -> None:
        """⛔ fuzzy matching 을 넣지 않았다."""
        import inspect

        from procurement.matchers.company_matcher import CompanyMatcher

        source = inspect.getsource(CompanyMatcher)
        for term in ("fuzzy", "ratio", "similar", "levenshtein", "difflib"):
            assert term not in source.lower(), term
        assert "find_by_business_no" in source

    def test_no_api_call_without_a_configured_client(self, db_path: Path) -> None:
        """API 키가 없는 환경에서 **외부 호출을 시도하지 않는다.**"""
        from procurement.app import build_company_source_service

        service = build_company_source_service(db_path)

        with pytest.raises(RuntimeError):
            service.import_from_api("WOMAN_SMPP", [BUSINESS_NO], stdr_date=date(2026, 1, 1))


# ======================================================================
# §9  목표비율 화면
# ======================================================================
class TestTargetScreen:
    """화면이 지켜야 할 것들."""

    @pytest.fixture(scope="class")
    def index_html(self) -> str:
        from procurement.web.page import read_index_html

        return read_index_html()

    def test_policy_names_come_from_the_server(self, index_html: str) -> None:
        """⛔ 정책명을 프론트에 하드코딩하지 않는다."""
        assert "item.policy_name" in index_html
        target_card = index_html.split("목표비율 관리")[1].split("</section>")[0]
        for name in ("중소기업", "여성기업", "장애인기업", "창업기업"):
            assert name not in target_card, name

    def test_changing_the_year_refetches_from_the_server(self, index_html: str) -> None:
        """연도를 바꾸면 서버에서 그 연도를 **다시 조회**한다."""
        # 대시보드 연도 셀렉터와 섞이지 않도록 목표비율 초기화 함수 안만 본다.
        init = index_html.split("function initPolicyTargets()")[1].split("\n  }")[0]
        handler = init.split('select.addEventListener("change"')[1]
        assert "loadPolicyTargets()" in handler

    def test_only_changed_policies_are_sent(self, index_html: str) -> None:
        """변경한 정책만 PUT 한다."""
        assert "if (typed === before) { return; }" in index_html

    def test_blank_is_sent_as_null(self, index_html: str) -> None:
        """⛔ 빈칸을 0 으로 저장하지 않는다."""
        assert 'typed === "" ? null : typed' in index_html

    def test_the_dashboard_is_refreshed_after_saving(self, index_html: str) -> None:
        assert "loadPolicyTargets().then(load)" in index_html

    @pytest.mark.parametrize("status", ["403", "422", "503"])
    def test_error_causes_are_explained(self, index_html: str, status: str) -> None:
        """⛔ 무조건 "다시 시도" 로 안내하지 않는다."""
        handler = index_html.split("function ptFailureMessage")[1].split("\n  }")[0]
        assert status in handler, status

    def test_no_company_selection_on_the_target_card(self, index_html: str) -> None:
        """⛔ 기업 선택 UI 를 만들지 않았다."""
        assert 'id="pt-company' not in index_html
        assert "기업별 목표비율" not in index_html


# ======================================================================
# §14  하지 않기로 한 것
# ======================================================================
class TestForbiddenChangesWereNotMade:
    """⛔ 지시서 §14 금지 목록."""

    def test_no_institution_tables(self, db_path: Path) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(purchase)").fetchall()}
        conn.close()

        assert "institution" not in tables
        assert "organization" not in tables
        assert "institution_id" not in columns

    def test_policy_target_has_no_company_axis(self, db_path: Path) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_target)").fetchall()}
        conn.close()

        assert "company_id" not in columns
        assert "business_no" not in columns

    def test_the_legacy_column_and_endpoint_survive(
        self, db_path: Path, client: TestClient
    ) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(policy)").fetchall()}
        conn.close()

        assert "target_rate" in columns
        assert client.get("/policies").status_code == 200

    def test_the_unused_sync_path_was_left_alone(self) -> None:
        """⛔ 미사용 legacy 경로를 이번에 정리하지 않았다."""
        from procurement.collectors.sync_service import SKIP_COMPANY_NOT_FOUND

        assert SKIP_COMPANY_NOT_FOUND == "COMPANY_NOT_FOUND"

    def test_the_display_thresholds_are_unchanged(self) -> None:
        from procurement.web.achievement_display import DEFAULT_THRESHOLDS, LEVEL_LABELS

        assert DEFAULT_THRESHOLDS == (
            Decimal("20"),
            Decimal("40"),
            Decimal("60"),
            Decimal("80"),
            Decimal("100"),
        )
        assert LEVEL_LABELS == ("위험", "미달", "주의", "적정", "충족 임박", "충족")


def test_the_period_filter_still_needs_an_explicit_date_field() -> None:
    """⛔ 기간 판정 기준일에 기본값을 두지 않는다 — 호출자가 매번 명시한다."""
    import inspect

    signature = inspect.signature(PeriodFilter.for_year)
    assert signature.parameters["date_field"].default is inspect.Parameter.empty
    assert PeriodFilter.for_year(2026, RESOLUTION_DATE).date_field == "resolution_date"
