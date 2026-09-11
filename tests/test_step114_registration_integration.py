"""
운영 흐름 통합 점검 — 정책별 등록 · 재등록 · 일괄매칭 · 독립 계산.

    정책마다 FILE/API 선택 → 기업목록 등록 → 공공구매 **1회** 업로드
    → 정책별 사업자번호 일괄 매칭 → 정책별 독립 계산

무엇을 지키는가 (지시서 §2 · §5 · §6 · §7 · §8 · §9 · §15 · §16)
================================================================

1. 정책마다 데이터 소스를 **따로** 고른다. 한쪽 선택이 다른 쪽에 영향 없음.
2. 한 정책의 목록이 **다른 정책을 등록하지 않는다** (⛔ 타인증구분 자동등록 없음).
3. 구매 데이터 **한 번** 올리면 등록된 정책 전부와 매칭된다.
4. 등록되지 않은 정책 때문에 **업로드·계산이 실패하지 않는다**.
5. 미등록 → 조회불가(NULL). 등록 후 미매칭 → 미해당(0원). ⛔ 섞이지 않는다.
6. 한 거래가 여러 정책에 **각각** 들어간다. ⛔ 중복 제거 없음.
7. 목표율이 정책 사이로 **새지 않는다**.
8. 사업자등록번호는 **표기가 달라도 같은 기업**이다. ⛔ 틀린 번호를 고치지 않는다.
9. 🟡 **재등록은 «누적»이다** — 현재 동작을 있는 그대로 적어 둔다(§15).

.. warning::
    ⛔ 이 파일은 업무규칙을 정하지 않습니다. 삭제·폐기·이력 정책이 확정되면
    9번의 기대값을 바꾸고 사유를 적습니다.

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
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값이며 실제 업체의 번호가 아닙니다.
_A = "1000000009"
_B = "1000000014"
_C = "1000000028"
_D = "1000000033"

_FROM = date(2026, 1, 1)
_TO = date(2026, 12, 31)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "integration.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _one(business_no: str) -> list[list[object]]:
    return [[business_no, "합성기업", "가나다", _FROM.isoformat(), _TO.isoformat()]]


def _upload(client: TestClient, path: Path, code: str) -> dict[str, Any]:
    response = client.post("/companies/upload", json={"file_path": str(path), "policy_code": code})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _purchase(db: Path, business_no: str, *, amount: str) -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name="합성업체",
            resolution_date=date(2026, 5, 1),
            amount=Decimal(amount),
        )
    )


def _summary(client: TestClient) -> dict[str, dict[str, Any]]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    return {row["policy_code"]: dict(row) for row in payload["policies"]}


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


class TestEachPolicyChoosesItsOwnSource:
    """§2 — 정책마다 따로 고른다."""

    def test_every_policy_offers_file_and_some_offer_api(self, client: TestClient) -> None:
        rows = {r["policy_code"]: r for r in client.get("/companies/registration").json()["items"]}
        assert len(rows) == 8
        for code, row in rows.items():
            assert "FILE" in row["available_methods"], code
            assert row["source"] is None  # 아직 아무것도 고르지 않았다

    def test_choosing_one_policy_does_not_choose_another(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _file(tmp_path / "w.xlsx", _one(_A)), "WOMAN")
        rows = {r["policy_code"]: r for r in client.get("/companies/registration").json()["items"]}
        assert rows["WOMAN"]["source"] == "FILE"
        for other in ("STARTUP", "DISABLED", "SOCIAL_ENTERPRISE"):
            assert rows[other]["source"] is None, other


class TestOnePolicyListNeverRegistersAnother:
    """§5 — ⛔ 파일 안의 다른 인증 표시로 자동 등록하지 않는다."""

    def test_the_certification_lands_only_on_the_chosen_policy(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _file(tmp_path / "w.xlsx", _one(_A)), "WOMAN")

        assert len(CertificationRepository(db).find_by_policy(_policy_id(db, "WOMAN"))) == 1
        for other in ("STARTUP", "DISABLED", "SOCIAL_ENTERPRISE", "SMALL_BUSINESS"):
            assert CertificationRepository(db).find_by_policy(_policy_id(db, other)) == [], other

    def test_the_same_company_can_belong_to_several_policies(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """같은 기업이 여러 정책에 해당하는 것은 **정상**이다 — 각각 올렸을 때만."""
        for code in ("WOMAN", "STARTUP", "DISABLED"):
            _upload(client, _file(tmp_path / f"{code}.xlsx", _one(_A)), code)

        assert CompanyRepository(db).count() == 1  # 기업은 하나
        assert CertificationRepository(db).count() == 3  # 인증은 정책마다


class TestOneUploadMatchesEveryRegisteredPolicy:
    """§6 — 구매 데이터를 한 번 올리면 등록된 정책 전부와 매칭된다."""

    def test_a_single_rematch_feeds_every_policy(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _A, amount="1000")
        _purchase(db, _B, amount="9000")
        for code in ("WOMAN", "STARTUP", "DISABLED"):
            _upload(client, _file(tmp_path / f"{code}.xlsx", _one(_A)), code)

        client.post("/purchases/rematch")  # ⭐ 한 번만 부른다

        rows = _summary(client)
        assert rows["STARTUP"]["purchase_amount"] == "1000"
        assert rows["DISABLED"]["purchase_amount"] == "1000"

    def test_unregistered_policies_do_not_break_the_others(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """§11 — 중소기업·자활용사촌이 비어 있어도 나머지가 계산된다."""
        _purchase(db, _A, amount="1000")
        _upload(client, _file(tmp_path / "s.xlsx", _one(_A)), "STARTUP")
        assert client.post("/purchases/rematch").status_code == 200

        rows = _summary(client)
        assert rows["STARTUP"]["achievement_rate"] is not None  # 계산된다
        assert rows["SMALL_BUSINESS"]["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert rows["SELF_SUPPORT_VILLAGE"]["status"] == "COMPANY_DATA_NOT_REGISTERED"


class TestUnavailableIsNotNoMatch:
    """§7 · §8 — 조회불가와 미해당은 다르다."""

    def test_the_two_states_differ_side_by_side(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _B, amount="1000")  # 어느 목록에도 없는 업체
        _upload(client, _file(tmp_path / "s.xlsx", _one(_A)), "STARTUP")
        client.post("/purchases/rematch")

        rows = _summary(client)
        # 등록했고 맞는 업체가 없다 → 미해당(0원). 셀 수 있었다.
        assert rows["STARTUP"]["purchase_amount"] == "0"
        assert Decimal(rows["STARTUP"]["achievement_rate"]) == Decimal("0")
        # 등록한 적이 없다 → 조회불가(NULL). ⛔ 0원도 0% 도 아니다.
        assert rows["DISABLED"]["purchase_amount"] is None
        assert rows["DISABLED"]["achievement_rate"] is None
        assert rows["DISABLED"]["status"] == "COMPANY_DATA_NOT_REGISTERED"


class TestPoliciesAreCountedIndependently:
    """§9 — ⛔ 중복 제거 없음. 정책별 합계가 전체보다 커질 수 있다."""

    def test_one_purchase_counts_in_four_policies(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _A, amount="1000")
        for code in ("STARTUP", "DISABLED", "SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE"):
            _upload(client, _file(tmp_path / f"{code}.xlsx", _one(_A)), code)
        client.post("/purchases/rematch")

        rows = _summary(client)
        for code in ("STARTUP", "DISABLED", "SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE"):
            assert rows[code]["purchase_amount"] == "1000", code
        # 분모는 한 번만 센다. 합계 4,000원 > 전체 1,000원 — 정상이다.
        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        assert payload["total_purchase_amount"] == "1000"


class TestTargetsDoNotLeakBetweenPolicies:
    """§10 · §17 — 목표율이 정책 사이로 새지 않는다."""

    def test_every_policy_keeps_its_own_target(self, client: TestClient) -> None:
        payload = client.get("/policy-targets", params={"year": 2026}).json()
        items = {i["policy_code"]: i for i in payload["items"]}
        expected = {
            "SMALL_BUSINESS": "50",
            "STARTUP": "3.4",
            "SOCIAL_ENTERPRISE": "3",
            "SOCIAL_COOPERATIVE": "0.1",
            "DISABLED": "1",
            "DISABLED_STANDARD_WORKPLACE": "0.8",
        }
        for code, rate in expected.items():
            assert Decimal(items[code]["target_rate"]) == Decimal(rate), code
        # 분모가 다른 두 정책은 총액 목표 칸이 비어 있다.
        assert items["WOMAN"]["target_rate"] is None
        assert items["SELF_SUPPORT_VILLAGE"]["target_rate"] is None


class TestTheBusinessNumberIsTheOnlyKey:
    """§16 — 표기가 달라도 같은 기업. ⛔ 틀린 번호를 고치지 않는다."""

    def test_a_hyphenated_number_matches(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _A, amount="1000")
        _upload(
            client,
            _file(
                tmp_path / "s.xlsx",
                [["100-00-00009", "합성기업", "가나다", _FROM.isoformat(), _TO.isoformat()]],
            ),
            "STARTUP",
        )
        client.post("/purchases/rematch")
        assert _summary(client)["STARTUP"]["purchase_amount"] == "1000"

    def test_a_malformed_number_is_refused_not_repaired(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        result = _upload(
            client,
            _file(
                tmp_path / "bad.xlsx",
                [["12345", "합성기업", "가나다", _FROM.isoformat(), _TO.isoformat()]],
            ),
            "STARTUP",
        )
        assert result["stored"] is False
        assert CompanyRepository(db).count() == 0  # ⛔ 자릿수를 채워 넣지 않는다


class TestReuploadingAccumulates:
    """🟡 §15 — 재등록의 **현재 동작**을 있는 그대로 적어 둔다.

    .. warning::
        ⛔ 이 시험은 업무규칙을 정하지 않습니다. 최신 목록에서 빠진 기업을
        어떻게 할지(삭제·비활성·이력)는 **고객이 정한 적이 없습니다.** 정해지면
        기대값을 바꾸고 사유를 적습니다.

        지금은 «누적» 입니다 — 같은 파일을 다시 올려도 늘지 않지만(멱등),
        **다른 목록**을 올리면 옛 목록이 남습니다.
    """

    def test_the_same_file_twice_changes_nothing(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """멱등 — 같은 파일 재업로드로 중복이 쌓이지 않는다. ✅"""
        path = _file(tmp_path / "v1.xlsx", _one(_A))
        first = _upload(client, path, "WOMAN")
        second = _upload(client, path, "WOMAN")

        assert first["created"] == 1 and first["certifications"] == 1
        assert second["created"] == 0 and second["certifications"] == 0
        assert second["already_exists"] == 1
        assert CertificationRepository(db).count() == 1

    def test_a_company_dropped_from_the_new_list_stays(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """🟡 최신 목록에 없는 C 가 **남는다** — 확인 요청서 ③ ⑨."""
        _upload(client, _file(tmp_path / "v1.xlsx", _one(_A) + _one(_B) + _one(_C)), "WOMAN")
        _upload(client, _file(tmp_path / "v2.xlsx", _one(_A) + _one(_B) + _one(_D)), "WOMAN")

        stored = CertificationRepository(db).find_by_policy(_policy_id(db, "WOMAN"))
        numbers = set()
        for certification in stored:
            rows = CompanyRepository(db).execute(
                "SELECT business_no FROM company WHERE company_id = ?", (certification.company_id,)
            )
            numbers.add(rows[0]["business_no"])
        assert numbers == {_A, _B, _C, _D}  # C 가 남아 있다 — 4곳
        assert _C in numbers

    def test_a_changed_period_is_added_not_replaced(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """🟡 인증기간이 바뀌면 **두 구간이 다 남는다** — 확인 요청서 ③ ⑨.

        판정은 «어느 한 구간에라도 들면 인정» 이므로, 옛 구간도 계속
        인정됩니다. 최신 파일이 기간을 좁혀도 좁혀지지 않습니다.
        """
        _upload(client, _file(tmp_path / "v1.xlsx", _one(_A)), "WOMAN")
        _upload(
            client,
            _file(
                tmp_path / "v2.xlsx",
                [[_A, "합성기업", "가나다", "2026-03-01", "2027-02-28"]],
            ),
            "WOMAN",
        )

        stored = CertificationRepository(db).find_by_policy(_policy_id(db, "WOMAN"))
        periods = sorted((str(c.valid_from), str(c.valid_to)) for c in stored)
        assert periods == [
            ("2026-01-01", "2026-12-31"),  # 옛 구간이 남아 있다
            ("2026-03-01", "2027-02-28"),  # 새 구간이 더해졌다
        ]

    def test_the_registration_record_shows_the_latest_upload(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """등록 기록은 **마지막 파일**을 가리킨다 — 무엇을 올렸는지 남는다."""
        _upload(client, _file(tmp_path / "v1.xlsx", _one(_A)), "WOMAN")
        _upload(client, _file(tmp_path / "v2.xlsx", _one(_A) + _one(_B)), "WOMAN")

        row = next(
            r
            for r in client.get("/companies/registration").json()["items"]
            if r["policy_code"] == "WOMAN"
        )
        assert row["source"] == "FILE"
        assert row["source_label"] == "v2.xlsx"
