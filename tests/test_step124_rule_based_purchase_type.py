"""
STEP 122 — **고객이 확정한 규칙만** 자동 확정하고, 나머지는 담당자가 본다.

문서 조사 결과 (§21)
====================

**A. 확정 규칙** — ``DECISIONS.md`` §0.5.3 (2026-08-14 고객 확정)

======================  ======  ======  ======  ======  ==========================
예산과목                  물품    용역    공사    합계   이번 STEP
======================  ======  ======  ======  ======  ==========================
``소모성물품구입비``        214       0       0     214   ✅ **자동 확정 → 물품**
``도서인쇄비``              122       0       0     122   ✅ **자동 확정 → 물품**
``임차료``                    3     210       0     213   ⛔ **보류** (§0.9.4)
======================  ======  ======  ======  ======  ==========================

실측은 ``PURCHASE_TYPE_CLASSIFICATION_ANALYSIS.md`` §165~167. ``임차료`` 는
213건 중 3건이 물품이라 §0.9.4 가 **계산 연결을 보류**했고 §0.9.5 가 그 보류를
유지했다. ⛔ 보류를 푸는 것은 고객 확인 사항이므로 여기서 풀지 않았다.

**B. 예시일 뿐** — §0.9.5 「고객이 확정한 사례 — **검토 후보 생성에만 쓴다**」

소프트웨어 라이선스(Adobe·CAD·DreamPlus) · 나라장터 물품 수수료 · 행사운영비의
물건 형태(현수막·배너·백월·포디움·감사패·기념품) · 기념품 KC인증/발송 · 장기
차량 임차 · 장기 주차비 · 프로그램 임차 · 업무용 피복.

⛔ 문서가 **직접** 못 박았다 — 「위 표를 문자열 규칙으로 옮기지 않는다. …
이 표는 담당자에게 보여 줄 참고 사례이며 확정값이 아니다.」 그렇게 하면 고객이
직접 부정한 사례(기념품 KC인증 → 용역, 나라장터 물품 수수료 → 물품)를 그대로
틀리게 된다.

**C. 미확정 — 자동판정 금지**

``외주용역비``(용역 201 / 공사 69) · ``각종수수료`` · ``행사운영비`` ·
``자산취득비`` · ``설치`` 가 든 적요 · 공사·용역 복합 적요 ·
``하도급지킴이``/``노무비``(Q5-7: 단독 근거로는 안 된다).

판정 원칙(§0.9.5) — **이번 STEP 에서도 그대로다**
==================================================

1. 적요 낱말 하나만으로 확정하지 않는다.
2. **예산과목 단독으로도 확정하지 않는다.**
3. 복합·애매한 거래는 담당자가 지출결의서를 보고 판정한다.
4. 거래처 과거 이력은 사업자등록번호로 연결한다.
5. 업체명을 판정 근거로 쓰지 않는다.

⭐ 원칙 2 와 이번 자동 확정이 어긋나지 않는 이유: A 의 둘은 「예산과목이라서」
확정하는 것이 아니라 **고객이 그 항목을 따로 확정했고, 실측에서 다른 유형이
한 건도 나오지 않았기** 때문이다. 원칙 2 가 겨냥한 ``외주용역비`` 류는 그대로
미분류로 남는다.

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호·건수·금액은 넣지 않는다.
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
from procurement.core.purchase_type import (
    CONSTRUCTION,
    GOODS,
    RULE_CLASSIFIABLE_BUDGET_ACCOUNTS,
    SERVICE,
    classify_by_confirmed_rule,
)
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.review_repository import ReviewRepository
from procurement.reviews.rule_classification import RULE_REVIEWER
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_WOMAN = "1000000009"
_OTHER = "1000000014"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step122.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _won(value: object) -> Decimal:
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


def _purchase_row(
    *, day: str, amount: int, business_no: str, budget: str, note: str = "합성 거래"
) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
        "계": amount,
        "신고기준일": day,
        "적요": note,
        "예산과목": budget,
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


def _upload(
    client: TestClient, path: Path, *, month: int | None = 3, replace: bool = False
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/uploads/purchases",
        json={
            "file_path": str(path),
            "year": 2026,
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


def _woman(client: TestClient) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == "WOMAN"))


def _scoped(client: TestClient) -> dict[str, dict[str, Any]]:
    return {entry["scope"]: entry for entry in _woman(client)["scoped_achievements"]}


def _types(db: Path) -> dict[int, str | None]:
    """구매 ID → 확정된 구매유형."""
    import sqlite3

    connection = sqlite3.connect(db)
    try:
        rows = list(
            connection.execute(
                "SELECT p.purchase_id, r.final_purchase_type FROM purchase p "
                "JOIN import_batch b USING (batch_id) "
                "LEFT JOIN purchase_review r ON r.purchase_id = p.purchase_id "
                "WHERE b.status = 'ACTIVE' ORDER BY p.purchase_id"
            )
        )
    finally:
        connection.close()
    return {int(row[0]): row[1] for row in rows}


# ======================================================================
# §21  문서 조사 결과가 코드와 맞는가
# ======================================================================
class TestOnlyTheConfirmedRulesAreWired:
    def test_1_exactly_two_budget_accounts_classify_automatically(self) -> None:
        """⭐ 자동판정은 **둘뿐**이다. ⛔ 여기에 항목을 더하면 추측이 된다."""
        assert dict(RULE_CLASSIFIABLE_BUDGET_ACCOUNTS) == {
            "도서인쇄비": GOODS,
            "소모성물품구입비": GOODS,
        }

    def test_2_the_held_mapping_stays_held(self) -> None:
        """⛔ ``임차료`` 는 자동 확정하지 않는다 — 213건 중 3건이 물품(§0.9.4)."""
        assert classify_by_confirmed_rule("임차료") is None

    @pytest.mark.parametrize(
        "budget",
        ["외주용역비", "각종수수료", "행사운영비", "자산취득비", "차량유지비", "통신비"],
    )
    def test_3_mixed_accounts_are_never_classified(self, budget: str) -> None:
        """⛔ 혼재하는 예산과목은 어느 것도 자동 확정하지 않는다(원칙 2)."""
        assert classify_by_confirmed_rule(budget) is None

    @pytest.mark.parametrize(
        "budget", ["도서인쇄", "도서구입비", "소모성물품", "소모품비", "도서인쇄비용"]
    )
    def test_4_partial_matches_do_not_count(self, budget: str) -> None:
        """⛔ 「도서가 들어가면 물품」 같은 부분 문자열 규칙이 없다."""
        assert classify_by_confirmed_rule(budget) is None

    #: §0.9.5 의 **참고 사례**에 나오는 낱말들. ⛔ 어느 것도 구매유형에 **매여서는**
    #: 안 된다 — 매는 순간 고객이 부정한 사례(기념품 KC인증 → 용역, 나라장터 물품
    #: 수수료 → 물품)를 그대로 틀리게 된다.
    _EXAMPLE_WORDS = (
        "현수막",
        "배너",
        "백월",
        "포디움",
        "감사패",
        "기념품",
        "라이선스",
        "나라장터",
        "하도급",
        "렌탈",
        "피복",
        "단체복",
        "설치",
        "수수료",
    )

    def test_5_no_example_word_is_tied_to_a_purchase_type(self) -> None:
        """⛔ 참고 사례의 낱말이 **구매유형에 매여 있지 않다.**

        무서운 것은 낱말이 코드에 **등장하는 것**이 아니라, 낱말이 유형에
        **매이는 것**이다. 그래서 「낱말 → 유형」 형태의 사전을 찾는다.

        .. note::
            ``core/description_hints.py`` 는 이 시험을 통과한다 — 고객이 말한
            낱말이 적요에 들어 있는지 **보여 주기만** 하고, 유형과 짝지어 두지
            않았다(세 묶음을 하나로 이어 붙이고, ``DescriptionHint`` 에는
            ``purchase_type`` 필드가 **아예 없다**). 그것이 문서가 말한
            「검토 후보 생성에만 쓴다」 그대로다.
        """
        import ast

        types = {"CONSTRUCTION", "SERVICE", "GOODS", "공사", "용역", "물품"}
        offenders: list[str] = []
        for path in (Path("src") / "procurement").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values, strict=True):
                    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                        continue
                    if not any(word in key.value for word in self._EXAMPLE_WORDS):
                        continue
                    named: str | None = None
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        named = value.value
                    elif isinstance(value, ast.Name):
                        named = value.id
                    if named is not None and named in types:
                        offenders.append(f"{path}:{key.lineno}: {key.value} -> {named}")
        assert offenders == []

    def test_5b_the_classifier_itself_names_no_example_word(self) -> None:
        """⛔ 자동판정 모듈의 **판정 코드**에 참고 사례 낱말이 하나도 없다."""
        import ast

        source = (Path("src") / "procurement" / "reviews" / "rule_classification.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        }
        offenders = [
            f"{node.lineno}: {word}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in docstrings
            for word in self._EXAMPLE_WORDS
            if word in node.value
        ]
        assert offenders == []


# ======================================================================
# §22-A · §6  확정 규칙 자동판정
# ======================================================================
class TestTheRuleClassifiesOnUpload:
    @pytest.fixture
    def uploaded(self, client: TestClient, tmp_path: Path) -> TestClient:
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=2_000, business_no=_WOMAN, budget="소모성물품구입비"
                ),
                _purchase_row(day="2026-03-03", amount=3_000, business_no=_WOMAN, budget="임차료"),
                _purchase_row(
                    day="2026-03-04", amount=4_000, business_no=_WOMAN, budget="외주용역비"
                ),
                _purchase_row(day="2026-03-05", amount=5_000, business_no=_WOMAN, budget=""),
            ],
        )
        assert _upload(client, spend).status_code == 200
        return client

    def test_a1_the_two_confirmed_accounts_become_goods(
        self, db: Path, uploaded: TestClient
    ) -> None:
        """⭐ 도서인쇄비·소모성물품구입비는 올리는 즉시 **물품**으로 확정된다."""
        types = list(_types(db).values())

        assert types[0] == GOODS  # 도서인쇄비
        assert types[1] == GOODS  # 소모성물품구입비

    def test_b_everything_else_stays_pending(self, db: Path, uploaded: TestClient) -> None:
        """⛔ 임차료·외주용역비·예산과목 없음 → 담당자 검토 대상으로 남는다."""
        types = list(_types(db).values())

        assert types[2] is None  # 임차료 — 보류
        assert types[3] is None  # 외주용역비 — 혼재
        assert types[4] is None  # 예산과목 없음

    def test_a2_the_rule_confirmation_is_traceable(self, db: Path, uploaded: TestClient) -> None:
        """자동 확정도 **누가 정했는지** 남는다 — ⛔ 새 컬럼을 만들지 않았다(§7)."""
        review = ReviewRepository(db).find_by_purchase_id(1)

        assert review is not None
        assert review.final_purchase_type == GOODS
        assert review.reviewed_by == RULE_REVIEWER
        assert review.review_note is not None and "도서인쇄비" in review.review_note

    def test_d_the_rule_confirmation_is_in_the_history(self, uploaded: TestClient) -> None:
        """§22-D — 이력에 남는다."""
        history = uploaded.get("/reviews/1/history").json()["items"]

        assert history
        assert history[-1]["after_type"] == GOODS

    def test_a3_the_endpoint_reports_what_it_did(self, uploaded: TestClient) -> None:
        """다시 돌려도 이미 정해진 것은 건드리지 않는다."""
        payload = uploaded.post("/reviews/apply-rules", params={"year": 2026}).json()

        assert payload["examined"] == 5
        assert payload["classified"] == 0  # 업로드 때 이미 했다
        assert payload["already_decided"] == 2
        assert payload["pending"] == 3


# ======================================================================
# §22-C · §17  담당자가 고른 값이 이긴다
# ======================================================================
class TestThePersonCanOverrideTheRule:
    @pytest.fixture
    def uploaded(self, client: TestClient, tmp_path: Path) -> TestClient:
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=2_000, business_no=_WOMAN, budget="외주용역비"
                ),
            ],
        )
        assert _upload(client, spend).status_code == 200
        return client

    def test_c_a_pending_row_can_be_confirmed_by_hand(self, db: Path, uploaded: TestClient) -> None:
        """§22-C — 규칙이 못 정한 건을 담당자가 정한다."""
        assert (
            uploaded.put(
                "/reviews/2",
                json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
            ).status_code
            == 200
        )

        review = ReviewRepository(db).find_by_purchase_id(2)
        assert review is not None
        assert review.final_purchase_type == SERVICE
        assert review.reviewed_by == "담당자"

    def test_e1_the_person_can_change_a_rule_decision(self, db: Path, uploaded: TestClient) -> None:
        """⭐ §22-E — 규칙이 물품이라고 한 건도 담당자가 용역으로 바꿀 수 있다."""
        assert (
            uploaded.put(
                "/reviews/1",
                json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
            ).status_code
            == 200
        )

        review = ReviewRepository(db).find_by_purchase_id(1)
        assert review is not None
        assert review.final_purchase_type == SERVICE
        assert review.reviewed_by == "담당자"

    def test_e2_the_earlier_decision_stays_in_the_history(self, uploaded: TestClient) -> None:
        """⛔ 바뀌기 전 값이 이력에서 지워지지 않는다."""
        uploaded.put("/reviews/1", json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"})

        history = uploaded.get("/reviews/1/history").json()["items"]

        assert len(history) >= 2
        assert any(entry["after_type"] == GOODS for entry in history)
        assert history[-1]["after_type"] == SERVICE

    def test_e3_rerunning_the_rule_does_not_undo_the_person(
        self, db: Path, uploaded: TestClient
    ) -> None:
        """⭐ 규칙을 다시 돌려도 담당자가 고친 값을 되돌리지 않는다."""
        uploaded.put("/reviews/1", json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"})

        uploaded.post("/reviews/apply-rules", params={"year": 2026})

        review = ReviewRepository(db).find_by_purchase_id(1)
        assert review is not None
        assert review.final_purchase_type == SERVICE


# ======================================================================
# §11 · §22-F·G·H  여성기업 계산으로 이어지는가
# ======================================================================
class TestItReachesTheWomanCalculation:
    @pytest.fixture
    def seeded(self, client: TestClient, tmp_path: Path) -> TestClient:
        _register_woman(client, tmp_path)
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                # 규칙으로 물품이 되는 거래 — 여성기업 / 그 밖
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=9_000, business_no=_OTHER, budget="도서인쇄비"
                ),
                # 규칙이 못 정하는 거래
                _purchase_row(
                    day="2026-03-03", amount=5_000, business_no=_WOMAN, budget="외주용역비"
                ),
            ],
        )
        assert _upload(client, spend).status_code == 200
        client.post("/purchases/rematch")
        return client

    def test_f1_goods_is_calculated(self, seeded: TestClient) -> None:
        """§22-F — 물품 1,000 ÷ 10,000 = 10% · 목표 5% → 달성률 200%."""
        entry = _scoped(seeded)[GOODS]

        assert _won(entry["purchase_amount"]) == 1_000
        assert _won(entry["total_purchase_amount"]) == 10_000
        assert _won(entry["achievement_rate"]) == Decimal("200.00")

    def test_g_the_unclassified_row_is_excluded(self, seeded: TestClient) -> None:
        """§22-G — 규칙이 못 정한 5,000원은 어느 유형 계산에도 없다."""
        scoped = _scoped(seeded)

        assert _won(scoped[GOODS]["total_purchase_amount"]) == 10_000  # 15,000 이 아니다
        for scope in (CONSTRUCTION, SERVICE):
            assert scoped[scope]["achievement_rate"] is None
            assert scoped[scope]["status"] == "CALCULATION_ON_HOLD"

    def test_h_the_woman_total_is_unchanged(self, seeded: TestClient) -> None:
        """§22-H — 여성기업 TOTAL 매칭은 유형과 무관하게 그대로다."""
        row = _woman(seeded)

        assert _won(row["purchase_amount"]) == 6_000  # 1,000 + 5,000
        assert row["status"] == "SCOPED_BY_PURCHASE_TYPE"

    def test_f2_a_manual_confirmation_joins_the_other_scopes(self, seeded: TestClient) -> None:
        """담당자가 외주용역비 건을 용역으로 확정하면 용역 계산이 선다."""
        assert (
            seeded.put(
                "/reviews/3", json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"}
            ).status_code
            == 200
        )

        entry = _scoped(seeded)[SERVICE]
        assert _won(entry["purchase_amount"]) == 5_000
        assert _won(entry["total_purchase_amount"]) == 5_000

    def test_i_other_policies_are_unaffected(self, seeded: TestClient) -> None:
        """§22-I — 규칙 적용이 다른 정책 결과를 바꾸지 않는다."""
        before = seeded.get("/dashboard/summary", params={"year": 2026}).json()["policies"]

        seeded.post("/reviews/apply-rules", params={"year": 2026})

        after = seeded.get("/dashboard/summary", params={"year": 2026}).json()["policies"]
        for was in before:
            if was["policy_code"] == "WOMAN":
                continue
            assert next(row for row in after if row["policy_code"] == was["policy_code"]) == was


# ======================================================================
# §18 · §22-J  재업로드 — 규칙은 다시 돌고, 사람의 확정은 옮기지 않는다
# ======================================================================
class TestReuploadKeepsTheRuleButNotTheManualChoice:
    @pytest.fixture
    def confirmed(self, db: Path, client: TestClient, tmp_path: Path) -> TestClient:
        spend = _purchase_file(
            tmp_path / "a.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=2_000, business_no=_WOMAN, budget="외주용역비"
                ),
            ],
        )
        assert _upload(client, spend).status_code == 200
        # 담당자가 외주용역비 건을 손으로 확정해 둔다.
        assert (
            client.put(
                "/reviews/2", json={"final_purchase_type": CONSTRUCTION, "reviewed_by": "담당자"}
            ).status_code
            == 200
        )
        return client

    def test_j1_the_manual_choice_is_not_carried_over(
        self, db: Path, confirmed: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 담당자가 고른 값은 새 거래로 **복사되지 않는다**(§18)."""
        again = _purchase_file(
            tmp_path / "b.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=2_000, business_no=_WOMAN, budget="외주용역비"
                ),
            ],
        )
        assert _upload(confirmed, again, replace=True).status_code == 200

        types = _types(db)
        # 새 거래 두 건. 외주용역비 쪽은 다시 비어 있어야 한다.
        assert list(types.values())[1] is None

    def test_j2_but_the_rule_runs_again_on_the_new_rows(
        self, db: Path, confirmed: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 새 거래가 확정 규칙에 해당하면 **규칙으로는** 다시 정해진다(§18)."""
        again = _purchase_file(
            tmp_path / "b.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                ),
                _purchase_row(
                    day="2026-03-02", amount=2_000, business_no=_WOMAN, budget="외주용역비"
                ),
            ],
        )
        assert _upload(confirmed, again, replace=True).status_code == 200

        types = _types(db)
        assert list(types.values())[0] == GOODS

    def test_j3_the_old_confirmation_is_not_deleted(
        self, db: Path, confirmed: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 옛 거래의 확정 기록을 지우지도 않는다."""
        again = _purchase_file(
            tmp_path / "b.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                )
            ],
        )
        assert _upload(confirmed, again, replace=True).status_code == 200

        kept = ReviewRepository(db).find_by_purchase_id(2)
        assert kept is not None
        assert kept.final_purchase_type == CONSTRUCTION


# ======================================================================
# §19 · §20  건드리지 않은 것
# ======================================================================
class TestWhatWasNotBuilt:
    def test_6_reopen_behaviour_is_unchanged(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """⚠️ reopen 규칙(확인 요청서 ⑪)을 임의로 바꾸지 않았다(§19)."""
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(
                    day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="도서인쇄비"
                )
            ],
        )
        assert _upload(client, spend).status_code == 200

        assert client.post("/reviews/1/reopen", json={}).status_code == 200

        review = ReviewRepository(db).find_by_purchase_id(1)
        assert review is not None
        assert review.review_status == "REOPENED"
        assert review.final_purchase_type == GOODS  # ⚠️ 그대로 — 기존 동작

    def test_7_no_similarity_search_reached_the_classifier(self) -> None:
        """⛔ BM25·RAG·FUSE 를 자동판정 경로에 두지 않았다(§20)."""
        import ast

        offenders: list[str] = []
        for path in (
            Path("src") / "procurement" / "reviews" / "rule_classification.py",
            Path("src") / "procurement" / "core" / "purchase_type.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "procurement.experiments"
                ):
                    offenders.append(f"{path}:{node.lineno}")
        assert offenders == []

    def test_8_the_classifier_never_reads_the_description(self) -> None:
        """⛔ 적요·거래처명·금액을 보지 않는다 — 예산과목 하나만 본다."""
        source = (Path("src") / "procurement" / "reviews" / "rule_classification.py").read_text(
            encoding="utf-8"
        )

        for attribute in (".description", ".company_name", ".amount"):
            assert f"purchase{attribute}" not in source
