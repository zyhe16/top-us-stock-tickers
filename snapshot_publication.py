"""Build, validate, and atomically publish legacy v1 and v2 snapshots."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

import pandas as pd

from contracts import (
    LEGACY_COLUMNS,
    MANIFEST_SCHEMA_VERSION,
    MIN_ALL_TICKERS,
    MIN_SP500_TICKERS,
    MIN_US_TICKERS,
    V2_COLUMNS,
    V2_DATASET_CONTRACT_VERSION,
    load_v2_snapshot,
    normalize_symbol,
    sha256_file,
    v2_record_from_mapping,
    validate_descending_market_caps,
    validate_v2_records,
)


NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
WIKIPEDIA_SP500_RAW_URL = "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw"
TOP_LIST_SIZES = (50, 100, 200)
DATASET_CONTRACT_VERSION = "legacy-v1"
V2_DATA_PATH = "data/v2/tickers.csv"
V2_MANIFEST_PATH = "data/v2/manifest.json"


def utc_now():
    """Return a stable UTC timestamp for published metadata."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class SourceMetadata:
    """Typed source and provenance facts used by both manifests."""

    generated_at: str | None = None
    nasdaq_fetched_at: str | None = None
    wikipedia_fetched_at: str | None = None
    nasdaq_rows_fetched: int | None = None
    published_united_states_rows: int | None = None
    sp500_symbols_fetched: int | None = None
    sp500_symbols_matched: int | None = None
    sp500_match_rate: float | None = None
    provenance_status: str = "generated-by-updater"
    provenance_note: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping | None):
        if values is None:
            return cls()
        allowed = {
            "generatedAt",
            "nasdaqFetchedAt",
            "wikipediaFetchedAt",
            "nasdaqRowsFetched",
            "publishedUnitedStatesRows",
            "sp500SymbolsFetched",
            "sp500SymbolsMatched",
            "sp500MatchRate",
            "provenanceStatus",
            "provenanceNote",
        }
        unknown = set(values).difference(allowed)
        if unknown:
            raise ValueError(
                f"Unknown source metadata fields: {', '.join(sorted(unknown))}"
            )
        return cls(
            generated_at=values.get("generatedAt"),
            nasdaq_fetched_at=values.get("nasdaqFetchedAt"),
            wikipedia_fetched_at=values.get("wikipediaFetchedAt"),
            nasdaq_rows_fetched=values.get("nasdaqRowsFetched"),
            published_united_states_rows=values.get(
                "publishedUnitedStatesRows"
            ),
            sp500_symbols_fetched=values.get("sp500SymbolsFetched"),
            sp500_symbols_matched=values.get("sp500SymbolsMatched"),
            sp500_match_rate=values.get("sp500MatchRate"),
            provenance_status=values.get(
                "provenanceStatus", "generated-by-updater"
            ),
            provenance_note=values.get("provenanceNote"),
        )

    def with_generated_at(self):
        if self.generated_at is not None:
            return self
        return replace(self, generated_at=utc_now())


def _source_metadata(value, *, default_generated_at=False):
    metadata = (
        value if isinstance(value, SourceMetadata) else SourceMetadata.from_mapping(value)
    )
    return metadata.with_generated_at() if default_generated_at else metadata


def _safe_industry_name(industry):
    safe_name = re.sub(r"[^\w\s-]", "", industry.lower())
    return re.sub(r"\s+", "_", safe_name.strip())


def build_output_frames(tickers, sp500_tickers):
    """Build the legacy v1 CSV layout without writing it."""
    if not tickers:
        raise ValueError("No United States ticker rows are available to publish")
    if not sp500_tickers:
        raise ValueError("No matched S&P 500 ticker rows are available to publish")

    frame = pd.DataFrame(tickers, columns=LEGACY_COLUMNS)
    frame["industry"] = frame["industry"].fillna("").astype(str).str.strip()
    frame.loc[frame["industry"] == "", "industry"] = "Uncategorized"
    frame = frame.sort_values("marketCap", ascending=False).reset_index(drop=True)

    sp500_frame = pd.DataFrame(sp500_tickers, columns=LEGACY_COLUMNS)
    sp500_frame = sp500_frame.sort_values(
        "marketCap", ascending=False
    ).reset_index(drop=True)

    frames = {
        "tickers/all.csv": frame,
        "tickers/sp500.csv": sp500_frame,
    }
    for size in TOP_LIST_SIZES:
        frames[f"tickers/top_{size}.csv"] = frame.head(size)

    industry_paths = {}
    for industry in sorted(frame["industry"].unique()):
        safe_name = _safe_industry_name(industry)
        if not safe_name:
            raise ValueError(
                f"Industry name produces an empty filename: {industry!r}"
            )
        path = f"by_industry/{safe_name}.csv"
        previous = industry_paths.get(path)
        if previous is not None:
            raise ValueError(
                f"Industry filename collision: {previous!r} and {industry!r}"
            )
        industry_paths[path] = industry
        frames[path] = frame[frame["industry"] == industry]

    validate_output_frames(frames)
    return frames


def build_v2_frame(tickers, sp500_symbols):
    """Build the richer v2 dataset from the complete Nasdaq response."""
    if not tickers:
        raise ValueError("No Nasdaq rows are available for the v2 dataset")

    sp500_set = {normalize_symbol(symbol) for symbol in sp500_symbols}
    sp500_set.discard("")
    rows = []
    for ticker in tickers:
        row = dict(ticker)
        row["is_sp500"] = normalize_symbol(row["symbol"]) in sp500_set
        rows.append(row)

    frame = pd.DataFrame(rows, columns=V2_COLUMNS)
    frame = frame.sort_values("market_cap", ascending=False).reset_index(drop=True)
    validate_v2_frame(frame)
    return frame


def validate_v2_frame(frame):
    """Validate a v2 frame through the shared dataset contract."""
    if list(frame.columns) != list(V2_COLUMNS):
        raise ValueError("data/v2/tickers.csv does not match the v2 column contract")
    validate_v2_records(
        v2_record_from_mapping(row) for row in frame.to_dict(orient="records")
    )


def _load_v2_frame(path):
    """Read v2 without treating valid ticker symbols such as NA as null."""
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def validate_output_frames(frames):
    """Validate relationships among all generated legacy-v1 CSV frames."""
    required_paths = {
        "tickers/all.csv",
        "tickers/sp500.csv",
        *(f"tickers/top_{size}.csv" for size in TOP_LIST_SIZES),
    }
    missing_paths = required_paths.difference(frames)
    if missing_paths:
        raise ValueError(
            f"Snapshot is missing files: {', '.join(sorted(missing_paths))}"
        )

    for path, frame in frames.items():
        if list(frame.columns) != list(LEGACY_COLUMNS):
            raise ValueError(f"{path} does not match the legacy v1 column contract")

    all_frame = frames["tickers/all.csv"]
    normalized_symbols = all_frame["symbol"].fillna("").astype(str).map(
        normalize_symbol
    )
    if (normalized_symbols == "").any():
        raise ValueError("tickers/all.csv contains an empty symbol")
    duplicate_symbols = normalized_symbols[
        normalized_symbols.duplicated()
    ].tolist()
    if duplicate_symbols:
        sample = ", ".join(str(symbol) for symbol in duplicate_symbols[:10])
        raise ValueError(f"tickers/all.csv contains duplicate symbols: {sample}")
    market_caps = pd.to_numeric(all_frame["marketCap"], errors="coerce")
    validate_descending_market_caps(
        market_caps.tolist(), field_name="marketCap"
    )

    for size in TOP_LIST_SIZES:
        path = f"tickers/top_{size}.csv"
        try:
            pd.testing.assert_frame_equal(
                frames[path].reset_index(drop=True),
                all_frame.head(size).reset_index(drop=True),
                check_dtype=False,
            )
        except AssertionError as error:
            raise ValueError(f"{path} is not the expected all.csv prefix") from error

    industry_frames = {
        path: frame
        for path, frame in frames.items()
        if path.startswith("by_industry/")
    }
    if not industry_frames:
        raise ValueError("Snapshot does not contain any by_industry CSV files")

    grouped_symbols = []
    for frame in industry_frames.values():
        grouped_symbols.extend(frame["symbol"].tolist())
    if len(grouped_symbols) != len(set(grouped_symbols)):
        raise ValueError("by_industry CSV files contain duplicate symbols")
    if set(grouped_symbols) != set(all_frame["symbol"].tolist()):
        raise ValueError("by_industry CSV files do not partition tickers/all.csv")


def _quality_summary(frames, metadata):
    all_frame = frames["tickers/all.csv"]
    market_cap = pd.to_numeric(all_frame["marketCap"], errors="coerce")
    return {
        "missingMarketCapRows": int(market_cap.isna().sum()),
        "nonPositiveMarketCapRows": int((market_cap.fillna(1) <= 0).sum()),
        "uncategorizedRows": int(
            (all_frame["industry"] == "Uncategorized").sum()
        ),
        "sp500MatchRate": metadata.sp500_match_rate,
    }


def _provenance(metadata):
    return {
        "status": metadata.provenance_status,
        "note": metadata.provenance_note,
    }


def _sources(metadata, *, published_us_rows=False, matched_symbols=None):
    nasdaq = {
        "url": NASDAQ_URL,
        "fetchedAt": metadata.nasdaq_fetched_at,
        "rowsFetched": metadata.nasdaq_rows_fetched,
    }
    if published_us_rows:
        nasdaq["publishedUnitedStatesRows"] = (
            metadata.published_united_states_rows
        )
    return {
        "nasdaq": nasdaq,
        "wikipediaSp500": {
            "url": WIKIPEDIA_SP500_RAW_URL,
            "fetchedAt": metadata.wikipedia_fetched_at,
            "symbolsFetched": metadata.sp500_symbols_fetched,
            "symbolsMatched": (
                metadata.sp500_symbols_matched
                if matched_symbols is None
                else matched_symbols
            ),
        },
    }


def _build_manifest(frames, file_root, metadata):
    files = {}
    for relative_path, frame in sorted(frames.items()):
        files[relative_path] = {
            "rows": len(frame),
            "sha256": sha256_file(file_root / relative_path),
        }

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetContractVersion": DATASET_CONTRACT_VERSION,
        "generatedAt": metadata.generated_at,
        "provenance": _provenance(metadata),
        "sources": _sources(metadata, published_us_rows=True),
        "quality": _quality_summary(frames, metadata),
        "files": files,
    }


def _load_output_frames(root):
    paths = sorted((root / "tickers").glob("*.csv"))
    paths.extend(sorted((root / "by_industry").glob("*.csv")))
    return {
        path.relative_to(root).as_posix(): pd.read_csv(path) for path in paths
    }


def validate_published_snapshot(root):
    """Validate a written legacy snapshot and all manifest checksums."""
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("Published snapshot is missing manifest.json")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest.json has an unsupported schema version")
    if manifest.get("datasetContractVersion") != DATASET_CONTRACT_VERSION:
        raise ValueError("manifest.json has an unsupported dataset contract version")

    frames = _load_output_frames(root)
    validate_output_frames(frames)
    if set(frames) != set(manifest.get("files", {})):
        raise ValueError("manifest.json does not list the complete published CSV set")

    for relative_path, file_metadata in manifest["files"].items():
        path = root / relative_path
        if sha256_file(path) != file_metadata.get("sha256"):
            raise ValueError(f"Checksum mismatch for {relative_path}")
        if len(frames[relative_path]) != file_metadata.get("rows"):
            raise ValueError(f"Row-count mismatch for {relative_path}")
    return manifest


def _replace_managed_files(
    staging_root,
    output_root,
    relative_paths,
    *,
    existing_paths=None,
):
    backup_root = staging_root / ".backup"
    if existing_paths is None:
        existing_paths = set()
        for directory in ("tickers", "by_industry"):
            existing_paths.update(
                path.relative_to(output_root)
                for path in (output_root / directory).glob("*.csv")
            )
        if (output_root / "manifest.json").exists():
            existing_paths.add(Path("manifest.json"))

    backed_up = []
    published = []
    try:
        for relative_path in sorted(existing_paths):
            source = output_root / relative_path
            backup = backup_root / relative_path
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, backup)
            backed_up.append((source, backup))

        for relative_path in sorted(relative_paths):
            source = staging_root / relative_path
            destination = output_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            published.append(destination)
    except Exception:
        for path in reversed(published):
            if path.exists():
                path.unlink()
        for destination, backup in reversed(backed_up):
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
        raise


def _complete_v1_frames(frames, output_root):
    """Keep every established legacy v1 CSV path across later updates."""
    complete_frames = dict(frames)
    for directory in ("tickers", "by_industry"):
        for path in sorted((output_root / directory).glob("*.csv")):
            relative_path = path.relative_to(output_root).as_posix()
            if relative_path in complete_frames:
                continue
            if directory == "by_industry":
                complete_frames[relative_path] = pd.DataFrame(
                    columns=LEGACY_COLUMNS
                )
            else:
                complete_frames[relative_path] = pd.read_csv(path)
    validate_output_frames(complete_frames)
    return complete_frames


def _stage_v1_snapshot(frames, staging_root, metadata):
    for relative_path, frame in frames.items():
        path = staging_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)

    staged_frames = _load_output_frames(staging_root)
    validate_output_frames(staged_frames)
    manifest = _build_manifest(staged_frames, staging_root, metadata)
    (staging_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_published_snapshot(staging_root)
    return manifest


def _build_v2_manifest(frame, file_root, metadata):
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetContractVersion": V2_DATASET_CONTRACT_VERSION,
        "generatedAt": metadata.generated_at,
        "provenance": _provenance(metadata),
        "sources": _sources(
            metadata, matched_symbols=int(frame["is_sp500"].sum())
        ),
        "quality": {
            "rows": len(frame),
            "unitedStatesDomicileRows": int(frame["is_us_domiciled"].sum()),
            "sp500Rows": int(frame["is_sp500"].sum()),
            "missingMarketCapRows": int(frame["market_cap"].isna().sum()),
            "uncategorizedSectorRows": int(
                (frame["sector"] == "Uncategorized").sum()
            ),
            "uncategorizedIndustryRows": int(
                (frame["industry"] == "Uncategorized").sum()
            ),
        },
        "files": {
            V2_DATA_PATH: {
                "rows": len(frame),
                "sha256": sha256_file(file_root / V2_DATA_PATH),
            }
        },
    }


def validate_v2_published_snapshot(root):
    """Validate a written v2 snapshot through the shared contract module."""
    return load_v2_snapshot(Path(root)).manifest


def _stage_v2_snapshot(frame, staging_root, metadata):
    data_path = staging_root / V2_DATA_PATH
    data_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_path, index=False)

    staged_frame = _load_v2_frame(data_path)
    validate_v2_frame(staged_frame)
    manifest = _build_v2_manifest(staged_frame, staging_root, metadata)
    manifest_path = staging_root / V2_MANIFEST_PATH
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_v2_published_snapshot(staging_root)
    return manifest


def _publish_transaction(
    output_root,
    *,
    prefix,
    relative_paths,
    stage,
    existing_paths=None,
):
    with tempfile.TemporaryDirectory(
        prefix=prefix, dir=output_root
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        result = stage(staging_root)
        _replace_managed_files(
            staging_root,
            output_root,
            relative_paths,
            existing_paths=existing_paths,
        )
    return result


def publish_snapshot(frames, output_root=".", *, source_metadata=None):
    """Stage, validate, and publish a complete legacy v1 snapshot."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    frames = _complete_v1_frames(frames, output_root)
    metadata = _source_metadata(source_metadata, default_generated_at=True)
    relative_paths = {Path(path) for path in frames}
    relative_paths.add(Path("manifest.json"))
    return _publish_transaction(
        output_root,
        prefix=".ticker-update-",
        relative_paths=relative_paths,
        stage=lambda staging_root: _stage_v1_snapshot(
            frames, staging_root, metadata
        ),
    )


def publish_v2_snapshot(frame, output_root=".", *, source_metadata=None):
    """Stage, validate, and publish the v2 dataset."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    metadata = _source_metadata(source_metadata, default_generated_at=True)
    relative_paths = {Path(V2_DATA_PATH), Path(V2_MANIFEST_PATH)}
    existing_paths = {
        path for path in relative_paths if (output_root / path).exists()
    }
    return _publish_transaction(
        output_root,
        prefix=".ticker-v2-update-",
        relative_paths=relative_paths,
        existing_paths=existing_paths,
        stage=lambda staging_root: _stage_v2_snapshot(
            frame, staging_root, metadata
        ),
    )


def publish_release(frames, v2_frame, output_root=".", *, source_metadata=None):
    """Publish legacy v1 and v2 as one rollback-safe release."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    frames = _complete_v1_frames(frames, output_root)
    metadata = _source_metadata(source_metadata, default_generated_at=True)
    relative_paths = {Path(path) for path in frames}
    relative_paths.update(
        {Path("manifest.json"), Path(V2_DATA_PATH), Path(V2_MANIFEST_PATH)}
    )
    existing_paths = {
        path for path in relative_paths if (output_root / path).exists()
    }

    def stage(staging_root):
        return {
            "v1": _stage_v1_snapshot(frames, staging_root, metadata),
            "v2": _stage_v2_snapshot(v2_frame, staging_root, metadata),
        }

    return _publish_transaction(
        output_root,
        prefix=".ticker-release-",
        relative_paths=relative_paths,
        existing_paths=existing_paths,
        stage=stage,
    )


def write_manifest_for_existing_snapshot(output_root=".", *, source_metadata=None):
    """Create metadata for an existing snapshot without rewriting its CSV files."""
    output_root = Path(output_root)
    frames = _load_output_frames(output_root)
    validate_output_frames(frames)
    metadata = _source_metadata(source_metadata)
    manifest = _build_manifest(frames, output_root, metadata)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
