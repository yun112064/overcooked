# T04：TFDA 優先與 LLM 備援

> 合併狀態：2026-10-30 使用者決議 T12 可直接使用 T04 驗證後的 LLM 烹調係數，須保留估算標示、來源及條件；本機測試仍不代表 G04 通過。主系統採用資格也不能直接等同於七項營養完整旗標。詳見 [T04／T06 待決議與驗收界線](../docs/T04-T06-merge-pending.md)。

每項食材先找「食材型態及料理方式相符」的 TFDA 熟食營養；缺少時，以 TFDA 生食營養作基底，讓 LLM 參考相關 USDA 產率紀錄及料理條件估算參數，再由程式計算。這是執行時提供參考資料，不是訓練模型。USDA 熟食營養已退出主計算流程。

## 快速執行

Python 3.11+；執行計算僅用標準函式庫。從 Excel 重新匯入需 `pandas` 與 `openpyxl`；相關匯入測試在未安裝 pandas 時跳過。在專案根目錄執行：

```powershell
$env:PYTHONUTF8 = "1"
python nutrition_model/calculate_recipe.py --input nutrition_model/examples/cooked_sesame.json
```

熟白芝麻範例採 TFDA `C1610101` 與實際熟重，七項營養完整，不需 LLM。`examples/cooked_rice.json` 展示糖缺值：白飯其餘六項有值，但七項營養不完整。加上 `--output result.json` 可保存結果；`--input -` 接收標準輸入。完整且烹調狀態完整時退出碼為 0，輸入錯誤或結果不完整為 2；不完整結果仍輸出 JSON。

## 預覽 USDA 資料與設定 Gemini

先檢查每道食譜實際會送給模型的資料；預覽不呼叫 API，也不需要金鑰：

```powershell
python nutrition_model/calculate_recipe.py --input nutrition_model/examples/roast_chicken.json --preview-llm
```

輸出的 `llm_requests` 是每項需要估算的食材各自的請求內容，含 TFDA 生食每100g營養、料理條件、檢索到的 USDA 產率紀錄與 `reference_id`。這些資料只隨當次請求提供模型參考，沒有訓練或上傳整份 Excel。若 `request_count` 為 0，代表此範例不需模型。請先確認產率參考的部位與料理方式；類比紀錄不是精確適用值。

MVP 使用 Gemini 原生 Generate Content API，不需要自行填 API 網址。設定本機環境變數即可；根目錄 `.env.example` 是模板；可複製為 `.env` 後在本機填寫金鑰。CLI 會載入「目前工作目錄」的 `.env`，其中 LLM 設定會覆寫同名環境變數；請從專案根目錄執行。支援單行值、引號、行內註解及 `export` 前綴，不支援變數插值或多行值。直接呼叫 Python API 時由呼叫端設定環境。不要把金鑰寫進食譜或提交到專案。

```powershell
$env:NUTRITION_LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "你在本機設定的金鑰"
# 如需改用其他有權限的模型，可設定 $env:GEMINI_MODEL
python nutrition_model/calculate_recipe.py --input nutrition_model/examples/roast_chicken.json
```

預設模型為 `gemini-3.8-flash`，可用 `GEMINI_MODEL` 覆寫。每項需估算的食材最多 4 次生成請求，每次逾時 120 秒；500/502/503/504 先重試一次，404 直接查詢模型清單，備援只選實際列出且查證有免費級別的文字模型（3.8 Flash、3.5 Flash-Lite、3.1 Flash-Lite）。401/403/429/400 不回退。模型清單另有分頁請求，每頁逾時 120 秒，最多 20 頁。程式只記錄遮蔽金鑰後的模型、HTTP 狀態與服務錯誤訊息，不記錄請求標頭或模型原始估算回覆。免費資格與費率由 Google 專案帳務等級決定，程式不會啟用付費或自動升級。使用者已取得一次通過參數驗證的 Gemini 估算；尚未用實際料理對照驗證營養準確度。烤雞胸肉範例的 TFDA 原始纖維缺值，因此即使模型成功估算，七項營養也可能不完整，程式以退出碼 2 與 null 明確表示。

原有通用 JSON chat/completions 介面仍可選用：設定 `NUTRITION_LLM_PROVIDER=openai_compatible`、`NUTRITION_LLM_ENDPOINT`、`NUTRITION_LLM_MODEL`，必要時設定 `NUTRITION_LLM_API_KEY`。遠端網址須為 HTTPS，本機 localhost 可用 HTTP。

也可注入團隊現有串接：`estimator(payload) -> dict`。回覆格式如下，數字只示範格式，不是推薦係數：

```json
{
  "yield_factor": 0.75,
  "reference_ids": ["必須是payload中提供的reference_id"],
  "nutrient_retention_factors": {
    "energy_kcal": null,
    "carbohydrate_g": null,
    "protein_g": 0.9,
    "fat_g": null,
    "sugars_g": null,
    "sodium_mg": null,
    "dietary_fiber_g": null
  },
  "assumptions": ["說明類比對象、料理條件與保留率假設"],
  "missing_information": []
}
```

模型只估參數，不回傳最終營養。不存在的引用 ID、非有限數字、負值與非法保留率會被拒絕。保留率限制 0～1，指原有營養實際食用的比例，新增油鹽由獨立食材列處理。沒有 USDA 參考時，產率須為 null；有生重時仍可估營養保留率，但缺熟重就不輸出每100g值。保留率是 LLM 假設，不宣稱是 USDA 官方資料。

## 接入程式

將 `nutrition_model` 加入 Python 模組搜尋路徑後：

```python
from nutrition_service import calculate_recipe_nutrition

result = calculate_recipe_nutrition("烤雞胸肉", [
    {
        "name": "雞胸肉", "weight_g": 150, "weight_basis": "raw",
        "cooking_method": "烤", "cooked_weight_g": 112,
        "cooking_context": {"drained": True, "juices_consumed": False}
    }
], servings=1)
```

- `weight_g` 是可食部重量，`weight_basis` 預設為 `raw`。已是熟重請填 `cooked`，不會再乘產率，也不能拿熟重乘生食成分。
- 食材 `cooked_weight_g` 為實際熟重，優先於估算。食譜層級同名參數指定整道成品實測重，適合含食用湯汁的料理；只要各項營養完整，可完成整道食譜計算，不會因此填入未知的個別食材熟重。未指定時，只有每項成品重量都已知才加總計算每100g值。
- 油、鹽及醬料須列為獨立食材。`consumed_as_is: true` 表示直接按提供成分食用，不需烹調轉換；`retained_fraction` 為實際食用比例（0～1，預設 1）。例如10g油留存一半填0.5，營養及該列成品重都乘0.5。模型不再額外加入油鹽。
- `cooking_context` 可提供時間、溫度、部位、皮骨、加水量、是否瀝乾及湯汁是否食用；不會自動新增食材。
- TFDA 生食缺值保留 null，不讓模型猜測缺少的生食營養。因此雞胸肉範例的纖維仍可能缺值。Excel 共七項營養：熱量、碳水、蛋白質、脂肪、糖、鈉、膳食纖維。
- 無料理方式的生食列僅保留 `raw_reference`，正式成品營養為 null；直接食用須明確填 `consumed_as_is=true`。已熟 TFDA 品項須標記 `weight_basis=cooked`，可直接使用並標示完成。單獨查生食仍可用 `calculate_nutrition()`。

## 計算規則

```text
產率 Y = 熟可食重 / 生可食重
有 TFDA 熟食值：每100g熟食營養 × 熟重 / 100
無 TFDA 熟食值：每100g生食營養 × 生重 / 100 × 該營養保留率
每100g成品營養：整份營養 / 成品熟重 × 100
每份營養：整份營養 / servings
```

產率不等於營養保留率。已有 TFDA 熟食值不再乘保留率。熟食只缺部分營養時保留已知值，僅估缺項；逐項來源在 `nutrient_sources`。加總前保留精度，食譜輸出才四捨五入。

## 資料對應與限制

`data/source/食材營養成分表.xlsx` 是這版的營養成分來源；`import_workbook.py` 由 Excel 產生 `ingredient_master.json`、完整來源表 `workbook_foods.json`、`ingredient_id_master.json` 與來源雜湊 `workbook_manifest.json`。完整來源表保留 2,213 筆 TFDA 與 372 筆 USDA 熟食補充，另有 461 筆食材 ID；主營養流程只從 TFDA 食品列計算，不使用 USDA 熟食補充數值。既有食材別名只用於查找，沒有取代 Excel 的營養值。更新 Excel 後執行：

食材 ID 主檔的 `ING` 編號與來源食品 ID 是兩種識別。Excel 未提供逐列配對，因此本版保存主檔但不猜測 `ING` 編號對應哪一筆營養紀錄；食譜查詢仍以既有名稱、別名與人工確認的 TFDA 對應表為準。

```powershell
python nutrition_model/import_workbook.py --input "D:\Queena\AIPE04-G5-overcooked\食材營養成分表.xlsx"
```

匯入程式會把指定 Excel 複製到專案的 `data/source/`，並重建資料檔。舊的原始 TFDA CSV 匯入腳本僅供歷史用途，不應用來刷新這版主檔。

`data/tfda_cooked_mapping.json` 是人工維護對應表，目前依既有 TFDA 描述加入白飯與烤白／黑芝麻。52筆熟食紀錄不會自動當成任意料理方式皆適用。匯入器會套用已確認的料理方式；確認生熟配對後，可在熟食紀錄加入 `raw_ingredient_id` 及 `cooking_method`，生熟名稱不必相同。多筆無法唯一判定不任選；`cooking_context.tfda_cooked_id` 只能在已符合條件的候選中指定。

USDA 以 `data/usda_food_mapping.json` 前兩項關鍵字（物種及部位）檢索，優先排序方式及型態相符者，最多五筆。只有明確 `food_form`、全部關鍵字符合且無排除詞的唯一紀錄，才能直接作精確產率；其他標記 `analogy` 交給模型比較。新增食材需維護對應表。

現有產率來源為 **USDA Table of Cooking Yields for Meat and Poultry, Release 2**，不涵蓋米飯或蔬菜通用產率。紅蘿蔔不會收到無關肉類參考；可提供實際熟重，或日後補適用參考來源。原 USDA 熟食資料與匯入器保留供歷史查詢。

湯汁、油脂及鹽分在同鍋食材間轉移時，逐食材模型是近似；需依湯汁是否食用設定條件，避免將仍留在湯中的營養當作流失。應以代表性料理實測案例評估模型品質。

## 結果狀態

| 欄位 | 意義 |
|---|---|
| `success` | 食材辨識及輸入處理成功，不代表營養完整 |
| `nutrition_complete` | 七項都有數值，不代表都是資料庫直接值 |
| `cooking_adjustment_complete` | 烹調營養及成品重量均可計算 |
| `is_estimated` | 任一營養或產率使用 LLM |
| `nutrition_basis` | `cooked_tfda`、`raw_tfda_llm_estimate` 或混合 `cooked_tfda_with_llm_estimate` |
| `yield_basis` | `measured`、`usda_exact`、`llm_estimated` 或 null |
| `nutrient_sources` | 逐項來源及保留率 |
| `llm_estimate` | 已驗證模型參數、引用、假設及缺少資訊 |
| `raw_reference` | 不參與熟食總量的生食參考 |

查無食材或營養缺值時，食譜相應 `total_nutrition` 為 null；已知部分只放 `partial_nutrition_totals`。LLM 未設定、逾時或無效時不退回生食值冒充熟食值。

`total_nutrition`、`per_serving_nutrition` 與 `nutrition_per_100g` 為顯示用，取兩位小數。T12 做 n 倍縮放與 ±10% 邊界判斷時，從 TFDA 原始十進位數值與 T04 驗證後的係數重算精確值，不直接使用 float 型別的 `total_nutrition_unrounded` 作邊界判斷；缺值同樣是 null。

## 營養知識資料（R12、G07）

`knowledge_service.py` 只處理文字知識，與 TFDA 數值營養分開；缺知識可省略說明，不代表缺必要營養。

- `KnowledgeStore(ingredient_ids)`：`import_record()` 需 `knowledge_id`、`ingredient_id`、`excerpt`、`source_url`（http/https）、`source_version`、`rights`、`conditions`，一律先成為 pending；同一 `knowledge_id` 與 `source_version` 不可重複匯入。
- `review(..., reviewer, reviewed_at, approved, note)` 是管理端操作，審核後不可再改；只有 approved 可檢索。匯入新版本後舊版退出檢索，但保留在歷史，已回傳的快照不變。
- `retrieve(final_ingredient_ids)` 只取最終配方食材，回傳 `status`（available／not_found）、`records` 與逐食材的 `by_ingredient`。配方變更後須重新呼叫，舊食材的說明不會保留。
- `retrieve_knowledge(path, ingredient_ids, final_ingredient_ids)` 讀檔或解析失敗時回傳 `unavailable`，不會偽裝成查無。
- `verify_citations(citations, snapshot)` 核對說明引用的 `knowledge_id`／`source_version`／`ingredient_id` 是否確實出現在本次快照；虛構、錯食材、未審核或舊版引用列入 `invalid`，不可顯示。

`test_knowledge_service.py` 只使用合成資料驗證契約。使用者尚未提供真實知識來源、授權與審核，**G07 仍未通過**。

## 驗證

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "nutrition_model"
python -m unittest discover -s nutrition_model -v
```

`test_tfda_llm.py` 涵蓋 TFDA 優先、部分缺值、重量基準、參數驗證、Gemini 與通用 HTTP 邊界、預覽不呼叫 API、油脂留存、來源與加總，以及錯誤料理方式、非有限／負數營養與缺少單位的匯入。`test_workbook_source.py` 檢查來源資料數量、七項 TFDA 營養與白飯缺糖。`test_knowledge_service.py` 驗證營養知識契約。原有部分 `test_*.py` 是展示腳本，不是斷言測試。

## 2026-10-16 驗收修正

修正已有料理方式時漏檢已熟食材重量基準、整道實測重的完成狀態，以及超大整數輸入造成例外。CLI 改為標準函式庫讀取單行 `.env`，無須安裝 python-dotenv。新增 6 個回歸測試，連同既有測試共 53 項；完整結果以附帶驗收報告為準。
