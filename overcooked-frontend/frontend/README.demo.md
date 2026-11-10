# 今天吃啥前端

React 19、TypeScript、Vite，搭配 React Hook Form、Zod 與 Lucide。介面為繁體中文，使用焦橘、奶油白與淡紫配色。

## 執行

需要 Node.js 22.18 以上版本。在 `frontend/` 內執行：

```sh
npm ci
npm run dev
```

開啟終端機顯示的網址，按「登入示範帳號」即可體驗。`npm run build` 產生 `dist/`，可使用 `npm run preview` 預覽建置結果。

## 功能

- Email 登入／註冊頁籤、首頁、家庭與成員、新增／編輯成員。
- 當餐條件、現有食材、照片預覽、可修改的示範辨識結果。
- 草稿自動保存、保存失敗重試、離開警示；家庭各自保存草稿。
- 食譜、採購、營養、分餐四個頁籤。
- 食譜示意照片、依食材對應的 RAG 知識摘要與可展開的適用條件／來源。
- 營養估算與目標長條圖、數字旁的示範計算說明；營養符合時短暫彩紙動畫，未符合時列出原因與調整入口。
- 分餐表格與「全家分配／個人餐盤」切換版型；圓餅圖比例待確認，目前不顯示推算比例，份量保留 g／ml。
- 採用確認、缺價例外確認、保存後以月曆回看紀錄。
- 服務失敗、無可行方案、缺價格、缺必要營養、保存／辨識失敗的示範情境。
- 重新整理會保留草稿及已採用紀錄；尚未採用的結果與上傳照片不會保存。

畫面上方的「預覽情境」會套用於下一次相關操作。服務與營養情境在「產生菜單」或「重新產生」時套用；保存失敗在修改表單後套用。切回「正常流程」可重試。

## 示範與正式服務

此版本延續原本的獨立 Demo，沒有合併後續 main 的 API 前端。Vite 保留原有 `/api` proxy 與健康檢查輔助程式；規劃流程仍使用本機示範服務。

- 示範登入僅在 `sessionStorage` 記錄示範 Email，不傳送或保存密碼，不提供真實身份驗證。正式登入須改接伺服器 session 與 Secure／HttpOnly Cookie。
- 家庭、成員、草稿及示範紀錄存放於此瀏覽器的 `localStorage`，不是正式帳號資料，也沒有跨裝置同步或伺服器授權。
- 照片只在記憶體中預覽，不上傳或持久化。示範辨識固定回傳「紅蘿蔔」，使用者可修正。
- 食譜、包裝價格、營養目標、熟食量與 AI 模式內容皆為 fixtures。示範驗證通過不代表正式營養、資料 Gate 或後端採用驗證通過。
- 分餐目前以示範食譜的每 n 熟食量展示介面，沒有計算每位成員的個人營養適足性。正式功能需新增後端分餐契約與驗證。
- 蔬果圖片為先前設計使用的 AI 生成素材。

## 食譜圖片與知識來源

`src/data/recipe-photos.json` 記錄 12 個示範菜色的圖片路徑、作者、原圖頁與授權。圖片存於 `public/recipes/`，來自 Wikimedia Commons；相近料理皆標示「料理示意照片」，畫面會附上作者／來源與授權連結。原圖未修改，顯示時由 CSS 裁切視窗。各圖片依其原授權提供，詳見 [PHOTO_CREDITS.md](./PHOTO_CREDITS.md)。`RecipeDetail` 可接收優先於示意圖片的 `photo` 資料，目前沒有串接資料庫照片 API。

`src/data/knowledge-snapshot.json` 收錄 overcooked 提交 `e7d3dbdbdd0b5550188bd7e37c030a9b5e7fdfc5` 中 `knowledge_rag/candidates/web_paper_2026-10-30.json` 的 8 筆對應摘要，保留知識 ID、食材 ID、原文摘要、適用條件與來源，並參照同版 review 資料。這是離線來源快照，沒有呼叫 RAG 檢索服務，也不表示資料已匯入正式索引。

只有食譜實際使用且對應成功的食材才顯示知識；雞胸肉資料限香煎雞胸，研究族群與證據限制不外推至其他食材或一般族群。未找到對應知識的食材不補寫內容。

## 程式結構

| 位置 | 責任 |
|---|---|
| `src/App.tsx` | 路由、工作階段、頁面流程與離開保護 |
| `src/pages/` | 各頁面與表單 |
| `src/components/PlanView.tsx` | 結果與歷史共用的四個頁籤 |
| `src/domain/` | 資料型別、Zod schema、份量／價格／採用規則 |
| `src/data/catalog.ts` | 示範食材、菜色與固定選項 |
| `src/services/planner.ts` | `PlannerGateway` 與可替換的示範實作 |
| `src/services/storage.ts` | 帶版本及 schema 檢查的本機儲存 |
| `src/services/api.ts` | 現有健康檢查 API |

正式串接時，將 `PlannerGateway` 的辨識、生成與採用替換為後端實作，並將登入、家庭、成員與草稿的本機操作改接定案後的 API。前端檢查僅改善操作體驗；身份授權、版本、冪等、過敏禁忌、昨日去重、價格與營養採用條件仍需由後端重新驗證。

## 驗證

```sh
npm run build
npm run lint
npm test
```

規則測試涵蓋便當與份量、包裝支出與比例成本、已有食材、過敏衝突、昨日排除與指定例外、缺價／缺營養、版本失效、採用冪等、快照隔離、儲存失敗與知識適用範圍。

## GitHub Pages 展示

儲存庫根目錄的 `.github/workflows/frontend-pages.yml` 會在 `front_page_demo_v1` 的前端有更新時，安裝依賴、執行檢查並部署前端。

1. 將前端與 workflow 提交並推送至 `front_page_demo_v1` 分支。
2. 在 GitHub 的 Settings → Pages → Build and deployment → Source 選擇 GitHub Actions。
3. 等 Actions 的「Publish frontend demo」成功，再開啟 Pages 顯示的網址。

此儲存庫預期網址為 `https://yun112064.github.io/overcooked/`；實際網址以 Pages 設定顯示為準。工作流程只部署 `frontend/dist`，示範資料保存在各自瀏覽器，不會在隊友之間同步。

本機驗證相同子路徑的建置：

```sh
npm run build -- --base /overcooked/
npm run preview
```

開啟終端機顯示的網址並加上 `/overcooked/`。
