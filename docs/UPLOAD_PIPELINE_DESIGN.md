# 표준 Excel 업로드 — 남은 구현 목록과 결정 지점

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-15 |
| 목적 | PM 지시서 §32 (1·2·3순위) — **확인 · 목록화 · 결정 지점 분리** |
| 범위 | **설계 문서.** 이번에 코드를 구현하지 않았다 |
| 기준 | HEAD `54fcdbe` |

---

# 1. 이미 구현된 것 — 다시 만들지 않는다 (§32 1순위)

`b12fc85` · `58752aa` · `bf7c1ff` · `54fcdbe` 를 확인했다.

| 계층 | 파일 | 상태 |
|---|---|---|
| 표준 양식 컬럼 정의 | `uploads/format.py` | ✅ 확정 5개 · 미확정은 `PENDING_COLUMNS` |
| 행 단위 검증 | `uploads/validation.py` | ✅ 테스트 45건 |
| 사업자번호 정규화 | `matchers/business_no.py` | ✅ **재사용 중** (새로 만들지 않음) |
| 행 적재 | `importers/purchase_importer.py` | ✅ 매핑된 행을 받아 저장 |
| 배치 적재 | `importers/batch_import_service.py` | ✅ 월별 누적 · `ACTIVE`/`SUPERSEDED` |
| 창업기업 OR 판정 | `calculators/rules/date_rules.py` | ✅ `ResolutionOrContractDateRule` |
| 음수 상계 | `core/offsetting.py` | ✅ 로직만 (연결 안 됨) |
| 구매유형 매핑 | `core/purchase_type.py` | ✅ 확정 3건만 (연결 안 됨) |
| Electron 생명주기 | `electron/backend.js` | ✅ 검증 8/8 |

> **`PurchaseImporter` 가 이미 "매핑된 행"을 받는 구조**다. 표준 양식 → 내부 필드
> 변환만 앞에 붙이면 되고, 적재 로직을 새로 만들 필요가 없다.

---

# 2. 🔬 openpyxl 필요 여부 — 실측 결과 (§32 2순위)

지시서가 "필요 여부를 검토" 하라고 했으므로 추측하지 않고 실험했다.

## 2.1 실험 1 — 표준 라이브러리만으로 .xlsx 를 만들 수 있는가

`zipfile` + `ElementTree` 로 최소 OOXML 패키지를 직접 만들었다.
(저장소에 선례가 있다 — `collectors/smpp.py` 등이 `ElementTree` 로 XML 을 다룬다.)

| 항목 | 결과 |
|---|---|
| 생성 | ✅ 성공 (1,749 bytes, 파트 5개) |
| **openpyxl 로 다시 읽기** | ✅ **성공** — 시트명·전 셀 값 일치 |

→ **쓰기는 표준 라이브러리로 가능하다.**

## 2.2 실험 2 — 표준 라이브러리만으로 .xlsx 를 읽을 수 있는가

openpyxl 이 만든 파일을 소박한 stdlib 리더로 읽어 봤다.

| 항목 | 결과 |
|---|---|
| 파일 파트 수 | 9개 (`styles.xml` · `theme1.xml` · `docProps` 등 추가) |
| **값 읽기** | 🔴 **실패 — 전부 `None`** |

**원인**: 셀 값 표현 방식이 여러 가지다. 리더가 `<v>` 만 보고 있었는데 실제
파일은 `t="inlineStr"` + `<is><t>` 를 썼다.

```xml
<c r="A1" t="inlineStr"><is><t>결의일자</t></is></c>
```

실제 Excel 이 저장한 파일은 여기에 더해 다음을 쓴다.

| 변형 | 내용 |
|---|---|
| `sharedStrings.xml` | 문자열을 별도 테이블에 두고 `t="s"` + 인덱스 참조 |
| 날짜 | 문자열이 아니라 **1900 기준 일련번호** + `styles.xml` 의 서식 참조 |
| 빈 셀 | 아예 `<c>` 자체가 없음 → 열 위치가 밀림 |
| 병합·수식 | `<f>` · `mergeCells` |

## 2.3 판단

| 방향 | 쓰기(양식 다운로드) | 읽기(업로드) |
|---|---|---|
| 표준 라이브러리 | ✅ 가능 (약 120줄) | 🔴 **권장하지 않음** |
| openpyxl | ✅ 간단 | ✅ 변형 처리 검증됨 |

> ## 결론: **읽기 때문에 openpyxl 이 필요하다.**
>
> 쓰기만 보면 의존성 없이도 되지만, **사용자가 Excel 로 저장한 파일**을 읽는
> 쪽이 문제다. 날짜 일련번호·sharedStrings·빈 셀을 직접 처리하면 사실상
> openpyxl 을 다시 만드는 일이고, 잘못 읽으면 **틀린 금액·날짜가 조용히 저장된다.**

**폐쇄망 영향**: openpyxl 은 순수 Python 휠(`py2.py3-none-any`)이며 의존성은
`et_xmlfile` 하나뿐이다. 컴파일러·시스템 라이브러리가 필요 없어 오프라인 설치가
쉽다. (설치 자체는 승인 후 수행 — 이번에는 격리된 임시 환경에서 실험만 했고
**프로젝트 의존성과 `.venv` 는 건드리지 않았다.**)

> ⚠️ **LibreOffice 검증은 실패했다.** 이 환경의 `soffice` 가 stdlib 파일도
> openpyxl 파일도 모두 열지 못했다(`javaldx` 경고). **파일 문제가 아니라 도구
> 문제**로 보이며, 실제 Excel 호환성은 **미검증**이다. Windows 검증 시 함께 확인해야 한다.

---

# 3. 남은 구현 목록 (§32 2순위)

## 3.1 표준 양식 다운로드

| 항목 | 설계안 |
|---|---|
| 생성 위치 | Python backend (Electron 이 만들지 않는다) |
| 내용 | 시트1 = 머리글 + 예시 1행 / 시트2 = 작성 안내 |
| 근거 | `format.header_row()` · `example_row()` · `guide_lines()` **이미 있음** |
| 전달 | `GET /uploads/template` → `.xlsx` 파일 응답 |
| Electron | 저장 위치를 `dialog.showSaveDialog` 로 고르게 함 |

## 3.2 업로드 API

| 항목 | 설계안 |
|---|---|
| 엔드포인트 | `POST /uploads/purchases` (`multipart/form-data`) |
| 파일 전달 | Electron 이 파일 **경로**가 아니라 **내용**을 보낸다 (sandbox 유지) |
| 처리 | 엑셀 읽기 → `validate_headers` → `validate_rows` → (승인 후) Mapping → 저장 |
| 미리보기 | `?dry_run=true` 로 **검증만** 수행. 지시서 §44 "검증 후 저장" 대응 |

**검증 결과 JSON (안)**

```json
{
  "ok": false,
  "summary": { "total": 1250, "valid": 1230, "error_rows": 20, "warnings": 3 },
  "issues": [
    { "row": 12, "column": "사업자등록번호", "message": "값이 없습니다.", "severity": "error" }
  ]
}
```

`ValidationReport` 가 이미 이 정보를 전부 들고 있다. 응답 모델만 씌우면 된다.

**저장 결과 JSON (안)**

```json
{
  "batch_id": 7,
  "saved": 1230,
  "skipped": 0,
  "period": { "start": "2026-01-01", "end": "2026-12-31" }
}
```

> `BatchImportService.import_batch()` 가 이미 배치 단위 적재·대체를 처리한다.
> **대상 기간은 호출자가 지정**하므로 화면에서 받아야 한다(파일에서 유추 금지).

## 3.3 Electron preload API (안)

`sandbox: true` 를 유지하면서 최소만 노출한다.

| 노출 함수 | 역할 |
|---|---|
| `pickExcelFile()` | `dialog.showOpenDialog` — 경로만 main 이 알고, **내용은 main 이 읽어 백엔드로 전송** |
| `saveTemplate()` | `dialog.showSaveDialog` + 백엔드에서 받은 양식 저장 |

렌더러에는 **파일 시스템 접근을 노출하지 않는다.** 검증·파싱·저장은 전부 Python.

## 3.4 renderer 호출 방식

기존 화면이 `fetch("/dashboard/...")` 로 백엔드를 직접 부른다. 업로드도 같은
방식을 쓰되, **파일 선택만** preload 를 거친다.

```
[파일 선택] → preload.pickExcelFile() → main: dialog + 파일 읽기
           → main 이 백엔드로 POST → 결과를 렌더러에 반환 → 화면 표시
```

## 3.5 트랜잭션 (§44)

> 🔴 **현재 `BaseRepository` 의 트랜잭션 경계를 확인해야 한다.**
> 이전 보고에서 Technical Risk 로 기록해 둔 항목이며, "전부 검증 후 저장" 을
> 보장하려면 이 부분을 먼저 봐야 한다. 이번에는 손대지 않았다.

---

# 4. ✅ 결의일자 결정 — B안 채택 (2026-08-15 PM 최종 결정)

> `resolution_date` 필드를 신설한다. 기존 `payment_date` 를 결의일자로 재정의하지 않는다.
>
> Excel `결의일자` → `Purchase.resolution_date`
> Excel `계약일자` → `Purchase.contract_date`

## 4.1 반영 결과

```
표준 Excel "결의일자"
      │
      ▼
 validation.py  →  values["resolution_date"]      ← ✅ 구현됨
      │
      ▼
 ┌─────────────────────────────────────┐
 │  Mapping 계층 — 아직 만들지 않음     │  ← 남은 작업(§4.3)
 └─────────────────────────────────────┘
      │
      ▼
 PurchaseImporter.import_rows()  ← ✅ resolution_date 를 읽는다(2026-08-15 추가)
```

검증 계층이 만드는 키 5개가 **모두** 적재 계층 키와 이름까지 일치하게 되었다.
Mapping 계층은 새 변환기가 아니라 **얇은 연결자**면 된다.

## 4.2 실제로 바뀐 곳

| 파일 | 변경 |
|---|---|
| `models/purchase.py` | `resolution_date: date \| None = None` 추가 |
| `database/purchase_repository.py` | 컬럼 · INSERT · `_row_to_purchase` |
| `database/bootstrap.py` | `_ADDED_COLUMNS` · `_REQUIRED_SCHEMA` · STARTUP seed · 기준값 마이그레이션 |
| `core/period.py` | `ALLOWED_DATE_FIELDS` 에 `resolution_date` 추가 |
| `core/config/settings.py` | `PURCHASE_PERIOD_DATE_FIELD` 허용값 추가 |
| `calculators/rules/date_rules.py` | `PaymentOrContractDateRule` → **`ResolutionOrContractDateRule`** |
| `database/policy_repository.py` | 허용 기준값에 `RESOLUTION_OR_CONTRACT_DATE` 추가(구 값 유지) |
| `core/offsetting.py` | `date_of` 허용값 · 값 없는 행은 오류로 보고 |
| `importers/purchase_importer.py` | 행 키 `resolution_date`(선택) 추가 |

**창업기업 OR 규칙이 함께 바뀌었다** — 사전에 예고한 대로다. 고객 확정 문구가
"결의일자 OR 계약일자" 이므로, 결의일자가 별도 필드가 된 이상 규칙 대상도
`resolution_date` · `contract_date` 가 된다. `payment_date` 는 더 이상 보지 않는다.

> ⚠️ `resolution_date` 가 비어 있는 **기존 행**은 계약일자만으로 판정한다.
> 없는 값을 `payment_date` 로 대체하지 않는다(PM 금지 사항).

## 4.3 ✅ 해소 — 표준 양식에 `지급일` 을 추가한다 (2026-08-17 PM 결정)

표준 양식에 지급일 컬럼이 없어 적재 계층의 `payment_date` 를 채울 수 없던 문제는
**양식에 컬럼을 추가**하는 것으로 정리되었다(안 1).

```text
결의일자 | 계약일자 | 지급일 | 기업명 | 사업자등록번호 | 계
```

`payment_date` 를 nullable 로 바꾸지 않았다. 그 안(안 2)은 **정책 4종의 판정
기준일을 새로 정해야** 했기 때문이다 — 근거는 `DECISIONS.md` §0.8.1.

## 4.4 🔴 2026-08-17 실측으로 드러난 사실 2건 (결정 아님 — 보고용)

신규 PM 인수 후 `resolution_date` 가 "구매실적 기준일 = 결의일자" 규칙과 실제로
일관되게 연결되어 있는지 실측한 결과다. **어느 것도 임의로 고치지 않았다.**

### 4.4.1 결의일자가 없는 기존 행은 연도 조회에서 **조용히 빠진다**

`PURCHASE_PERIOD_DATE_FIELD=resolution_date` 로 두면, `resolution_date` 가
`NULL` 인 행은 SQL `BETWEEN` 조건에서 제외된다. 실측:

| `date_field` | 조회 결과 |
|---|---|
| `resolution_date` | **1건 / 1,000** (결의일자 있는 행만) |
| `payment_date` | 2건 / 3,000 |
| `contract_date` | 2건 / 3,000 |

빠진 행은 **분모·분자 양쪽에서 사라지므로 달성률 자체는 왜곡되지 않는다.**
문제는 **전체 구매액이 조용히 줄어드는데 화면에 아무 표시가 없다**는 점이다.
운영자가 "작년 대비 구매액이 왜 이렇게 적지?" 를 알아차릴 방법이 없다.

> 업무적으로는 "결의일자가 없으면 어느 연도인지 알 수 없다" 가 맞다. 다만
> **경고를 띄울지 여부**는 결정 사항이다.

### 4.4.2 일반 정책의 인증 판정 기준일은 **아직 `payment_date`** 다

두 가지 "기준일" 이 서로 다른 값을 쓰고 있다.

| 용도 | 현재 값 | 근거 |
|---|---|---|
| **연도 귀속** (`PeriodFilter.date_field`) | 운영자 지정 — `resolution_date` 선택 가능 | D-24 ✅ |
| **인증 유효기간 판정** (`evaluation_basis`) | 중소·여성·장애인·녹색 = **`PAYMENT_DATE`** | `POLICY_DEFINITION.md` (고객 확정 아님) |

즉 `resolution_date` 는 **연도 귀속에는 연결되었지만, 일반 정책의 인증 판정에는
연결되지 않았다.** 창업기업만 `RESOLUTION_OR_CONTRACT_DATE` 로 바뀌었다.

고객 확정 문구(`DECISIONS.md` §0.1 ①)는

> 지출데이터의 **결의일자**를 기준으로 **연도 귀속·실적을 산정한다**

인데, 여기서 "실적 산정" 이 **인증 유효기간 판정까지 포함하는지**가 문장만으로는
갈린다. 포함한다면 일반 정책 4종의 `evaluation_basis` 도 `RESOLUTION_DATE` 로
바뀌어야 하고, **이는 달성률 숫자를 바꾼다.**

⛔ **추정하지 않았다.** 고객 확인 또는 PM 결정 사항으로 남긴다.

---

# 4.5 ✅ 2026-08-16 구현 — Excel 어댑터 · 양식 다운로드 · 업로드 API

`openpyxl` 이 승인되어(지시서 §11) 읽기·쓰기 계층을 붙였다.

| 계층 | 파일 | 상태 |
|---|---|---|
| Excel 읽기 | `uploads/excel_adapter.py` | ✅ |
| 표준 양식 생성 | `uploads/template.py` | ✅ |
| 흐름 조립 | `uploads/upload_service.py` | ✅ (검증까지) |
| 응답 직렬화 | `uploads/upload_response.py` | ✅ |
| API | `GET /uploads/template` · `POST /uploads/purchases/validate` | ✅ |
| Electron 연결 | `electron/uploads.js` · preload · 대시보드 화면 | ✅ |
| **저장** | — | ❌ **§4.3 결정 대기** |

## 4.5.1 파일 전달 방식 — 경로 전달을 택한 이유

지시서 §26 의 세 후보 중 **임시 파일 경로 전달**을 택했다.

| 후보 | 판단 |
|---|---|
| multipart 업로드 | `python-multipart` 가 **추가로 필요**하다. 승인된 의존성은 openpyxl 하나뿐이라 채택하지 않았다 |
| **경로 전달** | ✅ 채택. 백엔드는 앱이 띄운 `127.0.0.1` 전용 자식 프로세스이고 파일은 같은 PC 에 있다. 파일 본문을 네트워크로 다시 실어 보내지 않는다 |
| main 에서 직접 처리 | Electron 에 업무 로직이 생긴다(§9 금지) |

렌더러에는 **경로 문자열만** 돌아오며 파일 내용은 넘기지 않는다.

## 4.5.2 검증 결과 표시

`행 번호 · 항목명 · 구분 · 내용` 4열 표로 보여준다(지시서 §15).

```text
3행 | 사업자등록번호 | 확인 | 사업자등록번호 체크섬이 일치하지 않습니다: 1234567890
4행 | 결의일자       | 오류 | 날짜 형식이 잘못되었습니다: '2026.13.45' (예: 2026-03-15)
4행 | 계             | 오류 | 숫자가 아닙니다: 'abc'
```

## 4.5.3 🔴 대상 기간(`period_start` / `period_end`) — 제안

저장이 연결될 때 필요하다. 현재 `import_batch()` 는 기본값이 없고, 파일에서
유추하면 확정되지 않은 규칙이 생긴다.

**제안**: 업로드 카드에 **대상 연도 선택**을 두고 `1/1 ~ 12/31`(D-23 역년)로 넘긴다.
화면 상단에 이미 같은 형태의 연도 선택이 있어 사용자에게 일관되며, 새 업무규칙을
만들지 않는다. **승인 전까지 UI 를 만들지 않았다.**

---

# 4.6 ✅ 2026-08-17 구현 — 업로드 → DB 저장 → 계산 연결

## 4.6.1 완성된 흐름

```text
표준 Excel (6컬럼)
  ↓ uploads/excel_adapter.py        읽기
  ↓ uploads/validation.py           머리글 검증 → 행 검증
  ↓ uploads/mapping.py              얇은 연결자 (값 무변환)
  ↓ importers/batch_import_service  기존 재사용
  ↓ importers/purchase_importer     기존 재사용
  ↓ database/purchase_repository    기존 재사용
SQLite
  ↓ find_for_calculation(PeriodFilter)
calculators/procurement_achievement
  ↓
대시보드
```

**새 Importer 를 만들지 않았다.** 업로드 경로와 기존 적재 경로가 같은 저장
로직을 공유하므로 결과가 갈라지지 않는다.

## 4.6.2 API 책임 분리

| 엔드포인트 | 책임 | 저장 |
|---|---|---|
| `POST /uploads/purchases/validate` | 검증만 | ❌ 어떤 경우에도 저장하지 않음 |
| `POST /uploads/purchases` | 검증 후 저장 | ✅ **오류 0건일 때만** |

기존 검증 API 의 계약은 그대로 두고 저장 API 를 추가했다.

## 4.6.3 "전부 검증 → 전부 저장" 을 **구조로** 강제

오류가 하나라도 있으면 `BatchImportService` 를 **호출조차 하지 않는다.** 따라서
부분 저장 경로가 코드에 존재하지 않는다.

| 상황 | 응답 | DB |
|---|---|---|
| 전체 정상 | `stored: true`, 배치 ID | 저장됨 |
| 1행이라도 오류 | `stored: false` + 오류 목록 | **아무 변화 없음** (배치도 안 생김) |
| 파일 자체 오류 | `stored: false` + 파일 오류 | 〃 |

## 4.6.4 대상 기간

화면이 **연도**를 보내면 백엔드가 `1/1 ~ 12/31` (D-23)로 환산한다.

> `PeriodFilter` 를 쓰지 않았다. 배치 기간은 **단순 날짜 범위**이고, 어느 날짜
> 컬럼으로 연도를 나눌지(D-24)와는 별개다. 여기서 `date_field` 를 고르면
> 확정되지 않은 의미가 붙는다.

---

# 4.7 ✅ 재업로드 교체 — 사용자 확인 구현 완료 (2026-08-18)

PM 결정 PM-004~PM-007 을 구현하려면 무엇이 필요한지 **기존 코드를 실측**했다.
**이번 단계에서는 구현하지 않는다.**

## 4.7.1 이미 갖춰져 있는 것 (변경 불필요)

| 요구 | 기존 구현 | 위치 |
|---|---|---|
| 같은 기간 배치 찾기 | `find_active_by_period()` — **기간이 정확히 일치**하는 ACTIVE 배치 1건 | `import_batch_repository.py` |
| 기존 데이터 교체 | `supersede(previous, new)` — 이전 배치를 `SUPERSEDED` 로 |  〃 |
| 교체된 데이터 계산 제외 | `find_for_calculation()` — `batch_id IS NULL` 이거나 **ACTIVE 배치**인 행만 | `purchase_repository.py` |
| **검증 실패 시 기존 데이터 보존** | 오류가 있으면 `BatchImportService` 를 **호출조차 하지 않음** | `upload_service.py` |
| **적재 성공 후에만 무효화** | `import_batch()` 가 새 배치 적재를 **마친 뒤** `supersede()` 호출 | `batch_import_service.py` |
| 교체 실패 감지 | `find_conflicts()` — 같은 기간 ACTIVE 가 2건 이상이면 반환 | 〃 |

> ✅ **PM-006(검증 오류 시 기존 데이터 유지)은 이미 만족한다.** 새 파일 적재가
> 끝나기 전에는 이전 배치를 건드리지 않는 순서로 되어 있다.

## 4.7.2 ✅ 추가한 것 — 사용자 확인 (PM-005)

그전에는 `POST /uploads/purchases` 가 **묻지 않고 교체**했다. 이제는 확인 없이
교체되지 않는다.

| 계층 | 변경 |
|---|---|
| **DB** | **없음** |
| **Service** | `import_file(replace_existing=False)` · `ExistingPeriodBatchError` · `find_active_batch()` |
| **API** | `replace_existing` 추가. 확인 없으면 **409 `EXISTING_PERIOD`** |
| **UI** | 409 → 팝업 → `[취소]` 무동작 / `[교체]` 재요청 |

409 응답 본문:

```json
{"detail": {
  "code": "EXISTING_PERIOD",
  "message": "2026년 데이터가 이미 등록되어 있습니다.",
  "existing_batch_id": 1, "existing_file_name": "...", "existing_row_count": 2, "year": 2026
}}
```

> ⛔ **409 를 낼 때 DB 는 전혀 변경되지 않는다.** 검사 시점에 아직 아무것도
> 저장하지 않았기 때문이다. 테스트로 고정했다.

## 4.7.3 검증이 교체 확인보다 **먼저**다

오류가 있는 파일로 "교체하시겠습니까" 를 물으면 사용자가 혼란스럽다. 그래서
오류가 있으면 **409 가 아니라 200 + 오류 목록**을 돌려준다.

| 상황 | 응답 | DB |
|---|---|---|
| 오류 있음 (기존 데이터 유무 무관) | 200 · `stored:false` · 오류 목록 | 무변경 |
| 정상 + 기존 없음 | 200 · `stored:true` | 저장 |
| 정상 + 기존 있음 + 확인 없음 | **409** | **무변경** |
| 정상 + 기존 있음 + 확인 있음 | 200 · `stored:true` | 저장 후 이전 배치 SUPERSEDED |

**교체를 승인했더라도 새 파일에 오류가 있으면 기존 데이터를 지킨다**(PM-006).

## 4.7.4 ✅ 논리 교체로 확정 (PM-012)

물리 삭제하지 않는다. 이전 배치의 행은 DB 에 남고 `SUPERSEDED` 표시만 붙어
계산에서 빠진다.

| 이유 |
|---|
| 과거 업로드 이력 보존 |
| 어떤 파일이 사용되었는지 추적 가능 |
| 잘못된 업로드의 원인 추적 |
| 향후 감사·검증에 유리 |

실기동 확인: 교체 후 `purchase` 4건(물리 보존) · 계산 대상 2건 ·
배치 #1 `SUPERSEDED` / #2 `ACTIVE`.

---

# 5. 이번 작업에서 하지 않은 것

| 항목 | 이유 |
|---|---|
| `payment_date` nullable 변경 | PM 결정 — 양식에 컬럼을 추가하는 쪽을 택함 (§4.3) |
| 일반 정책 `evaluation_basis` 변경 | 고객 확인 대기 (W-1-2 · `DECISIONS.md` §0.8.3) |
| 결의일자 미기재 행 UI 안내 | backlog (§0.8.4). 계산 로직은 무변경 |
| 음수 저장 제약 해제 | 승인 대기 |
| multipart 업로드 | 승인되지 않은 의존성이 필요 (§4.5.1) |
| 업로드 API · 화면 | 위 둘에 종속 |
| 중복 판정 | 기준 미확정 (§18) |
| 음수 저장 제약 | 승인 대기 (§7) |
| 트랜잭션 경계 변경 | 먼저 확인 필요 (§44) |

---

# 6. 승인되면 바로 진행할 순서

| # | 작업 | 선행 |
|---|---|---|
| 1 | openpyxl 추가 + 엑셀 어댑터 (읽기) | **승인 ①** |
| 2 | 양식 다운로드 (`GET /uploads/template`) | 승인 ① |
| 3 | Mapping 계층 | **승인 ②** (지급일 처리 §4.3) |
| 4 | 업로드 API (`POST /uploads/purchases`) | 1 · 3 |
| 5 | Electron 파일 선택 + 결과 화면 | 4 |

> **①·② 두 개만 풀리면 업로드가 끝까지 연결된다.** 나머지(중복 판정 · 음수 저장 ·
> 구매유형)는 업로드가 동작한 뒤에 붙여도 되는 항목이다.

---

# 7. 기존 적재 계층 재사용 — 구체적 호출 흐름 (§28-3)

새 저장 엔진을 만들지 않는다. 아래는 **코드에서 실측한** 계약이다.

## 7.1 두 계층의 이음매

| 계층 | 키 |
|---|---|
| `validation` 이 만드는 값 | `resolution_date` · `contract_date` · `company_name` · `business_no` · `amount` |
| `PurchaseImporter` 가 읽는 키 | `payment_date` · `contract_date` · `company_name` · `business_no` · `amount` |

```text
겹치는 키   4개  →  이름까지 그대로 통한다
갈 곳 없는 키 1개  →  resolution_date
채울 값 없는 키 1개 →  payment_date
```

> **즉 남은 결정은 1:1 연결 하나다.** Mapping 계층은 새 변환기가 아니라
> **얇은 연결자**면 된다. 이 사실을 `tests/test_upload_importer_seam.py` 가
> 실행 가능한 형태로 고정한다(테스트 13건).

## 7.2 업로드 API 내부 호출 순서

```python
# 1. 엑셀 읽기 (openpyxl 승인 후)
headers, raw_rows = read_xlsx(file_bytes)

# 2. 파일 단위 검증 — 기존
file_errors = validate_headers(headers)

# 3. 행 단위 검증 — 기존
report = validate_rows(raw_rows)

# 4. Mapping — 미구현. 승인 후 여기 한 줄이 들어간다
rows = [to_purchase_row(row.values) for row in report.rows]

# 5. 적재 — 기존. 새로 만들지 않는다
result = batch_import_service.import_batch(
    rows,
    file_name=upload.filename,
    period_start=...,   # ⛔ 호출자가 지정. 파일에서 유추하지 않는다
    period_end=...,
    file_hash=sha256(file_bytes).hexdigest(),
)
```

**4번이 비어 있는 유일한 칸**이며, §4의 결정이 그 칸을 채운다.

## 7.3 기간(`period_start` / `period_end`)을 어디서 받는가

`import_batch()` 의 두 인자는 **기본값이 없다**(실측). 파일에서 유추하면
확정되지 않은 규칙이 생기므로, **화면에서 사용자가 지정**해야 한다.

| 안 | 내용 |
|---|---|
| **가** | 업로드 화면에서 대상 연도(또는 월)를 고르게 한다 |
| 나 | 표준 양식에 대상 기간 셀을 추가한다 (⚠️ 양식 변경 = 확정 필요) |

> 🔴 **미결정.** 업로드 화면 설계 시 PM 확인이 필요하다.

## 7.4 재업로드 시 동작 (기존 구현)

`BatchImportService` 가 이미 처리한다.

| 상황 | 동작 |
|---|---|
| 같은 기간 재업로드 | 새 배치 생성 + 이전 배치를 `SUPERSEDED` 로 표시 |
| 같은 파일(해시 동일) | `duplicate_of` 로 **보고**. 막지는 않음 |
| 행 물리 삭제 | **하지 않음** |

> 지시서 §18 의 "중복 업로드 판정 기준 미확정" 은 **행 단위 중복**을 말한다.
> **배치 단위** 대체는 D-25 로 이미 확정·구현되어 있다. 둘을 혼동하지 않는다.

---

# 8. 업로드 결과 화면 설계 (§28-6)

## 8.1 화면 상태

```text
[대기]  →  [파일 선택]  →  [검증 중]  →  ┬ [검증 실패] → 오류 목록 → 다시 선택
                                          └ [검증 통과] → 미리보기 → [저장] → [완료]
```

**검증과 저장을 분리한다.** 사용자가 오류를 먼저 보고 엑셀을 고칠 수 있어야
하며, 부분 저장 후 실패하는 상황을 피하기 위함이다(지시서 §44).

## 8.2 표시 항목

| 구역 | 내용 | 출처 |
|---|---|---|
| 요약 | 총 / 정상 / 오류 / 확인 필요 | `ValidationReport.summary_lines()` |
| 오류 목록 | 행 · 컬럼 · 사유 | `ValidationReport.issue_lines()` |
| 저장 결과 | 배치 번호 · 저장 건수 · 대체된 배치 | `BatchImportResult` |

**전부 기존 객체가 이미 들고 있는 값이다.** 화면에서 다시 계산하지 않는다.

## 8.3 오류 표시 원칙

```
총 1,250건    정상 1,230건    오류 20건

  12행 | 사업자등록번호 | 값이 없습니다.
  18행 | 결의일자       | 날짜 형식이 잘못되었습니다: '2026.13.45' (예: 2026-03-15)
  25행 | 계             | 숫자가 아닙니다: 'abc'
```

| 원칙 | 이유 |
|---|---|
| 행 · 컬럼 · 사유 3요소 | 사용자가 엑셀에서 바로 찾아 고칠 수 있어야 한다 |
| stack trace 노출 금지 | 행정업무 담당자용 프로그램이다 |
| 한 행에 문제 여러 개여도 **1행으로 집계** | 이미 구현됨 (`error_row_count`) |
| 100건 초과 시 절단 + "외 N건" | 이미 구현됨 (`issue_lines(limit=)`) |
| **경고는 저장을 막지 않음** | 체크섬 오류(D-002) · 0 이하 금액 |

## 8.4 화면 구현 방식

기존 `index.html` 에 **섹션을 추가**한다. 화면을 새로 만들지 않는다.

| 동작 | 담당 |
|---|---|
| 파일 선택 | Electron `dialog.showOpenDialog` (preload 경유) |
| 파일 읽기 | Electron main (렌더러에 파일 시스템 미노출) |
| 전송 | main → `POST /uploads/purchases` |
| 검증·저장 | **Python** |
| 결과 표시 | 기존 화면 스타일 재사용 |

⛔ **렌더러에서 검증·집계를 다시 하지 않는다.** 서버가 준 값을 그대로 그린다.
