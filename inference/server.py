"""團隊電腦 YOLO 推論服務（T09）：python inference/server.py

只用標準函式庫的 HTTP 伺服器，可直接在已安裝 Ultralytics 的訓練環境執行。
對外只透過加密通道（T17）提供給雲端後端，並以 Bearer token 驗證。

  GET  /v1/health  → 模型版本、前處理、類別數
  POST /v1/detect  → multipart 欄位 image；回傳 {"model_version", "detections": [...]}

環境變數：
  INFERENCE_TOKEN       必填，與雲端 RECOGNITION_SERVICE_TOKEN 相同
  INFERENCE_WEIGHTS     權重檔路徑（預設為 manifest 的 weights_path，已放在 Git）
  INFERENCE_PREPROCESS  none／denoise，預設使用 manifest 指定、與訓練相同的前處理
  INFERENCE_DEVICE      cpu 或 GPU 編號（預設 cpu）
  INFERENCE_CONF        偵測信心下限（預設 0.25）
  INFERENCE_HOST／INFERENCE_PORT（預設 127.0.0.1:8765）
"""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preprocess  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "inference/model_manifest.json"
MAX_BYTES = 12 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Detector:
    """包裝模型與前處理；同一時間只跑一張，避免多執行緒共用模型出錯。"""

    def __init__(self, predict, *, model_version: str, preprocess_mode: str, names: list[str]):
        self._predict = predict
        self._lock = threading.Lock()
        self.model_version = model_version
        self.preprocess_mode = preprocess_mode
        self.names = names

    def detect(self, data: bytes, mode: str | None = None) -> dict:
        mode = mode or self.preprocess_mode
        image = preprocess.apply(preprocess.decode(data), mode)
        started = time.perf_counter()
        with self._lock:
            rows = self._predict(image)
        return {
            # 版本含前處理：非預設前處理的結果不能混用預設版本的指標
            "model_version": f"{self.model_version}+{mode}",
            "preprocess": mode,
            "inference_ms": round((time.perf_counter() - started) * 1000, 1),
            "detections": [
                {"label": self.names[int(cls)], "confidence": float(conf), "box": [float(v) for v in box]}
                for cls, conf, box in rows
            ],
        }


def load_detector(manifest: dict, weights: Path, *, device: str, conf: float, mode: str) -> Detector:
    """載入並核對權重；雜湊與 manifest 不同時拒絕啟動，避免混用不同版本。"""
    actual = sha256_file(weights)
    if actual != manifest["weights_sha256"]:
        raise SystemExit(f"Weights SHA-256 mismatch: expected {manifest['weights_sha256']}, got {actual}")
    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = [model.names[i] for i in range(len(model.names))]
    if names != manifest["classes"]:
        raise SystemExit("Model class names differ from manifest; publish a new manifest first.")

    def predict(image):
        result = model.predict(image, imgsz=manifest["imgsz"], conf=conf, device=device, verbose=False)[0]
        boxes = result.boxes
        return list(zip(boxes.cls.tolist(), boxes.conf.tolist(), boxes.xyxy.tolist()))

    return Detector(predict, model_version=f"{manifest['model_id']}@{actual[:12]}",
                    preprocess_mode=mode, names=names)


def _image_from_multipart(content_type: str, body: bytes) -> bytes:
    message = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    if not message.is_multipart():
        raise ValueError("expected multipart/form-data")
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "image":
            return part.get_payload(decode=True) or b""
    raise ValueError("missing image field")


def make_handler(detector: Detector, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "meal-planner-inference/1"

        def log_message(self, fmt, *args):  # 不記錄請求內容或照片
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {token}")

        def do_GET(self):
            if urlsplit(self.path).path != "/v1/health":
                return self._send(404, {"error": "not_found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            self._send(200, {"status": "ok", "model_version": detector.model_version,
                             "preprocess": detector.preprocess_mode, "classes": len(detector.names)})

        def do_POST(self):
            url = urlsplit(self.path)
            if url.path != "/v1/detect":
                return self._send(404, {"error": "not_found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BYTES:
                return self._send(413 if length > MAX_BYTES else 400, {"error": "invalid_size"})
            body = self.rfile.read(length)
            mode = parse_qs(url.query).get("preprocess", [None])[0]
            try:
                data = _image_from_multipart(self.headers.get("Content-Type", ""), body)
                result = detector.detect(data, mode)
            except ValueError as exc:
                return self._send(400, {"error": "invalid_request", "message": str(exc)})
            except Exception:
                return self._send(500, {"error": "inference_failed"})
            self._send(200, result)

    return Handler


def main() -> None:
    token = os.environ.get("INFERENCE_TOKEN")
    if not token:
        raise SystemExit("Set INFERENCE_TOKEN")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    weights = Path(os.environ.get("INFERENCE_WEIGHTS") or ROOT / manifest["weights_path"])
    if not weights.is_file():
        raise SystemExit(f"Weights not found: {weights}")
    mode = os.environ.get("INFERENCE_PREPROCESS", manifest["preprocess"])
    if mode not in preprocess.PREPROCESSORS:
        raise SystemExit(f"Unknown INFERENCE_PREPROCESS {mode!r}")
    detector = load_detector(manifest, weights, device=os.environ.get("INFERENCE_DEVICE", "cpu"),
                             conf=float(os.environ.get("INFERENCE_CONF", "0.25")), mode=mode)
    host = os.environ.get("INFERENCE_HOST", "127.0.0.1")
    port = int(os.environ.get("INFERENCE_PORT", "8765"))
    print(f"Serving {detector.model_version} (preprocess={mode}) on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), make_handler(detector, token)).serve_forever()


if __name__ == "__main__":
    main()
