# 05 — YOLO 蔬菜辨識模型與獨立評估

- Spec：`../spec.md`
- Blocked by：無
- 覆蓋：R03、G05 辨識部分；AC22、AC28
- 狀態：使用者已核准本次拆分及後續修正；尚未實作。

## 交付成果

交付可載入的蔬菜辨識模型、固定類別／前後處理版本及獨立品質報告。

## 驗收條件

- 部署模型固定為 YOLO26s（2026-10-29 規格變更，見下方「規格變更紀錄」），不再要求 YOLOv8n／v8m 比較；依來源分開訓練／驗證／保留測試，防止相同或相近照片跨集合；記錄資料來源及權重版本。
- 獨立測試集主要類別 macro precision／recall 各至少 80%，採 IoU≥0.5 一對一配對；另報 mAP50、mAP50–95、各類樣本數及延遲，不計人工修正。
- 內部保留類別、位置、信心及版本供模型評估；對使用者取最高信心蔬菜名稱填入文字框，不輸出重量。AC22 評估仍用偵測結果，不能用文字框修正後答案計分。團隊電腦推論及跨節點容量留待 Ticket 18 驗證。


## 規格變更紀錄

### 2026-10-29｜部署模型由「YOLOv8n／v8m 比較」改為固定 YOLO26s

- 決定：組內決定 T05 以 YOLO26s（Food41 39 類、OpenCV denoise 前處理 `cv2.medianBlur(image, 3)`）作為部署模型，取消原規格「以相同資料比較 YOLOv8n 與 v8m」的要求，不補跑 v8m。
- 理由：
  - 已訓練的架構中，YOLO26s 的獨立 test 指標最高：denoise 版 mAP50–95 63.39%、mAP50 82.98%、P 77.79%、R 76.99%，RTX 4060 單張 5.45 ms（591 張 test）。對照 YOLOv8n 去噪 mAP50–95 58.55%、YOLO11s 去噪 60.50%（535 張共同 test）。兩份 test 不同，只作為選型參考，不作為驗收證據。
  - 在 YOLO26s 的 8 種單一前處理中，denoise 的 mAP50–95 最高。10/18 的組合前處理實驗依 validation 選出 white_balance_denoise，但它的 test 成績（mAP50–95 61.86%）低於 denoise，因此不換。
  - 原規格本來就寫明「不預設 v8m 必較佳」，選型依準確率與速度決定。v8m 只有訓練腳本，沒有訓練紀錄；在期末時程內優先把算力用在重切 test 與重訓 26s。
- 不變的部分：AC22 門檻與計算方式（主要類別 macro P／R 各 ≥80%、IoU≥0.5 一對一配對，另報 mAP50、mAP50–95、各類樣本數與延遲）維持不變；G05 仍需固定模型版本、類別、門檻與測試集證據。
- 已知問題（尚未解決）：既有 591 張 test 中有 9 張與 train／valid 內容完全相同，現有分數含資料洩漏風險，不能作為 AC22 驗收證據。須依來源與重複群組重切 test、固定清單後重訓並重新評估 YOLO26s。
- 證據：T05 證據包 `T05_model_evidence.zip`（訓練腳本、各 run 的 args.yaml／results.csv、`yolo26s_opencv_full_20261017/comparison/scores.json`、`yolo26s_combinations_20261018/comparison/selected_test.json`、`audit/split_audit.json`）；部署 manifest 為 `inference/model_manifest.json`。
