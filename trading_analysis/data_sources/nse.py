from __future__ import annotations


class NseDataAccess:
    """Notes for using NSE data in an automated pipeline.

    NSE exposes public pages for manual reference and paid/authorized products for
    automated real-time, delayed, snapshot, historical, corporate, and master data.
    This project avoids brittle website scraping by default.
    """

    option_chain_url = "https://www.nseindia.com/option-chain"
    market_data_products_url = "https://www.nseindia.com/market-data/real-time-data-subscription"

    def option_chain_sync_status(self) -> str:
        return (
            "Not configured. Use broker option-chain/quote APIs or an authorized "
            "NSE Data & Analytics product for automated option-chain ingestion."
        )

