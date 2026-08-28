"""Read-only HTTP API for the checked-in ticker snapshot."""

import asyncio
import hashlib
import ipaddress
import math
import mimetypes
from operator import attrgetter
import os
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contracts import V2_COLUMNS, load_v2_snapshot, normalize_symbol

API_VERSION = "2.0.0"
API_PREFIX = "/api/v2"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
REPOSITORY_ROOT = Path(__file__).resolve().parent
LANDING_PAGE_PATH = REPOSITORY_ROOT / "landing.html"
PRIVACY_PAGE_PATH = REPOSITORY_ROOT / "privacy.html"
ASSETS_PATH = REPOSITORY_ROOT / "assets"
DEFAULT_MAX_CONCURRENCY = 64
DEFAULT_RATE_LIMIT_REQUESTS = 120
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMIT_MAX_CLIENTS = 10_000
SECURITY_HEADERS = {
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
STATIC_PAGE_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
    )
)
TICKER_VALUES = attrgetter(*V2_COLUMNS)

mimetypes.add_type("font/woff2", ".woff2")


def _positive_environment_integer(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class RateLimitDecision:
    """The result of checking one client against a sliding time window."""

    allowed: bool
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    """A process-local, bounded sliding-window limiter keyed by client."""

    def __init__(
        self,
        *,
        requests: int,
        window_seconds: int,
        max_clients: int = DEFAULT_RATE_LIMIT_MAX_CLIENTS,
    ):
        if requests < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("Rate-limit values must be positive integers")
        self.requests = requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_cleanup = 0.0

    async def check(
        self, client: str, *, now: float | None = None
    ) -> RateLimitDecision:
        current_time = time.monotonic() if now is None else now
        cutoff = current_time - self.window_seconds

        async with self._lock:
            if current_time - self._last_cleanup >= self.window_seconds:
                stale_clients = [
                    key
                    for key, timestamps in self._requests.items()
                    if not timestamps or timestamps[-1] <= cutoff
                ]
                for key in stale_clients:
                    del self._requests[key]
                self._last_cleanup = current_time

            timestamps = self._requests.get(client)
            if timestamps is None:
                if len(self._requests) >= self.max_clients:
                    self._requests.popitem(last=False)
                timestamps = deque()
                self._requests[client] = timestamps
            else:
                self._requests.move_to_end(client)
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.requests:
                retry_after = max(
                    1,
                    math.ceil(
                        timestamps[0] + self.window_seconds - current_time
                    ),
                )
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )

            timestamps.append(current_time)
            return RateLimitDecision(
                allowed=True,
                remaining=self.requests - len(timestamps),
                retry_after=0,
            )


def _client_identifier(request: Request) -> str:
    cloudflare_ip = request.headers.get("cf-connecting-ip")
    if cloudflare_ip:
        try:
            return ipaddress.ip_address(cloudflare_ip.strip()).compressed
        except ValueError:
            pass
    if request.client is not None:
        return request.client.host
    return "unknown"


@dataclass(frozen=True, slots=True)
class Ticker:
    """One row from the v2 dataset."""

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
class Page:
    items: tuple[Ticker, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class SectorCount:
    sector: str
    count: int


@dataclass(frozen=True, slots=True)
class SectorList:
    items: tuple[SectorCount, ...]
    total: int


@dataclass(frozen=True, slots=True)
class CountryCount:
    country: str
    count: int


@dataclass(frozen=True, slots=True)
class CountryList:
    items: tuple[CountryCount, ...]
    total: int


@dataclass(frozen=True, slots=True)
class IndustryCount:
    industry: str
    count: int


@dataclass(frozen=True, slots=True)
class IndustryList:
    items: tuple[IndustryCount, ...]
    total: int



class SnapshotStore:
    """An immutable, validated view of one published repository snapshot."""

    def __init__(self, root: Path):
        self.root = root
        snapshot = load_v2_snapshot(root)
        self.manifest = snapshot.manifest
        self.manifest_sha256 = snapshot.manifest_sha256
        all_tickers = tuple(Ticker(*TICKER_VALUES(row)) for row in snapshot.rows)
        us_tickers = tuple(
            ticker for ticker in all_tickers if ticker.is_us_domiciled
        )
        self.collections = {
            "all": all_tickers,
            "us": us_tickers,
            "sp500": tuple(ticker for ticker in all_tickers if ticker.is_sp500),
            "top_50": us_tickers[:50],
            "top_100": us_tickers[:100],
            "top_200": us_tickers[:200],
        }

        lookup = {normalize_symbol(ticker.symbol): ticker for ticker in all_tickers}
        self.by_symbol = lookup
        sector_counts = Counter(ticker.sector for ticker in all_tickers)
        self.sectors = SectorList(
            items=tuple(
                SectorCount(sector=sector, count=count)
                for sector, count in sorted(sector_counts.items())
            ),
            total=len(sector_counts),
        )
        country_counts = Counter(
            ticker.country or "Unspecified" for ticker in all_tickers
        )
        self.countries = CountryList(
            items=tuple(
                CountryCount(country=country, count=count)
                for country, count in sorted(country_counts.items())
            ),
            total=len(country_counts),
        )
        industry_counts = Counter(ticker.industry for ticker in all_tickers)
        self.industries = IndustryList(
            items=tuple(
                IndustryCount(industry=industry, count=count)
                for industry, count in sorted(industry_counts.items())
            ),
            total=len(industry_counts),
        )

    def etag_for(self, request: Request) -> str:
        resource = request.url.path
        if request.url.query:
            resource = f"{resource}?{request.url.query}"
        digest = hashlib.sha256(
            f"{API_VERSION}:{self.manifest_sha256}:{resource}".encode()
        ).hexdigest()
        return f'"{digest}"'


store = SnapshotStore(REPOSITORY_ROOT)
LANDING_PAGE = LANDING_PAGE_PATH.read_text(encoding="utf-8")
PRIVACY_PAGE = PRIVACY_PAGE_PATH.read_text(encoding="utf-8")
rate_limiter = SlidingWindowRateLimiter(
    requests=_positive_environment_integer(
        "API_RATE_LIMIT_REQUESTS", DEFAULT_RATE_LIMIT_REQUESTS
    ),
    window_seconds=_positive_environment_integer(
        "API_RATE_LIMIT_WINDOW_SECONDS", DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    ),
    max_clients=_positive_environment_integer(
        "API_RATE_LIMIT_MAX_CLIENTS", DEFAULT_RATE_LIMIT_MAX_CLIENTS
    ),
)

app = FastAPI(
    title="Top US Stock Tickers API",
    summary="Query the validated ticker snapshot published by this repository.",
    description=(
        "Version 2 exposes the richer data/v2 snapshot as read-only JSON. "
        "The legacy v1 CSV files remain available for existing consumers."
    ),
    version=API_VERSION,
    license_info={
        "name": "MIT for code only",
        "url": (
            "https://github.com/zyhe16/top-us-stock-tickers/"
            "blob/main/DATA_LICENSE.md"
        ),
    },
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=[
        "ETag",
        "Retry-After",
        "X-Dataset-Contract",
        "X-Manifest-SHA256",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Window",
    ],
)
app.mount("/assets", StaticFiles(directory=ASSETS_PATH), name="assets")


@app.middleware("http")
async def add_response_headers(request: Request, call_next):
    rate_limit_decision = None
    if request.method == "GET" and request.url.path.startswith(API_PREFIX):
        rate_limit_decision = await rate_limiter.check(
            _client_identifier(request)
        )
        if not rate_limit_decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Rate limit exceeded. Retry after "
                        f"{rate_limit_decision.retry_after} seconds."
                    )
                },
                headers={
                    **SECURITY_HEADERS,
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": (
                        "Retry-After, X-RateLimit-Limit, "
                        "X-RateLimit-Remaining, X-RateLimit-Window"
                    ),
                    "Cache-Control": "no-store",
                    "Retry-After": str(rate_limit_decision.retry_after),
                    "X-RateLimit-Limit": str(rate_limiter.requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window": str(rate_limiter.window_seconds),
                },
            )

    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if rate_limit_decision is not None:
        response.headers.update(
            {
                "X-RateLimit-Limit": str(rate_limiter.requests),
                "X-RateLimit-Window": str(rate_limiter.window_seconds),
            }
        )
    if request.url.path in {"/", "/privacy"}:
        response.headers["Content-Security-Policy"] = (
            STATIC_PAGE_CONTENT_SECURITY_POLICY
        )
    if (
        not request.url.path.startswith(API_PREFIX)
        or request.method not in {"GET", "HEAD"}
        or response.status_code != 200
    ):
        return response

    etag = store.etag_for(request)
    headers = {
        "Cache-Control": "public, max-age=300",
        "ETag": etag,
        "X-Dataset-Contract": store.manifest["datasetContractVersion"],
        "X-Manifest-SHA256": store.manifest_sha256,
        **SECURITY_HEADERS,
    }
    if rate_limit_decision is not None:
        headers.update(
            {
                "X-RateLimit-Limit": str(rate_limiter.requests),
                "X-RateLimit-Window": str(rate_limiter.window_seconds),
            }
        )
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    return LANDING_PAGE


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy():
    return PRIVACY_PAGE


@app.get("/health", tags=["service"])
async def health():
    return {
        "status": "ok",
        "apiVersion": API_VERSION,
        "datasetContractVersion": store.manifest["datasetContractVersion"],
        "manifestSha256": store.manifest_sha256,
    }


@app.get(f"{API_PREFIX}/tickers", response_model=Page, tags=["tickers"])
def list_tickers(
    collection: Literal[
        "all", "us", "sp500", "top_50", "top_100", "top_200"
    ] = "all",
    q: str | None = Query(default=None, min_length=1, max_length=100),
    country: str | None = Query(default=None, min_length=1, max_length=100),
    sector: str | None = Query(default=None, min_length=1, max_length=100),
    industry: str | None = Query(default=None, min_length=1, max_length=200),
    sort: Literal["market_cap", "symbol", "name"] = "market_cap",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    rows = store.collections[collection]
    needle = q.casefold().strip() if q else None
    requested_sector = sector.casefold().strip() if sector else None
    requested_country = country.casefold().strip() if country else None
    requested_industry = industry.casefold().strip() if industry else None
    if (
        needle is not None
        or requested_sector is not None
        or requested_country is not None
        or requested_industry is not None
    ):
        rows = tuple(
            ticker
            for ticker in rows
            if (
                needle is None
                or needle in ticker.symbol.casefold()
                or needle in ticker.name.casefold()
            )
            and (
                requested_sector is None
                or ticker.sector.casefold() == requested_sector
            )
            and (
                requested_country is None
                or ticker.country.casefold() == requested_country
            )
            and (
                requested_industry is None
                or ticker.industry.casefold() == requested_industry
            )
        )

    if sort == "market_cap" and order == "asc":
        populated = [ticker for ticker in rows if ticker.market_cap is not None]
        missing = [ticker for ticker in rows if ticker.market_cap is None]
        rows = tuple(
            sorted(
                populated,
                key=lambda ticker: ticker.market_cap,
            )
            + missing
        )
    elif sort != "market_cap":
        rows = tuple(
            sorted(
                rows,
                key=lambda ticker: getattr(ticker, sort).casefold(),
                reverse=order == "desc",
            )
        )

    total = len(rows)
    items = rows[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return Page(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        next_offset=next_offset,
    )


@app.get(
    f"{API_PREFIX}/tickers/{{symbol:path}}",
    response_model=Ticker,
    tags=["tickers"],
)
async def get_ticker(symbol: str):
    ticker = store.by_symbol.get(normalize_symbol(symbol))
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"Ticker {symbol!r} was not found")
    return ticker


@app.get(f"{API_PREFIX}/sectors", response_model=SectorList, tags=["reference"])
async def list_sectors():
    return store.sectors


@app.get(f"{API_PREFIX}/countries", response_model=CountryList, tags=["reference"])
async def list_countries():
    return store.countries


@app.get(f"{API_PREFIX}/industries", response_model=IndustryList, tags=["reference"])
async def list_industries():
    return store.industries


@app.get(f"{API_PREFIX}/meta", tags=["reference"])
async def metadata():
    return {
        "apiVersion": API_VERSION,
        "datasetContractVersion": store.manifest["datasetContractVersion"],
        "manifestSha256": store.manifest_sha256,
        "manifest": store.manifest,
    }


def run():
    import uvicorn

    uvicorn.run(
        "api:app",
        # Railway requires the process to accept traffic outside the container.
        host="0.0.0.0",  # nosec B104
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,
        limit_concurrency=_positive_environment_integer(
            "API_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY
        ),
        timeout_keep_alive=5,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    run()
