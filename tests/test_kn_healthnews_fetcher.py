"""G07 健康醫療網抓取禮節：robots.txt、限速 ≥2 秒、可識別 User-Agent、重試退避（假傳輸，不連網）。"""
import unittest

ROBOTS = "User-agent: PetalBot\nDisallow: /\n\nUser-agent: *\nDisallow:/rss_file/\n"


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeTransport:
    """routes：url → [回應或例外, ...]，依序取用；最後一個重複使用。"""

    def __init__(self, clock, routes):
        self.clock = clock
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers, timeout):
        from knowledge_rag.fetcher import Response
        self.calls.append((self.clock.now, url, dict(headers)))
        seq = self.routes.get(url, [Response(404, "")])
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item


class PoliteFetcherTests(unittest.TestCase):
    def make(self, routes, **kw):
        from knowledge_rag.fetcher import PoliteFetcher, Response
        self.clock = FakeClock()
        routes.setdefault("https://www.healthnews.com.tw/robots.txt", [Response(200, ROBOTS)])
        self.transport = FakeTransport(self.clock, routes)
        return PoliteFetcher(self.transport, user_agent="overcooked-knowledge-bot/1.0 (+contact)",
                             clock=self.clock.time, sleep=self.clock.sleep, **kw)

    def ok(self, text="ok"):
        from knowledge_rag.fetcher import Response
        return Response(200, text)

    def test_requests_carry_user_agent_and_are_spaced_at_least_two_seconds(self):
        f = self.make({"https://www.healthnews.com.tw/article/1": [self.ok("a")],
                       "https://www.healthnews.com.tw/article/2": [self.ok("b")]})
        self.assertEqual(f.get("https://www.healthnews.com.tw/article/1"), "a")
        self.assertEqual(f.get("https://www.healthnews.com.tw/article/2"), "b")
        times = [t for t, _, _ in self.transport.calls]
        self.assertEqual([u for _, u, _ in self.transport.calls][0], "https://www.healthnews.com.tw/robots.txt")
        self.assertTrue(all(b - a >= 2.0 for a, b in zip(times, times[1:])), times)
        self.assertTrue(all(h["User-Agent"] == "overcooked-knowledge-bot/1.0 (+contact)"
                            for _, _, h in self.transport.calls))

    def test_interval_below_two_seconds_is_refused(self):
        with self.assertRaises(ValueError):
            self.make({}, min_interval=1.0)

    def test_robots_disallowed_path_is_never_requested(self):
        from knowledge_rag.fetcher import RobotsDisallowed
        f = self.make({})
        with self.assertRaises(RobotsDisallowed):
            f.get("https://www.healthnews.com.tw/rss_file/x.xml")
        self.assertNotIn("https://www.healthnews.com.tw/rss_file/x.xml", [u for _, u, _ in self.transport.calls])

    def test_robots_fetch_failure_blocks_crawling(self):
        from knowledge_rag.fetcher import FetchError, Response
        f = self.make({"https://www.healthnews.com.tw/robots.txt": [Response(503, "")]}, retries=0)
        with self.assertRaises(FetchError):
            f.get("https://www.healthnews.com.tw/article/1")
        self.assertEqual([u for _, u, _ in self.transport.calls], ["https://www.healthnews.com.tw/robots.txt"])

    def test_missing_robots_allows_crawling(self):
        from knowledge_rag.fetcher import Response
        f = self.make({"https://www.healthnews.com.tw/robots.txt": [Response(404, "")],
                       "https://www.healthnews.com.tw/article/1": [self.ok("a")]})
        self.assertEqual(f.get("https://www.healthnews.com.tw/article/1"), "a")

    def test_transient_errors_retry_with_backoff_then_succeed(self):
        from knowledge_rag.fetcher import Response
        url = "https://www.healthnews.com.tw/article/1"
        f = self.make({url: [Response(503, ""), ConnectionError("reset"), self.ok("a")]}, retries=3, backoff=5.0)
        self.assertEqual(f.get(url), "a")
        times = [t for t, u, _ in self.transport.calls if u == url]
        self.assertEqual(len(times), 3)
        self.assertGreaterEqual(times[1] - times[0], 5.0)
        self.assertGreaterEqual(times[2] - times[1], 10.0)

    def test_gives_up_after_retries(self):
        from knowledge_rag.fetcher import FetchError, Response
        url = "https://www.healthnews.com.tw/article/1"
        f = self.make({url: [Response(500, "")]}, retries=2)
        with self.assertRaises(FetchError):
            f.get(url)
        self.assertEqual(len([u for _, u, _ in self.transport.calls if u == url]), 3)

    def test_not_found_returns_none_without_retry(self):
        url = "https://www.healthnews.com.tw/article/999999"
        f = self.make({})
        self.assertIsNone(f.get(url))
        self.assertEqual(len([u for _, u, _ in self.transport.calls if u == url]), 1)

    def test_only_the_configured_site_is_fetched(self):
        f = self.make({})
        with self.assertRaises(ValueError):
            f.get("https://www.google.com/search?q=healthnews")


if __name__ == "__main__":
    unittest.main()
