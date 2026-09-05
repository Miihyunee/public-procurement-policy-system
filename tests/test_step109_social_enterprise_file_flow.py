"""
사회적기업 정책 — **FILE 등록 → 매칭 → 결의일자 판정 → 계산** 전 구간을 잠급니다.

⚠️ **실제 사회적기업 파일이 아직 도착하지 않았습니다.** 그래서 이 파일은
합성 데이터로 **구조가 실제로 동작하는지**를 확인합니다. 실제 파일이 오면
같은 경로를 그대로 태우면 됩니다 — ⛔ 새 업로드 경로를 만들지 않았습니다.

무엇을 지키는가 (지시서 §22)
============================

1. 사회적기업을 **FILE 로 등록**할 수 있다 (기존 정책 선택 업로드 그대로).
2. 사업자등록번호는 **숫자만 남겨** 정규화한다 — 하이픈이 있어도 같은 기업이다.
3. **정확히 일치**할 때만 매칭한다 — 비슷한 번호·같은 이름은 매칭하지 않는다.
4. 판정 기준일은 **결의일자**다. 시작일·종료일 **당일은 인정**, 밖은 제외.
5. 목표율 3% · 범위 TOTAL 로 달성률을 낸다.
6. 기업정보 미등록 → **조회불가**. 등록했는데 안 맞으면 → **0원(미해당)**.
7. 한 거래가 여러 정책에 해당하면 **각 정책에 독립적으로** 들어간다.
8. 본점·지점은 사업자등록번호가 다르면 **각각 따로** 본다.
9. 같은 사업자등록번호의 **여러 인증 이력**을 병합하지 않는다.

.. warning::
    ⛔ 인증상태(`인증`/`인증취소`)와 `인증취소일` 은 **판정에 쓰지 않습니다.**
    고객 확정 규칙이 없기 때문입니다(지시서 §8 · §9). 이 파일도 그 값들을
    만들어 내지 않으며, 모델에 그런 칸이 없다는 사실만 확인합니다.

.. note::
    합성 데이터만 씁니다. 실제 기업명·사업자등록번호는 넣지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Purchase
from procurement.models.certification import Certification as CertificationModel

#: 합성 사업자등록번호 — 체크섬만 맞춘 값이며 실제 업체의 번호가 아닙니다.
_SOCIAL = "1000000009"  # 사회적기업 본점
_BRANCH = "1000000014"  # 같은 조합의 지점 — **번호가 다르므로 따로 본다**
_OTHER = "1000000028"  # 목록에 없는 업체 — 미해당
_MULTI = "1000000033"  # 여러 정책에 동시에 해당하는 업체

#: 인증 유효기간.
_FROM = date(2026, 3, 1)
_TO = date(2026, 9, 30)

#: 목표율 — 🟢 2026-09-03 고객 확정(DECISIONS §0.24).
_TARGET_RATE = Decimal("3")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "social.db"
    init_db(path)
    seed_policies(path)
    # 목표비율은 seed 가 아니라 ``python -m procurement targets --year`` 이
    # 넣습니다(STEP 98). 화면에서 목표·달성률을 보려면 그 단계가 필요합니다.
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    assert main(["targets", "--year", "2030", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _company_file(path: Path, rows: list[tuple[str, str, str, str, str | None]]) -> Path:
    """정책 선택 업로드용 표준 양식 파일을 만듭니다 (⛔ 인증종류 칸 없음)."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(list(row))
    book.save(path)
    return path


def _purchase(db: Path, business_no: str, *, resolution: date, amount: str) -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name="합성업체",
            resolution_date=resolution,
            amount=Decimal(amount),
        )
    )


def _upload(
    client: TestClient, path: Path, policy_code: str = "SOCIAL_ENTERPRISE"
) -> dict[str, Any]:
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _summary(client: TestClient, code: str = "SOCIAL_ENTERPRISE") -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    found = [row for row in payload["policies"] if row["policy_code"] == code]
    assert found, payload
    return dict(found[0])


class TestThePolicyIsAlreadyWired:
    """§4 · §23 — 이미 되어 있는 것을 다시 만들지 않았다."""

    def test_the_policy_code_exists_with_the_resolution_date_rule(self, db: Path) -> None:
        policy = PolicyRepository(db).find_by_policy_code("SOCIAL_ENTERPRISE")
        assert policy is not None
        assert policy.policy_name == "사회적기업"
        # §7 — 판정 기준일은 결의일자다. ⛔ 계약일·지급일이 아니다.
        assert policy.evaluation_basis == "RESOLUTION_DATE"

    def test_file_registration_is_offered_for_this_policy(self, client: TestClient) -> None:
        rows = client.get("/companies/registration").json()["items"]
        row = next(r for r in rows if r["policy_code"] == "SOCIAL_ENTERPRISE")
        assert "FILE" in row["available_methods"]
        # 아직 아무것도 올리지 않았다 — 등록 기록이 없다.
        assert row["source"] is None

    def test_the_target_is_three_percent_on_the_total(self, client: TestClient) -> None:
        rows = client.get("/policy-targets", params={"year": 2026}).json()["items"]
        row = next(r for r in rows if r["policy_code"] == "SOCIAL_ENTERPRISE")
        assert Decimal(row["target_rate"]) == _TARGET_RATE

    def test_the_certification_has_no_status_or_cancellation_field(self) -> None:
        """§8 · §9 — ⛔ 인증상태·인증취소일을 담는 칸을 만들지 않았다.

        고객 확정 규칙이 없으므로 저장할 자리부터 만들지 않습니다. 자리가
        있으면 언젠가 누군가 그 값으로 판정하게 되고, 그 순간 아무도 정하지
        않은 업무규칙이 실적 숫자를 바꿉니다.
        """
        fields = set(CertificationModel.__dataclass_fields__)
        for absent in ("status", "certification_status", "cancelled_at", "cancellation_date"):
            assert absent not in fields, absent


class TestNotRegisteredIsNotZero:
    """§16 — 기업정보를 받은 적이 없으면 **조회불가**다."""

    def test_before_any_upload_the_policy_is_unavailable(
        self, client: TestClient, db: Path
    ) -> None:
        _purchase(db, _SOCIAL, resolution=date(2026, 5, 1), amount="1000")
        row = _summary(client)
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        # ⛔ 0원도 0%도 아니다 — 모른다는 것과 없다는 것을 구분한다.
        assert row["purchase_amount"] is None
        assert row["achievement_rate"] is None


class TestTheFileFlowEndToEnd:
    """§5 · §14 — 파일 한 장이 실적 숫자가 되기까지."""

    @pytest.fixture
    def uploaded(self, client: TestClient, db: Path, tmp_path: Path) -> TestClient:
        # 분모 10,000원 · 그중 사회적기업 거래 300원 → 3.0% → 달성률 100%
        _purchase(db, _SOCIAL, resolution=date(2026, 5, 1), amount="300")
        _purchase(db, _OTHER, resolution=date(2026, 5, 1), amount="9700")
        path = _company_file(
            tmp_path / "social.xlsx",
            [("100-00-00009", "합성사회적기업", "가나다", "2026-03-01", "2026-09-30")],
        )
        result = _upload(client, path)
        assert result["stored"] is True
        assert result["certifications"] == 1
        client.post("/purchases/rematch")
        return client

    def test_the_hyphenated_number_matches_the_purchase(self, uploaded: TestClient) -> None:
        """§6 — 하이픈은 지우고 숫자만 남긴다. 새 유사매칭은 없다."""
        row = _summary(uploaded)
        assert row["purchase_amount"] == "300"

    def test_the_rate_and_achievement_use_the_existing_formula(self, uploaded: TestClient) -> None:
        """§15 — 300 / 10,000 = 3.0% · 목표 3% → 달성률 100%."""
        row = _summary(uploaded)
        assert row["total_purchase_amount"] == "10000"
        assert Decimal(row["target_rate"]) == _TARGET_RATE
        assert Decimal(row["achievement_rate"]) == Decimal("100")
        assert row["status"] == "NORMAL"

    def test_a_company_outside_the_list_is_simply_not_counted(self, uploaded: TestClient) -> None:
        """§16 — 등록은 되어 있고 목록에 없는 업체는 **미해당**이다.

        조회불가와 달리 금액이 나옵니다 — 셀 수 있는데 이 업체가 아닐 뿐입니다.
        """
        row = _summary(uploaded)
        assert row["status"] != "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] == "300"  # 9,700원짜리 거래는 들어가지 않았다


class TestExactMatchOnly:
    """§6 — 정확히 같은 번호만 매칭한다."""

    def test_a_near_miss_number_does_not_match(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _OTHER, resolution=date(2026, 5, 1), amount="1000")
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", "2026-03-01", "2026-09-30")],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _summary(client)["purchase_amount"] == "0"

    def test_branches_are_kept_apart(self, client: TestClient, db: Path, tmp_path: Path) -> None:
        """§10 — 본점·지점은 번호가 다르면 각각 독립이다.

        ⛔ 법인등록번호가 같다는 이유로 합치지 않습니다. 여기서는 **지점만**
        목록에 넣었으므로 본점 거래는 실적이 아닙니다.
        """
        _purchase(db, _SOCIAL, resolution=date(2026, 5, 1), amount="700")  # 본점
        _purchase(db, _BRANCH, resolution=date(2026, 5, 1), amount="300")  # 지점
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_BRANCH, "합성사회적기업 지점", "가나다", "2026-03-01", "2026-09-30")],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _summary(client)["purchase_amount"] == "300"


class TestTheResolutionDateBoundaries:
    """§7 · §22 — 시작일·종료일 **당일은 인정**, 밖은 제외."""

    @pytest.mark.parametrize(
        ("resolution", "expected"),
        [
            (date(2026, 2, 28), "0"),  # 시작일 하루 전 → 제외
            (_FROM, "1000"),  # 시작일 당일 → 인정
            (date(2026, 6, 15), "1000"),  # 기간 중 → 인정
            (_TO, "1000"),  # 종료일 당일 → 인정
            (date(2026, 10, 1), "0"),  # 종료일 다음날 → 제외
        ],
    )
    def test_only_purchases_inside_the_period_count(
        self, client: TestClient, db: Path, tmp_path: Path, resolution: date, expected: str
    ) -> None:
        _purchase(db, _SOCIAL, resolution=resolution, amount="1000")
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", _FROM.isoformat(), _TO.isoformat())],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _summary(client)["purchase_amount"] == expected

    def test_the_issue_date_is_not_the_basis(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 신고기준일(``issue_date``)로 판정하지 않는다.

        결의일자는 기간 밖, 신고기준일은 기간 안에 두었습니다. 신고기준일을
        본다면 실적이 잡히는데, 잡히면 안 됩니다.
        """
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_SOCIAL,
                company_name="합성업체",
                resolution_date=date(2026, 12, 1),  # 기간 밖
                issue_date=date(2026, 5, 1),  # 기간 안 — ⛔ 판정에 쓰이면 안 된다
                amount=Decimal("1000"),
            )
        )
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", _FROM.isoformat(), _TO.isoformat())],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _summary(client)["purchase_amount"] == "0"


class TestSeveralCertificationHistories:
    """§11 — 같은 사업자등록번호의 여러 인증 이력을 병합하지 않는다."""

    def test_two_periods_are_stored_separately_and_both_judge(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _SOCIAL, resolution=date(2022, 5, 1), amount="100")  # 옛 기간 안
        _purchase(db, _SOCIAL, resolution=date(2023, 8, 1), amount="500")  # 두 기간 사이
        _purchase(db, _SOCIAL, resolution=date(2026, 5, 1), amount="300")  # 새 기간 안
        path = _company_file(
            tmp_path / "social.xlsx",
            [
                (_SOCIAL, "합성사회적기업", "가나다", "2022-01-01", "2022-12-31"),
                (_SOCIAL, "합성사회적기업", "가나다", "2026-03-01", "2026-09-30"),
            ],
        )
        result = _upload(client, path)
        # ⛔ 둘을 하나로 합치지 않았다 — 인증 두 건이 그대로 남는다.
        assert result["certifications"] == 2
        client.post("/purchases/rematch")

        certifications = CertificationRepository(db).find_by_policy(
            _policy_id(db, "SOCIAL_ENTERPRISE")
        )
        assert sorted(c.valid_from for c in certifications) == [
            date(2022, 1, 1),
            date(2026, 3, 1),
        ]
        # 2026년 화면에는 2026년 거래만 — 사이에 낀 500원은 어느 기간에도 없다.
        assert _summary(client)["purchase_amount"] == "300"


class TestPoliciesAreCountedIndependently:
    """§17 · §22 — 한 거래가 여러 정책에 동시에 들어간다. ⛔ 중복 제거 없음."""

    def test_one_purchase_lands_in_three_policies(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _MULTI, resolution=date(2026, 5, 1), amount="1000")
        for code in ("SOCIAL_ENTERPRISE", "WOMAN", "STARTUP"):
            path = _company_file(
                tmp_path / f"{code}.xlsx",
                [(_MULTI, "합성업체", "가나다", _FROM.isoformat(), _TO.isoformat())],
            )
            _upload(client, path, code)
        client.post("/purchases/rematch")

        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        amounts = {row["policy_code"]: row["purchase_amount"] for row in payload["policies"]}
        # 같은 1,000원이 세 정책에 각각 들어간다 — 나누지도, 빼지도 않는다.
        assert amounts["SOCIAL_ENTERPRISE"] == "1000"
        assert amounts["STARTUP"] == "1000"
        # 여성기업은 유형별 목표라 총액 칸이 비어 있다(DECISIONS §0.26) —
        # 실적 자체는 계산기가 같은 값을 낸다.
        assert payload["total_purchase_amount"] == "1000"


class TestAnOpenEndedSocialEnterpriseCertification:
    """🟢 2026-09-04 고객 확정 — 사회적기업도 종료일이 없을 수 있다(§0.27)."""

    def test_a_blank_end_date_is_accepted_and_keeps_counting(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _SOCIAL, resolution=date(2026, 2, 1), amount="100")  # 인증 시작 전
        _purchase(db, _SOCIAL, resolution=date(2030, 5, 1), amount="900")  # 아주 뒤 → 인정
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", _FROM.isoformat(), None)],
        )
        result = _upload(client, path)
        assert result["stored"] is True
        client.post("/purchases/rematch")

        stored = CertificationRepository(db).find_by_policy(_policy_id(db, "SOCIAL_ENTERPRISE"))
        assert [c.valid_to for c in stored] == [None]  # ⛔ 지어낸 종료일이 없다

        # 2030년 거래까지 인정된다 — 끝이 없기 때문이다.
        payload = client.get("/dashboard/summary", params={"year": 2030}).json()
        row = next(r for r in payload["policies"] if r["policy_code"] == "SOCIAL_ENTERPRISE")
        assert row["purchase_amount"] == "900"


class TestTheExistingPoliciesAreUntouched:
    """§18 — 사회적기업을 올려도 다른 정책 판정 규칙은 그대로다."""

    def test_the_other_policies_keep_their_rules(self, db: Path) -> None:
        expected = {
            "WOMAN": "RESOLUTION_DATE",
            "DISABLED": "RESOLUTION_DATE",
            "STARTUP": "RESOLUTION_OR_CONTRACT_DATE",
            "SOCIAL_ENTERPRISE": "RESOLUTION_DATE",
        }
        repository = PolicyRepository(db)
        for code, basis in expected.items():
            policy = repository.find_by_policy_code(code)
            assert policy is not None
            assert policy.evaluation_basis == basis, code

    def test_registering_one_policy_does_not_certify_another(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 사회적기업 목록을 올렸다고 그 업체가 창업기업이 되지 않는다."""
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", _FROM.isoformat(), _TO.isoformat())],
        )
        _upload(client, path)
        repository = CertificationRepository(db)
        assert repository.find_by_policy(_policy_id(db, "STARTUP")) == []
        assert len(repository.find_by_policy(_policy_id(db, "SOCIAL_ENTERPRISE"))) == 1


class TestNothingInventsACancellationRule:
    """§8 · §21 — ⛔ 인증취소일로 판정하는 코드가 어디에도 없다."""

    @pytest.mark.parametrize("term", ["인증취소", "cancell", "revoke", "취소일"])
    def test_the_term_appears_nowhere_in_the_source(self, term: str) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        hits = [
            path.name
            for path in source_root.rglob("*.py")
            if term.lower() in path.read_text(encoding="utf-8").lower()
        ]
        assert hits == [], hits

    def test_the_upload_form_has_no_cancellation_column(self) -> None:
        from procurement.uploads.company_format import COMPANY_REQUIRED_HEADERS

        assert "인증취소일" not in COMPANY_REQUIRED_HEADERS
        assert "인증상태" not in COMPANY_REQUIRED_HEADERS


def _unused() -> Certification:  # pragma: no cover - import 유지용
    return Certification(company_id=1, policy_id=1, valid_from=_FROM, valid_to=_TO)


class TestAFarFutureEndDateIsJustADate:
    """§5 — `9999-12-31` 을 시스템이 **어떻게 다루는지**를 적어 둡니다.

    실제 사회적기업 자료에 종료일이 `9999-12-31` 로 적혀 있는 경우가 있다고
    합니다. 지금 시스템에는 그 값에 대한 **특별 규칙이 하나도 없습니다** —
    그냥 아주 먼 날짜입니다. 저장한 값 그대로 남고, 판정은 여느 종료일과
    똑같이 `기준일 <= 종료일` 입니다.

    .. warning::
        ⛔ *"9999-12-31 은 무기한을 뜻한다"* 는 **고객이 확정한 적 없는
        규칙**입니다. 그래서 만들지 않았습니다. 이 시험은 규칙을 정하는
        것이 아니라 **지금 무슨 일이 일어나는지**를 적어 둘 뿐이며,
        고객이 뜻을 알려 주면 그때 기대값을 바꾸고 사유를 적습니다.

    .. note::
        종료일이 **없는**(``NULL``) 인증과는 다른 이야기입니다. 그쪽은
        2026-09-04 고객 확정이 있고 사회적기업·사회적협동조합에만
        적용됩니다(DECISIONS §0.27).
    """

    def test_it_is_stored_as_written_and_not_turned_into_null(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", "2026-01-01", "9999-12-31")],
        )
        _upload(client, path)

        repository = CertificationRepository(db)
        stored = repository.find_by_policy(_policy_id(db, "SOCIAL_ENTERPRISE"))
        assert [c.valid_to for c in stored] == [date(9999, 12, 31)]
        # ⛔ 「무기한」으로 해석해 NULL 로 바꾸지 않았다.
        raw = repository.execute("SELECT valid_to FROM certification")
        assert [row["valid_to"] for row in raw] == ["9999-12-31"]

    def test_it_judges_like_any_other_end_date(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _SOCIAL, resolution=date(2025, 12, 31), amount="100")  # 시작 전 → 제외
        _purchase(db, _SOCIAL, resolution=date(2026, 5, 1), amount="300")  # 안 → 인정
        path = _company_file(
            tmp_path / "social.xlsx",
            [(_SOCIAL, "합성사회적기업", "가나다", "2026-01-01", "9999-12-31")],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _summary(client)["purchase_amount"] == "300"
