# 驗證指南

本文件是各功能分支共用的驗證範圍與執行方式，不是特定提交的測試通過證明。變更交付時應說明實際受測提交、執行項目、失敗、略過與未驗證部分；本機路徑、操作日誌、環境識別及排錯過程不納入交付文件。

## 分支相依順序

依 T01_V4 → T02_V4 → T13_V3 → Sim_Supermarket_V1 → Admin_dashboard_V2 整合。T02 使用 T01 的管理與來源契約，T13 使用 T02 的審核價格服務；超市與 Dashboard 分別使用共同的容量及價格介面。

合併檢查只針對已提交內容。工作樹中的未提交修改不會自動納入驗證或合併；更新任一分支或整合基底後，應重新檢查差異與相依關係。

## 測試環境

- 一般測試使用合成 SQLite、工作簿、報價及模擬外部服務，不執行真實爬蟲或付費模型。
- PostgreSQL 測試只使用明確可拋棄、已核對名稱及標記的隔離測試庫。需要 pgvector 的測試應使用具備該擴充的 PostgreSQL。
- 測試可能重建 schema、清空資料或執行降版。正式 V3、V1 及匯入後的執行庫不可作為測試清理目標。
- `TEST_DATABASE_URL`、`T02_TEST_DATABASE_URL`、`MANAGEMENT_TEST_DATABASE_URL` 只在隔離庫驗證完成後設定。部分測試另檢查固定連接埠、資料庫名稱與 comment，應以該測試的前置條件為準。
- 不把歷史來源核准繼承為新核准，不以零或推定換算填補未知資料。

## 一般回歸

在已安裝開發依賴的專案根目錄，以 PowerShell 執行合成資料回歸：

```powershell
Remove-Item Env:TEST_DATABASE_URL, Env:T02_TEST_DATABASE_URL, Env:MANAGEMENT_TEST_DATABASE_URL -ErrorAction SilentlyContinue
$env:DATABASE_URL = 'sqlite://'
$env:PYTHONPATH = 'backend;tests;.'
python -m pytest tests nutrition_planning/tests -q -ra
```

未設定隔離 PostgreSQL 時，資料庫專用案例會略過；缺少歷史資料包的案例也可能略過。這些結果必須列明，不可當作通過。上述命令不會提供 PostgreSQL 遷移驗證。

前端改動應執行 `npm --prefix frontend run lint` 與 `npm --prefix frontend run build`。超市分支另在 `frontend` 目錄執行 `node --test tests/simMarket.test.mjs`。依各分支實際存在的測試檔案選擇驗證項目。

## 功能驗收範圍

| 範圍 | 必須確認的行為 |
|---|---|
| T01 食譜與候選 | 原始單位及來源證據保留；容量不自動補克重；缺少重量或審核證據時仍阻擋相應操作 |
| T02 價格 | 商品、食材與發布版本綁定；效期及條件檢查；已核准人工價優先；替換既有人工價必須明確指定 |
| T13 成本 | L／mL 精確換算；用量成本與整包實付分開；相容需求先合併再算包數；無可靠換算的混合需求保持待確認 |
| Sim’s Supermarket | 採用後的缺料可進入模擬結帳；購物車按使用者、家庭、餐單及資料版本隔離；不產生真實交易 |
| Dashboard | 權限與審核流程有效；重量／容量切換清理不相容欄位；來源用量與未知克重清楚呈現 |
| 歷史與營養 | 價格更新不改已採用快照；容量價格估算不取代營養的克重及證據要求；不自動繼承 T04 核准 |

## PostgreSQL 與介面驗收

隔離資料庫應實際驗證從空庫升版、降版後重新升版、Alembic 結構檢查，以及舊 Admin schema 的相容遷移。以合成歷史餐單核對遷移前後的快照，檢查管理初始資料、權限、發布及啟用狀態，避免清理測試資料時遺失必要初始列。

瀏覽器驗收涵蓋食譜審核、價格單位切換、採用後加入購物車、包數調整、重開與重新整理、家庭及資料版本隔離。模擬 API 的瀏覽器測試與實際 PostgreSQL 測試應分別報告；兩者通過不等於已驗證連接真實後端的完整瀏覽器流程。

文件變更只需核對內容、差異與合併結果；沒有重新執行的功能測試，不宣稱再次通過。Git 可自動合併也不代表功能或正式部署已通過驗收。
