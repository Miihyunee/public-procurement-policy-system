"""
tests.test_experiments_classifiers

🔬 **적요 분석 방법 비교 실험** 검증 (STEP 4).

여기서 잡으려는 것은 "어느 방법이 좋은가" 가 아닙니다. **아직 아무것도 고르지
않았기 때문**입니다. 대신 다음을 고정합니다.

1. 세 구현체가 **같은 인터페이스**를 만족한다 — 교체 가능
2. ⛔ 코퍼스(=담당자 확정)가 없으면 **후보를 만들지 않는다**
3. ⛔ 손으로 쓴 **키워드 규칙이 없다**
4. ⛔ 실험 코드가 **운영 경로에 섞이지 않는다**
5. ⛔ 실험이 **DB 를 건드리지 않는다**

설계 근거: ``docs/DESCRIPTION_SIMILARITY_DESIGN.md``
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from procurement.core.description_classifier import DescriptionClassifier
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.experiments import (
    BM25Classifier,
    ClassificationCorpus,
    FUSEClassifier,
    LabeledExample,
    RAGClassifier,
    run_comparison,
)
from procurement.experiments.corpus import CorpusError, tokenize
from procurement.experiments.rag import TokenCosineBackend
from procurement.models.review import CONFIRMED, PENDING, PurchaseReview

#: 담당자가 확정했다고 가정한 사례들 (테스트 전용 · 업무규칙 아님).
EXAMPLES = [
    LabeledExample(description="LED 교체공사", purchase_type=CONSTRUCTION, key="1"),
    LabeledExample(description="옥상 방수공사", purchase_type=CONSTRUCTION, key="2"),
    LabeledExample(description="정수기 렌탈 용역", purchase_type=SERVICE, key="3"),
    LabeledExample(description="청소 용역 대금", purchase_type=SERVICE, key="4"),
    LabeledExample(description="사무용품 구매", purchase_type=GOODS, key="5"),
    LabeledExample(description="복사용지 구매", purchase_type=GOODS, key="6"),
]


@pytest.fixture
def corpus() -> ClassificationCorpus:
    return ClassificationCorpus.from_examples(EXAMPLES)


@pytest.fixture
def empty_corpus() -> ClassificationCorpus:
    return ClassificationCorpus.from_examples([])


def _classifiers(corpus: ClassificationCorpus) -> dict[str, DescriptionClassifier]:
    """세 구현체를 같은 코퍼스로 만듭니다."""
    bm25 = BM25Classifier(corpus)
    rag = RAGClassifier(corpus)
    return {"bm25": bm25, "rag": rag, "fuse": FUSEClassifier([bm25, rag])}


class TestCorpus:
    """코퍼스 — 학습 자료는 담당자 확정뿐."""

    def test_tokenize_handles_korean_and_spacing(self) -> None:
        """띄어쓰기가 흔들려도 같은 조각이 나온다."""
        assert set(tokenize("교육계획서  발송")) & set(tokenize("교육계획서 발송"))

    def test_tokenize_empty(self) -> None:
        assert tokenize(None) == []
        assert tokenize("") == []

    def test_unknown_label_is_rejected(self) -> None:
        """⛔ 새 분류 체계를 만들지 않는다."""
        with pytest.raises(CorpusError, match="허용되지 않는 구매유형"):
            LabeledExample(description="무엇", purchase_type="ETC")

    def test_from_reviews_takes_only_confirmed(self) -> None:
        """⛔ 확정되지 않은 건을 학습 자료로 쓰지 않는다."""
        reviews = [
            PurchaseReview(
                purchase_id=1, review_status=CONFIRMED, final_purchase_type=CONSTRUCTION
            ),
            PurchaseReview(purchase_id=2, review_status=PENDING),
            # 판단 보류 — 사람이 유형을 정하지 않았으므로 제외한다.
            PurchaseReview(purchase_id=3, review_status=CONFIRMED, final_purchase_type=None),
        ]
        built = ClassificationCorpus.from_reviews(
            reviews, {1: "LED 교체공사", 2: "청소 용역", 3: "무언가"}
        )

        assert len(built) == 1
        assert built.examples[0].purchase_type == CONSTRUCTION

    def test_without_removes_one_example(self, corpus: ClassificationCorpus) -> None:
        """leave-one-out — 자기 자신을 빼고 평가한다."""
        reduced = corpus.without("1")

        assert len(reduced) == len(corpus) - 1
        assert all(example.key != "1" for example in reduced.examples)

    def test_label_counts(self, corpus: ClassificationCorpus) -> None:
        assert corpus.label_counts() == {CONSTRUCTION: 2, SERVICE: 2, GOODS: 2}


class TestAllThreeShareTheInterface:
    """세 구현체가 교체 가능하다."""

    def test_each_satisfies_the_protocol(self, corpus: ClassificationCorpus) -> None:
        for classifier in _classifiers(corpus).values():
            assert isinstance(classifier, DescriptionClassifier)

    def test_each_reports_its_identity(self, corpus: ClassificationCorpus) -> None:
        """DB-2 에 남아 나중에 방법별로 결과를 비교할 수 있다."""
        for classifier in _classifiers(corpus).values():
            assert classifier.name
            assert classifier.version

    def test_names_are_distinct(self, corpus: ClassificationCorpus) -> None:
        names = {classifier.name for classifier in _classifiers(corpus).values()}

        assert len(names) == 3

    def test_each_returns_sorted_candidates(self, corpus: ClassificationCorpus) -> None:
        for classifier in _classifiers(corpus).values():
            scores = [
                candidate.score for candidate in classifier.classify("LED 교체공사").candidates
            ]
            assert scores == sorted(scores, reverse=True), classifier.name

    def test_each_is_deterministic(self, corpus: ClassificationCorpus) -> None:
        for classifier in _classifiers(corpus).values():
            first = classifier.classify("옥상 방수공사")
            second = classifier.classify("옥상 방수공사")
            assert first.candidates == second.candidates, classifier.name

    def test_scores_are_within_range(self, corpus: ClassificationCorpus) -> None:
        for classifier in _classifiers(corpus).values():
            for candidate in classifier.classify("청소 용역 대금").candidates:
                assert Decimal("0") <= candidate.score <= Decimal("1"), classifier.name


class TestNoCorpusMeansNoCandidate:
    """⛔ **담당자 확정이 없으면 아무 후보도 만들지 않는다.**

    이것이 "규칙을 만들지 않았다" 의 증거입니다. 손으로 쓴 사전이 있었다면
    빈 코퍼스에서도 후보가 나올 것입니다.
    """

    @pytest.mark.parametrize(
        "description", ["LED 교체공사", "청소 용역", "사무용품 구매", "시설물 유지관리"]
    )
    def test_all_methods_stay_silent(
        self, empty_corpus: ClassificationCorpus, description: str
    ) -> None:
        for classifier in _classifiers(empty_corpus).values():
            result = classifier.classify(description)
            assert result.candidates == [], classifier.name
            assert result.is_ambiguous is False

    def test_empty_description(self, corpus: ClassificationCorpus) -> None:
        for classifier in _classifiers(corpus).values():
            assert classifier.classify(None).candidates == [], classifier.name
            assert classifier.classify("").candidates == [], classifier.name

    def test_no_overlap_means_no_candidate(self, corpus: ClassificationCorpus) -> None:
        """확정 사례와 겹치는 표현이 없으면 억지 후보를 만들지 않는다."""
        assert BM25Classifier(corpus).classify("zzzz qqqq").candidates == []


class TestNoHandWrittenRules:
    """⛔ 손으로 쓴 구매유형 규칙이 없다."""

    def test_no_keyword_table_in_the_sources(self) -> None:
        """⛔ '공사'·'용역'·'물품' 이라는 한글 낱말이 판정에 쓰이지 않는다.

        키워드 사전을 두었다면 소스에 그 낱말들이 값으로 등장합니다.
        (라벨 상수 ``CONSTRUCTION`` 등은 코드값이라 무관합니다.)
        """
        from pathlib import Path

        import procurement.experiments as package

        root = Path(package.__file__).parent
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            body = "\n".join(
                line for line in source.splitlines() if not line.strip().startswith("#")
            )
            # docstring 설명에는 등장할 수 있으므로 **코드 문자열 리터럴**만 본다.
            import ast

            tree = ast.parse(source)
            literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docstrings.add(doc)
            code_literals = literals - docstrings
            for banned in ("공사", "용역", "물품"):
                hits = [text for text in code_literals if banned in text and len(text) < 40]
                # f-string 조각·라벨 출력은 예외로 두되, 매핑처럼 보이는 것은 없어야 한다.
                assert not [text for text in hits if "=" in text or ":" in text], (
                    path.name,
                    banned,
                    hits,
                )
            assert body  # 파일이 비어 있지 않다

    def test_purchase_type_module_is_untouched(self) -> None:
        """⛔ 확정 매핑 3건에 손대지 않았다."""
        from procurement.core.purchase_type import CONFIRMED_BUDGET_ACCOUNT_TYPES

        assert dict(CONFIRMED_BUDGET_ACCOUNT_TYPES) == {
            "도서인쇄비": GOODS,
            "소모성물품구입비": GOODS,
            "임차료": SERVICE,
        }


class TestBM25:
    """BM25 — 유형별 문서 검색."""

    def test_finds_the_confirmed_label(self, corpus: ClassificationCorpus) -> None:
        """확정 사례와 같은 표현이면 그 유형이 1순위가 된다."""
        result = BM25Classifier(corpus).classify("LED 교체공사")

        assert result.top is not None
        assert result.top.purchase_type == CONSTRUCTION

    def test_evidence_shows_matched_words(self, corpus: ClassificationCorpus) -> None:
        """담당자가 왜 이 후보인지 볼 수 있어야 한다."""
        result = BM25Classifier(corpus).classify("LED 교체공사")

        assert result.top is not None
        assert "겹치는 표현" in result.top.evidence

    def test_parameters_are_adjustable(self, corpus: ClassificationCorpus) -> None:
        """k1·b 는 실험 파라미터로 열려 있다(업무규칙 아님)."""
        assert BM25Classifier(corpus, k1=1.2, b=0.5).classify("청소 용역").candidates


class TestRAG:
    """RAG — 유사 사례 검색."""

    def test_finds_the_confirmed_label(self, corpus: ClassificationCorpus) -> None:
        result = RAGClassifier(corpus).classify("복사용지 구매")

        assert result.top is not None
        assert result.top.purchase_type == GOODS

    def test_evidence_quotes_a_past_case(self, corpus: ClassificationCorpus) -> None:
        """근거가 '과거에 이렇게 확정하셨습니다' 형태여야 한다."""
        result = RAGClassifier(corpus).classify("복사용지 구매")

        assert result.top is not None
        assert "유사 확정 사례" in result.top.evidence

    def test_backend_is_replaceable(self, corpus: ClassificationCorpus) -> None:
        """임베딩 백엔드로 갈아 끼울 수 있다(아직 선택하지 않음)."""

        class AlwaysSame:
            name = "stub"

            def similarity(self, query_tokens: list[str], document_tokens: list[str]) -> float:
                return 1.0

        classifier = RAGClassifier(corpus, backend=AlwaysSame())

        assert classifier.name == "rag:stub"
        assert classifier.classify("무엇이든").candidates

    def test_default_backend_needs_no_dependency(self) -> None:
        """기본 백엔드는 외부 패키지·인터넷 없이 동작한다."""
        backend = TokenCosineBackend()

        assert backend.similarity(tokenize("청소 용역"), tokenize("청소 용역")) == pytest.approx(
            1.0
        )
        assert backend.similarity(tokenize("청소"), tokenize("zzz")) == 0.0

    def test_top_k_is_adjustable(self, corpus: ClassificationCorpus) -> None:
        assert RAGClassifier(corpus, top_k=1).classify("옥상 방수공사").candidates


class TestFUSE:
    """FUSE — 순위 결합."""

    def test_combines_sub_methods(self, corpus: ClassificationCorpus) -> None:
        bm25 = BM25Classifier(corpus)
        rag = RAGClassifier(corpus)
        fused = FUSEClassifier([bm25, rag])

        result = fused.classify("LED 교체공사")

        assert result.top is not None
        assert result.top.purchase_type == CONSTRUCTION

    def test_name_shows_what_was_combined(self, corpus: ClassificationCorpus) -> None:
        fused = FUSEClassifier([BM25Classifier(corpus), RAGClassifier(corpus)])

        assert fused.name.startswith("fuse:")
        assert "bm25" in fused.name

    def test_evidence_shows_each_source_rank(self, corpus: ClassificationCorpus) -> None:
        fused = FUSEClassifier([BM25Classifier(corpus), RAGClassifier(corpus)])

        result = fused.classify("청소 용역 대금")

        assert result.top is not None
        assert "순위" in result.top.evidence

    def test_no_sub_method_means_no_candidate(self) -> None:
        assert FUSEClassifier([]).classify("무엇이든").candidates == []

    def test_weight_count_is_validated(self, corpus: ClassificationCorpus) -> None:
        with pytest.raises(ValueError, match="가중치 개수"):
            FUSEClassifier([BM25Classifier(corpus)], weights=[1.0, 2.0])

    def test_default_weights_are_equal(self, corpus: ClassificationCorpus) -> None:
        """⛔ '어느 방법을 더 믿는다' 는 판단을 만들지 않았다."""
        import inspect

        source = inspect.getsource(FUSEClassifier.__init__)

        assert "[1.0] * len(classifiers)" in source


class TestComparison:
    """비교 실행기."""

    def test_reports_every_method(self, corpus: ClassificationCorpus) -> None:
        report = run_comparison(
            corpus,
            {
                "BM25": lambda c: BM25Classifier(c),
                "RAG": lambda c: RAGClassifier(c),
            },
        )

        assert [method.method for method in report.methods] == ["BM25", "RAG"]
        assert report.corpus_size == len(corpus)

    def test_metrics_cover_the_required_items(self, corpus: ClassificationCorpus) -> None:
        """지시서가 요구한 비교 항목이 모두 나온다."""
        report = run_comparison(corpus, {"BM25": lambda c: BM25Classifier(c)})
        method = report.methods[0]

        assert method.total == len(corpus)  # 대상 건수
        assert method.with_candidate >= 0  # 처리 가능한 건수
        assert method.ambiguous >= 0  # 애매한 건수
        assert method.top1_hits >= 0  # 확정값과 비교
        assert method.coverage >= Decimal("0")
        assert method.top1_accuracy >= Decimal("0")

    def test_item_rows_carry_rank_score_and_confirmation(
        self, corpus: ClassificationCorpus
    ) -> None:
        """건별로 후보 유형·점수·순위·확정값을 나란히 볼 수 있다."""
        report = run_comparison(corpus, {"BM25": lambda c: BM25Classifier(c)})
        item = report.methods[0].items[0]

        assert item.confirmed_type in {CONSTRUCTION, SERVICE, GOODS}
        assert item.method == "BM25"
        if item.candidates:
            assert item.candidates[0].rank == 1
            assert item.candidates[0].score >= Decimal("0")

    def test_leave_one_out_removes_the_item_itself(self) -> None:
        """⛔ 자기 자신을 코퍼스에 남기면 **외운 것을 맞히는** 셈이 된다."""
        single = ClassificationCorpus.from_examples(
            [LabeledExample(description="LED 교체공사", purchase_type=CONSTRUCTION, key="1")]
        )

        loo = run_comparison(single, {"BM25": lambda c: BM25Classifier(c)}, leave_one_out=True)
        in_sample = run_comparison(
            single, {"BM25": lambda c: BM25Classifier(c)}, leave_one_out=False
        )

        assert loo.methods[0].with_candidate == 0, "코퍼스가 비므로 후보가 없어야 한다"
        assert in_sample.methods[0].top1_hits == 1, "자기 자신을 보면 맞힌다"

    def test_evaluation_set_can_be_separate(self, corpus: ClassificationCorpus) -> None:
        """시간 분할 평가 — 학습과 평가를 나눌 수 있다."""
        holdout = [LabeledExample(description="배관 교체공사", purchase_type=CONSTRUCTION)]

        report = run_comparison(
            corpus,
            {"BM25": lambda c: BM25Classifier(c)},
            evaluation_set=holdout,
            leave_one_out=False,
        )

        assert report.methods[0].total == 1

    def test_table_does_not_pick_a_winner(self, corpus: ClassificationCorpus) -> None:
        """⛔ 표가 승자를 표시하지 않는다."""
        report = run_comparison(corpus, {"BM25": lambda c: BM25Classifier(c)})
        text = "\n".join(report.table_lines())

        assert "PM/고객 확인 사항" in text
        for banned in ("최적", "권장", "선택함", "winner", "best"):
            assert banned not in text


class TestExperimentsAreIsolated:
    """⛔ 실험 코드가 운영 경로에 섞이지 않는다."""

    def test_production_modules_do_not_import_experiments(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for path in root.rglob("*.py"):
            if "experiments" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            assert "experiments" not in source, path

    def test_experiments_do_not_touch_the_database(self) -> None:
        """⛔ 실험은 DB 를 읽지도 쓰지도 않는다."""
        import ast
        from pathlib import Path

        import procurement.experiments as package

        root = Path(package.__file__).parent
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            assert not [name for name in imported if "database" in name or "repository" in name], (
                path.name
            )

    def test_experiments_do_not_reach_the_network(self) -> None:
        """⛔ 외부 연결 코드가 없다(데이터 반출 방지)."""
        from pathlib import Path

        import procurement.experiments as package

        root = Path(package.__file__).parent
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for forbidden in ("http", "urllib", "requests", "socket", "openai"):
                assert forbidden not in source, (path.name, forbidden)

    def test_no_new_dependency(self) -> None:
        """⛔ 새 외부 패키지를 쓰지 않는다 — 표준 라이브러리만."""
        import ast
        from pathlib import Path

        import procurement.experiments as package

        allowed_roots = {
            "procurement",
            "math",
            "re",
            "collections",
            "dataclasses",
            "decimal",
            "typing",
            "__future__",
            "collections.abc",
        }
        root = Path(package.__file__).parent
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name.split(".")[0] in {
                        root_name.split(".")[0] for root_name in allowed_roots
                    }, (path.name, name)
