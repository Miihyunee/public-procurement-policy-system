"""
tests.test_policy_company_registration

**정책별 기업정보 등록**과 **조회불가** (STEP 96).

이 파일이 지키는 것은 하나입니다.

    ⭐ **"기업정보를 받지 못했다" 와 "해당 기업이 없다" 는 다른 사실이다.**

    ========================  ==========================================
    받은 적 있음 + 목록에 있음   해당
    받은 적 있음 + 목록에 없음   미해당
    받은 적 **없음**            **조회불가** — ⛔ 미해당도 0원도 아니다
    ========================  ==========================================

.. warning::
    ⛔ **합성 데이터만 사용합니다.** 사업자등록번호는 체크섬을 만족하는
    형식값이며 실제 거래처가 아닙니다. 실제 고객 파일을 쓰지 않았습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.dashboard.models import DashboardStatus
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.purchase import Purchase
from procurement.uploads.company_format import (
    POLICY_SCOPED_REQUIRED_HEADERS,
    policy_scoped_header_row,
)

ADMIN_TOKEN = "step96-token-not-a-real-secret"

#: 합성 사업자등록번호(체크섬 만족).
BIZ_A = "220-81-62517"
BIZ_B = "104-81-24017"
BIZ_C = "110-81-14429"
NORM_A = "2208162517"
NORM_B = "1048124017"
NORM_C = "1108114429"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "step96.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=db_path, admin_token=ADMIN_TOKEN))


@pytest.fixture
def registry(db_path: Path) -> PolicyCompanySourceRepository:
    return PolicyCompanySourceRepository(db_path)


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None
    assert policy.policy_id is not None
    return policy.policy_id


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _policy_file(path: Path, rows: list[list[object]]) -> Path:
    """**정책을 고르고 올리는** 파일 — ⛔ 인증종류 칸이 없다."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(policy_scoped_header_row()))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def _row(business_no: str, name: str) -> list[object]:
    return [business_no, name, "홍길동", date(2026, 1, 1), date(2026, 12, 31)]


def _register(client: TestClient, path: Path, policy_code: str) -> dict[str, object]:
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _summary(client: TestClient, year: int = 2026) -> dict[str, dict[str, object]]:
    body = client.get(f"/dashboard/summary?year={year}").json()
    return {item["policy_code"]: item for item in body["policies"]}


def _seed_purchase(db_path: Path, business_no: str, amount: str) -> None:
    PurchaseRepository(db_path).insert(
        Purchase(
            business_no=business_no,
            company_name="거래처",
            amount=Decimal(amount),
            resolution_date=date(2026, 6, 1),
        )
    )


# ======================================================================
# §23-A  정책별 파일 등록
# ======================================================================
class TestPolicyScopedUpload:
    """사용자가 고른 정책으로 저장한다."""

    def test_the_file_needs_no_policy_column(self, tmp_path: Path) -> None:
        """⭐ §5 — 파일에 ``인증종류`` 칸이 **필요 없다.**

        정책별 원본 명단에는 그런 칸이 없다. 요구하면 사용자가 원본을 고쳐야
        하고, 고치는 순간 원본이 아니게 된다.
        """
        assert "인증종류" not in POLICY_SCOPED_REQUIRED_HEADERS
        assert POLICY_SCOPED_REQUIRED_HEADERS == (
            "사업자등록번호",
            "기업명",
            "대표자명",
            "유효시작일",
            "유효종료일",
        )

    def test_file_is_stored_under_the_chosen_policy(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """여성기업으로 고르면 **여성기업 인증**으로 저장된다."""
        path = _policy_file(tmp_path / "woman.xlsx", [_row(BIZ_A, "가나산업")])

        body = _register(client, path, "WOMAN")

        assert body["stored"] is True
        assert body["certifications"] == 1
        company = CompanyRepository(db_path).find_by_business_no(NORM_A)
        assert company is not None
        assert company.company_id is not None
        saved = CertificationRepository(db_path).find_by_company(company.company_id)
        assert [cert.policy_id for cert in saved] == [_policy_id(db_path, "WOMAN")]

    def test_the_same_file_under_a_different_policy_is_a_different_list(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⭐ §1.1 — 같은 파일도 **고른 정책이 다르면 다른 목록**이다.

        ⛔ 파일 내용을 보고 정책을 재분류하지 않는다.
        """
        path = _policy_file(tmp_path / "list.xlsx", [_row(BIZ_A, "가나산업")])

        _register(client, path, "WOMAN")
        _register(client, path, "STARTUP")

        company = CompanyRepository(db_path).find_by_business_no(NORM_A)
        assert company is not None
        assert company.company_id is not None
        saved = {
            cert.policy_id
            for cert in CertificationRepository(db_path).find_by_company(company.company_id)
        }
        assert saved == {_policy_id(db_path, "WOMAN"), _policy_id(db_path, "STARTUP")}

    def test_registration_is_recorded(
        self,
        client: TestClient,
        db_path: Path,
        tmp_path: Path,
        registry: PolicyCompanySourceRepository,
    ) -> None:
        """등록했다는 **사실**이 남는다 — 조회불가 판정의 근거다."""
        path = _policy_file(tmp_path / "woman.xlsx", [_row(BIZ_A, "가나산업")])

        _register(client, path, "WOMAN")

        record = registry.get(_policy_id(db_path, "WOMAN"))
        assert record is not None
        assert record.source == "FILE"
        assert record.certification_count == 1
        assert record.source_label == "woman.xlsx"

    def test_reregistering_keeps_one_record(
        self,
        client: TestClient,
        db_path: Path,
        tmp_path: Path,
        registry: PolicyCompanySourceRepository,
    ) -> None:
        path = _policy_file(tmp_path / "woman.xlsx", [_row(BIZ_A, "가나산업")])

        _register(client, path, "WOMAN")
        _register(client, path, "WOMAN")

        assert len(registry.find_all()) == 1

    def test_an_empty_list_still_counts_as_registered(
        self,
        client: TestClient,
        db_path: Path,
        tmp_path: Path,
        registry: PolicyCompanySourceRepository,
    ) -> None:
        """⭐ 목록을 받았는데 **한 곳도 우리 거래처가 아닐 수 있다.**

        그것은 "판단할 수 없다" 가 아니라 **"전부 미해당"** 이므로 등록완료다.
        """
        path = _policy_file(tmp_path / "empty.xlsx", [])

        _register(client, path, "WOMAN")

        record = registry.get(_policy_id(db_path, "WOMAN"))
        assert record is not None
        assert record.certification_count == 0


# ======================================================================
# §23-B  미등록 → 조회불가 (가장 중요)
# ======================================================================
class TestNotRegisteredMeansUnknown:
    """⛔ 미등록을 미해당·0% 로 처리하지 않는다."""

    def test_unregistered_policy_is_not_available(self, client: TestClient, db_path: Path) -> None:
        """§8 — 기업정보가 없으면 **조회불가**다."""
        _seed_purchase(db_path, NORM_A, "1000000")

        item = _summary(client)["SMALL_BUSINESS"]

        assert item["status"] == DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        assert item["status_label"] == "기업정보 미등록"

    def test_unregistered_is_never_zero(self, client: TestClient, db_path: Path) -> None:
        """⛔ §22-8 — 0원·0% 로 계산하지 않는다."""
        _seed_purchase(db_path, NORM_A, "1000000")

        item = _summary(client)["SMALL_BUSINESS"]

        for key in ("purchase_amount", "achievement_rate", "shortage_rate"):
            assert item[key] is None, key
            assert item[key] != "0"
            assert item[key] != 0

    def test_a_target_rate_does_not_make_it_calculable(
        self, client: TestClient, db_path: Path
    ) -> None:
        """⭐ 목표비율이 있어도 **기업정보가 없으면** 계산하지 않는다.

        둘은 다른 이유이며, 기업정보 쪽이 먼저다.
        """
        _seed_purchase(db_path, NORM_A, "1000000")
        PolicyTargetRepository(db_path).upsert(
            2026, _policy_id(db_path, "SMALL_BUSINESS"), Decimal("50")
        )

        item = _summary(client)["SMALL_BUSINESS"]

        assert item["status"] == DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        assert item["achievement_rate"] is None

    def test_registered_but_no_target_is_a_different_state(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⭐ §16 — 기업정보는 있고 목표비율만 없으면 **목표율 미설정**이다.

        조회불가와 혼동하지 않는다.
        """
        _seed_purchase(db_path, NORM_A, "1000000")
        _register(client, _policy_file(tmp_path / "w.xlsx", [_row(BIZ_A, "가나")]), "WOMAN")

        item = _summary(client)["WOMAN"]

        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value
        assert item["status_label"] == "목표율 미설정"

    def test_the_two_unknown_states_are_distinct_values(self) -> None:
        """⭐ 두 '계산하지 않음' 은 **다른 값**이다. 라벨도 다르다."""
        values = {
            DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value,
            DashboardStatus.TARGET_RATE_NOT_SET.value,
        }
        assert len(values) == 2
        assert DashboardStatus.COMPANY_DATA_NOT_REGISTERED.label == "기업정보 미등록"
        assert DashboardStatus.TARGET_RATE_NOT_SET.label == "목표율 미설정"

    def test_registration_listing_marks_unregistered(self, client: TestClient) -> None:
        """§2 화면이 쓸 목록 — 미등록 정책도 **빠지지 않는다.**"""
        body = client.get("/companies/registration").json()

        by_code = {item["policy_code"]: item for item in body["items"]}
        assert set(by_code) == {"SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP"}
        assert all(item["registered"] is False for item in body["items"])
        assert all(item["status_label"] == "미등록" for item in body["items"])


# ======================================================================
# §23-D · §23-E  해당 / 미해당 / 복수 정책
# ======================================================================
class TestMatchingStates:
    """해당 · 미해당 · 조회불가 세 상태."""

    @pytest.fixture
    def seeded(self, client: TestClient, db_path: Path, tmp_path: Path) -> Path:
        """지출 3건(A·B·C)과 여성기업·창업기업 목록을 등록합니다.

        여성기업 목록 = A, B / 창업기업 목록 = A, C
        중소기업·장애인기업 = **미등록**(조회불가)
        """
        for business_no, amount in ((NORM_A, "600000"), (NORM_B, "400000"), (NORM_C, "200000")):
            _seed_purchase(db_path, business_no, amount)

        _register(
            client,
            _policy_file(tmp_path / "woman.xlsx", [_row(BIZ_A, "A기업"), _row(BIZ_B, "B기업")]),
            "WOMAN",
        )
        _register(
            client,
            _policy_file(tmp_path / "startup.xlsx", [_row(BIZ_A, "A기업"), _row(BIZ_C, "C기업")]),
            "STARTUP",
        )
        client.post("/purchases/rematch")
        for code in ("WOMAN", "STARTUP"):
            PolicyTargetRepository(db_path).upsert(2026, _policy_id(db_path, code), Decimal("50"))
        return db_path

    def test_matched_company_counts(self, seeded: Path, client: TestClient) -> None:
        """§23-D — 목록에 있는 사업자만 실적에 들어간다."""
        summary = _summary(client)

        assert summary["WOMAN"]["purchase_amount"] == "1000000"  # A + B
        assert summary["STARTUP"]["purchase_amount"] == "800000"  # A + C

    def test_one_purchase_counts_in_every_policy_it_belongs_to(
        self, seeded: Path, client: TestClient
    ) -> None:
        """⭐ §1.2 — A기업 60만원이 **두 정책 실적에 모두** 들어간다."""
        summary = _summary(client)
        summed = Decimal(str(summary["WOMAN"]["purchase_amount"])) + Decimal(
            str(summary["STARTUP"]["purchase_amount"])
        )

        assert summed == Decimal("1800000")
        assert summed > Decimal(str(summary["WOMAN"]["total_purchase_amount"]))

    def test_a_company_absent_from_the_list_is_simply_excluded(
        self, seeded: Path, client: TestClient
    ) -> None:
        """§23-E — 목록에 없는 사업자(C)는 여성기업 실적에 없다 = 미해당."""
        summary = _summary(client)

        # 여성기업 실적 100만 = A(60만) + B(40만). C(20만)는 빠져 있다.
        assert summary["WOMAN"]["purchase_amount"] == "1000000"

    def test_unregistered_policies_stay_unknown(self, seeded: Path, client: TestClient) -> None:
        """⭐ 같은 화면에서 등록된 정책은 계산되고 미등록 정책은 조회불가다."""
        summary = _summary(client)

        assert summary["WOMAN"]["status"] != DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        for code in ("SMALL_BUSINESS", "DISABLED"):
            assert summary[code]["status"] == DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
            assert summary[code]["purchase_amount"] is None

    def test_the_denominator_is_shared(self, seeded: Path, client: TestClient) -> None:
        body = client.get("/dashboard/summary?year=2026").json()
        assert body["total_purchase_amount"] == "1200000"


# ======================================================================
# §23-F  유효기간 · §23-G 목표비율
# ======================================================================
class TestValidityAndTargets:
    """결의일자 기준 판정과 정책별 목표비율."""

    def test_resolution_date_outside_validity_is_excluded(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """§10 — 결의일자가 인증기간 밖이면 실적에 들어가지 않는다."""
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=NORM_A,
                company_name="가나",
                amount=Decimal("1000000"),
                resolution_date=date(2026, 6, 1),
            )
        )
        # 인증기간을 2027년으로 두어 결의일자(2026-06-01)가 밖에 있게 한다.
        rows = [[BIZ_A, "가나산업", "홍길동", date(2027, 1, 1), date(2027, 12, 31)]]
        _register(client, _policy_file(tmp_path / "w.xlsx", rows), "WOMAN")
        client.post("/purchases/rematch")
        PolicyTargetRepository(db_path).upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("50"))

        item = _summary(client)["WOMAN"]

        # 등록은 되었으므로 조회불가가 아니다. 다만 실적이 0이다 — **미해당**이다.
        assert item["status"] != DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        assert item["purchase_amount"] == "0"

    def test_targets_stay_per_policy(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """§15 — 목표비율은 정책마다 독립이다."""
        _seed_purchase(db_path, NORM_A, "1000000")
        for code in ("WOMAN", "STARTUP"):
            _register(client, _policy_file(tmp_path / f"{code}.xlsx", [_row(BIZ_A, "가나")]), code)
        client.post("/purchases/rematch")
        PolicyTargetRepository(db_path).upsert(2026, _policy_id(db_path, "WOMAN"), Decimal("50"))
        PolicyTargetRepository(db_path).upsert(2026, _policy_id(db_path, "STARTUP"), Decimal("10"))

        summary = _summary(client)

        assert summary["WOMAN"]["target_rate"] == "50"
        assert summary["STARTUP"]["target_rate"] == "10"
        assert summary["WOMAN"]["achievement_rate"] == "200.00"
        assert summary["STARTUP"]["achievement_rate"] == "1000.00"


# ======================================================================
# §3  조회 선택지는 실제 구현된 정책에만
# ======================================================================
class TestAvailableMethods:
    """⛔ 없는 선택지를 화면에 만들지 않는다."""

    def test_small_business_has_no_api_option(self, client: TestClient) -> None:
        """⭐ 중소기업은 조회 출처가 코드에 없다 → 파일 방식만."""
        body = client.get("/companies/registration").json()
        by_code = {item["policy_code"]: item for item in body["items"]}

        assert by_code["SMALL_BUSINESS"]["available_methods"] == ["FILE"]

    @pytest.mark.parametrize("code", ["WOMAN", "DISABLED", "STARTUP"])
    def test_api_capable_policies_offer_both(self, client: TestClient, code: str) -> None:
        body = client.get("/companies/registration").json()
        by_code = {item["policy_code"]: item for item in body["items"]}

        assert by_code[code]["available_methods"] == ["FILE", "API"]

    def test_no_direct_production_policy_is_offered(self, client: TestClient) -> None:
        """⛔ §3 — 직접생산확인은 실적 집계에 쓰지 않는다."""
        body = client.get("/companies/registration").json()

        codes = {item["policy_code"] for item in body["items"]}
        assert "DIRECT_PRODUCTION" not in codes
        assert "DIRECT_PRODUCTION_SMPP" not in codes


# ======================================================================
# §22  하지 않기로 한 것
# ======================================================================
class TestForbidden:
    """⛔ 금지 목록."""

    def test_no_new_policy_was_registered(self, db_path: Path) -> None:
        """⛔ §22-12 — 고객 미확정 정책을 임의로 추가하지 않았다."""
        codes = {policy.policy_code for policy in PolicyRepository(db_path).find_all()}
        assert codes == {"SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN"}

    def test_registration_table_has_no_company_axis(self, db_path: Path) -> None:
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(policy_company_source)").fetchall()
        }
        conn.close()
        assert "business_no" not in columns
        assert "company_id" not in columns

    def test_the_calculator_was_not_changed(self) -> None:
        """⛔ §22-15 — 계산기는 등록 여부를 모른다."""
        import inspect

        from procurement.calculators import ProcurementAchievementCalculator

        source = inspect.getsource(ProcurementAchievementCalculator)
        assert "policy_company_source" not in source
        assert "NOT_REGISTERED" not in source

    def test_matching_is_still_exact_business_number(self) -> None:
        """⛔ §22-5·6 — 유사도 매칭을 넣지 않았다."""
        import inspect

        from procurement.matchers.company_matcher import CompanyMatcher

        source = inspect.getsource(CompanyMatcher).lower()
        for term in ("fuzzy", "ratio", "similar", "levenshtein", "difflib"):
            assert term not in source, term


# ======================================================================
# §2 화면
# ======================================================================
class TestScreen:
    """정책별 기업정보 등록 화면."""

    @pytest.fixture(scope="class")
    def index_html(self) -> str:
        from procurement.web.page import read_index_html

        return read_index_html()

    def test_the_card_exists(self, index_html: str) -> None:
        assert "기업정보 등록" in index_html
        assert 'id="cr-rows"' in index_html

    def test_the_screen_asks_the_server_for_the_policy_list(self, index_html: str) -> None:
        """⛔ 정책 목록·정책명을 화면이 들고 있지 않는다."""
        assert '"/companies/registration"' in index_html
        card = index_html.split("기업정보 등록")[1].split("</section>")[0]
        for name in ("중소기업", "여성기업", "장애인기업", "창업기업"):
            assert name not in card, name

    def test_the_screen_sends_the_chosen_policy(self, index_html: str) -> None:
        """⭐ §5 — policy_code 를 명시해서 보낸다."""
        assert "policy_code: item.policy_code" in index_html

    def test_the_screen_says_unregistered_not_unmatched(self, index_html: str) -> None:
        """⛔ 화면 문구가 '미해당' 으로 읽히지 않는다."""
        assert "조회불가" in index_html
        assert "해당 기업이 없다는 뜻이 아닙니다" in index_html

    def test_rematch_is_reused(self, index_html: str) -> None:
        """⛔ 새 매칭 기능을 만들지 않았다."""
        assert '"/purchases/rematch"' in index_html
