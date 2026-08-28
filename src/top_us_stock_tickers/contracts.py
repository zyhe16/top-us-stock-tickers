"""Versioned dataset contracts shared by publishers and readers."""

import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from sys import intern
from typing import Iterable, Mapping


LEGACY_COLUMNS = (
    "symbol",
    "name",
    "price",
    "marketCap",
    "volume",
    "industry",
)

V2_COLUMNS = (
    "symbol",
    "name",
    "price",
    "price_change",
    "percent_change",
    "market_cap",
    "volume",
    "country",
    "sector",
    "industry",
    "ipo_year",
    "nasdaq_url",
    "is_us_domiciled",
    "is_sp500",
)

MANIFEST_SCHEMA_VERSION = 1
V2_DATASET_CONTRACT_VERSION = "v2"
MIN_ALL_TICKERS = 6_000
MIN_US_TICKERS = 4_000
MIN_SP500_TICKERS = 450


def normalize_symbol(symbol):
    """Normalize share-class separators across data sources and requests."""
    normalized = symbol.strip()
    if not normalized.isupper():
        normalized = normalized.upper()
    if "." in normalized:
        normalized = normalized.replace(".", "/")
    return normalized


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one published artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_descending_market_caps(
    values: Iterable[float | int | None], *, field_name: str
) -> None:
    """Require descending market caps with missing values at the end."""
    previous = None
    found_missing = False
    for value in values:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            found_missing = True
            continue
        numeric_value = float(value)
        if found_missing or (previous is not None and numeric_value > previous):
            raise ValueError(f"Rows are not in descending {field_name} order")
        previous = numeric_value


def _missing(value) -> bool:
    return value is None or value == "" or (
        isinstance(value, float) and math.isnan(value)
    )


def _optional_float(value) -> float | None:
    if _missing(value):
        return None
    return float(value)


def _optional_int(value) -> int | None:
    if _missing(value):
        return None
    return int(float(value))


def _boolean(value) -> bool:
    if value is True or value == "True":
        return True
    if value is False or value == "False":
        return False
    raise ValueError(f"Invalid boolean in v2 dataset: {value!r}")


def _text(value) -> str:
    return "" if _missing(value) else str(value)


@dataclass(frozen=True, slots=True)
class V2TickerRecord:
    """One typed row from the v2 dataset contract."""

    symbol: str
    name: str
    price: float | None
    price_change: float | None
    percent_change: float | None
    market_cap: float | None
    volume: int | None
    country: str
    sector: str
    industry: str
    ipo_year: int | None
    nasdaq_url: str | None
    is_us_domiciled: bool
    is_sp500: bool


@dataclass(frozen=True, slots=True)
class ValidatedV2Snapshot:
    """A v2 artifact after schema, content, and manifest validation."""

    rows: tuple[V2TickerRecord, ...]
    manifest: dict
    manifest_sha256: str


def v2_record_from_mapping(row: Mapping) -> V2TickerRecord:
    """Interpret one mapping according to the v2 row contract."""
    return V2TickerRecord(
        symbol=_text(row["symbol"]),
        name=_text(row["name"]),
        price=_optional_float(row["price"]),
        price_change=_optional_float(row["price_change"]),
        percent_change=_optional_float(row["percent_change"]),
        market_cap=_optional_float(row["market_cap"]),
        volume=_optional_int(row["volume"]),
        country=intern(_text(row["country"])),
        sector=intern(_text(row["sector"])),
        industry=intern(_text(row["industry"])),
        ipo_year=_optional_int(row["ipo_year"]),
        nasdaq_url=None if _missing(row["nasdaq_url"]) else str(row["nasdaq_url"]),
        is_us_domiciled=_boolean(row["is_us_domiciled"]),
        is_sp500=_boolean(row["is_sp500"]),
    )


def validate_v2_records(
    rows: Iterable[V2TickerRecord],
) -> tuple[V2TickerRecord, ...]:
    """Validate all cross-row invariants in the v2 contract."""
    records = tuple(rows)
    symbols = [normalize_symbol(row.symbol) for row in records]
    if any(not symbol for symbol in symbols):
        raise ValueError("data/v2/tickers.csv contains an empty symbol")
    if len(symbols) != len(set(symbols)):
        raise ValueError("data/v2/tickers.csv contains duplicate symbols")
    if any(not row.sector.strip() for row in records):
        raise ValueError("data/v2/tickers.csv contains an empty sector")
    if any(not row.industry.strip() for row in records):
        raise ValueError("data/v2/tickers.csv contains an empty industry")
    if len(records) < MIN_ALL_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few rows")
    if sum(row.is_us_domiciled for row in records) < MIN_US_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few US domicile rows")
    if sum(row.is_sp500 for row in records) < MIN_SP500_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few S&P 500 rows")
    validate_descending_market_caps(
        (row.market_cap for row in records), field_name="market_cap"
    )
    return records


def read_v2_csv(path: Path) -> tuple[V2TickerRecord, ...]:
    """Read and validate one v2 CSV artifact."""
    with path.open(newline="", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        if tuple(reader.fieldnames or ()) != V2_COLUMNS:
            raise ValueError("data/v2/tickers.csv has an unsupported schema")
        return validate_v2_records(v2_record_from_mapping(row) for row in reader)


def load_v2_snapshot(root: Path) -> ValidatedV2Snapshot:
    """Load one complete v2 artifact through the shared contract seam."""
    data_path = root / "data/v2/tickers.csv"
    manifest_path = root / "data/v2/manifest.json"
    if not data_path.exists() or not manifest_path.exists():
        raise ValueError("The v2 dataset or manifest is missing")

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("data/v2/manifest.json has an unsupported schema")
    if manifest.get("datasetContractVersion") != V2_DATASET_CONTRACT_VERSION:
        raise ValueError("data/v2/manifest.json has an unsupported contract")

    metadata = manifest.get("files", {}).get("data/v2/tickers.csv", {})
    if sha256_file(data_path) != metadata.get("sha256"):
        raise ValueError("Checksum mismatch for data/v2/tickers.csv")
    rows = read_v2_csv(data_path)
    if len(rows) != metadata.get("rows"):
        raise ValueError("Row-count mismatch for data/v2/tickers.csv")
    return ValidatedV2Snapshot(
        rows=rows,
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
