import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from top_us_stock_tickers.api import (
    API_VERSION,
    SlidingWindowRateLimiter,
    SnapshotStore,
    run,
    store,
)


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_each_client_independently_and_recovers(self):
        limiter = SlidingWindowRateLimiter(requests=2, window_seconds=10)

        first = await limiter.check("client-a", now=100)
        second = await limiter.check("client-a", now=101)
        blocked = await limiter.check("client-a", now=102)
        other_client = await limiter.check("client-b", now=102)
        recovered = await limiter.check("client-a", now=111)

        self.assertTrue(first.allowed)
        self.assertEqual(first.remaining, 1)
        self.assertTrue(second.allowed)
        self.assertEqual(second.remaining, 0)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after, 8)
        self.assertTrue(other_client.allowed)
        self.assertTrue(recovered.allowed)

    async def test_parallel_requests_cannot_exceed_the_limit(self):
        limiter = SlidingWindowRateLimiter(requests=3, window_seconds=60)

        decisions = await asyncio.gather(
            *(limiter.check("same-client", now=100) for _ in range(10))
        )

        self.assertEqual(sum(decision.allowed for decision in decisions), 3)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repository_root = Path(__file__).resolve().parents[1]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        environment = os.environ.copy()
        environment["PORT"] = str(port)
        cls.base_url = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "top_us_stock_tickers.api"],
            cwd=repository_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read()
                raise RuntimeError(f"API exited during startup:\n{output}")
            try:
                with urlopen(f"{cls.base_url}/health", timeout=1):
                    return
            except URLError:
                time.sleep(0.05)
        cls.server.terminate()
        raise RuntimeError("API did not become healthy within 10 seconds")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)
        cls.server.stdout.close()

    def request(self, path, *, params=None, headers=None):
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers=headers or {})
        try:
            with urlopen(request, timeout=5) as response:
                content = response.read()
                return response.status, content, response.headers
        except HTTPError as error:
            try:
                content = error.read()
                return error.code, content, error.headers
            finally:
                error.close()

    def json_request(self, path, *, params=None):
        status, content, headers = self.request(path, params=params)
        return status, json.loads(content), headers

    def test_health_reports_the_loaded_snapshot(self):
        status, body, _headers = self.json_request("/health")

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["apiVersion"], API_VERSION)
        self.assertEqual(body["manifestSha256"], store.manifest_sha256)

    def test_root_serves_a_landing_page_with_the_main_destinations(self):
        status, content, headers = self.request("/")
        page = content.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("Top US Stock Tickers", page)
        self.assertIn('href="/docs"', page)
        self.assertIn('href="/api/v2/tickers?collection=sp500&amp;limit=5"', page)
        self.assertIn('href="/openapi.json"', page)
        self.assertIn('href="/health"', page)
        self.assertIn('href="/privacy"', page)
        self.assertIn("Legacy v1 stays put", page)
        self.assertIn('assets/fonts/Inter-Variable.woff2', page)
        self.assertIn('assets/fonts/SpaceGrotesk-Variable.woff2', page)
        self.assertNotIn("fonts.googleapis.com", page)
        self.assertNotIn("fonts.gstatic.com", page)

    def test_responses_include_browser_security_headers(self):
        for path in ("/", "/privacy", "/api/v2/meta", "/missing"):
            with self.subTest(path=path):
                _status, _content, headers = self.request(path)

                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertEqual(headers["Referrer-Policy"], "no-referrer")
                self.assertEqual(
                    headers["Permissions-Policy"],
                    "camera=(), geolocation=(), microphone=()",
                )
                self.assertEqual(
                    headers["Strict-Transport-Security"],
                    "max-age=31536000",
                )

    def test_static_pages_have_a_content_security_policy(self):
        for path in ("/", "/privacy"):
            with self.subTest(path=path):
                _status, _content, headers = self.request(path)

                policy = headers["Content-Security-Policy"]
                self.assertIn("default-src 'none'", policy)
                self.assertIn("frame-ancestors 'none'", policy)
                self.assertIn("font-src 'self'", policy)

    def test_serves_self_hosted_landing_page_fonts(self):
        for path in (
            "/assets/fonts/Inter-Variable.woff2",
            "/assets/fonts/SpaceGrotesk-Variable.woff2",
        ):
            with self.subTest(path=path):
                status, content, headers = self.request(path)

                self.assertEqual(status, 200)
                self.assertGreater(len(content), 10_000)
                self.assertEqual(headers["Content-Type"], "font/woff2")

    def test_privacy_notice_describes_actual_service_behavior(self):
        status, content, headers = self.request("/privacy")
        page = content.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("No accounts or advertising", page)
        self.assertIn("temporary counters by IP address", page)
        self.assertIn("security cookie", page)
        self.assertIn("Cloudflare", page)
        self.assertIn("Railway", page)
        self.assertIn("does not create a visitor database", page)
        self.assertNotIn("fonts.googleapis.com", page)
        self.assertNotIn("fonts.gstatic.com", page)

    def test_store_rejects_a_file_that_does_not_match_the_manifest(self):
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/v2").mkdir(parents=True)
            shutil.copy2(
                repository_root / "data/v2/manifest.json",
                root / "data/v2/manifest.json",
            )
            shutil.copy2(
                repository_root / "data/v2/tickers.csv",
                root / "data/v2/tickers.csv",
            )
            with (root / "data/v2/tickers.csv").open("a", encoding="utf-8") as file:
                file.write("\n")

            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                SnapshotStore(root)

    def test_store_rejects_a_reordered_file_with_a_matching_manifest(self):
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/v2").mkdir(parents=True)
            manifest_path = root / "data/v2/manifest.json"
            data_path = root / "data/v2/tickers.csv"
            shutil.copy2(
                repository_root / "data/v2/manifest.json",
                manifest_path,
            )
            shutil.copy2(repository_root / "data/v2/tickers.csv", data_path)

            lines = data_path.read_text(encoding="utf-8").splitlines()
            lines[1], lines[2] = lines[2], lines[1]
            data_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["data/v2/tickers.csv"]["sha256"] = hashlib.sha256(
                data_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "descending market_cap order"):
                SnapshotStore(root)

    def test_lists_a_paginated_collection_with_corrected_field_names(self):
        status, body, _headers = self.json_request(
            "/api/v2/tickers",
            params={"collection": "top_50", "limit": 2, "offset": 1},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 50)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 1)
        self.assertEqual(body["next_offset"], 3)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(
            [item["symbol"] for item in body["items"]],
            [ticker.symbol for ticker in store.collections["top_50"][1:3]],
        )
        self.assertIn("market_cap", body["items"][0])
        self.assertIn("sector", body["items"][0])
        self.assertIn("industry", body["items"][0])
        self.assertIn("country", body["items"][0])
        self.assertIn("ipo_year", body["items"][0])
        self.assertIn("percent_change", body["items"][0])
        self.assertNotIn("marketCap", body["items"][0])

    def test_sorts_market_cap_ascending_with_missing_values_last(self):
        _status, first_page, _headers = self.json_request(
            "/api/v2/tickers",
            params={"collection": "all", "limit": 1},
        )
        status, body, _headers = self.json_request(
            "/api/v2/tickers",
            params={
                "collection": "all",
                "sort": "market_cap",
                "order": "asc",
                "offset": max(0, first_page["total"] - 500),
                "limit": 500,
            },
        )

        self.assertEqual(status, 200)
        market_caps = [item["market_cap"] for item in body["items"]]
        populated = [value for value in market_caps if value is not None]
        self.assertEqual(populated, sorted(populated))
        if None in market_caps:
            first_missing = market_caps.index(None)
            self.assertTrue(
                all(value is None for value in market_caps[first_missing:])
            )

    def test_filters_by_search_and_sector(self):
        _status, apple, _headers = self.json_request("/api/v2/tickers/AAPL")
        status, body, _headers = self.json_request(
            "/api/v2/tickers",
            params={"q": "apple", "sector": apple["sector"]},
        )

        self.assertEqual(status, 200)
        self.assertIn("AAPL", [item["symbol"] for item in body["items"]])

    def test_all_and_us_collections_have_distinct_meanings(self):
        _status, all_rows, _headers = self.json_request(
            "/api/v2/tickers", params={"collection": "all", "limit": 1}
        )
        _status, us_rows, _headers = self.json_request(
            "/api/v2/tickers", params={"collection": "us", "limit": 1}
        )

        self.assertGreater(all_rows["total"], us_rows["total"])

    def test_symbol_lookup_normalizes_share_class_separator(self):
        dotted_status, dotted, _headers = self.json_request(
            "/api/v2/tickers/BRK.B"
        )
        slashed_status, slashed, _headers = self.json_request(
            "/api/v2/tickers/BRK/B"
        )

        self.assertEqual(dotted_status, 200)
        self.assertEqual(slashed_status, 200)
        self.assertEqual(dotted, slashed)

    def test_returns_404_for_an_unknown_symbol(self):
        status, _body, headers = self.json_request(
            "/api/v2/tickers/NOT-A-REAL-SYMBOL"
        )

        self.assertEqual(status, 404)
        self.assertNotIn("ETag", headers)

    def test_rejects_invalid_pagination(self):
        status, _body, _headers = self.json_request(
            "/api/v2/tickers", params={"limit": 501}
        )

        self.assertEqual(status, 422)

    def test_lists_sector_counts_for_the_all_collection(self):
        status, body, _headers = self.json_request("/api/v2/sectors")

        self.assertEqual(status, 200)
        self.assertEqual(
            sum(item["count"] for item in body["items"]),
            len(store.collections["all"]),
        )

    def test_lists_country_and_industry_reference_values(self):
        country_status, countries, _headers = self.json_request(
            "/api/v2/countries"
        )
        industry_status, industries, _headers = self.json_request(
            "/api/v2/industries"
        )

        self.assertEqual(country_status, 200)
        self.assertEqual(industry_status, 200)
        self.assertIn(
            "United States",
            [item["country"] for item in countries["items"]],
        )
        self.assertIn(
            "Semiconductors",
            [item["industry"] for item in industries["items"]],
        )

    def test_serves_snapshot_cache_headers_and_conditional_requests(self):
        first_status, _content, first_headers = self.request("/api/v2/meta")
        second_status, second_content, _headers = self.request(
            "/api/v2/meta",
            headers={"If-None-Match": first_headers["ETag"]},
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(first_headers["X-Manifest-SHA256"], store.manifest_sha256)
        self.assertEqual(first_headers["X-RateLimit-Limit"], "120")
        self.assertEqual(first_headers["X-RateLimit-Window"], "60")
        self.assertNotIn("X-RateLimit-Remaining", first_headers)
        self.assertEqual(second_status, 304)
        self.assertEqual(second_content, b"")

    def test_openapi_describes_the_v2_routes(self):
        status, body, _headers = self.json_request("/openapi.json")

        self.assertEqual(status, 200)
        self.assertEqual(body["info"]["version"], API_VERSION)
        self.assertIn("/api/v2/tickers", body["paths"])

    def test_production_server_has_resource_limits(self):
        with (
            patch.dict(os.environ, {"API_MAX_CONCURRENCY": "64"}),
            patch("uvicorn.run") as uvicorn_run,
        ):
            run()

        _args, kwargs = uvicorn_run.call_args
        self.assertEqual(kwargs["limit_concurrency"], 64)
        self.assertEqual(kwargs["timeout_keep_alive"], 5)
        self.assertEqual(kwargs["timeout_graceful_shutdown"], 10)

    def test_rejects_an_invalid_concurrency_limit(self):
        with (
            patch.dict(os.environ, {"API_MAX_CONCURRENCY": "0"}),
            self.assertRaisesRegex(ValueError, "API_MAX_CONCURRENCY"),
        ):
            run()

    def test_rate_limiter_rejects_excess_requests(self):
        repository_root = Path(__file__).resolve().parents[1]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        environment = os.environ.copy()
        environment.update(
            {
                "PORT": str(port),
                "API_RATE_LIMIT_REQUESTS": "2",
                "API_RATE_LIMIT_WINDOW_SECONDS": "60",
            }
        )
        server = subprocess.Popen(
            [sys.executable, "-m", "top_us_stock_tickers.api"],
            cwd=repository_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with urlopen(f"{base_url}/health", timeout=1):
                        break
                except URLError:
                    time.sleep(0.05)
            else:
                self.fail("Rate-limited API did not become healthy")

            request_headers = {"CF-Connecting-IP": "203.0.113.10"}
            responses = []
            for _index in range(3):
                request = Request(
                    f"{base_url}/api/v2/meta", headers=request_headers
                )
                try:
                    with urlopen(request, timeout=5) as response:
                        responses.append(
                            (response.status, response.read(), response.headers)
                        )
                except HTTPError as error:
                    with error:
                        responses.append(
                            (error.code, error.read(), error.headers)
                        )

            self.assertEqual([item[0] for item in responses], [200, 200, 429])
            _status, content, headers = responses[-1]
            self.assertIn("Rate limit exceeded", content.decode("utf-8"))
            self.assertGreaterEqual(int(headers["Retry-After"]), 1)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-RateLimit-Remaining"], "0")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
            self.assertIn(
                "Retry-After", headers["Access-Control-Expose-Headers"]
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server.stdout.close()


if __name__ == "__main__":
    unittest.main()
