# frontend-3d｜場景式前端

「今天吃啥」的新前端：整個 App 是一間家，換頁等於鏡頭在房子裡移動。與現有的 `frontend/` 並存，共用同一個後端 API，驗收後才切換。

- 設計規格：[DESIGN.md](DESIGN.md)
- 分鏡：[preview/storyboard.html](preview/storyboard.html)
- 插圖審核頁：[preview/art.html](preview/art.html)（由 `python frontend-3d/preview/build_art.py` 產生）
- 假後端的端點、種子資料與暗號：[dev/README.md](dev/README.md)

## 目前做到哪

九個場景與飲食小幫手都接上資料了，行為照 `frontend/` 的對應頁面移植（同樣的端點、請求內容、query key、版本與冪等鍵規則）。
**2026-11-15 用假後端在內建瀏覽器走過一遍**（桌機 1360×850、手機 390×844）：每個場景都進得去、主要流程從登入到採用都能操作。走過哪些、沒走哪些，記在 `docs/1102_V3/VALIDATION.md` 和最下面的「還沒驗證的事」。

| 場景 | 網址 | 程式 | 狀態 |
|---|---|---|---|
| 大門：登入／註冊 | `/login` | `rooms/door` | 已做 |
| 玄關：選擇／建立家庭 | `/households` | `rooms/genkan` | 已做 |
| 客廳：今日餐單、電視（早午晚、近 7 天熱量）、通往廚房與全家福 | `/today`（`?meal=breakfast｜lunch｜dinner`） | `rooms/living` | 已做 |
| 廚房：單軌、瓦斯爐上的鍋、食譜書、冰箱便利貼 | `/plan` | `rooms/kitchen` | 已做 |
| 單板＋鍋：問卷五步、送出、生成中 | `/plan/:id`（`?step=1…5`）、`/plan/:id/cooking` | `rooms/passboard` | 已做（料飛進鍋等動畫未做） |
| 餐桌：生成結果、決定、採用、重試 | `/plan/:id/result/:version` | `rooms/dining` | 已做 |
| 食譜書：誰要吃 → 挑菜 → 結果 | `/pick`、`/pick/:id` | `rooms/recipe-book` | 已做 |
| 冰箱門：已採用月曆、營養與採購 | `/records`（`?tab=adopted｜nutrition`、`?date=YYYY-MM-DD`） | `rooms/fridge` | 已做 |
| 全家福：成員、設定 | `/family` | `rooms/portrait` | 已做（橡皮擦動畫未做） |
| 飲食小幫手：吉祥物、懸浮聊天室、全頁 | 每個房間、`/assistant` | `assistant/`、`shell/Mascot` | 已做 |

舊網址會轉過來：`/history` → `/records?tab=adopted`、`/generation-history` → `/records?tab=nutrition`、`/members` → `/family`；其他不認得的網址 → `/today`。

共用的基礎：

| 項目 | 狀態 |
|---|---|
| 景深模式（沉浸／靜態景深／關閉）與系統「減少動態」的一次性提示 | 已做 |
| 鏡頭取景：世界蓋滿畫面、手機停靠點（`span`、`letterboxTop`）、邊緣提示、物件列 | 已做 |
| 導覽殼 `SceneHud`：手機底部分頁、桌機左側欄、動作列、右上角家庭選單 | 已做 |
| 鍋狀態機 `pot/potState`：客廳門口、廚房、單板三個地方用同一個判斷 | 已做 |
| 共用查詢 `session/queries`：同一個 query key 一定是同一種資料形狀；採用後一次讓相關快取過期 | 已做 |

已知的限制：

- 圖層目前直接用 SVG。寫實風的濾鏡很吃效能，規格要求建置時轉成 WebP／AVIF，這一步還沒做；沒有在實體手機上量過效能。
- 「關閉」景深模式目前和「靜態景深」一樣（場景還在、不會動）；規格裡退回卡片版面的做法還沒做。
- 手機的視角微調（陀螺儀）還沒做；滑鼠視差只在桌機生效。
- 客廳餐盤上的菜是同一張示意圖，不會依菜色換。
- 沒有 DOM 測試環境：元件測試只檢查伺服器端渲染出來的標記，effect、點擊、輪詢都沒有在測試裡跑過。

## 開發

需要 Node 24。

```bash
npm --prefix frontend-3d install
```

接真的後端（預設轉發到 `http://127.0.0.1:8000`，可用 `VITE_API_PROXY_TARGET` 改）：

```bash
npm --prefix frontend-3d run dev
```

不接後端，用記憶體中的假 API（資料是合成的，重啟就歸零，不碰任何資料庫）：

```bash
npm --prefix frontend-3d run dev:fake
```

兩者都開在 <http://localhost:5173>。

## 用假後端試每個場景

假後端在 `dev/fake/`（`dev/fakeApi.ts` 只負責把 HTTP 接上去）。整個開發伺服器只有一個登入狀態，重啟後要重新登入、重新選家庭。
完整的端點、種子資料與暗號在 [dev/README.md](dev/README.md)；下面是每個場景最快看得到東西的走法。

| 場景 | 怎麼試 |
|---|---|
| 大門 | 任何 Email 配 8 個字元以上的密碼都能登入。密碼 `wrongpass1` → 登入失敗。翻到註冊：Email `taken@example.com` → 已註冊；註冊成功的新帳號沒有家庭。 |
| 玄關 | 登入後有兩個家：**林家廚房**（資料最多）與**外婆家**。也可以掛一張新門牌建立空的家。 |
| 客廳 | 選林家廚房。今天午餐已採用；電視切換午餐／晚餐，近 7 天裡有「沒有紀錄」的日子，也有「未知」的日子（虛線加問號，不是 0）。門口的鍋在冒煙（種子裡有一份會煮 10 分鐘的單）。 |
| 廚房 | 單軌上是最新三張單，第四格是「全部的單」。左鍋是最新一份還沒採用的問卷；點空鍋開新的一餐。便利貼通往冰箱門。 |
| 單板＋鍋 | 從廚房點空鍋。填五步；第 4 步的「口味偏好」可以放暗號：`fail`（失敗）、`slow`（煮 10 分鐘，試取消）、`nonutri`（熱量未知 → 等你決定）、`overbudget`（超出預算、不能採用）、`noknow`／`noknowever`／`nf`（小知識拿不到／重試也失敗／沒有）、`allprice`（價格完整）、`noprice`（重新查價會失敗）、`aimenu`（AI 生成菜單）。第 3 步上傳的檔名含 `empty` → 沒辨識到；含 `offline` → 服務不可用，按重試就成功。第 2 步選到阿嬤 → 目標算不出來（到全家福把她的目標改成「一般餐食」就好）。第 5 步有資料庫菜單預覽，可以直接採用或「不採用，換一份」；外婆家那份填好的草稿最適合試這個。 |
| 餐桌 | 種子裡「今天晚餐・已有結果」那張單：四道菜、鱸魚查不到價格、膳食纖維與糖未知、小安超過熱量上限、味噌湯高鈉、4 則小知識。「明天晚餐・等你決定」那張：先決定，再看能不能採用。採用後回客廳、冰箱門都看得到。 |
| 食譜書 | 廚房的食譜書，或種子裡「食譜庫選菜」的草稿（已挑三道，可以直接看菜單、營養、小知識並採用）。含成員禁忌食材的菜不能選（小惠花生、小安白蝦與青椒、阿明茄子）。結果那一步的「不採用，換一份」最多換 5 組。 |
| 冰箱門 | 月曆上過去 25 天有 17 筆紀錄；5 天前沒有、4 天前是營養未知的最小紀錄、2 天前有兩餐。有鱸魚的日子成本是「價格不完整」。把手 → 開新單並跳到問卷第 3 步。 |
| 全家福 | 點人物看資料與每日基準；阿嬤名字下有「目標算不出來」。編輯後用舊版本再存會遇到版本衝突（開兩個分頁可以試）。畫框下的名牌是設定。 |
| 飲食小幫手 | 點底部的小番茄。問「蛋白質」「高麗菜」「鈉」「纖維」「早餐」「魚」有附出處的回答；`山藥` → 改用同類食材回答；含「藥、劑量、診斷、治療」→ 不回答；「胸痛」→ 緊急提示；`offline`／`ratelimit`／`broken`／`quota` → 各種錯誤。 |

## 檢查

```bash
npm --prefix frontend-3d run lint
```

```bash
npm --prefix frontend-3d run test
```

```bash
npm --prefix frontend-3d run build
```

`build` 會先跑 `tsc -b` 再跑 `vite build`。在 `frontend-3d/` 裡也可以分開跑：`npx tsc -b`、`npx eslint .`、`npx vitest run`、`npx vite build`。

最近一次四項都跑過（2026-11-15，Node 24.18.1）：型別檢查與 lint 沒有訊息；測試 48 個檔案、932 項全過；建置成功，
進入點 `index` 425.74 kB（gzip 137.77 kB）、表單驗證 `schemas` 128.12 kB（gzip 38.57 kB），各場景自己一包，最大的是單板 72.10 kB（gzip 24.59 kB）。
這些只代表程式編得過、純邏輯與伺服器端渲染的標記對得上，不代表畫面看過。

`src/journey.fake.test.ts` 用假後端把主要的一趟路走一遍（進門 → 開單 → 五步 → 送出 → 餐桌 → 決定／採用 → 客廳與冰箱門 → 食譜書 → 全家福 → 小幫手 → 換家庭與登出），每一步用的都是場景自己的函式。某個房間改了規則、另一個沒跟上時，這個測試會先壞。

## 結構

```
frontend-3d/
├─ dev/
│  ├─ fakeApi.ts           Vite 外掛（只在 --mode fake 啟用），把 HTTP 接到假後端
│  ├─ fakeApi.test.ts      假後端自己的規則測試
│  ├─ fake/                假後端本體：handle(state, request)、種子資料、各端點的規則
│  └─ README.md            端點、種子資料、暗號
├─ preview/                分鏡、插圖審核頁與它的產生腳本
└─ src/
   ├─ App.tsx              路由與進門的條件（登入、已選家庭）；每個場景用到才載入
   ├─ journey.fake.test.ts 跨房間的主要流程（對假後端）
   ├─ api/                 axios client、後端資料型別、營養換算、小幫手的串流
   ├─ tokens/tokens.css    顏色、字型、動態時間、層級
   ├─ depth/               景深模式（DepthProvider、depthMode）
   ├─ camera/              取景計算（framing）與房間元件（Room、Placed、Hotspot、useRoom）、announce（換房間或停靠點時念給螢幕閱讀器）
   ├─ shell/               SceneHud（分頁／側欄／動作列）與 hudLayout、SessionMenu、Mascot、
   │                       RouteError（錯誤畫面與 CrashBoundary）、routeHelpers（載入失敗再試一次、舊網址轉址）
   ├─ session/             session.ts：useMe、登入／換家庭／登出後清快取
   │                       queries.ts：好幾個房間共用的查詢與 invalidateAdoptedPlans()
   ├─ components/          Button、Panel、ConfirmDialog、Notice、HudNote、Spinner、StatusChip、
   │                       UnknownValue、NutritionBadge、Money、formatMoney、nutritionVerdict、requestStatus
   ├─ pot/                 potState（鍋狀態機、stovePots 兩口爐的規則）與 <Pot>
   ├─ assistant/           AssistantProvider（對話狀態，換房間不會消失）、AssistantLauncher（吉祥物＋懸浮視窗）、
   │                       AssistantPage（/assistant）、ChatParts、chatLogic、chatStore
   ├─ rooms/               每個場景一個資料夾（下面只列主要的檔案；測試檔在同一層）
   │  ├─ door/ genkan/     大門、玄關
   │  ├─ living/           LivingScene、Tv、TodayCard、today.ts（純邏輯）
   │  ├─ kitchen/          KitchenScene、TicketListPanel、FridgeNotes、useFridgeNotes.ts、kitchenLogic.ts
   │  ├─ passboard/        PassboardScene、Steps、Pantry（辨識）、Confirm、MenuPreview、PersonNutrition、
   │  │                    draft.ts、gate.ts、generation.ts、useGeneration.ts、previewText.ts、layout.ts
   │  ├─ dining/           DiningScene、Tabletop、panels/、tableModel.ts、tableLayout.ts、resultText.ts、useMealResult.ts
   │  ├─ recipe-book/      RecipeBookScene、PeoplePage、CatalogPages、ResultPages、ResultPanels、
   │  │                    bookLogic.ts、bookFraming.ts、useGeneration.ts、useMenuPreview.ts、previewText.ts
   │  ├─ fridge/           FridgeScene、DoorNotes、PlanDetail、records.ts、nutritionView.ts、useMarketCheckout.ts、layout.ts
   │  └─ portrait/         PortraitScene、MemberForm、IngredientPicker、MemberDetail、RosterPanel、SettingsPanel、
   │                       memberRules.ts、people.ts
   └─ assets/              分層插圖（rooms/<room>/z0…z3.svg）與元件圖（pot、tableware、family、mascot）
```

### 幾個跨房間的約定

- **共用的資料用 `session/queries.ts` 的查詢**（`mealRequestsQuery`、`mealRequestQuery(id)`、`membersQuery`、`generationJobsQuery`、`mealResultsQuery(id)`、`planDatesQuery`、`plansOnDateQuery(date)`、`trendQuery(days, today)`、`teamStatusQuery`）。快取裡放的是後端回的原樣，不要先加工再存。
- **`['jobs']` 與 `['jobs', 問卷 id]` 是兩份不同的資料**：前者是整個家的生成工作清單（廚房、客廳的鍋），後者是那份問卷最新的一筆（單板、食譜書）。只想讓清單過期時用 `{ queryKey: ['jobs'], exact: true }`。
- **採用了一餐之後呼叫 `invalidateAdoptedPlans(queryClient)`**（餐桌、單板的資料庫菜單、食譜書三個入口都有），客廳、廚房便利貼、冰箱門才會一起更新。
- **送出、取消、煮完之後**讓 `['jobs']`（exact）、`['meal-results', id]`、`['meal-requests']` 過期，並把新的問卷放進 `['meal-request', id]`。
- **鍋長什麼樣子只由 `potState()` 決定**；兩口爐放哪兩份問卷只由 `stovePots()` 決定。還在挑菜的食譜書單不上爐。
- **登入逾期或還沒選家庭**（401、409 `HOUSEHOLD_NOT_SELECTED`）：讓 `['me']` 過期，`App.tsx` 的守門會把人帶回大門或玄關。換家庭、換人、登出時的快取清理都在 `session/session.ts`。
- **未知值（null）不顯示成 0**：用 `<UnknownValue>` 或各場景的虛線樣式；三態判定用 `components/nutritionVerdict.ts`（熱量 ≤110%、蛋白質 ≥90%）。整包花費與按比例成本是兩個數字，不相加。

### 加一個新場景

1. 插圖放 `src/assets/rooms/<room>/z0.svg…z3.svg`（1600×1000，由遠到近）。
2. 在 `src/rooms/<room>/` 寫場景元件：預設匯出、不收 props，需要的資料自己讀（`useParams`、`useQuery`…）。
3. 用 `<Room>` 包起來，傳入圖層、停靠點和物件列；HUD 用 `<Room hud={<SceneHud tab="…">…</SceneHud>}>`。
   - `SceneHud` 會自己告訴房間底部／左邊被佔掉多少（物件列與吉祥物跑道會讓開）；場景不要自己畫吉祥物或家庭選單。
   - `<Panel>`、`<ConfirmDialog>`、`<HudNote>` 放在 `SceneHud` 的 children。
   - 手機上場景自己開了下方抽屜時傳 `assistant={false}`；主要按鈕剛好在吉祥物跑道上時傳 `assistant="parked"`。
4. 疊在場景上的東西用 `<Placed>`（不可點）或 `<Hotspot>`（可點），位置填畫稿座標（x＝水平中心、y＝上緣）。
5. 每個可點物件都要出現在 `hotspots`，並指定它在哪個停靠點看得到——手機靠這份清單保證每個功能都操作得到。
6. 寫樣式時可以用 `:global([data-layout='phone']) .x { … }` 分辨手機與寬版面；`--bottom-inset`、`--left-inset` 是 HUD 佔掉的邊緣（px）。
7. 在 `App.tsx` 加路由（`lazy(() => import(…))`）。

### 手機取景要注意

- 長螢幕手機（例如 390×844）蓋滿畫面時只看得到約 **460 個世界單位寬**（約 436 個畫稿單位），比審圖頁的手機預覽（9:16，約 560 單位）窄。一個停靠點要看到的東西比這更寬時，在停靠點加 `span`，鏡頭會拉遠到看得全，上下多出來的地方留給物件列。
- **圖層比世界大一圈（1.06）**，`<Placed>`／`<Hotspot>` 已經換算好了，但停靠點的 `fx` 要自己算：畫稿座標 x 對應的 `fx = 0.5 + (x / 1600 − 0.5) × 1.06`。直接用 `x / 1600` 會偏掉幾個像素，東西貼邊時就被切到。
- 鏡頭為了 `span` 拉遠後世界比畫面矮：預設上方留 30% 的空白；場景下半有自己的抽屜時用 `letterboxTop` 把世界往上讓。
- 左右邊緣的「‹ 上一段／下一段 ›」按鈕在畫面 42% 高的地方。那個高度剛好是內容時，用 `<Room edgeTop="…">` 換一個高度，或場景自己有換停靠點的東西時用 `edgeNav={false}` 關掉。
- **寬螢幕比 16:10 扁時（多數桌機瀏覽器視窗）世界的上下會被裁掉。** `useRoom()` 的 `visible` 是現在看得到畫稿的哪一塊（和 `<Placed>` 同一套座標），需要時用它把東西留在看得到的範圍裡。

## 還沒驗證的事

- 只用假後端在內建瀏覽器看過（2026-11-15，桌機 1360×850 與手機 390×844）；沒有實體手機，滑動手勢只用點擊和物件列確認。
- 這些互動沒有在瀏覽器裡實際按過，只有單元測試：一鍵購入開新分頁、換一桌、重新查價、新增與刪除成員、拖曳聊天視窗、全頁 `/assistant`、Escape 與焦點移動、螢幕鍵盤。
- 沒有接過真的後端：只對 `dev/fake` 的假後端確認過請求內容與回應形狀。真的 multipart 上傳與 SSE 串流也沒跑過。
- 沒有做效能量測與螢幕閱讀器測試。
- 已知的小問題：手機餐桌上下有留白帶；桌機餐桌上吉祥物跑過時會短暫蓋住下方座位的名牌。
