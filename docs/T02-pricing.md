# T02 零售價格來源（第一階段：全聯）

驗證日期：2026-10-18（Asia/Taipei）

## 範圍與結論

本階段只接入全聯福利中心「全聯小時達」，未實作萬家福或其他供應商。程式以 `recipe_ingredients.Food_ID` 的唯一值建立查價範圍，使用活頁簿現有 `canonical_name` 對應標準名稱；`ingredient_aliases.alias` 只作商品匹配提示。相同標準名稱只查全聯一次，結果保留其全部 Food_ID。

本機 `data/recipe_dataset_1017.xlsx` 實測有 **434 個唯一 Food_ID**，434 個皆可對應名稱，去重後需搜尋 **434 種標準食材**，無法對應 0 個。CLI 每次執行都重新計算，不將數字硬編碼；找不到或有衝突的名稱會記為缺值，不會送到全聯搜尋或填入假價格。

## 來源研究與取得邊界

- 公開搜尋入口：`https://shop.pxgo.com.tw/hourArrive/search/result?q=<關鍵字>`。
- 頁面可在未登入情況下以 HTTP GET 取得 server-rendered HTML；商品以 Schema.org `Product` JSON-LD 提供名稱、商品 URL、`Offer.price`、`priceCurrency` 與 availability。
- `https://shop.pxgo.com.tw/robots.txt` 在驗證日只禁止 `/admin/`、`/maintenance/`、`/hourArrive/member/`、`/inBatches/member/`，沒有禁止上述公開搜尋路徑。
- 沒有找到供第三方承諾穩定性的正式公開 API，因此 Adapter 不呼叫或模擬未公開的內部 API，也不登入、不帶 Cookie、不選門市、不處理 CAPTCHA。
- `robots.txt` 與免登入技術可達性不等同於全聯授予大量再利用或商業使用權。正式部署、大量排程或對外散布資料前，仍應由專案負責人向全聯確認使用條款/授權。若條款或 robots 規則改變應停用 Adapter 並重新審查。

預設行為是 1.5 秒查一種食材、單次請求 15 秒逾時、最多重試 1 次。HTTP 401/403/429、CAPTCHA/robot-check 字樣會立即記為 `source_error`，不繞過、不持續重試。

## 資料契約

`PriceQuote` 使用 Pydantic，價格與包裝量使用 `Decimal`。主要狀態如下：

| 狀態 | 意義 |
|---|---|
| `success` | 高可信度候選已依政策自動採用，可作正式價格快照 |
| `review_required` | 中可信度估價備選；只寫 `estimated_price`，正式 `price` 仍為空 |
| `not_found` | 無結果或所有商品皆未通過名稱/加工狀態對映 |
| `missing_price` | 匹配且在庫，但來源沒有售價 |
| `out_of_stock` | 匹配候選都缺貨或供貨狀態未知 |
| `unusable_package` | 找到商品與價格，但重量或單位不可可靠換算 |
| `stale_price` | 來源明示的價格有效期已過 |
| `timeout` / `source_error` | 網路逾時、HTTP 錯誤、限流或阻擋 |
| `schema_changed` | HTTP 成功，但找不到預期的 Product JSON-LD |

結果另分為 `auto_adopted`、`estimate`、`missing` 三層。非成功狀態的正式 `price` 必須為 `null` 且必須有 `missing_reason`，不能用 0 代替。估價只出現在獨立的 `estimated_price` 欄位。原始 JSON-LD、候選分數、信心、重量可靠度、匹配與拒絕原因均保存在 `candidates`。

價格有效期只接受來源明示的 `validFrom` / `priceValidUntil`；來源未提供時保持 `null`。公開搜尋未指定配送地址或門市，所以 `region_condition`、`store_condition` 會註明價格及供貨仍可能隨地區、門市、時間與結帳頁改變。JSON-LD 只能證明頁面顯示價，無法可靠區分一般價或促銷價時，`price_kind` 記為 `displayed`、促銷欄位保持空值。

## 商品對映與自動選價

`pxmart-selection-v2` 明確分成五階段：公開搜尋候選、食材類型判定、硬性排除、候選評分、自動選價。

- 身分判定使用 canonical name、Excel 同列 alias 與少量保守中文別名字典；「蛋」等過短別名及「蔬菜」等泛稱不能建立匹配。
- 雞蛋對鴨蛋／皮蛋／鹹蛋、青蔥對洋蔥／紅蔥頭／蔥餅、太白粉對地瓜粉等品種衝突會在評分前直接排除。
- 調理、即食、滷、炸、醃、煙燻、罐頭、布丁、餅、醬等加工狀態不符時直接排除；冷凍、生熟、帶骨／去骨、帶皮／去皮差異最多只能成為估價備選。
- 只有通過身分、狀態、在庫、正價格、未過期及重量可靠度門檻的高可信度候選可自動採用。價格不參與食材身分補救。
- 多個高可信度候選依「既有代表商品、語意分數、重量可靠度、單位價格、穩定商品 ID」排序，因此多商品不再一律要求人工確認。
- 包裝解析保存總重、件數、每件重量、公差、可食部重量與重量基準；支援 `360±36g`、`600±30g`、`100gx6入`、`540g以上`。`以上` 等下限重量不能自動採用。

這些規則仍刻意保守；無法可靠匹配時留下缺值，下一個排程日重試。

## 安裝與執行

在 repository 根目錄建立虛擬環境後：

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "backend"
# 鎖與續跑測試需要 TEST_DATABASE_URL（可清空的 PostgreSQL）
python -m unittest discover -s tests -p "test_t02_*.py" -v
```

先查前三種標準食材並輸出 JSON：

```powershell
python scripts/collect_pxmart_prices.py `
  --input data/recipe_dataset_1017.xlsx `
  --output outputs/t02-sample.json `
  --limit 3
```

完整查詢 Food_ID 範圍去重後的標準食材並輸出 CSV：

```powershell
python scripts/collect_pxmart_prices.py `
  --input data/recipe_dataset_1017.xlsx `
  --output outputs/pxmart `
  --format csv
```

`--output` 可以是檔案或資料夾；資料夾模式會產生含時間戳的檔案。JSON 保存完整階層候選；CSV 的 `candidates_json` 保存同一份候選資訊。`data/` 與 `outputs/` 已忽略，不會把原始 Excel 或即時結果自動提交。

無網路 dry-run 會驗證完整 canonical 清單與輸出計畫：

```powershell
python scripts/collect_pxmart_prices.py --input data/recipe_dataset_1017.xlsx `
  --output outputs/t02-dry-run.json --dry-run
```

批次與續跑可使用 `--batch-size 20 --checkpoint outputs/t02-checkpoint.json`。每完成一種食材即寫 checkpoint；同一命令重跑會略過已完成項目。單一食材未預期失敗會寫 `source_error` 後繼續。

## 每日自動更新

每日查價已併入專案唯一的排程程序 `app.jobs.scheduler`（T10；以 PostgreSQL advisory lock 保證只有一個在執行），**沒有**掛在 FastAPI lifespan，也不另開第二個排程器。預設每天 `Asia/Taipei` 03:00 執行；每次由 `load_ingredient_queries()` 讀取 `recipe_ingredients.Food_ID`、解析工作簿標準名稱並去重 canonical name，目前供應商固定為 `pxmart`。

執行前先套用 migration：

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:<password>@localhost:5432/meal_planner"
python -m alembic upgrade head
```

本機啟動長駐排程：

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:PRICE_SCHEDULER_ENABLED="true"
python -m app.jobs.scheduler
```

手動立即執行一次並寫入同一套 run/snapshot 表：

```powershell
$env:PYTHONPATH="$PWD\backend"
python -m app.pricing_scheduler --run-once
```

除錯重跑時可明示 idempotency key；相同 key 第二次會跳過：

```powershell
python -m app.pricing_scheduler --run-once --run-key debug-2026-10-18-a
```

查看最近 20 次執行摘要：

```powershell
python -m app.pricing_scheduler --show-runs 20
```

雲端／Docker Compose：`docker compose up -d --build` 會啟動唯一的 `scheduler` service（同時負責 T10 工作回收與日誌清理），查價設定由 `PRICE_*` 環境變數傳入。

```powershell
docker compose logs -f scheduler
```

只停用查價時設 `PRICE_SCHEDULER_ENABLED=false` 後重建 scheduler container（其他排程照常執行）。不要另開第二個 scheduler service；`python -m app.pricing_scheduler` 只保留 `--run-once`、`--show-runs` 等手動操作。

| 變數 | 預設 | 用途 |
|---|---|---|
| `PRICE_SCHEDULER_ENABLED` | `true` | 啟用／停用長駐排程；`--run-once` 不受此值阻擋 |
| `PRICE_SCHEDULER_HOUR` / `PRICE_SCHEDULER_MINUTE` | `3` / `0` | 每日執行時間 |
| `PRICE_SCHEDULER_TIMEZONE` | `Asia/Taipei` | 排程時區 |
| `PRICE_WORKBOOK_PATH` | `data/recipe_dataset_1017.xlsx` | 本機輸入；Compose 用 `/app/data/recipe_dataset_1017.xlsx` |
| `PRICE_COLLECTION_DELAY_SECONDS` | `1.5` | 食材間限速 |
| `PRICE_COLLECTION_TIMEOUT_SECONDS` | `15` | 單次來源逾時 |
| `PRICE_COLLECTION_RETRIES` | `1` | 有限重試，允許 0–3 |
| `PRICE_COLLECTION_BATCH_SIZE` | `0` | `0` 表示完整處理；正整數可分批並以同一 run key 續跑 |

### 重複執行與歷史保存

- APScheduler job 設定 `max_instances=1`、`coalesce=True`。
- 執行全程持有 PostgreSQL session advisory lock；即使誤啟兩個容器，也只有一個能進入來源查詢。
- 每個排程日使用 `pxmart-daily-YYYY-MM-DD` 作 `price_collection_runs.run_key`，資料庫 unique constraint 與 `ON CONFLICT DO NOTHING` 防止重啟後重做同一日工作。
- 每種食材的結果都 `INSERT` 到 `price_quote_snapshots`，不更新或覆寫上一批快照；完整 `PriceQuote`（含原始候選、來源、查詢時間、有效期與缺值原因）保存在 JSONB。
- 首次高可信度自動採用會寫入 `retailer_product_mappings`，保存 canonical name、PX Mart product ID、選擇理由、信心及政策版本。後續排程優先選既有代表商品；無效時由同次搜尋結果重新評分替代品。連續三次失敗會停用映射，代表商品每 30 天強制重新評估。
- 每筆 snapshot 寫完即提交。單一食材發生未預期錯誤時寫入 `source_error` snapshot 後繼續；run row 最後記錄成功、待確認、失敗、各狀態與缺值原因次數、開始／完成時間及耗時。
- 若 process 在中途崩潰，advisory lock 會由 PostgreSQL 連線關閉自動釋放，已提交 snapshot 與 `running` run row保留供調查；同一日不會被自動偽裝成新的完整更新，可另用新的 manual run key 人工重跑。

目前未執行完整 434 種的每日 production run，也沒有接入萬家福。下方三筆真實小量驗證只證明公開全聯頁面在驗證時間可取得候選，不代表每日雲端更新已成功上線。

## 測試與 G02 狀態

固定 fixtures 覆蓋 Excel 欄位/去重、Decimal 價格、缺價、缺貨、包裝缺值、加工狀態不匹配、有限逾時重試、來源欄位變動、過期/未知有效期，以及缺值不得為 0。

2026-10-18 修正短名稱與水類排除規則後，以新的 Food_ID 範圍執行 `--limit 3 --retries 0`：低筋麵粉、太白粉成功自動採用；「純水冰角」被硬性排除，水沒有可靠可採用商品，回傳 `selection_tier=missing`、`price=null`。這次只證明少量來源與規則可運作，不能外推為完整 434 種的匹配正確率。

| canonical_name | 原始候選 | 自動採用代表商品 | 售價（NTD） | 判定 |
|---|---:|---|---:|---|
| 低筋麵粉 | 46 | 耆盛低筋麵粉 500 g | 26 | 高可信度；score 94 |
| 太白粉 | 42 | 台糖高級太白粉 300 g | 22 | 高可信度；score 94 |
| 水 | 49 | 無 | — | `unusable_package`／`missing`；純水冰角因冰角衝突被排除，未填入價格 |

本次原始候選、分數與拒絕原因保存在本機忽略檔 `outputs/t02-waterfix-live-limit3.json`。434 種無網路 dry-run 已確認 Food_ID、名稱對應與查價清單，但尚未執行 434 種真實請求，因此完整真實覆蓋率仍未知。

G02 目前可完成「公開來源取得、追溯候選、缺值/阻擋處理與小量真實查詢」的工程驗證。

**授權（2026-10-19 更新）**：專案負責人確認已取得全聯資料使用授權；授權文件編號或存放位置仍需補入專案紀錄。原本「尚未取得書面授權」的限制解除，但仍維持低頻、不繞過封鎖的取得方式。

仍有的正式驗收限制：

1. 未選地址/門市的公開頁面不能代表特定家庭所在門市一定可購得，也不能取代結帳頁最終價格。
2. 促銷價未解析（`price_kind=displayed`），地區只記「未選地區」。

### 2026-10-19 真實資料小量驗證

以 `recipe_dataset_1017.xlsx`（434 種 Food_ID）在乾淨 PostgreSQL（含 pgvector、完整遷移鏈）執行 `--run-once --limit 10`：9 種自動採用、1 種查無，耗時 17 秒。

| canonical_name | 結果 | 代表商品 | 售價（NTD） |
|---|---|---|---:|
| 中筋麵粉 | success | 耆盛中筋麵粉 500g | 26 |
| 低筋麵粉 | success | 耆盛低筋麵粉 500g | 26 |
| 太白粉 | success | 台糖高級太白粉 300g | 22 |
| 白胡椒粉 | success | 味好美白胡椒粉 30g | 45 |
| 砂糖 | success | 台糖貳號砂糖 1kg | 36 |
| 雞蛋 | success | 味全木崗高品質雞蛋（10 入）630g | 104 |
| 青蔥 | success | 青蔥（產銷履歷）100g | 33 |
| 食鹽 | success | 生活良好長崎五島灘食鹽 1000g | 49 |
| 水 | success | 悅氏礦泉水 1500ml | 15 |
| 玉米澱粉 | not_found | — | — |

**水不計價（已決議）**：`config/price_exempt_ingredients.json` 列出不查價、成本為已知 0 的食材（水 FOOD000119、滾水 FOOD001208）。查價範圍因此為 432 種，執行摘要的 `status_counts.exempt` 記錄略過數；營養仍照配方計算。重跑 `--limit 10` 確認水不再查價。完整 432 種的真實覆蓋率仍未量測。

因此 G02 應標示為「部分通過」：來源取得、授權與缺值處理已具備，門市條件與完整覆蓋率仍待驗證。
