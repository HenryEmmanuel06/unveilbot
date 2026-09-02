"""Pocket Option market-data provider (the ONLY live data source).

Protocol implemented here was derived by reading the open-source
BinaryOptionsTools-v2 Pocket Option client
(https://github.com/ChipaDevTeam/BinaryOptionsTools-v2), specifically:

- ``crates/binary_options_tools/data/pocket_options_regions.json`` and
  ``src/pocketoption/connect.rs``          -> WebSocket endpoints / fallbacks
- ``src/pocketoption/utils.rs::try_connect`` -> handshake headers
  (Origin ``https://pocketoption.com``, browser User-Agent)
- ``src/pocketoption/ssid.rs``             -> SSID format ``42["auth",{...}]``
- ``src/pocketoption/modules/keep_alive.rs`` -> Engine.IO/Socket.IO handshake:
  ``0`` -> send ``40`` -> ``40{sid}`` -> send SSID -> ``successauth``,
  ``2`` -> ``3`` heartbeat, ``41`` -> session rejected
- ``src/pocketoption/modules/subscriptions.rs`` -> subscribe messages
  ``42["changeSymbol",{"asset":..,"period":..}]`` + ``42["subfor","<asset>"]``
  and the ``updateStream`` price event
- ``src/pocketoption/modules/historical_data.rs`` -> history request
  (``changeSymbol``) and ``updateHistory*`` responses
- ``src/pocketoption/candle.rs`` -> server array layouts
  ``[timestamp, open, close, high, low(, volume)]`` and tick ``[timestamp, price]``
- ``src/pocketoption/modules/trade.rs`` -> order placement
  ``42["openOrder",{asset, amount, action, isDemo, requestId, optionType, time}]``
  with ``successopenOrder`` / ``failopenOrder`` replies
- ``src/pocketoption/modules/balance.rs`` -> ``successupdateBalance`` /
  ``updateBalance`` carrying ``{"balance": .., "isDemo": ..}``

No endpoint, authentication message or event name in this module is invented:
each one appears in that reference implementation.

Order placement is OFF unless ``TAKE_TRADE=true``. When it is on, orders go to
whichever account the configured ``PO_SSID`` belongs to - demo or real - as
reported by :attr:`PocketOptionSsid.is_demo`. Every outgoing order frame is
logged in full so the protocol can be verified against the live socket.
The SSID itself is never logged.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from config.assets import pocket_option_symbol
from config.strategy import timeframe_seconds
from src.data.base import MarketDataProvider, ProviderError
from src.data.models import Candle, Tick
from src.utils.logging import get_logger
from src.utils.time import UTC, floor_to_timeframe

logger = get_logger(__name__)

#: Every candle produced from this provider carries this source tag.
POCKET_OPTION_SOURCE = "POCKET_OPTION"

AUTH_PREFIX = '42["auth",'

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
ORIGIN = "https://pocketoption.com"

REGIONS: dict[str, str] = {
    "RUSSIA_MOSCOW": "wss://api-msk.po.market/socket.io/?EIO=4&transport=websocket",
    "RUSSIA_SPB": "wss://api-spb.po.market/socket.io/?EIO=4&transport=websocket",
    "EUROPE": "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket",
    "US_SOUTH": "wss://api-us-south.po.market/socket.io/?EIO=4&transport=websocket",
    "ASIA": "wss://api-asia.po.market/socket.io/?EIO=4&transport=websocket",
    "DEMO": "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
}
REAL_REGION_ORDER = ("EUROPE", "US_SOUTH", "ASIA", "RUSSIA_MOSCOW", "RUSSIA_SPB")

HISTORY_EVENTS = (
    "updateHistoryNew",
    "updateHistoryNewFast",
    "updateHistory",
    "history",
)
#: Paginated deep history (see reference `modules/get_candles.rs`).
HISTORY_PERIOD_EVENTS = ("loadHistoryPeriod", "loadHistoryPeriodFast")
AUTH_REJECTED_EVENTS = ("NotAuthorized", "authError", "auth_error", "error")

#: Account balance events (reference `modules/balance.rs`).
BALANCE_EVENTS = ("successupdateBalance", "updateBalance")
#: Order placement events (reference `modules/trade.rs`).
ORDER_OPEN_EVENT = "openOrder"
ORDER_SUCCESS_EVENTS = ("successopenOrder",)
ORDER_FAIL_EVENTS = ("failopenOrder",)
#: Pocket Option option type for binary/turbo contracts.
OPTION_TYPE_BINARY = 100
#: Directions accepted by `openOrder`.
ORDER_ACTION_CALL = "call"
ORDER_ACTION_PUT = "put"

MS_THRESHOLD = 1_000_000_000_000.0
#: Seconds of history requested per `loadHistoryPeriod` page.
HISTORY_PAGE_SECONDS = 3600
#: Safety limit on pages walked backwards for one request.
MAX_HISTORY_PAGES = 20


class PocketOptionAuthError(ProviderError):
    """The Pocket Option session was rejected (expired/invalid SSID)."""


class PocketOptionOrderError(ProviderError):
    """Pocket Option refused an order, or never acknowledged it."""


@dataclass(frozen=True)
class OrderResult:
    """Outcome of one `openOrder` round trip."""

    accepted: bool
    request_id: int
    symbol: str
    amount: float
    action: str
    duration: int
    is_demo: bool
    order_id: str | None = None
    error: str | None = None
    payload: dict | None = None


def normalize_timestamp(raw: float) -> float:
    """Pocket Option sends seconds or milliseconds; normalize to seconds."""
    value = float(raw)
    return value / 1000.0 if value > MS_THRESHOLD else value


def to_pocket_symbol(asset: str) -> str:
    """``EUR/USD`` -> ``EURUSD`` ; ``EUR/USD OTC`` -> ``EURUSD_otc``.

    Instruments that do not follow that rule (stocks, indices) are taken from
    `config.assets.POCKET_OPTION_SYMBOLS`.
    """
    override = pocket_option_symbol(asset)
    if override:
        return override
    value = asset.strip()
    is_otc = value.upper().endswith("OTC")
    if is_otc:
        value = value[:-3].strip()
    core = value.replace("/", "").replace(" ", "").upper()
    return f"{core}_otc" if is_otc else core


@dataclass(frozen=True)
class AssetInfo:
    """One entry of Pocket Option's `updateAssets` list."""

    symbol: str
    name: str
    payout: int
    is_active: bool
    is_otc: bool
    asset_type: str

    @property
    def payout_label(self) -> str:
        return f"+{self.payout}%"


def parse_assets(payload: Any) -> dict[str, AssetInfo]:
    """Parse `updateAssets` rows.

    Row layout (reference ``types.rs::Asset``):
    ``[id, symbol, name, type, _, payout, _, _, _, is_otc, _, _, _, _, is_active, ...]``
    """
    assets: dict[str, AssetInfo] = {}
    if not isinstance(payload, list):
        return assets
    for row in payload:
        if not isinstance(row, list) or len(row) < 15:
            continue
        try:
            symbol = str(row[1])
            info = AssetInfo(
                symbol=symbol,
                name=str(row[2]),
                payout=int(row[5]),
                is_active=bool(row[14]),
                is_otc=row[9] == 1,
                asset_type=str(row[3]),
            )
        except (TypeError, ValueError, IndexError):
            continue
        assets[symbol] = info
    return assets


@dataclass(frozen=True)
class PocketOptionSsid:
    """Parsed Pocket Option session credential. Never rendered in plain text."""

    auth_message: str
    payload: dict

    @classmethod
    def parse(cls, raw: str) -> "PocketOptionSsid":
        value = (raw or "").strip()
        if not value:
            raise ProviderError(
                "PO_SSID is missing. Add PO_SSID=<your Pocket Option session> to your .env file."
            )
        if value.startswith('"') or value.startswith("'"):
            raise ProviderError("PO_SSID looks double-encoded: remove the surrounding quotes.")

        inner = value
        if value.startswith(AUTH_PREFIX):
            if not value.endswith("]"):
                raise ProviderError('PO_SSID is malformed: 42["auth",...] is missing the "]".')
            inner = value[len(AUTH_PREFIX) : -1]

        try:
            payload = json.loads(inner)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "PO_SSID could not be parsed. Paste the full 42[\"auth\",{...}] message "
                "captured from the Pocket Option WebSocket."
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError("PO_SSID payload must be a JSON object.")
        if not (payload.get("session") or payload.get("sessionToken")):
            raise ProviderError("PO_SSID payload has no 'session'/'sessionToken' field.")

        auth_message = value if value.startswith(AUTH_PREFIX) else f'42["auth",{inner}]'
        return cls(auth_message=auth_message, payload=payload)

    @property
    def is_demo(self) -> bool:
        if int(self.payload.get("isDemo", 0) or 0) == 1:
            return True
        current_url = str(self.payload.get("currentUrl") or "")
        return "demo" in current_url.lower()

    @property
    def uid(self) -> str:
        return str(self.payload.get("uid", "unknown"))

    @property
    def user_agent(self) -> str:
        return str(self.payload.get("userAgent") or DEFAULT_USER_AGENT)

    def summary(self) -> str:
        """Safe description: contains no session token."""
        return f"uid={self.uid} account={'DEMO' if self.is_demo else 'REAL'}"

    def __repr__(self) -> str:  # pragma: no cover - safety net for logs
        return f"PocketOptionSsid({self.summary()}, session=REDACTED)"

    __str__ = __repr__


def region_urls(ssid: PocketOptionSsid, region: str = "") -> list[str]:
    if region:
        url = REGIONS.get(region.upper())
        if url is None:
            raise ProviderError(
                f"Unknown PO_REGION {region!r}. Available: {', '.join(REGIONS)}"
            )
        return [url]
    if ssid.is_demo:
        return [REGIONS["DEMO"]]
    return [REGIONS[name] for name in REAL_REGION_ORDER]


class PocketOptionClient:
    """Authenticated Socket.IO client for Pocket Option market data."""

    def __init__(
        self,
        ssid: PocketOptionSsid,
        urls: list[str] | None = None,
        connect_timeout: float = 15.0,
        auth_timeout: float = 30.0,
        queue_size: int = 20_000,
    ) -> None:
        self.ssid = ssid
        self.urls = urls or region_urls(ssid)
        self.connect_timeout = connect_timeout
        self.auth_timeout = auth_timeout
        self.ticks: asyncio.Queue[Tick] = asyncio.Queue(maxsize=queue_size)
        self.status = "DISCONNECTED"
        self.connected_url: str | None = None
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._authenticated = asyncio.Event()
        self._auth_error: str | None = None
        self._closing = False
        self._history: dict[tuple[str, int], asyncio.Future] = {}
        self._history_period: dict[int, asyncio.Future] = {}
        self._request_index = 0
        self._subscriptions: dict[str, int] = {}
        self._symbol_to_asset: dict[str, str] = {}
        self._pending_binary_event: str | None = None
        self._last_tick: dict[str, tuple[float, float]] = {}
        self.ticks_received = 0
        #: symbol -> AssetInfo, refreshed from Pocket Option's `updateAssets`.
        self.assets: dict[str, AssetInfo] = {}
        #: Account balance as last reported by the broker, None until received.
        self.balance: float | None = None
        self._orders: dict[int, asyncio.Future] = {}
        self._order_index = 0
        #: Called with the new balance on every broker balance update.
        self.on_balance: Any = None

    # ------------------------------------------------------------ lifecycle
    @property
    def connected(self) -> bool:
        return self.status == "CONNECTED"

    async def connect(self) -> None:
        errors: list[str] = []
        for url in self.urls:
            try:
                await self._open(url)
            except PocketOptionAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - try the next region
                errors.append(f"{_host(url)}: {exc}")
                logger.warning("Pocket Option connection to %s failed: %s", _host(url), exc)
                await self._close_socket()
                continue
            return
        raise ProviderError("Could not connect to any Pocket Option server: " + "; ".join(errors))

    async def _open(self, url: str) -> None:
        self._closing = False
        self._authenticated.clear()
        self._auth_error = None
        logger.info("Connecting to Pocket Option (%s)", _host(url))
        self._ws = await ws_connect(
            url,
            additional_headers={
                "Origin": ORIGIN,
                "User-Agent": self.ssid.user_agent,
                "Cache-Control": "no-cache",
            },
            open_timeout=self.connect_timeout,
            close_timeout=5,
            ping_interval=None,  # Socket.IO has its own 2/3 heartbeat
            max_size=None,
        )
        self.connected_url = url
        self._reader = asyncio.create_task(self._read_loop(), name="po-reader")

        try:
            await asyncio.wait_for(self._authenticated.wait(), timeout=self.auth_timeout)
        except asyncio.TimeoutError as exc:
            if self._auth_error:
                raise PocketOptionAuthError(self._auth_error) from exc
            raise ProviderError(
                f"Pocket Option did not authenticate within {self.auth_timeout:.0f}s"
            ) from exc

        if self._auth_error:
            raise PocketOptionAuthError(self._auth_error)

        self.status = "CONNECTED"
        logger.info(
            "Pocket Option authenticated on %s (%s)", _host(url), self.ssid.summary()
        )

    async def disconnect(self) -> None:
        self._closing = True
        await self._close_socket()
        self.status = "DISCONNECTED"

    async def _close_socket(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        self.status = "DISCONNECTED"
        for future in (
            list(self._history.values())
            + list(self._history_period.values())
            + list(self._orders.values())
        ):
            if not future.done():
                future.set_exception(ProviderError("Pocket Option connection closed"))
        self._history.clear()
        self._history_period.clear()
        self._orders.clear()

    # ------------------------------------------------------------- messages
    async def _send(self, message: str) -> None:
        if self._ws is None:
            raise ProviderError("Pocket Option socket is not connected")
        await self._ws.send(message)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    if isinstance(raw, (bytes, bytearray)):
                        await self._handle_binary(bytes(raw))
                    else:
                        await self._handle_text(str(raw))
                except Exception as exc:  # noqa: BLE001 - malformed message guard
                    logger.warning("Discarded malformed Pocket Option message: %s", exc)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pocket Option reader stopped: %s", exc)
        finally:
            self.status = "DISCONNECTED"
            if not self._closing:
                logger.warning("Pocket Option connection lost - DATA_STATUS=DISCONNECTED")

    async def _handle_text(self, text: str) -> None:
        if not text:
            return

        # Engine.IO open packet -> Socket.IO connect
        if text[0] == "0":
            await self._send("40")
            return
        if text == "2":  # heartbeat ping
            await self._send("3")
            return
        if text.startswith("41"):
            self._auth_error = (
                "Pocket Option rejected the session (Socket.IO 41). "
                "The SSID is expired/invalid or was captured from another IP."
            )
            self._authenticated.set()
            return
        if text.startswith("40"):
            # Socket.IO session established -> authenticate with the SSID
            await self._send(self.ssid.auth_message)
            return

        start = text.find("[")
        if start == -1:
            return
        try:
            frame = json.loads(text[start:])
        except json.JSONDecodeError:
            return
        if not isinstance(frame, list) or not frame:
            return

        event = frame[0] if isinstance(frame[0], str) else None
        payload = frame[1] if len(frame) > 1 else None

        if isinstance(payload, dict) and payload.get("_placeholder"):
            # Socket.IO binary attachment follows this frame.
            self._pending_binary_event = event
            return

        await self._dispatch(event, payload)

    async def _handle_binary(self, data: bytes) -> None:
        event = self._pending_binary_event
        self._pending_binary_event = None
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        if isinstance(payload, dict) and "index" in payload and "data" in payload:
            # loadHistoryPeriod attachments are matched by their request index.
            self._resolve_history_period(payload)
            return
        if event is None:
            # successauth attachment / unlabelled payload: infer from shape.
            if isinstance(payload, dict) and "serverName" in payload:
                self._mark_authenticated()
                return
            if _looks_like_assets(payload):
                self._handle_assets(payload)
                return
            event = "updateHistoryNew" if isinstance(payload, dict) else "updateStream"
        await self._dispatch(event, payload)

    async def _dispatch(self, event: str | None, payload: Any) -> None:
        if event is None:
            return
        if event == "successauth":
            self._mark_authenticated()
            return
        if event in AUTH_REJECTED_EVENTS:
            self._auth_error = f"Pocket Option authentication rejected (event '{event}')."
            self._authenticated.set()
            return
        if event == "updateStream":
            self._handle_stream(payload)
            return
        if event == "updateAssets":
            self._handle_assets(payload)
            return
        if event in BALANCE_EVENTS:
            self._handle_balance(payload)
            return
        if event in ORDER_SUCCESS_EVENTS:
            self._resolve_order(payload, accepted=True)
            return
        if event in ORDER_FAIL_EVENTS:
            self._resolve_order(payload, accepted=False)
            return
        if event in HISTORY_PERIOD_EVENTS and isinstance(payload, dict):
            self._resolve_history_period(payload)
            return
        if event in HISTORY_EVENTS and isinstance(payload, dict):
            self._resolve_history(payload)

    def _handle_assets(self, payload: Any) -> None:
        assets = parse_assets(payload)
        if not assets:
            return
        self.assets = assets
        logger.info(
            "Pocket Option asset list updated (%s instruments, %s active)",
            len(assets),
            sum(1 for info in assets.values() if info.is_active),
        )

    def _mark_authenticated(self) -> None:
        if not self._authenticated.is_set():
            self._authenticated.set()
            self.status = "CONNECTED"

    def _handle_stream(self, payload: Any) -> None:
        items: list = []
        if isinstance(payload, list):
            items = payload if payload and isinstance(payload[0], list) else [payload]
        for item in items:
            if not isinstance(item, list) or len(item) < 3:
                continue
            symbol = item[0]
            if not isinstance(symbol, str):
                continue
            try:
                seconds = normalize_timestamp(float(item[1]))
                price = float(item[2])
            except (TypeError, ValueError):
                continue

            previous = self._last_tick.get(symbol)
            if previous == (seconds, price):  # duplicate tick protection
                continue
            self._last_tick[symbol] = (seconds, price)

            tick = Tick(
                asset=self._symbol_to_asset.get(symbol, symbol),
                timestamp=datetime.fromtimestamp(seconds, tz=UTC),
                price=price,
                source=POCKET_OPTION_SOURCE,
            )
            self.ticks_received += 1
            try:
                self.ticks.put_nowait(tick)
            except asyncio.QueueFull:
                logger.warning("Pocket Option tick queue full - dropping oldest tick")
                try:
                    self.ticks.get_nowait()
                    self.ticks.put_nowait(tick)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def _resolve_history(self, payload: dict) -> None:
        asset = payload.get("asset")
        if not isinstance(asset, str):
            return
        try:
            period = int(payload.get("period", 0))
        except (TypeError, ValueError):
            period = 0
        future = self._history.pop((asset, period), None)
        if future is None:
            for key in list(self._history):
                if key[0] == asset:
                    future = self._history.pop(key)
                    break
        if future is not None and not future.done():
            future.set_result(payload)

    def _resolve_history_period(self, payload: dict) -> None:
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            return
        future = self._history_period.pop(index, None)
        if future is not None and not future.done():
            future.set_result(payload)

    # -------------------------------------------------------------- balance
    def _handle_balance(self, payload: Any) -> None:
        """Apply a broker balance report (`successupdateBalance`/`updateBalance`)."""
        raw = payload
        if isinstance(payload, list) and payload:
            raw = payload[0]
        if not isinstance(raw, dict):
            return
        value = raw.get("balance", raw.get("amount"))
        if value is None:
            return
        try:
            balance = float(value)
        except (TypeError, ValueError):
            return

        previous = self.balance
        self.balance = balance
        if previous is None or abs(previous - balance) > 1e-9:
            logger.info(
                "Pocket Option balance %s (%s account)",
                f"{balance:.2f}",
                "DEMO" if self.ssid.is_demo else "REAL",
            )
        if self.on_balance is not None:
            try:
                self.on_balance(balance)
            except Exception as exc:  # noqa: BLE001 - a listener must never break the socket
                logger.warning("balance listener failed: %s", exc)

    # ---------------------------------------------------------------- orders
    def _resolve_order(self, payload: Any, accepted: bool) -> None:
        """Match a `successopenOrder`/`failopenOrder` reply to its request."""
        raw = payload
        if isinstance(payload, list) and payload:
            raw = payload[0]
        body = raw if isinstance(raw, dict) else {}

        request_id = body.get("requestId", body.get("request_id"))
        future: asyncio.Future | None = None
        if request_id is not None:
            try:
                future = self._orders.pop(int(request_id), None)
            except (TypeError, ValueError):
                future = None
        if future is None and len(self._orders) == 1:
            # Pocket Option does not always echo requestId back; with a single
            # order in flight the match is still unambiguous.
            _, future = self._orders.popitem()

        if future is None:
            logger.warning(
                "Unmatched order reply (accepted=%s): %s", accepted, _short(body)
            )
            return
        if not future.done():
            future.set_result((accepted, body))

    async def place_order(
        self,
        symbol: str,
        amount: float,
        action: str,
        duration: int,
        timeout: float = 15.0,
    ) -> OrderResult:
        """Send `openOrder` and wait for the broker's acknowledgement.

        `action` must be `ORDER_ACTION_CALL` or `ORDER_ACTION_PUT`; `duration`
        is the expiry in seconds. The account (demo or real) is decided by the
        configured SSID, never by this call.
        """
        if action not in (ORDER_ACTION_CALL, ORDER_ACTION_PUT):
            raise PocketOptionOrderError(
                f"action must be {ORDER_ACTION_CALL!r} or {ORDER_ACTION_PUT!r}, got {action!r}"
            )
        if amount <= 0:
            raise PocketOptionOrderError(f"order amount must be positive, got {amount}")
        if duration <= 0:
            raise PocketOptionOrderError(f"order duration must be positive, got {duration}")
        if not self.connected:
            raise PocketOptionOrderError("Pocket Option socket is not connected")

        self._order_index += 1
        request_id = self._order_index
        payload = {
            "asset": symbol,
            "amount": round(float(amount), 2),
            "action": action,
            "isDemo": 1 if self.ssid.is_demo else 0,
            "requestId": request_id,
            "optionType": OPTION_TYPE_BINARY,
            "time": int(duration),
        }
        frame = f'42["{ORDER_OPEN_EVENT}",' + json.dumps(payload) + "]"

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._orders[request_id] = future

        # Logged in full and deliberately: this is the only place the bot spends
        # money, and the frame must be verifiable against the live socket.
        logger.info("Pocket Option order -> %s", frame)
        try:
            await self._send(frame)
        except Exception as exc:
            self._orders.pop(request_id, None)
            raise PocketOptionOrderError(f"could not send order: {exc}") from exc

        try:
            accepted, body = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._orders.pop(request_id, None)
            raise PocketOptionOrderError(
                f"Pocket Option did not acknowledge the {symbol} order within {timeout:.0f}s"
            ) from exc

        order_id = body.get("id", body.get("orderId", body.get("ticket")))
        error = None if accepted else str(body.get("error") or body.get("message") or body)
        result = OrderResult(
            accepted=accepted,
            request_id=request_id,
            symbol=symbol,
            amount=payload["amount"],
            action=action,
            duration=int(duration),
            is_demo=self.ssid.is_demo,
            order_id=str(order_id) if order_id is not None else None,
            error=error,
            payload=body,
        )
        if accepted:
            logger.info(
                "Pocket Option accepted order %s: %s %s %.2f for %ss",
                result.order_id,
                symbol,
                action.upper(),
                result.amount,
                duration,
            )
        else:
            logger.error("Pocket Option rejected %s order: %s", symbol, error)
        return result

    # ---------------------------------------------------------------- public
    async def subscribe(self, symbol: str, asset: str, period: int = 1) -> None:
        """Subscribe to a Pocket Option symbol's live price stream."""
        self._symbol_to_asset[symbol] = asset
        self._subscriptions[symbol] = period
        await self._send(
            '42["changeSymbol",' + json.dumps({"asset": symbol, "period": period}) + "]"
        )
        await self._send(f'42["subfor","{symbol}"]')
        logger.info("Subscribed to Pocket Option symbol %s (period %ss)", symbol, period)

    async def resubscribe_all(self) -> None:
        for symbol, period in list(self._subscriptions.items()):
            await self.subscribe(symbol, self._symbol_to_asset.get(symbol, symbol), period)

    async def request_history(self, symbol: str, period: int, timeout: float = 30.0) -> dict:
        """Ask Pocket Option for its own history of a symbol/period."""
        key = (symbol, int(period))
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._history[key] = future
        await self._send(
            '42["changeSymbol",' + json.dumps({"asset": symbol, "period": int(period)}) + "]"
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._history.pop(key, None)
            raise ProviderError(
                f"Pocket Option did not return history for {symbol} (period {period}s)"
            ) from exc

    async def request_history_period(
        self,
        symbol: str,
        period: int,
        end_time: int,
        offset: int = HISTORY_PAGE_SECONDS,
        timeout: float = 20.0,
    ) -> dict:
        """One page of deep history ending at `end_time` (`loadHistoryPeriod`)."""
        self._request_index += 1
        index = self._request_index
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._history_period[index] = future
        payload = {
            "asset": symbol,
            "period": int(period),
            "time": int(end_time),
            "index": index,
            "offset": int(offset),
        }
        await self._send('42["loadHistoryPeriod",' + json.dumps(payload) + "]")
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._history_period.pop(index, None)
            raise ProviderError(
                f"Pocket Option did not return deep history for {symbol} "
                f"(period {period}s, time {end_time})"
            ) from exc


def candles_from_server(
    payload: dict, asset: str, timeframe: str
) -> list[Candle]:
    """Convert a Pocket Option history payload into validated candles.

    Server layouts (see reference ``candle.rs``):
    - ``candles``: ``[timestamp, open, close, high, low(, volume)]``
    - ``history``: ``[timestamp, price]`` ticks -> aggregated locally
    """
    seconds = timeframe_seconds(timeframe)
    raw_candles = payload.get("candles")
    if isinstance(raw_candles, list) and raw_candles:
        candles: list[Candle] = []
        for row in raw_candles:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                timestamp = datetime.fromtimestamp(normalize_timestamp(row[0]), tz=UTC)
                open_, close, high, low = (
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                )
            except (TypeError, ValueError):
                continue
            candles.append(
                Candle(
                    asset=asset,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=open_,
                    high=max(high, open_, close),
                    low=min(low, open_, close),
                    close=close,
                    volume=None,
                    source=POCKET_OPTION_SOURCE,
                )
            )
        server_period = int(payload.get("period") or 0)
        if candles and server_period == seconds:
            candles.sort(key=lambda c: c.timestamp)
            return candles
        # Period mismatch: rebuild from candle closes so the timeframe is exact.
        ticks = [(c.timestamp.timestamp(), c.close) for c in candles]
        return candles_from_ticks(ticks, asset, timeframe)

    history = payload.get("history")
    if isinstance(history, list) and history:
        ticks: list[tuple[float, float]] = []
        for row in history:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                ticks.append((normalize_timestamp(row[0]), float(row[1])))
            except (TypeError, ValueError):
                continue
        return candles_from_ticks(ticks, asset, timeframe)

    return []


def parse_history_period(payload: dict) -> tuple[list[tuple[float, float]], list[list[float]]]:
    """Split a `loadHistoryPeriod` page into ticks and OHLC rows.

    Items are either ``{"time": t, "price": p}`` or
    ``{"time": t, "open": o, "close": c, "high": h, "low": l}``.
    """
    ticks: list[tuple[float, float]] = []
    ohlc: list[list[float]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or "time" not in item:
            continue
        try:
            timestamp = normalize_timestamp(item["time"])
        except (TypeError, ValueError):
            continue
        if all(item.get(key) is not None for key in ("open", "high", "low", "close")):
            try:
                ohlc.append(
                    [
                        timestamp,
                        float(item["open"]),
                        float(item["close"]),
                        float(item["high"]),
                        float(item["low"]),
                    ]
                )
                continue
            except (TypeError, ValueError):
                continue
        price = item.get("close", item.get("price"))
        if price is None:
            continue
        try:
            ticks.append((timestamp, float(price)))
        except (TypeError, ValueError):
            continue
    return ticks, ohlc


def candles_from_ticks(
    ticks: list[tuple[float, float]],
    asset: str,
    timeframe: str,
    drop_forming: bool = True,
) -> list[Candle]:
    """Aggregate Pocket Option ticks into strict UTC-aligned OHLC candles.

    `drop_forming` removes the newest bucket, which is still being built. It
    must be disabled for historical pages, where every bucket is complete and
    dropping one would punch a hole in the series.
    """
    seconds = timeframe_seconds(timeframe)
    buckets: dict[datetime, list[float]] = {}
    for raw_ts, price in sorted(ticks, key=lambda item: item[0]):
        bucket = floor_to_timeframe(datetime.fromtimestamp(raw_ts, tz=UTC), seconds)
        buckets.setdefault(bucket, []).append(price)

    candles: list[Candle] = []
    for bucket in sorted(buckets):
        prices = buckets[bucket]
        candles.append(
            Candle(
                asset=asset,
                timeframe=timeframe,
                timestamp=bucket,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=float(len(prices)),
                source=POCKET_OPTION_SOURCE,
            )
        )
    # The final bucket may still be forming; only closed candles are usable.
    if candles and drop_forming:
        newest_tick = max(ticks, key=lambda item: item[0])[0]
        last = candles[-1]
        if newest_tick < (last.timestamp + timedelta(seconds=seconds)).timestamp():
            candles.pop()
    return candles


class PocketOptionDataProvider(MarketDataProvider):
    """Live market data straight from Pocket Option. Never trades."""

    name = "pocketoption"
    source = POCKET_OPTION_SOURCE
    #: Candles are always built from Pocket Option's own price stream.
    native_timeframes = ()
    supports_streaming = True

    def __init__(self, ssid: str, region: str = "") -> None:
        self.ssid = PocketOptionSsid.parse(ssid)
        self.region = region
        self.client = PocketOptionClient(self.ssid, urls=region_urls(self.ssid, region))
        self.assets: list[str] = []
        #: display name -> verified Pocket Option symbol
        self.symbols: dict[str, str] = {}

    @property
    def data_status(self) -> str:
        return self.client.status

    async def connect(self) -> None:
        await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def subscribe(self, assets: list[str]) -> None:
        """Subscribe to each asset, skipping symbols Pocket Option does not list."""
        await self._await_assets()
        self.assets = []
        for asset in assets:
            symbol = self._resolve_symbol(asset)
            if symbol is None:
                continue
            self.symbols[asset] = symbol
            self.assets.append(asset)
            await self.client.subscribe(symbol, asset, period=1)
        if not self.assets:
            raise ProviderError("No configured asset could be matched on Pocket Option")

    async def _await_assets(self, timeout: float = 10.0) -> None:
        """Pocket Option sends `updateAssets` shortly after authentication."""
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.client.assets and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
        if not self.client.assets:
            logger.warning(
                "Pocket Option asset list not received: payouts unavailable and "
                "symbols cannot be verified"
            )

    def _resolve_symbol(self, asset: str) -> str | None:
        """Configured symbol, verified against Pocket Option's asset list."""
        symbol = to_pocket_symbol(asset)
        catalogue = self.client.assets
        if not catalogue:
            return symbol
        info = catalogue.get(symbol)
        if info is None:
            match = next(
                (
                    candidate
                    for candidate in catalogue.values()
                    if candidate.name.strip().lower() == asset.strip().lower()
                ),
                None,
            )
            if match is None:
                logger.error(
                    "Skipping %s: symbol %r is not in Pocket Option's asset list. "
                    "Fix POCKET_OPTION_SYMBOLS in config/assets.py",
                    asset,
                    symbol,
                )
                return None
            logger.warning(
                "Symbol %r not found for %s; using %r matched by name",
                symbol,
                asset,
                match.symbol,
            )
            info = match
            symbol = match.symbol
        if not info.is_active:
            logger.warning("%s (%s) is currently closed on Pocket Option", asset, symbol)
        else:
            logger.info("%s -> %s (payout %s)", asset, symbol, info.payout_label)
        return symbol

    def _symbol(self, asset: str) -> str:
        return self.symbols.get(asset) or to_pocket_symbol(asset)

    def asset_info(self, asset: str) -> AssetInfo | None:
        """Live Pocket Option information (payout, active) for `asset`."""
        return self.client.assets.get(self._symbol(asset))

    def payout(self, asset: str) -> int | None:
        """Current Pocket Option payout percentage, e.g. 92."""
        info = self.asset_info(asset)
        return info.payout if info else None

    # ---------------------------------------------------------------- trading
    @property
    def balance(self) -> float | None:
        """Account balance last reported by Pocket Option, None until known."""
        return self.client.balance

    @property
    def is_demo(self) -> bool:
        """Whether the configured PO_SSID belongs to a demo account."""
        return self.ssid.is_demo

    def set_balance_listener(self, listener) -> None:
        """Register a callback invoked on every broker balance update."""
        self.client.on_balance = listener

    async def place_order(
        self, asset: str, amount: float, action: str, duration: int
    ) -> OrderResult:
        """Place one binary order on the account behind the configured SSID."""
        return await self.client.place_order(
            symbol=self._symbol(asset),
            amount=amount,
            action=action,
            duration=duration,
        )

    async def resubscribe(self) -> None:
        await self.client.resubscribe_all()

    async def get_ticks(self, asset: str, period: int = 60) -> list[Tick]:
        """Recent raw Pocket Option ticks for an asset."""
        symbol = self._symbol(asset)
        payload = await self.client.request_history(symbol, period)
        history = payload.get("history")
        ticks: list[Tick] = []
        if isinstance(history, list):
            for row in history:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                try:
                    seconds = normalize_timestamp(row[0])
                    price = float(row[1])
                except (TypeError, ValueError):
                    continue
                ticks.append(
                    Tick(
                        asset=asset,
                        timestamp=datetime.fromtimestamp(seconds, tz=UTC),
                        price=price,
                        source=POCKET_OPTION_SOURCE,
                    )
                )
        return ticks

    async def get_candles(self, asset: str, timeframe: str, limit: int) -> list[Candle]:
        """Pocket Option history for `asset`, deepened with `loadHistoryPeriod`.

        `changeSymbol` only returns a short window, so older pages are walked
        backwards until `limit` candles are available.
        """
        symbol = self._symbol(asset)
        seconds = timeframe_seconds(timeframe)

        payload = await self.client.request_history(symbol, seconds)
        by_timestamp: dict[datetime, Candle] = {
            candle.timestamp: candle
            for candle in candles_from_server(payload, asset, timeframe)
        }

        oldest = min(by_timestamp) if by_timestamp else datetime.now(tz=UTC)
        end_time = int(oldest.timestamp())
        page = max(HISTORY_PAGE_SECONDS, seconds * limit // MAX_HISTORY_PAGES + seconds)
        for _ in range(MAX_HISTORY_PAGES):
            if len(by_timestamp) >= limit:
                break
            try:
                deep = await self.client.request_history_period(
                    symbol, seconds, end_time, offset=page
                )
            except ProviderError as exc:
                logger.warning("Deep history stopped for %s %s: %s", asset, timeframe, exc)
                break

            ticks, ohlc = parse_history_period(deep)
            if ohlc:
                new_candles = candles_from_server(
                    {"asset": symbol, "period": seconds, "candles": ohlc}, asset, timeframe
                )
            else:
                new_candles = candles_from_ticks(
                    ticks, asset, timeframe, drop_forming=False
                )
            fresh = [c for c in new_candles if c.timestamp not in by_timestamp]
            for candle in new_candles:
                by_timestamp.setdefault(candle.timestamp, candle)

            if not fresh:
                break
            end_time = int(min(by_timestamp).timestamp())

        candles = [by_timestamp[key] for key in sorted(by_timestamp)]
        if not candles:
            raise ProviderError(
                f"Pocket Option returned no usable {timeframe} history for {asset}"
            )
        return candles[-limit:]

    async def stream_prices(self) -> AsyncIterator[Tick]:
        if not self.client.connected:
            raise ProviderError("Pocket Option provider is not connected")
        while True:
            if not self.client.connected:
                raise ProviderError("Pocket Option connection lost (DATA_STATUS=DISCONNECTED)")
            try:
                yield await asyncio.wait_for(self.client.ticks.get(), timeout=30)
            except asyncio.TimeoutError as exc:
                raise ProviderError(
                    "No Pocket Option price updates received for 30s"
                ) from exc


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]


def _short(payload: Any, limit: int = 300) -> str:
    """Compact representation for log lines."""
    text = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
    return text if len(text) <= limit else text[:limit] + "..."


def _looks_like_assets(payload: Any) -> bool:
    """True for an `updateAssets` body: a list of long rows with a symbol."""
    return (
        isinstance(payload, list)
        and bool(payload)
        and isinstance(payload[0], list)
        and len(payload[0]) >= 15
        and isinstance(payload[0][1], str)
    )
