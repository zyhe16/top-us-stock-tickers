"""
US Stock Ticker Fetcher
=======================
Fetches securities from Nasdaq's stock screener, keeps the legacy United States
country filter, and publishes CSV files sorted by market capitalization.
"""

import random
import re
import time

import requests

from contracts import (
    LEGACY_COLUMNS,
    MANIFEST_SCHEMA_VERSION,
    MIN_ALL_TICKERS,
    MIN_SP500_TICKERS,
    MIN_US_TICKERS,
    V2_COLUMNS,
    V2_DATASET_CONTRACT_VERSION,
    normalize_symbol,
)
from snapshot_publication import (
    DATASET_CONTRACT_VERSION,
    NASDAQ_URL,
    TOP_LIST_SIZES,
    WIKIPEDIA_SP500_RAW_URL,
    build_output_frames,
    build_v2_frame,
    publish_release,
    publish_snapshot,
    publish_v2_snapshot,
    utc_now,
    validate_output_frames,
    validate_published_snapshot,
    validate_v2_frame,
    validate_v2_published_snapshot,
    write_manifest_for_existing_snapshot,
)

# Configuration
NASDAQ_BASE_URL = "https://www.nasdaq.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
SP500_SYMBOL_PATTERN = re.compile(
    r"^\|+\s*\{\{(?:NyseSymbol|NasdaqSymbol|BZX link)\|([^}|]+)",
    re.MULTILINE,
)
MIN_SP500_SYMBOLS = MIN_SP500_TICKERS
MIN_SP500_MATCH_RATE = 0.98


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
