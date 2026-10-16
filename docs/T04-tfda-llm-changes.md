# T04 修改交接：2026-10-16

## 本次 Excel 來源修訂

營養成分主檔現由 `nutrition_model/data/source/食材營養成分表.xlsx` 透過 `nutrition_model/import_workbook.py` 建立。來源版本的 SHA-256 存在 `workbook_manifest.json`；`workbook_foods.json` 保留整張食品表的 2,585 列（TFDA 2,213 列、USDA 熟食補充 372 列），`ingredient_id_master.json` 保留原食材 ID 主檔 461 列。主流程使用 TFDA 列，不使用 USDA 熟食營養列。原有食材別名只供查找，七項營養數值及來源欄位取自 Excel。

食材 ID 主檔並未提供 `ING` 編號與來源食品紀錄的一對一配對；目前保留該表，食譜仍以食材名稱及人工確認的 TFDA 對應表查詢，不自動推測配對。

逐筆核對 2,213 筆 TFDA 的七項營養欄位與 Excel 對應；先前六項數值與舊資料逐筆相同，新補「糖」欄位。Excel 中白飯的糖為空，因此即使其他六項有值，整體 `nutrition_complete` 仍為 false；熟白芝麻有七項資料，作為完整的離線範例。

## 新流程

食材型態及料理方式相符的 TFDA 熟食營養優先；沒有資料時，以 TFDA 生食營養為基底，由 LLM 參考檢索到的 USDA 產率紀錄估算產率與各項營養保留率，最後由 Python 計算。USDA 熟食營養已退出主流程。

## 修改重點

- `nutrition_service.py`：保留查詢及生食 API，兩個熟食入口統一呼叫新流程；食譜支援份數、生熟重量與實際成品重。
- `cooked_nutrition_service.py`：TFDA 對應、產率選擇、缺項備援、來源及食譜加總。
- `cooking_yield_service.py`：增加具穩定 ID 的相關 USDA 紀錄檢索，精確資料與類比資料分開標示。
- `llm_cooking_estimator.py`：提示內容、可注入估算器、Gemini 原生 API、原有通用 JSON chat HTTP 串接及回覆驗證。
- `tfda_cooked_mapping.json`：依既有資料描述建立白飯、烤白／黑芝麻的料理方式對應；其他需持續人工確認。
- `import_tfda.py`：套用已確認料理方式；缺單位、負數及非有限營養保留缺值。
- `calculate_recipe.py`、`.env.example`、範例與 README：提供 USDA/TFDA 模型輸入預覽、執行及串接方式。

## 與舊流程的差異

1. 熟食轉換失敗時，生食數值只放 `raw_reference`，不冒充熟食總量。
2. 無料理方式的生食列也只保留參考；直接食用請填 `consumed_as_is=true`。單獨生食查詢仍用 `calculate_nutrition()`。
3. 已熟食材需 `weight_basis=cooked`，避免熟重重複換算。
4. 油、鹽及醬料是獨立食材列，以 `retained_fraction` 指定實際食用比例，不由模型再加一次。
5. 有資料不等於非估算；檢查 `nutrition_complete`、`cooking_adjustment_complete` 及 `is_estimated`。

## 驗證與尚需設定

T04 離線測試 40 項通過；熟白芝麻範例可完整計算，白飯缺糖會明確標示。Gemini 請求／回覆格式與預覽模式以測試傳輸驗證。獨立檢查發現的生食總量、料理方式、非法數值與單位問題，均以失敗測試重現後修正。

Gemini 設定 `NUTRITION_LLM_PROVIDER=gemini` 與本機 `GEMINI_API_KEY` 即可；預設模型 `gemini-3.8-flash` 可覆寫。執行 `python nutrition_model/calculate_recipe.py --input nutrition_model/examples/roast_chicken.json --preview-llm` 可在不呼叫 API 的情況下檢查實際送出的 USDA 紀錄。尚未使用真實金鑰呼叫付費模型，因此未驗證營養推估品質。原始 TFDA 與 USDA 數據沒有重新下載或進行來源檢驗。交付專案以外的系統整合、部署及使用者實際食譜未執行端到端驗證。

現有 USDA 產率參考只涵蓋肉禽，不能將肉類產率當成米飯或蔬菜依據。無合適參考不產生外推產率，可提供實測熟重；原生食營養缺值仍保留 null。

原始下載 ZIP 未改動。

## 本機驗收修訂：2026-10-16

以 Gemini MVP 為驗收對象，原版 47 個測試通過，但獨立驗收發現 4 類問題：已熟食材在料理分支漏檢重量基準、整道實測重未解除食譜重量缺項、超大整數例外，以及 CLI 未宣告的 python-dotenv 依賴。修正版處理上述問題，新增 6 個回歸測試，更新設定優先序與重試說明。原始資料未修改；雞胸肉纖維等原始缺值不填 0。完整證據、修正前後與未驗範圍見交付驗收報告。這次檢查沒有發送新的 Gemini 生成請求。
