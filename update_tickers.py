"""
US Stock Ticker Fetcher
=======================
Fetches securities from Nasdaq's stock screener, keeps the legacy United States
country filter, and publishes CSV files sorted by market capitalization.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import tempfile
import time

import requests
import pandas as pd

from contracts import LEGACY_COLUMNS, V2_COLUMNS, normalize_symbol

# Configuration
NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_BASE_URL = "https://www.nasdaq.com"
WIKIPEDIA_SP500_RAW_URL = "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
SP500_SYMBOL_PATTERN = re.compile(
    r"^\|+\s*\{\{(?:NyseSymbol|NasdaqSymbol|BZX link)\|([^}|]+)",
    re.MULTILINE,
)
TOP_LIST_SIZES = (50, 100, 200)
MIN_US_TICKERS = 4_000
MIN_ALL_TICKERS = 6_000
MIN_SP500_SYMBOLS = 450
MIN_SP500_MATCH_RATE = 0.98
MANIFEST_SCHEMA_VERSION = 1
DATASET_CONTRACT_VERSION = "legacy-v1"
V2_DATASET_CONTRACT_VERSION = "v2"


def utc_now():
    """Return a stable UTC timestamp for published metadata."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_sp500_symbols(content):
    """Parse S&P 500 constituent tickers from Wikipedia wikitext."""
    start_marker = "== S&P 500 component stocks =="
    end_marker = "== Selected changes to the list of S&P 500 components =="

    if start_marker not in content:
        raise ValueError("Wikipedia page format changed: missing constituents section")

    section = content.split(start_marker, 1)[1]
    if end_marker in section:
        section = section.split(end_marker, 1)[0]

    symbols = []
    seen = set()
    for symbol in SP500_SYMBOL_PATTERN.findall(section):
        normalized = normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        symbols.append(symbol.strip().upper())

    return symbols


def fetch_sp500_symbols():
    """Fetch current S&P 500 constituent tickers from Wikipedia."""
    print("Fetching S&P 500 constituents from Wikipedia...")

    time.sleep(random.uniform(0.5, 1.5))

    try:
        response = requests.get(WIKIPEDIA_SP500_RAW_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()

        symbols = parse_sp500_symbols(response.text)

        if len(symbols) < MIN_SP500_SYMBOLS:
            raise ValueError(f"Wikipedia parse returned too few symbols: {len(symbols)}")

        print(f"  Found {len(symbols)} S&P 500 constituent tickers")
        return symbols

    except Exception as e:
        print(f"  Error: {e}")
        return []


def fetch_tickers():
    """
    Fetch Nasdaq rows and build the legacy v1 and v2 source representations.
    """
    print("Fetching tickers from NASDAQ...")
    
    # Random initial delay (0.5-1.5s) to avoid predictable patterns
    time.sleep(random.uniform(0.5, 1.5))
    
    try:
        params = {
            "tableonly": "true",
            "download": "true"
        }
        
        response = requests.get(NASDAQ_URL, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        rows = response.json().get('data', {}).get('rows', [])
        
        if not rows:
            print("  No data returned from API")
            return [], [], []
        
        print(f"  Fetched {len(rows)} tickers from API")
        
        # Parse tickers and fail closed if the source repeats a symbol.
        all_tickers = []
        us_tickers = []
        v2_tickers = []
        seen_symbols = set()
        non_us_count = 0
        
        for row in rows:
            symbol = row.get('symbol', '').strip()
            
            if not symbol:
                continue
            if symbol in seen_symbols:
                raise ValueError(f"NASDAQ returned duplicate symbol: {symbol}")
            seen_symbols.add(symbol)

            ticker = {
                'symbol': symbol,
                'name': row.get('name', ''),
                'price': parse_number(row.get('lastsale', '')),
                'marketCap': parse_market_cap(row.get('marketCap', '')),
                'volume': parse_int(row.get('volume', '')),
                'industry': row.get('sector', ''),
            }
            all_tickers.append(ticker)

            source_path = row.get("url", "").strip()
            v2_tickers.append(
                {
                    "symbol": symbol,
                    "name": row.get("name", "").strip(),
                    "price": parse_number(row.get("lastsale", "")),
                    "price_change": parse_number(row.get("netchange", "")),
                    "percent_change": parse_percent(row.get("pctchange", "")),
                    "market_cap": parse_market_cap(row.get("marketCap", "")),
                    "volume": parse_int(row.get("volume", "")),
                    "country": row.get("country", "").strip(),
                    "sector": row.get("sector", "").strip() or "Uncategorized",
                    "industry": row.get("industry", "").strip() or "Uncategorized",
                    "ipo_year": parse_int(row.get("ipoyear", "")),
                    "nasdaq_url": (
                        f"{NASDAQ_BASE_URL}{source_path}" if source_path else None
                    ),
                    "is_us_domiciled": row.get("country") == "United States",
                    "is_sp500": False,
                }
            )

            if row.get('country') == 'United States':
                us_tickers.append(ticker)
            else:
                non_us_count += 1
        
        print(f"  Excluded: {non_us_count} non-US")
        print(f"  Found {len(us_tickers)} unique US tickers")
        print(f"  Found {len(all_tickers)} total unique listed tickers")
        return us_tickers, all_tickers, v2_tickers
        
    except Exception as e:
        print(f"  Error: {e}")
        return [], [], []


def parse_market_cap(s):
    """Parse market cap string like '$1.2T' or '$500M' to numeric."""
    if not s or s == 'N/A':
        return None
    try:
        s = s.replace('$', '').replace(',', '').strip()
        multipliers = {'T': 1e12, 'B': 1e9, 'M': 1e6, 'K': 1e3}
        for suffix, mult in multipliers.items():
            if s.endswith(suffix):
                return float(s[:-1]) * mult
        return float(s)
    except (AttributeError, TypeError, ValueError):
        return None


def parse_number(s):
    """Parse price/number string to float."""
    if not s or s == 'N/A':
        return None
    try:
        return float(s.replace('$', '').replace(',', '').strip())
    except (AttributeError, TypeError, ValueError):
        return None


def parse_int(s):
    """Parse volume string to integer."""
    if not s or s == 'N/A':
        return None
    try:
        return int(str(s).replace(',', '').strip())
    except (AttributeError, TypeError, ValueError):
        return None


def parse_percent(s):
    """Parse a percentage string to percentage points."""
    if not s or s == "N/A":
        return None
    try:
        return float(str(s).replace("%", "").replace(",", "").strip())
    except (AttributeError, TypeError, ValueError):
        return None


def filter_sp500_tickers(tickers, sp500_symbols):
    """Filter ticker rows down to S&P 500 constituents."""
    if not tickers or not sp500_symbols:
        return []

    sp500_map = {normalize_symbol(symbol): symbol for symbol in sp500_symbols}
    matched_tickers = []
    unmatched_symbols = set(sp500_map)

    for ticker in tickers:
        normalized = normalize_symbol(ticker.get('symbol', ''))
        if normalized in sp500_map:
            matched_tickers.append(ticker)
            unmatched_symbols.discard(normalized)

    print(f"  Matched {len(matched_tickers)} NASDAQ rows to S&P 500 constituents")
    if unmatched_symbols:
        sample = ", ".join(sorted(unmatched_symbols)[:10])
        suffix = "..." if len(unmatched_symbols) > 10 else ""
        print(f"  Warning: {len(unmatched_symbols)} S&P symbols not found in NASDAQ data: {sample}{suffix}")

    return matched_tickers


def _duplicate_symbols(tickers):
    seen = set()
    duplicates = set()
    for ticker in tickers:
        symbol = normalize_symbol(ticker.get("symbol", ""))
        if not symbol:
            continue
        if symbol in seen:
            duplicates.add(symbol)
        seen.add(symbol)
    return sorted(duplicates)


def validate_source_snapshot(
    tickers,
    all_tickers,
    sp500_symbols,
    sp500_tickers,
    *,
    min_us_tickers=MIN_US_TICKERS,
    min_all_tickers=MIN_ALL_TICKERS,
    min_sp500_symbols=MIN_SP500_SYMBOLS,
    min_sp500_match_rate=MIN_SP500_MATCH_RATE,
):
    """Reject incomplete or inconsistent source data before writing any files."""
    if len(all_tickers) < min_all_tickers:
        raise ValueError(
            "NASDAQ returned too few total ticker rows: "
            f"{len(all_tickers)} < {min_all_tickers}"
        )
    if len(tickers) < min_us_tickers:
        raise ValueError(
            "NASDAQ returned too few United States ticker rows: "
            f"{len(tickers)} < {min_us_tickers}"
        )

    duplicate_all = _duplicate_symbols(all_tickers)
    if duplicate_all:
        sample = ", ".join(duplicate_all[:10])
        raise ValueError(f"NASDAQ returned duplicate symbols: {sample}")

    required_columns = set(LEGACY_COLUMNS)
    for position, ticker in enumerate(all_tickers):
        missing = required_columns.difference(ticker)
        if missing:
            raise ValueError(
                f"NASDAQ row {position} is missing fields: {', '.join(sorted(missing))}"
            )

    normalized_sp500 = {normalize_symbol(symbol) for symbol in sp500_symbols}
    normalized_sp500.discard("")
    if len(normalized_sp500) < min_sp500_symbols:
        raise ValueError(
            "Wikipedia returned too few S&P 500 symbols: "
            f"{len(normalized_sp500)} < {min_sp500_symbols}"
        )

    matched_symbols = {
        normalize_symbol(ticker.get("symbol", "")) for ticker in sp500_tickers
    }
    matched_symbols.discard("")
    unexpected_matches = matched_symbols.difference(normalized_sp500)
    if unexpected_matches:
        sample = ", ".join(sorted(unexpected_matches)[:10])
        raise ValueError(f"S&P 500 output contains unexpected symbols: {sample}")

    match_rate = len(matched_symbols) / len(normalized_sp500)
    if match_rate < min_sp500_match_rate:
        raise ValueError(
            "S&P 500 match rate is too low: "
            f"{match_rate:.1%} < {min_sp500_match_rate:.1%}"
        )

    all_symbols = {
        normalize_symbol(ticker.get("symbol", "")) for ticker in all_tickers
    }
    missing_us_symbols = {
        normalize_symbol(ticker.get("symbol", "")) for ticker in tickers
    }.difference(all_symbols)
    if missing_us_symbols:
        sample = ", ".join(sorted(missing_us_symbols)[:10])
        raise ValueError(f"Published ticker rows are missing from NASDAQ data: {sample}")

    return {
        "nasdaqRowsFetched": len(all_tickers),
        "publishedUnitedStatesRows": len(tickers),
        "sp500SymbolsFetched": len(normalized_sp500),
        "sp500SymbolsMatched": len(matched_symbols),
        "sp500MatchRate": match_rate,
    }


def _safe_industry_name(industry):
    safe_name = re.sub(r"[^\w\s-]", "", industry.lower())
    return re.sub(r"\s+", "_", safe_name.strip())


def build_output_frames(tickers, sp500_tickers):
    """Build the legacy v1 CSV layout without writing it."""
    if not tickers:
        raise ValueError("No United States ticker rows are available to publish")
    if not sp500_tickers:
        raise ValueError("No matched S&P 500 ticker rows are available to publish")

    df = pd.DataFrame(tickers, columns=LEGACY_COLUMNS)
    df["industry"] = df["industry"].fillna("").astype(str).str.strip()
    df.loc[df["industry"] == "", "industry"] = "Uncategorized"
    df = df.sort_values("marketCap", ascending=False).reset_index(drop=True)

    sp500_df = pd.DataFrame(sp500_tickers, columns=LEGACY_COLUMNS)
    sp500_df = sp500_df.sort_values("marketCap", ascending=False).reset_index(
        drop=True
    )

    frames = {
        "tickers/all.csv": df,
        "tickers/sp500.csv": sp500_df,
    }
    for size in TOP_LIST_SIZES:
        frames[f"tickers/top_{size}.csv"] = df.head(size)

    industry_paths = {}
    for industry in sorted(df["industry"].unique()):
        safe_name = _safe_industry_name(industry)
        if not safe_name:
            raise ValueError(f"Industry name produces an empty filename: {industry!r}")
        path = f"by_industry/{safe_name}.csv"
        previous = industry_paths.get(path)
        if previous is not None:
            raise ValueError(
                f"Industry filename collision: {previous!r} and {industry!r}"
            )
        industry_paths[path] = industry
        frames[path] = df[df["industry"] == industry]

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
    """Validate the corrected schema and its source-derived flags."""
    if list(frame.columns) != list(V2_COLUMNS):
        raise ValueError("data/v2/tickers.csv does not match the v2 column contract")
    if len(frame) < MIN_ALL_TICKERS:
        raise ValueError(
            f"v2 contains too few Nasdaq rows: {len(frame)} < {MIN_ALL_TICKERS}"
        )

    symbols = frame["symbol"].map(normalize_symbol)
    duplicates = symbols[symbols.duplicated()].tolist()
    if duplicates:
        raise ValueError(f"v2 contains duplicate symbols: {', '.join(duplicates[:10])}")
    if int(frame["is_us_domiciled"].sum()) < MIN_US_TICKERS:
        raise ValueError("v2 contains too few United States domicile rows")
    if int(frame["is_sp500"].sum()) < MIN_SP500_SYMBOLS:
        raise ValueError("v2 contains too few matched S&P 500 rows")
    if frame["sector"].isna().any() or (frame["sector"] == "").any():
        raise ValueError("v2 contains an empty sector")
    if frame["industry"].isna().any() or (frame["industry"] == "").any():
        raise ValueError("v2 contains an empty industry")


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

    all_df = frames["tickers/all.csv"]
    duplicate_symbols = all_df["symbol"][all_df["symbol"].duplicated()].tolist()
    if duplicate_symbols:
        sample = ", ".join(str(symbol) for symbol in duplicate_symbols[:10])
        raise ValueError(f"tickers/all.csv contains duplicate symbols: {sample}")

    for size in TOP_LIST_SIZES:
        path = f"tickers/top_{size}.csv"
        try:
            pd.testing.assert_frame_equal(
                frames[path].reset_index(drop=True),
                all_df.head(size).reset_index(drop=True),
                check_dtype=False,
            )
        except AssertionError as error:
            raise ValueError(f"{path} is not the expected all.csv prefix") from error

    industry_frames = {
        path: frame for path, frame in frames.items() if path.startswith("by_industry/")
    }
    if not industry_frames:
        raise ValueError("Snapshot does not contain any by_industry CSV files")

    grouped_symbols = []
    for frame in industry_frames.values():
        grouped_symbols.extend(frame["symbol"].tolist())
    if len(grouped_symbols) != len(set(grouped_symbols)):
        raise ValueError("by_industry CSV files contain duplicate symbols")
    if set(grouped_symbols) != set(all_df["symbol"].tolist()):
        raise ValueError("by_industry CSV files do not partition tickers/all.csv")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quality_summary(frames, source_metadata):
    all_df = frames["tickers/all.csv"]
    market_cap = pd.to_numeric(all_df["marketCap"], errors="coerce")
    return {
        "missingMarketCapRows": int(market_cap.isna().sum()),
        "nonPositiveMarketCapRows": int((market_cap.fillna(1) <= 0).sum()),
        "uncategorizedRows": int((all_df["industry"] == "Uncategorized").sum()),
        "sp500MatchRate": source_metadata.get("sp500MatchRate"),
    }


def _build_manifest(frames, file_root, source_metadata):
    files = {}
    for relative_path, frame in sorted(frames.items()):
        files[relative_path] = {
            "rows": len(frame),
            "sha256": _sha256(file_root / relative_path),
        }

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetContractVersion": DATASET_CONTRACT_VERSION,
        "generatedAt": source_metadata.get("generatedAt"),
        "provenance": {
            "status": source_metadata.get("provenanceStatus", "generated-by-updater"),
            "note": source_metadata.get("provenanceNote"),
        },
        "sources": {
            "nasdaq": {
                "url": NASDAQ_URL,
                "fetchedAt": source_metadata.get("nasdaqFetchedAt"),
                "rowsFetched": source_metadata.get("nasdaqRowsFetched"),
                "publishedUnitedStatesRows": source_metadata.get(
                    "publishedUnitedStatesRows"
                ),
            },
            "wikipediaSp500": {
                "url": WIKIPEDIA_SP500_RAW_URL,
                "fetchedAt": source_metadata.get("wikipediaFetchedAt"),
                "symbolsFetched": source_metadata.get("sp500SymbolsFetched"),
                "symbolsMatched": source_metadata.get("sp500SymbolsMatched"),
            },
        },
        "quality": _quality_summary(frames, source_metadata),
        "files": files,
    }


def _load_output_frames(root):
    paths = sorted((root / "tickers").glob("*.csv"))
    paths.extend(sorted((root / "by_industry").glob("*.csv")))
    return {
        path.relative_to(root).as_posix(): pd.read_csv(path)
        for path in paths
    }


def validate_published_snapshot(root):
    """Validate a written snapshot and every checksum in its manifest."""
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
        if _sha256(path) != file_metadata.get("sha256"):
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
                complete_frames[relative_path] = pd.DataFrame(columns=LEGACY_COLUMNS)
            else:
                complete_frames[relative_path] = pd.read_csv(path)
    validate_output_frames(complete_frames)
    return complete_frames


def _stage_v1_snapshot(frames, staging_root, source_metadata):
    for relative_path, frame in frames.items():
        path = staging_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)

    staged_frames = _load_output_frames(staging_root)
    validate_output_frames(staged_frames)
    manifest = _build_manifest(staged_frames, staging_root, source_metadata)
    (staging_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_published_snapshot(staging_root)
    return manifest


def publish_snapshot(frames, output_root=".", *, source_metadata=None):
    """Stage, validate, and publish a complete legacy v1 snapshot."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    frames = _complete_v1_frames(frames, output_root)
    source_metadata = dict(source_metadata or {})
    source_metadata.setdefault("generatedAt", utc_now())

    with tempfile.TemporaryDirectory(
        prefix=".ticker-update-", dir=output_root
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        manifest = _stage_v1_snapshot(frames, staging_root, source_metadata)

        relative_paths = {Path(path) for path in frames}
        relative_paths.add(Path("manifest.json"))
        _replace_managed_files(staging_root, output_root, relative_paths)

    return manifest


def _build_v2_manifest(frame, file_root, source_metadata):
    relative_path = "data/v2/tickers.csv"
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetContractVersion": V2_DATASET_CONTRACT_VERSION,
        "generatedAt": source_metadata.get("generatedAt"),
        "provenance": {
            "status": source_metadata.get("provenanceStatus", "generated-by-updater"),
            "note": source_metadata.get("provenanceNote"),
        },
        "sources": {
            "nasdaq": {
                "url": NASDAQ_URL,
                "fetchedAt": source_metadata.get("nasdaqFetchedAt"),
                "rowsFetched": source_metadata.get("nasdaqRowsFetched"),
            },
            "wikipediaSp500": {
                "url": WIKIPEDIA_SP500_RAW_URL,
                "fetchedAt": source_metadata.get("wikipediaFetchedAt"),
                "symbolsFetched": source_metadata.get("sp500SymbolsFetched"),
                "symbolsMatched": int(frame["is_sp500"].sum()),
            },
        },
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
            relative_path: {
                "rows": len(frame),
                "sha256": _sha256(file_root / relative_path),
            }
        },
    }


def validate_v2_published_snapshot(root):
    """Validate the checked-in v2 file and its manifest."""
    root = Path(root)
    manifest_path = root / "data/v2/manifest.json"
    data_path = root / "data/v2/tickers.csv"
    if not manifest_path.exists() or not data_path.exists():
        raise ValueError("Published snapshot is missing the v2 dataset or manifest")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("data/v2/manifest.json has an unsupported schema version")
    if manifest.get("datasetContractVersion") != V2_DATASET_CONTRACT_VERSION:
        raise ValueError("data/v2/manifest.json has an unsupported contract version")

    frame = _load_v2_frame(data_path)
    validate_v2_frame(frame)
    metadata = manifest.get("files", {}).get("data/v2/tickers.csv", {})
    if _sha256(data_path) != metadata.get("sha256"):
        raise ValueError("Checksum mismatch for data/v2/tickers.csv")
    if len(frame) != metadata.get("rows"):
        raise ValueError("Row-count mismatch for data/v2/tickers.csv")
    return manifest


def _stage_v2_snapshot(frame, staging_root, source_metadata):
    data_path = staging_root / "data/v2/tickers.csv"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_path, index=False)

    staged_frame = _load_v2_frame(data_path)
    validate_v2_frame(staged_frame)
    manifest = _build_v2_manifest(staged_frame, staging_root, source_metadata)
    manifest_path = staging_root / "data/v2/manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_v2_published_snapshot(staging_root)
    return manifest


def publish_v2_snapshot(frame, output_root=".", *, source_metadata=None):
    """Stage, validate, and publish the new v2 dataset."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    source_metadata = dict(source_metadata or {})
    source_metadata.setdefault("generatedAt", utc_now())

    with tempfile.TemporaryDirectory(
        prefix=".ticker-v2-update-", dir=output_root
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        manifest = _stage_v2_snapshot(frame, staging_root, source_metadata)

        relative_paths = {
            Path("data/v2/tickers.csv"),
            Path("data/v2/manifest.json"),
        }
        existing_paths = {
            path
            for path in relative_paths
            if (output_root / path).exists()
        }
        _replace_managed_files(
            staging_root,
            output_root,
            relative_paths,
            existing_paths=existing_paths,
        )

    return manifest


def publish_release(frames, v2_frame, output_root=".", *, source_metadata=None):
    """Publish legacy v1 and v2 as one rollback-safe release."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    frames = _complete_v1_frames(frames, output_root)
    source_metadata = dict(source_metadata or {})
    source_metadata.setdefault("generatedAt", utc_now())

    with tempfile.TemporaryDirectory(
        prefix=".ticker-release-", dir=output_root
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        v1_manifest = _stage_v1_snapshot(frames, staging_root, source_metadata)
        v2_manifest = _stage_v2_snapshot(v2_frame, staging_root, source_metadata)

        relative_paths = {Path(path) for path in frames}
        relative_paths.update(
            {
                Path("manifest.json"),
                Path("data/v2/tickers.csv"),
                Path("data/v2/manifest.json"),
            }
        )
        existing_paths = {
            path for path in relative_paths if (output_root / path).exists()
        }
        _replace_managed_files(
            staging_root,
            output_root,
            relative_paths,
            existing_paths=existing_paths,
        )

    return {"v1": v1_manifest, "v2": v2_manifest}


def write_manifest_for_existing_snapshot(output_root=".", *, source_metadata=None):
    """Create metadata for an existing snapshot without rewriting its CSV files."""
    output_root = Path(output_root)
    frames = _load_output_frames(output_root)
    validate_output_frames(frames)
    source_metadata = dict(source_metadata or {})
    manifest = _build_manifest(frames, output_root, source_metadata)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def save_files(
    tickers,
    sp500_tickers,
    v2_tickers,
    sp500_symbols,
    *,
    output_root=".",
    source_metadata=None,
):
    """Publish the compatible legacy v1 files and richer v2 dataset."""
    frames = build_output_frames(tickers, sp500_tickers)
    v2_frame = build_v2_frame(v2_tickers, sp500_symbols)

    print("\nSaving ticker lists...")
    for relative_path in (
        "tickers/all.csv",
        "tickers/sp500.csv",
        *(f"tickers/top_{size}.csv" for size in TOP_LIST_SIZES),
    ):
        print(f"  - {relative_path} ({len(frames[relative_path])} rows)")

    print("\nSaving by industry...")
    for relative_path, frame in sorted(frames.items()):
        if relative_path.startswith("by_industry/"):
            print(f"  - {relative_path} ({len(frame)} tickers)")

    publish_release(
        frames,
        v2_frame,
        output_root,
        source_metadata=source_metadata,
    )
    print("  - manifest.json")

    print("\nSaving v2 dataset...")
    print(f"  - data/v2/tickers.csv ({len(v2_frame)} rows)")
    print("  - data/v2/manifest.json")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("US Stock Ticker Update")
    print("=" * 50)

    tickers, all_tickers, v2_tickers = fetch_tickers()
    nasdaq_fetched_at = utc_now()
    sp500_symbols = fetch_sp500_symbols()
    wikipedia_fetched_at = utc_now()

    if not tickers:
        print("\nNo data found!")
        exit(1)

    if not sp500_symbols:
        print("\nNo S&P 500 data found!")
        exit(1)

    sp500_tickers = filter_sp500_tickers(all_tickers, sp500_symbols)
    source_metadata = validate_source_snapshot(
        tickers,
        all_tickers,
        sp500_symbols,
        sp500_tickers,
    )
    source_metadata.update(
        {
            "generatedAt": utc_now(),
            "nasdaqFetchedAt": nasdaq_fetched_at,
            "wikipediaFetchedAt": wikipedia_fetched_at,
        }
    )

    if save_files(
        tickers,
        sp500_tickers,
        v2_tickers,
        sp500_symbols,
        source_metadata=source_metadata,
    ):
        print("\n" + "=" * 50)
        print("Update completed!")
        print("=" * 50)
    else:
        print("\nFailed to save!")
        exit(1)
