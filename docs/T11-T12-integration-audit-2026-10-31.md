# T11／T12 與最新 main 整合重驗

> 2026-11-01 更新：下文保留 10-31 當時的稽核快照；主食缺口已由後續提交修正。白飯明確選擇後使用 FOOD000296／A0550601、每 1n 160 克起始及 100～250 克調整，計入整餐營養並保留設定快照；缺選擇或缺營養會阻止通過。T12 現為 93 passed、合併覆蓋率 85%；T11／T03／T12 相關回歸 143 passed、90 subtests，Pyright 0 錯誤，Docker build 成功。正式問卷 API 接線、畫面缺值提示、完整新版 TFDA 原檔逐筆核驗和最終採用交易仍未完成，因此 PR #18 繼續保持草稿。

> 2026-11-01 實檔補查：使用者提供的 `C:/Users/BOSS/Downloads/All_Food_ID_1018.xlsx` SHA-256 為 `b1ccb1b9d9e89c559580571af8018608b442919df4136ed61cf737e928a6dd5f`，與本分支 `knowledge_rag/sources/All_Food_ID_1018.xlsx` 相同，仍不同於 main／PR #17 記錄的 `cb0c0f938a26fdd1258cb05b52e3a460d4f146be259048e55d0b7e63e600a959`。此檔 All 第 974 列為 FOOD000296「白飯」，已熟製、電鍋煮，183 kcal／3.1 g 蛋白質每 100 g，計算來源 TFDA_A0550601；「原始來源與判定」第 75 列來源資料 ID A0550601 數值及狀態一致，糖欄留空。All 另有第 1732 列同 FOOD ID 的未判定、食譜推算紀錄，不能取代上述 TFDA 樣品。實檔涵蓋食譜使用的 434 個 FOOD ID，但此檔 1,996 筆 All 紀錄有 312 筆熱量空白、30 筆蛋白質空白、666 筆糖空白；TFDA 原始來源 2,213 筆中蛋白質空白 30 筆、糖空白 1,001 筆。白飯來源方式已納入 T12 回歸，選錯方式會擋下。以上是所提供檔案的觀察值，不能充作 PR #17 新版雜湊的正式驗收。

## 結論與範圍

本輪尚未合併至 main。整合分支 `codex/integrate-t11-t12-audit` 包含 main `007e51d`（含 PR #17）、T11 `9a76f9f`、T12 `801470b`，解決 README／spec 的文字衝突，保留 main 的 YOLO26s 與 T11 地端知識模型決議。沒有使用 `t11-t20-demo`，沒有更新其他人的分支。

使用 implement、tdd、code-review 流程；本輪兩項修正為部署封裝與合併遷移，未修改營養算法或新增公開介面。既有公開計算介面重跑回歸；兩軸審查再次複核修正。

PR #17 明確記載新版 Excel **僅在其作者本機讀取，沒有提交或上傳到 GitHub**，並且未匯入正式庫。該 PR 的匯入成功與筆數為前次驗證紀錄，不是本輪逐筆重驗證據。參見 https://github.com/yun112064/overcooked/pull/17 。

## 圖片六項問題

| 問題 | 重驗判定 | 證據與限制 |
|---|---|---|
| float 造成 ±10% 邊界錯誤 | T12 精確計算路徑已處理 | targets.exact 拒絕 float；service.check_tolerance 使用 Fraction 的 9/10、11/10，兩項各自含邊界，取烹調單位後重算。通過 test_acceptance 的邊界及取整案例。這不是宣稱所有上游 float 都已消除。 |
| 434 種僅 93 種有營養 | 舊結論不能套用新版，適用性仍未完成核驗 | 93 是舊 JSON 的明確 TFDA 對映數。PR #17 記錄 434 個食譜 Food_ID 均有營養紀錄，但只有 742/1996 筆七項完整；有紀錄不等於正確狀態、唯一 TFDA 樣品或必要營養完整。T12 新路徑必須明確選擇版本與來源。 |
| 未註明／複合生熟狀態不能計算 | 仍會安全阻擋，沒有自動補推定 | imported_nutrition.STATES 只將明確的未熟製、已熟製轉為 raw/cooked。其餘回報 nutrition_state_unknown。需逐筆確認或更換有依據的樣品，不能為增加覆蓋而猜。新版受影響筆數尚未實測。 |
| 糖、油、醋的蛋白質空白 | 缺值處理正確，資料補齊狀態未確認 | NULL 保持 None 並阻擋，明確的數字 0 可使用。不能以食物類別推定空白等於 0。圖片提及 #32/#33/#45 的原決議文字未提供，不推定它們授權了補值。 |
| 每道菜各自對齊整餐、湯無法達標 | T12 算法已按整餐；實際可調資料仍缺 | align_baseline 先以各食譜 original_servings 歸一，再對所有食材合計、對齊一次，最後乘 N。並未要求每碗湯達到整餐600/30。main 的 recipe_dataset_1017.xlsx 有2403筆非空食材明細，可調上下限均未填、adjustment_status 均空白；不能捏造已審核範圍。 |
| 主食未算入營養 | 尚未解決跨 Ticket 接線 | T11 有白飯等額外主食時排除主食類候選，selected/alternatives 沒有主食重量與食材；T12 只讀取傳入 recipe_ids，無法偵測未傳來的主食。需要明確的完整菜單／主食合約及缺席阻擋。 |

## 本輪完成修正

- 新增 `b72e9c014af6` 合併遷移，同時依賴營養 `0d52b9a6c831` 與 T11 `a1c4e7f20b39`，保留兩個既有 migration 原文。修正前 `alembic heads` 有兩個終點，修正後唯一 head 為 b72e9c014af6。
- Dockerfile 納入 nutrition_planning 與 nutrition_model，配合既有 PYTHONPATH=/app。實際建置映像並在無網路容器成功匯入 validate_database_menu 與 food_id_mapping。
- 新建隔離 PostgreSQL 容器驗證，未使用原有 ingredient-rag-db-1 或正式資料庫。

## 測試及人工對照

| 驗證 | 結果 |
|---|---|
| pytest tests nutrition_target/tests nutrition_planning/tests | 251 passed，98 subtests passed，1項既有 Starlette/httpx 棄用警告；使用新建隔離 PostgreSQL |
| T12 單獨 pytest | 88 passed |
| T12 覆蓋率 | 行817/931＝87.76%；分支290/386＝75.13%；工具行與分支合計84.05% |
| T04 unittest discover | 68項：66通過、2略過（可選pandas相關），未把略過計為通過 |
| pyright nutrition_planning/pyrightconfig.json | 0 errors、0 warnings |
| Alembic 空庫 upgrade head | PostgreSQL 成功，包含兩條分支及合併遷移；唯一 head b72e9c014af6 |
| 後端 Docker build／無網路匯入 | 成功；未部署網站 |
| T11 知識 JSON dry-run | 123筆格式通過，未寫庫、未呼叫 embedding；不代表正式檢索驗收通過 |
| git diff --check | 通過 |

初次擴大 pytest 時未設定 DATABASE_URL／PROJECT_DATA_ROOT，7項 synonyms 測試因本機環境設定失敗；補上隔離設定後7項全過，後續完整251項也通過。此為環境問題，未更改 synonyms 程式。

人工算術對照：600/30 乘0.7、0.85、1、1.15、1.3，依序為420/21、510/25.5、600/30、690/34.5、780/39。最後一組的容差是熱量702～858、蛋白質35.1～42.9，各自判斷。100g含600/30時，n=1.3 原應130g；40g步長取120g得到720/36，通過；80g步長取160g得到960/48，必須失敗。對應 test_acceptance。

多人／便當案例對應 test_targets：成人晚餐1.3、同成人午餐便當1.3、兒童晚餐0.7，N=3.3；目標2043.53067828/78.694，精確基準為17029422319/27500000與3577/150。保留三個參與項目，沒有把同人成員的便當漏掉。

T11 公開 retrieve_database_menu 的人工 SQL 案例在隔離庫執行，資料全部交易回滾：

- 有一份白飯及一道主菜時，status=database_menu，selected 只有主菜；沒有白飯數量或營養。
- 同一道主菜有兩個合格食譜版本時，candidates 有兩版，alternatives 只含其中第一版。若第一版缺營養而第二版完整，下游只巡 alternatives 可能漏掉可用版本。
- 指定兩道湯且 dish_count=2 時，status=database_menu，selected 為兩道湯，沒有主菜。是否允許指定菜例外須依團隊決策 #22 原文確認，不能僅靠函式註解當正式規則。

## Standards

未找到 AGENTS、CODING_STANDARDS 或 CONTRIBUTING 的額外規範。獨立審查指出主食合約、候選版本枚舉及 T11 測試不足；Docker 缺套件已修正並複審。未發現兩項新修正的硬規範違反；不把啟發式 code smell 當硬違規。

## Spec

R06／AC10–12 的算術及邊界有單元證據；R07 的完整菜單尚缺主食接線。雙 migration head 已修正。main 新的 t04_database_nutrition 是所選 All 彙整觀測的縮放器，與 T12 接收的舊 T04 烹調係數收據並非相同合約，不能直接交換；T12 目前從匯入表選原始 TFDA，再以舊 T04 烹調核心及收據驗證，沒有把 All 混合值冒充 TFDA。尚缺由正式呼叫端完成的實際接線、逐列選擇及完整流程測試。

兩軸結果：Standards 有3項未完成的整合／測試風險；Spec 有2項未完成的整合問題（主食、T04呼叫端合約），另有實際新版資料驗證待完成。兩項本輪修正均通過兩軸複審，不能據此宣稱全案通過。

## 合併前仍需完成

1. 明確帶入主食及完整菜單的食材、重量、生熟依據；不能讓僅有菜色的結果宣稱整餐通過。
2. 補 T11 檢索的可重現回歸，處理同菜不同版本的備選枚舉；釐清指定菜占滿菜數時的結構規則。
3. 以 PR #17 作者本機的三份原檔，或其隔離 SQLite 匯出，實際跑 T12 原始 TFDA 來源／狀態／缺值核驗。不要將 PR 中的「434個都有紀錄」當作「434個都能採用」。
4. 確認可調範圍、非克單位、生熟及可食部／烹調資料；必要營養採用政策、G03/G04及正式呼叫端仍待完整驗收。
5. 整合分支 GitHub CI 與 T11 實際知識服務檢索尚待驗證。此次251項不包含不存在於此分支的 T11 檢索測試，也不包含網站端到端。

本機可用的 All_Food SHA256 為 b1ccb1b9d9e89c559580571af8018608b442919df4136ed61cf737e928a6dd5f，與main文件記載新版 cb0c0f938a26fdd1258cb05b52e3a460d4f146be259048e55d0b7e63e600a959 不同；不以舊檔推定新版欄位。食譜檔 SHA256 為6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304，與main文件一致。

2026-10-31 使用者另提供 `C:/Users/BOSS/Downloads/recipe_dataset_1017.xlsx`，其 SHA256 同為 6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304，與專案已追蹤的食譜檔完全相同。直接讀檔核對：2,403 筆食材列、434 個不同 Food_ID、所有列都有 Food_ID；1,925 列單位不是 g，雖然 2,403 列都有 `weight_g`，仍不能由此認定非 g 的換算已審核。全部可調上下限缺席。

食譜來源欄 `protein_g` 只有 4 列空白：Excel 2304列 R0277 其他香料、2309列 R0279 餃子皮、2321列 R0280 水餃皮、2403列 R0293 泡蝦米水。糖、油、醋相關食譜列在這一欄沒有空白，部分是明確 0；但這是食譜來源欄，不是新版 `All_Food_ID_1018.xlsx` 的 TFDA 原始來源欄，不能拿來解除 TFDA 缺值阻擋，也不能把食譜來源 0 推定為 TFDA 實測 0。
