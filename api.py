"""Read-only HTTP API for the checked-in ticker snapshot."""

from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from contracts import V2_COLUMNS, normalize_symbol


API_VERSION = "2.0.0"
API_PREFIX = "/api/v2"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
REPOSITORY_ROOT = Path(__file__).resolve().parent
V2_DATA_PATH = "data/v2/tickers.csv"
V2_MANIFEST_PATH = "data/v2/manifest.json"
MIN_ALL_TICKERS = 6_000
MIN_US_TICKERS = 4_000
MIN_SP500_TICKERS = 450


class Ticker(BaseModel):
    """One row from the v2 dataset."""

    model_config = ConfigDict(frozen=True)

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


class Page(BaseModel):
    items: list[Ticker]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class SectorCount(BaseModel):
    sector: str
    count: int


class SectorList(BaseModel):
    items: list[SectorCount]
    total: int


class CountryCount(BaseModel):
    country: str
    count: int


class CountryList(BaseModel):
    items: list[CountryCount]
    total: int


class IndustryCount(BaseModel):
    industry: str
    count: int


class IndustryList(BaseModel):
    items: list[IndustryCount]
    total: int


def _optional_float(value: str) -> float | None:
    if not value:
        return None
    return float(value)


def _optional_int(value: str) -> int | None:
    if not value:
        return None
    return int(float(value))


def _boolean(value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"Invalid boolean in v2 dataset: {value!r}")


def _read_csv(path: Path) -> tuple[Ticker, ...]:
    with path.open(newline="", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        if tuple(reader.fieldnames or ()) != V2_COLUMNS:
            raise ValueError("data/v2/tickers.csv has an unsupported schema")
        tickers = tuple(
            Ticker(
                symbol=row["symbol"],
                name=row["name"],
                price=_optional_float(row["price"]),
                price_change=_optional_float(row["price_change"]),
                percent_change=_optional_float(row["percent_change"]),
                market_cap=_optional_float(row["market_cap"]),
                volume=_optional_int(row["volume"]),
                country=row["country"],
                sector=row["sector"],
                industry=row["industry"],
                ipo_year=_optional_int(row["ipo_year"]),
                nasdaq_url=row["nasdaq_url"] or None,
                is_us_domiciled=_boolean(row["is_us_domiciled"]),
                is_sp500=_boolean(row["is_sp500"]),
            )
            for row in reader
        )

    symbols = [normalize_symbol(ticker.symbol) for ticker in tickers]
    if len(symbols) != len(set(symbols)):
        raise ValueError("data/v2/tickers.csv contains duplicate symbols")
    if any(not ticker.sector.strip() for ticker in tickers):
        raise ValueError("data/v2/tickers.csv contains an empty sector")
    if any(not ticker.industry.strip() for ticker in tickers):
        raise ValueError("data/v2/tickers.csv contains an empty industry")
    if len(tickers) < MIN_ALL_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few rows")
    if sum(ticker.is_us_domiciled for ticker in tickers) < MIN_US_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few US domicile rows")
    if sum(ticker.is_sp500 for ticker in tickers) < MIN_SP500_TICKERS:
        raise ValueError("data/v2/tickers.csv contains too few S&P 500 rows")
    return tickers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SnapshotStore:
    """An immutable, validated view of one published repository snapshot."""

    def __init__(self, root: Path):
        self.root = root
        data_path = root / V2_DATA_PATH
        manifest_path = root / V2_MANIFEST_PATH
        if not data_path.exists() or not manifest_path.exists():
            raise ValueError("The v2 dataset or manifest is missing")

        manifest_bytes = manifest_path.read_bytes()
        self.manifest = json.loads(manifest_bytes)
        if self.manifest.get("schemaVersion") != 1:
            raise ValueError("data/v2/manifest.json has an unsupported schema")
        if self.manifest.get("datasetContractVersion") != "v2":
            raise ValueError("data/v2/manifest.json has an unsupported contract")

        file_metadata = self.manifest.get("files", {}).get(V2_DATA_PATH, {})
        if _sha256(data_path) != file_metadata.get("sha256"):
            raise ValueError("Checksum mismatch for data/v2/tickers.csv")
        self.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        all_tickers = _read_csv(data_path)
        if len(all_tickers) != file_metadata.get("rows"):
            raise ValueError("Row-count mismatch for data/v2/tickers.csv")
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

    def etag_for(self, request: Request) -> str:
        resource = request.url.path
        if request.url.query:
            resource = f"{resource}?{request.url.query}"
        digest = hashlib.sha256(
            f"{API_VERSION}:{self.manifest_sha256}:{resource}".encode()
        ).hexdigest()
        return f'"{digest}"'


store = SnapshotStore(REPOSITORY_ROOT)

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
)


@app.middleware("http")
async def add_snapshot_headers(request: Request, call_next):
    response = await call_next(request)
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
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


@app.get("/", include_in_schema=False)
def root():
    return {
        "name": "Top US Stock Tickers API",
        "version": API_VERSION,
        "api": API_PREFIX,
        "documentation": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["service"])
def health():
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
    rows = list(store.collections[collection])

    if q:
        needle = q.casefold().strip()
        rows = [
            ticker
            for ticker in rows
            if needle in ticker.symbol.casefold() or needle in ticker.name.casefold()
        ]

    if sector:
        requested_sector = sector.casefold().strip()
        rows = [
            ticker
            for ticker in rows
            if ticker.sector.casefold() == requested_sector
        ]

    if country:
        requested_country = country.casefold().strip()
        rows = [
            ticker
            for ticker in rows
            if ticker.country.casefold() == requested_country
        ]

    if industry:
        requested_industry = industry.casefold().strip()
        rows = [
            ticker
            for ticker in rows
            if ticker.industry.casefold() == requested_industry
        ]

    if sort == "market_cap":
        populated = [ticker for ticker in rows if ticker.market_cap is not None]
        missing = [ticker for ticker in rows if ticker.market_cap is None]
        rows = sorted(
            populated,
            key=lambda ticker: ticker.market_cap,
            reverse=order == "desc",
        ) + missing
    else:
        rows.sort(
            key=lambda ticker: getattr(ticker, sort).casefold(),
            reverse=order == "desc",
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
def get_ticker(symbol: str):
    ticker = store.by_symbol.get(normalize_symbol(symbol))
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"Ticker {symbol!r} was not found")
    return ticker


@app.get(f"{API_PREFIX}/sectors", response_model=SectorList, tags=["reference"])
def list_sectors():
    counts = Counter(ticker.sector for ticker in store.collections["all"])
    items = [
        SectorCount(sector=sector, count=count)
        for sector, count in sorted(counts.items())
    ]
    return SectorList(items=items, total=len(items))


@app.get(f"{API_PREFIX}/countries", response_model=CountryList, tags=["reference"])
def list_countries():
    counts = Counter(
        ticker.country or "Unspecified" for ticker in store.collections["all"]
    )
    items = [
        CountryCount(country=country, count=count)
        for country, count in sorted(counts.items())
    ]
    return CountryList(items=items, total=len(items))


@app.get(f"{API_PREFIX}/industries", response_model=IndustryList, tags=["reference"])
def list_industries():
    counts = Counter(ticker.industry for ticker in store.collections["all"])
    items = [
        IndustryCount(industry=industry, count=count)
        for industry, count in sorted(counts.items())
    ]
    return IndustryList(items=items, total=len(items))


@app.get(f"{API_PREFIX}/meta", tags=["reference"])
def metadata():
    return {
        "apiVersion": API_VERSION,
        "datasetContractVersion": store.manifest["datasetContractVersion"],
        "manifestSha256": store.manifest_sha256,
        "manifest": store.manifest,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
