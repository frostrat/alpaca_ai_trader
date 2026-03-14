import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# API KEYS (loaded from .env file)
# ============================================================

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = "https://api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

# ============================================================
# TRADING MODE
# ============================================================

PAPER_TRADING = False  # Alpaca has $0 fees so less risk going live

# ============================================================
# CAPITAL & RISK
# ============================================================

INITIAL_CAPITAL = 1000.0  # account start
MAX_POSITIONS = 3  # One per sector
COMMISSION = 0.0  # Alpaca is commission-free
ATR_STOP_MULT = 3.5  # Same as crypto bot
PCT_TRAIL_THRESHOLD = 0.5  # when a position is up 50% or more, the trailing stop switches from ATR-based to percentage-based.
# So if you're up 50% on a stock, it starts protecting those gains more aggressively.
PCT_TRAIL_AMOUNT = 0.20  # once that percentage trail kicks in, the stop trails 20%
# below the peak price. So if a stock hits $300 peak, your stop moves to $240.

# ============================================================
# ALLOCATION
# ============================================================

MAX_ALLOCATION_PER_STOCK = 0.35  # Max 35% in one stock
PARTIAL_TAKE_THRESHOLD = 0.5  # Take profits at 50%
PARTIAL_TAKE_SIZE = 0.30  # sell 30% of the position when taking profits

# ============================================================
# TIMING
# ============================================================

TIMEFRAME = "1Day"
CANDLE_HISTORY = 250
CHECK_INTERVAL = 60  # 60 seconds between checks
CLAUDE_ANALYSIS_HOURS = 0.5  # Re-evaluate every 30 min during market hours
NEWS_REFRESH_HOURS = 4  # Fresh news every hour

# Market hours (Central time)
MARKET_OPEN_HOUR = 8  # 8:30am CT but we start checking at 8
MARKET_CLOSE_HOUR = 15  # 3:00pm CT (4pm ET)
PREMARKET_CHECK_HOUR = 8  # Run a fresh analysis 30min before open

# ============================================================
# SECTORS (Claude will pick one stock from each)
# ============================================================

SECTORS = ["Technology", "Energy", "Healthcare"]

# ============================================================
# FILES
# ============================================================

LOG_FILE = "stock_bot.log"
STATE_FILE = "stock_bot_state.json"
