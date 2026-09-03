"""
procurement.admin.policy_target_admin

**연도별 · 정책별 목표비율** 조회·저장 유스케이스입니다.

.. warning::
    ⛔ **구매처별 목표비율을 다루지 않습니다.** 축은 **연도 × 정책** 둘뿐입니다
    (``DECISIONS.md`` §0.20). 사업자등록번호로 목표비율을 나누는 경로가 이
    모듈에 없습니다.

.. note::
    목표비율 값 검증은 기존 :func:`~procurement.database.policy_repository.validate_target_rate`
    를 **재사용**합니다. 규칙이 두 벌이 되면 한쪽으로 우회해 잘못된 값이
    들어갑니다.

.. note::
    이 서비스는 계산기·대시보드에 의존하지 않습니다. 설정 경로와 계산 경로를
    분리해 두면, 목표비율을 바꾸는 일이 계산 코드를 건드리지 않습니다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from procurement.admin.policy_admin import PolicyNotFoundError
from procurement.admin.policy_target_response import (
    PolicyTargetItemModel,
    PolicyTargetListResponseModel,
    ScopedTargetModel,
    target_rate_status,
)
from procurement.core.target_scope import TOTAL, is_calculable, scope_label
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.database.policy_target_repository import (
    PolicyTargetRepository,
    validate_year,
)
from procurement.models.policy_target import PolicyTarget


class PolicyTargetAdminService:
    """연도별 목표비율 조회·저장 유스케이스를 제공합니다."""

    def __init__(
        self,
        policy_repository: PolicyRepository,
        policy_target_repository: PolicyTargetRepository,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            policy_repository: 정책 목록·코드 조회에 사용합니다.
            policy_target_repository: 목표비율 저장·조회에 사용합니다.
        """
        self._policies = policy_repository
        self._targets = policy_target_repository

    def list_targets(self, year: int) -> PolicyTargetListResponseModel:
        """한 연도의 정책별 목표비율을 조회합니다.

        **활성 정책 전체**를 반환합니다. 목표비율이 없는 정책도 빼지 않고
        ``NOT_SET`` 으로 담습니다 — 화면이 입력칸을 그리려면 정책 목록 자체가
        필요하고, "정책이 없다" 와 "목표비율이 아직 없다" 는 다른 상태이기
        때문입니다.

        ⛔ 다른 연도의 값을 끌어와 채우지 않습니다.

        Args:
            year: 대상 회계연도.

        Returns:
            :class:`PolicyTargetListResponseModel`.

        Raises:
            PolicyValidationError: 연도가 허용 범위를 벗어난 경우.
        """
        validate_year(year)

        # ⚠️ 한 정책에 목표가 **여럿**일 수 있습니다(여성기업: 공사·용역·물품).
        #    그래서 정책별로 모아 둡니다 — ⛔ 그중 하나만 남기면 나머지가 없는
        #    것처럼 보입니다(STEP 99 §2 금지사항).
        by_policy: dict[int, list[PolicyTarget]] = {}
        for target in self._targets.list_by_year(year):
            by_policy.setdefault(target.policy_id, []).append(target)

        items: list[PolicyTargetItemModel] = []
        for policy in self._policies.find_active():
            if policy.policy_id is None:  # pragma: no cover - 저장된 정책은 ID 가 있다
                continue
            saved = by_policy.get(policy.policy_id, [])
            # ``target_rate`` 는 기존 화면·API 가 읽던 값 그대로 — 기관 전체
            # 구매금액 기준 목표입니다. 분모가 다른 목표는 여기 담지 않습니다.
            total_target = next((t for t in saved if t.scope == TOTAL), None)
            rate = total_target.target_rate if total_target is not None else None
            items.append(
                PolicyTargetItemModel(
                    year=year,
                    policy_id=policy.policy_id,
                    policy_code=policy.policy_code,
                    policy_name=policy.policy_name,
                    is_active=policy.is_active,
                    target_rate=rate,
                    target_rate_status=target_rate_status(rate),
                    updated_at=total_target.updated_at if total_target is not None else None,
                    scoped_targets=tuple(
                        ScopedTargetModel(
                            scope=target.scope,
                            scope_label=scope_label(target.scope),
                            target_rate=target.target_rate,
                            calculable=is_calculable(target.scope),
                        )
                        for target in saved
                    ),
                )
            )
        return PolicyTargetListResponseModel(year=year, items=items)

    def set_target(
        self, year: int, policy_code: str, target_rate: str | None
    ) -> PolicyTargetItemModel:
        """한 연도 · 한 정책의 목표비율을 저장하거나 해제합니다.

        같은 ``(연도, 정책)`` 으로 몇 번을 호출해도 결과가 같습니다(멱등).

        Args:
            year: 대상 회계연도.
            policy_code: 대상 정책 코드.
            target_rate: 새 목표비율 문자열(예: ``"37.5"``). ``None`` 이면
                **해제**합니다 — 행을 지워 "미설정" 으로 되돌립니다.
                ⛔ 0 을 넣지 않습니다.

        Returns:
            저장 결과 :class:`PolicyTargetItemModel`.

        Raises:
            PolicyNotFoundError: 해당 정책 코드가 없는 경우.
            PolicyValidationError: 연도·목표비율이 허용 범위를 벗어났거나,
                **비활성 정책**의 목표비율을 바꾸려는 경우.
        """
        validate_year(year)

        policy = self._policies.find_by_policy_code(policy_code)
        if policy is None or policy.policy_id is None:
            raise PolicyNotFoundError(f"존재하지 않는 정책 코드입니다: {policy_code}")
        # 기존 목표율 API 와 같은 규칙 — 계산 대상이 아닌 정책에 목표를 두지 않는다.
        if not policy.is_active:
            raise PolicyValidationError(
                f"비활성 정책의 목표비율은 설정할 수 없습니다: {policy_code}"
            )

        parsed = self._parse_target_rate(target_rate)
        if parsed is None:
            # 해제 = 행 삭제. ⛔ 0 으로 저장하지 않는다 — 0% 는 "미설정" 이 아니다.
            self._targets.delete(year, policy.policy_id)
            saved_rate: Decimal | None = None
            updated_at = None
        else:
            saved = self._targets.upsert(year, policy.policy_id, parsed)
            saved_rate = saved.target_rate
            updated_at = saved.updated_at

        return PolicyTargetItemModel(
            year=year,
            policy_id=policy.policy_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            is_active=policy.is_active,
            target_rate=saved_rate,
            target_rate_status=target_rate_status(saved_rate),
            updated_at=updated_at,
            # 이 API 가 건드리는 것은 ``TOTAL`` 하나지만, 응답에는 그 정책에
            # 저장된 목표를 **모두** 담습니다 — 분모가 다른 목표를 이 API 로
            # 지웠다고 오해하지 않도록.
            scoped_targets=tuple(
                ScopedTargetModel(
                    scope=target.scope,
                    scope_label=scope_label(target.scope),
                    target_rate=target.target_rate,
                    calculable=is_calculable(target.scope),
                )
                for target in self._targets.list_for_policy(year, policy.policy_id)
            ),
        )

    @staticmethod
    def _parse_target_rate(value: str | None) -> Decimal | None:
        """목표비율 문자열을 ``Decimal`` 로 변환합니다 (``None`` 은 해제).

        Raises:
            PolicyValidationError: 숫자로 해석할 수 없는 문자열인 경우.
        """
        if value is None:
            return None
        try:
            parsed = Decimal(value.strip())
        except (InvalidOperation, ValueError) as exc:
            raise PolicyValidationError(f"목표비율은 숫자 형식이어야 합니다: {value!r}") from exc
        if not parsed.is_finite():
            raise PolicyValidationError(f"목표비율은 숫자 형식이어야 합니다: {value!r}")
        return parsed
