import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from update_tickers import (
    LEGACY_COLUMNS,
    V2_COLUMNS,
    build_output_frames,
    build_v2_frame,
    publish_release,
    publish_snapshot,
    publish_v2_snapshot,
    validate_output_frames,
    validate_published_snapshot,
    validate_source_snapshot,
    validate_v2_frame,
    validate_v2_published_snapshot,
)


def ticker(symbol, market_cap, sector="Technology"):
    return {
        "symbol": symbol,
        "name": f"{symbol} Company",
        "price": 10.0,
        "marketCap": market_cap,
        "volume": 100,
        "industry": sector,
    }


class SourceValidationTests(unittest.TestCase):
    def test_rejects_an_implausibly_small_nasdaq_snapshot(self):
        rows = [ticker("AAA", 100)]

        with self.assertRaisesRegex(ValueError, "NASDAQ returned too few total"):
            validate_source_snapshot(
                rows,
                rows,
                ["AAA"],
                rows,
                min_us_tickers=1,
                min_all_tickers=2,
                min_sp500_symbols=1,
                min_sp500_match_rate=1.0,
            )

    def test_rejects_a_low_sp500_match_rate(self):
        all_rows = [ticker("AAA", 100), ticker("BBB", 90)]

        with self.assertRaisesRegex(ValueError, "S&P 500 match rate"):
            validate_source_snapshot(
                all_rows,
                all_rows,
                ["AAA", "BBB"],
                [all_rows[0]],
                min_us_tickers=2,
                min_all_tickers=2,
                min_sp500_symbols=2,
                min_sp500_match_rate=0.75,
            )

    def test_rejects_duplicate_symbols(self):
        duplicate_rows = [ticker("AAA", 100), ticker("AAA", 90)]

        with self.assertRaisesRegex(ValueError, "duplicate symbols"):
            validate_source_snapshot(
                duplicate_rows,
                duplicate_rows,
                ["AAA"],
                [duplicate_rows[0]],
                min_us_tickers=1,
                min_all_tickers=1,
                min_sp500_symbols=1,
                min_sp500_match_rate=1.0,
            )

    def test_rejects_rows_missing_legacy_fields(self):
        incomplete = ticker("AAA", 100)
        del incomplete["volume"]

        with self.assertRaisesRegex(ValueError, "missing fields: volume"):
            validate_source_snapshot(
                [incomplete],
                [incomplete],
                ["AAA"],
                [incomplete],
                min_us_tickers=1,
                min_all_tickers=1,
                min_sp500_symbols=1,
                min_sp500_match_rate=1.0,
            )


class SnapshotPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tickers = [
            ticker("AAA", 300, "Technology"),
            ticker("BBB", 200, "Finance"),
            ticker("CCC", 100, ""),
        ]
        self.sp500_tickers = self.tickers[:2]

    def test_builds_the_legacy_schema_without_renaming_columns(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)

        self.assertEqual(list(frames["tickers/all.csv"].columns), list(LEGACY_COLUMNS))
        self.assertEqual(
            list(frames["tickers/all.csv"]["symbol"]),
            ["AAA", "BBB", "CCC"],
        )
        self.assertIn("by_industry/technology.csv", frames)
        self.assertIn("by_industry/uncategorized.csv", frames)

    def test_rejects_colliding_industry_filenames(self):
        rows = [
            ticker("AAA", 200, "A&B"),
            ticker("BBB", 100, "AB"),
        ]

        with self.assertRaisesRegex(ValueError, "Industry filename collision"):
            build_output_frames(rows, rows)

    def test_rejects_unsorted_legacy_all_even_when_top_lists_match(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)
        unsorted = frames["tickers/all.csv"].iloc[[1, 0, 2]].reset_index(
            drop=True
        )
        frames["tickers/all.csv"] = unsorted
        for size in (50, 100, 200):
            frames[f"tickers/top_{size}.csv"] = unsorted.head(size)

        with self.assertRaisesRegex(ValueError, "descending marketCap order"):
            validate_output_frames(frames)

    def test_rejects_unknown_source_metadata_fields(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "Unknown source metadata fields: generated"
            ):
                publish_snapshot(
                    frames,
                    directory,
                    source_metadata={"generated": "2026-08-28T00:00:00Z"},
                )

    def test_publishes_a_manifest_and_preserves_established_csv_paths(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "by_industry").mkdir()
            (root / "by_industry" / "stale.csv").write_text("old\n")
            (root / "by_industry" / "notes.txt").write_text("keep me\n")

            manifest = publish_snapshot(
                frames,
                root,
                source_metadata={
                    "generatedAt": "2026-08-28T00:00:00Z",
                    "nasdaqRowsFetched": 3,
                    "publishedUnitedStatesRows": 3,
                    "sp500SymbolsFetched": 2,
                    "sp500SymbolsMatched": 2,
                },
            )

            self.assertEqual(
                (root / "by_industry" / "stale.csv").read_text(),
                ",".join(LEGACY_COLUMNS) + "\n",
            )
            self.assertEqual(
                (root / "by_industry" / "notes.txt").read_text(),
                "keep me\n",
            )
            self.assertEqual(manifest["datasetContractVersion"], "legacy-v1")
            self.assertEqual(
                json.loads((root / "manifest.json").read_text()),
                manifest,
            )
            validate_published_snapshot(root)

    def test_restores_the_previous_snapshot_when_replacement_fails(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tickers").mkdir()
            (root / "by_industry").mkdir()
            old_all = root / "tickers" / "all.csv"
            old_all.write_text("old all\n")
            old_sector = root / "by_industry" / "old.csv"
            old_sector.write_text("old sector\n")

            original_replace = os.replace

            def fail_on_new_sp500(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    ".ticker-update-" in str(source_path)
                    and destination_path == root / "tickers" / "sp500.csv"
                ):
                    raise OSError("simulated replacement failure")
                return original_replace(source, destination)

            with mock.patch(
                "snapshot_publication.os.replace",
                side_effect=fail_on_new_sp500,
            ):
                with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                    publish_snapshot(
                        frames,
                        root,
                        source_metadata={"generatedAt": "2026-08-28T00:00:00Z"},
                    )

            self.assertEqual(old_all.read_text(), "old all\n")
            self.assertEqual(old_sector.read_text(), "old sector\n")
            self.assertFalse((root / "tickers" / "sp500.csv").exists())

    def test_restores_both_contracts_when_release_replacement_fails(self):
        frames = build_output_frames(self.tickers, self.sp500_tickers)
        repository_root = Path(__file__).resolve().parents[1]
        v2_frame = pd.read_csv(
            repository_root / "data/v2/tickers.csv",
            keep_default_na=False,
            na_values=[""],
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tickers").mkdir(parents=True)
            (root / "data/v2").mkdir(parents=True)
            old_all = root / "tickers/all.csv"
            old_v2 = root / "data/v2/tickers.csv"
            old_v2_manifest = root / "data/v2/manifest.json"
            old_all.write_bytes(b"old v1\n")
            old_v2.write_bytes(b"old v2\n")
            old_v2_manifest.write_bytes(b"old manifest\n")

            original_replace = os.replace

            def fail_late(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    ".ticker-release-" in str(source_path)
                    and destination_path == root / "tickers/sp500.csv"
                ):
                    raise OSError("simulated release failure")
                return original_replace(source, destination)

            with mock.patch(
                "snapshot_publication.os.replace",
                side_effect=fail_late,
            ):
                with self.assertRaisesRegex(OSError, "simulated release failure"):
                    publish_release(
                        frames,
                        v2_frame,
                        root,
                        source_metadata={"generatedAt": "2026-08-28T00:00:00Z"},
                    )

            self.assertEqual(old_all.read_bytes(), b"old v1\n")
            self.assertEqual(old_v2.read_bytes(), b"old v2\n")
            self.assertEqual(old_v2_manifest.read_bytes(), b"old manifest\n")
            self.assertFalse((root / "tickers/sp500.csv").exists())


class V2PublicationTests(unittest.TestCase):
    def test_rejects_an_empty_v2_symbol(self):
        repository_root = Path(__file__).resolve().parents[1]
        frame = pd.read_csv(
            repository_root / "data/v2/tickers.csv",
            keep_default_na=False,
            na_values=[""],
        )
        frame.loc[0, "symbol"] = ""

        with self.assertRaisesRegex(ValueError, "empty symbol"):
            validate_v2_frame(frame)

    def test_rejects_rows_outside_descending_market_cap_order(self):
        repository_root = Path(__file__).resolve().parents[1]
        frame = pd.read_csv(
            repository_root / "data/v2/tickers.csv",
            keep_default_na=False,
            na_values=[""],
        )
        frame.iloc[[0, 1]] = frame.iloc[[1, 0]].to_numpy()

        with self.assertRaisesRegex(ValueError, "descending market_cap order"):
            validate_v2_frame(frame)

    def test_publishes_the_richer_schema_without_touching_v1(self):
        rows = []
        for position in range(6_000):
            rows.append(
                {
                    "symbol": f"T{position}",
                    "name": f"Ticker {position}",
                    "price": 10.0,
                    "price_change": 1.0,
                    "percent_change": 10.0,
                    "market_cap": 10_000 - position,
                    "volume": 100,
                    "country": "United States" if position < 4_000 else "Canada",
                    "sector": "Technology",
                    "industry": "Computer Manufacturing",
                    "ipo_year": 2000,
                    "nasdaq_url": f"https://www.nasdaq.com/market-activity/stocks/t{position}",
                    "is_us_domiciled": position < 4_000,
                    "is_sp500": False,
                }
            )
        symbols = [f"T{position}" for position in range(450)]
        frame = build_v2_frame(rows, symbols)

        self.assertEqual(list(frame.columns), list(V2_COLUMNS))
        self.assertEqual(int(frame["is_sp500"].sum()), 450)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tickers").mkdir()
            legacy = root / "tickers/all.csv"
            legacy.write_bytes(b"legacy bytes\n")

            manifest = publish_v2_snapshot(
                frame,
                root,
                source_metadata={"generatedAt": "2026-08-28T00:00:00Z"},
            )

            self.assertEqual(legacy.read_bytes(), b"legacy bytes\n")
            self.assertEqual(manifest["datasetContractVersion"], "v2")
            validate_v2_published_snapshot(root)


class CheckedInSnapshotTests(unittest.TestCase):
    def test_checked_in_snapshot_matches_its_manifest(self):
        repository_root = Path(__file__).resolve().parents[1]

        validate_published_snapshot(repository_root)

    def test_checked_in_v2_snapshot_matches_its_manifest(self):
        repository_root = Path(__file__).resolve().parents[1]

        validate_v2_published_snapshot(repository_root)


if __name__ == "__main__":
    unittest.main()
