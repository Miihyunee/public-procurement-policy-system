"""
procurement.experiments

🔬 **적요 분석 방법 비교 실험** — 운영 경로와 분리된 실험용 패키지.

BM25 · RAG · FUSE 중 무엇이 실제 적요에 맞는지 **실측으로 판단**하기 위한
환경입니다. 셋 다 구현하되 **어느 것도 선택하지 않습니다.**

.. warning::
    ⛔ **운영 코드가 이 패키지를 import 하지 않습니다.**

    ``app.py`` · Calculator · Dashboard · Repository 어디에서도 참조하지
    않으며, 이를 테스트로 고정합니다. 방법이 확정되면 그때 승인을 받아
    운영 경로로 옮깁니다.

.. warning::
    ⛔ **새 구매유형 규칙을 만들지 않습니다.**

    세 구현체 모두 **담당자가 확정한 사례(corpus)** 에서만 배웁니다. 손으로
    쓴 키워드 목록이나 예산과목 매핑을 새로 만들지 않습니다. 코퍼스가 비어
    있으면 후보를 하나도 만들지 않습니다.

설계 근거: ``docs/DESCRIPTION_SIMILARITY_DESIGN.md``
"""

from procurement.experiments.bm25 import BM25Classifier
from procurement.experiments.comparison import (
    ComparisonReport,
    ItemComparison,
    MethodReport,
    run_comparison,
)
from procurement.experiments.corpus import ClassificationCorpus, LabeledExample
from procurement.experiments.fuse import FUSEClassifier
from procurement.experiments.rag import RAGClassifier

__all__ = [
    "BM25Classifier",
    "ClassificationCorpus",
    "ComparisonReport",
    "FUSEClassifier",
    "ItemComparison",
    "LabeledExample",
    "MethodReport",
    "RAGClassifier",
    "run_comparison",
]
