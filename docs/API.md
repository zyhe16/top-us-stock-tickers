# API contract

The API is read-only. Version 2 lives under `/api/v2` and reads the checked-in `data/v2` snapshot into memory when the process starts.

## Public service

The hosted base URL is:

```text
https://top-us-stock-tickers.zyhe.me
```

The API does not require a key. It accepts `GET` requests. Try it with curl:

```bash
curl "https://top-us-stock-tickers.zyhe.me/api/v2/tickers/AAPL"
```

Automated clients should send a descriptive `User-Agent` header. The network edge may reject default client headers, including Python's `urllib` default.

The running service publishes three generated references:

- [Swagger UI](https://top-us-stock-tickers.zyhe.me/docs)
- [ReDoc](https://top-us-stock-tickers.zyhe.me/redoc)
- [OpenAPI JSON](https://top-us-stock-tickers.zyhe.me/openapi.json)

The examples below show paths relative to the hosted base URL.

## Rate limit

The application allows 120 `/api/v2` requests per client in a rolling 60-second window. The limit applies to requests that reach the application, not the landing page, privacy notice, generated documentation, or health check. Cloudflare may answer repeated cacheable requests at its edge; those requests do not reach Railway or consume an application counter.

API responses include the current limit:

```text
X-RateLimit-Limit: 120
X-RateLimit-Window: 60
```

If the limit is exceeded, the service returns `429 Too Many Requests` with `X-RateLimit-Remaining: 0`. Wait for the number of seconds in `Retry-After` before sending another request. Counters are temporary, live only in application memory, and reset when the process restarts. Successful responses omit a remaining-request count because a shared Cloudflare cache must not replay one client's counter to another client.

## Ticker fields

| Field | Type | Meaning |
| --- | --- | --- |
| `symbol` | string | Symbol returned by the Nasdaq screener. |
| `name` | string | Security name returned by Nasdaq. |
| `price` | number or null | Parsed Nasdaq `lastsale` value. |
| `price_change` | number or null | Parsed Nasdaq `netchange` value. |
| `percent_change` | number or null | Parsed Nasdaq `pctchange`, expressed in percentage points. `2.5` means 2.5 percent. |
| `market_cap` | number or null | Parsed market capitalization in US dollars. |
| `volume` | integer or null | Parsed source volume. |
| `country` | string | Source country value. It can be empty. |
| `sector` | string | Nasdaq sector, or `Uncategorized` when absent. |
| `industry` | string | Detailed Nasdaq industry, or `Uncategorized` when absent. |
| `ipo_year` | integer or null | Parsed source IPO year. |
| `nasdaq_url` | string or null | Absolute URL built from the source security path. |
| `is_us_domiciled` | boolean | Whether the source country is exactly `United States`. |
| `is_sp500` | boolean | Whether the normalized symbol matched the Wikipedia S&P 500 list in this snapshot. |

These fields describe securities, not deduplicated companies. Multiple share classes and other security types can appear.

## List tickers

`GET /api/v2/tickers`

Example:

```bash
curl "https://top-us-stock-tickers.zyhe.me/api/v2/tickers?collection=sp500&sector=Technology&sort=market_cap&order=desc&limit=5"
```

Query parameters:

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `collection` | `all` | `all`, `us`, `sp500`, `top_50`, `top_100`, `top_200` |
| `q` | none | Case-insensitive substring of the symbol or name. |
| `country` | none | Exact case-insensitive country match. |
| `sector` | none | Exact case-insensitive sector match. |
| `industry` | none | Exact case-insensitive industry match. |
| `sort` | `market_cap` | `market_cap`, `symbol`, `name` |
| `order` | `desc` | `asc`, `desc` |
| `limit` | `100` | Integer from 1 through 500. |
| `offset` | `0` | Non-negative integer. |

`all` means every unique row returned by the Nasdaq screener. `us` applies the exact source country test used by legacy v1's `tickers/all.csv`. The top collections rank that United States domicile subset by market capitalization.

Example response:

```json
{
  "items": [
    {
      "symbol": "AAPL",
      "name": "Apple Inc. Common Stock",
      "price": 314.58,
      "price_change": 1.13,
      "percent_change": 0.361,
      "market_cap": 4591037144400.0,
      "volume": 32363857,
      "country": "United States",
      "sector": "Technology",
      "industry": "Computer Manufacturing",
      "ipo_year": 1980,
      "nasdaq_url": "https://www.nasdaq.com/market-activity/stocks/aapl",
      "is_us_domiciled": true,
      "is_sp500": true
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "next_offset": null
}
```

## Get one ticker

`GET /api/v2/tickers/{symbol}`

Symbol lookup is case-insensitive. It treats `.` and `/` as the same share-class separator, so both `BRK.B` and `BRK/B` resolve to the source record. Unknown symbols return `404`.

```bash
curl "https://top-us-stock-tickers.zyhe.me/api/v2/tickers/BRK.B"
```

## Reference endpoints

- `GET /api/v2/sectors` returns sector names and counts.
- `GET /api/v2/countries` returns country values and counts. Empty source values appear as `Unspecified` in this list.
- `GET /api/v2/industries` returns detailed industry names and counts.
- `GET /api/v2/meta` returns the API version, v2 dataset version, manifest hash, and complete v2 manifest.
- `GET /health` reports whether the service loaded a valid snapshot.

## Caching and browser access

Every successful `/api/v2` response includes:

```text
Cache-Control: public, max-age=300
ETag: "..."
X-Dataset-Contract: v2
X-Manifest-SHA256: ...
```

Send the ETag in `If-None-Match` to receive `304 Not Modified` when that request still maps to the same snapshot. The API allows cross-origin `GET` requests so browser applications can call it directly.

## Errors

FastAPI returns JSON errors. Invalid query values return `422`. Unknown symbols return `404`. Rate-limited clients receive `429` with a `Retry-After` header. A missing file, schema mismatch, row-count mismatch, or checksum mismatch stops the process during startup rather than exposing a partial dataset.
