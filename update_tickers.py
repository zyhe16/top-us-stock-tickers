"""
US Stock Ticker Fetcher
=======================
Fetches US stock tickers from NASDAQ's stock screener API (covers NYSE, NASDAQ, AMEX),
then saves to CSV files sorted by market capitalization.
"""

import os
import re
import random
import time
import requests
import pandas as pd

# Configuration
NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
WIKIPEDIA_SP500_RAW_URL = "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
SP500_SYMBOL_PATTERN = re.compile(
    r"^\|+\s*\{\{(?:NyseSymbol|NasdaqSymbol|BZX link)\|([^}|]+)",
    re.MULTILINE,
)


def normalize_symbol(symbol):
    """Normalize symbol formatting across data sources."""
    return symbol.strip().upper().replace(".", "/")


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

        if len(symbols) < 450:
            raise ValueError(f"Wikipedia parse returned too few symbols: {len(symbols)}")

        print(f"  Found {len(symbols)} S&P 500 constituent tickers")
        return symbols

    except Exception as e:
        print(f"  Error: {e}")
        return []


def fetch_tickers():
    """
    Fetches NASDAQ screener rows and splits them into all listings and US listings.
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
            return []
        
        print(f"  Fetched {len(rows)} tickers from API")
        
        # Parse tickers with deduplication
        all_tickers = []
        us_tickers = []
        seen_symbols = set()
        non_us_count = 0
        duplicate_count = 0
        
        for row in rows:
            symbol = row.get('symbol', '').strip()
            
            # Skip duplicates
            if not symbol or symbol in seen_symbols:
                duplicate_count += 1
                continue
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

            if row.get('country') == 'United States':
                us_tickers.append(ticker)
            else:
                non_us_count += 1
        
        print(f"  Excluded: {non_us_count} non-US, {duplicate_count} duplicates")
        print(f"  Found {len(us_tickers)} unique US tickers")
        print(f"  Found {len(all_tickers)} total unique listed tickers")
        return us_tickers, all_tickers
        
    except Exception as e:
        print(f"  Error: {e}")
        return [], []


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
    except:
        return None


def parse_number(s):
    """Parse price/number string to float."""
    if not s or s == 'N/A':
        return None
    try:
        return float(s.replace('$', '').replace(',', '').strip())
    except:
        return None


def parse_int(s):
    """Parse volume string to integer."""
    if not s or s == 'N/A':
        return None
    try:
        return int(str(s).replace(',', '').strip())
    except:
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


def save_files(tickers, sp500_tickers=None):
    """Save tickers to organized folder structure."""
    if not tickers:
        print("No tickers to save!")
        return False
    
    # Sort by market cap (descending)
    df = pd.DataFrame(tickers)
    df['industry'] = df['industry'].fillna('').astype(str).str.strip()
    df.loc[df['industry'] == '', 'industry'] = 'Uncategorized'
    df = df.sort_values('marketCap', ascending=False).reset_index(drop=True)
    
    # Create output directories
    os.makedirs("tickers", exist_ok=True)
    os.makedirs("by_industry", exist_ok=True)
    
    # === GENERAL TICKER LISTS ===
    print("\nSaving ticker lists...")
    
    df.to_csv("tickers/all.csv", index=False)
    print(f"  - tickers/all.csv ({len(df)} rows)")

    if sp500_tickers:
        sp500_df = pd.DataFrame(sp500_tickers)
        sp500_df = sp500_df.sort_values('marketCap', ascending=False).reset_index(drop=True)
        sp500_df.to_csv("tickers/sp500.csv", index=False)
        print(f"  - tickers/sp500.csv ({len(sp500_df)} rows)")
    
    for n in [50, 100, 200]:
        df.head(n).to_csv(f"tickers/top_{n}.csv", index=False)
        print(f"  - tickers/top_{n}.csv")
    
    # === BY INDUSTRY ===
    print("\nSaving by industry...")
    
    industries = df['industry'].unique()
    
    for industry in sorted(industries):
        industry_df = df[df['industry'] == industry]
        if len(industry_df) == 0:
            continue
        
        # Safe filename
        safe_name = re.sub(r'[^\w\s-]', '', industry.lower())
        safe_name = re.sub(r'\s+', '_', safe_name.strip())
        
        industry_df.to_csv(f"by_industry/{safe_name}.csv", index=False)
        print(f"  - by_industry/{safe_name}.csv ({len(industry_df)} tickers)")
    
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("US Stock Ticker Update")
    print("=" * 50)
    
    tickers, all_tickers = fetch_tickers()
    sp500_symbols = fetch_sp500_symbols()
    
    if not tickers:
        print("\nNo data found!")
        exit(1)

    if not sp500_symbols:
        print("\nNo S&P 500 data found!")
        exit(1)

    sp500_tickers = filter_sp500_tickers(all_tickers, sp500_symbols)
    
    if save_files(tickers, sp500_tickers):
        print("\n" + "=" * 50)
        print("Update completed!")
        print("=" * 50)
    else:
        print("\nFailed to save!")
        exit(1)
