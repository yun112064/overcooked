"""demo-3d 接線：app.main 掛上新路由、compose 的 frontend 改建 frontend-3d（不連資料庫、不啟動容器）。"""
import os
import re
import unittest
from pathlib import Path

# 設定物件要求 DATABASE_URL；這裡只匯入 app.main 讀路由表，不連資料庫（CI 已有自己的設定）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ("get", "/meal-requests/{request_id}/recipes"),
    ("post", "/meal-requests/{request_id}/nutrition-preview"),
    ("post", "/meal-requests/{request_id}/menu-rejections"),
    ("post", "/meal-requests/{request_id}/knowledge-tips"),
    ("post", "/meal-requests/{request_id}/t04-data"),
    ("post", "/sim-market/handoffs"),
    ("post", "/sim-market/checkout"),
    ("get", "/sim-market/catalog"),
    ("post", "/assistant/chat"),
    ("post", "/assistant/recipes"),
    ("get", "/recipe-covers/{release_id}/{recipe_id}"),
}


def route_table():
    from app.main import app
    return {(method, path) for path, ops in app.openapi()["paths"].items() for method in ops}


def compose_text():
    return (ROOT / "compose.yaml").read_text(encoding="utf-8")


def compose_services():
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(compose_text())["services"]


class RouteTableTests(unittest.TestCase):
    def test_demo3d_routes_are_mounted(self):
        self.assertEqual(sorted(EXPECTED - route_table()), [])

    def test_real_checkout_orders_route_is_not_ported(self):
        self.assertFalse([path for _, path in route_table() if path.startswith("/sim-market/orders")])


class ComposeWiringTests(unittest.TestCase):
    def test_frontend_builds_frontend_3d_and_old_frontend_stays_on_disk(self):
        services = compose_services()
        if services is not None:
            self.assertEqual(services["frontend"]["build"]["context"], "./frontend-3d")
        else:
            block = re.search(r"^  frontend:\n(?:    .*\n|\n)*", compose_text(), re.M).group(0)
            self.assertIn("context: ./frontend-3d", block)
        self.assertTrue((ROOT / "frontend").is_dir())

    def test_backend_release_binding_and_market_default_to_empty(self):
        keys = ("GENERATION_RECIPE_RELEASE_ID", "GENERATION_NUTRITION_IMPORT_ID",
                "GENERATION_FOOD_MAPPING_VERSION", "SIM_MARKET_URL")
        services = compose_services()
        for key in keys:
            if services is not None:
                self.assertEqual(services["backend"]["environment"][key], "${%s:-}" % key, key)
            else:
                self.assertIn("%s: ${%s:-}" % (key, key), compose_text(), key)
        if services is not None:
            self.assertNotIn("SIM_MARKET_REAL_CHECKOUT_ENABLED", services["backend"]["environment"])
            self.assertEqual(services["sim-supermarket"]["profiles"], ["sim-market"])


if __name__ == "__main__":
    unittest.main()
