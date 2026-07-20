"""
패키지 초기화 상태를 검증하는 smoke test.
비즈니스 로직은 아직 구현되지 않으므로 import 및 메타데이터만 확인합니다.
"""

import procurement


def test_version_exists() -> None:
    """__version__ 속성이 정의되어 있어야 합니다."""
    assert hasattr(procurement, "__version__")
    assert isinstance(procurement.__version__, str)


def test_subpackages_importable() -> None:
    """서브패키지들이 오류 없이 import 되어야 합니다."""
    from procurement import core, collectors, matchers, calculators, models, database, api  # noqa: F401