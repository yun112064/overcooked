# Codex Review：營養缺值處理（2026-11-04）

範圍：`nutrition_model/cooked_nutrition_service.py`、`nutrition_model/import_workbook.py`、`nutrition_model/nutrition_service.py`、`backend/app/services/nutrition_import.py`、`backend/app/services/t04_database_nutrition.py` 及相關測試。
依據：spec.md R09／AC13／AC17、`NutritionRecord` 契約，`docs/T04-T06-merge-pending.md` P03。
Codex CLI 0.166.0。缺值審查 4 輪（session `01a1a551-0627-7983-9064-0dd02b05cee0`）後，併入 `codex/t11-cloud-explain-t12` 再做分支審查 2 輪（session `01a1a56b-3eb1-7c21-a61a-ceb2c72247f3`）。**最終結論：VERDICT: APPROVED。**

## 各輪問題與處理

| 輪 | 問題 | 等級 | 處理 |
|---|---|---|---|
| 1 | t04 `cooking_adjustment_complete` 固定為 True，未驗證生／熟重量基準 | WARNING | 已修：新增 `weight_basis`（raw／cooked，對映 未熟製／已熟製），不符回 `weight_basis_mismatch`，未宣告為 `weight_basis_unverified` |
| 1 | 缺少採用驗證共用的必要／展示欄位清單與逐項缺值原因 | WARNING | **未修**：依 P03 待決議，需規格核准 |
| 1 | `partial_nutrition_totals` 全缺值時以 `or 0` 輸出 0 | WARNING | 已修：無任何已知值時為 `None`；原測試依賴假 0，已改並新增回歸測試 |
| 1 | `import_workbook.number()` 把非空異常值靜默當缺值 | WARNING | 已修：新增 `invalid_fields` 保存原始值，不中止匯入 |
| 1 | 計算來源為空的列仍可標營養完整 | WARNING | 已修：品質標記 `missing_calculation_source` |
| 1 | t04 食譜計算 N+1 查詢 | SUGGESTION | 未修，延後 |
| 2 | 生食列宣告 raw 即視為烹調完成 | WARNING | 已修：新增 `consumed_as_is`；未熟製須原樣食用宣告才完成；分出 `weight_basis_verified`、`source_basis_verified` |
| 2 | `is_invalid` 在 `clean()` 後判斷，inf／nan 誤判；`invalid_fields` 未傳到計算結果 | WARNING | 已修：先分類原值；`calculate_nutrition` 回傳 `invalid_fields` |
| 2 | 缺計算來源只是提示，無阻擋狀態 | WARNING | 已修：納入 `cooking_adjustment_complete` |
| 3 | 原始 inf 已被 pandas／clean 轉為缺值，仍漏記 | WARNING | 已修：以 `keep_default_na=False` 另讀原始儲存格分類 |
| 3 | 來源驗證只看新品質標記，舊匯入列不受保護 | WARNING | 已修：改查實際來源連結 `included_in_calculation` |
| 4 | 依據未驗證時，縮放值仍進入正式 `nutrition`／`total_nutrition`，可能 `nutrition_complete=True`（違 AC13） | WARNING | 已修（分支審查）：正式欄位為 `None`，縮放值移到 `reference_nutrition` |
| 4 | `import_workbook` 兩次讀取並以未檢查的 `zip` 配對列 | SUGGESTION | 未修 |
| 分支 1 | 只要任一計算來源連結就算驗證；已刪除／變更的食譜來源列會被忽略 | WARNING | 已修：宣告的 `計算來源ID` 集合須與參與計算的連結完全相同；新增混合來源測試 |
| 分支 1 | T03 核准直接改寫 0.3.0 規則檔與固定雜湊 | WARNING | 已修：0.3.0 還原，核准另發 `hpa-dri8-mvp-0.3.1` |
| 分支 2 | T03 文件仍寫 0.3.0 為現行／新流程版本 | SUGGESTION | 已修 |

## 未解決項目

1. **P03 契約**：必要／展示欄位清單及逐項缺值原因待決議與規格核准。
2. N+1 查詢、單次讀取 workbook 兩項建議。

## 驗證狀態

在臨時 pgvector PostgreSQL 容器上（依 CI 設定）：

- 後端 unittest：160 項通過。
- `nutrition_planning`：174 項通過；`nutrition_target`：27 項通過；`nutrition_model`：68 項通過、2 項略過。
- `alembic upgrade head` 後 `alembic check`：No new upgrade operations detected。
- 真實工作簿未重新匯入，`invalid_fields` 對真實資料的結果未驗。
