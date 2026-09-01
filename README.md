# Top US Stock Tickers

[![CI](https://github.com/zyhe16/top-us-stock-tickers/actions/workflows/ci.yml/badge.svg)](https://github.com/zyhe16/top-us-stock-tickers/actions/workflows/ci.yml)

Daily ticker data from the Nasdaq stock screener, with S&P 500 membership matched from Wikipedia.

Version 2 adds a richer dataset and a query API.

**Already using v1? Don't worry.** The old CSV paths and columns are still here. We now call that contract legacy v1. Existing scripts do not need to migrate until you want the new fields or the API.

## Project layout

```text
.
|-- src/top_us_stock_tickers/
|   |-- api.py                FastAPI application
|   |-- contracts.py          Shared v2 artifact validation
|   |-- publication.py        Legacy v1 and v2 publication transaction
|   |-- updater.py            Source fetch and updater command
|   `-- static/               Landing page, privacy notice, and fonts
|-- tickers/                  Legacy v1 ticker lists
|-- by_industry/              Legacy v1 grouped lists
|-- manifest.json             Legacy v1 snapshot metadata
|-- data/v2/
|   |-- tickers.csv           V2 dataset
|   `-- manifest.json         V2 snapshot metadata
|-- tests/                    Updater, publication, and HTTP tests
|-- .github/workflows/        CI and scheduled data update job
|-- Dockerfile                Railway API image
|-- requirements/             API and development dependencies
|-- docs/                     API, data, licensing, and Railway guides
|-- pyproject.toml            Python package metadata
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

## Use the API

The public API is available over HTTPS at:

```text
https://top-us-stock-tickers.zyhe.me
```

**The hosted API runs on three Railway replicas, one in California, one in Amsterdam, and one in Singapore.**

It is read-only and does not require an API key. Each client can make 120 API requests per 60-second window. A client that exceeds the limit receives `429 Too Many Requests` with a `Retry-After` header. Start with one ticker:

```bash
curl "https://top-us-stock-tickers.zyhe.me/api/v2/tickers/AAPL"
```

Or request a filtered page. This example returns the five largest technology securities in the current S&P 500 snapshot by Nasdaq market capitalization:

```bash
curl "https://top-us-stock-tickers.zyhe.me/api/v2/tickers?collection=sp500&sector=Technology&sort=market_cap&order=desc&limit=5"
```

The list response contains `items`, `total`, `limit`, `offset`, and `next_offset`. Pass `next_offset` back as `offset` to fetch the next page.

Python's default `urllib` user agent may be rejected at the network edge. Send a descriptive user agent for your application, as shown below.

```python
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

base_url = "https://top-us-stock-tickers.zyhe.me"
query = urlencode(
    {
        "collection": "sp500",
        "sector": "Technology",
        "sort": "market_cap",
        "order": "desc",
        "limit": 5,
    }
)

request = Request(
    f"{base_url}/api/v2/tickers?{query}",
    headers={"User-Agent": "my-ticker-app/1.0"},
)

with urlopen(request) as response:
    page = json.load(response)

for ticker in page["items"]:
    print(ticker["symbol"], ticker["market_cap"])
```

Browser applications can call the API directly:

```javascript
const response = await fetch(
  "https://top-us-stock-tickers.zyhe.me/api/v2/tickers?collection=sp500&limit=5",
);

if (!response.ok) throw new Error(`API returned ${response.status}`);

const page = await response.json();
console.log(page.items);
```

Common endpoints:

| Request | What it returns |
| --- | --- |
| `GET /api/v2/tickers/{symbol}` | One ticker. Symbol lookup is case-insensitive. |
| `GET /api/v2/tickers` | A filtered, sorted, paginated ticker collection. |
| `GET /api/v2/sectors` | Sector names and row counts. |
| `GET /api/v2/countries` | Country values and row counts. |
| `GET /api/v2/industries` | Detailed industry names and row counts. |
| `GET /api/v2/meta` | API version, manifest hash, and snapshot metadata. |
| `GET /health` | Service and loaded-snapshot status. |

The API reference is generated from the running FastAPI application:

- [Swagger UI](https://top-us-stock-tickers.zyhe.me/docs) lets you send requests from the browser.
- [ReDoc](https://top-us-stock-tickers.zyhe.me/redoc) presents the same contract as a reference page.
- [OpenAPI JSON](https://top-us-stock-tickers.zyhe.me/openapi.json) is the machine-readable contract.

Successful `/api/v2` responses include an ETag, a five-minute cache policy, the rate-limit policy, the dataset contract version, and the v2 manifest hash. Read [the API guide](docs/API.md) for every parameter, response field, and error. Read [the Railway guide](docs/RAILWAY.md) for deployment instructions.

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

Read [the dataset contract](docs/DATA_CONTRACT.md) for the full legacy v1 and v2 contracts.

## Update schedule

GitHub Actions runs the updater on weekdays at 10:17 and 12:47 UTC. The first
run is the normal update and the second is a same-day fallback. Both schedules
avoid the start of the hour, when GitHub says scheduled workflows are more
likely to be delayed. The workflow can also be started manually.
One successful run publishes:

- The compatible legacy v1 CSV collection and root `manifest.json`.
- `data/v2/tickers.csv` and `data/v2/manifest.json`.

The updater stages and validates legacy v1 and v2 together, then replaces them as one rollback-safe release. It rejects implausible source counts, duplicate normalized symbols, incomplete S&P matching, broken top lists, incorrect grouped files, and checksum mismatches.

When Railway watches the `main` branch, the resulting data commit triggers an API deployment. The API validates the v2 manifest during startup. A corrupt snapshot prevents the service from becoming healthy.

If both scheduled runs start, the concurrency guard prevents overlap. The
second run checks the committed manifest and skips the source fetch when the
snapshot was already generated on the current UTC day.

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
python -m pip install -r requirements/development.txt
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
python -m top_us_stock_tickers.api
```

Open `http://127.0.0.1:8000/docs` for local API documentation. Production traffic uses Railway's HTTPS domain.

To fetch and publish a fresh snapshot from the repository root, run:

```bash
python -m top_us_stock_tickers.updater
```

## Data rights

The MIT license covers this repository's code and documentation. It does not grant rights to third-party data. Read [the data reuse notes](docs/DATA_LICENSE.md) before redistributing the files or exposing the API publicly.

See [CHANGELOG.md](CHANGELOG.md) for the v2 release notes.
