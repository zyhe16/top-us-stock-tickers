import unittest

from update_tickers import parse_sp500_symbols


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


if __name__ == "__main__":
    unittest.main()
