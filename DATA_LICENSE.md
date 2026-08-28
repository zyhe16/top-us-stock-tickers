# Data sources and reuse

The MIT license in `LICENSE` applies to this repository's code and documentation. It does not create a license for data obtained from third parties.

## Nasdaq

The project obtains security names, prices, changes, market caps, volumes, country values, sector values, industries, IPO years, and security paths from the public [Nasdaq stock screener endpoint](https://api.nasdaq.com/api/screener/stocks).

This repository does not grant permission to copy, redistribute, sell, or commercially use Nasdaq-sourced data. Nasdaq publishes terms and restrictions on its [legal page](https://www.nasdaq.com/legal). Maintainers and consumers must determine whether their use of the endpoint and generated files complies with the terms that apply to them.

The project has not recorded a separate Nasdaq data-distribution agreement. That gap should be resolved before treating these CSVs as a generally redistributable data product.

The v2 HTTP API is another way to distribute the same source-derived data. Hosting it on Railway does not change or expand the rights granted by Nasdaq or any other source.

## Wikipedia and S&P 500 membership

The project extracts membership symbols from Wikipedia's [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies). It does not copy the article into this repository. The [Wikimedia Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content) explain the rules for reusing Wikimedia content and distinguish contributed facts from licensed text.

"S&P 500" identifies the referenced index. No affiliation with or endorsement by S&P Global is claimed.

## Consumer responsibility

The files are informational outputs. Nothing in this repository grants data-use rights beyond the rights supplied by each source. The files are not investment advice, a trading recommendation, or a guarantee of accuracy, completeness, timeliness, or continued source availability.

This document records known source and licensing constraints. It is not legal advice.
