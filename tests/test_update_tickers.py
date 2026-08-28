import unittest
from unittest import mock

from update_tickers import (
    fetch_tickers,
    parse_int,
    parse_market_cap,
    parse_number,
    parse_percent,
    parse_sp500_symbols,
)


class ParseSp500SymbolsTests(unittest.TestCase):
    def test_parses_current_and_historical_wikipedia_table_cells(self):
        content = """
== S&P 500 component stocks ==
|{{NyseSymbol|MMM}}
|| {{NasdaqSymbol|AAPL}}
|| {{NyseSymbol|BRK.B}}
|| {{NyseSymbol|BRK/B}}
|| {{BZX link|CBOE}}
== Selected changes to the list of S&P 500 components ==
|| {{NyseSymbol|SHOULD_NOT_BE_INCLUDED}}
"""

        self.assertEqual(
            parse_sp500_symbols(content),
            ["MMM", "AAPL", "BRK.B", "CBOE"],
        )

    def test_rejects_content_without_constituents_section(self):
        with self.assertRaisesRegex(ValueError, "missing constituents section"):
            parse_sp500_symbols("Wikipedia error page")


class NumberParsingTests(unittest.TestCase):
    def test_parses_source_number_formats(self):
        self.assertEqual(parse_market_cap("$1.25B"), 1_250_000_000)
        self.assertEqual(parse_market_cap("$750M"), 750_000_000)
        self.assertEqual(parse_number("$1,234.50"), 1234.5)
        self.assertEqual(parse_int("1,234"), 1234)
        self.assertEqual(parse_percent("2.957%"), 2.957)

    def test_returns_none_for_missing_or_invalid_numbers(self):
        for value in ("", "N/A", "not-a-number"):
            self.assertIsNone(parse_market_cap(value))
            self.assertIsNone(parse_number(value))
            self.assertIsNone(parse_int(value))
            self.assertIsNone(parse_percent(value))


class FetchTickersTests(unittest.TestCase):
    @mock.patch("update_tickers.time.sleep")
    @mock.patch("update_tickers.requests.get")
    def test_keeps_the_legacy_country_and_sector_semantics(self, get, _sleep):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "rows": [
                    {
                        "symbol": "AAA",
                        "name": "AAA Common Stock",
                        "lastsale": "$10.00",
                        "marketCap": "$1B",
                        "volume": "1,000",
                        "sector": "Technology",
                        "industry": "Computer Manufacturing",
                        "ipoyear": "1980",
                        "netchange": "1.5",
                        "pctchange": "15%",
                        "url": "/market-activity/stocks/aaa",
                        "country": "United States",
                    },
                    {
                        "symbol": "BBB",
                        "name": "BBB Common Stock",
                        "lastsale": "$20.00",
                        "marketCap": "$2B",
                        "volume": "2,000",
                        "sector": "Finance",
                        "industry": "Major Banks",
                        "country": "Canada",
                    },
                ]
            }
        }
        get.return_value = response

        united_states_rows, all_rows, v2_rows = fetch_tickers()

        self.assertEqual([row["symbol"] for row in united_states_rows], ["AAA"])
        self.assertEqual([row["symbol"] for row in all_rows], ["AAA", "BBB"])
        self.assertEqual(united_states_rows[0]["industry"], "Technology")
        self.assertEqual(v2_rows[0]["sector"], "Technology")
        self.assertEqual(v2_rows[0]["industry"], "Computer Manufacturing")
        self.assertEqual(v2_rows[0]["ipo_year"], 1980)
        self.assertEqual(v2_rows[0]["percent_change"], 15.0)
        self.assertTrue(v2_rows[0]["is_us_domiciled"])

    @mock.patch("update_tickers.time.sleep")
    @mock.patch("update_tickers.requests.get")
    def test_rejects_duplicate_source_symbols(self, get, _sleep):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "rows": [
                    {"symbol": "AAA", "country": "United States"},
                    {"symbol": "AAA", "country": "United States"},
                ]
            }
        }
        get.return_value = response

        self.assertEqual(fetch_tickers(), ([], [], []))


if __name__ == "__main__":
    unittest.main()
