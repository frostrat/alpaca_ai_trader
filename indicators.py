"""technical indicator computation (a ton of math)"""

import numpy as np
import pandas as pd


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """compute all technical indicators on OHLCV dataframe."""
    d = df.copy()

    # EMAs exponential mving averages
    for span in [5, 12, 21, 26, 50, 100, 200]:
        d[f"ema_{span}"] = d["close"].ewm(span=span, adjust=False).mean()

    # MACD moving average convergence divergence
    d["macd"] = d["ema_12"] - d["ema_26"]
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]

    # RSI relative strength index
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    # Bollinger Bands
    d["bb_mid"] = d["close"].rolling(20).mean()
    bb_std = d["close"].rolling(20).std()
    d["bb_upper"] = d["bb_mid"] + 2 * bb_std
    d["bb_lower"] = d["bb_mid"] - 2 * bb_std
    d["bb_pct"] = (d["close"] - d["bb_lower"]) / (
        d["bb_upper"] - d["bb_lower"]
    ).replace(0, np.nan)

    # ATR average true range
    high_low = d["high"] - d["low"]
    high_close = (d["high"] - d["close"].shift()).abs()
    low_close = (d["low"] - d["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()

    # ADX average directional index.
    plus_dm = d["high"].diff()
    minus_dm = -d["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    sm_tr = tr.rolling(14).sum()
    plus_di = 100 * (plus_dm.rolling(14).sum() / sm_tr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(14).sum() / sm_tr.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    d["adx"] = dx.rolling(14).mean()

    # Donchian Channels
    d["donchian_high"] = d["high"].rolling(20).max()
    d["donchian_low"] = d["low"].rolling(20).min()

    # Volume
    d["vol_sma"] = d["volume"].rolling(20).mean()
    d["vol_ratio"] = d["volume"] / d["vol_sma"].replace(0, np.nan)

    # Momentum
    d["mom_5d"] = d["close"].pct_change(5)
    d["mom_10d"] = d["close"].pct_change(10)
    d["mom_20d"] = d["close"].pct_change(20)

    # Stochastic RSI
    rsi_min = d["rsi"].rolling(14).min()
    rsi_max = d["rsi"].rolling(14).max()
    d["stoch_rsi"] = (d["rsi"] - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)

    return d
