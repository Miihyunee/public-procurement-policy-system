#!/usr/bin/env python
"""BM25 · RAG · FUSE 를 같은 데이터로 나란히 돌려 비교하는 실행 스크립트.

.. warning::
    ⛔ **어떤 방법도 선택하지 않습니다.** 숫자만 출력합니다. 방법 선택은
    PM/고객 확인 사항입니다.

.. warning::
    ⛔ **고객 데이터를 저장소에 두지 않습니다.** 입력 CSV 경로는 실행할 때
    인자로 받습니다. 저장소에는 샘플조차 커밋하지 않습니다.

입력 CSV 형식(헤더 필수)::

    description,purchase_type,key
    LED 등기구 교체공사,공사,1
    사무용품 구매,물품,2

``purchase_type`` 은 공사 · 용역 · 물품 셋 중 하나여야 합니다.
``key`` 는 생략 가능하며, 없으면 행 번호를 씁니다.

사용 예::

    python scripts/compare_description_methods.py /path/to/labeled.csv
    python scripts/compare_description_methods.py /path/to/labeled.csv --in-sample
    python scripts/compare_description_methods.py /path/to/labeled.csv --limit 300
    python scripts/compare_description_methods.py /path/to/labeled.csv --time-split 0.8

평가 방식 세 가지의 뜻:

``leave-one-out`` (기본)
    평가할 건을 코퍼스에서 빼고 분석합니다. **권장.**
``--in-sample``
    평가할 건이 코퍼스에 남아 있습니다. 기본값과의 차이가 곧 **암기 정도**입니다.
``--time-split R``
    앞쪽 R 비율을 코퍼스로, 뒤쪽을 평가 대상으로 씁니다. CSV 가 시간순일 때
    실제 운영에 가장 가깝습니다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from procurement.core.purchase_type import PURCHASE_TYPE_LABELS  # noqa: E402
from procurement.experiments.bm25 import BM25Classifier  # noqa: E402
from procurement.experiments.comparison import (  # noqa: E402
    ClassifierFactory,
    run_comparison,
)
from procurement.experiments.corpus import (  # noqa: E402
    ClassificationCorpus,
    CorpusError,
    LabeledExample,
)
from procurement.experiments.fuse import FUSEClassifier  # noqa: E402
from procurement.experiments.rag import RAGClassifier  # noqa: E402

#: 한글 표기 → 내부 코드. **표기 변환일 뿐 업무규칙이 아닙니다.**
#: 기존 :data:`PURCHASE_TYPE_LABELS` 를 뒤집어 쓰므로 새 분류 체계가 생기지
#: 않습니다.
_LABEL_TO_CODE = {label: code for code, label in PURCHASE_TYPE_LABELS.items()}


def load_examples(path: Path) -> list[LabeledExample]:
    """CSV 에서 담당자 확정 사례를 읽습니다.

    Args:
        path: 입력 CSV 경로.

    Returns:
        사례 목록. 적요가 비었거나 유형이 허용값이 아닌 행은 건너뜁니다.
    """
    examples: list[LabeledExample] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            description = (row.get("description") or "").strip()
            raw_type = (row.get("purchase_type") or "").strip()
            purchase_type = _LABEL_TO_CODE.get(raw_type, raw_type)
            if not description or not purchase_type:
                continue
            try:
                examples.append(
                    LabeledExample(
                        description=description,
                        purchase_type=purchase_type,
                        key=(row.get("key") or "").strip() or str(index),
                    )
                )
            except CorpusError as error:
                print(f"  건너뜀 {index}행: {error}", file=sys.stderr)
    return examples


def build_factories() -> dict[str, ClassifierFactory]:
    """비교할 세 방법. ⛔ 우열을 두지 않고 나란히 놓습니다."""
    return {
        "BM25": BM25Classifier,
        "RAG(token-cosine)": RAGClassifier,
        "FUSE(BM25+RAG)": lambda corpus: FUSEClassifier(
            [BM25Classifier(corpus), RAGClassifier(corpus)]
        ),
    }


def majority_baseline(
    corpus: ClassificationCorpus, targets: list[LabeledExample]
) -> tuple[str, Decimal]:
    """ "항상 가장 흔한 유형" 이라고 답했을 때의 정확도(기준선).

    분석기 숫자가 높아 보여도 이 기준선을 넘지 못하면 의미가 없습니다.
    """
    counts = corpus.label_counts()
    if not counts or not targets:
        return "-", Decimal("0.00")
    label = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    hits = sum(1 for example in targets if example.purchase_type == label)
    return label, (Decimal(hits) / Decimal(len(targets)) * 100).quantize(Decimal("0.01"))


def main() -> int:
    """스크립트 진입점."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="담당자 확정 사례 CSV (저장소 밖 경로)")
    parser.add_argument("--limit", type=int, default=0, help="평가 대상 건수 상한(0=전체)")
    parser.add_argument("--in-sample", action="store_true", help="암기 포함 평가")
    parser.add_argument(
        "--time-split",
        type=float,
        default=0.0,
        help="앞쪽 R 비율을 코퍼스, 뒤쪽을 평가 대상으로 사용(0=사용 안 함)",
    )
    arguments = parser.parse_args()

    examples = load_examples(arguments.csv_path)
    if not examples:
        print("사례를 하나도 읽지 못했습니다.", file=sys.stderr)
        return 1

    distribution = dict(Counter(example.purchase_type for example in examples))
    print(f"읽은 사례 {len(examples):,}건 · 유형 분포 {distribution}")

    if arguments.time_split:
        cut = int(len(examples) * arguments.time_split)
        corpus = ClassificationCorpus.from_examples(examples[:cut])
        targets = examples[cut:]
        leave_one_out = False
        print(f"시간 분할: 코퍼스 {len(corpus):,}건 → 평가 {len(targets):,}건")
    else:
        corpus = ClassificationCorpus.from_examples(examples)
        targets = list(examples)
        leave_one_out = not arguments.in_sample

    if arguments.limit:
        targets = targets[: arguments.limit]

    label, baseline = majority_baseline(corpus, targets)
    print(f"기준선(항상 '{label}') {baseline}%\n")

    report = run_comparison(
        corpus,
        build_factories(),
        evaluation_set=targets,
        leave_one_out=leave_one_out,
    )
    lines = list(report.table_lines())
    if arguments.time_split:
        # ⚠️ 표는 leave_one_out 여부만 알므로 시간 분할을 "in-sample" 로 적는다.
        #    평가 대상이 코퍼스에 없으므로 사실과 다르다. 여기서 바로잡는다.
        lines[0] = (
            f"코퍼스 {len(corpus):,}건 · 평가 방식 시간 분할"
            f"(앞 {arguments.time_split:.0%} 학습 → 뒤 {len(targets):,}건 평가, 암기 없음)"
        )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
