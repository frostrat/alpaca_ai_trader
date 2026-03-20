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
MAX_TRADE_HISTORY = 20  # trade history size mentioned in claudes memory section !!!
# started on 20 as its a good balance to see patterns but not get the prompt bloated with info.
# can change this if you want as its not hardcoded below.


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

        """Step 2.5: also build snapshots for held positions that arent in the news picks.
        if we own CRWD but news moved on to META, we still need claude to see CRWD
        and decide whether to HOLD or SELL it."""
        if positions:
            for p in positions:
                sym = p.get("symbol", "")
                if sym and sym not in snapshots:
                    df = market_data.get(sym)
                    if df is not None and len(df) >= 50:
                        row = df.iloc[-1]
                        snapshots[sym] = self._format_indicators(
                            sym, row, news_analysis
                        )
                        # mark it so claude knows this isnt a news pick
                        snapshots[sym]["sector"] = "Held (not in current news)"

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

        """Step 5: grabs previous signals so claude can see what he said last time- 
        keeps him from flip flopping without a good reason"""
        previous_signals_text = "No previous signals."
        if self._signals:
            previous_signals_text = json.dumps(self._signals, indent=2)

        """Step 6: builds prompt with everything combined and calls claude- verbose controls printing between the
         5min and 30 min checks (30min is the only checks that print besides when a buy goes through.) """
        prompt = self._build_prompt(
            snapshots, news_analysis, recent_trades, previous_signals_text
        )
        signals = self._call_claude(prompt, verbose)

        if signals:
            self._signals = signals
            self._save_signals(signals)
            log.info(f"Claude strategy updated for {len(signals)} symbols")

        return signals

    # =======
    # helper methods supporting main analysis ->

    def _get_tickers_from_news(self, news_analysis) -> list:
        """reads news analysis and pulls out top company in ea sector."""
        tickers = []
        for sector in config.SECTORS:
            data = news_analysis.get(sector, {})
            ticker = data.get("top_ticker", "")
            if ticker and ticker not in tickers:
                tickers.append(ticker)
        return tickers

    def _format_indicators(self, symbol, row, news_analysis) -> dict:
        """takes one rwo of indicator data and packages into clean dict"""

        def safe(val, decimals=2):
            if pd.isna(val):
                return None
            return round(float(val), decimals)

        # find which sector this stock belongs to via matching back to news analysis
        sector = "Unknown"
        for s in config.SECTORS:
            data = news_analysis.get(s, {})
            if data.get("top_ticker", "") == symbol:
                sector = s
                break

        # package of data Claude gets for one stock ->
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

    # ============================================================
    # prompt builder
    # ============================================================

    def _build_prompt(
        self, snapshots, news_analysis, trade_history, previous_signals_text
    ) -> str:
        """Build the full chained analysis prompt."""
        data_text = json.dumps(snapshots, indent=2)

        # format the inputs: take raw data and format into readble text for claude.
        news_text = ""
        overview = news_analysis.get("market_overview", {})
        news_text += f"Market Overview: {overview.get('sentiment', 'N/A')} ({overview.get('confidence', 0):.0%})\n"
        news_text += f"  {overview.get('summary', 'No overview available.')}\n"

        for sector in config.SECTORS:
            data = news_analysis.get(sector, {})
            sentiment = data.get("sentiment", "N/A")
            confidence = data.get("confidence", 0)
            summary = data.get("summary", "")
            ticker = data.get("top_ticker", "N/A")
            news_text += (
                f"\n{sector}: {sentiment} ({confidence:.0%}) — Pick: {ticker}\n"
            )
            news_text += f"  {summary}\n"

        # format trade history
        history_text = "No previous trades."
        if trade_history:
            history_text = json.dumps(trade_history, indent=2)

        return f"""You are a stock trading analyst managing a real portfolio on Alpaca. You receive:
1. News sentiment analysis per sector (already analyzed)
2. Technical indicators for the top stock in each sector
3. Current position information (INCLUDING held stocks not in current news picks)
4. Recent trade history (your past decisions and outcomes)
5. Your previous signals (what you said last time)

ANALYZE IN THIS ORDER:
Step 1: Review the news sentiment and market overview.
Step 2: Check if the technicals CONFIRM the news sentiment for each stock.
Step 3: Review positions and trade history for context.
Step 4: Review your previous signals for consistency.
Step 5: Make your final decision per stock.

IMPORTANT: You may see stocks marked as "Held (not in current news)" — these are positions
we currently own but the news cycle moved on to different tickers. You MUST still analyze
these and decide HOLD or SELL. Do NOT ignore held positions just because theyre not in the headlines.

ACTIONS (one per stock):
- BUY: NOT holding. Technicals confirm bullish news. Strong setup.
- WAIT: NOT holding. Setup isn't clear enough. This is the safe default.
- HOLD: Already holding. Position is healthy.
- SELL: Already holding. Breakdown or major negative catalyst.

RULES:
- You are a swing trader on DAILY candles. Positions last days to weeks.
- If NOT holding: only BUY or WAIT.
- If ALREADY holding: only HOLD or SELL.
- Your portfolio target is {config.MONTHLY_PROFIT_TARGET * 100:.0f}% monthly growth.
- FEES: Alpaca is commission-free. But still don't chase small moves under 3%.
- BUY requires: RSI not overbought (under 65), price showing trend support, ADX > 18, and bullish news. If BB %B > 0.85, do NOT buy.
- SELL requires: major technical breakdown OR high-confidence negative news.
- WAIT is always safer than a bad BUY.
- Learn from trade history. If similar setups lost money before, be cautious.
- Confidence = how strongly ALL signals align (0.0 = none, 1.0 = everything aligns).
- CONSISTENCY: review your previous signals below. If conditions havent changed much, keep your previous call. Dont flip between BUY and WAIT without a clear reason. If you change your mind, explain what changed.
- If market sentiment is BEARISH, do NOT open new positions. Only HOLD or SELL existing ones. Wait for neutral or bullish conditions before buying.

INDICATOR GUIDE:
- RSI < 30 = oversold (potential buy), RSI > 70 = overbought (avoid)
- RSI 60-70 = getting hot, be cautious
- MACD histogram > 0 and rising = bullish momentum
- Price above EMA 200 = long-term uptrend
- ADX > 20 = strong trend, ADX < 20 = choppy (avoid new entries)
- BB %B > 0.8 = near upper band (overbought), < 0.2 = oversold
- Volume ratio > 1.3 = high volume confirms the move

NEWS SENTIMENT:
{news_text}

TECHNICAL DATA + POSITIONS:
{data_text}

YOUR PREVIOUS SIGNALS (what you said last time — be consistent unless something changed):
{previous_signals_text}

TRADE HISTORY:
{history_text}

Respond ONLY with JSON, no markdown, no extra text. Include ALL stocks shown above (including held positions):
{{
  "TICKER1": {{
    "action": "buy/wait/hold/sell",
    "confidence": 0.0 to 1.0,
    "reasoning": "2-3 sentences: technicals + news alignment + decision"
  }},
  "TICKER2": {{
    "action": "buy/wait/hold/sell",
    "confidence": 0.0 to 1.0,
    "reasoning": "..."
  }}
}}"""

    # ============================================================
    # claude api call
    # ============================================================

    def _call_claude(self, prompt, verbose=True) -> dict:
        """send prompt to Claude and parse response."""
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,  # wait upto 30 seconds
            )

            data = response.json()  # error checking ->

            if "error" in data:
                log.error(
                    f"Claude API error: {data['error'].get('message', 'unknown')}"
                )
                return {}

            if "content" not in data or not data["content"]:
                log.error(f"Unexpected Claude response: {json.dumps(data)[:500]}")
                return {}

            raw = data["content"][0]["text"].strip()  # response clean up ->

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            signals = json.loads(raw)
            log.info("Claude trade analysis complete")

            # verbose logging - loop through ea stocks decision. If verbose = True (30 min check)
            # everything will print. if Verbose = False (5min check) doesnt print anything.
            for symbol, decision in signals.items():
                action = decision.get("action", "?").upper()
                conf = decision.get("confidence", 0)
                reasoning = decision.get("reasoning", "")
                if verbose or action in ["BUY", "SELL"]:
                    log.info(
                        f"  Claude [{symbol}]: {action} ({conf:.0%}) — {reasoning}"
                    )

            return signals

        except json.JSONDecodeError as e:  # more error catching ->
            log.error(f"Failed to parse Claude response: {e}")
            return {}
        except Exception as e:
            log.error(f"Claude API call failed: {e}")
            return {}

    # ============================================================
    # trade history // claudes memory
    # ============================================================

    def record_trade(
        self, symbol, side, entry_price, exit_price, pnl_pct, strategy, reason
    ):
        """Save a completed trade to Claude's memory."""
        history = self._load_trade_history()

        """when a trade closes, this gets called- loads whaterver trades are already saved->
        appends new trade as a dictionary w/ the details. 
        
        Keeps only the last 20 trades so file doesnt grow forever. """

        history.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "pnl_pct": round(pnl_pct, 2),
                "strategy": strategy,
                "exit_reason": reason,
                "time": datetime.now().astimezone().isoformat(),
            }
        )

        history = history[-MAX_TRADE_HISTORY:]

        try:  # writes trade list to ...._history.json. Claudes long-term memory.
            with open(TRADE_HISTORY_FILE, "w") as f:
                json.dump({"trades": history}, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save trade history: {e}")

    def _load_trade_history(self) -> list:  # reads the trade history back.
        """Load recent trades for Claude's context."""
        try:
            with open(TRADE_HISTORY_FILE, "r") as f:
                data = json.load(f)
            return data.get("trades", [])
        except FileNotFoundError:  # handles first run when no file exists yet.
            return []
        except Exception as e:
            log.error(f"Failed to load trade history: {e}")
            return []

    # ============================================================
    # persistance
    # ============================================================

    def _save_signals(self, signals):
        """claudes decisions are saved here with a timestamp. -every 5 min cycle reads from this."""
        data = {
            "analyzed_at": datetime.now().astimezone().isoformat(),
            "signals": signals,
        }
        try:
            with open(CLAUDE_SIGNAL_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save signals: {e}")

    def _load_signals(self):
        """load cached signals."""
        try:
            with open(CLAUDE_SIGNAL_FILE, "r") as f:
                data = json.load(f)
            self._signals = data.get("signals", {})
            analyzed = data.get("analyzed_at", "unknown")
            log.info(f"Loaded Claude signals from {analyzed}")
        except FileNotFoundError:
            self._signals = {}
        except Exception as e:
            log.error(f"Failed to load signals: {e}")
            self._signals = {}


# =======================================
# testterrrr
# =======================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    from exchange import ExchangeClient
    from indicators import compute_indicators
    from news import run_news_cycle

    print("\n--- Claude Stock Strategy Test ---")

    # Step 1: run news cycle first (feeds the chain)
    print("Fetching news and running sentiment analysis...")
    news_analysis = run_news_cycle()

    if not news_analysis:
        print("News cycle failed. Check API keys.")
        exit()

    # Step 2: Get the tickers Claude picked from news
    strategy = ClaudeStrategy()
    tickers = strategy._get_tickers_from_news(news_analysis)
    print(f"Claude picked: {tickers}")

    # Step 3: Fetch market data for those tickers
    print("Fetching market data...")
    exchange = ExchangeClient()
    market_data = {}

    for symbol in tickers:
        df = exchange.fetch_bars(symbol)
        if not df.empty and len(df) > 50:
            df = compute_indicators(df)
            market_data[symbol] = df
            price = df["close"].iloc[-1]
            print(f"  {symbol}: ${price:,.2f}")
        else:
            print(f"  {symbol}: no data")

    # Step 4: Get positions from Alpaca
    positions = exchange.get_positions()

    # Step 5: run the full chained analysis
    print("\nRunning Claude analysis...")
    signals = strategy.run_analysis(market_data, positions=positions)

    if signals:
        print("\n========== CLAUDE TRADE SIGNALS ==========")
        for symbol, data in signals.items():
            action = data.get("action", "?").upper()
            conf = data.get("confidence", 0)
            reasoning = data.get("reasoning", "")
            print(f"\n  {symbol}: {action} ({conf:.0%})")
            print(f"    {reasoning}")
        print("==========================================\n")
    else:
        print("\nNo signals generated. Check API keys.")
