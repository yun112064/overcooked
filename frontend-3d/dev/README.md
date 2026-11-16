# 假後端（`vite --mode fake`）

`npm run dev:fake` 會用這個資料夾裡的假後端取代真的 API，整個 App 不需要資料庫、背景工作程序或團隊電腦就能操作。

- 資料**全部是合成的**（食材數值、價格、食譜、知識出處都是示範內容），存在記憶體裡，**重啟開發伺服器就回到種子資料**。
- 不連任何資料庫、模型或外部服務；不發 Cookie，整個開發伺服器只有**一個**登入狀態（開兩個分頁是同一個人）。
- 路徑、方法、請求內容、狀態碼與錯誤格式 `{ "error": { "code", "message", "details" } }` 照 `backend/app/api/*.py`；回應的形狀對 `src/api/types.ts` 做型別檢查。

## 結構

| 檔案 | 內容 |
|---|---|
| `fakeApi.ts` | Vite 外掛：只把 HTTP（JSON、multipart、SSE）接到 `handle()` |
| `fake/router.ts` | `handle(state, request)`：所有端點的分派；純函式，測試直接呼叫 |
| `fake/seed.ts` | `createState({ now })`：建立狀態與種子資料（時鐘可注入） |
| `fake/catalog.ts` | 合成的標準食材字典（67 種）、食譜庫（47 道）、食材小知識 |
| `fake/requests.ts` | 成員與問卷：輸入檢查、完整性、限制、回應格式 |
| `fake/nutrition.ts` | 假的每日目標、整餐用量、每人份量、每日建議 |
| `fake/menu.ts` | 配菜、昨日去重、資料庫菜單、營養預覽、小知識 |
| `fake/results.ts` | 生成工作（依時間推進）、生成結果、採用資格、決定、重試 |
| `fake/plans.ts` | 採用（兩條路）、已採用紀錄、趨勢、一鍵購入 |
| `fake/extras.ts` | 飲食小幫手、蔬菜辨識、營養來源回饋 |
| `fakeApi.test.ts` | 直接呼叫 `handle()` 的流程測試（`npx vitest run dev`） |

在測試或腳本裡使用：

```ts
import { handle } from './fake/router.ts'
import { createState } from './fake/seed.ts'

let clock = Date.now()
const state = createState({ now: () => clock })       // seed: false 可以拿到空的狀態
handle(state, { method: 'POST', path: '/auth/login', query: {}, headers: {}, body: { email: 'a@example.com', password: 'password123' } })
clock += 10_000                                        // 快轉 10 秒，下一次呼叫時工作就完成了
```

背景工作沒有計時器：每次處理請求前，依「送出後經過的時間」把所有工作推進到該有的狀態。

## 端點

路徑都在 `/api` 底下。沒登入回 `401 UNAUTHENTICATED`；需要家庭的端點在還沒選家庭時回 `409 HOUSEHOLD_NOT_SELECTED`。

### 登入、家庭、健康檢查

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/auth/login` | `{ email, password }` → `Me`。只有一個家庭時直接選好 |
| POST | `/auth/register` | 201 → `Me`（沒有家庭）。密碼少於 8 字 422；Email 已註冊 409（`details.reason = EMAIL_TAKEN`） |
| POST | `/auth/logout` | 204 |
| GET | `/auth/me` | `Me` |
| GET | `/household` | `{ households, current_household_id }` |
| POST | `/household` | `{ name }` → 201，建立後直接成為目前家庭 |
| POST | `/household/select` | `{ household_id }`；不是自己的家庭 404 |
| GET | `/health`、`/health/db` | 不需登入 |
| GET | `/health/team` | `recognition`／`knowledge`／`assistant`（在線）、`assistant_openai`（未設定） |

### 成員與食材（需要家庭；`/ingredients` 只需登入）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/members`、`/members/options`、`/members/{id}` | |
| POST | `/members` | 201。規則與後端相同：出生日不能晚於今天、食量只能是 0.7／0.85／1／1.15／1.3、未滿 19 歲只能選一般餐食、減脂後每日熱量低於 1200 大卡會被拒絕、過敏／禁吃必須是字典裡的食材（都回 422 並附 `details.field`／`reason`） |
| PUT | `/members/{id}` | 內容 + `version`；版本不同 409 `VERSION_CONFLICT` |
| DELETE | `/members/{id}` | 204 |
| GET | `/members/{id}/target` | 今日每日基準 |
| GET | `/ingredients?q=&limit=` | 名稱與別名搜尋（`limit` 1–50，預設 20） |

### 問卷、菜單、工作

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/meal-requests` | 最近 50 筆摘要 |
| POST | `/meal-requests` | 內容可以只填一部分（例如只有 `meal_date`）；201 → `MealRequest` |
| GET | `/meal-requests/staple-options` | 白飯、糙米飯、不指定；`none_value` 是「不需要主食」 |
| GET | `/meal-requests/{id}` | 含 `issues`、`restrictions`、`targets_preview`、`budget_floor` |
| PUT | `/meal-requests/{id}` | `{ version, draft }`。生成中 409（`GENERATION_IN_PROGRESS`）；內容沒變不升版；已採用 409（`ALREADY_ADOPTED`）；版本不同 409；成功後升版並回到 `draft` |
| DELETE | `/meal-requests/{id}` | 204；生成中 409 |
| POST | `/meal-requests/{id}/submit` | `{ version }` + `Idempotency-Key`（8–100 字，缺了回 422）→ 202 `{ job, created }`。同鍵同版本回同一個工作；沒填完 422（`INCOMPLETE`） |
| POST | `/meal-requests/{id}/cancel` | `{ job, meal_request }`；沒有進行中的生成 409（`NOTHING_TO_CANCEL`） |
| GET | `/meal-requests/{id}/recipes` | 資料庫菜單 `RecipeRetrieval`；問卷沒填完 422 |
| POST | `/meal-requests/{id}/menu-rejections` | `{ version, recipe_ids }` → 204；之後 `/recipes` 改給下一組，最多 5 組，換完是 `all_menus_rejected` |
| POST | `/meal-requests/{id}/nutrition-preview` | `{ recipe_ids }` → `NutritionPreview` |
| POST | `/meal-requests/{id}/knowledge-tips` | `{ recipe_ids }` → `KnowledgeTips` |
| GET | `/meal-requests/{id}/recipe-catalog` | 47 道菜與四種分類；含成員禁忌的菜標出 `conflicts` |
| POST | `/meal-requests/{id}/t04-data` | `{ request_version, recipe_ids }` → 營養來源回饋；版本不同 409 |
| GET | `/jobs?meal_request_id=&kind=` | `kind`：`meal_generation`／`knowledge_retry`／`price_retry` |
| GET | `/jobs/{id}` | |

### 結果與決定

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/meal-requests/{id}/results` | 版本摘要，新的在前 |
| GET | `/meal-requests/{id}/results/{version}` | `MealResult`（含採用資格、重試狀態、昨日重新比對） |
| POST | `…/results/{version}/decisions` | `{ kind, request_version }` + `Idempotency-Key` → 201（重送 200）`{ decision, result }`；不適用 422（`DECISION_NOT_APPLICABLE`） |
| POST | `…/results/{version}/price-retry` | `{ request_version }` + `Idempotency-Key` → 202 `{ job, created }`；結果必須是 `PRICE_INCOMPLETE` |
| POST | `…/results/{version}/knowledge-retry` | 同上；知識必須是 `unavailable`，否則 422（`RETRY_NOT_APPLICABLE`） |

### 已採用紀錄

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/meal-plans` | + `Idempotency-Key`。帶 `result_version` 是採用生成結果；帶 `recipe_ids` 是採用資料庫菜單（含食譜庫選菜）。201（重送 200）`{ meal_plan, created }` |
| GET | `/meal-plans` | 沒有參數：`{ dates: [{ meal_date, count }] }`（最近 60 天有紀錄的日期） |
| GET | `/meal-plans?date=YYYY-MM-DD` | 當日各餐的完整快照 |
| GET | `/meal-plans/trend?days=&until=` | `{ since, until, items }` |
| GET | `/meal-plans/{id}` | 單筆快照 |
| POST | `/sim-market/handoffs` | `{ plan_id }` → `{ checkout_url, expires_at }`，網址指向假後端自己的示意頁 `/__fake-market/checkout` |

真後端沒有「日期範圍」的清單端點；要一段期間的資料用 `/meal-plans/trend`。

### 辨識與飲食小幫手

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/recognitions` | multipart：`image`（必要）、`meal_request_id`。201 |
| GET | `/recognitions/{id}`、`/recognitions/{id}/image` | 照片是一張示意 SVG |
| POST | `/recognitions/{id}/confirm` | `{ name, ingredient_id }` |
| POST | `/recognitions/{id}/retry` | 只有 `unavailable` 可以重試，其餘 409 |
| DELETE | `/recognitions/{id}` | 204 |
| POST | `/assistant/chat` | `{ message, history, engine }` → SSE：`meta` → `references` → `delta`… → `done`（或 `error`） |
| POST | `/assistant/recipes` | `{ answer, type }` → 1–3 道建議菜，排除全家過敏與禁吃 |

### 假後端專用（真後端沒有，畫面程式不要呼叫）

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/__fake/reset` | 回到種子資料並登出（給自動化測試用） |
| POST | `/__fake/team` | 例如 `{ "knowledge": "offline", "assistant_openai": "online" }`，切換 `/health/team` 的狀態；`assistant` 離線時 `/assistant/chat` 回 503，`recognition` 離線時辨識回 `unavailable` |

## 種子資料

「今天」一律取系統時鐘。過去的已採用紀錄用「幾天前」保存，查詢時才換算成日期。

**帳號**：任何 Email 配任何密碼都能登入，登入後是兩個種子家庭的成員。用「註冊」建立的帳號沒有家庭（玄關是空的）；`taken@example.com` 已經註冊過。

**林家廚房**（4 人）

| 成員 | 設定 | 用途 |
|---|---|---|
| 阿明 | 42 歲男、72 kg、維持、食量 1.15、不吃茄子 | |
| 小惠 | 40 歲女、55 kg、一般、食量 1、花生過敏 | |
| 小安 | 5 歲男、19 kg、一般、食量 0.85、白蝦過敏（連帶排除蝦仁）、不吃青椒 | 每人營養裡會**超過熱量上限**的那一位 |
| 阿嬤 | 78 歲女、38 kg、減脂、食量 0.7 | **資料算不出營養目標**：問卷選到她時 `targets_preview.status = unavailable`；把目標改成「一般餐食」就好了 |

問卷（依更新時間新到舊）：

| 狀態 | 內容 |
|---|---|
| `queued` → `running` | 明天午餐。啟動後排隊 5 秒，之後執行 10 分鐘（暗號 `slow`），可以看進度或取消 |
| `draft` | 今天晚餐，還沒填完，而且選了阿嬤 |
| `draft`（picker） | 後天晚餐，已挑蔥油雞腿、蒜香菠菜、玉米蘿蔔湯；可以看資料庫菜單、營養預覽、小知識並採用，採用後能按一鍵購入 |
| `failed` | 後天午餐，生成失敗（暗號 `fail`） |
| `awaiting_decision` | 明天晚餐，熱量未知（暗號 `nonutri`）：有待決定的 `continue_missing_nutrition`；小惠同時用餐與帶便當；主食糙米飯（糖未知） |
| `result_ready` | 今天晚餐，可以採用。四道菜：地瓜糙米飯（主食）、清蒸鱸魚（海鮮）、蒜炒高麗菜（蔬菜）、味噌豆腐湯（湯）。鱸魚片查不到價格（`PRICE_INCOMPLETE`，總額為 null、只給已知部分）；膳食纖維與糖未知（附已知部分與缺值食材）；小安超過熱量上限；味噌湯是高鈉；知識有 4 則並附出處；家中已有蒜頭與鹽 |
| `confirmed` | 今天午餐，已採用（香煎鮭魚、清炒青江菜、番茄蛋花湯 + 白飯） |
| `confirmed`（picker） | 2 天前午餐，食譜庫選菜的採用紀錄 |

已採用紀錄：過去 25 天內 17 筆，加上今天午餐。最近 7 天裡，5 天前沒有紀錄，4 天前是只有菜名的最小紀錄（營養是 null），2 天前有午餐（資料庫菜單）與晚餐兩筆。有幾天的菜含鱸魚，成本總額是 null。

**外婆家**（2 人：外婆、外公）：3 筆已採用紀錄，一份填好、可以直接送出的草稿（也適合用來試「不採用，換一份」）。

**新建立的家庭**：沒有成員、問卷與紀錄。

**食譜庫**：47 道（主菜 26、湯 8、主食 9、點心 4；台式／日式／中式／西式／韓式），一頁 10 道可以翻 5 頁。

## 暗號

| 在哪裡輸入 | 暗號 | 效果 |
|---|---|---|
| 登入密碼 | `wrongpass1` | 401「Email 或密碼不正確。」 |
| 註冊 Email | `taken@example.com` | 409「此 Email 已註冊」 |
| 問卷的口味偏好 | `fail` | 工作排隊 2 秒、執行 3 秒後失敗，附可讀的原因 |
| | `slow` | 排隊 5 秒、執行 10 分鐘（慢慢看進度、試取消） |
| | `noknow` | 結果的知識是 `unavailable`，可以「只重試知識說明」；小知識也回不可用 |
| | `noknowever` | 同上，而且知識重試也會失敗 |
| | `nf` | 知識是 `not_found`（不能重試） |
| | `nonutri` | 熱量未知 → `NUTRITION_INCOMPLETE`、問卷 `awaiting_decision` |
| | `overbudget` | 價格放大 20 倍 → `BUDGET_EXCEEDED`，不能採用 |
| | `allprice` | 鱸魚片也有價格（價格完整） |
| | `noprice` | 缺價重試會失敗（`PRICE_LOOKUP_FAILED`） |
| | `aimenu` | 標成 AI 生成菜單（多一個 `ai_generating` 階段與回退說明） |
| 指定菜色 | 食譜庫沒有的菜名 | 生成時改走 AI 模式；資料庫菜單回 `SPECIFIED_DISH_NOT_IN_DATABASE` |
| | 含成員禁忌的菜（例如小安 + 蒜蓉蒸蝦） | `SPECIFIED_DISH_DIETARY_CONFLICT` |
| | 主食類的菜 + 有選主食 | `SPECIFIED_DISH_STAPLE_CONFLICT` |
| | `隨便`、`123` | 422，不是菜名 |
| 辨識上傳的檔名 | 含 `empty` | 沒有辨識到（`no_result`） |
| | 含 `offline` | 服務不可用（`unavailable`），按重試就成功 |
| 飲食小幫手的問題 | 蛋白質、高麗菜、鈉、纖維、早餐、魚… | 有參考資料的回答 |
| | 山藥 | `category_answer`（改用同類食材回答） |
| | 含藥、劑量、診斷、治療、病、症 | `declined_medical`（不回答） |
| | 胸痛、呼吸困難… | `emergency` |
| | 你好 | `greeting` |
| | 其他 | `out_of_scope` |
| | `offline`／`ratelimit` | 503 `ASSISTANT_UNAVAILABLE`／429 `RATE_LIMITED` |
| | `broken`／`quota` | 串流中途 `error`：`ASSISTANT_STREAM_BROKEN`／`LLM_BUDGET_EXHAUSTED` |

口味偏好的暗號前後不能緊接其他英文字母（`info` 不會觸發 `nf`）。

工作時間表：生成排隊約 2 秒 → 執行約 6 秒（階段依序變化）→ 成功並產生結果版本；知識／缺價重試排隊 1 秒、執行 3 秒。

## 與真後端不同的地方

- **每人份量依食量 n 平分**（真後端依每人目標熱量的比例分）。這是刻意的：這樣食量設得比需求大的小安會超過熱量上限，畫面才看得到「有人超出」。
- **缺價不會產生待決定事項**：這和真後端現在的規則一致（缺價自動標示 `price_exception_accepted`，可以直接採用）；`accept_missing_price` 送了會回 `DECISION_NOT_APPLICABLE`。待決定事項只會來自缺必要營養。
- **辨識不回傳信心值**（真後端也不回傳）。「信心低」用對不到標準食材的那一筆（`ingredient_id: null`）代表。
- 每日目標、整餐用量、預算下限是簡化的假公式；數值取到小數 2 位，不是後端的精確分數。
- 營養預覽遇到算不出目標的成員時回 503 `NUTRITION_DATA_UNAVAILABLE`；真後端在這個情況的錯誤碼沒有對照過。
- 沒有模擬：Cookie 與來源檢查、每日生成次數與額度上限、減鹹調整（`sodium_adjustment` 一律 null）、調整建議裡的可調食材、食譜封面圖、`/meal-requests/{id}/versions/{version}`、模擬超市的型錄與結帳、管理端點。
- 趨勢的數值型別和真後端一樣是混合的：生成結果的紀錄回字串並附成本，資料庫菜單與最小紀錄回數字且沒有成本欄位。
