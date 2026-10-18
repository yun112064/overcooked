"""推論服務測試：前處理與 HTTP 契約。以假模型代替 YOLO，不需要 PyTorch。

執行：python -m unittest discover -s inference -p 'test_*.py' -v
"""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preprocess  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-123"


def noisy_jpeg() -> bytes:
    rng = np.random.default_rng(0)
    image = np.full((48, 64, 3), 120, dtype=np.uint8)
    salt = rng.random(image.shape[:2]) < 0.05
    image[salt] = 255
    ok, buf = cv2.imencode(".png", image)
    return buf.tobytes()


def multipart(data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"p.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class PreprocessTests(unittest.TestCase):
    def test_denoise_matches_training_transform(self):
        image = preprocess.decode(noisy_jpeg())
        np.testing.assert_array_equal(preprocess.apply(image, "denoise"), cv2.medianBlur(image, 3))
        np.testing.assert_array_equal(preprocess.apply(image, "none"), image)
        # 中值濾波應去掉大部分椒鹽雜訊
        self.assertLess((preprocess.apply(image, "denoise") == 255).mean(), (image == 255).mean() / 5)

    def test_invalid_inputs(self):
        for bad in (b"", b"not an image"):
            with self.assertRaises(ValueError):
                preprocess.decode(bad)
        with self.assertRaises(ValueError):
            preprocess.apply(np.zeros((2, 2, 3), np.uint8), "sharpen")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def fake_predict(image):
            self.seen.append(image.copy())
            return [(3, 0.61, [1, 2, 30, 40]), (18, 0.88, [5, 5, 20, 20])]

        self.detector = server.Detector(fake_predict, model_version="test-model@abc", preprocess_mode="denoise",
                                        names=["c0", "c1", "c2", "Cabbage"] + [f"c{i}" for i in range(4, 18)] + ["bok-choy"])
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.detector, TOKEN))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, path, data=None, *, token=TOKEN, content_type=None):
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_requires_token(self):
        body, ctype = multipart(noisy_jpeg())
        self.assertEqual(self.request("/v1/detect", body, token=None, content_type=ctype)[0], 401)
        self.assertEqual(self.request("/v1/detect", body, token="wrong", content_type=ctype)[0], 401)
        self.assertEqual(self.request("/v1/health", token=None)[0], 401)

    def test_detect_applies_default_denoise_and_returns_contract(self):
        body, ctype = multipart(noisy_jpeg())
        status, result = self.request("/v1/detect", body, content_type=ctype)
        self.assertEqual(status, 200)
        self.assertEqual(result["model_version"], "test-model@abc+denoise")
        self.assertEqual([d["label"] for d in result["detections"]], ["Cabbage", "bok-choy"])
        self.assertEqual(result["detections"][1]["confidence"], 0.88)
        original = preprocess.decode(noisy_jpeg())
        np.testing.assert_array_equal(self.seen[0], cv2.medianBlur(original, 3))

    def test_preprocess_can_be_overridden_for_evaluation(self):
        body, ctype = multipart(noisy_jpeg())
        status, result = self.request("/v1/detect?preprocess=none", body, content_type=ctype)
        self.assertEqual((status, result["model_version"]), (200, "test-model@abc+none"))
        np.testing.assert_array_equal(self.seen[0], preprocess.decode(noisy_jpeg()))
        status, result = self.request("/v1/detect?preprocess=bogus", body, content_type=ctype)
        self.assertEqual(status, 400)

    def test_bad_requests(self):
        body, ctype = multipart(b"not an image")
        self.assertEqual(self.request("/v1/detect", body, content_type=ctype)[0], 400)
        self.assertEqual(self.request("/v1/detect", b"x", content_type="text/plain")[0], 400)
        status, health = self.request("/v1/health")
        self.assertEqual((status, health["preprocess"], health["classes"]), (200, "denoise", 19))

    def test_weights_hash_must_match_manifest(self):
        weights = Path(self.id().replace(".", "_") + ".pt")
        weights.write_bytes(b"not the model")
        try:
            manifest = json.loads(server.MANIFEST_PATH.read_text(encoding="utf-8"))
            with self.assertRaises(SystemExit):
                server.load_detector(manifest, weights, device="cpu", conf=0.25, mode="denoise")
        finally:
            weights.unlink()


class ManifestTests(unittest.TestCase):
    def test_manifest_matches_cloud_label_map(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "inference/model_manifest.json").read_text(encoding="utf-8"))
        labels = json.loads((root / "config/recognition_labels.json").read_text(encoding="utf-8"))["labels"]
        self.assertEqual(sorted(manifest["classes"]), sorted(labels))
        self.assertIn(manifest["preprocess"], preprocess.PREPROCESSORS)


if __name__ == "__main__":
    unittest.main()
