# Sim's Supermarket

獨立的模擬購物網站。主程式採用餐單後，點「一鍵購入」開啟這裡的結帳頁；可調整包數、瀏覽同一份 T01 目錄及確認模擬訂單。商品包裝與售價為示範值，不是 T02 真實報價；沒有付款、配送、扣庫存或餐單回寫。

## 開發啟動

此目錄有自己的套件、路由、建置產物與網站入口，不依賴主前端的頁面或登入 Cookie。

1. 後端環境設定 `SIM_MARKET_URL=http://127.0.0.1:5174`。主程式使用 `http://localhost:5173`，兩站使用不同 hostname，避免共享 Cookie。
2. 後端設定 `SIM_MARKET_HANDOFF_KEY` 為至少 32 bytes 的隨機秘密。可執行 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 產生，僅存入未追蹤的環境設定；所有後端 worker 使用同一值，不放到任何 `VITE_*` 變數。
3. 在這個目錄執行 `npm ci`、`npm run dev -- --host 127.0.0.1`。後端若不是 `127.0.0.1:8000`，設定 `VITE_API_PROXY_TARGET`。
4. 從主程式的已採用餐單點「一鍵購入」。單獨開網站但沒有購物連結時，會提示由主程式進入。

本功能不會自動啟動資料庫、worker、爬蟲或模型。必須先有可用的已採用餐單。

## 交接與隔離

- 主程式以登入與目前家庭權限呼叫 `POST /sim-market/handoffs`，只提交 `plan_id`。
- 後端產生綁定該登入 session 的 10 分鐘簽章連結。識別內容只有餐單／使用者 ID、期限與隨機值；原始 Cookie、session 雜湊、家庭資料與簽章秘密不外送。連結只能讀這份清單，不能當作登入憑證。
- 短效 grant 放在 URL fragment，載入後立即從網址移除，僅暫存於該分頁的 sessionStorage。不要分享購物連結；持有者在有效期間可讀取此份清單。
- 超市只以 JSON 向同站 `/market-api/checkout` 交換清單，不帶 Cookie。代理只開放該端點；後端核對超市 Origin、簽章、期限、原 session 及家庭成員資格，登出或切換家庭後不能重新取得清單。
- 只回傳商品目錄、菜名及缺料量；不傳營養、問卷、預算、成員資料或整份歷史快照。購物車依交接識別碼、餐單及目錄規格隔離。
- 每 30 秒／回到分頁時重新驗證。重新整理可恢復有效連結的購物車；失效後需回主程式重新取得連結。
- 容量使用精確分數計包，未知量與 g／mL 不相容保持待確認；不推定密度。T01 版本不一致時阻擋自動購物。

## 建置與部署

`npm run lint`、`npm test`、`npm run build`；靜態產物在 `dist/`。

Compose 的 `sim-market` profile 提供獨立服務與連接埠 8081。使用前把後端 `SIM_MARKET_URL` 設為 `http://127.0.0.1:8081`，主站使用 `http://localhost:8080`。選擇啟動此 profile 可能連帶啟動後端相依服務，請先確認目標環境；不應拿正式 V3 做開發測試。

對外部署請使用獨立 HTTPS hostname、同站 checkout 代理及 SPA fallback；後端 URL 設成該超市 origin，不含路徑。不要把超市 origin 加入主程式 `ALLOWED_ORIGINS`，也不要代理主程式的 `/auth` 或任意 `/api`。提供的 Caddyfile 會移除 Cookie／Authorization，不設定存取日誌。

本說明描述操作契約，不代表尚未執行的真實付款、部署或端到端驗收已通過。
