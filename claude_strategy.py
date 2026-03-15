"""
Claude AI Strategy - Stock Sector Trading
==========================================
Chained analysis -> updated from prev. Parallelization workflow.
1. News sentiment picks sectors and top tickers
2. Technical indicators confirm or reject the setup
3. Position info and trade history inform the decision
4. Final action: BUY / WAIT / HOLD / SELL per stock

Claude picks WHAT to trade from the news, then confirms
WHEN to trade from the technicals.
"""

import json
import logging
import requests
from datetime import datetime
from typing import Optional

import pandas as pd

import config
from news import load_analysis

log = logging.getLogger("StockBot")

CLAUDE_SIGNAL_FILE = "claude_signals.json"
TRADE_HISTORY_FILE = "claude_trade_history.json"
MAX_TRADE_HISTORY = 20


class ClaudeStrategy:
    """
    AI stock strategy with chained analysis.
    News -> Technicals -> Positions -> Trade History -> Decision
    """

    def __init__(self, min_confidence=0.65):
        self.min_confidence = min_confidence
        self._signals = {}
        self._load_signals()

    # ============================================================
    # buy signal
    # ============================================================

    def signal(self, symbol) -> Optional[dict]:
        """
        Check if Claude recommends buying this symbol.
        Returns the signal dict or None.
        """
        if symbol not in self._signals:
            return None

        data = self._signals[symbol]
        action = data.get("action", "wait").lower()
        confidence = float(data.get("confidence", 0))

        if action == "buy" and confidence >= self.min_confidence:
            return data
        return None

    # ============================================================
    # sell signal
    # ============================================================

    def should_sell(self, symbol) -> tuple:
        """
        Check if Claude wants to sell, how confident, and why. The bot uses the reasoning in the log so you can see why Claude sold
        """
        if symbol not in self._signals:
            return False, 0.0, ""

        data = self._signals[symbol]
        action = data.get("action", "hold").lower()
        confidence = float(data.get("confidence", 0))
        reasoning = data.get("reasoning", "")

        if action == "sell" and confidence >= self.min_confidence:
            return True, confidence, reasoning
        return False, 0.0, ""

    # ============================================================
    # approval ->>>>>>>j
    # ============================================================

    def approves_buy(self, symbol) -> bool:
        """
        Quick check: does Claude say >BUY< for this symbol???
        used as a gate before any entry executes- stops issue of auto buy from v1 trade bot.
        """
        if symbol not in self._signals:
            return False

        data = self._signals[symbol]
        action = data.get("action", "wait").lower()
        confidence = float(data.get("confidence", 0))

        return action == "buy" and confidence >= self.min_confidence

    # ============================================================
    # main analysis (chained prompt)
    # ============================================================

    def run_analysis(
        self, market_data: dict, positions=None, trade_history=None, verbose=True
    ) -> dict:
        """
        single Claude call with full chain:
        news sentiment -> technicals -> positions -> trade history -> decision

        verbose: if True, always print analysis. If False, only print on BUY/SELL.
        """
        if not config.ANTHROPIC_API_KEY:
            log.error("ANTHROPIC_API_KEY not set")
            return {}

        """Step 1: Get news analysis (already done by news.py)"""
        news_analysis = load_analysis()

        """Step 2: get_tickers_from_news -> pulls top_ticker from ea sectors news analysis - stocks Claude picked from headlines.
        builds technical analysis for ea one-> everything from indicators.py"""
        tickers = self._get_tickers_from_news(news_analysis)
        if not tickers:
            log.warning("No tickers to analyze")
            return {}

        snapshots = {}
        for symbol in tickers:
            df = market_data.get(symbol)
            if df is None or len(df) < 50:
                continue
            row = df.iloc[-1]
            snapshots[symbol] = self._format_indicators(symbol, row, news_analysis)

        if not snapshots:
            log.warning("No technical data available for any tickers")
            return {}

        """Step 3: grabs current position from alpaca. uses alpacas position format vs v1bot format."""
        position_map = {}
        if positions:
            for p in positions:
                sym = p.get("symbol", "")
                position_map[sym] = {
                    "holding": True,
                    "entry_price": float(p.get("avg_entry_price", 0)),
                    "quantity": float(p.get("qty", 0)),
                    "current_price": float(p.get("current_price", 0)),
                    "unrealized_pnl_pct": float(p.get("unrealized_plpc", 0)) * 100,
                    "market_value": float(p.get("market_value", 0)),
                }

        for symbol in snapshots:
            if symbol in position_map:
                snapshots[symbol]["position"] = position_map[symbol]
            else:
                snapshots[symbol]["position"] = {"holding": False}

        """Step 4: load past trade history- enabling claude to learn from prev wins/losses"""
        recent_trades = self._load_trade_history()

        """Step 5: builds prompt with everything combined and calls claude- verbose controls printing between the
         5min and 30 min checks (30min is the only checks that print besides when a buy goes through.) """
        prompt = self._build_prompt(snapshots, news_analysis, recent_trades)
        signals = self._call_claude(prompt, verbose)

        if signals:
            self._signals = signals
            self._save_signals(signals)
            log.info(f"Claude strategy updated for {len(signals)} symbols")

        return signals

    # =======
    # helper methods supporting main analysis ->

    def _get_tickers_from_news(self, news_analysis) -> list:
        """Pull the top tickers Claude picked from news analysis."""
        tickers = []
        for sector in config.SECTORS:
            data = news_analysis.get(sector, {})
            ticker = data.get("top_ticker", "")
            if ticker and ticker not in tickers:
                tickers.append(ticker)
        return tickers

    def _format_indicators(self, symbol, row, news_analysis) -> dict:
        """Format one stock's indicators into a clean dict for Claude."""

        def safe(val, decimals=2):
            if pd.isna(val):
                return None
            return round(float(val), decimals)

        # Find which sector this stock belongs to
        sector = "Unknown"
        for s in config.SECTORS:
            data = news_analysis.get(s, {})
            if data.get("top_ticker", "") == symbol:
                sector = s
                break

        return {
            "symbol": symbol,
            "sector": sector,
            "price": safe(row.get("close"), 2),
            "ema_21": safe(row.get("ema_21"), 2),
            "ema_50": safe(row.get("ema_50"), 2),
            "ema_200": safe(row.get("ema_200"), 2),
            "adx": safe(row.get("adx"), 1),
            "rsi": safe(row.get("rsi"), 1),
            "macd": safe(row.get("macd"), 4),
            "macd_signal": safe(row.get("macd_signal"), 4),
            "macd_hist": safe(row.get("macd_hist"), 4),
            "stoch_rsi": safe(row.get("stoch_rsi"), 3),
            "atr": safe(row.get("atr"), 2),
            "bb_pct": safe(row.get("bb_pct"), 3),
            "vol_ratio": safe(row.get("vol_ratio"), 2),
            "mom_5d": safe(row.get("mom_5d"), 4),
            "mom_10d": safe(row.get("mom_10d"), 4),
            "mom_20d": safe(row.get("mom_20d"), 4),
        }
