import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from top_us_stock_tickers.freshness import snapshot_is_current_utc_day


class SnapshotFreshnessTests(unittest.TestCase):
    def test_accepts_a_snapshot_generated_on_the_current_utc_day(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps({"generatedAt": "2026-09-02T00:05:00Z"}),
                encoding="utf-8",
            )

            self.assertTrue(
                snapshot_is_current_utc_day(
                    manifest_path,
                    now=datetime(2026, 9, 2, 23, 59, tzinfo=UTC),
                )
            )

            manifest_path.write_text(
                json.dumps({"generatedAt": "2026-09-01T20:05:00-04:00"}),
                encoding="utf-8",
            )
            self.assertTrue(
                snapshot_is_current_utc_day(
                    manifest_path,
                    now=datetime(2026, 9, 2, 0, 10, tzinfo=UTC),
                )
            )

    def test_rejects_a_snapshot_from_an_earlier_utc_day(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps({"generatedAt": "2026-09-01T23:59:59Z"}),
                encoding="utf-8",
            )

            self.assertFalse(
                snapshot_is_current_utc_day(
                    manifest_path,
                    now=datetime(2026, 9, 2, 0, 1, tzinfo=UTC),
                )
            )

    def test_treats_a_missing_or_invalid_manifest_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            self.assertFalse(snapshot_is_current_utc_day(manifest_path))

            manifest_path.write_text(
                json.dumps({"generatedAt": "not-a-timestamp"}),
                encoding="utf-8",
            )
            self.assertFalse(snapshot_is_current_utc_day(manifest_path))

            manifest_path.write_text("not json", encoding="utf-8")
            self.assertFalse(snapshot_is_current_utc_day(manifest_path))


class ScheduledWorkflowContractTests(unittest.TestCase):
    def test_primary_and_fallback_schedules_use_the_freshness_guard(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/daily_update.yml").read_text(
            encoding="utf-8"
        )
        fallback_guard = (
            "github.event.schedule != '47 12 * * 1-5' || "
            "steps.freshness.outputs.current != 'true'"
        )

        self.assertIn('cron: "17 10 * * 1-5"', workflow)
        self.assertIn('cron: "47 12 * * 1-5"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("group: daily-stock-ticker-update", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("id: freshness", workflow)
        self.assertIn(
            "python -m top_us_stock_tickers.freshness data/v2/manifest.json",
            workflow,
        )
        self.assertEqual(workflow.count(f"if: {fallback_guard}"), 2)


if __name__ == "__main__":
    unittest.main()
