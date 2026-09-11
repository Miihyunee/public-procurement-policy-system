"""STEP 8 — 과거 이력 색인 재사용과 CSV 내보내기.

두 가지를 지킵니다.

1. **색인을 쓸데없이 다시 만들지 않되, 낡은 값을 주지도 않는다**
2. **CSV 의 '최종 유형' 은 담당자 확정값뿐이다** — 과거 이력이 대신 들어가지
   않는다

⚠️ 데이터는 전부 **합성**입니다.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.classification import ANALYZED, ClassificationResult, TypeCandidate
from procurement.models.purchase import Purchase
from procurement.reviews.export import EXPORT_COLUMNS, export_lines, export_row
from procurement.reviews.past_labels import MIXED_TYPES, NO_HISTORY, SINGLE_TYPE
from procurement.reviews.query import ReviewQuery
from procurement.reviews.review_service import ReviewService


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "export.db")
    init_db(path)
    return path


@pytest.fixture
def purchases(db_path: str) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def reviews(db_path: str) -> ReviewRepository:
    return ReviewRepository(db_path)


@pytest.fixture
def service(purchases: PurchaseRepository, reviews: ReviewRepository) -> ReviewService:
    return ReviewService(purchases, reviews)


def add(repository: PurchaseRepository, description: str, *, amount: str = "1650000") -> int:
    purchase = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name="가나건설",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 20),
            amount=Decimal(amount),
            resolution_date=date(2026, 3, 25),
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


class TestIndexIsReusedButNeverStale:
    """색인 재사용 (지시 F-5)."""

    def test_repeated_reads_reuse_the_same_index(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """확정이 그대로면 색인 객체가 **그대로 재사용**된다."""
        first = add(purchases, "같은 적요")
        add(purchases, "같은 적요")

        service.get_target(first)
        cached = service._past_label_index()
        service.get_target(first)

        assert service._past_label_index() is cached

    def test_new_confirmation_invalidates(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        assert service.get_target(second).past_labels.total == 0

        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        assert service.get_target(second).past_labels.total == 1

    def test_reopen_invalidates(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """재검토로 되돌리면 그 건은 **더 이상 확정 이력이 아니다.**"""
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        assert service.get_target(second).past_labels.total == 1

        service.reopen(first, reopened_by="이담당")

        assert service.get_target(second).past_labels.total == 0

    def test_type_change_invalidates(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """건수는 그대로지만 유형이 바뀌는 경우 — 시각으로 잡아야 한다."""
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        assert service.get_target(second).past_labels.labels[0].purchase_type == SERVICE

        service.reopen(first, reopened_by="이담당")
        service.confirm(first, final_purchase_type=CONSTRUCTION, reviewed_by="이담당")

        summary = service.get_target(second).past_labels
        assert summary.total == 1
        assert summary.labels[0].purchase_type == CONSTRUCTION

    def test_hold_removes_it_from_history(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """판단 보류로 바꾸면 확정 이력에서 빠진다."""
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(first, reopened_by="김담당")
        service.confirm(first, final_purchase_type=None, reviewed_by="김담당")

        assert service.get_target(second).past_labels.total == 0

    def test_out_of_band_write_is_detected(
        self,
        service: ReviewService,
        purchases: PurchaseRepository,
        reviews: ReviewRepository,
    ) -> None:
        """⛔ **서비스를 거치지 않은 변경**도 반영되어야 한다.

        판단 근거가 서비스의 기억이 아니라 **DB 지문**이기 때문에, 테스트나
        다른 코드가 Repository 를 직접 고쳐도 낡은 색인이 남지 않는다.
        """
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        service.get_target(second)  # 색인을 한 번 만들어 둔다

        reviews.confirm(first, final_purchase_type=GOODS, reviewed_by="김담당")

        assert service.get_target(second).past_labels.total == 1

    def test_list_and_single_agree(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """목록으로 본 값과 한 건으로 본 값이 달라지면 안 된다."""
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        from_list = {
            target.purchase.purchase_id: target.past_labels.total
            for target in service.search(ReviewQuery()).items
        }

        assert from_list[second] == service.get_target(second).past_labels.total

    def test_index_survives_analysis(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """분석은 확정을 바꾸지 않으므로 색인을 다시 만들 이유가 없다."""
        first = add(purchases, "같은 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        cached = service._past_label_index()

        reviews.save_analysis(
            first,
            ClassificationResult(
                candidates=[
                    TypeCandidate(purchase_type=GOODS, score=Decimal("0.9"), evidence="합성")
                ],
                analyzer_name="bm25",
                analyzer_version="1",
                status=ANALYZED,
            ),
        )

        assert service._past_label_index() is cached


class TestExportColumns:
    """CSV 열 (지시 F-6)."""

    def test_column_order_is_fixed(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        add(purchases, "적요")
        target = service.search(ReviewQuery()).items[0]

        assert len(export_row(target)) == len(EXPORT_COLUMNS)

    def test_required_columns_exist(self) -> None:
        """지시 D-2 가 요구한 항목이 전부 있어야 한다."""
        for column in (
            "적요",
            "현재 상태",
            "최종 유형",
            "확정자",
            "확정일시",
            "검토 메모",
            "과거 확정 최다 유형",
            "과거 최다 유형 비율",
            "과거 확정 유형 수",
            "과거 확정 건수",
        ):
            assert column in EXPORT_COLUMNS, column

    def test_header_comes_first_with_a_bom(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """엑셀이 한글을 깨지 않으려면 BOM 이 필요하다."""
        add(purchases, "적요")
        lines = list(export_lines(service.search_all(ReviewQuery())))

        assert lines[0].startswith("﻿")
        assert lines[0].lstrip("﻿").startswith(EXPORT_COLUMNS[0])

    def test_empty_result_still_has_a_header(self, service: ReviewService) -> None:
        lines = list(export_lines(service.search_all(ReviewQuery())))

        assert len(lines) == 1


class TestExportValues:
    """CSV 값."""

    def parsed(self, service: ReviewService) -> list[dict[str, str]]:
        """CSV 를 다시 읽어 딕셔너리 목록으로."""
        text = "".join(export_lines(service.search_all(ReviewQuery())))
        return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))

    def test_confirmed_row_carries_the_decision(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "확정된 적요")
        service.confirm(
            purchase_id,
            final_purchase_type=CONSTRUCTION,
            reviewed_by="김담당",
            review_note="하도급 노무비",
        )
        row = self.parsed(service)[0]

        assert row["최종 유형"] == "공사"
        assert row["확정자"] == "김담당"
        assert row["검토 메모"] == "하도급 노무비"
        assert row["확정일시"]

    def test_pending_row_has_an_empty_decision(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """⛔ 미확정이면 최종 유형 칸이 비어 있어야 한다."""
        add(purchases, "미확정 적요")
        row = self.parsed(service)[0]

        assert row["현재 상태"] == "PENDING"
        assert row["최종 유형"] == ""
        assert row["확정자"] == ""
        assert row["확정일시"] == ""

    def test_history_never_becomes_the_decision(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """⛔ **가장 중요한 검증** — 과거 최다 유형이 최종 유형 칸에 새면 안 된다.

        참고 정보가 확정값으로 둔갑하면, 담당자가 정하지 않은 값이 엑셀에서
        확정처럼 읽힌다.
        """
        seeded = add(purchases, "반복되는 적요")
        service.confirm(seeded, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        add(purchases, "반복되는 적요")  # 미확정

        rows = self.parsed(service)
        pending = [row for row in rows if row["현재 상태"] == "PENDING"][0]

        assert pending["과거 확정 최다 유형"] == "공사"  # 참고 정보는 채워지고
        assert pending["최종 유형"] == ""  # 확정값은 비어 있다

    def test_decision_can_differ_from_history(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """담당자가 과거와 다르게 골랐으면 **담당자 선택**이 실린다."""
        seeded = add(purchases, "반복되는 적요")
        service.confirm(seeded, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        other = add(purchases, "반복되는 적요")
        service.confirm(other, final_purchase_type=GOODS, reviewed_by="이담당")

        row = [r for r in self.parsed(service) if r["구매ID"] == str(other)][0]

        assert row["최종 유형"] == "물품"
        assert row["과거 확정 최다 유형"] in {"공사", "물품"}

    def test_hold_is_empty_but_confirmed(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """판단 보류는 '확정했으나 유형 없음' 이다."""
        purchase_id = add(purchases, "보류한 적요")
        service.confirm(purchase_id, final_purchase_type=None, reviewed_by="김담당")
        row = self.parsed(service)[0]

        assert row["현재 상태"] == "CONFIRMED"
        assert row["최종 유형"] == ""
        assert row["확정자"] == "김담당"

    def test_history_columns_for_a_fresh_description(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        add(purchases, "처음 보는 적요")
        row = self.parsed(service)[0]

        assert row["과거 확정 건수"] == "0"
        assert row["과거 확정 최다 유형"] == ""
        assert row["과거 최다 유형 비율"] == ""
        assert row["과거 이력 일관성"] == NO_HISTORY

    def test_history_columns_when_mixed(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "갈린 적요")
        second = add(purchases, "갈린 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        service.confirm(second, final_purchase_type=GOODS, reviewed_by="이담당")
        row = self.parsed(service)[0]

        assert row["과거 확정 건수"] == "2"
        assert row["과거 확정 유형 수"] == "2"
        assert row["과거 이력 일관성"] == MIXED_TYPES
        assert row["과거 최다 유형 비율"] == "50.00"

    def test_single_type_history(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "일관된 적요")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        row = self.parsed(service)[0]

        assert row["과거 이력 일관성"] == SINGLE_TYPE
        assert row["과거 최다 유형 비율"] == "100.00"

    def test_korean_survives_a_round_trip(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        add(purchases, "복합기 토너 및 사무실 청소 3월 이용료")
        row = self.parsed(service)[0]

        assert row["적요"] == "복합기 토너 및 사무실 청소 3월 이용료"
        assert row["거래처명"] == "가나건설"

    def test_amount_is_plain_digits(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """지수 표기(1.65E+6)로 나가면 엑셀에서 금액이 깨진다."""
        add(purchases, "금액 확인", amount="1650000")
        row = self.parsed(service)[0]

        assert row["금액"] == "1650000"

    def test_dates_are_iso(self, service: ReviewService, purchases: PurchaseRepository) -> None:
        add(purchases, "날짜 확인")
        row = self.parsed(service)[0]

        assert row["신고기준일"] == "2026-03-10"
        assert row["결의일자"] == "2026-03-25"


class TestCsvSafety:
    """CSV 인젝션·이스케이프."""

    def parsed_first(self, service: ReviewService) -> dict[str, str]:
        text = "".join(export_lines(service.search_all(ReviewQuery())))
        return next(iter(csv.DictReader(io.StringIO(text.lstrip("﻿")))))

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_formula_like_values_are_neutralised(
        self, service: ReviewService, purchases: PurchaseRepository, prefix: str
    ) -> None:
        """적요는 사람이 입력한 자유 문자열이라 수식이 될 수 있다."""
        add(purchases, f'{prefix}HYPERLINK("http://x")')
        row = self.parsed_first(service)

        assert row["적요"].startswith("'")

    def test_ordinary_text_is_untouched(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        add(purchases, "정상 적요")

        assert self.parsed_first(service)["적요"] == "정상 적요"

    def test_commas_and_quotes_survive(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """적요에 쉼표가 흔하다 — 열이 밀리면 안 된다."""
        add(purchases, '토너, 청소, "정수기" 3월')

        assert self.parsed_first(service)["적요"] == '토너, 청소, "정수기" 3월'

    def test_newline_inside_a_field(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        add(purchases, "첫 줄\n둘째 줄")

        assert self.parsed_first(service)["적요"] == "첫 줄\n둘째 줄"


class TestExportStreams:
    """대량 데이터에서 메모리를 붙들지 않는다."""

    def test_lines_are_produced_lazily(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """제너레이터여야 한다 — 전체를 문자열로 쌓으면 건수만큼 메모리를 쓴다."""
        for index in range(5):
            add(purchases, f"적요 {index}")

        stream = export_lines(service.search_all(ReviewQuery()))

        assert next(stream).startswith("﻿")  # 머리글만 먼저 나온다
        assert len(list(stream)) == 5
