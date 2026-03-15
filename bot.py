"""
Alpaca AI Stock Trading Bot
============================
Claude-powered stock trading across multiple sectors.
News -> Sentiment -> Technicals -> Trade Decision
"""

import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
from exchange import ExchangeClient
from indicators import compute_indicators
from news import run_news_cycle, load_analysis
from claude_strategy import ClaudeStrategy

# ============================================================
# logging
# ============================================================

_log_fmt = logging.Formatter("%(asctime)s [StockBot] %(message)s", datefmt="%H:%M:%S")

_console = logging.StreamHandler(
    stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
)
_console.setLevel(logging.INFO)
_console.setFormatter(_log_fmt)

_fileh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
_fileh.setLevel(logging.DEBUG)
_fileh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log = logging.getLogger("StockBot")
log.setLevel(logging.DEBUG)
log.addHandler(_console)
log.addHandler(_fileh)

# ============================================================
# bot engine
# ============================================================


class StockBot:
    def __init__(self):
        self.exchange = ExchangeClient()  # alpaca connection
        self.strategy = ClaudeStrategy(
            min_confidence=0.65
        )  # self.strat - confidence over .65 to purchase
        self.market_data = {}  # dict of dataframes
        self.current_tickers = []  # stocks Claude picked from the news- changes w/ news

        # Timing trackers: controls when news fetches, quick checks, when full prints happen.
        self.last_claude_analysis = None
        self.last_claude_full_print = None
        self.last_news_fetch = None

        # Buy confirmation tracker: Claude must say BUY twice in a row before we execute
        self.pending_buys = {}  # { "AAPL": 1 } means Claude said BUY once, need one more

        log.info(f"Stock Bot initialized | Sectors: {', '.join(config.SECTORS)}")

    # ============================================================
    # market data
    # ============================================================

    def fetch_market_data(self):
        """reads news analysis to see what tickers claude picked, then fetches bars from Alpaca."""
        news_analysis = load_analysis()
        self.current_tickers = self.strategy._get_tickers_from_news(news_analysis)

        if not self.current_tickers:
            log.warning("No tickers from news analysis")
            return

        for symbol in self.current_tickers:
            df = self.exchange.fetch_bars(symbol)
            if not df.empty and len(df) > 50:
                df = compute_indicators(df)
                self.market_data[symbol] = df
            else:
                log.warning(f"Insufficient data for {symbol}")

    def should_run_news(self) -> bool:
        """Check if it's time to refresh news (every 60 mins)"""
        if self.last_news_fetch is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - datetime.fromisoformat(self.last_news_fetch)
        ).total_seconds() / 60
        return elapsed >= config.NEWS_REFRESH_MINS

    def should_run_claude(self) -> bool:
        """Check if it's time for a quick Claude analysis (every 5 mins)"""
        if self.last_claude_analysis is None:
            return True
        elapsed = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(self.last_claude_analysis)
        ).total_seconds() / 60
        return elapsed >= config.CLAUDE_QUICK_CHECK_MINS

    def should_full_print(self) -> bool:
        """Check if it's time for a full verbose print (every 30 mins)"""
        if self.last_claude_full_print is None:
            return True
        elapsed = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(self.last_claude_full_print)
        ).total_seconds() / 60
        return elapsed >= config.CLAUDE_FULL_PRINT_MINS

    # ============================================================
    # trading
    # ============================================================

    def _get_open_order_symbols(self) -> list:
        """Check Alpaca for any pending/queued orders to prevent double-buying."""
        try:
            r = requests.get(
                f"{self.exchange.base_url}/v2/orders",
                headers=self.exchange.headers,
                params={"status": "open"},
                timeout=10,
            )
            if r.status_code == 200:
                return [order.get("symbol", "") for order in r.json()]
        except:
            pass
        return []

    def check_entries(self):
        """Check if Claude wants to buy anything. Requires BUY signal twice in a row."""
        positions = self.exchange.get_positions()
        held_symbols = [p["symbol"] for p in positions]

        # also check for queued/pending orders so we dont double-buy
        open_order_symbols = self._get_open_order_symbols()
        held_symbols += open_order_symbols

        cash = self.exchange.get_balance()
        num_positions = len(positions) + len(open_order_symbols)

        for symbol in self.current_tickers:
            if symbol in held_symbols:
                continue
            if num_positions >= config.MAX_POSITIONS:
                continue

            sig = self.strategy.signal(symbol)
            if not sig:
                # Claude didn't say BUY — reset the confirmation counter
                self.pending_buys.pop(symbol, None)
                continue

            # Claude said BUY — check if this is the first or second time
            buy_count = self.pending_buys.get(symbol, 0) + 1
            self.pending_buys[symbol] = buy_count

            if buy_count < 2:
                log.info(f"  [{symbol}] BUY signal detected — waiting for confirmation (1/2)")
                continue

            # Confirmed! Claude said BUY twice in a row
            self.pending_buys.pop(symbol, None)

            # Calculate position size
            amount = min(
                cash * config.MAX_ALLOCATION_PER_STOCK,
                cash * 0.90,
            )

            if amount < 10:
                continue

            result = self.exchange.market_buy(symbol, amount)
            if result.get("status") != "error":
                # Get current indicators for the log
                df = self.market_data.get(symbol)
                row = df.iloc[-1] if df is not None and not df.empty else None

                log.info("")
                log.info(f"  ========== TRADE EXECUTED ==========")
                log.info(f"  BUY {symbol}")
                log.info(f"    Order ID:  {result.get('id', 'N/A')}")
                log.info(f"    Amount:    ${amount:,.2f}")
                if row is not None:
                    log.info(f"    Price:     ${row.get('close', 0):,.2f}")
                    log.info(f"    RSI:       {row.get('rsi', 0):.1f}")
                    log.info(f"    ADX:       {row.get('adx', 0):.1f}")
                    log.info(f"    MACD hist: {row.get('macd_hist', 0):.4f}")
                    log.info(f"    BB %B:     {row.get('bb_pct', 0):.3f}")
                    log.info(f"    Mom 20d:   {row.get('mom_20d', 0) * 100:+.1f}%")
                log.info(f"    Reason:    {sig.get('reasoning', '')}")
                log.info(f"  ====================================")
                if not self.exchange.is_market_open():
                    log.info(f"  ** ORDER QUEUED — will execute at market open **")
                log.info("")
                num_positions += 1
                cash -= amount

    def check_exits(self):
        """checks if Claude wants to sell anything."""
        positions = self.exchange.get_positions()

        for p in positions:
            symbol = p["symbol"]
            sell, confidence, reasoning = self.strategy.should_sell(symbol)

            if sell:
                qty = float(p.get("qty", 0))
                if qty <= 0:
                    continue

                result = self.exchange.market_sell(symbol, qty)
                if result.get("status") != "error":
                    entry = float(p.get("avg_entry_price", 0))
                    exit_price = result.get("price", 0)
                    pnl_pct = float(p.get("unrealized_plpc", 0)) * 100

                    log.info("")
                    log.info(f"  ========== TRADE EXECUTED ==========")
                    log.info(f"  SELL {symbol}")
                    log.info(f"    Order ID:  {result.get('id', 'N/A')}")
                    log.info(f"    Quantity:  {qty}")
                    log.info(f"    Entry:     ${entry:,.2f}")
                    log.info(f"    Exit:      ${exit_price:,.2f}")
                    log.info(f"    P&L:       {pnl_pct:+.1f}%")
                    log.info(f"    Reason:    {reasoning}")
                    log.info(f"  ====================================")
                    if not self.exchange.is_market_open():
                        log.info(f"  ** ORDER QUEUED — will execute at market open **")
                    log.info("")

                    self.strategy.record_trade(
                        symbol=symbol,
                        side="sell",
                        entry_price=entry,
                        exit_price=exit_price,
                        pnl_pct=pnl_pct,
                        strategy="claude_ai",
                        reason=reasoning,
                    )

    # ============================================================
    # main cycle
    # ============================================================

    def run_cycle(self):
        """One cycle of the bot."""
        market_open = self.exchange.is_market_open()
        now = datetime.now(timezone.utc)

        # display portfolio status
        cash = self.exchange.get_balance()
        positions = self.exchange.get_positions()
        total_value = cash + sum(float(p.get("market_value", 0)) for p in positions)
        ret = (total_value - config.INITIAL_CAPITAL) / config.INITIAL_CAPITAL * 100

        # get market sentiment from news analysis
        news = load_analysis()
        overview = news.get("market_overview", {})
        market_sentiment = overview.get("sentiment", "unknown").upper()

        log.info(f"==================================================")
        log.info(
            f"  Balance: ${cash:,.2f} | Portfolio: ${total_value:,.2f} ({ret:+.1f}%) | "
            f"Positions: {len(positions)}"
        )
        log.info(
            f"  Market: {'OPEN' if market_open else 'CLOSED'} | "
            f"Sentiment: {market_sentiment} | "
            f"Watching: {', '.join(self.current_tickers) if self.current_tickers else 'None'}"
        )

        # per-stock status with indicators (like the crypto bot)
        for symbol in self.current_tickers:
            df = self.market_data.get(symbol)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                price = row.get("close", 0)
                adx = row.get("adx", 0)
                rsi = row.get("rsi", 0)
                mom20 = row.get("mom_20d", 0) or 0

                # check if we're holding this stock
                held = [p for p in positions if p["symbol"] == symbol]
                if held:
                    entry = float(held[0].get("avg_entry_price", 0))
                    pnl = float(held[0].get("unrealized_plpc", 0)) * 100
                    log.info(
                        f"  [{symbol}] HOLDING | Price: ${price:,.2f} | Entry: ${entry:,.2f} | "
                        f"P&L: {pnl:+.1f}% | ADX: {adx:.1f} | RSI: {rsi:.1f}"
                    )
                else:
                    log.info(
                        f"  [{symbol}] Watching | Price: ${price:,.2f} | ADX: {adx:.1f} | "
                        f"RSI: {rsi:.1f} | Mom20d: {mom20*100:+.1f}%"
                    )

        # show pending buy confirmations
        for symbol, count in self.pending_buys.items():
            log.info(f"  [{symbol}] BUY pending confirmation ({count}/2)")

        # refresh news if needed
        if self.should_run_news():
            try:
                log.info("Refreshing news...")
                run_news_cycle()
                self.last_news_fetch = now.isoformat()
            except Exception as e:
                log.error(f"News fetch failed: {e}")

        # fetch market data for current tickers
        self.fetch_market_data()

        # run Claude analysis based on timing
        if market_open:
            if self.should_full_print():
                log.info("Running Claude full analysis...")
                self.strategy.run_analysis(
                    self.market_data,
                    positions=positions,
                    verbose=True,
                )
                self.last_claude_analysis = now.isoformat()
                self.last_claude_full_print = now.isoformat()
            elif self.should_run_claude():
                self.strategy.run_analysis(
                    self.market_data,
                    positions=positions,
                    verbose=False,
                )
                self.last_claude_analysis = now.isoformat()
        else:
            # overnight: just check every 4 hours
            if self.last_claude_analysis is None:
                run_overnight = True
            else:
                elapsed = (
                    now - datetime.fromisoformat(self.last_claude_analysis)
                ).total_seconds() / 3600
                run_overnight = elapsed >= config.OVERNIGHT_CHECK_HOURS

            if run_overnight:
                log.info("Running overnight Claude analysis...")
                self.strategy.run_analysis(
                    self.market_data,
                    positions=positions,
                    verbose=True,
                )
                self.last_claude_analysis = now.isoformat()
                self.last_claude_full_print = now.isoformat()

        # trade (always check — Alpaca queues orders for open)
        self.check_exits()
        self.check_entries()

    # ============================================================
    # run loop
    # ============================================================

    def run(self):
        """Main bot loop- prints startup banner"""
        log.info(f"==================================================")
        log.info(f"Stock Bot | Sectors: {', '.join(config.SECTORS)}")
        log.info(f"  Capital:    ${config.INITIAL_CAPITAL:,.2f}")
        log.info(f"  Target:     {config.MONTHLY_PROFIT_TARGET * 100:.0f}% monthly")
        log.info(f"  Max positions: {config.MAX_POSITIONS}")
        log.info(f"  Quick check: every {config.CLAUDE_QUICK_CHECK_MINS}min")
        log.info(f"  Full print:  every {config.CLAUDE_FULL_PRINT_MINS}min")
        log.info(f"  News refresh: every {config.NEWS_REFRESH_MINS}min")
        log.info(f"  Overnight:   every {config.OVERNIGHT_CHECK_HOURS}h")
        log.info(f"==================================================")
        log.info("")

        try:
            while True:
                try:
                    self.run_cycle()
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    log.error(f"Error in cycle: {e}", exc_info=True)

                remaining = 30
                while remaining > 0:
                    print(f"\r  Next scan in {remaining}s   ", end="", flush=True)
                    time.sleep(1)
                    remaining -= 1
                print("\r" + " " * 30 + "\r", end="", flush=True)

        except KeyboardInterrupt:  # ctrl + c stops bot
            print()
            log.info("Bot stopped.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    bot = StockBot()
    bot.run()
