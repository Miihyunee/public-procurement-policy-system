"""
procurement.reviews.response

검토 API 의 **요청·응답 스키마**.

직렬화 규약은 기존 API 와 동일합니다.

- ``Decimal`` → **문자열**(정밀도 보존)
- ``datetime`` → ISO 8601 문자열
- 미설정은 JSON ``null``

.. warning::
    ⛔ **응답에서 원본 · 분석 · 확정을 분리합니다.**

    ``source`` / ``analysis`` / ``review`` 세 블록으로 나눠, 화면이 셋을 섞을
    수 없게 합니다. "원본 적요가 공사로 바뀌었다" 같은 표현이 구조적으로
    불가능해집니다(``docs/REVIEW_INTERFACE_DESIGN.md`` §4.1).

.. warning::
    ⛔ **확정값을 미리 채우지 않습니다.**

    분석 점수가 0.97 이어도 ``review.final_purchase_type`` 은 담당자가 확정하기
    전까지 ``null`` 입니다. 미리 채우면 담당자가 그대로 눌러 **사실상 자동
    확정**이 됩니다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, StrictStr, field_serializer

from procurement.core.purchase_type import PURCHASE_TYPE_LABELS
from procurement.models.review import (
    PurchaseReview,
    ReviewHistoryEntry,
    ReviewProgress,
)
from procurement.reviews.past_labels import PastLabelSummary
from procurement.reviews.review_service import ReviewTarget


class ConfirmReviewRequest(BaseModel):
    """검토 확정 요청 본문.

    ``final_purchase_type`` 은 **필수 키**입니다. 키가 없으면 422 로 거부되며,
    이를 통해 "값을 바꾸지 않겠다" 와 "판단 보류로 두겠다" 를 구분합니다.

    Attributes:
        final_purchase_type: ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS`` 또는
            ``None``(**판단 보류**). 허용값 검증은 서비스 계층이 합니다.
        reviewed_by: 확정자.
        review_note: 담당자 메모.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    final_purchase_type: StrictStr | None
    reviewed_by: StrictStr | None = None
    review_note: StrictStr | None = None


class ReopenReviewRequest(BaseModel):
    """재검토 요청 본문.

    Attributes:
        reopened_by: 되돌린 사람.
        note: 사유.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reopened_by: StrictStr | None = None
    note: StrictStr | None = None


class PurchaseSourceResponseModel(BaseModel):
    """DB-1 원본 (⛔ 읽기 전용).

    Attributes:
        purchase_id: 구매 ID.
        description: 적요. **원본 그대로**입니다.
        company_name: 거래처명.
        business_no: 사업자등록번호.
        amount: 금액(VAT 포함 총액).
        resolution_date: 결의일자.
        issue_date: 세금계산서 발행일자(신고기준일).
        budget_account: 예산과목. 공란일 수 있습니다.
    """

    model_config = ConfigDict(frozen=True)

    purchase_id: int
    description: str | None
    company_name: str
    business_no: str
    amount: Decimal
    resolution_date: str | None
    issue_date: str | None
    budget_account: str | None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return str(value)


class CandidateResponseModel(BaseModel):
    """구매유형 후보 하나.

    Attributes:
        purchase_type: 후보 유형 코드.
        label: 한글 라벨(공사 · 용역 · 물품).
        score: 0~1 점수(문자열). ⛔ 확정이 아니라 **분석기의 의견**입니다.
        evidence: 왜 이 후보인지에 대한 근거.
    """

    model_config = ConfigDict(frozen=True)

    purchase_type: str
    label: str
    score: Decimal
    evidence: str

    @field_serializer("score")
    def _score(self, value: Decimal) -> str:
        return str(value)


class AnalysisResponseModel(BaseModel):
    """자동 분석 결과 (⛔ 확정이 아님).

    Attributes:
        status: ``NOT_ANALYZED`` / ``ANALYZED`` / ``FAILED``.
        analyzer_name: 분석기 이름. 방법 비교에 쓰입니다.
        analyzer_version: 분석기 버전.
        analyzed_at: 분석 시각.
        is_ambiguous: 후보가 갈리는가. **정렬·표시용**이며 자동 확정에 쓰지
            않습니다(임계값 미확정 — 결정 대기).
        candidates: 후보 목록(점수 내림차순). 비어 있을 수 있습니다.
        candidate_count: 후보 개수. 0 이면 **판단하지 않았다**는 뜻입니다.
        score_gap: 1순위와 2순위의 점수 차. 후보가 1개 이하면 ``null``
            ("차이 0" 이 아니라 **차이라는 것이 없음**).
        note: 부가 설명.

    .. warning::
        ⛔ ``score_gap`` 은 **원시 정보**입니다. "차이가 크면 확정" 같은
        기준이 없으며, 임계값은 고객 미확정입니다.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    analyzer_name: str | None
    analyzer_version: str | None
    analyzed_at: datetime | None
    is_ambiguous: bool
    candidates: list[CandidateResponseModel]
    candidate_count: int
    score_gap: Decimal | None
    note: str | None

    @field_serializer("score_gap")
    def _score_gap(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class ReviewStateResponseModel(BaseModel):
    """담당자 확정 결과.

    Attributes:
        status: ``PENDING`` / ``CONFIRMED`` / ``REOPENED``.
        final_purchase_type: 담당자가 고른 유형. ``null`` 은 **판단 보류**이며
            "미설정" 과 같은 뜻입니다(0 이나 기본값이 아닙니다).
        final_purchase_type_label: 한글 라벨. 판단 보류면 ``null``.
        reviewed_by: 확정자.
        reviewed_at: 확정 시각.
        review_note: 담당자 메모.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    final_purchase_type: str | None
    final_purchase_type_label: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None


class PastLabelResponseModel(BaseModel):
    """과거 같은 적요가 확정된 유형 하나.

    Attributes:
        purchase_type: 유형 코드.
        label: 한글 라벨.
        count: 그렇게 확정된 건수.
    """

    model_config = ConfigDict(frozen=True)

    purchase_type: str
    label: str
    count: int


class PastLabelsResponseModel(BaseModel):
    """같은 적요의 **과거 확정 이력** — 담당자 판단을 돕는 참고 정보.

    .. warning::
        ⛔ 이 블록은 **추천이 아닙니다.** 과거 기록을 세어 보여줄 뿐이며,
        어떤 유형이 맞다고 말하지 않습니다. 확정값을 미리 채우지도 않습니다.

    Attributes:
        labels: 유형별 건수(내림차순). 이력이 없으면 빈 목록.
        total: 과거 확정 건수 합계.
        type_count: 과거에 붙은 서로 다른 유형 수.
        has_conflict: 같은 적요가 여러 유형으로 확정된 적이 있는가.
            **먼저 볼 것을 권하는 표시**이며 자동 판정에 쓰지 않습니다.
        differs_from_top_candidate: 1순위 후보가 과거 최빈 유형과 다른가.
            "틀렸다" 는 뜻이 아닙니다.
    """

    model_config = ConfigDict(frozen=True)

    labels: list[PastLabelResponseModel]
    total: int
    type_count: int
    has_conflict: bool
    differs_from_top_candidate: bool


class ReviewItemResponseModel(BaseModel):
    """검토 대상 1건 — **원본 · 분석 · 확정을 분리**해 담습니다.

    Attributes:
        source: DB-1 원본.
        analysis: 자동 분석 결과.
        review: 담당자 확정 결과.
        past_labels: 같은 적요의 과거 확정 이력(참고).
    """

    model_config = ConfigDict(frozen=True)

    source: PurchaseSourceResponseModel
    analysis: AnalysisResponseModel
    review: ReviewStateResponseModel
    past_labels: PastLabelsResponseModel

    @classmethod
    def from_target(cls, target: ReviewTarget) -> ReviewItemResponseModel:
        """서비스 결과를 응답 모델로 변환합니다."""
        purchase = target.purchase
        review = target.review
        assert purchase.purchase_id is not None  # 저장된 행만 검토 대상이 된다

        return cls(
            source=PurchaseSourceResponseModel(
                purchase_id=purchase.purchase_id,
                description=purchase.description,
                company_name=purchase.company_name,
                business_no=purchase.business_no,
                amount=purchase.amount,
                resolution_date=(
                    purchase.resolution_date.isoformat() if purchase.resolution_date else None
                ),
                issue_date=purchase.issue_date.isoformat() if purchase.issue_date else None,
                budget_account=purchase.budget_account,
            ),
            analysis=_analysis_of(review),
            review=_review_state_of(review),
            past_labels=_past_labels_of(target.past_labels, review.top_candidate),
        )


class ReviewProgressResponseModel(BaseModel):
    """검토 진행 상황.

    Attributes:
        total: 검토 대상 건수.
        confirmed: 확정 건수.
        pending: 미확정 건수.
        ambiguous: 후보가 갈리는 건수(먼저 볼 것을 권함).
        not_analyzed: 아직 분석하지 않은 건수.
    """

    model_config = ConfigDict(frozen=True)

    total: int
    confirmed: int
    pending: int
    ambiguous: int
    not_analyzed: int

    @classmethod
    def from_progress(cls, progress: ReviewProgress) -> ReviewProgressResponseModel:
        """집계 결과를 응답 모델로 변환합니다."""
        return cls(
            total=progress.total,
            confirmed=progress.confirmed,
            pending=progress.pending,
            ambiguous=progress.ambiguous,
            not_analyzed=progress.not_analyzed,
        )


class ReviewListResponseModel(BaseModel):
    """검토 목록 응답.

    Attributes:
        items: 검토 대상 목록.
        progress: 진행 상황.
    """

    model_config = ConfigDict(frozen=True)

    items: list[ReviewItemResponseModel]
    progress: ReviewProgressResponseModel


class ReviewHistoryItemResponseModel(BaseModel):
    """변경 이력 한 건.

    Attributes:
        action: ``ANALYZED`` / ``CONFIRMED`` / ``REOPENED``.
        changed_at: 변경 시각.
        changed_by: 변경자.
        before_type: 변경 전 유형.
        after_type: 변경 후 유형.
        note: 설명.
        candidates: 그 시점의 후보 스냅샷.
    """

    model_config = ConfigDict(frozen=True)

    action: str
    changed_at: datetime
    changed_by: str | None
    before_type: str | None
    after_type: str | None
    note: str | None
    candidates: list[CandidateResponseModel]

    @classmethod
    def from_entry(cls, entry: ReviewHistoryEntry) -> ReviewHistoryItemResponseModel:
        """이력 항목을 응답 모델로 변환합니다."""
        return cls(
            action=entry.action,
            changed_at=entry.changed_at,
            changed_by=entry.changed_by,
            before_type=entry.before_type,
            after_type=entry.after_type,
            note=entry.note,
            candidates=[_candidate_of(candidate) for candidate in entry.candidates],
        )


class ReviewHistoryResponseModel(BaseModel):
    """변경 이력 목록.

    Attributes:
        purchase_id: 대상 구매.
        items: 이력 목록(시간순).
    """

    model_config = ConfigDict(frozen=True)

    purchase_id: int
    items: list[ReviewHistoryItemResponseModel]


class PurchaseTypeOptionResponseModel(BaseModel):
    """담당자가 고를 수 있는 선택지 하나.

    화면이 선택지를 직접 만들지 않도록 **백엔드가 목록을 소유**합니다.

    Attributes:
        value: 유형 코드. **판단 보류**는 ``null``.
        label: 화면 라벨.
    """

    model_config = ConfigDict(frozen=True)

    value: str | None
    label: str


def purchase_type_options() -> list[PurchaseTypeOptionResponseModel]:
    """선택지 목록을 반환합니다 — 공사 · 용역 · 물품 · 판단 보류.

    ⛔ 새 분류 체계를 만들지 않습니다.
    :data:`~procurement.core.purchase_type.PURCHASE_TYPE_LABELS` 를 그대로
    씁니다. "판단 보류" 는 유형이 아니라 **값 없음**(``null``)입니다.
    """
    options = [
        PurchaseTypeOptionResponseModel(value=code, label=label)
        for code, label in PURCHASE_TYPE_LABELS.items()
    ]
    options.append(PurchaseTypeOptionResponseModel(value=None, label="판단 보류"))
    return options


def _candidate_of(candidate: object) -> CandidateResponseModel:
    """후보 하나를 응답 모델로 변환합니다."""
    from procurement.models.classification import TypeCandidate

    assert isinstance(candidate, TypeCandidate)
    return CandidateResponseModel(
        purchase_type=candidate.purchase_type,
        label=candidate.label,
        score=candidate.score,
        evidence=candidate.evidence,
    )


def _analysis_of(review: PurchaseReview) -> AnalysisResponseModel:
    """검토 상태에서 분석 블록만 뽑습니다.

    ``candidate_count`` · ``score_gap`` 은 후보 목록에서 **그대로 계산한
    값**입니다. 새 판정을 만들지 않습니다.
    """
    candidates = list(review.candidates)
    gap = candidates[0].score - candidates[1].score if len(candidates) > 1 else None
    return AnalysisResponseModel(
        status=review.analysis_status,
        analyzer_name=review.analyzer_name,
        analyzer_version=review.analyzer_version,
        analyzed_at=review.analyzed_at,
        is_ambiguous=review.is_ambiguous,
        candidates=[_candidate_of(candidate) for candidate in candidates],
        candidate_count=len(candidates),
        score_gap=gap,
        note=review.analysis_note,
    )


def _past_labels_of(summary: PastLabelSummary, top_candidate: object) -> PastLabelsResponseModel:
    """과거 확정 이력을 응답 모델로 변환합니다."""
    from procurement.models.classification import TypeCandidate

    top_type = top_candidate.purchase_type if isinstance(top_candidate, TypeCandidate) else None
    return PastLabelsResponseModel(
        labels=[
            PastLabelResponseModel(
                purchase_type=label.purchase_type, label=label.label, count=label.count
            )
            for label in summary.labels
        ],
        total=summary.total,
        type_count=summary.type_count,
        has_conflict=summary.has_conflict,
        differs_from_top_candidate=summary.differs_from(top_type),
    )


def _review_state_of(review: PurchaseReview) -> ReviewStateResponseModel:
    """검토 상태에서 확정 블록만 뽑습니다."""
    return ReviewStateResponseModel(
        status=review.review_status,
        final_purchase_type=review.final_purchase_type,
        final_purchase_type_label=review.final_purchase_type_label,
        reviewed_by=review.reviewed_by,
        reviewed_at=review.reviewed_at,
        review_note=review.review_note,
    )
