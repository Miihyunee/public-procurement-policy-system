"""
Entry point for ``python -m procurement``.

Usage:
    python -m procurement
    procurement          # pyproject.toml [project.scripts] 를 통해 설치된 경우
"""

from __future__ import annotations

import sys


def main() -> None:
    """Public Procurement Policy System CLI entry point."""
    print("Public Procurement Policy System v0.1.0")
    print("Python:", sys.version)
    print("Status: initialized — business logic not yet implemented.")


if __name__ == "__main__":
    main()