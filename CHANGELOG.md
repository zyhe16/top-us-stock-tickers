# Changelog

This file records changes to the dataset contracts, API, updater, validation, and deployment behavior.

## Unreleased

### Changed

- `/api/v2` now has a per-client sliding-window rate limit, response quota headers, and a bounded in-memory counter store. The default is 120 requests per 60 seconds.
- The production server now caps concurrent requests and uses bounded keep-alive and graceful-shutdown timeouts.
- All application responses now include browser security headers. The landing and privacy pages also use a restrictive Content Security Policy.
- GitHub Actions now use immutable commit references, and Dependabot checks Python and workflow dependencies each week.
- The root URL now shows a blue landing page with links to the API docs, sample JSON, OpenAPI schema, health check, GitHub repository, and both dataset versions.
- The landing page and README now identify the API's three Railway regions: California, Amsterdam, and Singapore.
- The landing page serves Inter and Space Grotesk from the API host. Visiting the page no longer sends a font request to Google.
- The privacy notice now explains Cloudflare and Railway processing, temporary rate-limit counters, possible Cloudflare security cookies, and the external assets used by the generated API documentation.
- Application access logs are disabled. Error logs remain available for operating the service.
- The README and API guide now use the public HTTPS domain and include copy-paste examples for curl, Python, and browser JavaScript.

## 2.0.0 - 2026-08-28

### Added

- `data/v2/tickers.csv`, a new dataset containing every unique Nasdaq screener row rather than only rows with `United States` in the country field.
- Correct `sector` and detailed `industry` columns in v2.
- Country, IPO year, price change, percentage change, Nasdaq URL, United States domicile, and S&P 500 fields.
- A separate v2 manifest with source timestamps, quality counts, row count, and SHA-256 checksum.
- A read-only FastAPI service under `/api/v2` with lookup, search, filters, sorting, pagination, reference values, and metadata.
- OpenAPI and interactive API documentation.
- ETag and conditional-request support, five-minute public caching, and browser access through CORS.
- A Railway-ready Docker image that runs as a non-root user and reads Railway's injected `PORT`.
- Real HTTP integration tests that start the production entrypoint.
- Deployment and API documentation.

### Changed

- The updater now builds both contracts from one Nasdaq response and one Wikipedia membership response.
- Legacy v1 and v2 now publish as one rollback-safe release instead of two sequential filesystem transactions.
- Established legacy v1 industry paths remain available as header-only CSVs if an industry disappears from a later source snapshot.
- Production dependencies now use tested top-level versions.
- The scheduled workflow stages the v2 dataset and manifest with the existing files.
- The root manifest identifies the existing CSV contract as `legacy-v1`.
- CI compiles and tests the API as well as the updater.

### Data corrections

- V2 no longer labels Nasdaq's sector value as an industry.
- V2 preserves both the complete source result and an explicit United States domicile flag, so consumers do not have to infer what "all" means.
- CSV loading preserves valid symbols such as `NA` instead of treating them as null values.
- Missing sector and industry values become `Uncategorized` independently.

### Compatibility

- No existing legacy v1 CSV file changed during the initial v2 work.
- Existing paths, column order, country selection, per-security rows, and sorting remain unchanged.
- Consumers can migrate to v2 on their own schedule.

### Deployment

- Railway supplies HTTPS at its public edge for generated and custom domains. The application uses HTTP only inside the container.
- The API validates the v2 schema and checksum before it becomes healthy.

## 1.1.0 - 2026-08-28

### Added

- Fail-closed checks for Nasdaq row counts, required fields, normalized symbol uniqueness, Wikipedia source size, and S&P 500 match rate.
- Staged legacy v1 publication with rollback when replacement fails.
- Cross-file checks for top-list prefixes and the complete `by_industry` partition.
- A root manifest for legacy v1 provenance, quality counts, row counts, and checksums.
- Read-only CI for pushes and pull requests.
- Dataset contract and source-rights documentation.

### Changed

- The README now describes the actual weekday schedule, source semantics, and canonical repository URLs.
- The scheduled updater stages the root manifest with the legacy v1 CSV files.

### Compatibility

- The six legacy v1 columns and every existing dataset path stayed unchanged.
