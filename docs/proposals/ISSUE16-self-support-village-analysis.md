# Issue #16 — 자활용사촌 정책 지원을 위한 분석 및 설계

## 문서 정보

| 항목 | 내용 |
|------|------|
| Version | v1.0 (제안/Draft) |
| Status | PM 검토 대기 |
| Last Updated | 2026-08-04 |
| 성격 | **분석·설계 제안 문서** — 코드/DB/설계문서 변경 없음 |
| 관련 | #11(분석), #12(업무규칙 정의 v1.1), #13(Purchase 날짜), #14(Policy evaluation_basis), #15(Calculator 판정) |
| 대상 정책 | 국가유공자 자활용사촌 생산품 (현재 MVP 범위 밖 / 향후 확장) |

> 본 문서는 Issue #16 의 산출물(분석 + 데이터 모델 영향 + 구현 방향 + Roadmap)이다.
> **이 문서는 어떤 코드도 DB도 설계문서도 변경하지 않는다.** PM 승인 후 별도 Issue 에서 구현한다.

---

# Part 1. 현재 시스템 구조 분석 — 자활용사촌 지원이 어려운 이유

## 1.1 현재 구조 요약 (분석 대상 4종)

| 구성요소 | 현재 판정/집계 방식 | 자활용사촌과의 간극 |
|----------|---------------------|---------------------|
| **Policy** | `evaluation_basis` = `PAYMENT_DATE` / `CONTRACT_DATE` 두 값만 (v1.1). `VENDOR_EXISTENCE` 는 **예약만** 되어 있고 미구현. #14 검증은 두 값 외 거부 | 자활용사촌 판정 유형(`VENDOR_EXISTENCE`) 자체가 저장·검증 불가 |
| **Certification** | `valid_from` / `valid_to` (둘 다 NOT NULL) **기간 기반** 인증 | 자활용사촌은 "기간"이 아니라 "**거래 유무**" 개념 → 유효기간 모델과 맞지 않음 |
| **Purchase** | `amount`(금액), `contract_date`, `payment_date` 만 보유. **품목(수량) 정보 없음** | "건바이건 **품목 갯수**" 집계 불가. 한 구매 = 단일 금액 1건이라 품목 단위 표현 불가 |
| **ProcurementAchievementCalculator** | `_sum_policy_purchase` 가 `evaluation_basis` 로 **날짜를 선택해 유효기간 내 금액만 합산**. 결과는 `AchievementResult`(금액 중심) | ① 날짜 판정 전제 → 거래유무 판정 분기 없음 ② 금액만 집계 → 품목 갯수 미집계 ③ `VENDOR_EXISTENCE` 미처리 |

## 1.2 자활용사촌이 지원되지 않는 4가지 근본 원인

**원인 1 — 판정 방식이 다르다 (날짜 vs 거래유무)**
현재 Calculator(#15)의 유일한 집계 규칙은 "판정 기준일이 인증 유효기간 내인가"이다. 자활용사촌은 기간을 보지 않고 **해당 업체와 거래(구매)가 존재하는가**만 본다. 현재 코드에는 이 분기가 없다.

**원인 2 — 품목(수량) 데이터가 없다**
자활용사촌은 금액뿐 아니라 **구매 품목 갯수**를 함께 집계해야 한다. `Purchase` 에는 수량/품목 컬럼이 없고, 계산 결과 모델 `AchievementResult` 에도 갯수 필드가 없다.

**원인 3 — "건바이건 품목"을 표현할 구조가 없다**
현재 `Purchase` 는 1건 = 1금액(단일 amount) 구조다. 한 구매건에 여러 품목이 있는 경우(건바이건 품목 갯수)를 표현하려면 구매-품목 1:N 구조 또는 최소한 품목 수 컬럼이 필요하다.

**원인 4 — Policy 판정유형 확장 지점이 막혀 있다**
`evaluation_basis` 는 v1.1 에서 `VENDOR_EXISTENCE` 를 "향후 예약"으로만 두었고, #14 Repository 검증이 `PAYMENT_DATE`/`CONTRACT_DATE` 외 값을 **거부**한다. 즉 자활용사촌 정책은 현재 **저장조차 불가**하다.

---

# Part 2. 자활용사촌 업무 기준 분석 — 필요한 데이터

## 2.1 알려진 업무 규칙 (PM 구두 전달 요약)

> 자활용사촌은 기간보다 **업체 유무 개념**이다. 지출일자(날짜)로 판정하지 않고, **거래를 했는지 여부**로 본다. **건바이건으로 구매 품목 갯수와 금액을 합산**한다.

## 2.2 판정에 필요한 데이터 (도출)

| 판정 요소 | 필요 데이터 | 현재 보유 | 비고 |
|-----------|-------------|-----------|------|
| **대상 업체 식별** | 해당 구매업체가 자활용사촌 대상인지 | Company(사업자번호)·Certification(연결)로 부분 가능 | 자활용사촌 "지정" 을 어떻게 표현할지 확정 필요 |
| **거래 유무** | 그 업체와 실제 구매(Purchase)가 있었는가 | Purchase.company_id 매칭으로 가능 | 기간 판정 불필요 |
| **금액 합산** | 구매금액 | `Purchase.amount` ✅ | 기존 재사용 |
| **품목 갯수 합산** | 구매 품목 수 | ❌ **없음** | 신규 데이터 필요 |

## 2.3 확정이 필요한 업무 정의 (모호점)

| # | 모호점 | 왜 중요한가 |
|---|--------|-------------|
| Q1 | "**품목 갯수**"의 정의: (a) 구매 건수, (b) 품목 종류 수(SKU), (c) 수량 합계 중 무엇인가? | 데이터 모델(단일 컬럼 vs 품목 테이블)이 갈린다 |
| Q2 | "**거래 유무**"의 판정 단위: 업체 단위(그 업체와 1건이라도 거래하면 인정) vs 구매 건 단위 | 집계·달성률 정의가 달라진다 |
| Q3 | 자활용사촌 "**지정**" 표현: 기존 Certification 재사용(기간 무시) vs 별도 표현 | Certification 의 valid_from/valid_to NOT NULL 과 충돌 여부 |
| Q4 | 달성률 산식: 자활용사촌도 "금액 비율" 기반인가, 아니면 갯수 기반 별도 지표인가 | `AchievementResult` 확장 형태 결정 |
| Q5 | MVP 범위 편입 여부 (현재 `REQUIREMENTS.md`·`POLICY_DEFINITION.md` 상 향후 확장) | 범위 문서 개정 선행 필요 |

---

# Part 3. 데이터 모델 영향 분석

## 3.1 Purchase 확장 필요 여부 — **필요**

품목 갯수 집계 때문에 확장이 불가피하다. 두 가지 표현 방식:

| 방식 | 내용 | 적합 조건 |
|------|------|-----------|
| **P-1** `Purchase.item_count` 컬럼 추가 | 구매 1건의 품목 수를 정수로 보관 | Q1 답이 "구매건별 품목 수 합계"면 충분 (최소 변경) |
| **P-2** `PurchaseItem` 별도 테이블 | 구매 1건에 품목 N개 (purchase_id, item_name, quantity, amount …) | 품목별 상세/제품 기준 판정(녹색제품)까지 필요하면 정확 |

> DATABASE_DESIGN v1.1 은 이미 "`item_count` 는 자활용사촌 등 향후 정책에서 사용 → MVP 제외" 로 **예고**해 두었다. P-1 은 그 연장선.

## 3.2 Certification 확장 필요 여부 — **검토 필요 (충돌 있음)**

- 현재 Certification 은 `valid_from` / `valid_to` 가 **둘 다 NOT NULL** 이다.
- 자활용사촌은 기간 개념이 아니므로 유효기간을 강제하면 의미가 없거나 형식상 채워야 한다.
- 선택지:
  - **C-1** 자활용사촌도 Certification 으로 표현하되, Calculator 가 유효기간을 **무시**(VENDOR_EXISTENCE 분기에서 기간 미검사). 스키마 변경 없음. 가장 간단.
  - **C-2** Certification 의 valid_from/valid_to 를 nullable 로 완화. 스키마 변경 + 기존 정책 영향 검토 필요.
  - **C-3** 자활용사촌 지정을 별도 개념(예: VendorDesignation)으로 분리. 변경 범위 큼.
- **권고: C-1** (스키마 무변경, Calculator 분기에서 기간 판정만 건너뜀).

## 3.3 Policy.evaluation_basis 확장 필요 여부 — **필요**

- `evaluation_basis` 허용값에 **`VENDOR_EXISTENCE`** 를 추가해야 한다(현재 #14 검증이 거부).
- DATABASE_DESIGN v1.1·POLICY_DEFINITION v1.1 에는 이미 "향후 VENDOR_EXISTENCE" 로 예약되어 있어 방향은 일치. 실제 허용값 목록·검증·문서 개정이 필요.

## 3.4 AchievementResult 확장 필요 여부 — **필요 (조건부)**

- 갯수 지표를 결과로 노출하려면 `item_count`(또는 `matched_item_count`) 필드 추가가 필요.
- Q4(갯수 기반 지표 여부)에 따라 형태가 달라진다.

## 3.5 영향 요약표

| 대상 | P-1(최소) | P-2(확장) |
|------|-----------|-----------|
| Purchase(Model/Repo/스키마/테스트) | `item_count` 컬럼 1개 추가 | 변경 없음(또는 소폭) + **PurchaseItem 신설** |
| Certification | 무변경 (C-1) | 무변경 (C-1) |
| Policy(evaluation_basis 값) | `VENDOR_EXISTENCE` 추가 | 동일 |
| Calculator | `VENDOR_EXISTENCE` 분기(기간무시·금액+갯수 합산) | 동일 + 품목 테이블 집계 |
| AchievementResult | `item_count` 추가 | 동일 |
| 설계문서(DATABASE_DESIGN/POLICY_DEFINITION/REQUIREMENTS) | 개정 | 개정(테이블 1개 추가) |

---

# Part 4. 구현 방향 제안 — 최소 변경안 vs 확장안

## 안 A — 기존 구조 최소 확장 (P-1 기반)

- `Purchase.item_count`(INTEGER) 추가 + `Policy.evaluation_basis` 에 `VENDOR_EXISTENCE` 추가.
- Calculator 에 `VENDOR_EXISTENCE` 분기: 유효기간 무시, 해당 업체 구매의 **금액 합계 + item_count 합계** 집계.
- `AchievementResult.item_count` 추가.
- **장점**: 변경 범위 작음, 빠름, 기존 계층 구조 유지.
- **단점**: 품목 "개별 정보"(품명·품목별 금액)는 표현 불가. 녹색제품 제품기준(#별도 트랙)과 재사용성 낮음.

## 안 B — PurchaseItem 별도 테이블 신설 (P-2 기반)

- `PurchaseItem(purchase_item_id, purchase_id, item_name, quantity, amount …)` 신설(구매 1:N 품목).
- Calculator 가 품목 단위로 갯수/금액 집계.
- **장점**: 품목별 상세 표현 가능, **녹색제품 제품기준 판정과 데이터 모델 공유** 가능, 확장성 큼.
- **단점**: 변경 범위 큼(모델·Repo·스키마·매핑·테스트·Excel 업로드 파서까지 파급), 위험도 높음, 개발 기간 김.

## 비교표

| 기준 | 안 A (최소) | 안 B (확장) |
|------|-------------|-------------|
| 변경 범위 | 작음 | 큼 |
| 구현 위험도 | 낮음 | 중~높음 |
| 품목 갯수 집계 | ✅ (합계값) | ✅ (품목 단위) |
| 품목 개별 정보 | ❌ | ✅ |
| 녹색제품 제품기준 재사용 | ❌ | ✅ |
| 개발 속도 | 빠름 | 느림 |

## 권고

**단계적 접근**: 요구가 "품목 **갯수** 합계"에 한정되면 **안 A** 로 시작(빠르고 저위험). 이후 품목별 상세·녹색제품 제품기준까지 필요해지면 **안 B(PurchaseItem)** 로 승격. 단, Q1(품목 갯수 정의)·Q4(지표 형태)를 **먼저 확정**해야 안 A/B 를 결정할 수 있다.

---

# Part 5. 향후 구현 Issue 분리 제안 (Roadmap)

| 제안 Issue | 제목 | 유형 | 산출물 |
|-----------|------|------|--------|
| **#17** | 자활용사촌 업무기준·범위 확정 | 문서 정의 | Q1~Q5 확정, MVP 편입 여부, `REQUIREMENTS.md`·`POLICY_DEFINITION.md`·`DATABASE_DESIGN.md` 개정안 |
| **#18** | Policy `evaluation_basis` 에 `VENDOR_EXISTENCE` 추가 | 구현 | Policy Model/Repo 검증 확장 + 테스트 |
| **#19** | Purchase 품목 데이터 구현 | 구현 | (안 A) `Purchase.item_count` 또는 (안 B) `PurchaseItem` 모델·Repo·스키마·테스트 |
| **#20** | `AchievementResult` 갯수 지표 확장 | 구현 | `item_count`(또는 갯수 지표) 필드 추가 |
| **#21** | Calculator `VENDOR_EXISTENCE` 분기 구현 | 구현 | 거래유무 기반 금액+갯수 집계, 기간 미판정 + 테스트 |
| **(연계)** | 녹색제품 제품기준 트랙 | 별도 | 안 B 채택 시 PurchaseItem 재사용 |

**권고 순서**: #17(확정) → #18 → #19 → #20 → #21. #17 에서 안 A/B 확정 후 #19 범위가 정해진다.

---

# 미결 질문 (PM 확정 요청)

| # | 질문 | 기본 권고 |
|---|------|-----------|
| Q1 | "품목 갯수" 정의 (구매건수/품목종류수/수량합계) | 확정 필요 → 안 A vs B 결정 |
| Q2 | "거래 유무" 판정 단위 (업체 단위 / 건 단위) | 업체 단위 (거래 1건 이상이면 인정) |
| Q3 | 자활용사촌 지정 표현 (Certification 재사용 / 별도) | C-1 (Certification 재사용, 기간 무시) |
| Q4 | 달성률 지표 형태 (금액 비율 / 갯수 지표 병행) | 확정 필요 → AchievementResult 확장 형태 |
| Q5 | MVP 편입 여부 | 향후 확장 유지 (범위 문서 개정 선행 시 편입) |
| Q6 | 안 A(item_count) vs 안 B(PurchaseItem) | Q1·Q4 확정 후 결정. 요구가 갯수 합계면 안 A 권고 |

---

# Acceptance Criteria 대비

- ✅ 코드 변경 없음
- ✅ DB 변경 없음
- ✅ 분석 문서 작성 (Part 1~5)
- ✅ 향후 구현 Roadmap 제안 (#17~#21)

# 요약

- 자활용사촌 미지원 원인: (1) 날짜가 아닌 **거래유무** 판정, (2) **품목 갯수** 데이터 부재, (3) **건바이건 품목** 표현 구조 부재, (4) `VENDOR_EXISTENCE` 미구현.
- 핵심 결정: **품목 갯수 정의(Q1)** 와 **지표 형태(Q4)** → 이에 따라 **안 A(item_count) / 안 B(PurchaseItem)** 가 갈린다.
- Certification 은 C-1(재사용·기간 무시)로 스키마 변경을 피할 수 있다.
- 자활용사촌은 문서상 MVP 밖 → 편입 시 범위 문서 개정을 #17 에서 선행한다.
- 본 Issue(#16) 에서는 **어떤 코드·DB·설계문서도 변경하지 않았다.**
