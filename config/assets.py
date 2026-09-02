"""Configurable asset lists.

OTC instruments are separate instruments and must never be merged with their
non-OTC counterparts (candles, patterns, statistics, backtests, results).

`POCKET_OPTION_SYMBOLS` holds the Pocket Option symbol for instruments whose
symbol cannot be derived from the display name (stocks use a `#` prefix). The
symbols are verified against Pocket Option's own `updateAssets` list at
startup, so a wrong entry is reported instead of silently producing no data.
"""

ASSETS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
]

OTC_ASSETS: list[str] = [
    # "EUR/USD OTC",
]

#: Pocket Option OTC stocks (payouts shown in the platform's Stocks tab).
STOCK_ASSETS: list[str] = [
    "Cisco OTC",
    "Intel OTC",
    "Johnson & Johnson OTC",
    "McDonald's OTC",
    "Advanced Micro Devices OTC",
    "Alibaba OTC",
    "FedEx OTC",
    "VISA OTC",
    "Amazon OTC",
    "Pfizer Inc OTC",
    "Boeing Company OTC",
    "ExxonMobil OTC",
    "Facebook OTC",
    "Apple OTC",
    "Microsoft OTC",
    "Tesla OTC",
]

#: Display name -> Pocket Option symbol.
POCKET_OPTION_SYMBOLS: dict[str, str] = {
    "Cisco OTC": "#CSCO_otc",
    "Intel OTC": "#INTC_otc",
    "Johnson & Johnson OTC": "#JNJ_otc",
    "McDonald's OTC": "#MCD_otc",
    "Advanced Micro Devices OTC": "AMD_otc",
    "Alibaba OTC": "BABA_otc",
    "FedEx OTC": "FDX_otc",
    "VISA OTC": "VISA_otc",
    "Amazon OTC": "AMZN_otc",
    "Pfizer Inc OTC": "#PFE_otc",
    "Boeing Company OTC": "#BA_otc",
    "ExxonMobil OTC": "#XOM_otc",
    "Facebook OTC": "#FB_otc",
    "Apple OTC": "#AAPL_otc",
    "Microsoft OTC": "#MSFT_otc",
    "Tesla OTC": "#TSLA_otc",
}


def all_assets() -> list[str]:
    return [*ASSETS, *OTC_ASSETS, *STOCK_ASSETS]


def pocket_option_symbol(asset: str) -> str | None:
    """Configured Pocket Option symbol for `asset`, if it needs an override."""
    return POCKET_OPTION_SYMBOLS.get(asset.strip())


def is_otc(asset: str) -> bool:
    return asset.strip().upper().endswith("OTC")


def asset_slug(asset: str) -> str:
    """EUR/USD -> EURUSD ; EUR/USD OTC -> EURUSDOTC ; Cisco OTC -> CISCOOTC"""
    cleaned = asset.replace("/", "").replace(" ", "").replace("&", "AND")
    return "".join(char for char in cleaned if char.isalnum()).upper()
