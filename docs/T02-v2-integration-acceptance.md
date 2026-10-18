# T02 v2 整合驗收報告

驗收日期：2026-10-18（Asia/Taipei）  
分支：`T02_pricing`  
範圍：PX Mart Adapter、每日排程、PostgreSQL 歷史快照、代表商品對映與續跑機制

## 結論

T02 v2 的兩個 migration 已在本機實際 PostgreSQL 17 測試資料庫升級成功。以公開 PX Mart 搜尋來源執行 `--run-once --limit 3` 時，太白粉、雞蛋、青蔥均成功建立價格快照與代表商品對映；同一 run key 再次執行會跳過，不會新增重複資料。以次日排程時間執行後，系統沿用代表商品更新價格並新增第二日快照，沒有覆蓋前一日資料。

交易失敗與程序中斷案例則以固定 Adapter 搭配實際 PostgreSQL 驗證。失敗的 run 不會被標成 completed；以相同 run key 重啟後會依既有快照 checkpoint 補齊未完成食材。

本次不能宣稱完整 production-ready 驗證：本機沒有 Docker，且乾淨資料庫從 migration chain 起點升級時，前置 migration 所需的 `vector` extension 不存在。兩個 T02 migration 是在實際 PostgreSQL 中先 stamp 到其共同前置 revision 後再升級驗證；完整 migration chain 仍須在具有 pgvector 的正式映像或環境重跑。

## 驗收環境

- PostgreSQL：本機 PostgreSQL 17，獨立臨時 cluster／獨立驗收 database
- Alembic 最終 revision：`a92c6d03e71f`
- Python：使用 PostgreSQL pgAdmin 隨附 Python 3.13 與隔離安裝的測試相依套件
- Docker／Docker Compose：本機沒有 `docker` 命令，未能驗證 Compose service
- 食材來源：只讀 `data/recipe_dataset_1017.xlsx` 的 `ingredient_aliases.canonical_name`，共 159 種唯一食材

## 實際執行成功

### PostgreSQL migration

以下兩個 T02 revision 已在實際 PostgreSQL 套用成功：

1. `7b58d1f4b2a0_t02_price_snapshots.py`
2. `a92c6d03e71f_t02_product_matching_v2.py`

升級後確認存在：

- `price_collection_runs`
- `price_quote_snapshots`
- `retailer_product_mappings`
- 快照 `(run_id, canonical_name)` 唯一限制
- 代表商品 `(provider, canonical_name)` 唯一限制

### 首次 `--run-once --limit 3`

實際以公開 PX Mart 搜尋來源執行，run key 為 `pxmart-daily-2026-10-18`。

| 項目 | 結果 |
|---|---:|
| Excel 唯一食材總數 | 159 |
| 本次限制數 | 3 |
| 自動採用 | 3 |
| 估價備選 | 0 |
| 缺值／失敗 | 0 |
| checkpoint | 3 |
| 執行時間 | 4,468 ms |

寫入結果：

| canonical_name | 代表商品 | 價格 | product_id |
|---|---|---:|---|
| 太白粉 | 台糖高級太白粉 300g | NT$22 | `239261-26410077-4710311993415` |
| 雞蛋 | 味全 木崗高品質雞蛋-冷藏白殼蛋(10入)(白) 630公克±30公克 | NT$104 | `244610-21810002-4710063023941` |
| 青蔥 | 青蔥（產銷履歷）100g | NT$33 | `417345-115049-2611504914311` |

資料庫當時建立 1 筆 completed run、3 筆 append-only snapshots 與 3 筆 active mappings。三個 mapping 均記錄 `high` confidence 與 `pxmart-selection-v2` policy version。

### 同日防重

以同一個 `pxmart-daily-2026-10-18` 再執行：

- 結果為 skipped；原因是 run key 已存在或 advisory lock 已持有。
- Adapter 未被建立，因此沒有再次呼叫 PX Mart。
- run、snapshot、mapping 數量維持 1、3、3。

這次實際重跑驗證了資料庫 run key 唯一性與已完成工作的防重行為。advisory lock 的程式分支由既有固定 fixture 測試覆蓋；本次沒有把它宣稱為真實多程序競爭測試。

### 可控次日更新

以 `scheduled_for=2026-10-19 03:00 Asia/Taipei`、run key `pxmart-daily-2026-10-19` 執行同三種食材，不需等待實際隔日：

- completed，3 筆自動採用，checkpoint 3，執行時間 4,596 ms。
- 總資料成為 2 筆 runs、6 筆 snapshots、3 筆 mappings。
- 第一日三筆 `selection_method` 為 `scored_search`；第二日皆為 `representative_refresh`。
- 第二日沿用相同 product ID，mapping 的 `last_success_at` 更新，但原始 `selected_at` 保留。
- 快照 `queried_at` 保留真正查詢時間（2026-10-18），沒有偽裝成 2026-10-19 的新鮮價格；排程日期則另由 run 的 `scheduled_for` 表示。

## 排序與成本估算政策檢查

候選只有先通過食材身分、加工狀態、供貨、價格與重量可靠性 gates 後，才會進入排序。排序依序考慮語意分數、重量可靠性，再以標準化單價作 tie-break；因此政策目標是取得可重現且可比較的代表價格，不是無條件選全站最低價。

### 青蔥

- 採用：青蔥（產銷履歷）100g，score 97，NT$33／100g。
- 次候選：青蔥 200g，score 94，NT$29／100g。
- 採用理由：前者語意／品項可信度較高；較低單價不會推翻較高的食材匹配分數。

### 雞蛋

- 採用：木崗冷藏白殼蛋 10 入、630±30g，score 95，約 NT$16.51／100g。
- 其他 score 95 候選單價約 NT$18.89 與 NT$22.22／100g，因此同分時採用最低標準化單價。
- 搜尋中另有約 NT$9／100g 的泛稱雞蛋，但 score 85；不因價格較低而越過食材／規格可信度排序。

兩者的 `selection_reasons` 均已保存在 snapshot JSONB，可追溯 gates、score、重量可靠性與 policy version。

## 實際 PostgreSQL＋固定 fixture 驗證

新增 opt-in 測試 `tests/test_t02_postgres_integration.py`，只有設定 `T02_TEST_DATABASE_URL` 才執行，避免把 SQLite 或離線 SQL 誤稱為 PostgreSQL 驗證。

- 第二筆 snapshot 注入真實 PostgreSQL `NOT NULL` violation：第一筆已提交 snapshot 保留，run 成為 failed；相同 run key 重啟後從 checkpoint 補齊至 3 筆並改為 completed。
- 模擬程序在第一筆 snapshot 後中斷：資料庫保持 running 且 `finished_at IS NULL`，不會誤判成功；重啟後跳過已完成食材並補齊至 3 筆。
- 測試使用唯一 prefix，結束後只清除自身 runs、snapshots、mappings；驗收用實際查價資料仍為 2／6／3 筆。

測試結果：

```text
tests/test_t02_postgres_integration.py   2 passed in 1.09s
tests/test_t02*.py                      34 passed in 1.27s
```

## 模擬測試成功項目

下列項目由 pytest 固定 fixture 或可控時間驗證，不宣稱為真實網站長期運行結果：

- PostgreSQL transaction failure 後的 failed 狀態與續跑
- 程序中斷留下 running run 後的 restart／checkpoint
- 排程時區、03:00 cron 設定、`max_instances=1`
- representative mapping 的失效、替代商品與跨日更新分支
- 包裝解析、缺價、缺貨、schema 變動、逾時與不可填 0

## 尚未驗證與阻礙

- **完整 migration chain：未通過。** 乾淨 PostgreSQL 從頭升級會在 T02 之前的 `c4afb1c5751e_enable_pgvector.py` 因缺少 `vector` extension 停止；該交易 rollback 後 public schema 沒有殘留表。本次只隔離驗證兩個 T02 migration。
- **Docker Compose：未驗證。** 本機沒有 Docker／Docker Compose 命令。
- **159 種 live 全量：未執行。** 本次依驗收要求只查前三種，不能由 3／3 推論完整覆蓋率。
- **正式環境多容器競爭：未驗證。** 本機已驗證同資料庫的 run key 與 advisory lock 邏輯，尚未做真正多容器併發壓力測試。
- **長期來源穩定性與網站使用條件：未驗證。** 真實 PX Mart 公開搜尋在本次少量測試可取得資料；在大規模每日查詢前仍應確認來源條款、限速與營運許可。
- **門市／地區價格代表性：有限。** 未指定門市時，快照不應被宣稱為全台所有門市通用價格。
- **完整專案回歸：本輪無系統 `python` 命令。** 本輪以隔離 Python 執行 T02 測試；沒有把無法啟動的全專案 pytest 宣稱為本輪成功。

## 重現方式

先準備已包含 pgvector 且可拋棄的 PostgreSQL database，再執行：

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE'
alembic upgrade head

python scripts/collect_pxmart_prices.py --run-once --limit 3

$env:T02_TEST_DATABASE_URL = $env:DATABASE_URL
pytest -q tests/test_t02_postgres_integration.py
pytest -q tests/test_t02_pricing.py tests/test_t02_scheduler.py tests/test_t02_postgres_integration.py
```

整合測試會寫入資料，因此 `T02_TEST_DATABASE_URL` 必須指向可拋棄、已完成 migration 的測試 database，不能指向 production。
