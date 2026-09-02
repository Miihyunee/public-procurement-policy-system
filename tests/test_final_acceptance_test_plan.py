"""
STEP 82 — 인수 테스트 계획이 **실제 시스템과 어긋나지 않는가**.

계획서는 마감 당일에 그대로 들고 도는 문서입니다. 거기에 없는 엔드포인트나
지어낸 함수가 섞여 있으면 그때 가서 헤매게 되고, 결과가 미리 채워져 있으면
**하지 않은 검증을 한 것으로** 남습니다.

무엇을 지키는가
===============

1. 계획서가 가리키는 **엔드포인트·함수·파일이 실재**한다.
2. **실제 결과 칸이 비어 있다** — 아직 인수 테스트를 하지 않았다.
3. 집계 숫자가 **표와 일치**한다.
4. 새 기능을 요구사항으로 만들지 않았다.

.. note::
    이 파일은 문서만 읽습니다. 계산·업무규칙을 시험하지 않습니다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from procurement.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_PLAN = _ROOT / "docs" / "FINAL_ACCEPTANCE_TEST_PLAN.md"

#: 이번 납품 범위에 넣지 않기로 한 것 — 계획서가 요구사항으로 만들면 안 된다.
#:
#: .. note::
#:     **"금액 검색" 이 빠진 이유** — STEP 82 작성 시점에는 범위 밖이었으나,
#:     2026-08-31 고객이 직접 요청하여(``DECISIONS.md`` §0.12.5) STEP 84 에서
#:     구현했습니다. 범위 밖으로 남겨 두면 **구현한 기능을 인수 테스트에서
#:     빼고 도는** 일이 생깁니다. ⛔ 나머지는 그대로입니다.
OUT_OF_SCOPE = (
    "지출결의서 자동 그룹핑",
    "구매유형 자동분류",
    "BM25",
    "threshold",
)


@pytest.fixture(scope="module")
def plan() -> str:
    return _PLAN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rows(plan: str) -> list[list[str]]:
    """체크리스트 행을 칸 단위로."""
    parsed = [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in plan.splitlines()
        if re.match(r"^\| [A-L]-\d+ \|", line)
    ]
    assert parsed, "체크리스트 행을 찾지 못했습니다"
    return parsed


class TestThePlanExists:
    """계획서가 체크리스트 형태인가."""

    def test_the_plan_exists(self) -> None:
        assert _PLAN.exists()

    def test_every_row_has_the_required_columns(self, rows: list[list[str]]) -> None:
        """번호 · 영역 · 내용 · 사전 조건 · 기대 결과 · 실제 결과 · 판정 · 비고."""
        assert all(len(row) == 8 for row in rows)

    def test_the_test_ids_are_unique(self, rows: list[list[str]]) -> None:
        ids = [row[0] for row in rows]
        assert len(ids) == len(set(ids))

    def test_every_row_states_what_is_expected(self, rows: list[list[str]]) -> None:
        """⛔ 기대 결과가 비면 무엇을 보고 PASS 를 줄지 알 수 없다."""
        assert all(row[4] for row in rows), [row[0] for row in rows if not row[4]]


class TestNothingWasFilledInAdvance:
    """⭐ 하지 않은 검증을 한 것처럼 남기지 않는다."""

    def test_the_actual_result_column_is_empty(self, rows: list[list[str]]) -> None:
        filled = [row[0] for row in rows if row[5]]
        assert filled == [], filled

    def test_no_row_claims_pass_or_fail(self, rows: list[list[str]]) -> None:
        """판정은 대기 상태이거나 합성 기준 표기뿐이어야 한다."""
        allowed = {"기존 QA PASS (합성)", "대기 — 고객 답변 필요", "대기 — 고객 데이터 필요"}
        assert {row[6] for row in rows} <= allowed

    def test_the_synthetic_marker_is_explained(self, plan: str) -> None:
        """⛔ "PASS" 가 실데이터 검증으로 읽히면 안 된다."""
        assert "실데이터 검증이 아니다" in plan
        assert "인수 테스트에서 다시 본다" in plan

    def test_the_summary_counts_match_the_table(self, plan: str, rows: list[list[str]]) -> None:
        """집계가 표와 어긋나면 그 문서는 그때부터 믿을 수 없다."""
        verdicts = Counter(row[6] for row in rows)
        assert f"**{len(rows)}건**" in plan
        for verdict, count in verdicts.items():
            assert f"**{count}건**" in plan, f"{verdict} = {count}"

    def test_the_area_counts_match(self, plan: str, rows: list[list[str]]) -> None:
        areas = Counter(row[0].split("-")[0] for row in rows)
        for area, count in areas.items():
            assert f"{area} {count}" in plan


class TestThePlanPointsAtRealThings:
    """⛔ 없는 것을 가리키면 마감 당일에 헤맨다."""

    def test_every_endpoint_exists(self, plan: str) -> None:
        app = create_app(_ROOT / "unused.db")
        real = {route.path for route in app.routes if isinstance(route, APIRoute)}
        cited = set(re.findall(r"`(?:GET|POST|PUT|DELETE) (/[^`?]*)`", plan))
        assert cited
        assert cited <= real, sorted(cited - real)

    def test_every_source_file_exists(self, plan: str) -> None:
        packages = "core|database|calculators|reviews|uploads|importers|models|collectors|dashboard"
        paths = set(re.findall(rf"`((?:{packages})/[a-z_/]+\.py)`", plan))
        assert paths
        for path in sorted(paths):
            assert (_ROOT / "src" / "procurement" / path).exists(), path

    def test_every_referenced_document_exists(self, plan: str) -> None:
        names = set(re.findall(r"`([A-Z_]+\.md)`", plan))
        assert names
        for name in sorted(names):
            assert (_ROOT / "docs" / name).exists(), name


class TestThePlanAddsNoRequirements:
    """⚠️ 검증 계획이지 요구사항 문서가 아니다."""

    def test_it_says_so(self, plan: str) -> None:
        assert "요구사항 문서가 아니다" in plan
        assert "새 기능을 정의하지 않는다" in plan

    @pytest.mark.parametrize("item", OUT_OF_SCOPE)
    def test_the_excluded_features_stay_excluded(self, plan: str, item: str) -> None:
        """⛔ 넣지 않기로 한 것이 계획서에서 되살아나면 안 된다."""
        section = plan.split("## 1.")[0]
        assert item in section  # "넣지 않는 것" 목록에 있어야 한다

    def test_the_two_blockers_are_stated(self, plan: str) -> None:
        assert "고객 답변" in plan
        assert "0 bytes" in plan

    def test_open_items_are_not_bugs(self, plan: str) -> None:
        assert "고객 답변 대기 항목을 버그로 분류하지 않는다" in plan

    def test_the_identity_comes_first(self, plan: str) -> None:
        """원본 = 적재 + 미적재 가 맞지 않으면 다른 숫자는 볼 필요가 없다."""
        assert "원본 행 수 = 적재 행 수 + 미적재 행 수" in plan

    def test_the_achievement_is_checked_independently(self, plan: str) -> None:
        assert "손계산" in plan
        assert "구매비율은 응답에 없으므로" in plan
