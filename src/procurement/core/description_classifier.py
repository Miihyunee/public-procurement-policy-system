"""
procurement.core.description_classifier

적요(``description``) → **구매유형 후보** 를 만드는 분석기의 **공통 인터페이스**.

.. warning::
    🔴 **분석 방법을 선택하지 않았습니다.**

    BM25 · RAG · FUSE 중 무엇을 쓸지는 **실측 후 결정**합니다
    (``docs/DESCRIPTION_SIMILARITY_DESIGN.md`` §3). 지금 하나를 고르면 근거
    없는 규칙이 코드에 박힙니다.

    그래서 이 모듈에는 **인터페이스와, 아무 규칙도 만들지 않는 기본 구현
    하나**만 둡니다. 방법이 정해지면 :class:`DescriptionClassifier` 를 구현한
    클래스를 추가하기만 하면 되고, 상위 계층(DB-2 · 검토 API · 화면)은 바뀌지
    않습니다.

.. warning::
    ⛔ **분석기는 원본을 수정하지 않습니다.**

    입력은 **문자열 하나**입니다. Repository 를 주입받지 않으므로 DB-1 을
    건드릴 수 없습니다. 예산과목·거래처를 입력에 넣지 않는 이유도 같습니다 —
    넣는 순간 그 조합이 **확정되지 않은 분류 규칙**이 됩니다.

.. warning::
    ⛔ **분석기는 확정하지 않습니다.**

    반환형 :class:`~procurement.models.classification.ClassificationResult` 에는
    최종 유형 필드가 없습니다. 확정은 담당자만 하며 DB-2 에 기록됩니다.

설계 근거: ``docs/DESCRIPTION_SIMILARITY_DESIGN.md``
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from procurement.models.classification import ANALYZED, ClassificationResult


@runtime_checkable
class DescriptionClassifier(Protocol):
    """적요를 읽어 구매유형 후보를 만드는 분석기.

    구현체는 다음을 지켜야 합니다.

    1. **결정적**이어야 한다 — 같은 입력에는 같은 결과
    2. 판단할 수 없으면 **후보를 만들지 않는다**(빈 목록)
    3. 후보는 **점수 내림차순**
    4. ⛔ 최종 유형을 정하지 않는다

    Attributes:
        name: 분석기 이름. DB-2 에 기록되어 방법 비교에 쓰입니다.
        version: 분석기 버전. 규칙이 바뀌면 올립니다.
    """

    name: str
    version: str

    def classify(self, description: str | None) -> ClassificationResult:
        """적요 하나를 분석해 후보를 반환합니다.

        Args:
            description: 원본 적요. ``None`` 이거나 공백일 수 있습니다.

        Returns:
            :class:`ClassificationResult`. 후보가 없어도 결과 객체는 반환합니다.
        """
        ...


class NoRuleClassifier:
    """🔴 **아무 규칙도 만들지 않는** 기본 분석기 (자리 표시자).

    항상 **후보 0개**를 반환합니다. 즉 모든 건이 "미분류" 로 남아 담당자가
    직접 고르게 됩니다.

    .. note::
        **왜 이런 것을 두는가**

        검토 화면과 DB-2 는 지금 만들 수 있지만, 분석 방법은 실측 후에야
        고를 수 있습니다. 그 사이를 비워 두면 화면을 테스트할 수 없고, 아무
        규칙이나 넣으면 **확정되지 않은 업무규칙**이 생깁니다.

        이 구현은 **후보를 만들지 않음으로써 어떤 규칙도 만들지 않습니다.**
        담당자는 원본만 보고 판단하게 되며, 이는 현재 수작업과 같습니다.
        분석 방법이 정해지면 이 클래스를 교체하면 됩니다.

    .. warning::
        ⛔ 여기에 키워드 목록·유사도 계산·예산과목 매핑을 **추가하지 마십시오.**
        그것이 곧 방법 선택이며, 실측 없이 하지 않기로 한 결정입니다
        (``DESCRIPTION_SIMILARITY_DESIGN.md`` §3.5).
    """

    #: 분석기 이름. DB-2 에 이 값이 남으므로 나중에 "규칙 없이 분석된 건" 을
    #: 찾아낼 수 있습니다.
    name = "no-rule"

    #: 규칙이 없으므로 버전도 고정입니다.
    version = "0"

    def classify(self, description: str | None) -> ClassificationResult:
        """항상 후보 0개를 반환합니다.

        Args:
            description: 원본 적요(사용하지 않습니다).

        Returns:
            후보가 비어 있는 :class:`ClassificationResult`.
        """
        return ClassificationResult(
            candidates=[],
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
            note="분석 방법이 아직 선택되지 않아 후보를 만들지 않습니다(결정 대기).",
        )
