"""
tests.test_description_classifier

적요 분석 **인터페이스**와 분류 결과 모델 검증.

여기서 잡으려는 것은 "분석이 잘 되는가" 가 아닙니다. **분석 방법이 아직
선택되지 않았기 때문**입니다(BM25 · RAG · FUSE — 결정 대기).

대신 다음을 고정합니다.

1. 결과에 **최종 확정값이 없다** — 타입 수준에서 자동 확정 불가
2. 분석기가 **원본을 건드릴 수 없다** — 입력이 문자열 하나
3. 기본 구현이 **아무 규칙도 만들지 않는다**

설계 근거: ``docs/DESCRIPTION_SIMILARITY_DESIGN.md``
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from procurement.core.description_classifier import DescriptionClassifier, NoRuleClassifier
from procurement.core.purchase_type import CONSTRUCTION, GOODS, PURCHASE_TYPES, SERVICE
from procurement.models.classification import (
    ANALYZED,
    ClassificationError,
    ClassificationResult,
    TypeCandidate,
)


def _result(*pairs: tuple[str, str]) -> ClassificationResult:
    return ClassificationResult(
        candidates=[
            TypeCandidate(purchase_type=code, score=Decimal(score)) for code, score in pairs
        ],
        analyzer_name="test",
        analyzer_version="1",
    )


class TestTypeCandidate:
    """후보 하나."""

    @pytest.mark.parametrize("purchase_type", sorted(PURCHASE_TYPES))
    def test_allowed_types(self, purchase_type: str) -> None:
        candidate = TypeCandidate(purchase_type=purchase_type, score=Decimal("0.5"))
        assert candidate.purchase_type == purchase_type

    def test_unknown_type_is_rejected(self) -> None:
        """⛔ 새 분류 체계를 만들지 않는다 — 기존 3값만 쓴다."""
        with pytest.raises(ClassificationError, match="허용되지 않는 구매유형"):
            TypeCandidate(purchase_type="ETC", score=Decimal("0.9"))

    @pytest.mark.parametrize("score", ["-0.1", "1.1"])
    def test_score_range(self, score: str) -> None:
        with pytest.raises(ClassificationError, match="0 이상 1 이하"):
            TypeCandidate(purchase_type=SERVICE, score=Decimal(score))

    def test_label_comes_from_the_existing_mapping(self) -> None:
        assert TypeCandidate(purchase_type=CONSTRUCTION, score=Decimal("1")).label == "공사"


class TestClassificationResult:
    """분석 결과."""

    def test_top_and_runner_up(self) -> None:
        result = _result((SERVICE, "0.72"), (CONSTRUCTION, "0.68"))

        assert result.top is not None and result.top.purchase_type == SERVICE
        assert result.runner_up is not None and result.runner_up.purchase_type == CONSTRUCTION

    def test_empty_candidates_are_allowed(self) -> None:
        """판단할 수 없으면 후보를 만들지 않는 것이 정직하다."""
        result = _result()

        assert result.candidates == []
        assert result.top is None
        assert result.runner_up is None
        assert result.is_ambiguous is False

    def test_candidates_must_be_sorted(self) -> None:
        with pytest.raises(ClassificationError, match="내림차순"):
            _result((SERVICE, "0.30"), (CONSTRUCTION, "0.90"))

    def test_unknown_status_is_rejected(self) -> None:
        with pytest.raises(ClassificationError, match="분석 상태"):
            ClassificationResult(analyzer_name="t", analyzer_version="1", status="DONE")

    def test_two_candidates_are_ambiguous(self) -> None:
        """이중 매칭 — '시설물 유지관리' 처럼 후보가 갈리는 경우."""
        assert _result((SERVICE, "0.72"), (CONSTRUCTION, "0.68")).is_ambiguous is True

    def test_one_candidate_is_not_ambiguous(self) -> None:
        """'LED 교체공사' 처럼 명확한 경우."""
        assert _result((CONSTRUCTION, "0.97")).is_ambiguous is False


class TestNoAutoConfirmation:
    """⛔ **자동 확정을 타입 수준에서 막는다** — 가장 중요한 성질."""

    def test_result_has_no_final_field(self) -> None:
        """결과 객체에 최종값 필드가 **없다**."""
        fields = set(ClassificationResult.__dataclass_fields__)

        assert "final_purchase_type" not in fields
        assert not [name for name in fields if name.startswith("final")]
        assert not [name for name in fields if "confirm" in name]

    def test_high_score_is_still_only_a_candidate(self) -> None:
        """0.97 이어도 결과에는 '후보' 만 있다."""
        result = _result((CONSTRUCTION, "0.97"))

        assert result.top is not None
        assert not hasattr(result, "final_purchase_type")

    def test_source_has_no_threshold(self) -> None:
        """⛔ 이중 매칭 임계값을 만들지 않았다(미확정).

        임계값이 생기면 "0.9 이상이면 확정" 같은 규칙으로 미끄러집니다.
        현재는 후보 개수만 봅니다.
        """
        import inspect
        import textwrap

        source = textwrap.dedent(
            inspect.getsource(ClassificationResult.is_ambiguous.fget)  # type: ignore[attr-defined]
        )
        body = source.split('"""')[-1]

        # 후보 개수 비교(> 1) 외의 숫자가 없어야 한다.
        assert body.strip() == "return len(self.candidates) > 1", body


class TestNoRuleClassifier:
    """🔴 기본 구현 — **아무 규칙도 만들지 않는다.**"""

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(NoRuleClassifier(), DescriptionClassifier)

    @pytest.mark.parametrize(
        "description",
        ["시설물 유지관리", "LED 교체공사", "사무용품 구매", "", None],
    )
    def test_always_returns_no_candidate(self, description: str | None) -> None:
        """⛔ 어떤 적요를 넣어도 후보를 만들지 않는다.

        방법이 선택되지 않았으므로 규칙을 만들지 않습니다. 담당자는 원본만
        보고 판단하며, 이는 현재 수작업과 같습니다.
        """
        result = NoRuleClassifier().classify(description)

        assert result.candidates == []
        assert result.status == ANALYZED
        assert result.is_ambiguous is False

    def test_is_deterministic(self) -> None:
        classifier = NoRuleClassifier()
        first = classifier.classify("시설물 유지관리")
        second = classifier.classify("시설물 유지관리")

        assert first.candidates == second.candidates

    def test_identifies_itself(self) -> None:
        """DB-2 에 남아 나중에 '규칙 없이 분석된 건' 을 찾을 수 있다."""
        result = NoRuleClassifier().classify("무엇이든")

        assert result.analyzer_name == "no-rule"
        assert result.analyzer_version == "0"
        assert "결정 대기" in result.note


class TestNoMethodWasChosen:
    """🔴 BM25 · RAG · FUSE 중 어느 것도 구현하지 않았다."""

    def test_no_algorithm_module_exists_outside_experiments(self) -> None:
        """운영 경로에는 BM25 · RAG · FUSE 구현이 없다.

        변경 사유(STEP 4): 세 방법을 **비교하기 위한** 실험 코드를
        ``procurement/experiments/`` 아래에 만들었다. 따라서 "어디에도 없다" 는
        더 이상 사실이 아니다. 다만 **선택하지 않았다** 는 것은 그대로이므로,
        검사 범위를 실험 패키지 밖(= 운영 경로)으로 좁힌다. 실험 코드가 운영
        코드에 스며들지 않는다는 것은
        ``test_experiments_classifiers.py::TestExperimentsAreIsolated`` 가
        따로 지킨다.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        names = {
            path.stem.lower()
            for path in root.rglob("*.py")
            if "experiments" not in path.relative_to(root).parts
        }

        for method in ("bm25", "rag", "fuse"):
            assert method not in names, method

    def test_classifier_takes_only_a_string(self) -> None:
        """⛔ 입력이 문자열 하나 — 예산과목·거래처를 넣으면 그 조합이 규칙이 된다."""
        import inspect

        parameters = list(inspect.signature(NoRuleClassifier.classify).parameters)

        assert parameters == ["self", "description"]

    def test_classifier_cannot_touch_the_database(self) -> None:
        """⛔ 분석기가 Repository 를 참조하지 않는다 → 원본을 건드릴 수 없다."""
        import ast
        from pathlib import Path

        import procurement.core.description_classifier as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert not [name for name in imported if "database" in name or "repository" in name]


class TestExistingTaxonomyIsReused:
    """기존 분류 체계를 그대로 쓴다 — 새로 만들지 않는다."""

    def test_only_three_types(self) -> None:
        assert PURCHASE_TYPES == frozenset({CONSTRUCTION, SERVICE, GOODS})

    def test_undecided_is_not_a_type(self) -> None:
        """'판단 보류' 는 유형이 아니라 **값 없음**(None)이다."""
        assert "UNCLASSIFIED" not in PURCHASE_TYPES
        assert "ETC" not in PURCHASE_TYPES
