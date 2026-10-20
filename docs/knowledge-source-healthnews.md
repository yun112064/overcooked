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
- 段落再比對，只有相關食材各成一筆知識。
- `knowledge_id = hn-<文章ID>-<FOOD編號>-<第幾段>`，`source_version` 是段落 SHA-256 的前 16 碼，
  並保存 `content_hash`、`fetched_at`、`published_at`、`title`、`article_id`、`content_provider`、`authorization_ref`。
- 抓取狀態記在知識庫的 `source_articles`（不存全文）。

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

`crawl --queued`、`index build` 與 `status` 的佇列計數都經**雲端內部 API**（`backend/app/api/internal_knowledge.py`），
團隊電腦**不需要也不可設定**雲端 `DATABASE_URL`（`ops.preflight team` 視為錯誤），也不持有 `OPENAI_API_KEY`。

| 方法 | 路徑（雲端後端） | 用途 |
|---|---|---|
| POST | `/internal/knowledge/crawl-requests/claim` | `{limit}` → 認領待補食材（需求次數多者優先）；認領 2 小時未回報可再被認領 |
| POST | `/internal/knowledge/crawl-requests/complete` | `{outcomes: {FOOD編號: {pending, approved}}}` → 標為已處理 |
| GET | `/internal/knowledge/crawl-requests/summary` | 待處理數 |
| POST | `/internal/knowledge/embeddings` | `{index_version, batch_no, texts}` → 雲端經 T10 額度預留後呼叫 OpenAI，回傳向量（呼叫鍵 `knowledge-index:<版本>:<批次>`，重建請用新版本） |

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
| 審核後 | `index build --version idx-<日期>`（每次用新的版本名稱） |
| 每週一次（選用） | `crawl --max-pages 20 --refresh`（檢查文章是否被修改） |

Windows 可用「工作排程器」執行，例如：
`cmd /c "cd /d D:\overcooked && set PYTHONPATH=backend && .venv\Scripts\python.exe -m knowledge_rag.admin crawl --max-pages 3"`。
第一次完整抓取（約 145 頁、上千篇文章）以每 2 秒一個請求計算需要約 1 小時以上，請在離峰時間執行。

## 限制

- 一篇文章最多對應 8 種相關食材（見上），但仍可能有順帶提到的；只是順帶提到的請退回。
- 比對只看名稱，不理解語意（例如「不建議吃」也會比對到）。審核時要看片段內容。
- 同名的不同 FOOD 編號（例如工作簿中兩筆「雞蛋」）會各得一筆。
- 文章被刪除或段落變少時，舊知識不會自動下架；需要人工退回新版本或在審核時處理。
- 分類頁列表的標題可能被截斷（`...`），佇列的標題比對只是優先順序，正文比對才決定知識。
- 網站結構改版會讓解析失敗（抓不到正文就當作沒有文章）；改版後請更新 `tests/fixtures/healthnews/` 的合成範本與解析器。

## 測試

`tests/test_kn_*.py`：合成 HTML 範本（`tests/fixtures/healthnews/`，內容是虛構的，不是真實文章）與假傳輸，
不連網、不呼叫付費 API。需要 `TEST_DATABASE_URL`；知識庫測試會建立 `<測試庫>_knowledge`。
