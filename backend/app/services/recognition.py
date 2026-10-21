"""T09 蔬菜辨識：影像解碼與正規化、團隊電腦推論轉接器、所有辨識到的蔬菜。

- 後端實際解碼影像、依 EXIF 轉正，再重新編碼成 JPEG 保存（去除 GPS 等中繼資料）。
- 推論服務未設定、連不上或逾時 → unavailable（保留照片可重試），與「沒有有效辨識」分開。
- 畫面列出每種有效辨識到的蔬菜（同類別合併，依信心由高到低）；信心與框線只存內部供評估。
"""
import io
import json
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.api.errors import invalid_input
from app.services.team_link import TeamCredentials, failure_reason, settings_credentials
from app.core.config import settings

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_EDGE = 2048  # 送推論與保存前縮到合理大小


class RecognizerUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: list[float] | None


@dataclass(frozen=True)
class RecognizerOutput:
    model_version: str
    detections: list[Detection]


class Recognizer(Protocol):
    def detect(self, jpeg: bytes) -> RecognizerOutput: ...


def normalize_image(raw: bytes) -> tuple[bytes, int, int]:
    """驗證並正規化上傳影像；任何不合格都以可讀原因拒絕。"""
    if len(raw) > settings.upload_max_bytes:
        raise invalid_input(f"照片太大，請小於 {settings.upload_max_bytes // (1024 * 1024)} MB。",
                            field="image", reason="IMAGE_TOO_LARGE")
    if not raw:
        raise invalid_input("沒有收到照片。", field="image", reason="IMAGE_EMPTY")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            width, height = probe.size
            probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise invalid_input("無法讀取這張照片，請改用 JPEG、PNG 或 WebP。",
                            field="image", reason="IMAGE_UNREADABLE") from None
    if fmt not in ALLOWED_FORMATS:
        raise invalid_input("只支援 JPEG、PNG 或 WebP 照片。", field="image", reason="IMAGE_FORMAT")
    if width * height > settings.upload_max_pixels:
        raise invalid_input("照片解析度過高，請縮小後再上傳。", field="image", reason="IMAGE_TOO_MANY_PIXELS")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)  # 依手機拍攝方向轉正
            img = img.convert("RGB")
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90)  # 重新編碼不帶 EXIF
            return out.getvalue(), img.width, img.height
    except (OSError, ValueError):
        raise invalid_input("無法讀取這張照片，請重新拍攝或換一張。",
                            field="image", reason="IMAGE_UNREADABLE") from None


@lru_cache(maxsize=1)
def _labels(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def display_name(label: str) -> tuple[str, str | None]:
    """類別 → (顯示名稱, 標準食材 ID)。未對照的類別直接顯示原名，由使用者確認。"""
    entry = _labels(str(settings.recognition_labels_path))["labels"].get(label)
    if entry:
        return entry["name"], entry.get("ingredient_id")
    return label, None


def vegetable_detections(detections: list[Detection]) -> list[Detection]:
    """有效偵測依類別合併（取該類最高信心），依信心由高到低；無效或低於門檻的不算。"""
    threshold = float(settings.recognition_min_confidence)
    best: dict[str, Detection] = {}
    for d in detections:
        if d.label.strip() and math.isfinite(d.confidence) and threshold <= d.confidence <= 1:
            if d.label not in best or d.confidence > best[d.label].confidence:
                best[d.label] = d
    return sorted(best.values(), key=lambda d: d.confidence, reverse=True)


def top_detection(detections: list[Detection]) -> Detection | None:
    found = vegetable_detections(detections)
    return found[0] if found else None


def suggestions(stored: list[dict] | None) -> list[dict]:
    """由保存的偵測明細產生畫面清單：每種蔬菜一筆名稱與標準食材 ID，對到同一名稱的只列一次。"""
    dets = [Detection(d["label"], d["confidence"], d.get("box")) for d in stored or []]
    out, seen = [], set()
    for d in vegetable_detections(dets):
        name, ingredient_id = display_name(d.label)
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "ingredient_id": ingredient_id})
    return out


def parse_output(body: dict) -> RecognizerOutput:
    """解析推論服務回應；格式不符視為服務異常，不當成沒有結果。"""
    try:
        version = body["model_version"]
        rows = body["detections"]
        if not isinstance(version, str) or not version or not isinstance(rows, list):
            raise ValueError
        detections = []
        for row in rows:
            label, conf = row["label"], row["confidence"]
            if not isinstance(label, str) or isinstance(conf, bool) or not isinstance(conf, (int, float)):
                raise ValueError
            box = row.get("box")
            detections.append(Detection(label, float(conf), [float(v) for v in box] if box else None))
        return RecognizerOutput(version, detections)
    except (KeyError, TypeError, ValueError):
        raise RecognizerUnavailable("INVALID_RESPONSE") from None


class HttpRecognizer:
    """團隊電腦推論服務：POST {url}/v1/detect，Bearer token，經 HTTPS 或加密通道。"""

    def __init__(self, url: str, credentials: TeamCredentials, timeout: float):
        self.url = url.rstrip("/")
        self.credentials = credentials
        self.timeout = timeout

    def detect(self, jpeg: bytes) -> RecognizerOutput:
        headers = self.credentials.headers()
        try:
            res = httpx.post(f"{self.url}/v1/detect", headers=headers, timeout=self.timeout,
                             files={"image": ("photo.jpg", jpeg, "image/jpeg")})
        except httpx.TimeoutException:
            raise RecognizerUnavailable("TIMEOUT") from None
        except httpx.HTTPError:
            raise RecognizerUnavailable("UNREACHABLE") from None
        if (reason := failure_reason(res.status_code)) is not None:
            raise RecognizerUnavailable(reason)
        try:
            body = res.json()
        except ValueError:
            raise RecognizerUnavailable("INVALID_RESPONSE") from None
        return parse_output(body)


def default_recognizer() -> Recognizer | None:
    if not settings.recognition_service_url:
        return None
    return HttpRecognizer(settings.recognition_service_url, settings_credentials(settings.recognition_service_token),
                          settings.recognition_timeout_seconds)


def _upload_dir(household_id) -> Path:
    return settings.project_data_root / "uploads" / "recognitions" / str(household_id)


def save_image(household_id, recognition_id: uuid.UUID, jpeg: bytes) -> str:
    folder = _upload_dir(household_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{recognition_id}.jpg"
    path.write_bytes(jpeg)
    return str(path.relative_to(settings.project_data_root)).replace("\\", "/")


def image_file(relative: str) -> Path:
    path = (settings.project_data_root / relative).resolve()
    root = settings.project_data_root.resolve()
    if root not in path.parents:
        raise FileNotFoundError(relative)
    return path


def run(recognizer: Recognizer | None, jpeg: bytes) -> dict:
    """回傳要寫入紀錄的欄位。"""
    if recognizer is None:
        return {"status": "unavailable", "unavailable_reason": "NOT_CONFIGURED"}
    try:
        output = recognizer.detect(jpeg)
    except RecognizerUnavailable as exc:
        return {"status": "unavailable", "unavailable_reason": exc.reason}
    top = top_detection(output.detections)
    fields = {
        "model_version": output.model_version,
        "detections": [{"label": d.label, "confidence": d.confidence, "box": d.box} for d in output.detections],
        "unavailable_reason": None,
    }
    if top is None:
        return {**fields, "status": "no_result", "top_label": None, "top_name": None, "top_confidence": None}
    name, _ = display_name(top.label)
    return {**fields, "status": "succeeded", "top_label": top.label, "top_name": name,
            "top_confidence": Decimal(str(round(top.confidence, 5)))}
