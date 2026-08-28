"""Shared dataset field names and symbol normalization."""


LEGACY_COLUMNS = (
    "symbol",
    "name",
    "price",
    "marketCap",
    "volume",
    "industry",
)

V2_COLUMNS = (
    "symbol",
    "name",
    "price",
    "price_change",
    "percent_change",
    "market_cap",
    "volume",
    "country",
    "sector",
    "industry",
    "ipo_year",
    "nasdaq_url",
    "is_us_domiciled",
    "is_sp500",
)


def normalize_symbol(symbol):
    """Normalize share-class separators across data sources and API requests."""
    return symbol.strip().upper().replace(".", "/")
