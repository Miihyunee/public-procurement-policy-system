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

from procurement.core.description_hints import DescriptionHint, find_hints
from procurement.core.purchase_type import PURCHASE_TYPE_LABELS
from procurement.models.purchase import Purchase
from procurement.models.review import (
    PurchaseReview,
    ReviewHistoryEntry,
    ReviewProgress,
)
from procurement.reviews.past_labels import PastLabelSummary
from procurement.reviews.query import PageInfo
from procurement.reviews.review_service import ConditionProgress, ReviewTarget


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

    @classmethod
    def from_purchase(cls, purchase: Purchase) -> PurchaseSourceResponseModel:
        """저장된 구매 행을 그대로 옮겨 담습니다.

        ⛔ **읽기 전용입니다.** 값을 채우거나 고치지 않습니다. 비어 있는 날짜는
        비어 있는 채로(``None``) 나갑니다 — 다른 날짜로 대체하지 않습니다.

        Args:
            purchase: 저장된 구매 행. ``purchase_id`` 가 있어야 합니다.
        """
        assert purchase.purchase_id is not None  # 저장된 행만 화면에 나간다
        return cls(
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
        )


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
        dominant_type: 과거 최다 확정 유형. 이력이 없으면 ``null``.
        dominant_label: 그 한글 라벨.
        dominant_ratio: 최다 유형이 차지한 비율(%). ⛔ 기준선 없음.
        consistency: ``NO_HISTORY`` / ``SINGLE_TYPE`` / ``MIXED_TYPES``.
            **구조적 구분**이며 점수를 잘라 만든 등급이 아닙니다.
    """

    model_config = ConfigDict(frozen=True)

    labels: list[PastLabelResponseModel]
    total: int
    type_count: int
    has_conflict: bool
    differs_from_top_candidate: bool
    dominant_type: str | None
    dominant_label: str | None
    dominant_ratio: Decimal
    consistency: str

    @field_serializer("dominant_ratio")
    def _dominant_ratio(self, value: Decimal) -> str:
        return str(value)


class CompanyLabelsResponseModel(BaseModel):
    """같은 **거래처**의 과거 확정 이력 — 담당자 판단을 돕는 참고 정보.

    고객이 *"실제 계약했던 업체명을 검색해서 공사 여부를 판단하기도 한다"* 고
    답했습니다(2026-08-25). 그 작업을 화면에서 볼 수 있게 한 것입니다.

    .. warning::
        🔴 **판정이 아닙니다.** 과거 기록을 세어 보여줄 뿐이며, "이 업체는
        공사업체다" 같은 결론을 말하지 않습니다. 확정값을 미리 채우지도
        않습니다.

        ⛔ 그래서 ``dominant_type`` · ``score`` · ``rank`` ·
        ``recommended_type`` 필드가 **의도적으로 없습니다.** 적요 이력
        (:class:`PastLabelsResponseModel`)에 있는 "최다 유형" 도 여기서는
        빼 두었습니다 — 거래처 축에서는 그것이 "이 업체 = 이 유형" 으로
        읽히기 쉽기 때문입니다.

    .. note::
        **사업자등록번호가 정확히 같은 건만 셉니다**(2026-08-30 고객 확정 ·
        ``DECISIONS.md`` §0.9.5 원칙 4). 거래처명 표기가 갈려도 번호가 같으면
        한 업체로 모이고, 이름이 같아도 번호가 다르면 나뉩니다 —
        :mod:`~procurement.reviews.company_labels` 참조.

    Attributes:
        business_no: 이 이력을 센 **기준**이 된 사업자등록번호.
        company_name: 이 건의 거래처명(**표시용**). ⛔ 묶음 기준이 아닙니다 —
            같은 사업자번호에 다른 표기가 섞여 있어도 이력은 하나입니다.
        labels: 유형별 건수(내림차순). 이력이 없으면 빈 목록.
        total: 과거 확정 건수 합계.
        type_count: 과거에 붙은 서로 다른 유형 수.
        has_conflict: 같은 거래처가 여러 유형으로 확정된 적이 있는가.
            **먼저 볼 것을 권하는 표시**이며 자동 판정에 쓰지 않습니다.
        consistency: ``NO_HISTORY`` / ``SINGLE_TYPE`` / ``MIXED_TYPES``.
            **구조적 구분**이며 점수를 잘라 만든 등급이 아닙니다.
    """

    model_config = ConfigDict(frozen=True)

    business_no: str
    company_name: str
    labels: list[PastLabelResponseModel]
    total: int
    type_count: int
    has_conflict: bool
    consistency: str


class DescriptionHintResponseModel(BaseModel):
    """적요에서 발견된 낱말 하나 — **참고 근거**입니다.

    .. warning::
        🔴 **판정이 아닙니다.** 고객이 말한 낱말이 적요에 들어 있다는
        **관찰 사실**일 뿐이며, 어떤 구매유형인지 말하지 않습니다.

        ⛔ 그래서 ``purchase_type`` · ``score`` · ``rank`` 필드가
        **의도적으로 없습니다.** 후보(:class:`CandidateResponseModel`)와
        섞이지 않도록 별도 모델로 둡니다.

    Attributes:
        keyword: 발견된 낱말.
        text: 화면에 그대로 쓸 수 있는 문장.
    """

    model_config = ConfigDict(frozen=True)

    keyword: str
    text: str

    @classmethod
    def from_hint(cls, hint: DescriptionHint) -> DescriptionHintResponseModel:
        """관찰 사실 하나를 응답 모델로 바꿉니다."""
        return cls(keyword=hint.keyword, text=hint.text)


class ReviewItemResponseModel(BaseModel):
    """검토 대상 1건 — **원본 · 분석 · 확정을 분리**해 담습니다.

    Attributes:
        source: DB-1 원본.
        analysis: 자동 분석 결과.
        review: 담당자 확정 결과.
        past_labels: 같은 적요의 과거 확정 이력(참고).
        company_labels: 같은 **업체**(사업자등록번호 기준)의 과거 확정 이력(참고).
            ⛔ 적요 이력(``past_labels``)과 **다른 블록**입니다 — 섞이면
            어느 축의 이력인지 알 수 없게 됩니다.
        description_hints: 적요에서 발견된 낱말들 — **참고 근거**.
            ⛔ 판정이 아니며 비어 있을 수 있습니다. 기존 화면이 이 필드를
            몰라도 동작하도록 **기본값을 둡니다**(하위 호환).
    """

    model_config = ConfigDict(frozen=True)

    source: PurchaseSourceResponseModel
    analysis: AnalysisResponseModel
    review: ReviewStateResponseModel
    past_labels: PastLabelsResponseModel
    company_labels: CompanyLabelsResponseModel
    description_hints: list[DescriptionHintResponseModel] = []

    @classmethod
    def from_target(cls, target: ReviewTarget) -> ReviewItemResponseModel:
        """서비스 결과를 응답 모델로 변환합니다."""
        purchase = target.purchase
        review = target.review
        return cls(
            source=PurchaseSourceResponseModel.from_purchase(purchase),
            analysis=_analysis_of(review),
            review=_review_state_of(review),
            past_labels=_past_labels_of(target.past_labels, review.top_candidate),
            company_labels=_company_labels_of(target.company_labels, purchase),
            # ⛔ 원본 적요만 보고 만든다. 확정값·분석 결과를 읽지 않으므로
            #    담당자가 무엇을 골랐든 결과가 달라지지 않는다.
            description_hints=[
                DescriptionHintResponseModel.from_hint(hint)
                for hint in find_hints(purchase.description)
            ],
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


class PageResponseModel(BaseModel):
    """페이지 상태.

    Attributes:
        page: 현재 페이지(1부터).
        page_size: 한 페이지 건수.
        total: **조건에 맞는 전체 건수**(이 페이지에 담긴 수가 아님).
        total_pages: 전체 페이지 수. 결과가 0건이면 1.
        has_previous: 이전 페이지가 있는가.
        has_next: 다음 페이지가 있는가.
    """

    model_config = ConfigDict(frozen=True)

    page: int
    page_size: int
    total: int
    total_pages: int
    has_previous: bool
    has_next: bool

    @classmethod
    def from_page(cls, info: PageInfo) -> PageResponseModel:
        """페이지 상태를 응답 모델로 변환합니다."""
        return cls(
            page=info.page,
            page_size=info.page_size,
            total=info.total,
            total_pages=info.total_pages,
            has_previous=info.has_previous,
            has_next=info.has_next,
        )


class ConditionProgressResponseModel(BaseModel):
    """**현재 조건 안에서의** 확정 진행 상황.

    상단의 전체 진행률(:class:`ReviewProgressResponseModel`)과 **다른 값**
    입니다. 둘을 나란히 보여줘야 "전체 중 얼마" 와 "지금 보고 있는 것 중
    얼마" 를 구분할 수 있습니다.

    ⛔ 평가 기준이 아니라 현황 표시입니다.

    Attributes:
        total: 조건에 맞는 건수.
        confirmed: 그중 확정한 건수.
        pending: 그중 아직 확정하지 않은 건수.
        ratio: 확정 비율(%).
    """

    model_config = ConfigDict(frozen=True)

    total: int
    confirmed: int
    pending: int
    ratio: Decimal

    @field_serializer("ratio")
    def _ratio(self, value: Decimal) -> str:
        return str(value)

    @classmethod
    def from_progress(cls, progress: ConditionProgress) -> ConditionProgressResponseModel:
        """집계 결과를 응답 모델로 변환합니다."""
        return cls(
            total=progress.total,
            confirmed=progress.confirmed,
            pending=progress.pending,
            ratio=progress.ratio,
        )


class ReviewListResponseModel(BaseModel):
    """검토 목록 응답.

    Attributes:
        items: 검토 대상 목록 — 페이지 조건을 주면 **그 페이지만** 담깁니다.
        progress: **전체** 진행 상황(필터와 무관).
        page: 페이지 상태. 페이지 조건을 주지 않으면 ``null``.
        condition: **현재 조건** 안에서의 진행 상황. 〃
    """

    model_config = ConfigDict(frozen=True)

    items: list[ReviewItemResponseModel]
    progress: ReviewProgressResponseModel
    page: PageResponseModel | None = None
    condition: ConditionProgressResponseModel | None = None


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
        dominant_type=summary.dominant.purchase_type if summary.dominant else None,
        dominant_label=summary.dominant.label if summary.dominant else None,
        dominant_ratio=summary.dominant_ratio,
        consistency=summary.consistency,
    )


def _company_labels_of(summary: PastLabelSummary, purchase: Purchase) -> CompanyLabelsResponseModel:
    """같은 업체의 과거 확정 이력을 응답 모델로 변환합니다.

    ⛔ 적요 이력과 달리 **최다 유형·후보 비교를 담지 않습니다** — 업체
    축에서는 그것이 판정으로 읽히기 쉽기 때문입니다.

    ``business_no`` 는 **무엇을 기준으로 셌는지**, ``company_name`` 은 이 건의
    거래처명(표시용)입니다. 둘을 함께 실어 화면이 기준을 밝힐 수 있게 합니다.
    """
    return CompanyLabelsResponseModel(
        business_no=purchase.business_no,
        company_name=purchase.company_name,
        labels=[
            PastLabelResponseModel(
                purchase_type=label.purchase_type, label=label.label, count=label.count
            )
            for label in summary.labels
        ],
        total=summary.total,
        type_count=summary.type_count,
        has_conflict=summary.has_conflict,
        consistency=summary.consistency,
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
