"""
STEP 118 — 여성기업 구매유형 **확정 흐름**이 계산까지 이어지는가.

무엇을 지키는가
===============
여성기업 목표는 셋이다 — 공사 3% · 용역 5% · 물품 5%. 이 파일은 담당자가
확정한 값이 그 셋에 **정확히** 닿는지, 확정하지 않은 것이 **어디에도** 섞이지
않는지를 고정한다.

⛔ 자동 분류하지 않는다. 적요·예산과목·거래처명·금액으로 유형을 정하지 않으며,
BM25·RAG·FUSE·유사도 임계값을 계산 경로에 두지 않는다.

실제 데이터에서는 어떠했는가 (§2·§5 계측 · 2026-09-05)
=======================================================

====================================  ================================
전체 계산대상                          2,079건 · 10,349,192,149원
여성기업 유효 거래                     **145건 · 1,525,413,644원** (기준값 일치)
구매유형 확정                          **0건** — 공사·용역·물품 모두 0
====================================  ================================

사본 DB 에서 8건(공사 3 · 용역 3 · 물품 2)을 담당자 확정으로 시뮬레이션했더니
세 유형이 각각 그 자리에서 계산되고 이력 8건이 남았다. 길은 이어져 있고,
없는 것은 담당자의 확정값뿐이다. 실제 DB 는 md5 로 불변을 확인했다(§17).

⚠️ 이 STEP 에서 드러난 두 가지 (규칙 아님 · 고객 확인 대기)
============================================================

1. **같은 달을 다시 올리면 확정이 계산에서 빠진다** — 확정 기록 자체는
   지워지지 않지만, 교체된 거래는 **새 ``purchase_id``** 로 들어오므로 검토행이
   붙어 있지 않다. :class:`TestReuploadingAMonthDropsTheConfirmation` 가 현재
   동작을 고정한다(확인 요청서 ⑭-1).

2. **부분 확정 중에는 분모도 확정분뿐이다** — 여성기업 거래만 확정하면 그
   유형의 분모가 곧 분자가 되어 달성률이 매우 높게 나온다. 틀린 계산이 아니라
   **아직 다 세지 않은 상태**다. :class:`TestPartialConfirmationIsVisiblySo`
   가 그것을 고정한다(확인 요청서 ⑭-2).

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호는 넣지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.review_repository import ReviewRepository
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다. 체크섬만 맞춘 값이다.
_WOMAN = "1000000009"
_OTHER = "1000000014"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step118.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _won(value: object) -> Decimal:
    """금액은 API 가 **문자열**로 준다 — 자릿수를 잃지 않으려고 그렇게 둔 값이다."""
    return Decimal(str(value))


def _company_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase_row(*, day: str, amount: int, business_no: str) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
        "계": amount,
        "신고기준일": day,
        "적요": "합성 거래",
        "예산과목": "일반수용비",
    }
    return [values[header] for header in header_row()]


def _purchase_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _upload_purchases(
    client: TestClient,
    path: Path,
    *,
    year: int = 2026,
    month: int | None = None,
    replace: bool = False,
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/uploads/purchases",
        json={
            "file_path": str(path),
            "year": year,
            "month": month,
            "replace_existing": replace,
        },
    )
    return response


def _register_woman(client: TestClient, tmp_path: Path) -> None:
    path = _company_file(
        tmp_path / "woman.xlsx",
        [[_WOMAN, "합성여성기업", "가나다", "2026-01-01", "2026-12-31"]],
    )
    assert (
        client.post(
            "/companies/upload", json={"file_path": str(path), "policy_code": "WOMAN"}
        ).status_code
        == 200
    )
    client.post("/purchases/rematch")


def _confirm(client: TestClient, purchase_id: int, purchase_type: str | None) -> httpx.Response:
    response: httpx.Response = client.put(
        f"/reviews/{purchase_id}",
        json={"final_purchase_type": purchase_type, "reviewed_by": "담당자"},
    )
    return response


def _woman(client: TestClient, year: int = 2026) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == "WOMAN"))


def _scoped(client: TestClient, year: int = 2026) -> dict[str, dict[str, Any]]:
    return {entry["scope"]: entry for entry in _woman(client, year)["scoped_achievements"]}


def _active_ids(db: Path) -> list[int]:
    """지금 계산 대상인 구매 ID — 활성 배치의 것만."""
    import sqlite3

    connection = sqlite3.connect(db)
    try:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT p.purchase_id FROM purchase p "
                "JOIN import_batch b USING (batch_id) "
                "WHERE b.status = 'ACTIVE' ORDER BY p.purchase_id"
            )
        ]
    finally:
        connection.close()


# ======================================================================
# §7 · §19-3·4·5·10  세 유형이 각각 따로 선다
# ======================================================================
class TestTheThreeTypesStandApart:
    """지시서 §7 의 합성 예시를 그대로 재현한다.

    전체 공사 1억 · 용역 2억 · 물품 3억 / 여성기업 1천만 · 2천만 · 3천만
    → 세 유형 모두 구매비율 10%.
    """

    @pytest.fixture
    def seeded(self, db: Path, client: TestClient, tmp_path: Path) -> TestClient:
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-01", amount=10_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-02", amount=90_000_000, business_no=_OTHER),
                _purchase_row(day="2026-03-03", amount=20_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-04", amount=180_000_000, business_no=_OTHER),
                _purchase_row(day="2026-03-05", amount=30_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-06", amount=270_000_000, business_no=_OTHER),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")

        ids = _active_ids(db)
        for purchase_id, purchase_type in zip(
            ids,
            (CONSTRUCTION, CONSTRUCTION, SERVICE, SERVICE, GOODS, GOODS),
            strict=True,
        ):
            assert _confirm(client, purchase_id, purchase_type).status_code == 200
        return client

    @pytest.mark.parametrize(
        ("scope", "numerator", "denominator", "target"),
        [
            (CONSTRUCTION, 10_000_000, 100_000_000, 3),
            (SERVICE, 20_000_000, 200_000_000, 5),
            (GOODS, 30_000_000, 300_000_000, 5),
        ],
    )
    def test_1_each_type_has_its_own_numerator_and_denominator(
        self, seeded: TestClient, scope: str, numerator: int, denominator: int, target: int
    ) -> None:
        """⭐ 같은 유형끼리 비교한다 — 분모가 전체 6억이 아니다."""
        entry = _scoped(seeded)[scope]

        assert _won(entry["purchase_amount"]) == numerator
        assert _won(entry["total_purchase_amount"]) == denominator
        assert _won(entry["target_rate"]) == target

    @pytest.mark.parametrize(
        ("scope", "rate"),
        [
            (CONSTRUCTION, "333.33"),  # 10% ÷ 3%
            (SERVICE, "200.00"),  # 10% ÷ 5%
            (GOODS, "200.00"),  # 10% ÷ 5%
        ],
    )
    def test_2_the_rates_are_independent(self, seeded: TestClient, scope: str, rate: str) -> None:
        """구매비율은 셋 다 10% 인데 목표가 달라 달성률이 갈린다."""
        assert _won(_scoped(seeded)[scope]["achievement_rate"]) == Decimal(rate)

    def test_3_the_policy_row_picks_no_single_rate(self, seeded: TestClient) -> None:
        """정책 한 줄은 달성률을 고르지 않는다 — 셋 중 하나를 고르면 둘이 사라진다."""
        row = _woman(seeded)

        assert row["achievement_rate"] is None
        assert row["status"] == "SCOPED_BY_PURCHASE_TYPE"
        assert _won(row["purchase_amount"]) == 60_000_000  # 유형과 무관한 전체 실적


# ======================================================================
# §8 · §19-6·7  확정하지 않은 것은 어디에도 섞이지 않는다
# ======================================================================
class TestTheUnconfirmedJoinsNothing:
    @pytest.fixture
    def spends(self, db: Path, client: TestClient, tmp_path: Path) -> list[int]:
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-02", amount=2_000_000, business_no=_OTHER),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        return _active_ids(db)

    def test_4_no_review_row_at_all_joins_no_scope(
        self, client: TestClient, spends: list[int]
    ) -> None:
        """① 검토행이 아예 없으면(NULL) 어느 유형에도 없다."""
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert _won(_scoped(client)[scope]["total_purchase_amount"]) == 0

    def test_5_a_pending_review_row_joins_no_scope(
        self, db: Path, client: TestClient, spends: list[int]
    ) -> None:
        """② 검토는 시작했지만 유형을 고르지 않았으면(PENDING) 역시 어디에도 없다."""
        for purchase_id in spends:
            assert _confirm(client, purchase_id, None).status_code == 200

        reviews = ReviewRepository(db)
        for purchase_id in spends:
            row = reviews.find_by_purchase_id(purchase_id)
            assert row is not None and row.final_purchase_type is None

        for scope in (CONSTRUCTION, SERVICE, GOODS):
            entry = _scoped(client)[scope]
            assert _won(entry["total_purchase_amount"]) == 0
            assert entry["status"] == "CALCULATION_ON_HOLD"

    def test_6_the_matched_total_is_not_the_typed_total(
        self, client: TestClient, spends: list[int]
    ) -> None:
        """③ 여성기업 매칭 금액과 유형별 금액을 섞지 않는다."""
        row = _woman(client)

        assert _won(row["purchase_amount"]) == 1_000_000  # 매칭은 되어 있다
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert _won(_scoped(client)[scope]["purchase_amount"]) == 0  # 유형은 모른다

    def test_7_a_non_woman_confirmation_stays_out_of_the_numerator(
        self, client: TestClient, spends: list[int]
    ) -> None:
        """④ 여성기업이 아닌 업체를 GOODS 로 확정해도 분자에 들어가지 않는다."""
        woman_purchase, other_purchase = spends
        assert _confirm(client, other_purchase, GOODS).status_code == 200

        entry = _scoped(client)[GOODS]
        assert _won(entry["total_purchase_amount"]) == 2_000_000  # 분모에는 든다
        assert _won(entry["purchase_amount"]) == 0  # ⛔ 분자에는 들지 않는다

    def test_8_an_unconfirmed_purchase_is_not_deleted(
        self, db: Path, client: TestClient, spends: list[int]
    ) -> None:
        """⛔ 미확정이라고 지우지 않는다 — 나중에 확정하면 그때 들어온다."""
        assert len(_active_ids(db)) == 2

        assert _confirm(client, spends[0], SERVICE).status_code == 200
        assert _won(_scoped(client)[SERVICE]["purchase_amount"]) == 1_000_000


# ======================================================================
# §9 · §19-8  분모가 0 이면 «계산 보류» 다
# ======================================================================
class TestAZeroDenominatorHolds:
    def test_9_a_type_with_no_confirmed_purchase_reports_no_rate(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """공사 확정이 한 건도 없으면 0% 가 아니라 «계산 보류» 다."""
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [_purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        assert _confirm(client, _active_ids(db)[0], GOODS).status_code == 200

        scoped = _scoped(client)
        assert _won(scoped[GOODS]["achievement_rate"]) > 0
        for scope in (CONSTRUCTION, SERVICE):
            assert scoped[scope]["achievement_rate"] is None
            assert scoped[scope]["status"] == "CALCULATION_ON_HOLD"


# ======================================================================
# §12 · §15  같은 달을 다시 올리면 확정이 계산에서 빠진다
# ======================================================================
class TestReuploadingAMonthDropsTheConfirmation:
    """⚠️ **규칙이 아니라 현재 동작이다 — 고객 확인 대기(확인 요청서 ⑭-1).**

    같은 달을 교체하면 그 달의 거래가 **새 ``purchase_id``** 로 들어온다. 검토행은
    옛 ID 에 붙어 있으므로 새 거래에는 확정이 없다 — 담당자가 확정해 둔 유형이
    계산에서 사라진다.

    ⛔ 고치지 않았다. 고치려면 「같은 거래를 무엇으로 알아보는가」를 정해야 하고,
    실제 데이터에서 결의일자·사업자번호·금액·적요를 다 합쳐도 **121건(42묶음)이
    구분되지 않는다.** ⛔ 새 거래 ID 를 임의로 만들지 않는다(지시서 §12).
    """

    def test_10_the_confirmation_record_is_kept(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 확정 기록 자체는 **지워지지 않는다** — 되살릴 근거가 남아 있다."""
        _register_woman(client, tmp_path)
        january = _purchase_file(
            tmp_path / "jan.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, january, month=1).status_code == 200
        client.post("/purchases/rematch")
        first = _active_ids(db)[0]
        assert _confirm(client, first, GOODS).status_code == 200
        assert _won(_scoped(client)[GOODS]["purchase_amount"]) == 1_000_000

        again = _purchase_file(
            tmp_path / "jan2.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, again, month=1, replace=True).status_code == 200
        client.post("/purchases/rematch")

        kept = ReviewRepository(db).find_by_purchase_id(first)
        assert kept is not None and kept.final_purchase_type == GOODS

    def test_11_but_the_replaced_purchase_has_no_confirmation(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """⚠️ 교체된 거래는 새 ID 라서 확정이 없다 — 유형별 계산에서 빠진다."""
        _register_woman(client, tmp_path)
        january = _purchase_file(
            tmp_path / "jan.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, january, month=1).status_code == 200
        client.post("/purchases/rematch")
        first = _active_ids(db)[0]
        assert _confirm(client, first, GOODS).status_code == 200

        again = _purchase_file(
            tmp_path / "jan2.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, again, month=1, replace=True).status_code == 200
        client.post("/purchases/rematch")

        current = _active_ids(db)
        assert current != [first]  # 새 ID 로 들어왔다
        assert ReviewRepository(db).find_by_purchase_id(current[0]) is None

        # 실적(매칭)은 그대로인데 유형별 계산에서는 빠졌다.
        assert _won(_woman(client)["purchase_amount"]) == 1_000_000
        assert _won(_scoped(client)[GOODS]["total_purchase_amount"]) == 0


# ======================================================================
# §6  부분 확정 중에는 분모도 확정분뿐이다
# ======================================================================
class TestPartialConfirmationIsVisiblySo:
    """⚠️ 틀린 계산이 아니라 **아직 다 세지 않은 상태**다(확인 요청서 ⑭-2)."""

    def test_12_confirming_only_the_woman_rows_makes_the_rate_look_perfect(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """여성기업 거래만 확정하면 분모 = 분자가 되어 달성률이 매우 높게 나온다.

        ⛔ 계산을 고칠 문제가 아니다 — 확정을 **끝까지** 해야 한다는 뜻이다.
        """
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-02", amount=99_000_000, business_no=_OTHER),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")

        woman_purchase = _active_ids(db)[0]
        assert _confirm(client, woman_purchase, GOODS).status_code == 200

        entry = _scoped(client)[GOODS]
        assert _won(entry["purchase_amount"]) == 1_000_000
        assert _won(entry["total_purchase_amount"]) == 1_000_000  # ⚠️ 아직 1건뿐
        assert _won(entry["achievement_rate"]) == Decimal("2000.00")

        # 나머지를 확정하면 분모가 제자리를 찾는다.
        assert _confirm(client, _active_ids(db)[1], GOODS).status_code == 200
        entry = _scoped(client)[GOODS]
        assert _won(entry["total_purchase_amount"]) == 100_000_000
        assert _won(entry["achievement_rate"]) == Decimal("20.00")  # 1% ÷ 5%


# ======================================================================
# §13 · §14 · §16 · §19-11·12·14  나머지를 건드리지 않는가
# ======================================================================
class TestItTouchesNothingElse:
    @pytest.fixture
    def confirmed(self, db: Path, client: TestClient, tmp_path: Path) -> TestClient:
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [_purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        assert _confirm(client, _active_ids(db)[0], GOODS).status_code == 200
        return client

    def test_13_reopen_keeps_the_value_and_keeps_counting_it(
        self, db: Path, confirmed: TestClient
    ) -> None:
        """⚠️ 확정을 되돌려도 값은 남고 계산에도 남는다 — 현재 동작(확인 요청서 ⑪).

        ⛔ reopen 을 이유로 ``final_purchase_type`` 을 ``NULL`` 로 만들거나
        계산에서 빼도록 임의로 바꾸지 않았다.
        """
        purchase_id = _active_ids(db)[0]
        assert confirmed.post(f"/reviews/{purchase_id}/reopen", json={}).status_code == 200

        row = ReviewRepository(db).find_by_purchase_id(purchase_id)
        assert row is not None
        assert row.review_status == "REOPENED"
        assert row.final_purchase_type == GOODS

        assert _won(_scoped(confirmed)[GOODS]["total_purchase_amount"]) == 1_000_000

    def test_14_confirming_leaves_the_other_policies_alone(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """유형 확정이 TOTAL 분모 정책과 총 구매금액을 움직이지 않는다."""
        _register_woman(client, tmp_path)
        startup = _company_file(
            tmp_path / "startup.xlsx",
            [[_OTHER, "합성창업기업", "라마바", "2026-01-01", "2026-12-31"]],
        )
        assert (
            client.post(
                "/companies/upload", json={"file_path": str(startup), "policy_code": "STARTUP"}
            ).status_code
            == 200
        )
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN),
                _purchase_row(day="2026-03-02", amount=9_000_000, business_no=_OTHER),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        before = client.get("/dashboard/summary", params={"year": 2026}).json()

        for purchase_id in _active_ids(db):
            assert _confirm(client, purchase_id, SERVICE).status_code == 200
        after = client.get("/dashboard/summary", params={"year": 2026}).json()

        assert after["total_purchase_amount"] == before["total_purchase_amount"]
        for name in ("STARTUP", "SMALL_BUSINESS", "SOCIAL_ENTERPRISE", "DISABLED"):
            was = next(row for row in before["policies"] if row["policy_code"] == name)
            now = next(row for row in after["policies"] if row["policy_code"] == name)
            assert now == was

    def test_15_confirming_does_not_touch_the_certification_source(
        self, db: Path, confirmed: TestClient
    ) -> None:
        """⛔ 구매유형 검토가 인증 소스 버전을 건드리지 않는다 (STEP 114)."""
        versions = PolicyCompanySourceRepository(db).find_versions(_policy_id(db, "WOMAN"))

        assert [row.version for row in versions] == [1]
        assert [row.is_active for row in versions] == [True]

    def test_16_the_monthly_accumulation_survives_confirmation(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """STEP 113 — 확정이 있어도 달이 쌓인다."""
        _register_woman(client, tmp_path)
        january = _purchase_file(
            tmp_path / "jan.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, january, month=1).status_code == 200
        client.post("/purchases/rematch")
        assert _confirm(client, _active_ids(db)[0], GOODS).status_code == 200

        february = _purchase_file(
            tmp_path / "feb.xlsx",
            [_purchase_row(day="2026-02-15", amount=2_000_000, business_no=_WOMAN)],
        )
        assert _upload_purchases(client, february, month=2).status_code == 200
        client.post("/purchases/rematch")

        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        assert _won(payload["total_purchase_amount"]) == 3_000_000  # 1월이 살아 있다
        # 1월의 확정은 그대로 유효하다 — 교체가 아니라 추가이기 때문이다.
        assert _won(_scoped(client)[GOODS]["purchase_amount"]) == 1_000_000

    def test_17_the_resolution_date_still_decides_the_year(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """연도 귀속은 여전히 결의일자다 — 확정해도 축이 바뀌지 않는다(STEP 86)."""
        assert main(["targets", "--year", "2027", "--db", str(db)]) == 0
        path = _company_file(
            tmp_path / "woman.xlsx",
            [[_WOMAN, "합성여성기업", "가나다", "2026-01-01", "2027-12-31"]],
        )
        assert (
            client.post(
                "/companies/upload", json={"file_path": str(path), "policy_code": "WOMAN"}
            ).status_code
            == 200
        )

        row = _purchase_row(day="2026-11-20", amount=5_000_000, business_no=_WOMAN)
        headers = list(header_row())
        row[headers.index("결의일자")] = "2027-01-10"  # ⭐ 결의일자만 다음 해
        spend = _purchase_file(tmp_path / "spend.xlsx", [row])
        assert _upload_purchases(client, spend, year=2027).status_code == 200
        client.post("/purchases/rematch")
        assert _confirm(client, _active_ids(db)[0], GOODS).status_code == 200

        assert _won(_scoped(client, 2027)[GOODS]["purchase_amount"]) == 5_000_000
        assert _won(_scoped(client, 2026)[GOODS]["total_purchase_amount"]) == 0


# ======================================================================
# §4 · §18  자동 분류가 없다
# ======================================================================
class TestNothingClassifiesAutomatically:
    def test_18_the_calculation_path_never_imports_the_experiments(self) -> None:
        """⛔ BM25·FUSE 가 계산 경로에 들어와 있지 않다."""
        import ast

        offenders: list[str] = []
        for root in (
            Path("src") / "procurement" / "calculators",
            Path("src") / "procurement" / "dashboard",
        ):
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                        "procurement.experiments"
                    ):
                        offenders.append(f"{path}:{node.lineno}")
        assert offenders == []

    def test_19_a_description_naming_a_type_confirms_nothing(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """적요에 「공사」라고 적혀 있어도 확정되지 않는다."""
        _register_woman(client, tmp_path)
        row = _purchase_row(day="2026-03-01", amount=1_000_000, business_no=_WOMAN)
        headers = list(header_row())
        row[headers.index("적요")] = "청사 방수공사 용역 물품 일괄"
        spend = _purchase_file(tmp_path / "spend.xlsx", [row])
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")

        assert ReviewRepository(db).find_by_purchase_id(_active_ids(db)[0]) is None
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert _won(_scoped(client)[scope]["total_purchase_amount"]) == 0
