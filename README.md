# Top US Stock Tickers

[![CI](https://github.com/zyhe16/top-us-stock-tickers/actions/workflows/ci.yml/badge.svg)](https://github.com/zyhe16/top-us-stock-tickers/actions/workflows/ci.yml)

Daily ticker data from the Nasdaq stock screener, with S&P 500 membership matched from Wikipedia.

Version 2 adds a richer dataset and a query API.

**Already using v1? Don't worry.** The old CSV paths and columns are still here. We now call that contract legacy v1. Existing scripts do not need to migrate until you want the new fields or the API.

## Project layout

```text
.
|-- api.py                    FastAPI application
|-- contracts.py              Shared columns and symbol normalization
|-- update_tickers.py         Source fetch, validation, and publication
|-- tickers/                  Legacy v1 ticker lists
|-- by_industry/              Legacy v1 grouped lists
|-- manifest.json             Legacy v1 snapshot metadata
|-- data/v2/
|   |-- tickers.csv           V2 dataset
|   `-- manifest.json         V2 snapshot metadata
|-- tests/                    Updater, publication, and HTTP tests
|-- .github/workflows/        CI and weekday data updates
|-- Dockerfile                Railway API image
|-- requirements-api.txt      Production API dependencies
|-- requirements.txt          Updater and test dependencies
|-- API.md                    API guide
|-- DATA_CONTRACT.md          Dataset rules
|-- DATA_LICENSE.md           Source and reuse notes
|-- RAILWAY.md                Deployment guide
`-- CHANGELOG.md              Release history
```

## What is in v2

[`data/v2/tickers.csv`](data/v2/tickers.csv) contains every unique symbol returned by the Nasdaq screener. It adds fields that legacy v1 discarded:

- Country, sector, and detailed industry.
- IPO year.
- Price change and percentage change.
- A link to the security's Nasdaq page.
- `is_us_domiciled` and `is_sp500` flags.

The checked-in v2 snapshot contains 7,103 rows. Of those, 5,354 have `United States` in the source country field and 503 match the current S&P 500 membership list. See [`data/v2/manifest.json`](data/v2/manifest.json) for timestamps, counts, and the file checksum.

The values describe one source snapshot. Nasdaq does not supply a reliable quote timestamp through this endpoint, so do not treat `price`, `price_change`, or `percent_change` as real-time prices.

## API

The read-only API supports symbol lookup, text search, filters, sorting, and pagination:

```text
GET /api/v2/tickers/AAPL
GET /api/v2/tickers?collection=us&sector=Technology&limit=25
GET /api/v2/tickers?collection=sp500&sort=market_cap&order=desc
GET /api/v2/sectors
GET /api/v2/countries
GET /api/v2/industries
GET /api/v2/meta
```

FastAPI generates the API reference from the routes, query parameters, and response models in `api.py`. Swagger UI is available at `/docs`, ReDoc at `/redoc`, and the raw OpenAPI document at `/openapi.json`. These pages update when the API code changes. Nothing needs to generate or commit a separate documentation bundle.

`API.md` is different. It is the hand-written guide for people reading the repository, so it explains behavior and examples without requiring a running server.

Successful API responses include an ETag, a five-minute cache policy, the dataset contract version, and the v2 manifest hash. Read [API.md](API.md) for the request and response contract. Read [RAILWAY.md](RAILWAY.md) for deployment instructions. Railway supplies HTTPS for both its generated domain and custom domains.

## Legacy v1 is still here

Current consumers can keep using these paths and columns without a migration:

```text
tickers/all.csv
tickers/sp500.csv
tickers/top_50.csv
tickers/top_100.csv
tickers/top_200.csv
by_industry/*.csv
```

Every legacy v1 file keeps this column order:

```text
symbol,name,price,marketCap,volume,industry
```

The legacy v1 `industry` column contains Nasdaq's broader `sector` value. The name is inaccurate, but changing it would break existing consumers. V2 publishes both `sector` and the detailed source `industry` field under the correct names.

Read [DATA_CONTRACT.md](DATA_CONTRACT.md) for the full legacy v1 and v2 contracts.

## Update schedule

GitHub Actions fetches and validates the data on weekdays at 10:00 UTC. One successful run publishes:

- The compatible legacy v1 CSV collection and root `manifest.json`.
- `data/v2/tickers.csv` and `data/v2/manifest.json`.

The updater stages and validates legacy v1 and v2 together, then replaces them as one rollback-safe release. It rejects implausible source counts, duplicate normalized symbols, incomplete S&P matching, broken top lists, incorrect grouped files, and checksum mismatches.

When Railway watches the `main` branch, the resulting data commit triggers an API deployment. The API validates the v2 manifest during startup. A corrupt snapshot prevents the service from becoming healthy.

## Use the files directly

```python
import pandas as pd

v2 = pd.read_csv(
    "https://raw.githubusercontent.com/zyhe16/top-us-stock-tickers/main/data/v2/tickers.csv",
    keep_default_na=False,
)

us_technology = v2[
    (v2["is_us_domiciled"] == True)
    & (v2["sector"] == "Technology")
]
print(us_technology.head())
```

Use `keep_default_na=False` when symbols are important. `NA` is a valid ticker in this dataset, and pandas otherwise interprets it as a missing value.

The legacy v1 raw URLs still work:

```text
https://raw.githubusercontent.com/zyhe16/top-us-stock-tickers/main/tickers/all.csv
https://raw.githubusercontent.com/zyhe16/top-us-stock-tickers/main/tickers/sp500.csv
```

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python api.py
```

Open `http://127.0.0.1:8000/docs` for local API documentation. Production traffic uses Railway's HTTPS domain.

## Data rights

The MIT license covers this repository's code and documentation. It does not grant rights to third-party data. Read [DATA_LICENSE.md](DATA_LICENSE.md) before redistributing the files or exposing the API publicly.

See [CHANGELOG.md](CHANGELOG.md) for the v2 release notes.
