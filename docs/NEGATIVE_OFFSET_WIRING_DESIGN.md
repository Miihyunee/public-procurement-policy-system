# D단계 사전 설계 — 음수 거래 저장 및 상계 계산 연결

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-20 |
| 상태 | 📄 **설계만.** 코드·DB 스키마 변경 없음 |
| 근거 | `DECISIONS.md` §0.6.3.5(상계 업무규칙) · §0.6.3.3(A~D 단계) · D-003 · C-2 |
| 범위 | D단계 구현계획 · 영향범위 · 테스트계획 |
| ⛔ 제외 | 담당자 확인 84건의 처리 **구현** · 최종 8개 정책 목록 변경 · 고객 확인 2건 |

> 이 문서는 **착수 전 설계**입니다. PM 승인 후 구현하며, 승인 전에는 어떤
> 코드·스키마도 바꾸지 않습니다.

---

# 1. 현재 데이터 흐름 (코드 실측)

## 1.1 전체 경로

```text
표준 Excel (9컬럼)
   ↓ uploads/excel_adapter.py          파일 → 머리글 + 행
   ↓ uploads/validation.py             행 검증 · 값 정규화
   ↓ uploads/mapping.py                검증 키 → 적재 키 (그대로 전달)
   ↓ importers/batch_import_service.py 배치 생성 · 이전 배치 SUPERSEDED
   ↓ importers/purchase_importer.py    사업자번호 정규화 · Company 매칭
   ↓ database/purchase_repository.py   ⛔ _validate() 에서 amount <= 0 거부
   ↓ SQLite  purchase 테이블
   ↓ database/purchase_repository.find_for_calculation(period)
   ↓ calculators/procurement_achievement.py   분모·분자 집계
   ↓ dashboard/data_service.py         목표율 결합 · 상태 판정
   ↓ app.py  GET /dashboard/summary
   ↓ web/static/index.html             화면
```

## 1.2 음수가 실제로 막히는 지점

| # | 지점 | 현재 동작 |
|---|---|---|
| 1 | `uploads/validation.py:308` | `amount <= 0` → **경고(warning)만.** 오류로 단정하지 않아 행은 검증을 통과한다 |
| 2 | `importers/purchase_importer.py` | Repository 저장을 시도하고, 예외를 잡아 그 행만 **FAILED** 로 기록 |
| 3 | **`database/purchase_repository.py:374`** | 🔴 **`if purchase.amount <= 0: raise PurchaseValidationError`** — 여기서 실제로 막힌다 |

```python
# purchase_repository.py  _validate()
if purchase.amount <= 0:
    raise PurchaseValidationError(f"구매금액은 0 보다 커야 합니다: amount={purchase.amount}")
```

**결론: 음수는 DB 에 한 건도 들어올 수 없다.** 따라서 상계 로직을 붙여도 대상
데이터가 존재하지 않는다(D-003 · C-2).

## 1.3 계산이 데이터를 읽는 지점 — **단 하나**

`ProcurementAchievementCalculator` 는 분모와 분자를 **같은 메서드**로 읽는다.

| 용도 | 호출 |
|---|---|
| 분모 (전체 구매금액) | `calculate_total_purchase()` → `find_for_calculation(period)` |
| 분자 (정책별 구매금액) | `_sum_policy_purchase()` → `find_for_calculation(period)` |

```python
# calculate_total_purchase
for purchase in self._purchase_repository.find_for_calculation(period):
    total += purchase.amount

# _sum_policy_purchase
for purchase in self._purchase_repository.find_for_calculation(period):
    ...  # rule.applies(...) 인 것만 합산
```

> ✅ **상계를 붙일 이음매(seam)가 하나뿐이다.** `find_for_calculation()` 이
> 돌려주는 목록만 상계 후 목록으로 바꾸면 분모·분자에 **동시에·일관되게**
> 적용된다. 두 곳을 따로 고치면 분모와 분자의 기준이 어긋날 수 있다.

## 1.4 `find_for_calculation()` 이 이미 하는 일

```sql
SELECT * FROM purchase
WHERE (batch_id IS NULL OR batch_id IN (SELECT batch_id FROM import_batch WHERE status='ACTIVE'))
  AND {date_field} BETWEEN ? AND ?
ORDER BY purchase_id
```

- 대체된 배치(SUPERSEDED)의 행을 제외한다
- 기간 조건을 적용한다(`PeriodFilter`)
- ⚠️ **기간 필터가 상계보다 먼저 적용된다** → 2.4 참조

## 1.5 상계 로직의 현재 위치

`core/offsetting.py` 는 **어디에도 연결되어 있지 않다.** 다음 테스트가 이를
고정하고 있다.

```python
# tests/test_offsetting.py :: TestNotWiredIntoCalculation
for relative in ("uploads", "services", "database", "importers"):
    for path in (root / relative).rglob("*.py"):
        assert "offsetting" not in path.read_text(encoding="utf-8")
```

---

# 2. D단계 변경 계획

## 2.1 변경 대상 요약

| # | 파일 | 대상 | 변경 이유 |
|---|---|---|---|
| **D-1** | `database/purchase_repository.py` | `_validate()` | 음수 저장 금지 해제. 이것을 풀지 않으면 상계 대상이 0건 |
| **D-2** | `uploads/validation.py` | `_parse_amount()` | 0 이하 경고 문구가 "저장하지 않습니다" 라고 안내 — 사실과 달라짐 |
| **D-3** | `calculators/procurement_achievement.py` | `calculate_total_purchase()` · `_sum_policy_purchase()` | 상계 후 목록을 쓰도록 **한 이음매**로 교체 |
| **D-4** | `app.py` | `build_dashboard_api()` | 상계 적용 여부를 조립 지점에서 주입 |
| **D-5** | `dashboard/status_service.py` | 집계 | 상계·확인 대상 건수를 운영 지표로 노출(선택) |
| **D-6** | `tests/test_offsetting.py` | `TestNotWiredIntoCalculation` | 연결되면 이 테스트가 깨진다. 통합 테스트로 **대체** |

⛔ **변경하지 않는 것**: `core/offsetting.py`(상계 규칙) · `models/purchase.py` ·
DB 스키마 · 표준 업로드 양식 · `web/static/index.html`.

## 2.2 D-1 — 저장 제약 해제

```python
# 현재
if purchase.amount <= 0:
    raise PurchaseValidationError(...)
```

| 항목 | 내용 |
|---|---|
| 변경 | `amount < 0` 을 허용. `amount is None` 검증은 유지 |
| 🟡 결정 대기 | **금액 0 을 허용할 것인가** → 3.1 (결정 ①) |
| 영향 | `Purchase` 모델 docstring("0 보다 커야 합니다")도 함께 수정 |
| 위험 | 이 제약을 믿고 있는 곳이 있는지 확인 필요 — 실측 결과 계산기·대시보드는 부호를 가정하지 않는다. `batch_import_service` 의 `total_amount` 합계는 음수를 포함해 **순액**이 된다(의도된 동작) |

## 2.3 D-2 — 업로드 경고 문구

```python
# validation.py 현재 문구
"0 이하 금액입니다: {amount}. 현재 시스템은 0 이하 금액을 저장하지 않습니다(처리 방식 확정 대기)."
```

저장하게 되면 이 안내가 거짓이 된다. 🟡 **결정 대기** — 경고를 남길지, 없앨지
→ 3.1 (결정 ②).

## 2.4 D-3 — 계산 연결 (핵심)

**제안 구조** — 계산기가 Repository 를 직접 읽는 대신, 주입된 **조회 함수**를
거치게 한다.

```python
# calculators/procurement_achievement.py (제안)
def __init__(self, purchase_repository, certification_repository, policy_repository,
             *, offsetting_enabled: bool = False) -> None:
    ...

def _purchases_for_calculation(self, period):
    """분모·분자가 **같은 목록**을 보게 하는 단일 지점."""
    rows = self._purchase_repository.find_for_calculation(period)
    if not self._offsetting_enabled:
        return rows
    return offset_negative_purchases(rows).remaining
```

`calculate_total_purchase()` 와 `_sum_policy_purchase()` 가 이 메서드를 쓰도록
바꾼다. **두 줄 교체**로 끝나며, 분모·분자 불일치가 구조적으로 불가능해진다.

⚠️ **기간 필터와의 순서 문제 — 반드시 확인해야 한다.**

```text
현재 순서:  기간 필터 → (상계)
```

상계 짝이 **연도를 넘나들면**(예: 2026-12 양수 ↔ 2027-01 음수) 기간 필터가
먼저 잘라내어 짝을 찾지 못한다.

| 실측 (2026년 데이터) | 결과 |
|---|---|
| 자동 상계 42쌍 중 연도를 넘는 쌍 | **0건** (전 건 2026년 내) |
| 발행일자 차이 최대 | 상계 판정에 쓰지 않음 |

현재 데이터에서는 문제가 없다. 다만 **연말·연초 데이터에서 발생할 수 있는
구조적 위험**이므로 3.1 (결정 ④) 로 남긴다.

## 2.5 D-4 — 조립 지점

```python
# app.py  build_dashboard_api()
calculator = ProcurementAchievementCalculator(
    purchase_repo, certification_repo, policy_repo,
    offsetting_enabled=settings.offsetting_enabled,   # 🟡 결정 대기 (결정 ③)
)
```

기본값을 `False` 로 두면 **기존 동작이 그대로 유지**되고, 설정으로만 켤 수
있다. 되돌리기가 쉬워 배포 위험이 낮다.

## 2.6 D-5 — 운영 지표 (선택)

`GET /data-status` 에 다음을 더할 수 있다. 계산 결과를 바꾸지 않는 **관측용**이다.

| 지표 | 의미 |
|---|---|
| `negative_purchase_count` | 적재된 음수 거래 수 |
| `offset_pair_count` | 자동 상계된 쌍 수 |
| `needs_manual_review_count` | 담당자 확인 대상 수 |
| `unmatched_negative_count` | 짝 없는 음수 수 |

---

# 3. 🟡 결정 대기 항목

> 고객 답변 또는 PM 결정에 따라 갈리는 지점입니다. **임의로 정하지 않습니다.**

## 3.1 결정 목록

| # | 결정 사항 | 갈리는 결과 | 코드 지점 |
|---|---|---|---|
| **①** | 금액 **0** 을 저장할 것인가 | 0원 거래의 저장 가부. 상계 대상은 아니다(`offsetting` 이 0원을 건너뜀) | `purchase_repository.py::_validate` |
| **②** | 업로드 시 음수 **경고를 남길 것인가** | 화면 경고 표시 유무. 저장 여부에는 영향 없음 | `validation.py::_parse_amount` |
| **③** | 상계 적용을 **설정으로 켤 것인가, 항상 켤 것인가** | 롤백 난이도. 설정이면 기본 off 로 안전 | `app.py::build_dashboard_api` |
| **④** | **기간 필터와 상계의 순서** | 연말·연초를 넘는 상계 짝의 처리 | `procurement_achievement.py::_purchases_for_calculation` |
| **⑤** | **담당자 확인 84건의 처리** | 🔴 **달성률 숫자가 바뀐다** → 3.2 |
| **⑥** | 담당자 최종 선택의 **기록 방식** | 화면·저장 구조 (`CUSTOMER_DATA_QUESTIONS.md` Q4-6, 보류 중) | 미정 |

## 3.2 🔴 결정 ⑤ — 담당자 확인 84건 (**구현하지 않음. 지점만 표시**)

### 결정이 들어갈 코드 지점 — 정확히 한 곳

```python
def _purchases_for_calculation(self, period):
    rows = self._purchase_repository.find_for_calculation(period)
    if not self._offsetting_enabled:
        return rows
    result = offset_negative_purchases(rows)

    # ┌──────────────────────────────────────────────────────────┐
    # │ 🟡 결정 ⑤ 가 들어갈 자리 — 오늘은 아무것도 넣지 않는다.   │
    # │                                                          │
    # │ result.needs_manual_review (84건) 를                     │
    # │   (가) 그대로 remaining 에 남긴다  ← 현재 offsetting 동작 │
    # │   (나) 음수만 계산에서 제외한다                          │
    # │   (다) 후보 양수까지 함께 제외한다                        │
    # └──────────────────────────────────────────────────────────┘
    return result.remaining
```

`offset_negative_purchases()` 는 이미 **(가)** 로 동작합니다 — 확인 대상은
`remaining` 에 그대로 남습니다. 따라서 **아무 코드도 추가하지 않으면 (가) 가
적용**되며, 다른 선택을 하려면 이 지점에서만 걸러내면 됩니다.
⛔ `core/offsetting.py` 는 건드리지 않습니다.

### 선택지별 실측 영향 (2026년 2,292행)

| 처리 | 행 | 분모 | 중소기업 달성률 | 장애인기업 달성률 |
|---|---|---|---|---|
| **(가) 포함** — 음수·양수 모두 남김 | 2,208 | 8,808,740,570 | 126.84% | 46.00% |
| **(나) 음수만 제외** | 2,124 | **9,658,159,113** | **129.58%** | **44.27%** |
| (다) 후보 양수까지 제외 | — | 후보 선택에 좌우됨 | 미산출 | 미산출 |

담당자 확인 84건의 음수 합계 = **−849,418,543원**. (나) 를 고르면 이 금액만큼
**분모가 늘어난다.**

> 🟡 **분석자 의견(결정 아님)**: (가) 를 권장합니다. 확인 대상은 "어느 양수와
> 짝지을지 모르는 것" 이지 "실적이 아닌 것" 이 아닙니다. 음수만 빼면 취소된
> 거래가 실적으로 남습니다. 또한 (가) 는 A/B/C 시나리오가 모두 같은 값이라
> **확정 시점이 언제든 숫자가 흔들리지 않습니다.**

---

# 4. 영향 범위

## 4.1 숫자가 바뀌는 것 — **D-1 뿐**

| 변경 | 달성률 영향 |
|---|---|
| **D-1 음수 저장 허용** | 🔴 **바뀐다.** 분모 10,362,615,496 → 8,808,740,570 (**−15.5억 · −15%**) |
| D-3 상계 연결 | ✅ **바뀌지 않는다.** 상계는 `+X` 와 `−X` 를 함께 제거하므로 합계 불변 |

정책별 (2026년 실데이터 · 목표율은 파일 기준):

| 정책 | 목표율 | 현재(음수 제외) | D-1 이후(음수 포함) | 차이 |
|---|---|---|---|---|
| 중소기업 | 50% | 131.12% | 126.84% | **−4.28%p** |
| 창업기업 | 3.4% | 142.35% | 142.06% | −0.29%p |
| 여성기업 | 이원화 | 구매비율 14.78% | 14.11% | −0.67%p |
| 장애인기업 | 1% | 43.00% | 46.00% | **+3.00%p** |

> 상계 연결은 숫자를 바꾸지 않지만, **남는 거래의 구성**을 바꿉니다
> (2,292 → 2,208행). 향후 구매유형 분류를 붙이면 여기서 차이가 납니다.

## 4.2 기존 테스트에 미치는 영향

| 테스트 | 영향 | 조치 |
|---|---|---|
| `test_offsetting.py::TestNotWiredIntoCalculation` (3건) | 🔴 **반드시 깨진다** — 연결을 금지하는 테스트 | 통합 테스트로 **대체**. 삭제 사유를 docstring 에 기록 |
| `test_purchase_repository.py` 음수 거부 테스트 | 🔴 깨진다 | 기대값 변경 + 사유 기록 |
| `test_upload_e2e.py` 음수 행 FAILED 기대 | 🔴 깨질 가능성 | 확인 후 기대값 변경 |
| 그 밖 1,131건 | ✅ 영향 없음 — `offsetting_enabled=False` 기본값이면 동작 불변 | 그대로 유지 |

## 4.3 최종 8개 정책 문제와의 접점 — **조사 결과: 접점 없음**

지시 5번에 따라 현재 정책 구조에서 영향받는 부분만 조사했습니다.

| 항목 | D단계와의 관계 |
|---|---|
| 정책 판정 | `evaluation_basis` → Rule Engine. **정책 개수와 무관** |
| 분모 | 전체 구매금액 하나. 모든 정책이 **공유** |
| 분자 | 정책별 인증 유효기간 판정. 정책이 늘어도 같은 목록을 읽는다 |
| 상계 | 정책을 모른다. `Purchase` 목록만 다룬다 |

> ✅ **D단계는 정책 목록 확정과 독립적입니다.** 정책이 4개든 8개든 상계는
> `find_for_calculation()` 결과에만 작용하므로, 나중에 정책을 추가해도 D단계
> 코드는 바뀌지 않습니다.

⚠️ 단 **하나의 예외**가 있습니다. 미구현 정책 2종은 **분모가 다릅니다**.

| 정책 | 분모 |
|---|---|
| 온누리상품권 | 기관 경상경비 금액 |
| 국가유공자자활용사촌 | 자활용사촌 생산가능품목 총 구매액 |

현재 Calculator 는 분모가 하나라는 전제 위에 서 있고, D단계의
`_purchases_for_calculation()` 도 그 전제를 따릅니다. **이 2종을 넣으려면
분모 구조 자체를 바꿔야 하며, 그때 D단계 코드도 함께 손봐야 합니다.**
지금은 두 정책 모두 미구현이므로 **현 시점 영향은 없습니다.**

⛔ 정책 목록은 **임의로 변경하지 않았습니다.**

## 4.4 GREEN 과의 관계

`GREEN` 은 `is_active=False` 로 이미 계산에서 빠져 있습니다. D단계는 정책이
아니라 구매 목록을 다루므로 **서로 영향이 없습니다.**

---

# 5. 테스트 계획

## 5.1 D-1 저장 제약

| # | 케이스 | 기대 |
|---|---|---|
| 1 | 음수 금액 저장 | 성공. 조회 시 값이 그대로 |
| 2 | 금액 0 저장 | 🟡 결정 ① 에 따름 |
| 3 | `amount` 누락 | 여전히 오류(변경 없음) |
| 4 | 기존 양수 저장 경로 | 회귀 없음 |
| 5 | 업로드 E2E — 음수 행 포함 파일 | 저장되고 배치 합계가 **순액** |

## 5.2 D-3 계산 연결

| # | 케이스 | 기대 |
|---|---|---|
| 6 | `offsetting_enabled=False` | 기존과 **완전히 동일** (기본값 회귀) |
| 7 | `offsetting_enabled=True` · 1:1 짝 1쌍 | 분모·분자에서 **양쪽 모두** 빠짐 |
| 8 | 분모·분자 일관성 | 같은 목록을 본다 — 한쪽만 상계되지 않음 |
| 9 | 후보 2건 이상 | 상계되지 않고 계산에 남음 (**결정 ⑤ (가)**) |
| 10 | 짝 없는 음수 | 상계되지 않고 계산에 남음 |
| 11 | SUPERSEDED 배치 | 상계 전에 제외된 채로 동작 |
| 12 | 기간 필터 | 분모·분자에 동일 적용 (🟡 결정 ④ 확정 후 경계 케이스 추가) |

## 5.3 실데이터 회귀

| # | 케이스 | 기대 |
|---|---|---|
| 13 | 2026년 2,292행 적재 | 음수 129건 포함 전량 저장 |
| 14 | 상계 미적용 달성률 | 4.1 표의 "D-1 이후" 값과 일치 |
| 15 | 상계 적용 달성률 | **14 와 동일** (상계는 숫자를 바꾸지 않음) |
| 16 | 상계 판정 | 자동 42쌍 · 확인 84건 · 짝 없음 3건 |

> 15 번이 이 단계에서 가장 중요한 검증입니다. 값이 달라지면 분모·분자 중
> 한쪽에만 상계가 적용됐다는 뜻입니다.

## 5.4 금지 사항 고정

| # | 케이스 | 기대 |
|---|---|---|
| 17 | `core/offsetting.py` 판정 로직 | 자동 우선순위·G20 접근 부재 (기존 테스트 유지) |
| 18 | 화면 파일 | D단계에서 변경 없음 |
| 19 | DB 스키마 | 컬럼 추가 없음 |

---

# 6. 착수 조건

| # | 조건 | 상태 |
|---|---|---|
| 1 | PM 의 D단계 착수 승인 | 🔴 대기 |
| 2 | 결정 ① ~ ④ | 🔴 대기 (PM 결정 가능) |
| 3 | 결정 ⑤ — 담당자 확인 84건 | 🔴 대기 (달성률이 바뀌므로 고객 확인이 필요할 수 있음) |
| 4 | 결정 ⑥ — 최종 선택 기록 방식 | 🟡 보류 (Q4-6) |

> **결정 ⑤ 없이도 D-1 · D-3 은 착수할 수 있습니다.** `offset_negative_purchases()`
> 가 이미 (가) 로 동작하므로, 아무것도 추가하지 않으면 (가) 가 적용됩니다.
> 다른 선택을 하게 되면 3.2 의 한 지점만 고치면 됩니다.
