# 營養知識來源：健康醫療網（G07）

營養知識 RAG（R12、T11）的正式來源是 [健康醫療網](https://www.healthnews.com.tw/)（decisions #14）。
爬蟲在**團隊電腦**執行，寫入團隊電腦的知識庫（PostgreSQL＋pgvector）。所有抓到的內容一律是 **pending**，
要經過人工審核、再重建索引，才會被檢索和顯示（spec：只用審核通過資料）。

## 授權紀錄（必須由使用者完成）

使用者表示團隊已取得健康醫療網的**書面授權**。repo 裡**沒有**、也不應放入任何假的授權證據。

1. 把書面授權（信件、簽署文件的 PDF 或截圖）存到團隊保存的位置，例如：

   ```
   【待填：授權證據存放路徑，例如 \\team-pc\evidence\healthnews-authorization-2026.pdf】
   ```

2. 抓取時用環境變數 `KNOWLEDGE_AUTHORIZATION_REF`（或 `--authorization-ref`）填這個位置。
   沒填就不能抓取；每筆匯入的知識都會在 `authorization_ref` 欄位記下這個位置，`rights` 欄位也會寫明。
3. T20 驗收時附上授權證據。
4. 轉載文章：文末標示「內容授權提供／某某刊物」，代表內容是其他出版者轉載授權給健康醫療網的。
   爬蟲把供稿者記在 `content_provider`、寫進 `conditions`，並標 `license_status = third_party_unconfirmed`
   （pre-review #8）。這些知識**預設不能核准**：審核者確認團隊的書面授權涵蓋該供稿者的內容後，核准時必須加
   `--confirm-third-party-license`；確認會寫進審核紀錄（`review_note` 前綴「已確認轉載授權涵蓋原始供稿：X」），
   狀態改為 `third_party_confirmed`。不確定就退回（reject，不需要旗標）。`status` 的 `license_holds` 顯示還有幾則待確認。

## 顯示出處（不可移除）

網站使用條款第 7 條規定不得移除著作權聲明。每一則顯示給使用者的知識都會帶：

- `來源：健康醫療網《文章標題》（發布日期）`（台灣日期），以及原文連結（`source_url`）。

資料流：知識庫欄位 `source_name`／`title`／`published_at` → 團隊電腦檢索服務 → 雲端檢索快照
（`backend/app/services/knowledge.py`，外部來源缺標題整份視為無效回應）→ T14 說明引用（`citations[].attribution`、
`source_url`）→ T15 結果 API 與 T16 採用快照 → 前端結果頁與歷史頁。

## 指令

都在 repo 根目錄執行，`PYTHONPATH=backend`（只用來解析 FOOD 字典，不連雲端資料庫）。輸出是 JSON。
錯誤訊息只印例外類型與經遮蔽的內容（不含連線字串、密碼或權杖）。

```bash
export KNOWLEDGE_DATABASE_URL=postgresql://user:pw@127.0.0.1:55433/meal_knowledge   # 團隊電腦本機知識庫
export KNOWLEDGE_AUTHORIZATION_REF='【待填：授權證據存放路徑】'
# 雲端內部 API（crawl --queued、index build、status 的佇列數需要）：值放 deploy/team/.env.team
export CLOUD_INTERNAL_URL=https://cloud-internal.<網域>
export CLOUD_INTERNAL_TOKEN=...            # 與雲端 INTERNAL_API_TOKEN 相同
export CLOUD_ACCESS_CLIENT_ID=... CLOUD_ACCESS_CLIENT_SECRET=...   # 內部 hostname 的 Access 服務權杖

python -m knowledge_rag.admin status
python -m knowledge_rag.admin crawl                              # 營養飲食分類，跟著分頁抓到最後一頁
python -m knowledge_rag.admin crawl --max-pages 5                # 只抓前 5 頁（日常更新用）
python -m knowledge_rag.admin crawl --channel <頻道ID> --channel <頻道ID>
python -m knowledge_rag.admin crawl --ids 69000-70078            # 文章 ID 範圍（新的先抓）
python -m knowledge_rag.admin crawl --queued                     # 雲端待補食材優先（經雲端內部 API）
python -m knowledge_rag.admin classify --dry-run                 # 列出待分類筆數與批數，不花錢
python -m knowledge_rag.admin classify                           # 營養分類＋萃取（gpt-5.4-mini，經雲端內部 API 與 T10 額度）
python -m knowledge_rag.admin classify --limit 50                # 本次最多 50 筆
python -m knowledge_rag.admin classify --reclassify              # 重跑所有現行的待審／已核准紀錄（prompt 或關鍵字改版後）
python -m knowledge_rag.admin classify --apply-rejections        # 把已核准但分類為非營養的紀錄退回（可加 --dry-run 先看）
python -m knowledge_rag.admin list --status pending
python -m knowledge_rag.admin show hn-70049-FOOD000585-1
python -m knowledge_rag.admin approve hn-70049-FOOD000585-1 <版本> --reviewer 王小明 --note "已核對原文與出處"
python -m knowledge_rag.admin approve hn-70050-FOOD000585-1 <版本> --reviewer 王小明 --note "已核對" --confirm-third-party-license  # 轉載文章
python -m knowledge_rag.admin reject  hn-70049-FOOD000585-1 <版本> --reviewer 王小明 --note "只是順帶提到"
python -m knowledge_rag.admin index build --version idx-2026-10-21   # 每批送雲端內部 API 向量化（經 T10 額度）
```

- 頻道：營養飲食是 `f72c8a39-1105-2663-255a-b925ad108bac`。其他頻道 ID 可在分類頁網址看到。
- `--refresh` 會重抓已抓過的文章；內容沒變就略過。

### 抓取規則

- 只抓 `https://www.healthnews.com.tw/`。先讀 robots.txt（目前只禁止 `/rss_file/` 與 PetalBot）。讀不到 robots.txt 就停止。
- 每個請求間隔至少 2 秒（程式不接受更短的間隔），User-Agent 可識別（`overcooked-meal-planner-knowledge/1.0 …`）。
- 429、5xx、連線錯誤會退避重試（5、10、20 秒），最多 3 次。404 代表沒有這篇文章，不重試。
- 不使用站內搜尋（Google CSE）、不抓 Google、不用 sitemap（停在 2015）。
- 分類頁：`/channel/<頻道ID>/` 與 `/channel/<頻道ID>/<頁碼>`，頁數從分頁列讀出。
- 文章頁：`og:title`、`og:url`、`article:published_time`、ld+json（Article）、`#article-content` 裡的 `<p>`。

### 文章怎麼變成知識

- 正文切成段落（相鄰短段落合併，每段最多 400 字）。
- 比對用 FOOD 字典的標準名稱和**無歧義**別名（來源是 `data/recipe_dataset_1017.xlsx`，與雲端 `ingredient_aliases` 同規則），
  加上 `config/ingredient_synonyms.json` 中已確認（confirmed）的同義組。單字名稱不用（例如「蛋」「鹽」「油」「水」「糖」），
  「蛋白質」等營養詞不算食材，名稱重疊時最長的優先（例如「醬油膏」不算「醬油」）。
- **文章層級先篩相關食材**（pre-review #7；一篇真實文章曾比對到約 52 種食材）：
  - 通用詞（食鹽、沙拉油、清水、砂糖、醬油、米酒、太白粉等，見 `healthnews.GENERIC_TERMS`）只有出現在**標題**時才算；
  - 候選超過 5 種時，只留標題提到的食材或全文出現 **2 次以上**者；
  - 依（是否在標題、出現次數、首次出現位置）排序，**每篇最多 8 種**。
- **營養關鍵字前置過濾（決策 #29）**：段落必須同時含食材名稱與任一營養／健康效益關鍵字
  （`config/nutrition_keywords.json`：膳食纖維、維生素、礦物質、蛋白質、熱量、鈣、鐵、鉀、抗氧化、血糖、腸胃、免疫…，可增修）才存。
  只談做法、口感、擺盤的段落（例如春捲內餡「保留清甜與脆度」）不會進知識庫。關鍵字只是粗篩，真正的判斷在下一步分類。
- 段落再比對，只有相關食材各成一筆知識。
- `knowledge_id = hn-<文章ID>-<FOOD編號>-<第幾段>`，`source_version` 是段落 SHA-256 的前 16 碼，
  並保存 `content_hash`、`fetched_at`、`published_at`、`title`、`article_id`、`content_provider`、`authorization_ref`。
- 抓取狀態記在知識庫的 `source_articles`（不存全文）。

### 營養分類與萃取（決策 #29）

流程固定為 **crawl → classify → approve → index build**。

- `classify` 把未分類的現行待審／已核准紀錄，每 10 筆一批送雲端內部 API `/internal/knowledge/classify`；
  雲端以 `gpt-5.4-mini-2026-03-17` Structured Outputs 回傳每筆的 `is_nutrition_knowledge`（是否陳述**該食材**的營養成分或健康效益）、
  `nutrition_fact`（一句繁體中文、只根據原文，例如「高麗菜含膳食纖維，有助腸胃消化」）、`evidence_quote`（原文支持句）與 `reason`。
  每批經 T10 額度預留／結算，呼叫鍵 `knowledge-classify:<run_id>:<批次>`（`run_id` 預設依時間＋亂數產生，重跑不會撞鍵）。
- 團隊電腦寫入前以程式驗證（`knowledge_rag/classify.py`）：`evidence_quote` 必須是原文 excerpt 的連續子字串；
  `nutrition_fact` 必須是一句（≤80 字）、含營養關鍵字，且至少一個關鍵字也出現在 `evidence_quote`。
- 結果寫在 `knowledge_records` 的 `classification`（`nutrition`／`not_nutrition`／`invalid`）、`nutrition_fact`、`evidence_quote`、
  `classification_reason`、`classified_at`、`classifier_model`、`classifier_prompt_version`（`schema.sql` 以 `ADD COLUMN IF NOT EXISTS` 追加，可重複套用）。
- **待審**且非營養或驗證失敗 → 直接退回（`reviewer=classifier`，`review_note` 記原因），紀錄保留。
- **已核准**但分類為非營養 → `classify` 只列在 `approved_non_nutrition`；加 `--apply-rejections` 才退回
  （`reviewer=classifier`，`review_note` 保留原核准者與註記）。退回後立即不再被檢索；重建索引後索引也不含它。
- 模型拒答、截斷或格式不符（`CLASSIFICATION_INVALID_OUTPUT`）：該批列在 `failed_batches`，紀錄維持未分類，下次再跑；
  額度不足、可能已計費、連線失敗則整個指令停止。
- **只有 `classification='nutrition'` 且已核准的現行版本會進索引與被檢索**；`approve-all --dev-bulk` 也只核准分類為營養知識者。
  人工 `approve` 不限制，但未分類的核准紀錄不會進索引。
- 生成的知識說明只把 `nutrition_fact`／`evidence_quote` 給模型（不給整段原文）；說明裡出現的營養素／效益關鍵字必須都能在引用紀錄的
  `nutrition_fact` 或 `evidence_quote` 找到，否則該食材省略（`NOT_GROUNDED_IN_NUTRITION_FACT`）；檢索到但沒有營養知識的食材也省略
  （`NO_NUTRITION_FACT`）。畫面呈現「高麗菜：含膳食纖維，有助腸胃消化」＋「來源：健康醫療網《標題》（日期）」與原文連結。

#### 費用估算（gpt-5.4-mini：輸入 US$0.75、輸出 US$4.50／百萬 token；匯率以 `USD_TWD_RATE=33` 計）

| 項目 | 估算 |
|---|---|
| 每筆輸入 | 片段 ≤400 字 ≈ 500–600 token；每批另含指示與 schema ≈ 700 token |
| 每筆輸出 | ≈ 150–200 token（JSON、營養知識、支持句、理由） |
| 655 筆（66 批） | 輸入 ≈ 0.41M token ≈ US$0.31；輸出 ≈ 0.13M token ≈ US$0.59；合計 **≈ US$0.9（約 NT$30）** |
| 重建索引 | 只含營養知識（< 655 筆）× ≈ 600 token × US$0.02／百萬 ≈ US$0.01，可忽略 |

T10 預留以位元組數當 token 上限（保守），每批約預留 NT$1–2，結算後依實際用量釋放差額。
`classify --dry-run` 會列出待處理筆數與批數，執行前可先確認。

### 重抓與版本

- 段落內容沒變：不新增。
- 段落內容變了：匯入新版本（pending）。舊的已審核版本保留在歷史中、**不會被覆寫或自動核准**。
  依 T11 規則，新版本審核前這則知識暫停提供（不顯示未審核的新內容，也不讓舊內容冒充已改過的原文）。
  `status` 的 `approved_awaiting_new_version_review` 會顯示有幾則在等審核；審核後要重建索引。

## 生成後查詢所用食材（補抓佇列）

雲端生成結果確定最終食材後，若某食材在知識快照中是 `not_found`（沒有審核通過的知識），
就把它的 FOOD 編號記到雲端表 `knowledge_crawl_requests`（migration `e2b7c4a9f310`）。

- 只記 FOOD 編號、次數、時間；不記家庭、使用者或問卷。
- 知識服務不可用時不記（不知道是否真的缺）。
- 記錄失敗只寫一行警告，**不會阻擋或延誤生成**。
- 團隊電腦執行 `crawl --queued`：依需求次數取待補食材，掃分類頁，標題提到該食材（含別名）的文章先抓，
  其餘未抓過的文章排後面（預設最多 20 頁、30 篇），結束後把食材標為已處理並記下抓到的 pending／approved 數。
  已處理的食材 7 天內再被要求只累加次數，不重新排入。
- 抓到的內容一樣要審核＋重建索引才會顯示。

### 團隊電腦不連雲端資料庫（pre-review #6）

`crawl --queued`、`classify`、`index build` 與 `status` 的佇列計數都經**雲端內部 API**（`backend/app/api/internal_knowledge.py`），
團隊電腦**不需要也不可設定**雲端 `DATABASE_URL`（`ops.preflight team` 視為錯誤），也不持有 `OPENAI_API_KEY`。

| 方法 | 路徑（雲端後端） | 用途 |
|---|---|---|
| POST | `/internal/knowledge/crawl-requests/claim` | `{limit}` → 認領待補食材（需求次數多者優先）；認領 2 小時未回報可再被認領 |
| POST | `/internal/knowledge/crawl-requests/complete` | `{outcomes: {FOOD編號: {pending, approved}}}` → 標為已處理 |
| GET | `/internal/knowledge/crawl-requests/summary` | 待處理數 |
| POST | `/internal/knowledge/embeddings` | `{index_version, batch_no, texts}` → 雲端經 T10 額度預留後呼叫 OpenAI，回傳向量（呼叫鍵 `knowledge-index:<版本>:<批次>`，重建請用新版本） |
| POST | `/internal/knowledge/classify` | `{run_id, batch_no, records: [{knowledge_id, source_version, ingredient_name, excerpt}]}`（每批 ≤20）→ 雲端經 T10 額度以 gpt-5.4-mini 分類與萃取（呼叫鍵 `knowledge-classify:<run_id>:<批次>`）；格式不符回 502 `CLASSIFICATION_INVALID_OUTPUT` |

驗證與 T17 雲端→團隊電腦相同的兩層，方向相反：
1. Cloudflare Access：雲端 tunnel 另加內部 hostname（例如 `cloud-internal.<網域>` → `http://backend:8000`），
   建 Access 應用程式、Policy 只允許**團隊電腦專用**的服務權杖（`CLOUD_ACCESS_CLIENT_ID`／`SECRET`）。
2. Bearer：`Authorization: Bearer <INTERNAL_API_TOKEN>`（團隊端 `CLOUD_INTERNAL_TOKEN`），常數時間比對；
   雲端未設定 `INTERNAL_API_TOKEN` 時整組 API 回 503（關閉）。

公開網站（`PUBLIC_HOSTNAME`）的前端 Caddy 對 `/api/internal/*` 一律回 404，瀏覽器路徑到不了內部 API。
額度不足（`SERVICE_BUDGET_EXHAUSTED`）、確定未計費（`EMBEDDING_UNAVAILABLE`）、可能已計費（`BILLING_UNCERTAIN`）、
tunnel 斷線（`UNREACHABLE`）、Access 拒絕（`AUTH_FAILED`）都是明確錯誤，不會被當成「沒有待補食材」。

## 建議排程（團隊電腦）

| 頻率 | 指令 |
|---|---|
| 每天一次 | `crawl --max-pages 3`（新文章） |
| 每天一次 | `crawl --queued`（經雲端內部 API） |
| 抓取後 | `classify`（只處理新進未分類的紀錄） |
| 審核後 | `index build --version idx-<日期>`（每次用新的版本名稱） |
| 每週一次（選用） | `crawl --max-pages 20 --refresh`（檢查文章是否被修改） |

Windows 可用「工作排程器」執行，例如：
`cmd /c "cd /d D:\overcooked && set PYTHONPATH=backend && .venv\Scripts\python.exe -m knowledge_rag.admin crawl --max-pages 3"`。
第一次完整抓取（約 145 頁、上千篇文章）以每 2 秒一個請求計算需要約 1 小時以上，請在離峰時間執行。

## 本機開發環境一鍵流程（決策 #23、#24）

`scripts/start-dev.ps1` 會：在同一個 dev 資料庫容器建立 `<DEV_DB_NAME>_knowledge`（預設 `meal_planner_dev_knowledge`）並套用
`knowledge_rag/schema.sql`（可重複執行）；第一次執行時在 `.env.dev` 產生 `KNOWLEDGE_SERVICE_TOKEN`、`INTERNAL_API_TOKEN`
（隨機值，不顯示）；啟動 `[knowledge]`（`python -m knowledge_rag.server`，`127.0.0.1:8766`），並把
`KNOWLEDGE_SERVICE_URL`／`KNOWLEDGE_SERVICE_TOKEN` 傳給後端與 worker。不需要時設 `DEV_SKIP_KNOWLEDGE=1`。

`scripts/dev-run.ps1` 會替管理指令設好 `KNOWLEDGE_DATABASE_URL`、`CLOUD_INTERNAL_URL=http://127.0.0.1:8000`（本機後端當「雲端內部 API」）、
`CLOUD_INTERNAL_TOKEN`（＝`INTERNAL_API_TOKEN`）與 `APP_ENV=development`。開發環境不需要 Cloudflare Access 權杖。

事前要由人工提供（`.env.dev`）：

- `KNOWLEDGE_AUTHORIZATION_REF`：書面授權證據的存放位置（見上方「授權紀錄」）；沒填 crawl 會拒絕。
- `OPENAI_API_KEY`：建索引與生成時的查詢向量化都要（經 T10 額度預留）。

start-dev 執行中（後端要在線，index build 經本機內部 API 向量化），另開一個 PowerShell：

```powershell
# 1. 抓取營養飲食分類（每次請求 ≥2 秒；先用 --max-pages 5 試，之後再拿掉抓全部）
.\scripts\dev-run.ps1 -m knowledge_rag.admin crawl --max-pages 5
# 2. 營養分類與萃取（決策 #29；經本機後端內部 API 呼叫 gpt-5.4-mini，T10 額度控制）；先 --dry-run 看筆數
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify --dry-run
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify
# 3. 僅限開發：整批核准「分類為營養知識」的待審非轉載紀錄（reviewer=dev-bulk；未分類與轉載文章一律排除，
#    要用轉載文章仍須逐筆 approve --confirm-third-party-license）。APP_ENV 不是 development（例如 production）時拒絕執行
.\scripts\dev-run.ps1 -m knowledge_rag.admin approve-all --dev-bulk --reviewer dev-bulk --note "開發環境整批核准，未逐筆人工審核"
# 4. 建索引並啟用（版本名稱每次要新的；只含已核准的營養知識）
.\scripts\dev-run.ps1 -m knowledge_rag.admin index build --version idx-dev-2026-10-21
# 5. 確認：active_index 有值、records.approved > 0、classification 計數
.\scripts\dev-run.ps1 -m knowledge_rag.admin status
```

**既有資料重新清整（決策 #29 之前已用舊版整批核准的開發庫）**：

```powershell
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify --dry-run                       # 待分類筆數
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify                                 # 分類＋萃取；待審的非營養直接退回
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify --apply-rejections --dry-run    # 列出會被退回的已核准紀錄
.\scripts\dev-run.ps1 -m knowledge_rag.admin classify --apply-rejections              # 退回（reviewer=classifier）
.\scripts\dev-run.ps1 -m knowledge_rag.admin approve-all --dev-bulk --reviewer dev-bulk --note "決策 #29 重新清整後整批核准"
.\scripts\dev-run.ps1 -m knowledge_rag.admin index build --version idx-dev-<新版本>  # 重建索引（新版本名）
.\scripts\dev-run.ps1 -m knowledge_rag.admin status
```

重建索引後，舊結果的知識說明不會自動更新；請重新生成，或對舊結果按「只重試知識說明」。

確認生成有知識：

1. 知識服務健康檢查應回 200 與 `index_version`（沒有 active 索引時是 503 `NO_ACTIVE_INDEX`，生成會顯示知識不可用）：
   ```powershell
   . .\scripts\dev-env.ps1; $c = Read-DevConfig .env.dev
   Invoke-RestMethod http://127.0.0.1:8766/v1/health -Headers @{ Authorization = "Bearer $($c.KNOWLEDGE_SERVICE_TOKEN)" }
   ```
2. 在 http://localhost:5173 重新生成一份菜單（舊結果不會自動更新；知識不可用的舊結果可按「只重試知識說明」）。
   結果的知識說明應為可用、每則顯示「來源：健康醫療網」＋原文連結；API 回應的 `knowledge.status` 是 `available`
   （食材剛好沒有已核准知識時是 `not_found`，不是 `unavailable`）。

`approve-all` 只為了讓開發環境能看到知識；正式環境仍須逐筆人工審核（spec：只用審核通過資料）。
## 限制

- 一篇文章最多對應 8 種相關食材（見上），但仍可能有順帶提到的；只是順帶提到的請退回。
- 比對只看名稱，不理解語意（例如「不建議吃」也會比對到）。審核時要看片段內容。
- 營養分類由 gpt-5.4-mini 判斷，可能誤判；程式只能驗證支持句出自原文、營養知識含關鍵字，無法保證醫學正確性。
  開發環境的整批核准未經人工逐筆審核；正式環境仍須人工核對 `nutrition_fact` 與原文。
- 關鍵字前置過濾可能漏掉用詞不在清單上的營養段落（例如只寫「顧眼睛」）；可增修 `config/nutrition_keywords.json` 後重抓（`--refresh`）。
- 說明的依據檢查以關鍵字比對：說明用了 nutrition_fact 沒有的同義詞（例如原文「消化」、說明寫「蠕動」）也會被省略，寧可少說不亂說。
- 同名的不同 FOOD 編號（例如工作簿中兩筆「雞蛋」）會各得一筆。
- 文章被刪除或段落變少時，舊知識不會自動下架；需要人工退回新版本或在審核時處理。
- 分類頁列表的標題可能被截斷（`...`），佇列的標題比對只是優先順序，正文比對才決定知識。
- 網站結構改版會讓解析失敗（抓不到正文就當作沒有文章）；改版後請更新 `tests/fixtures/healthnews/` 的合成範本與解析器。

## 測試

`tests/test_kn_*.py`：合成 HTML 範本（`tests/fixtures/healthnews/`，內容是虛構的，不是真實文章）與假傳輸，
不連網、不呼叫付費 API。需要 `TEST_DATABASE_URL`；知識庫測試會建立 `<測試庫>_knowledge`。
