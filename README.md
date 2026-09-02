# Pocket Option W/M Strategy Telegram Signal Bot

## 1. Project Overview

Build a Python application that monitors financial-market price data, detects a specific W/M pullback trading strategy, tracks each setup through multiple stages, and sends structured trading signals to Telegram.

The application is a:

> **MARKET ANALYSIS + SIGNAL GENERATION BOT**

It must **NOT automatically execute trades** on Pocket Option.

The system must analyze the market and send three Telegram signal phases:

### Phase 1 — Pattern Formed

Example:

```text
🟢 W PATTERN FORMED

EUR/USD
5M Timeframe

W Pattern Formed

Waiting for pattern confirmation...
```

### Phase 2 — Pullback Formed

Example:

```text
🟡 W PULLBACK FORMED

EUR/USD
5M Timeframe

W Confirmed ✓
Pullback: 2 Red Candles

Reference High: 1.16580

Waiting for entry confirmation...
```

### Phase 3 — Execution Signal

Example:

```text
🟢 CALL / UP EXECUTION

EUR/USD
5M Timeframe

W Pattern ✓
Pullback ✓
Entry Confirmation ✓

Expiration: 10 Minutes

CALL / UP
```

The bot must follow the strategy rules in this README exactly.

Do not replace the strategy with RSI, MACD, moving averages, generic candlestick patterns, machine learning, or another trading strategy unless explicitly instructed.

---

# 2. Primary Objective

The finished system should be capable of doing this:

```text
LIVE MARKET DATA
       ↓
15S / 1M / 5M CANDLES
       ↓
SWING DETECTION
       ↓
W/M PATTERN DETECTION
       ↓
PHASE 1 TELEGRAM
       ↓
PATTERN CONFIRMATION
       ↓
P/T BREAK
       ↓
PULLBACK DETECTION
       ↓
PHASE 2 TELEGRAM
       ↓
ENTRY CONFIRMATION
       ↓
PHASE 3 TELEGRAM
       ↓
RECORD SIGNAL
       ↓
TRACK OUTCOME
```

---

# 3. Trading Timeframes

The strategy operates on three primary analysis timeframes.

| Analysis Timeframe | Expiration |
|---|---:|
| 15 seconds | 1 minute |
| 1 minute | 3 minutes |
| 5 minutes | 10 minutes |

These expiration values are informational only.

The bot does not place trades.

The expiration must be included in the Phase 3 Telegram message.

---

# 4. Initial Assets

Initially support:

```text
EUR/USD
GBP/USD
USD/JPY
AUD/USD
USD/CAD
USD/CHF
EUR/GBP
EUR/JPY
GBP/JPY
```

The asset list must be configurable.

Do not hard-code the list throughout the application.

Example:

```python
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
```

---

# 5. OTC Assets

OTC instruments must be treated as separate instruments.

For example:

```text
EUR/USD
```

and:

```text
EUR/USD OTC
```

must NOT be treated as the same asset.

Do not mix their:

- candles
- patterns
- statistics
- backtests
- signal results

The asset identifier should preserve the OTC designation.

Example:

```python
"EUR/USD"
"EUR/USD OTC"
```

The official Pocket Option assets page currently distinguishes available instruments and displays their payouts/availability dynamically.

The bot may later include an asset-availability/payout service, but this is separate from the market candle-data provider.

---

# 6. MARKET DATA — CRITICAL REQUIREMENT

The strategy depends on accurate:

- 15-second candles
- 1-minute candles
- 5-minute candles

The market-data architecture must therefore be provider-independent.

Do NOT hard-code an undocumented Pocket Option API endpoint.

Do NOT invent WebSocket URLs.

Do NOT assume that a normal EUR/USD forex feed exactly matches the Pocket Option chart.

Pocket Option publishes general information about API/integration and real-time market data, but this project must verify the exact available live data interface before implementing it.

Create a provider abstraction:

```text
MarketDataProvider
        ↓
Live Provider
        ↓
Candle Builder
        ↓
Strategy Engine
```

Example interface:

```python
class MarketDataProvider:

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def subscribe(self, assets):
        pass

    async def get_historical_candles(
        self,
        asset,
        timeframe,
        limit
    ):
        pass

    async def stream_prices(self):
        pass
```

The strategy engine must never directly depend on the implementation of the provider.

---

# 7. Live Data Provider Requirements

Before implementing a live provider, verify:

1. Whether an authorized Pocket Option market-data interface is available.
2. Authentication requirements.
3. Supported instruments.
4. Whether OTC instruments are supported.
5. Price update frequency.
6. Timestamp format.
7. Historical candle availability.
8. 15-second data availability.
9. 1-minute data availability.
10. 5-minute data availability.
11. Connection/reconnection behavior.
12. Whether the feed can provide data sufficiently close to the Pocket Option chart.

If an official/authorized direct Pocket Option feed cannot be used, create a separate provider adapter for the selected alternative data source.

The strategy engine must remain unchanged.

---

# 8. Data Accuracy Requirement

Because this strategy uses short-duration trades, especially 15-second setups, timestamp and price accuracy are extremely important.

The system must record:

```text
asset
timestamp
open
high
low
close
source
timeframe
```

The system should also record the data source used for every signal.

Example:

```text
Data Source: provider_name
```

This makes it possible to compare the bot's candles against the Pocket Option chart.

---

# 9. Candle Model

Use a standard OHLC candle model.

```python
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    asset: str
    timeframe: str
```

Candle colors:

### Green/Bullish

```python
close > open
```

### Red/Bearish

```python
close < open
```

### Doji

```python
close == open
```

Default:

```text
DOJI = NEITHER GREEN NOR RED
```

Do not automatically classify a doji as bullish or bearish.

---

# 10. Closed Candle Requirement

Strategy decisions should be based on completed candles unless a specific rule explicitly requires intrabar monitoring.

Do not use an unfinished candle to confirm:

- L2
- H2
- P breakout
- T breakout
- pullback completion
- execution

The execution condition should be evaluated using the completed confirmation candle.

---

# 11. W PATTERN

A W is a double-bottom structure.

Basic structure:

```text
L1 → P → L2
```

Then:

```text
L2 → bullish reversal → BREAK P
```

Full sequence:

```text
L1
 ↓
P
 ↓
≥50% retracement
 ↓
L2
 ↓
bullish reversal
 ↓
BREAK ABOVE P
 ↓
W CONFIRMED
```

---

# 12. W — L1

L1 is the first meaningful swing low.

After L1:

- price must move upward
- a meaningful middle peak P must form

The swing detector must prevent tiny random fluctuations from becoming L1.

The swing-detection parameters must be configurable.

---

# 13. W — Middle Peak P

P is the meaningful swing high between L1 and L2.

P must have enough height relative to L1/L2 to prevent random noise from being classified as a W.

The minimum pattern depth must be configurable.

Do not choose an arbitrary permanent value.

Example configuration:

```python
MIN_W_PEAK_DEPTH = configurable_value
```

This value should later be optimized through backtesting.

---

# 14. W — L2

After P forms, price must move downward toward L1.

L2 is the second bottom.

L2 must satisfy all of the following:

1. It occurs after P.
2. Price moves toward L1.
3. The movement retraces at least 50% of the L1 → P movement.
4. Price does NOT break below L1.
5. L2 is reasonably close to L1.

---

# 15. W — 50% Retracement

Calculate:

```python
retracement_50 = L1 + ((P - L1) * 0.50)
```

Valid W requirement:

```python
L2 <= retracement_50
```

and:

```python
L2 > L1
```

Example:

```text
L1 = 100
P  = 120

50% retracement = 110
```

Price must reach:

```text
110 or lower
```

but must remain above:

```text
100
```

Therefore:

```text
100 < L2 <= 110
```

---

# 16. W — L1 Protection

If price breaks below L1 before W confirmation:

```text
W = INVALID
```

Example:

```text
L1
 └────── price breaks below L1
              ↓
          INVALID
```

Do not continue tracking that setup.

---

# 17. W — L2 Tolerance

L2 must be reasonably close to L1.

Use configurable percentage tolerance.

Example:

```python
abs(L2 - L1) / L1 <= W_BOTTOM_TOLERANCE
```

Do not permanently hard-code the tolerance.

Example configuration:

```python
W_BOTTOM_TOLERANCE = 0.002
```

This is an initial placeholder only.

It must be tested and optimized.

---

# 18. W — Pattern Confirmation

The W is NOT confirmed simply because L2 starts moving upward.

The bullish reversal must break P.

Therefore:

```text
L1
 ↓
P
 ↓
≥50% retracement
 ↓
L2
 ↓
bullish reversal
 ↓
BREAK ABOVE P
 ↓
W CONFIRMED
```

The candle that breaks P confirms the W.

Once P is broken, transition to:

```text
W_CONFIRMED
```

---

# 19. M PATTERN

The M is the exact inverse of the W.

Structure:

```text
H1 → T → H2
```

Then:

```text
H2 → bearish reversal → BREAK T
```

Full sequence:

```text
H1
 ↓
T
 ↓
≥50% retracement
 ↓
H2
 ↓
bearish reversal
 ↓
BREAK BELOW T
 ↓
M CONFIRMED
```

---

# 20. M — H1

H1 is the first meaningful swing high.

After H1:

- price moves downward
- meaningful middle trough T forms

---

# 21. M — Middle Trough T

T is the meaningful swing low between H1 and H2.

It must have sufficient depth to prevent ordinary market noise from being classified as an M.

Make this configurable.

---

# 22. M — H2

After T forms, price rises toward H1.

H2 must satisfy:

1. It occurs after T.
2. Price moves toward H1.
3. The movement retraces at least 50% of the H1 → T movement.
4. Price does NOT break above H1.
5. H2 is reasonably close to H1.

---

# 23. M — 50% Retracement

Calculate:

```python
retracement_50 = H1 - ((H1 - T) * 0.50)
```

Valid M requirement:

```python
H2 >= retracement_50
```

and:

```python
H2 < H1
```

Example:

```text
H1 = 120
T  = 100

50% retracement = 110
```

Price must reach:

```text
110 or higher
```

but remain below:

```text
120
```

Therefore:

```text
110 <= H2 < 120
```

---

# 24. M — H1 Protection

If price breaks above H1 before M confirmation:

```text
M = INVALID
```

Do not continue tracking that setup.

---

# 25. M — H2 Tolerance

Use configurable tolerance:

```python
abs(H2 - H1) / H1 <= M_TOP_TOLERANCE
```

The final tolerance must be determined through testing.

---

# 26. M — Pattern Confirmation

The M is confirmed only when the bearish reversal breaks T.

```text
H1
 ↓
T
 ↓
≥50% retracement
 ↓
H2
 ↓
bearish reversal
 ↓
BREAK BELOW T
 ↓
M CONFIRMED
```

---

# 27. PHASE 1 — PATTERN SIGNAL

The first Telegram signal should communicate that a W/M setup has formed.

This is NOT an execution signal.

The message should clearly say:

```text
WAITING FOR CONFIRMATION
```

Example:

```text
🟢 W PATTERN FORMED

Asset: EUR/USD
Timeframe: 5M

Pattern: W / Double Bottom

L1: 1.16000
P:  1.16500
L2: 1.16150

50% Retracement: ✓
L1 Protection: ✓
L2 Tolerance: ✓

Status:
WAITING FOR P BREAKOUT

Setup ID:
EURUSD-5M-W-20260825-XXXX
```

M:

```text
🔴 M PATTERN FORMED

Asset: EUR/USD
Timeframe: 5M

Pattern: M / Double Top

H1: 1.16500
T:  1.16000
H2: 1.16400

50% Retracement: ✓
H1 Protection: ✓
H2 Tolerance: ✓

Status:
WAITING FOR T BREAKOUT

Setup ID:
EURUSD-5M-M-20260825-XXXX
```

---

# 28. Important Phase 1 Behavior

Phase 1 is an early setup notification.

It does NOT mean:

```text
CALL
```

or:

```text
PUT
```

It means:

> A valid W/M structure has been detected and the bot is monitoring it.

The bot must not tell the user to execute a trade at Phase 1.

---

# 29. W PULLBACK

After P breaks:

```text
W CONFIRMED
```

The bot waits for the first red/bearish candle.

That first red candle starts the pullback.

Record:

```python
pullback_candle_1
```

And specifically:

```python
pullback_reference_high = pullback_candle_1.high
```

This high becomes the entry reference.

---

# 30. W Pullback Candle Count

After the first red candle:

```text
RED #1
```

then:

```text
RED #2
```

then optionally:

```text
RED #3
```

Maximum:

```text
3 consecutive red candles
```

If:

```text
RED #4
```

appears before the green confirmation:

```text
SETUP INVALID
```

Do not continue.

---

# 31. W Pullback Minimum

The strategy requires approximately 2–3 pullback candles.

For implementation:

```text
Minimum = 2
Maximum = 3
```

Therefore:

```text
1 red candle
→ pullback has started but is not yet valid

2 red candles
→ valid pullback

3 red candles
→ valid pullback

4 red candles
→ INVALID
```

---

# 32. W Pullback Telegram Signal

The Phase 2 message should be sent when the pullback becomes valid.

That means after the second red candle.

Example:

```text
🟡 W PULLBACK FORMED

Asset: EUR/USD
Timeframe: 5M

Pattern: W / Double Bottom
Direction: CALL / UP

P Breakout: ✓

Pullback:
🔴 Red Candle #1
🔴 Red Candle #2

Maximum: 3 candles

Entry Reference:
Pullback Candle #1 High:
1.16580

Status:
WAITING FOR FIRST GREEN CANDLE
TO REACH OR BREAK THE REFERENCE HIGH

Expiration:
10 Minutes

Setup ID:
EURUSD-5M-W-20260825-XXXX
```

If a third red candle forms, update the internal state.

Do not send another Phase 2 message for the same setup unless the application is explicitly configured to send updates.

Default:

> Only one Phase 2 Telegram message per setup.

---

# 33. W Entry Confirmation

After the pullback:

```text
RED #1
RED #2
or
RED #3
```

the next candle must be green.

That first green candle is the ONLY execution-confirmation candle.

It must reach or break the high of the FIRST red pullback candle.

Condition:

```python
green_candle.high >= pullback_candle_1.high
```

If true:

```text
CALL / UP
```

If false:

```text
INVALID
```

---

# 34. W Important Invalidation Rule

Example:

```text
RED #1
RED #2
RED #3
GREEN
```

If:

```text
GREEN HIGH < RED #1 HIGH
```

then:

```text
SETUP INVALID
```

Do NOT:

- wait for another green candle
- reset the pullback
- create a new reference
- use a later candle
- generate a later entry

The setup is permanently invalidated.

---

# 35. W Execution Signal

If:

```python
green_candle.high >= pullback_candle_1.high
```

send:

```text
🟢 CALL / UP EXECUTION SIGNAL

Asset: EUR/USD
Timeframe: 5M

Pattern: W / Double Bottom

W Confirmation: ✓
Pullback: ✓
Entry Candle: ✓

Reference High:
1.16580

Entry Candle High:
1.16585

➡️ Direction: CALL / UP

⏱ Expiration: 10 Minutes

Setup ID:
EURUSD-5M-W-20260825-XXXX

⚠️ Strategy signal only.
```

---

# 36. M PULLBACK

After T breaks:

```text
M CONFIRMED
```

wait for the first green/bullish candle.

That first green candle starts the pullback.

Record:

```python
pullback_candle_1
```

and:

```python
pullback_reference_low = pullback_candle_1.low
```

---

# 37. M Pullback Candle Count

Valid:

```text
GREEN #1
GREEN #2
GREEN #3
```

Maximum:

```text
3
```

If:

```text
GREEN #4
```

then:

```text
SETUP INVALID
```

---

# 38. M Pullback Minimum

Minimum:

```text
2 green candles
```

Maximum:

```text
3 green candles
```

Therefore:

```text
1 green
→ pullback forming

2 green
→ valid pullback

3 green
→ valid pullback

4 green
→ INVALID
```

---

# 39. M Pullback Telegram Signal

After two green candles:

```text
🟡 M PULLBACK FORMED

Asset: EUR/USD
Timeframe: 5M

Pattern: M / Double Top
Direction: PUT / DOWN

T Breakout: ✓

Pullback:
🟢 Green Candle #1
🟢 Green Candle #2

Maximum: 3 candles

Entry Reference:
Pullback Candle #1 Low:
1.16420

Status:
WAITING FOR FIRST RED CANDLE
TO REACH OR BREAK THE REFERENCE LOW

Expiration:
10 Minutes

Setup ID:
EURUSD-5M-M-20260825-XXXX
```

---

# 40. M Entry Confirmation

The first red candle after the green pullback is the ONLY execution-confirmation candle.

It must reach or break the low of the first green pullback candle.

Condition:

```python
red_candle.low <= pullback_candle_1.low
```

If true:

```text
PUT / DOWN
```

If false:

```text
INVALID
```

---

# 41. M Important Invalidation Rule

Example:

```text
GREEN #1
GREEN #2
GREEN #3
RED
```

If:

```text
RED LOW > GREEN #1 LOW
```

then:

```text
SETUP INVALID
```

Do not wait for another red candle.

---

# 42. M Execution Signal

If valid:

```text
🔴 PUT / DOWN EXECUTION SIGNAL

Asset: EUR/USD
Timeframe: 5M

Pattern: M / Double Top

M Confirmation: ✓
Pullback: ✓
Entry Candle: ✓

Reference Low:
1.16420

Entry Candle Low:
1.16415

➡️ Direction: PUT / DOWN

⏱ Expiration: 10 Minutes

Setup ID:
EURUSD-5M-M-20260825-XXXX

⚠️ Strategy signal only.
```

---

# 43. Three-Phase Signal Lifecycle

Every setup follows:

```text
PHASE 1
PATTERN FORMED
       ↓
PATTERN CONFIRMED
       ↓
PHASE 2
PULLBACK FORMED
       ↓
ENTRY CONFIRMATION
       ↓
PHASE 3
EXECUTION SIGNAL
```

A setup can also terminate as:

```text
INVALIDATED
```

or:

```text
EXPIRED
```

---

# 44. W State Machine

Implement W using an explicit state machine.

```text
W_NONE
   ↓
W_CANDIDATE
   ↓
W_STRUCTURE_VALID
   ↓
WAITING_FOR_P_BREAK
   ↓
W_CONFIRMED
   ↓
WAITING_FOR_RED_PULLBACK
   ↓
W_PULLBACK_ACTIVE
   ↓
RED_1
   ↓
RED_2
   ↓
RED_3
   ↓
WAITING_FOR_FIRST_GREEN
   ↓
CHECK_GREEN_BREAK
   ↓
CALL_SIGNAL
```

Invalid transitions:

```text
L1 BREAK
    ↓
INVALID
```

```text
4TH RED
    ↓
INVALID
```

```text
FIRST GREEN DOES NOT REACH RED #1 HIGH
    ↓
INVALID
```

---

# 45. M State Machine

```text
M_NONE
   ↓
M_CANDIDATE
   ↓
M_STRUCTURE_VALID
   ↓
WAITING_FOR_T_BREAK
   ↓
M_CONFIRMED
   ↓
WAITING_FOR_GREEN_PULLBACK
   ↓
M_PULLBACK_ACTIVE
   ↓
GREEN_1
   ↓
GREEN_2
   ↓
GREEN_3
   ↓
WAITING_FOR_FIRST_RED
   ↓
CHECK_RED_BREAK
   ↓
PUT_SIGNAL
```

Invalid transitions:

```text
H1 BREAK
    ↓
INVALID
```

```text
4TH GREEN
    ↓
INVALID
```

```text
FIRST RED DOES NOT REACH GREEN #1 LOW
    ↓
INVALID
```

---

# 46. Multi-Timeframe Strategy

Higher timeframes should act as directional filters.

Do not automatically require every timeframe to contain its own W/M.

---

## 15-Second Setup

For a 15-second CALL:

```text
5M bullish bias
        +
1M bullish bias
        +
15S W setup
        ↓
CALL
```

For PUT:

```text
5M bearish bias
        +
1M bearish bias
        +
15S M setup
        ↓
PUT
```

---

## 1-Minute Setup

For a 1-minute CALL:

```text
5M bullish bias
        +
1M W setup
        ↓
CALL
```

For PUT:

```text
5M bearish bias
        +
1M M setup
        ↓
PUT
```

---

## 5-Minute Setup

Initially:

```text
5M W/M setup
```

can operate independently.

Optionally introduce:

```text
15M directional filter
```

in a future version.

Do not add the 15M filter unless testing demonstrates that it improves the strategy.

---

# 47. Higher-Timeframe Bias

The implementation must NOT invent a random definition of "bullish bias."

Create a dedicated configurable module:

```text
mtf_filter.py
```

The exact bias methodology should be configurable and tested separately.

Do not automatically introduce:

- RSI
- MACD
- EMA
- SMA
- stochastic
- other indicators

unless explicitly requested.

---

# 48. Strategy Configuration

All numerical parameters must be configurable.

Example:

```python
STRATEGY_CONFIG = {

    "minimum_retracement": 0.50,

    "w_bottom_tolerance": 0.002,

    "m_top_tolerance": 0.002,

    "minimum_pullback_candles": 2,

    "maximum_pullback_candles": 3,

    "require_center_break": True,

    "allow_doji": False,

    "use_mtf_filter": True,

    "use_15m_filter": False,

}
```

The tolerance values above are placeholders.

Do not treat them as proven values.

They must be optimized using historical testing.

---

# 49. Timeframe Configuration

```python
TIMEFRAME_CONFIG = {

    "15s": {
        "expiration": "1m",
        "higher_timeframes": ["1m", "5m"]
    },

    "1m": {
        "expiration": "3m",
        "higher_timeframes": ["5m"]
    },

    "5m": {
        "expiration": "10m",
        "higher_timeframes": []
    }

}
```

---

# 50. Asset Configuration

Create:

```text
config/assets.py
```

Example:

```python
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
```

Allow future OTC assets:

```python
OTC_ASSETS = [
    "EUR/USD OTC",
]
```

Do not mix OTC and non-OTC statistics.

---

# 51. Telegram Configuration

Use environment variables.

`.env`:

```env
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

Never hard-code credentials.

Create:

```text
src/telegram/
```

with:

```text
bot.py
formatter.py
notifier.py
```

---

# 52. Telegram Notification Functions

Implement separate functions:

```python
send_pattern_signal()
send_pullback_signal()
send_execution_signal()
send_invalidated_signal()
```

Default behavior:

- Pattern = Phase 1
- Pullback = Phase 2
- Execution = Phase 3
- Invalidations are logged internally

Optionally allow invalidation notifications through configuration.

---

# 53. Setup ID

Every detected setup receives a unique ID.

Example:

```text
EURUSD-5M-W-20260825-182000
```

Every Telegram message associated with that setup must contain the same ID.

This allows the system to connect:

```text
Pattern
   ↓
Pullback
   ↓
Execution
```

---

# 54. Multiple Assets

Each asset must maintain independent state.

Example:

```text
EUR/USD 5M → W pullback
GBP/USD 5M → M confirmed
USD/JPY 1M → no setup
```

EUR/USD state must never interfere with GBP/USD.

---

# 55. Multiple Timeframes

The same asset can have multiple independent setups.

Example:

```text
EUR/USD 15S → W pullback
EUR/USD 1M  → M candidate
EUR/USD 5M  → W confirmed
```

These must be tracked independently.

Use a unique key such as:

```python
(asset, timeframe, setup_id)
```

---

# 56. Duplicate Prevention

Never send duplicate Phase 1/2/3 messages for the same setup.

Track setup state.

Example:

```python
if setup.phase == "EXECUTION_SENT":
    return
```

Also ensure that the same candle is not processed repeatedly.

---

# 57. Setup Expiration

Every setup should have a configurable maximum lifetime.

Example:

```python
MAX_SETUP_AGE = {
    "15s": configurable_value,
    "1m": configurable_value,
    "5m": configurable_value,
}
```

If a setup remains incomplete for too long:

```text
EXPIRED
```

Do not allow stale setups to trigger later.

---

# 58. Data Storage

Store every setup.

At minimum:

```text
setup_id
asset
timeframe
pattern
L1/H1
P/T
L2/H2
retracement
pattern_confirmation_time
pullback_start
pullback_candle_count
reference_price
entry_confirmation
entry_price
direction
expiration
result
created_at
updated_at
status
```

Use SQLite initially.

The database should be replaceable with PostgreSQL later.

---

# 59. Signal Outcome Tracking

After Phase 3, record:

```text
asset
timeframe
pattern
direction
entry_time
entry_price
expiration
expiry_time
result
```

Result:

```text
WIN
LOSS
UNKNOWN
```

Initially this can be manually or programmatically determined from market data.

The bot must NOT execute the trade.

---

# 60. Backtesting

Backtesting is mandatory before trusting live signals.

The backtester must test:

- W patterns
- M patterns
- 15S
- 1M
- 5M
- all approved assets
- pullback length
- entry confirmation
- higher-timeframe filter
- expiration
- time of day
- market session

Statistics:

```text
Total setups
Valid setups
Invalid setups
Execution signals
Wins
Losses
Win rate
Loss rate
Consecutive wins
Consecutive losses
Maximum drawdown
Average setup frequency
Average time between signals
Performance by asset
Performance by timeframe
Performance by W/M
Performance by pullback length
Performance by session
```

---

# 61. Backtesting Data Separation

Never optimize and evaluate on exactly the same dataset.

Use:

```text
TRAINING DATA
```

for parameter development.

Then:

```text
VALIDATION DATA
```

for testing.

Finally:

```text
OUT-OF-SAMPLE DATA
```

for final evaluation.

Do not claim the strategy has an edge merely because it performs well on one historical period.

---

# 62. Development Modes

The application must support:

```text
BACKTEST
PAPER
LIVE_SIGNAL
```

---

## BACKTEST

Historical data.

No live signals.

Optional test Telegram notifications.

---

## PAPER

Live market data.

Signals generated in real time.

No real trade execution.

Track hypothetical outcomes.

---

## LIVE_SIGNAL

Live market data.

Send signals to Telegram.

No automatic trade execution.

---

# 63. Logging

Every important strategy event must be logged.

Example:

```text
2026-08-25 18:20:01 INFO EUR/USD 5M W candidate detected
2026-08-25 18:25:00 INFO EUR/USD 5M L2 validated
2026-08-25 18:30:00 INFO EUR/USD 5M P broken - W confirmed
2026-08-25 18:35:00 INFO EUR/USD 5M pullback started
2026-08-25 18:40:00 INFO EUR/USD 5M pullback candle 2/3
2026-08-25 18:45:00 INFO EUR/USD 5M CALL confirmation
2026-08-25 18:45:00 INFO Telegram execution signal sent
```

Invalidation example:

```text
INFO EUR/USD 5M setup invalidated:
4th red pullback candle
```

Another:

```text
INFO EUR/USD 5M setup invalidated:
first green candle did not reach pullback reference high
```

---

# 64. Project Structure

Use this structure:

```text
pocket-option-signal-bot/
│
├── README.md
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml
│
├── config/
│   ├── settings.py
│   ├── strategy.py
│   └── assets.py
│
├── src/
│   ├── main.py
│   │
│   ├── data/
│   │   ├── base.py
│   │   ├── provider.py
│   │   ├── candle_builder.py
│   │   └── models.py
│   │
│   ├── strategy/
│   │   ├── swing_detector.py
│   │   ├── w_pattern.py
│   │   ├── m_pattern.py
│   │   ├── retracement.py
│   │   ├── pullback.py
│   │   ├── confirmation.py
│   │   ├── mtf_filter.py
│   │   └── state_machine.py
│   │
│   ├── signals/
│   │   ├── models.py
│   │   ├── generator.py
│   │   └── manager.py
│   │
│   ├── telegram/
│   │   ├── bot.py
│   │   ├── formatter.py
│   │   └── notifier.py
│   │
│   ├── storage/
│   │   ├── database.py
│   │   └── models.py
│   │
│   └── utils/
│       ├── logging.py
│       └── time.py
│
├── tests/
│   ├── test_swing_detector.py
│   ├── test_w_pattern.py
│   ├── test_m_pattern.py
│   ├── test_retracement.py
│   ├── test_pullback.py
│   ├── test_confirmation.py
│   ├── test_state_machine.py
│   └── test_telegram.py
│
├── data/
│   ├── historical/
│   └── logs/
│
└── scripts/
    ├── test_telegram.py
    ├── backtest.py
    └── paper_trade.py
```

---

# 65. Python Environment

Use Python:

```text
Python 3.11 or 3.12
```

Recommended:

```text
Python 3.12
```

Create virtual environment:

```powershell
python -m venv .venv
```

Activate on Windows:

```powershell
.venv\Scripts\activate
```

---

# 66. Initial Dependencies

Use only required dependencies.

Suggested:

```text
python-dotenv
pydantic
pandas
numpy
requests
python-telegram-bot
pytest
pytest-asyncio
```

Additional packages may be added only when required by the selected market-data provider.

Do not add unnecessary trading libraries.

---

# 67. Environment Variables

`.env.example`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

LOG_LEVEL=INFO

APP_MODE=PAPER

ENABLE_15S=true
ENABLE_1M=true
ENABLE_5M=true
```

Never commit `.env`.

---

# 68. Telegram Test

Create:

```text
scripts/test_telegram.py
```

Run:

```bash
python scripts/test_telegram.py
```

Expected:

```text
Telegram connection successful.
Test message sent successfully.
```

---

# 69. Development Order

Windsurf must build the application in this order.

## Step 1

Create the Python project.

## Step 2

Create configuration system.

## Step 3

Create data models.

## Step 4

Create Telegram service.

## Step 5

Create mock/historical market-data provider.

## Step 6

Create candle builder.

## Step 7

Create swing detector.

## Step 8

Implement W detection.

## Step 9

Implement M detection.

## Step 10

Implement 50% retracement validation.

## Step 11

Implement L1/H1 protection.

## Step 12

Implement P/T breakout confirmation.

## Step 13

Implement Phase 1 Telegram message.

## Step 14

Implement W/M pullback state machine.

## Step 15

Implement Phase 2 Telegram message.

## Step 16

Implement final entry confirmation.

## Step 17

Implement Phase 3 Telegram message.

## Step 18

Implement setup invalidation.

## Step 19

Implement setup expiration.

## Step 20

Implement database storage.

## Step 21

Implement backtesting.

## Step 22

Implement paper mode.

## Step 23

Verify the live market-data provider.

## Step 24

Connect live market data.

## Step 25

Run the bot in paper mode.

## Step 26

Only after successful testing enable LIVE_SIGNAL mode.

---

# 70. Unit Tests

The strategy must have unit tests for every important rule.

Test W:

```text
Valid L1/P/L2
Invalid L2 below L1
Valid 50% retracement
Invalid retracement below 50%
Valid P breakout
Invalid without P breakout
Valid 2-red pullback
Valid 3-red pullback
Invalid 4-red pullback
Valid first green reaches red #1 high
Invalid first green does not reach red #1 high
```

Test M:

```text
Valid H1/T/H2
Invalid H2 above H1
Valid 50% retracement
Invalid retracement below 50%
Valid T breakout
Invalid without T breakout
Valid 2-green pullback
Valid 3-green pullback
Invalid 4-green pullback
Valid first red reaches green #1 low
Invalid first red does not reach green #1 low
```

---

# 71. Critical W Rules

Do NOT change these rules.

```text
L1
 ↓
P
 ↓
Price retraces at least 50%
 ↓
L2
 ↓
L2 does not break L1
 ↓
Bullish reversal
 ↓
P breaks
 ↓
W confirmed
 ↓
First red candle
 ↓
Red #2
 ↓
Optional Red #3
 ↓
First green candle
 ↓
Green high reaches/breaks Red #1 high
 ↓
CALL
```

Invalid:

```text
L1 broken
```

or:

```text
4th red candle
```

or:

```text
first green high < first red high
```

---

# 72. Critical M Rules

Do NOT change these rules.

```text
H1
 ↓
T
 ↓
Price retraces at least 50%
 ↓
H2
 ↓
H2 does not break H1
 ↓
Bearish reversal
 ↓
T breaks
 ↓
M confirmed
 ↓
First green candle
 ↓
Green #2
 ↓
Optional Green #3
 ↓
First red candle
 ↓
Red low reaches/breaks Green #1 low
 ↓
PUT
```

Invalid:

```text
H1 broken
```

or:

```text
4th green candle
```

or:

```text
first red low > first green low
```

---

# 73. Important Interpretation of the Entry Candle

The first opposite-color candle after the pullback is the ONLY execution candle.

For W:

```text
RED pullback
       ↓
FIRST GREEN
       ↓
CHECK GREEN HIGH
       ↓
>= FIRST RED HIGH?
       ↓
CALL
```

For M:

```text
GREEN pullback
       ↓
FIRST RED
       ↓
CHECK RED LOW
       ↓
<= FIRST GREEN LOW?
       ↓
PUT
```

Do not use a later candle.

---

# 74. Signal Timing

The bot should generate signals based on completed candle information.

For the execution signal:

### W

The first green candle must close with:

```python
high >= first_red.high
```

Then generate:

```text
CALL
```

### M

The first red candle must close with:

```python
low <= first_green.low
```

Then generate:

```text
PUT
```

---

# 75. Signal Message Formatting

Keep Telegram messages concise but informative.

Each message should include:

```text
Asset
Timeframe
Pattern
Direction
Pattern status
Pullback status
Reference price
Expiration
Setup ID
Timestamp
```

Do not send huge technical logs to Telegram.

Technical details belong in application logs/database.

---

# 76. Signal Example — Complete W Lifecycle

### Phase 1

```text
🟢 W PATTERN FORMED

EUR/USD
5M

W / Double Bottom

L1: 1.16000
P: 1.16500
L2: 1.16150

50% Retracement ✓
L1 Protected ✓

Waiting for P breakout.

ID: EURUSD-5M-W-001
```

### Phase 2

```text
🟡 W PULLBACK FORMED

EUR/USD
5M

W Confirmed ✓
P Break ✓

Pullback:
🔴 2 Red Candles

Reference High:
1.16580

Waiting for first green candle
to reach/break 1.16580.

Expiration: 10M

ID: EURUSD-5M-W-001
```

### Phase 3

```text
🟢 CALL / UP EXECUTION

EUR/USD
5M

W Pattern ✓
Pullback ✓
Entry Confirmation ✓

Reference:
1.16580

Confirmation High:
1.16585

➡️ CALL / UP

Expiration: 10M

ID: EURUSD-5M-W-001
```

---

# 77. Signal Example — Complete M Lifecycle

### Phase 1

```text
🔴 M PATTERN FORMED

EUR/USD
5M

M / Double Top

H1: 1.16500
T: 1.16000
H2: 1.16400

50% Retracement ✓
H1 Protected ✓

Waiting for T breakout.

ID: EURUSD-5M-M-001
```

### Phase 2

```text
🟡 M PULLBACK FORMED

EUR/USD
5M

M Confirmed ✓
T Break ✓

Pullback:
🟢 2 Green Candles

Reference Low:
1.16420

Waiting for first red candle
to reach/break 1.16420.

Expiration: 10M

ID: EURUSD-5M-M-001
```

### Phase 3

```text
🔴 PUT / DOWN EXECUTION

EUR/USD
5M

M Pattern ✓
Pullback ✓
Entry Confirmation ✓

Reference:
1.16420

Confirmation Low:
1.16415

➡️ PUT / DOWN

Expiration: 10M

ID: EURUSD-5M-M-001
```

---

# 78. No Automatic Trading

This application must NEVER:

- place a Pocket Option trade
- click CALL
- click PUT
- submit account credentials to an unofficial service
- attempt to bypass authentication
- attempt to bypass platform security
- attempt to bypass anti-bot protections

The application only:

```text
ANALYZE
→ DETECT
→ SIGNAL
→ RECORD
```

---

# 79. Security

Never expose:

```text
Telegram bot token
Pocket Option credentials
API keys
database credentials
```

Use `.env`.

Add `.env` to `.gitignore`.

---

# 80. Error Handling

The application must handle:

- market-data disconnect
- Telegram failure
- malformed candle
- duplicate candle
- missing candle
- timestamp errors
- provider timeout
- reconnection
- invalid asset
- database failure

A temporary data-provider failure must NOT create a false trading signal.

If candle data becomes unreliable:

```text
SIGNAL GENERATION = PAUSED
```

until the data stream is healthy again.

---

# 81. Data Gap Protection

If candles are missing:

```text
15S:
expected candle
missing candle
missing candle
```

do not attempt to reconstruct the strategy blindly.

Mark the data stream as:

```text
DATA_UNRELIABLE
```

and pause new signal generation for that asset/timeframe until sufficient valid data is available.

---

# 82. Time Synchronization

All timestamps must use timezone-aware UTC internally.

Display Telegram timestamps in the configured user timezone.

Configuration:

```env
DISPLAY_TIMEZONE=Africa/Lagos
```

Do not mix local time and UTC inside strategy calculations.

---

# 83. Future Dashboard

The architecture should make it possible to add a dashboard later showing:

```text
Active Setups
Recent Signals
Win Rate
Loss Rate
Assets
Timeframes
W/M Statistics
Pullback Statistics
```

Do not build the dashboard in Version 1 unless required.

---

# 84. Future Features

Possible future versions:

```text
15-minute MTF filter
Signal quality grading
A/A+/B setup ranking
Payout filtering
Trading session filtering
Historical chart visualization
Web dashboard
Mobile notifications
Advanced statistics
Strategy optimization
```

Do not implement these prematurely.

---

# 85. Version 1 Definition of Done

Version 1 is complete when the system can:

- Start successfully.
- Connect to a verified market-data provider.
- Receive candle data.
- Build 15S/1M/5M candles.
- Detect swing highs/lows.
- Detect W patterns.
- Detect M patterns.
- Apply 50% retracement.
- Protect L1/H1.
- Confirm W through P breakout.
- Confirm M through T breakout.
- Track 2–3 candle pullbacks.
- Invalidate at the fourth pullback candle.
- Record the first pullback candle's high/low.
- Validate the first opposite-color confirmation candle.
- Invalidate failed confirmation.
- Generate Phase 1 Telegram signal.
- Generate Phase 2 Telegram signal.
- Generate Phase 3 Telegram execution signal.
- Store setup information.
- Prevent duplicate signals.
- Support multiple assets.
- Support multiple timeframes.
- Run unit tests.
- Run historical backtests.
- Run in PAPER mode.
- Run in LIVE_SIGNAL mode without executing trades.

---

# 86. Windsurf Implementation Instructions

Use this README as the **single source of truth** for the strategy.

Do not simplify the strategy.

Do not change the W/M rules.

Do not add indicators.

Do not change the 50% requirement.

Do not change the 2–3 pullback rule.

Do not change the first-pullback-candle reference rule.

Do not allow later candles to trigger a setup after the first confirmation candle fails.

Do not automatically execute trades.

Do not invent an undocumented Pocket Option API.

Do not hard-code an unverified WebSocket endpoint.

Before implementing the live market-data provider, verify the actual available/authorized interface and document its:

- endpoint/interface
- authentication
- instruments
- candle availability
- timestamps
- update frequency
- reconnection behavior

If the live provider cannot supply 15-second candles directly, implement a tick/price-stream candle builder if the provider supplies sufficiently frequent data.

If no suitable live provider is available, keep the provider interface abstract and complete the entire strategy using historical/mock data first.

---

# 87. Most Important Strategy Summary

## W / CALL

```text
L1
 ↓
P
 ↓
≥50% retracement toward L1
 ↓
L2 above L1
 ↓
bullish reversal
 ↓
break P
 ↓
W CONFIRMED
 ↓
first RED candle
 ↓
RED #2
 ↓
optional RED #3
 ↓
first GREEN candle
 ↓
GREEN HIGH >= RED #1 HIGH
 ↓
CALL
```

## M / PUT

```text
H1
 ↓
T
 ↓
≥50% retracement toward H1
 ↓
H2 below H1
 ↓
bearish reversal
 ↓
break T
 ↓
M CONFIRMED
 ↓
first GREEN candle
 ↓
GREEN #2
 ↓
optional GREEN #3
 ↓
first RED candle
 ↓
RED LOW <= GREEN #1 LOW
 ↓
PUT
```

## Invalidations

```text
W:
L1 broken
OR
4th red pullback candle
OR
first green candle fails to reach red #1 high
= INVALID
```

```text
M:
H1 broken
OR
4th green pullback candle
OR
first red candle fails to reach green #1 low
= INVALID
```

## Timeframes

```text
15S → 1M expiration
1M  → 3M expiration
5M  → 10M expiration
```

## Telegram

```text
PHASE 1
PATTERN FORMED

        ↓

PHASE 2
PULLBACK FORMED

        ↓

PHASE 3
EXECUTION SIGNAL
```

---

# 88. Final Principle

The objective of this project is NOT to make the bot "predict the market."

The objective is:

> **Convert the trader's exact W/M pattern-recognition process into deterministic, testable rules.**

Every signal must be explainable.

For every CALL or PUT, the system should be able to answer:

```text
Why did this signal occur?
```

with:

```text
Pattern:
W/M

L1/H1:
...

P/T:
...

L2/H2:
...

50% retracement:
PASS

Pattern breakout:
PASS

Pullback:
2 or 3 candles

Reference:
...

Entry candle:
...

Entry reference break:
PASS

Higher timeframe:
...

Expiration:
...
```

If the bot cannot explain why a signal was generated, the implementation is not complete.

**END OF SPECIFICATION**