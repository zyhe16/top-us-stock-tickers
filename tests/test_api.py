import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api import API_VERSION, SnapshotStore, store


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
            [sys.executable, "api.py"],
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
        self.assertIn("No cookies or analytics", page)
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
        self.assertIn("market_cap", body["items"][0])
        self.assertIn("sector", body["items"][0])
        self.assertIn("industry", body["items"][0])
        self.assertIn("country", body["items"][0])
        self.assertIn("ipo_year", body["items"][0])
        self.assertIn("percent_change", body["items"][0])
        self.assertNotIn("marketCap", body["items"][0])

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
        self.assertEqual(second_status, 304)
        self.assertEqual(second_content, b"")

    def test_openapi_describes_the_v2_routes(self):
        status, body, _headers = self.json_request("/openapi.json")

        self.assertEqual(status, 200)
        self.assertEqual(body["info"]["version"], API_VERSION)
        self.assertIn("/api/v2/tickers", body["paths"])


if __name__ == "__main__":
    unittest.main()
