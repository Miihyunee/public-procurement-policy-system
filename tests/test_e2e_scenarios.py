"""
End-to-End 시나리오 검증 (A~E).

**외부 API 인증키와 고객 실데이터가 없어도 계산 엔진 전체를 검증**하기 위한
테스트입니다. 다음 흐름 전체가 하나의 시나리오로 성공하는지 확인합니다::

    구매데이터
      → 사업자번호 정규화
      → Company 매칭
      → Certification 연결
      → 판정 기준일 결정
      → 인증 유효성 판단
      → 구매금액 집계
      → 목표율 적용
      → 달성률 계산
      → Dashboard 표시

시나리오 정의는 ``docs/E2E_TEST_SCENARIOS.md`` 와 1:1로 대응합니다.

.. important::
    여기서 만드는 인증·기업 데이터는 **pytest 전용 Fixture** 입니다.
    운영 데이터나 Bootstrap seed 에 넣지 않습니다. 실제 인증정보는 외부 API
    에서 수집해야 하며, 본 테스트는 **그 데이터가 확보되었다고 가정했을 때
    계산 엔진이 정상 동작하는지**만 검증합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers import PurchaseImporter
from procurement.models import Certification, Company, Policy

#: 체크섬까지 유효한 사업자등록번호 (경고 없는 정상 케이스)
BUSINESS_NO = "1018116293"
#: 위 번호의 하이픈 표기 — 정규화 동작을 함께 검증하기 위해 사용
BUSINESS_NO_HYPHENATED = "101-81-16293"
#: 등록되지 않은 기업의 사업자번호 (전체 구매액에만 포함)
OTHER_BUSINESS_NO = "2088155147"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "e2e.db"
    init_db(path)
    return path


@pytest.fixture
def importer(db_path: Path) -> PurchaseImporter:
    return PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path))


# ----------------------------------------------------------------------
# 시나리오 구성 헬퍼
# ----------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class PolicyFixture:
    """시나리오에서 사용할 정책 정의."""

    policy_code: str
    policy_name: str
    evaluation_basis: str
    target_rate: Decimal


def _register_company(db_path: Path, business_no: str = BUSINESS_NO) -> int:
    """기업정보를 등록합니다(실제로는 외부 수집 대상)."""
    saved = CompanyRepository(db_path).insert(
        Company(
            business_no=business_no,
            company_name="가기업",
            representative_name="김대표",
        )
    )
    assert saved.company_id is not None
    return saved.company_id


def _register_policy(db_path: Path, fixture: PolicyFixture) -> int:
    """정책과 목표율을 등록합니다."""
    saved = PolicyRepository(db_path).insert(
        Policy(
            policy_code=fixture.policy_code,
            policy_name=fixture.policy_name,
            evaluation_basis=fixture.evaluation_basis,
            target_rate=fixture.target_rate,
        )
    )
    assert saved.policy_id is not None
    return saved.policy_id


def _register_certification(
    db_path: Path,
    company_id: int,
    policy_id: int,
    valid_from: date,
    valid_to: date,
) -> None:
    """인증정보를 등록합니다(실제로는 외부 API 수집 대상)."""
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company_id,
            policy_id=policy_id,
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )


def _purchase_row(**overrides: object) -> dict[str, Any]:
    """고객 구매데이터 한 행(컬럼 매핑 완료 상태)."""
    row: dict[str, Any] = {
        "business_no": BUSINESS_NO_HYPHENATED,
        "company_name": "가기업",
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-15",
        "amount": "3,000,000",
    }
    row.update(overrides)
    return row


def _dashboard(db_path: Path) -> dict[str, Any]:
    """Dashboard API 응답을 정책코드로 조회 가능한 형태로 돌려줍니다."""
    payload: dict[str, Any] = TestClient(create_app(db_path)).get("/dashboard/summary").json()
    payload["by_code"] = {item["policy_code"]: item for item in payload["policies"]}
    return payload


# ----------------------------------------------------------------------
# 시나리오 A — 중소기업 (지급일 기준)
# ----------------------------------------------------------------------
class TestScenarioASmallBusiness:
    """중소기업: 지급일이 인증 유효기간 내인 구매를 실적으로 인정합니다.

    구성: 정책 구매 3,000,000 / 전체 10,000,000 = 30%, 목표 50% → 달성률 60%.
    """

    POLICY = PolicyFixture(
        policy_code="SMALL_BUSINESS",
        policy_name="중소기업",
        evaluation_basis="PAYMENT_DATE",
        target_rate=Decimal("50"),
    )

    @pytest.fixture
    def prepared(self, db_path: Path, importer: PurchaseImporter) -> Path:
        company_id = _register_company(db_path)
        policy_id = _register_policy(db_path, self.POLICY)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 12, 31)
        )
        importer.import_rows(
            [
                _purchase_row(amount="3000000"),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="7000000"),
            ]
        )
        return db_path

    def test_full_chain_produces_achievement_rate(self, prepared: Path) -> None:
        """전체 흐름이 달성률까지 이어집니다."""
        item = _dashboard(prepared)["by_code"]["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "3000000"
        assert item["target_rate"] == "50"
        assert item["achievement_rate"] == "60.00"
        assert item["shortage_rate"] == "40.00"
        assert item["status"] == "SHORTAGE"
        assert item["status_label"] == "부족"

    def test_total_includes_unmatched_purchase(self, prepared: Path) -> None:
        """미등록 기업 구매도 전체 구매액(분모)에는 포함됩니다."""
        assert _dashboard(prepared)["total_purchase_amount"] == "10000000"

    def test_hyphenated_business_no_was_normalized(self, prepared: Path) -> None:
        """하이픈 표기로 들어온 구매가 정규화되어 매칭되었습니다."""
        purchases = PurchaseRepository(prepared).find_all()
        matched = [p for p in purchases if p.company_id is not None]
        assert len(matched) == 1
        assert matched[0].business_no == BUSINESS_NO


# ----------------------------------------------------------------------
# 시나리오 B — 여성기업 (지급일 기준)
# ----------------------------------------------------------------------
class TestScenarioBWomanBusiness:
    """여성기업: 정책 코드만 다를 뿐 동일한 흐름으로 계산됩니다.

    구성: 정책 구매 1,000,000 / 전체 10,000,000 = 10%, 목표 10% → 달성률 100%.
    """

    POLICY = PolicyFixture(
        policy_code="WOMAN",
        policy_name="여성기업",
        evaluation_basis="PAYMENT_DATE",
        target_rate=Decimal("10"),
    )

    @pytest.fixture
    def prepared(self, db_path: Path, importer: PurchaseImporter) -> Path:
        company_id = _register_company(db_path)
        policy_id = _register_policy(db_path, self.POLICY)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 12, 31)
        )
        importer.import_rows(
            [
                _purchase_row(amount="1000000"),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="9000000"),
            ]
        )
        return db_path

    def test_achievement_reaches_target(self, prepared: Path) -> None:
        item = _dashboard(prepared)["by_code"]["WOMAN"]
        assert item["purchase_amount"] == "1000000"
        assert item["achievement_rate"] == "100.00"
        assert item["shortage_rate"] == "0.00"
        assert item["status"] == "NORMAL"
        assert item["status_label"] == "정상"


# ----------------------------------------------------------------------
# 시나리오 C — 창업기업 (계약일 기준)
# ----------------------------------------------------------------------
class TestScenarioCStartupUsesContractDate:
    """창업기업: **지급일이 아니라 계약일**로 판정하는지 검증합니다.

    인증 유효기간은 2026-01-01 ~ 2026-06-30 이며, 두 구매를 넣습니다.

    - 구매 ①: 계약일 2026-05-01(유효) / 지급일 2026-08-01(만료 후) → **인정**
    - 구매 ②: 계약일 2026-08-01(만료 후) / 지급일 2026-05-01(유효) → **제외**

    두 구매의 날짜가 서로 반대이므로, 지급일로 판정했다면 결과가 뒤바뀝니다.
    """

    POLICY = PolicyFixture(
        policy_code="STARTUP",
        policy_name="창업기업",
        evaluation_basis="CONTRACT_DATE",
        target_rate=Decimal("20"),
    )

    @pytest.fixture
    def prepared(self, db_path: Path, importer: PurchaseImporter) -> Path:
        company_id = _register_company(db_path)
        policy_id = _register_policy(db_path, self.POLICY)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        importer.import_rows(
            [
                # ① 계약일 유효 / 지급일 만료 후 → 계약일 기준이므로 인정
                _purchase_row(
                    contract_date="2026-05-01", payment_date="2026-08-01", amount="2000000"
                ),
                # ② 계약일 만료 후 / 지급일 유효 → 계약일 기준이므로 제외
                _purchase_row(
                    contract_date="2026-08-01", payment_date="2026-05-01", amount="3000000"
                ),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="5000000"),
            ]
        )
        return db_path

    def test_only_contract_date_within_validity_is_counted(self, prepared: Path) -> None:
        """계약일이 유효기간 내인 구매만 집계됩니다."""
        item = _dashboard(prepared)["by_code"]["STARTUP"]
        assert item["purchase_amount"] == "2000000"

    def test_payment_date_would_have_given_different_result(self, prepared: Path) -> None:
        """지급일로 판정했다면 3,000,000 이 되었을 것입니다(판정 기준 구분 확인)."""
        item = _dashboard(prepared)["by_code"]["STARTUP"]
        assert item["purchase_amount"] != "3000000"

    def test_achievement_rate(self, prepared: Path) -> None:
        """정책 구매 2,000,000 / 전체 10,000,000 = 20%, 목표 20% → 100%."""
        item = _dashboard(prepared)["by_code"]["STARTUP"]
        assert item["achievement_rate"] == "100.00"
        assert item["status"] == "NORMAL"


# ----------------------------------------------------------------------
# 시나리오 D — 인증기간 만료
# ----------------------------------------------------------------------
class TestScenarioDExpiredCertification:
    """인증기간이 만료된 시점의 구매는 정책 실적에서 제외됩니다.

    인증 유효기간 2026-01-01 ~ 2026-06-30, 구매 지급일 2026-08-01(만료 후).
    """

    POLICY = PolicyFixture(
        policy_code="SMALL_BUSINESS",
        policy_name="중소기업",
        evaluation_basis="PAYMENT_DATE",
        target_rate=Decimal("50"),
    )

    @pytest.fixture
    def prepared(self, db_path: Path, importer: PurchaseImporter) -> Path:
        company_id = _register_company(db_path)
        policy_id = _register_policy(db_path, self.POLICY)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        importer.import_rows(
            [_purchase_row(payment_date="2026-08-01", amount="5000000")]
        )
        return db_path

    def test_expired_purchase_is_excluded_from_policy(self, prepared: Path) -> None:
        item = _dashboard(prepared)["by_code"]["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "0"
        assert Decimal(item["achievement_rate"]) == Decimal("0")

    def test_expired_purchase_still_counts_in_total(self, prepared: Path) -> None:
        """인증이 만료되어도 전체 구매액에는 포함됩니다."""
        assert _dashboard(prepared)["total_purchase_amount"] == "5000000"

    def test_boundary_date_is_inclusive(self, db_path: Path, importer: PurchaseImporter) -> None:
        """유효기간 마지막 날(경계값)은 포함됩니다."""
        company_id = _register_company(db_path)
        policy_id = _register_policy(db_path, self.POLICY)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        importer.import_rows(
            [_purchase_row(payment_date="2026-06-30", amount="5000000")]
        )
        item = _dashboard(db_path)["by_code"]["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "5000000"


# ----------------------------------------------------------------------
# 시나리오 E — 미매칭 후 재매칭
# ----------------------------------------------------------------------
class TestScenarioEUnmatchedThenRematch:
    """구매데이터가 먼저 들어오고 기업정보가 나중에 확보되는 경우.

    미매칭으로 보관했다가 ``rematch()`` 로 연결하면 달성률에 반영됩니다.
    유입 순서와 무관하게 최종 결과가 같아지는지 확인합니다.
    """

    POLICY = PolicyFixture(
        policy_code="SMALL_BUSINESS",
        policy_name="중소기업",
        evaluation_basis="PAYMENT_DATE",
        target_rate=Decimal("50"),
    )

    def test_step1_purchase_first_is_unmatched(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """① 구매데이터만 먼저 들어오면 미매칭으로 보관됩니다."""
        _register_policy(db_path, self.POLICY)
        report = importer.import_rows([_purchase_row(amount="5000000")])

        assert report.stored_count == 1
        assert report.matched_count == 0
        assert PurchaseRepository(db_path).find_all()[0].company_id is None

    def test_step2_company_is_not_auto_created(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """② 미매칭이어도 Company 를 임의로 만들지 않습니다(방안 C)."""
        _register_policy(db_path, self.POLICY)
        importer.import_rows([_purchase_row(amount="5000000")])
        assert CompanyRepository(db_path).find_by_business_no(BUSINESS_NO) is None

    def test_step3_rematch_links_and_dashboard_reflects(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """③ 기업·인증정보 확보 후 재매칭하면 달성률에 반영됩니다."""
        policy_id = _register_policy(db_path, self.POLICY)
        importer.import_rows(
            [
                _purchase_row(amount="5000000"),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="5000000"),
            ]
        )

        before = _dashboard(db_path)["by_code"]["SMALL_BUSINESS"]
        assert before["purchase_amount"] == "0"

        # 외부에서 기업·인증정보가 들어온 상황
        company_id = _register_company(db_path)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 12, 31)
        )

        assert importer.rematch() == 1

        after = _dashboard(db_path)["by_code"]["SMALL_BUSINESS"]
        assert after["purchase_amount"] == "5000000"
        assert after["achievement_rate"] == "100.00"
        assert after["status"] == "NORMAL"

    def test_order_of_arrival_does_not_change_result(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """기업정보를 먼저 넣은 경우와 결과가 같아야 합니다."""
        policy_id = _register_policy(db_path, self.POLICY)
        company_id = _register_company(db_path)
        _register_certification(
            db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 12, 31)
        )
        importer.import_rows(
            [
                _purchase_row(amount="5000000"),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="5000000"),
            ]
        )

        item = _dashboard(db_path)["by_code"]["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "5000000"
        assert item["achievement_rate"] == "100.00"


# ----------------------------------------------------------------------
# 통합 — 여러 정책이 함께 계산되는지
# ----------------------------------------------------------------------
class TestAllPoliciesTogether:
    """여러 정책이 한 응답에 함께 계산되는지 확인합니다."""

    def test_multiple_policies_share_denominator(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """같은 기업이 여러 정책 인증을 보유하면 각각 집계됩니다."""
        company_id = _register_company(db_path)
        small = _register_policy(
            db_path,
            PolicyFixture(
                policy_code="SMALL_BUSINESS",
                policy_name="중소기업",
                evaluation_basis="PAYMENT_DATE",
                target_rate=Decimal("50"),
            ),
        )
        woman = _register_policy(
            db_path,
            PolicyFixture(
                policy_code="WOMAN",
                policy_name="여성기업",
                evaluation_basis="PAYMENT_DATE",
                target_rate=Decimal("30"),
            ),
        )
        for policy_id in (small, woman):
            _register_certification(
                db_path, company_id, policy_id, date(2026, 1, 1), date(2026, 12, 31)
            )

        importer.import_rows(
            [
                _purchase_row(amount="3000000"),
                _purchase_row(business_no=OTHER_BUSINESS_NO, amount="7000000"),
            ]
        )

        by_code = _dashboard(db_path)["by_code"]
        # 같은 구매가 두 정책 실적에 동시에 반영된다(공통 계산 원칙).
        assert by_code["SMALL_BUSINESS"]["purchase_amount"] == "3000000"
        assert by_code["WOMAN"]["purchase_amount"] == "3000000"
        assert by_code["SMALL_BUSINESS"]["achievement_rate"] == "60.00"  # 30% / 50%
        assert by_code["WOMAN"]["achievement_rate"] == "100.00"  # 30% / 30%

    def test_policy_without_target_rate_is_shown_as_not_set(
        self, db_path: Path, importer: PurchaseImporter
    ) -> None:
        """목표율이 없는 정책은 계산하지 않고 미설정으로 표시됩니다."""
        PolicyRepository(db_path).insert(
            Policy(policy_code="GREEN", policy_name="녹색제품", target_rate=None)
        )
        importer.import_rows([_purchase_row(amount="1000000")])

        item = _dashboard(db_path)["by_code"]["GREEN"]
        assert item["status"] == "TARGET_RATE_NOT_SET"
        assert item["achievement_rate"] is None
