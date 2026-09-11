"""
STEP 75 — 사업자등록번호 조사 기능이 **안전한가**.

STEP 74 는 저장·조회 표기를 맞췄고, 그와 함께 조사·정리 기능을 두었습니다. 이
파일은 그 기능들이 **운영 중에 데이터를 건드리지 않는지**를 잠급니다.

무엇을 지키는가
===============

1. 조사(`survey_business_no_formats` · `find_normalization_conflicts`)는
   **읽기만** 한다.
2. 부트스트랩 · 앱 시작 · 업로드 · 조회 · 매칭 · 재매칭 · 대시보드 어디에서도
   `company.business_no` 가 **저절로 바뀌지 않는다.**
3. 저장 · 결합키 · 검색 **세 규칙이 계속 갈라져 있다.**
4. 인증 수집도 같은 조회를 쓰므로 옛 표기 기업을 **건너뛰지 않는다.**

.. warning::
    ⛔ **이 파일은 실데이터 조사 결과가 아닙니다.** 실제 고객 DB 는 이 환경에
    없습니다(`docs/BUSINESS_NO_DATA_AUDIT.md` §1). 여기 숫자는 전부 합성이며,
    확인하는 것은 **동작의 안전성**이지 고객 데이터의 현황이 아닙니다.

.. note::
    옛 표기 상태를 만들려면 저장소를 거치지 않고 넣어야 합니다(:func:`_legacy`).
    운영 코드는 그렇게 넣지 않습니다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.collectors.client import SOURCE_WOMAN, CertificationApiClient
from procurement.collectors.sync_service import SKIP_COMPANY_NOT_FOUND, CertificationSyncService
from procurement.collectors.transport import HttpResponse
from procurement.core.business_no_storage import to_storage_business_no
from procurement.core.period import PAYMENT_DATE
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers.business_no import business_no_search_key, normalize_business_no
from procurement.models import Purchase
from procurement.uploads.format import header_row

# 합성 사업자등록번호 — 인쇄 표기와 저장 표기.
_PRINTED = "220-81-62517"
_STORED = "2208162517"
_SPACED = "220 81 62517"

_DAY = date(2026, 3, 1)

_AUDIT = Path(__file__).resolve().parents[1] / "docs" / "BUSINESS_NO_DATA_AUDIT.md"

#: 여성기업 확인 응답 — 명세서 샘플 구조를 따르는 고정 문자열.
_WOMAN_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <certSeCode>03</certSeCode>
    <issuInstt>한국여성경제인협회</issuInstt>
    <validPdBeginDe>20240401</validPdBeginDe>
    <validPdEndDe>20270331</validPdEndDe>
  </item></items></body>
</response>
"""


@pytest.fixture(scope="module")
def text() -> str:
    """조사 기록 문서 본문."""
    return _AUDIT.read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "audit.db"
    bootstrap(path)
    return path


def _legacy(db: Path, business_no: str, name: str = "옛 표기로 등록된 기업") -> int:
    """저장소를 거치지 않고 넣습니다 — **정리되지 않은 기존 데이터** 재현."""
    now = datetime.now().isoformat(sep=" ")
    connection = sqlite3.connect(db)
    cursor = connection.execute(
        "INSERT INTO company (business_no, company_name, representative_name,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (business_no, name, "홍길동", now, now),
    )
    connection.commit()
    company_id = int(cursor.lastrowid or 0)
    connection.close()
    return company_id


def _stored_numbers(db: Path) -> list[str]:
    rows = CompanyRepository(db).execute("SELECT business_no FROM company ORDER BY company_id")
    return [str(row["business_no"]) for row in rows]


def _purchase(db: Path, business_no: str, amount: str = "1000") -> int:
    saved = PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name="합성 거래처",
            contract_date=_DAY,
            payment_date=_DAY,
            resolution_date=_DAY,
            description="합성 구매",
            budget_account="일반운영비",
            amount=Decimal(amount),
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _excel(path: Path, business_no: str) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(header_row()))
    sheet.append(
        [_DAY, _DAY, _DAY, "합성 거래처", business_no, 1000, _DAY, "업로드 구매", "일반운영비"]
    )
    workbook.save(path)
    workbook.close()
    return path


# ======================================================================
# 1. 조사는 읽기만 한다
# ======================================================================
class TestSurveyingIsReadOnly:
    """⛔ 무엇이 있는지 세는 동안 무엇도 바뀌지 않는다."""

    @pytest.fixture
    def mixed(self, db: Path) -> Path:
        """표기가 뒤섞인 기존 데이터."""
        _legacy(db, _PRINTED, "하이픈")
        _legacy(db, _SPACED, "공백")
        _legacy(db, "1198102316", "숫자만")
        _legacy(db, "119-81 02316", "하이픈+공백")
        return db

    def test_the_survey_does_not_write(self, mixed: Path) -> None:
        before = _stored_numbers(mixed)
        CompanyRepository(mixed).survey_business_no_formats()
        assert _stored_numbers(mixed) == before

    def test_finding_conflicts_does_not_write(self, mixed: Path) -> None:
        before = _stored_numbers(mixed)
        CompanyRepository(mixed).find_normalization_conflicts()
        assert _stored_numbers(mixed) == before

    def test_looking_up_does_not_write(self, mixed: Path) -> None:
        """⛔ 옛 표기 행을 찾아냈다고 그 자리에서 고치지 않는다."""
        repository = CompanyRepository(mixed)
        before = _stored_numbers(mixed)

        found = repository.find_by_business_no(_STORED)

        assert found is not None
        assert found.business_no == _PRINTED  # 찾았지만 저장값은 그대로
        assert _stored_numbers(mixed) == before

    def test_the_survey_sees_every_notation(self, mixed: Path) -> None:
        survey = CompanyRepository(mixed).survey_business_no_formats()
        assert survey.total == 4
        assert survey.with_hyphen == 2  # 하이픈 · 하이픈+공백
        assert survey.with_space == 2  # 공백 · 하이픈+공백
        assert survey.digits_only == 1

    def test_a_conflict_is_reported_not_resolved(self, db: Path) -> None:
        _legacy(db, _PRINTED, "가기업")
        _legacy(db, _STORED, "가기업(다른 등록)")
        repository = CompanyRepository(db)

        conflicts = repository.find_normalization_conflicts()

        assert [conflict.business_no for conflict in conflicts] == [_STORED]
        assert repository.count() == 2  # 합치지 않았다
        assert set(_stored_numbers(db)) == {_PRINTED, _STORED}

    def test_the_notation_buckets_can_be_derived(self, mixed: Path) -> None:
        """문서(§2.2)가 안내하는 분류가 실제로 만들어진다."""
        buckets: dict[str, int] = {}
        for row in CompanyRepository(mixed).execute("SELECT business_no FROM company"):
            raw = str(row["business_no"])
            clean = to_storage_business_no(raw)
            has_space = any(char.isspace() for char in raw)
            if not clean:
                kind = "빈 값"
            elif raw == clean:
                kind = "숫자만"
            elif "-" in raw and has_space:
                kind = "하이픈+공백"
            elif "-" in raw:
                kind = "하이픈"
            elif has_space:
                kind = "공백"
            else:
                kind = "기타 구분자"
            buckets[kind] = buckets.get(kind, 0) + 1

        assert buckets == {"하이픈": 1, "공백": 1, "숫자만": 1, "하이픈+공백": 1}


# ======================================================================
# 2. 운영 중에는 저장값이 바뀌지 않는다
# ======================================================================
class TestNothingRewritesStoredNumbers:
    """⛔ 사람이 시키지 않으면 `company.business_no` 는 그대로다."""

    @pytest.fixture
    def legacy_db(self, db: Path) -> Path:
        company_id = _legacy(db, _PRINTED)
        PolicyRepository(db).update_target_rate("SMALL_BUSINESS", Decimal("30"))
        assert company_id == 1
        return db

    def test_bootstrap_does_not_rewrite(self, legacy_db: Path) -> None:
        bootstrap(legacy_db)
        assert _stored_numbers(legacy_db) == [_PRINTED]

    def test_creating_the_app_does_not_rewrite(self, legacy_db: Path) -> None:
        create_app(legacy_db, period_date_field=PAYMENT_DATE)
        assert _stored_numbers(legacy_db) == [_PRINTED]

    def test_uploading_does_not_rewrite(self, legacy_db: Path, tmp_path: Path) -> None:
        client = TestClient(create_app(legacy_db, period_date_field=PAYMENT_DATE))
        response = client.post(
            "/uploads/purchases",
            json={"file_path": str(_excel(tmp_path / "up.xlsx", _STORED)), "year": 2026},
        )

        assert response.json()["stored"] is True
        assert _stored_numbers(legacy_db) == [_PRINTED]

    def test_matching_does_not_rewrite(self, legacy_db: Path) -> None:
        """⭐ 옛 표기 기업과 연결되더라도 **기업 쪽 값은 건드리지 않는다.**"""
        purchase_id = _purchase(legacy_db, _STORED)
        client = TestClient(create_app(legacy_db, period_date_field=PAYMENT_DATE))

        client.post("/purchases/rematch")

        purchase = PurchaseRepository(legacy_db).find_by_id(purchase_id)
        assert purchase is not None
        assert purchase.company_id == 1  # 연결은 되었다
        assert _stored_numbers(legacy_db) == [_PRINTED]  # 저장값은 그대로

    def test_reading_the_dashboard_does_not_rewrite(self, legacy_db: Path) -> None:
        _purchase(legacy_db, _STORED)
        client = TestClient(create_app(legacy_db, period_date_field=PAYMENT_DATE))

        assert client.get("/dashboard/summary?year=2026").status_code == 200
        assert client.get("/dashboard/data-status").status_code == 200
        assert client.get("/dashboard/unmatched-companies").status_code == 200
        assert _stored_numbers(legacy_db) == [_PRINTED]

    def test_reviewing_does_not_rewrite(self, legacy_db: Path) -> None:
        _purchase(legacy_db, _STORED)
        client = TestClient(create_app(legacy_db, period_date_field=PAYMENT_DATE))

        assert client.get("/reviews?page=1&page_size=10").status_code == 200
        assert _stored_numbers(legacy_db) == [_PRINTED]

    def test_only_an_explicit_apply_rewrites(self, legacy_db: Path) -> None:
        """정리는 사람이 `apply=True` 로 시켜야 일어난다."""
        repository = CompanyRepository(legacy_db)

        repository.normalize_stored_business_numbers()
        assert _stored_numbers(legacy_db) == [_PRINTED]

        repository.normalize_stored_business_numbers(apply=True)
        assert _stored_numbers(legacy_db) == [_STORED]


# ======================================================================
# 3. 세 규칙은 계속 갈라져 있다
# ======================================================================
class TestThreeRulesRegression:
    """저장 · 결합키 · 검색."""

    def test_the_join_key_still_demands_a_full_number(self) -> None:
        assert normalize_business_no(_PRINTED).value == _STORED
        assert normalize_business_no("22081").value is None

    def test_storage_keeps_the_digits(self) -> None:
        assert to_storage_business_no(_PRINTED) == _STORED
        assert to_storage_business_no(_SPACED) == _STORED

    def test_search_accepts_a_partial_number(self) -> None:
        assert business_no_search_key(_PRINTED) == _STORED
        assert business_no_search_key("220-81") == "22081"

    def test_a_partial_number_never_matches_a_company(self, db: Path) -> None:
        """⛔ 검색에서 통하는 것이 매칭에서 통하면 엉뚱한 회사의 실적이 된다."""
        _legacy(db, _PRINTED)
        repository = CompanyRepository(db)

        assert business_no_search_key("22081")  # 검색 키는 만들어진다
        assert repository.find_by_business_no("22081") is None
        assert repository.exists("22081") is False

    def test_a_partial_number_does_not_link_a_purchase(self, db: Path) -> None:
        _legacy(db, _PRINTED)
        _purchase(db, "22081")
        client = TestClient(create_app(db, period_date_field=PAYMENT_DATE))

        client.post("/purchases/rematch")

        assert PurchaseRepository(db).find_unmatched() != []


# ======================================================================
# 4. 인증 수집도 옛 표기 기업을 찾는다
# ======================================================================
class _StubTransport:
    """준비된 응답을 돌려주는 전송 대역. ⛔ 외부 서버에 접속하지 않습니다."""

    def __init__(self, body: str) -> None:
        self._body = body

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """준비된 응답을 반환합니다."""
        del url, params, timeout
        return HttpResponse(status=200, body=self._body)


class TestCertificationSyncFindsLegacyCompanies:
    """⭐ 조사에서 드러난 것 — 인증 수집도 같은 조회를 쓴다.

    기업이 옛 표기로 저장되어 있으면 인증 수집이 **조용히 건너뛰고** 있었다.
    인증이 없으면 정책 분자도 0 이므로, 매칭 실패와 같은 결과가 **한 단계
    앞에서** 벌어진다.
    """

    def _service(self, db: Path) -> CertificationSyncService:
        return CertificationSyncService(
            client=CertificationApiClient(
                smpp_api_key="test-smpp-key",
                startup_api_key="test-startup-key",
                transport=_StubTransport(_WOMAN_OK),
            ),
            company_repository=CompanyRepository(db),
            policy_repository=PolicyRepository(db),
            certification_repository=CertificationRepository(db),
        )

    def test_a_legacy_company_is_no_longer_skipped(self, db: Path) -> None:
        company_id = _legacy(db, _PRINTED)
        result = self._service(db).sync_one(SOURCE_WOMAN, _STORED, stdr_date=date(2026, 8, 14))

        assert result.skip_reason != SKIP_COMPANY_NOT_FOUND
        assert result.saved == 1
        assert len(CertificationRepository(db).find_by_company(company_id)) == 1

    def test_an_unknown_number_is_still_skipped(self, db: Path) -> None:
        """⛔ 넓어진 것이 아니다 — 기업이 없으면 여전히 건너뛴다."""
        result = self._service(db).sync_one(SOURCE_WOMAN, "9999999999", stdr_date=date(2026, 8, 14))

        assert result.skip_reason == SKIP_COMPANY_NOT_FOUND
        assert result.saved == 0

    def test_syncing_does_not_rewrite_the_company(self, db: Path) -> None:
        _legacy(db, _PRINTED)
        self._service(db).sync_one(SOURCE_WOMAN, _STORED, stdr_date=date(2026, 8, 14))

        assert _stored_numbers(db) == [_PRINTED]


# ======================================================================
# 5. 조사 기록 문서
# ======================================================================
class TestTheAuditDocument:
    """⛔ 합성 결과를 실데이터 조사 결과처럼 적지 않았는가."""

    def test_the_audit_exists(self) -> None:
        assert _AUDIT.exists()

    def test_it_states_the_real_database_was_absent(self, text: str) -> None:
        assert "실제 고객 DB 부재로" in text
        assert "합성 데이터로 대신 조사하지 않았다" in text

    def test_unknown_results_are_marked_unknown(self, text: str) -> None:
        """실데이터 항목은 🔴 미확인이어야 한다."""
        assert text.count("🔴 **미확인**") >= 4

    def test_candidates_are_not_reported_as_confirmed(self, text: str) -> None:
        assert "후보 금액 ≠ 분자 증가액" in text or "왜 후보 금액 ≠ 분자 증가액인가" in text
        assert "분자가 N 원 증가한다          ← 틀렸다" in text

    def test_the_certification_finding_is_recorded(self, text: str) -> None:
        assert "인증 테이블에는 사업자등록번호가 **없다**" in text

    def test_merging_stays_a_human_decision(self, text: str) -> None:
        assert "자동 병합하지 않는다" in text

    def test_no_business_rule_was_decided(self, text: str) -> None:
        for item in ("W-1-2", "Q5-8", "Q5-9", "W-6"):
            assert item in text
        assert "하나도 정하지 않았다" in text
