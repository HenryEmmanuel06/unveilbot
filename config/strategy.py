"""Strategy configuration.

Every numeric parameter is a placeholder that must be optimized through
backtesting. Nothing here is a proven value.
"""

STRATEGY_CONFIG: dict = {
    # --- W/M structure -------------------------------------------------
    "minimum_retracement": 0.50,
    "w_bottom_tolerance": 0.002,
    "m_top_tolerance": 0.002,
    # Minimum height of the middle peak/trough relative to price, e.g.
    # (P - L1) / L1 >= min_w_peak_depth
    "min_w_peak_depth": 0.0004,
    "min_m_trough_depth": 0.0004,
    "require_center_break": True,
    # --- swing detection ----------------------------------------------
    # A swing high/low must be the extreme of `swing_lookback` candles on
    # each side (fractal detection).
    "swing_lookback": 2,
    # Minimum move (fraction of price) between consecutive swings for the
    # swing to be considered meaningful.
    "min_swing_size": 0.0002,
    # How many candles back the scanner looks for a fresh W/M structure.
    "structure_lookback_candles": 120,
    # --- candle representation -----------------------------------------
    # Heikin Ashi smooths the series so a pullback is a real directional move
    # instead of a single noisy candle.
    "use_heikin_ashi": True,
    # "pullback" -> Heikin Ashi drives pullback counting and entry confirmation,
    #               W/M structure and the P/T breakout stay on real candles.
    # "all"      -> Heikin Ashi also drives structure detection, L1/H1
    #               protection and the P/T breakout.
    "heikin_ashi_scope": "pullback",
    # --- pullback ------------------------------------------------------
    # How much of the #1 pullback candle the first opposite candle must
    # retrace to trigger the trade. 1.0 = full high/low break; 0.5 = 50%.
    "entry_confirmation_threshold": 0.5,
    "minimum_pullback_candles": 2,
    "maximum_pullback_candles": 3,
    # Doji candles are neither green nor red. When False a doji does not
    # count as a pullback candle and does not confirm an entry.
    "allow_doji": False,
    # --- multi timeframe ----------------------------------------------
    "use_mtf_filter": True,
    # 5m has no higher analysis timeframe of its own, so the 15m bias is what
    # gives a 5m execution the same top-down confirmation 30s/1m already have.
    "use_15m_filter": True,
    # --- flat candle confirmation --------------------------------------
    # Before an execution is allowed, the *currently forming* Heikin Ashi
    # candle of each listed timeframe must have no wick against the trade:
    # a flat bottom for a W/CALL, a flat top for an M/PUT.
    "use_flat_candle_filter": True,
    "flat_candle_timeframes": {
        "15s": ["1m", "5m"],
        "30s": ["1m", "5m"],
        "1m": ["5m"],
        "5m": ["15m"],
    },
    # Allowed wick as a fraction of the candle range. 0.0 = perfectly flat.
    "flat_candle_wick_tolerance": 0.0,
    # "swing_structure" -> higher highs + higher lows = bullish
    # "range_position"  -> close in upper part of recent range = bullish
    "mtf_bias_method": "swing_structure",
    "mtf_bias_lookback": 40,
    # Stage at which the higher timeframe bias is applied:
    # "pattern" (Phase 1) or "execution" (Phase 3).
    "mtf_filter_stage": "execution",
    # --- lifecycle -----------------------------------------------------
    # Maximum age of an incomplete setup, expressed in candles of its own
    # timeframe.
    "max_setup_age_candles": {
        "15s": 40,
        "30s": 40,
        "1m": 40,
        "5m": 40,
    },
    # Number of candles required before a data stream is considered healthy
    # enough to generate signals.
    "min_candles_for_analysis": 60,
    # Candles that may be missing in a row before the stream is flagged
    # DATA_UNRELIABLE.
    "max_missing_candles": 2,
}

TIMEFRAME_CONFIG: dict = {
    "15s": {"seconds": 15, "expiration": "1m", "higher_timeframes": ["1m", "5m"]},
    "30s": {"seconds": 30, "expiration": "1m", "higher_timeframes": ["1m", "5m"]},
    "1m": {"seconds": 60, "expiration": "3m", "higher_timeframes": ["5m"]},
    "5m": {"seconds": 300, "expiration": "10m", "higher_timeframes": []},
    "15m": {"seconds": 900, "expiration": "30m", "higher_timeframes": []},
}

EXPIRATION_LABELS: dict = {
    "1m": "1 Minute",
    "3m": "3 Minutes",
    "10m": "10 Minutes",
    "30m": "30 Minutes",
}


def timeframe_seconds(timeframe: str) -> int:
    return int(TIMEFRAME_CONFIG[timeframe]["seconds"])


def expiration_for(timeframe: str) -> str:
    return str(TIMEFRAME_CONFIG[timeframe]["expiration"])


def expiration_label(timeframe: str) -> str:
    exp = expiration_for(timeframe)
    return EXPIRATION_LABELS.get(exp, exp)


def higher_timeframes(timeframe: str) -> list[str]:
    tfs = list(TIMEFRAME_CONFIG[timeframe]["higher_timeframes"])
    if STRATEGY_CONFIG["use_15m_filter"] and timeframe == "5m" and "15m" not in tfs:
        tfs.append("15m")
    return tfs


def flat_candle_timeframes(timeframe: str) -> list[str]:
    """Timeframes whose forming candle must be flat before an execution."""
    if not STRATEGY_CONFIG["use_flat_candle_filter"]:
        return []
    return list(STRATEGY_CONFIG["flat_candle_timeframes"].get(timeframe, []))
