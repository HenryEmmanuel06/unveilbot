from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.data.base import ProviderError
from src.data.pocket_option import (
    POCKET_OPTION_SOURCE,
    REGIONS,
    PocketOptionClient,
    PocketOptionSsid,
    candles_from_server,
    candles_from_ticks,
    normalize_timestamp,
    parse_assets,
    parse_history_period,
    region_urls,
    to_pocket_symbol,
)

DEMO_SSID = (
    '42["auth",{"session":"demo-session-token","isDemo":1,"uid":123456,'
    '"platform":1,"isFastHistory":true}]'
)
REAL_SSID = (
    '42["auth",{"session":"a:4:{s:10:\\"session_id\\";s:32:\\"abc\\";}","isDemo":0,'
    '"uid":987654,"platform":2}]'
)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


def make_client(ssid_raw: str = DEMO_SSID) -> tuple[PocketOptionClient, FakeSocket]:
    client = PocketOptionClient(PocketOptionSsid.parse(ssid_raw))
    socket = FakeSocket()
    client._ws = socket
    return client, socket


# --------------------------------------------------------------------- SSID
def test_parses_auth_message_format():
    ssid = PocketOptionSsid.parse(DEMO_SSID)
    assert ssid.auth_message == DEMO_SSID
    assert ssid.is_demo
    assert ssid.uid == "123456"


def test_parses_bare_json_payload():
    payload = '{"session":"tok","isDemo":0,"uid":42,"platform":2}'
    ssid = PocketOptionSsid.parse(payload)
    assert ssid.auth_message == f'42["auth",{payload}]'
    assert not ssid.is_demo


def test_demo_detected_from_current_url():
    ssid = PocketOptionSsid.parse(
        '{"session":"tok","uid":1,"platform":2,"currentUrl":"demo-quick-high-low"}'
    )
    assert ssid.is_demo


def test_missing_ssid_raises_clear_error():
    with pytest.raises(ProviderError, match="PO_SSID is missing"):
        PocketOptionSsid.parse("")


def test_double_encoded_ssid_rejected():
    with pytest.raises(ProviderError, match="double-encoded"):
        PocketOptionSsid.parse('"42[\\"auth\\",{}]"')


def test_ssid_without_session_rejected():
    with pytest.raises(ProviderError, match="session"):
        PocketOptionSsid.parse('{"uid":1,"platform":2}')


def test_ssid_never_leaks_in_repr_or_str():
    ssid = PocketOptionSsid.parse(DEMO_SSID)
    assert "demo-session-token" not in repr(ssid)
    assert "demo-session-token" not in str(ssid)
    assert "REDACTED" in repr(ssid)
    assert "demo-session-token" not in ssid.summary()


# ------------------------------------------------------------------ symbols
def test_symbol_mapping():
    assert to_pocket_symbol("EUR/USD") == "EURUSD"
    assert to_pocket_symbol("EUR/USD OTC") == "EURUSD_otc"
    assert to_pocket_symbol("GBP/JPY") == "GBPJPY"


def test_stock_symbols_come_from_the_configured_overrides():
    # Stocks use a '#' prefix that cannot be derived from the display name.
    assert to_pocket_symbol("Cisco OTC") == "#CSCO_otc"
    assert to_pocket_symbol("Facebook OTC") == "#FB_otc"
    assert to_pocket_symbol("Alibaba OTC") == "BABA_otc"
    assert to_pocket_symbol("Johnson & Johnson OTC") == "#JNJ_otc"


def test_every_stock_asset_has_a_symbol():
    from config.assets import POCKET_OPTION_SYMBOLS, STOCK_ASSETS

    missing = [asset for asset in STOCK_ASSETS if asset not in POCKET_OPTION_SYMBOLS]
    assert missing == []


def test_stock_asset_slugs_are_filesystem_safe():
    from config.assets import STOCK_ASSETS, asset_slug

    for asset in STOCK_ASSETS:
        slug = asset_slug(asset)
        assert slug.isalnum(), f"{asset} -> {slug}"
    assert asset_slug("Johnson & Johnson OTC") == "JOHNSONANDJOHNSONOTC"


def test_otc_and_non_otc_are_different_symbols():
    assert to_pocket_symbol("EUR/USD") != to_pocket_symbol("EUR/USD OTC")


def test_region_selection():
    demo = PocketOptionSsid.parse(DEMO_SSID)
    real = PocketOptionSsid.parse(REAL_SSID)
    assert region_urls(demo) == [REGIONS["DEMO"]]
    assert region_urls(real)[0] == REGIONS["EUROPE"]
    assert region_urls(real, "US_SOUTH") == [REGIONS["US_SOUTH"]]
    with pytest.raises(ProviderError):
        region_urls(real, "MARS")


def test_all_endpoints_are_pocket_option_websockets():
    for url in REGIONS.values():
        assert url.startswith("wss://")
        assert ".po.market/socket.io/" in url


# --------------------------------------------------------------- timestamps
def test_normalize_timestamp_handles_milliseconds():
    assert normalize_timestamp(1_760_000_000) == 1_760_000_000
    assert normalize_timestamp(1_760_000_000_500) == 1_760_000_000.5


# -------------------------------------------------------------- handshake
async def test_engine_io_handshake_sends_socketio_connect():
    client, socket = make_client()
    await client._handle_text('0{"sid":"abc","pingInterval":25000}')
    assert socket.sent == ["40"]


async def test_socketio_session_triggers_ssid_auth():
    client, socket = make_client()
    await client._handle_text('40{"sid":"abc"}')
    assert socket.sent == [client.ssid.auth_message]


async def test_heartbeat_is_answered():
    client, socket = make_client()
    await client._handle_text("2")
    assert socket.sent == ["3"]


async def test_successauth_marks_authenticated():
    client, _ = make_client()
    await client._handle_text('42["successauth",{"isDemo":1}]')
    assert client._authenticated.is_set()
    assert client.status == "CONNECTED"


async def test_disconnect_packet_reports_auth_error():
    client, _ = make_client()
    await client._handle_text("41")
    assert client._authenticated.is_set()
    assert "rejected" in (client._auth_error or "")


async def test_subscribe_sends_change_symbol_and_subfor():
    client, socket = make_client()
    await client.subscribe("EURUSD_otc", "EUR/USD OTC", period=1)
    assert socket.sent == [
        '42["changeSymbol",{"asset": "EURUSD_otc", "period": 1}]',
        '42["subfor","EURUSD_otc"]',
    ]


# ------------------------------------------------------------------- ticks
async def test_update_stream_produces_pocket_option_ticks():
    client, _ = make_client()
    await client.subscribe("EURUSD_otc", "EUR/USD OTC")
    await client._handle_text(
        '42["updateStream",[["EURUSD_otc",1760000000.25,1.16543]]]'
    )
    tick = client.ticks.get_nowait()
    assert tick.asset == "EUR/USD OTC"
    assert tick.price == 1.16543
    assert tick.source == POCKET_OPTION_SOURCE
    assert tick.timestamp == datetime.fromtimestamp(1760000000.25, tz=timezone.utc)


async def test_duplicate_ticks_are_dropped():
    client, _ = make_client()
    frame = '42["updateStream",[["EURUSD",1760000000,1.16543]]]'
    await client._handle_text(frame)
    await client._handle_text(frame)
    assert client.ticks.qsize() == 1


async def test_malformed_stream_payload_is_ignored():
    client, _ = make_client()
    await client._handle_text('42["updateStream",[["EURUSD"]]]')
    await client._handle_text('42["updateStream","garbage"]')
    await client._handle_text("not json at all")
    assert client.ticks.empty()


async def test_binary_history_attachment_resolves_request(monkeypatch):
    client, _ = make_client()
    import asyncio

    future = asyncio.get_running_loop().create_future()
    client._history[("EURUSD", 60)] = future
    await client._handle_text('451-["updateHistoryNew",{"_placeholder":true,"num":0}]')
    payload = {"asset": "EURUSD", "period": 60, "history": [[1760000000, 1.1]]}
    await client._handle_binary(json.dumps(payload).encode())
    assert future.result() == payload


# ----------------------------------------------------------------- candles
def ticks_for(count: int, start: float = 1_760_000_000.0, step: float = 1.0):
    return [(start + i * step, 1.16000 + (i % 5) * 0.00001) for i in range(count)]


def test_15s_candles_use_strict_utc_boundaries():
    candles = candles_from_ticks(ticks_for(120), "EUR/USD", "15s")
    assert candles
    for candle in candles:
        assert candle.timestamp.second % 15 == 0
        assert candle.timestamp.microsecond == 0
        assert candle.source == POCKET_OPTION_SOURCE
        assert candle.timeframe == "15s"


def test_candle_ohlc_matches_tick_sequence():
    # 1_760_000_010 is exactly a 15-second boundary.
    ticks = [
        (1_760_000_010.0, 1.16000),
        (1_760_000_013.0, 1.16050),
        (1_760_000_017.0, 1.15980),
        (1_760_000_024.0, 1.16020),
        (1_760_000_026.0, 1.16030),  # next bucket -> closes the first one
    ]
    candles = candles_from_ticks(ticks, "EUR/USD", "15s")
    assert len(candles) == 1
    candle = candles[0]
    assert candle.open == 1.16000
    assert candle.high == 1.16050
    assert candle.low == 1.15980
    assert candle.close == 1.16020
    assert candle.volume == 4


def test_forming_candle_is_not_returned():
    ticks = [(1_760_000_000.0, 1.16), (1_760_000_005.0, 1.161)]
    assert candles_from_ticks(ticks, "EUR/USD", "15s") == []


def test_one_minute_and_five_minute_boundaries():
    ticks = ticks_for(700)
    minute = candles_from_ticks(ticks, "EUR/USD", "1m")
    five = candles_from_ticks(ticks, "EUR/USD", "5m")
    assert minute and five
    assert all(c.timestamp.second == 0 for c in minute)
    assert all(c.timestamp.second == 0 and c.timestamp.minute % 5 == 0 for c in five)


def test_server_candles_use_timestamp_open_close_high_low_order():
    payload = {
        "asset": "EURUSD",
        "period": 60,
        "candles": [[1_760_000_040, 0.92124, 0.92155, 0.92162, 0.92124]],
    }
    candles = candles_from_server(payload, "EUR/USD", "1m")
    assert len(candles) == 1
    candle = candles[0]
    assert candle.open == 0.92124
    assert candle.close == 0.92155
    assert candle.high == 0.92162
    assert candle.low == 0.92124
    assert candle.source == POCKET_OPTION_SOURCE


def test_server_history_ticks_are_aggregated_to_15s():
    payload = {
        "asset": "EURUSD",
        "period": 15,
        "history": [[ts, price] for ts, price in ticks_for(90)],
    }
    candles = candles_from_server(payload, "EUR/USD", "15s")
    assert candles
    assert all(c.timeframe == "15s" for c in candles)
    assert all(c.timestamp.second % 15 == 0 for c in candles)


def test_empty_payload_returns_no_candles():
    assert candles_from_server({"asset": "EURUSD", "period": 60}, "EUR/USD", "1m") == []


# ------------------------------------------------------------ asset payouts
def asset_row(symbol: str, name: str, payout: int, is_otc: int = 1, active: bool = True):
    # [id, symbol, name, type, _, payout, _, _, _, is_otc, _, _, _, _, is_active, candles]
    return [
        1,
        symbol,
        name,
        "stock",
        None,
        payout,
        None,
        None,
        None,
        is_otc,
        None,
        None,
        None,
        None,
        active,
        [{"time": 60}],
    ]


def test_parse_assets_reads_symbol_name_and_payout():
    assets = parse_assets(
        [
            asset_row("#CSCO_otc", "Cisco OTC", 92),
            asset_row("EURUSD", "EUR/USD", 80, is_otc=0, active=False),
            ["too", "short"],
            "garbage",
        ]
    )
    assert set(assets) == {"#CSCO_otc", "EURUSD"}

    cisco = assets["#CSCO_otc"]
    assert cisco.name == "Cisco OTC"
    assert cisco.payout == 92
    assert cisco.payout_label == "+92%"
    assert cisco.is_otc and cisco.is_active

    eurusd = assets["EURUSD"]
    assert not eurusd.is_otc
    assert not eurusd.is_active


async def test_update_assets_event_populates_the_catalogue():
    client, _ = make_client()
    payload = [asset_row("#CSCO_otc", "Cisco OTC", 92)]
    await client._handle_text(f'42["updateAssets",{json.dumps(payload)}]')
    assert client.assets["#CSCO_otc"].payout == 92


async def test_update_assets_binary_attachment_populates_the_catalogue():
    client, _ = make_client()
    payload = [asset_row("AMZN_otc", "Amazon OTC", 87)]
    await client._handle_binary(json.dumps(payload).encode())
    assert client.assets["AMZN_otc"].payout == 87
    assert client.ticks.empty()  # must not be mistaken for price ticks


# --------------------------------------------------- deep history paging
def test_history_period_request_is_matched_by_index():
    import asyncio

    async def scenario():
        client, socket = make_client()
        task = asyncio.create_task(
            client.request_history_period("EURUSD", 60, 1_760_000_000, offset=3600)
        )
        await asyncio.sleep(0)
        sent = json.loads(socket.sent[0][2:])
        assert sent[0] == "loadHistoryPeriod"
        request = sent[1]
        assert request["asset"] == "EURUSD"
        assert request["period"] == 60
        assert request["time"] == 1_760_000_000
        assert request["offset"] == 3600

        payload = {"asset": "EURUSD", "index": request["index"], "period": 60, "data": []}
        await client._handle_text(f'42["loadHistoryPeriod",{json.dumps(payload)}]')
        assert await task == payload

    asyncio.run(scenario())


def test_history_period_binary_attachment_is_matched_by_index():
    import asyncio

    async def scenario():
        client, socket = make_client()
        task = asyncio.create_task(client.request_history_period("EURUSD", 60, 1_760_000_000))
        await asyncio.sleep(0)
        index = json.loads(socket.sent[0][2:])[1]["index"]
        payload = {"asset": "EURUSD", "index": index, "period": 60, "data": []}
        await client._handle_binary(json.dumps(payload).encode())
        assert await task == payload

    asyncio.run(scenario())


def test_parse_history_period_reads_tick_items():
    payload = {
        "data": [
            {"asset": "EURUSD", "time": 1_760_000_000, "price": 1.16},
            {"asset": "EURUSD", "time": 1_760_000_001_000, "price": 1.161},
            {"asset": "EURUSD", "time": 1_760_000_002},
            "garbage",
        ]
    }
    ticks, ohlc = parse_history_period(payload)
    assert ohlc == []
    assert ticks == [(1_760_000_000.0, 1.16), (1_760_000_001.0, 1.161)]


def test_parse_history_period_reads_ohlc_items():
    payload = {
        "data": [
            {
                "symbol_id": 1,
                "time": 1_760_000_040,
                "open": 1.16,
                "close": 1.1605,
                "high": 1.1606,
                "low": 1.1599,
                "volume": 12,
            }
        ]
    }
    ticks, ohlc = parse_history_period(payload)
    assert ticks == []
    assert ohlc == [[1_760_000_040.0, 1.16, 1.1605, 1.1606, 1.1599]]


def test_historical_pages_keep_every_bucket():
    # Starts on a 15s boundary: 30 ticks fill exactly two buckets.
    ticks = ticks_for(30, 1_760_000_010.0)
    assert len(candles_from_ticks(ticks, "EUR/USD", "15s", drop_forming=False)) == 2
    assert len(candles_from_ticks(ticks, "EUR/USD", "15s")) == 1
