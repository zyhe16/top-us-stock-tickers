FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements/api.txt ./requirements/api.txt
RUN pip install --no-cache-dir -r requirements/api.txt

RUN useradd --create-home --uid 10001 api

COPY --chown=api:api \
    src/top_us_stock_tickers/__init__.py \
    src/top_us_stock_tickers/api.py \
    src/top_us_stock_tickers/contracts.py \
    ./src/top_us_stock_tickers/
COPY --chown=api:api src/top_us_stock_tickers/static ./src/top_us_stock_tickers/static
COPY --chown=api:api data/v2 ./data/v2

USER api
EXPOSE 8000

CMD ["python", "-m", "top_us_stock_tickers.api"]
