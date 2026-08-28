# Dataset contract

The project publishes two contracts. Legacy v1 keeps old integrations working. V2 is the corrected, richer dataset used by the HTTP API.

## Legacy v1 contract

Legacy v1 covers:

- `tickers/all.csv`
- `tickers/sp500.csv`
- `tickers/top_50.csv`
- `tickers/top_100.csv`
- `tickers/top_200.csv`
- `by_industry/*.csv`

Every file keeps these columns in this order:

```text
symbol,name,price,marketCap,volume,industry
```

The `industry` column contains Nasdaq's broad `sector` value. The project will not fix that name in place. It will not rename, remove, reorder, or change the meaning of a legacy v1 column. It will not silently change the exact `United States` country filter or turn security rows into company rows.

`tickers/all.csv` contains source rows whose country is exactly `United States`. This is a domicile test. It is not a complete definition of securities listed on US exchanges.

The top lists are prefixes of `tickers/all.csv` after descending market-cap sorting. The `by_industry` directory retains its historical name and partitions `tickers/all.csv` by the legacy sector value. `tickers/sp500.csv` matches Wikipedia membership symbols against the complete Nasdaq response, so a row can appear there without appearing in `tickers/all.csv`.

## V2 contract

V2 lives at `data/v2/tickers.csv`. It contains one row for each unique, non-empty symbol returned by the Nasdaq screener. It does not apply the legacy v1 country filter.

The columns are:

```text
symbol,name,price,price_change,percent_change,market_cap,volume,country,sector,industry,ipo_year,nasdaq_url,is_us_domiciled,is_sp500
```

| Column | Meaning |
| --- | --- |
| `symbol` | Source security symbol. |
| `name` | Source security name with surrounding whitespace removed. |
| `price` | Parsed `lastsale`. |
| `price_change` | Parsed `netchange`. |
| `percent_change` | Parsed `pctchange` in percentage points. |
| `market_cap` | Parsed market capitalization in US dollars. |
| `volume` | Parsed volume. |
| `country` | Source country. An empty value stays empty. |
| `sector` | Source sector, or `Uncategorized` when empty. |
| `industry` | Detailed source industry, or `Uncategorized` when empty. |
| `ipo_year` | Parsed source IPO year. |
| `nasdaq_url` | Absolute Nasdaq security URL derived from the source path. |
| `is_us_domiciled` | `True` only when the source country is exactly `United States`. |
| `is_sp500` | `True` when the normalized symbol matches the fetched Wikipedia membership list. |

Failed numeric conversions produce empty CSV values. Rows sort by `market_cap` in descending order, with missing values last. Equal values have no guaranteed order.

V2 contains securities, not deduplicated companies. It can include multiple share classes, preferred or depositary shares, units, and other source rows.

## Snapshot metadata

The root `manifest.json` describes legacy v1 and uses `legacy-v1` as its contract ID. `data/v2/manifest.json` describes v2. Each manifest records:

- Contract and manifest schema versions.
- Source URLs and fetch timestamps.
- Source, publication, and quality counts.
- Published row counts and SHA-256 checksums.

The fetch timestamp records when the updater completed the source request. It is not a quote timestamp supplied by Nasdaq.

## Publication checks

The updater rejects a candidate when:

- Nasdaq or Wikipedia returns an implausibly small result.
- Normalized symbols collide.
- S&P 500 matching falls below 98 percent.
- A legacy v1 row loses a required field.
- A top list is not the expected prefix.
- The grouped legacy v1 files do not partition `tickers/all.csv` exactly once.
- The v2 schema, country count, or S&P flag count is invalid.
- A written row count or checksum differs from its manifest.

The updater stages files before replacement and restores files moved during a failed replacement. The GitHub workflow commits generated data only after the process exits successfully.
