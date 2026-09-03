"""
tests.test_policy_target_rate

**연도별 · 정책별 목표비율** (STEP 93 · ``DECISIONS.md`` §0.20).

증명하려는 것은 셋입니다.

1. 목표비율의 축은 **연도 × 정책** 뿐이다 — ⛔ 구매처가 들어오지 않는다
2. 연도끼리 **서로 간섭하지 않는다** — ⛔ 없는 연도를 다른 연도로 메우지 않는다
3. 정책 간 기업 중복 집계가 **그대로 동작한다** — PM 확정 예제로 확인한다

.. warning::
    ⛔ **합성 데이터만 사용합니다.** 사업자등록번호는 체크섬을 만족하는
    형식값이며 실제 거래처가 아닙니다.

.. note::
    §13 의 A/B/C 예제(여성 100만 · 창업 80만 · 중소 120만)를 **API 부터 계산기
    까지 한 줄로** 통과시키는 것이 이 파일의 핵심입니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.admin.policy_admin import PolicyNotFoundError
from procurement.calculators import ProcurementAchievementCalculator
from procurement.core.period import RESOLUTION_DATE, PeriodFilter
from procurement.dashboard.data_service import DashboardDataService
from procurement.dashboard.models import DashboardStatus
from procurement.database.bootstrap import MVP_POLICY_SEEDS, bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import (
    PolicyRepository,
    PolicyValidationError,
)
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.certification import Certification
from procurement.models.company import Company
from procurement.models.purchase import Purchase

ADMIN_TOKEN = "test-token-not-a-real-secret"
LIST_URL = "/policy-targets"

#: PM 확정 예제(§13). 합성 사업자등록번호 — 체크섬 만족.
EXAMPLE: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("2208162517", "A기업", 600000, ("WOMAN", "STARTUP", "SMALL_BUSINESS")),
    ("1048124017", "B기업", 400000, ("WOMAN", "SMALL_BUSINESS")),
    ("1108114429", "C기업", 200000, ("STARTUP", "SMALL_BUSINESS")),
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "target.db"
    bootstrap(path)
    return path


@pytest.fixture
def targets(db_path: Path) -> PolicyTargetRepository:
    return PolicyTargetRepository(db_path)


@pytest.fixture
def policies(db_path: Path) -> PolicyRepository:
    return PolicyRepository(db_path)


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """관리자 토큰이 설정된 API 클라이언트."""
    from procurement.app import create_app

    return TestClient(create_app(db_path=db_path, admin_token=ADMIN_TOKEN))


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None
    assert policy.policy_id is not None
    return policy.policy_id


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _put(client: TestClient, year: int, code: str, rate: str | None) -> dict[str, object]:
    response = client.put(
        f"/policy-targets/{year}/{code}", json={"target_rate": rate}, headers=_auth()
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


# ======================================================================
# §14 DB — 저장소
# ======================================================================
class TestRepository:
    """PolicyTarget 저장·조회."""

    def test_create_and_get(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        saved = targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))

        assert saved.policy_target_id is not None
        assert saved.year == 2026
        assert saved.target_rate == Decimal("60")

    def test_get_returns_none_when_unset(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """⛔ 미설정은 ``None`` 이다. 0 이 아니다."""
        assert targets.get(2026, _policy_id(db_path, "WOMAN")) is None

    def test_list_by_year(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))
        targets.upsert(2026, _policy_id(db_path, "STARTUP"), Decimal("10"))
        targets.upsert(2025, _policy_id(db_path, "WOMAN"), Decimal("40"))

        assert len(targets.list_by_year(2026)) == 2
        assert len(targets.list_by_year(2025)) == 1

    def test_upsert_never_duplicates(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        """같은 (연도, 정책) 은 **한 건만** 존재한다."""
        policy_id = _policy_id(db_path, "WOMAN")

        targets.upsert(2026, policy_id, Decimal("60"))
        targets.upsert(2026, policy_id, Decimal("70"))
        targets.upsert(2026, policy_id, Decimal("80"))

        assert targets.count() == 1
        saved = targets.get(2026, policy_id)
        assert saved is not None
        assert saved.target_rate == Decimal("80")

    def test_unique_constraint_exists_in_schema(self, db_path: Path) -> None:
        """제약이 **DB 에** 있다 — 애플리케이션 코드를 우회해도 막힌다."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'policy_target'").fetchone()[
            0
        ]
        conn.close()
        assert "UNIQUE (year, policy_id)" in sql

    def test_years_do_not_interfere(self, db_path: Path, targets: PolicyTargetRepository) -> None:
        """⭐ 2026년을 바꿔도 2025년이 그대로다."""
        policy_id = _policy_id(db_path, "WOMAN")
        targets.upsert(2025, policy_id, Decimal("40"))

        targets.upsert(2026, policy_id, Decimal("50"))

        saved_2025 = targets.get(2025, policy_id)
        assert saved_2025 is not None
        assert saved_2025.target_rate == Decimal("40")

    def test_delete_clears_the_setting(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """해제는 **행을 지운다**. ⛔ 0 으로 저장하지 않는다."""
        policy_id = _policy_id(db_path, "WOMAN")
        targets.upsert(2026, policy_id, Decimal("60"))

        assert targets.delete(2026, policy_id) is True
        assert targets.get(2026, policy_id) is None
        assert targets.delete(2026, policy_id) is False

    def test_rates_by_policy_id_matches_calculator_shape(
        self, db_path: Path, targets: PolicyTargetRepository
    ) -> None:
        """계산기가 받는 모양 그대로다 — ⛔ 계산기 시그니처를 바꾸지 않는다."""
        woman = _policy_id(db_path, "WOMAN")
        targets.upsert(2026, woman, Decimal("60"))

        assert targets.rates_by_policy_id(2026) == {woman: Decimal("60")}
        assert targets.rates_by_policy_id(2025) == {}

    def test_unknown_policy_is_rejected(self, targets: PolicyTargetRepository) -> None:
        with pytest.raises(PolicyValidationError):
            targets.upsert(2026, 99999, Decimal("60"))


# ======================================================================
# §14 Validation
# ======================================================================
class TestValidation:
    """목표비율 값 검증 — ⛔ 20/40/60/80/100 으로 제한하지 않는다."""

    @pytest.mark.parametrize("rate", ["20", "37", "42.5", "60", "0.01", "100"])
    def test_allowed(self, db_path: Path, targets: PolicyTargetRepository, rate: str) -> None:
        saved = targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal(rate))
        assert saved.target_rate == Decimal(rate)

    @pytest.mark.parametrize("rate", ["0", "-1", "-0.5", "100.01", "101", "1000"])
    def test_rejected(self, db_path: Path, targets: PolicyTargetRepository, rate: str) -> None:
        with pytest.raises(PolicyValidationError):
            targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal(rate))

    @pytest.mark.parametrize("year", [1899, 3000, 0, -2026])
    def test_year_out_of_range(
        self, db_path: Path, targets: PolicyTargetRepository, year: int
    ) -> None:
        with pytest.raises(PolicyValidationError):
            targets.upsert(year, _policy_id(db_path, "WOMAN"), Decimal("60"))

    def test_the_display_thresholds_are_not_a_limit(self) -> None:
        """⛔ 20/40/60/80/100 은 **표시 구간**이지 입력값 제한이 아니다."""
        from procurement.web.achievement_display import DEFAULT_THRESHOLDS

        assert DEFAULT_THRESHOLDS == (
            Decimal("20"),
            Decimal("40"),
            Decimal("60"),
            Decimal("80"),
            Decimal("100"),
        )
        # 표시 구간에 없는 값도 목표비율로는 정상이다.
        from procurement.database.policy_repository import validate_target_rate

        validate_target_rate(Decimal("37.5"))


# ======================================================================
# §14 API
# ======================================================================
class TestApi:
    """GET /policy-targets · PUT /policy-targets/{year}/{code}."""

    def test_list_includes_unset_policies(self, client: TestClient) -> None:
        """미설정 정책도 목록에 담긴다 — 화면이 입력칸을 그려야 한다."""
        body = client.get(f"{LIST_URL}?year=2026").json()

        assert body["year"] == 2026
        codes = {item["policy_code"] for item in body["items"]}
        # ⚠️ 활성 seed 와 대조한다 — §0.22 로 활성 정책이 4종 → 8종이 되었고,
        #    이 시험의 요지는 "미설정 정책도 목록에 담긴다" 이다.
        assert codes == {seed.policy_code for seed in MVP_POLICY_SEEDS if seed.is_active}
        assert all(item["target_rate"] is None for item in body["items"])
        assert all(item["target_rate_status"] == "NOT_SET" for item in body["items"])

    def test_list_carries_policy_names(self, client: TestClient) -> None:
        """⛔ 화면이 정책명을 들고 있지 않도록 서버가 준다."""
        body = client.get(f"{LIST_URL}?year=2026").json()

        names = {item["policy_code"]: item["policy_name"] for item in body["items"]}
        assert names["WOMAN"] == "여성기업"
        assert names["SMALL_BUSINESS"] == "중소기업"

    def test_list_never_carries_a_company(self, client: TestClient) -> None:
        """⭐ 응답 어디에도 구매처가 없다 — 축은 연도 × 정책 뿐이다."""
        body = client.get(f"{LIST_URL}?year=2026").json()

        for item in body["items"]:
            assert "company_id" not in item
            assert "business_no" not in item
            assert set(item) == {
                "year",
                "policy_id",
                "policy_code",
                "policy_name",
                "is_active",
                "target_rate",
                "target_rate_status",
                "updated_at",
            }

    def test_year_is_required(self, client: TestClient) -> None:
        assert client.get(LIST_URL).status_code == 422

    def test_put_creates(self, client: TestClient) -> None:
        body = _put(client, 2026, "WOMAN", "60")

        assert body["target_rate"] == "60"
        assert body["target_rate_status"] == "SET"
        assert body["year"] == 2026

    def test_put_updates_and_is_idempotent(self, client: TestClient, db_path: Path) -> None:
        _put(client, 2026, "WOMAN", "60")
        _put(client, 2026, "WOMAN", "60")
        body = _put(client, 2026, "WOMAN", "70")

        assert body["target_rate"] == "70"
        assert PolicyTargetRepository(db_path).count() == 1

    def test_put_null_clears(self, client: TestClient, db_path: Path) -> None:
        """``null`` 은 해제다. ⛔ 0 으로 저장하지 않는다."""
        _put(client, 2026, "WOMAN", "60")

        body = _put(client, 2026, "WOMAN", None)

        assert body["target_rate"] is None
        assert body["target_rate_status"] == "NOT_SET"
        assert PolicyTargetRepository(db_path).count() == 0

    def test_missing_key_is_rejected(self, client: TestClient) -> None:
        """ "바꾸지 않음" 과 "해제" 를 구분한다."""
        response = client.put("/policy-targets/2026/WOMAN", json={}, headers=_auth())
        assert response.status_code == 422

    def test_json_number_is_rejected(self, client: TestClient) -> None:
        """⛔ float 를 거치면 37.5 의 정밀도가 깨진다."""
        response = client.put(
            "/policy-targets/2026/WOMAN", json={"target_rate": 60}, headers=_auth()
        )
        assert response.status_code == 422

    def test_years_are_independent_over_the_api(self, client: TestClient) -> None:
        """⭐ 2026년을 입력해도 2025년 값이 바뀌지 않는다(§11-3)."""
        _put(client, 2025, "WOMAN", "40")
        _put(client, 2026, "WOMAN", "50")

        body_2025 = client.get(f"{LIST_URL}?year=2025").json()
        rates = {item["policy_code"]: item["target_rate"] for item in body_2025["items"]}
        assert rates["WOMAN"] == "40"

    def test_unknown_policy_is_404(self, client: TestClient) -> None:
        response = client.put(
            "/policy-targets/2026/NOT_A_POLICY", json={"target_rate": "60"}, headers=_auth()
        )
        assert response.status_code == 404

    def test_inactive_policy_is_rejected(self, client: TestClient) -> None:
        """GREEN 은 계산 대상이 아니므로 목표비율을 두지 않는다."""
        response = client.put(
            "/policy-targets/2026/GREEN", json={"target_rate": "60"}, headers=_auth()
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("rate", ["0", "-1", "101", "abc"])
    def test_bad_rate_is_422(self, client: TestClient, rate: str) -> None:
        response = client.put(
            "/policy-targets/2026/WOMAN", json={"target_rate": rate}, headers=_auth()
        )
        assert response.status_code == 422

    def test_write_requires_the_admin_token(self, client: TestClient) -> None:
        """기존 목표율 API 와 **같은** 권한 정책을 따른다."""
        response = client.put("/policy-targets/2026/WOMAN", json={"target_rate": "60"})
        assert response.status_code == 401

    def test_read_does_not_require_a_token(self, client: TestClient) -> None:
        assert client.get(f"{LIST_URL}?year=2026").status_code == 200

    def test_write_is_disabled_without_a_configured_token(self, db_path: Path) -> None:
        """토큰 미설정이면 503 — 기존 규칙 그대로다."""
        from procurement.app import create_app

        anonymous = TestClient(create_app(db_path=db_path, admin_token=None))
        response = anonymous.put("/policy-targets/2026/WOMAN", json={"target_rate": "60"})
        assert response.status_code == 503

    def test_the_old_endpoint_still_works(self, client: TestClient) -> None:
        """⛔ 기존 API 를 삭제하지 않았다(§7)."""
        response = client.put(
            "/policies/WOMAN/target-rate", json={"target_rate": "60"}, headers=_auth()
        )
        assert response.status_code == 200


# ======================================================================
# §13 · §14 핵심 — PM 확정 예제
# ======================================================================
class TestTheConfirmedExample:
    """⭐ A/B/C 예제 — 정책 간 기업 중복 집계."""

    @pytest.fixture
    def seeded(self, db_path: Path) -> Path:
        """A/B/C 3개 거래처와 그 지출·인증을 넣습니다."""
        companies = CompanyRepository(db_path)
        certifications = CertificationRepository(db_path)
        purchases = PurchaseRepository(db_path)

        for business_no, name, amount, codes in EXAMPLE:
            company = companies.insert(
                Company(
                    business_no=business_no,
                    company_name=name,
                    representative_name="홍길동",
                )
            )
            assert company.company_id is not None
            for code in codes:
                certifications.insert(
                    Certification(
                        company_id=company.company_id,
                        policy_id=_policy_id(db_path, code),
                        valid_from=date(2026, 1, 1),
                        valid_to=date(2026, 12, 31),
                    )
                )
            purchases.insert(
                Purchase(
                    business_no=business_no,
                    company_name=name,
                    amount=Decimal(amount),
                    # ⭐ 결의일자 기준이다.
                    resolution_date=date(2026, 6, 1),
                    company_id=company.company_id,
                )
            )
        return db_path

    def _calculator(self, db_path: Path) -> ProcurementAchievementCalculator:
        return ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )

    def test_total_is_the_denominator(self, seeded: Path) -> None:
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert self._calculator(seeded).calculate_total_purchase(period) == Decimal("1200000")

    @pytest.mark.parametrize(
        ("code", "expected"),
        [("WOMAN", "1000000"), ("STARTUP", "800000"), ("SMALL_BUSINESS", "1200000")],
    )
    def test_policy_amounts_overlap(self, seeded: Path, code: str, expected: str) -> None:
        """⭐ A기업 60만원이 **세 정책 실적에 모두** 들어간다."""
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        amount = self._calculator(seeded).calculate_policy_purchase(
            _policy_id(seeded, code), period
        )
        assert amount == Decimal(expected)

    def test_policy_amounts_may_exceed_the_total(self, seeded: Path) -> None:
        """⭐ 정책 실적 합(300만)이 기관 전체(120만)를 **넘는 것이 정상**이다.

        ⛔ 정책 간 지출을 차감하거나 배타적으로 나누지 않는다.
        """
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        calculator = self._calculator(seeded)
        summed = sum(
            calculator.calculate_policy_purchase(_policy_id(seeded, code), period)
            for code in ("WOMAN", "STARTUP", "SMALL_BUSINESS")
        )
        assert summed == Decimal("3000000")
        assert summed > calculator.calculate_total_purchase(period)

    @pytest.mark.parametrize(
        ("code", "rate", "expected"),
        [
            ("WOMAN", "60", "138.89"),
            ("STARTUP", "10", "666.67"),
            ("SMALL_BUSINESS", "50", "200.00"),
        ],
    )
    def test_achievement_matches_the_confirmed_numbers(
        self, seeded: Path, code: str, rate: str, expected: str
    ) -> None:
        """§13 의 목표 대비 달성률과 일치한다."""
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        result = self._calculator(seeded).calculate_achievement(
            _policy_id(seeded, code), Decimal(rate), period
        )
        assert result.achievement_rate == Decimal(expected)

    def test_end_to_end_through_the_new_api(self, seeded: Path, client: TestClient) -> None:
        """⭐ **API 로 목표비율을 넣고 대시보드가 그 값으로 계산한다.**

        저장 → 조회 → 계산이 한 줄로 이어지는지 보는 시험이다.
        """
        _put(client, 2026, "WOMAN", "60")
        _put(client, 2026, "STARTUP", "10")
        _put(client, 2026, "SMALL_BUSINESS", "50")

        body = client.get("/dashboard/summary?year=2026").json()

        assert body["total_purchase_amount"] == "1200000"
        by_code = {item["policy_code"]: item for item in body["policies"]}
        assert by_code["WOMAN"]["purchase_amount"] == "1000000"
        assert by_code["WOMAN"]["achievement_rate"] == "138.89"
        assert by_code["STARTUP"]["purchase_amount"] == "800000"
        assert by_code["STARTUP"]["achievement_rate"] == "666.67"
        assert by_code["SMALL_BUSINESS"]["purchase_amount"] == "1200000"
        assert by_code["SMALL_BUSINESS"]["achievement_rate"] == "200.00"
        # 목표비율을 넣지 않은 정책은 **미설정**으로 남는다.
        assert by_code["DISABLED"]["achievement_rate"] is None


# ======================================================================
# §9 · §10 대시보드 연결
# ======================================================================
class TestDashboardWiring:
    """대시보드가 **연도별** 목표비율을 읽는다."""

    def _service(self, db_path: Path) -> DashboardDataService:
        return DashboardDataService(
            ProcurementAchievementCalculator(
                PurchaseRepository(db_path),
                CertificationRepository(db_path),
                PolicyRepository(db_path),
            ),
            policy_repository=PolicyRepository(db_path),
            purchase_repository=PurchaseRepository(db_path),
            policy_target_repository=PolicyTargetRepository(db_path),
        )

    def test_the_year_selects_the_target(self, db_path: Path) -> None:
        """⭐ 2025년 조회는 2025년 목표비율을 쓴다."""
        targets = PolicyTargetRepository(db_path)
        woman = _policy_id(db_path, "WOMAN")
        targets.upsert(2025, woman, Decimal("40"))
        targets.upsert(2026, woman, Decimal("60"))

        service = self._service(db_path)
        summary_2025 = service.build_summary_from_registered_targets(
            PeriodFilter.for_year(2025, RESOLUTION_DATE)
        )
        rates = {s.policy_code: s.target_rate for s in summary_2025.policy_summaries}
        assert rates["WOMAN"] == Decimal("40")

    def test_a_year_without_a_target_stays_unset(self, db_path: Path) -> None:
        """⛔ 다른 연도 값을 끌어오지 않는다."""
        targets = PolicyTargetRepository(db_path)
        targets.upsert(2025, _policy_id(db_path, "WOMAN"), Decimal("40"))

        summary = self._service(db_path).build_summary_from_registered_targets(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )

        woman = next(s for s in summary.policy_summaries if s.policy_code == "WOMAN")
        assert woman.target_rate is None
        assert woman.achievement_rate is None
        assert woman.status is DashboardStatus.TARGET_RATE_NOT_SET

    def test_the_legacy_column_is_not_read(self, db_path: Path) -> None:
        """⭐ §8 — 새 경로는 ``Policy.target_rate`` 를 **읽지 않는다.**

        예전 컬럼에 값이 있어도, 그 연도의 목표비율이 없으면 **미설정**이다.
        """
        PolicyRepository(db_path).update_target_rate("WOMAN", Decimal("99"))

        summary = self._service(db_path).build_summary_from_registered_targets(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )

        woman = next(s for s in summary.policy_summaries if s.policy_code == "WOMAN")
        assert woman.target_rate is None
        assert woman.status is DashboardStatus.TARGET_RATE_NOT_SET

    def test_the_legacy_path_still_works_without_the_repository(self, db_path: Path) -> None:
        """저장소를 주입하지 않은 기존 호출부는 예전대로 동작한다(하위호환)."""
        PolicyRepository(db_path).update_target_rate("WOMAN", Decimal("60"))
        service = DashboardDataService(
            ProcurementAchievementCalculator(
                PurchaseRepository(db_path),
                CertificationRepository(db_path),
                PolicyRepository(db_path),
            ),
            policy_repository=PolicyRepository(db_path),
        )

        summary = service.build_summary_from_registered_targets(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )

        woman = next(s for s in summary.policy_summaries if s.policy_code == "WOMAN")
        assert woman.target_rate == Decimal("60")


# ======================================================================
# §11 화면
# ======================================================================
class TestScreen:
    """목표비율 입력 화면 — ⛔ 기업 선택 UI 를 만들지 않았다."""

    @pytest.fixture(scope="class")
    def index_html(self) -> str:
        from procurement.web.page import read_index_html

        return read_index_html()

    def test_the_card_exists(self, index_html: str) -> None:
        assert "목표비율 관리" in index_html
        assert 'id="pt-year"' in index_html
        assert 'id="pt-rows"' in index_html
        assert 'id="pt-save"' in index_html

    def test_the_screen_calls_the_new_endpoints(self, index_html: str) -> None:
        assert '"/policy-targets?year="' in index_html
        assert '"/policy-targets/"' in index_html

    def test_no_company_selection_ui(self, index_html: str) -> None:
        """⛔ §11-2 — 기업별 목표비율 화면을 만들지 않았다."""
        assert "기업별 목표비율" not in index_html
        assert "사업자등록번호별 목표비율" not in index_html
        # 목표비율 카드에 기업을 고르는 입력이 없다.
        assert 'id="pt-company' not in index_html

    def test_the_screen_does_not_hold_policy_names(self, index_html: str) -> None:
        """⛔ §11-1 — 정책명을 프론트에 하드코딩하지 않는다."""
        assert 'el("pt-input-" + item.policy_code)' in index_html
        assert "item.policy_name" in index_html

    def test_blank_means_unset_not_zero(self, index_html: str) -> None:
        """⛔ §10 — 빈칸을 0 으로 바꿔 보내지 않는다."""
        assert 'typed === "" ? null : typed' in index_html
        assert "미설정" in index_html

    def test_changing_the_year_refetches(self, index_html: str) -> None:
        """§11-3 — 연도를 바꾸면 그 연도 값을 다시 읽는다."""
        assert "loadPolicyTargets()" in index_html


# ======================================================================
# §16 금지사항이 지켜졌는가
# ======================================================================
class TestForbiddenThingsWereNotDone:
    """⛔ 하지 않기로 한 것들."""

    def test_no_company_axis_in_the_schema(self, db_path: Path) -> None:
        """⭐ 목표비율 테이블에 구매처 축이 없다."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_target)").fetchall()}
        conn.close()
        assert columns == {
            "policy_target_id",
            "year",
            "policy_id",
            "target_rate",
            "created_at",
            "updated_at",
        }
        assert "company_id" not in columns
        assert "business_no" not in columns

    def test_no_institution_table_was_added(self, db_path: Path) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert "institution" not in tables
        assert "organization" not in tables

    def test_purchase_has_no_institution_column(self, db_path: Path) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(purchase)").fetchall()}
        conn.close()
        assert "institution_id" not in columns

    def test_policy_target_rate_column_survives(self, db_path: Path) -> None:
        """⛔ §8 — 기존 컬럼을 지우지 않았다."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(policy)").fetchall()}
        conn.close()
        assert "target_rate" in columns

    def test_no_policy_beyond_the_confirmed_scope(self) -> None:
        """⛔ 확정 범위를 넘는 정책을 만들지 않았다.

        .. note::
            **기대값이 바뀐 이유** — 2026-09-03 PM 확정(``DECISIONS.md`` §0.22 ·
            STEP 97 §2)으로 정책 4종이 **확정을 받고** 추가되었다. STEP 93
            시점에는 미확정이었다. 막으려던 것 — *확정 없이 정책이 늘어나는
            것* — 은 그대로이며, 기준표만 확정본으로 바꿔 적었다.
        """
        from procurement.database.bootstrap import MVP_POLICY_SEEDS

        assert len(MVP_POLICY_SEEDS) == 9
        codes = {seed.policy_code for seed in MVP_POLICY_SEEDS}
        assert codes == {
            "SMALL_BUSINESS",
            "WOMAN",
            "DISABLED",
            "STARTUP",
            "GREEN",
            "SOCIAL_ENTERPRISE",
            "SOCIAL_COOPERATIVE",
            "DISABLED_STANDARD_WORKPLACE",
            "SELF_SUPPORT_VILLAGE",
        }

    def test_the_resolution_date_basis_is_unchanged(self) -> None:
        """⛔ 결의일자 기준을 바꾸지 않았다."""
        from procurement.core.config import settings

        assert settings.PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    def test_the_admin_service_has_no_company_path(self) -> None:
        """⛔ 서비스에 구매처별 목표비율 경로가 없다."""
        import inspect

        from procurement.admin.policy_target_admin import PolicyTargetAdminService

        source = inspect.getsource(PolicyTargetAdminService)
        assert "company" not in source.lower()
        assert "business_no" not in source


# ======================================================================
# 재실행 안전성 (§5)
# ======================================================================
class TestBootstrapIsIdempotent:
    """기존 DB 에 테이블이 더해져도 잃는 것이 없다."""

    def test_rerunning_bootstrap_keeps_the_targets(self, db_path: Path) -> None:
        targets = PolicyTargetRepository(db_path)
        targets.upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("60"))

        bootstrap(db_path)

        saved = targets.get(2026, _policy_id(db_path, "WOMAN"))
        assert saved is not None
        assert saved.target_rate == Decimal("60")

    def test_the_table_is_created_on_an_existing_db(self, tmp_path: Path) -> None:
        """구(舊) 스키마 DB 에도 테이블이 생긴다."""
        from procurement.database.bootstrap import init_db

        path = tmp_path / "old.db"
        # 목표비율 테이블 없이 만들어진 DB 를 흉내 낸다.
        PolicyRepository(path).create_table()

        init_db(path)

        assert PolicyTargetRepository(path).count() == 0  # 조회가 성공한다 = 테이블이 있다

    def test_health_check_requires_the_table(self, db_path: Path) -> None:
        from procurement.database.bootstrap import verify_bootstrap

        assert verify_bootstrap(db_path).healthy is True


def test_policy_not_found_error_is_reused() -> None:
    """⛔ 새 예외 체계를 만들지 않았다 — 기존 것을 쓴다."""
    from procurement.admin.policy_target_admin import PolicyTargetAdminService

    assert PolicyTargetAdminService is not None
    assert issubclass(PolicyNotFoundError, LookupError)
