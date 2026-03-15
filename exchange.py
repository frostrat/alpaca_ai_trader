"""exchange client for Alpaca stock trading via REST API."""

import logging  # logger
import requests  # raw http calls (to alpaca api)
import pandas as pd  # time for dataframes !
from datetime import datetime, timedelta  # timestamps /time math

import config  # import api keys and settings

log = logging.getLogger("StockBot")


# =================================================================
# account and balence methods:
# =================================================================
class ExchangeClient:
    """handles all Alpaca API communication."""

    def __init__(self):  # sets up the 3 things the other mothod w/in the class needs.
        self.base_url = config.ALPACA_BASE_URL  # acct info, orders, positions
        self.data_url = config.ALPACA_DATA_URL  # price quotes & candle bars home
        self.headers = {  # a dict with api keys formatted how alpaca expects them-> stored here and referenced in ea method as self.headers !
            "APCA-API-KEY-ID": config.ALPACA_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
        }
        log.info("Alpaca connected")  # log :)

    def get_account(
        self,
    ) -> dict:  # gets all the info from our acct returning full acct dictionary (cash, buying power, portfolio value, ...)
        """Get account info."""
        try:
            r = requests.get(  # makes GET request to
                f"{self.base_url}/v2/account",  # ALPACA_BASE_URL pulled from config file and saved as self.base_url
                headers=self.headers,  # attaches api keys to request
                timeout=10,  # max wait 10 seconds &&&&&&
            )
            return r.json()  # return output as JSON
        except Exception as e:
            log.error(
                f"Failed to get account: {e}"
            )  # log the error and return an empty dict so it doesnt crash
            return {}

    def get_balance(self) -> float:
        """call get_account to get the dict from alpaca, then finds cash value and converts to a #####"""
        acct = self.get_account()
        return float(acct.get("cash", 0))

    def get_buying_power(self) -> float:
        """^^^same thing but grabs "buying power" instead of cash."""
        acct = self.get_account()
        return float(acct.get("buying_power", 0))

    def get_price(self, symbol) -> float:
        """asks alpaca for the latest price on a stock->."""
        try:
            r = requests.get(
                f"{self.data_url}/v2/stocks/{symbol}/quotes/latest",
                headers=self.headers,
                timeout=10,
            )
            if r.status_code != 200:
                log.error(
                    f"Quote failed for {symbol}: {r.status_code}"
                )  # <- if this fails log it and return 0
                return 0.0
            quote = r.json().get(
                "quote", {}
            )  # pulls the bid and ask prices out of response, converts to #s & returns midpoint.
            # Use midpoint of bid/ask
            bid = float(quote.get("bp", 0))
            ask = float(quote.get("ap", 0))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return ask or bid
        except Exception as e:  # more error handling.
            log.error(f"Failed to get price for {symbol}: {e}")
            return 0.0

    # ==================================================
    # Buy and Sell orders
    # ==================================================

    def fetch_bars(self, symbol, limit=250) -> pd.DataFrame:
        """Fetch daily OHLCV bars for a symbol."""
        try:
            end = datetime.now()  # end is today. start is ~300 days ago
            start = end - timedelta(days=limit + 50)

            r = requests.get(  # api requests for daily candles
                f"{self.data_url}/v2/stocks/{symbol}/bars",
                headers=self.headers,
                params={
                    "timeframe": "1Day",
                    "start": start.strftime(
                        "%Y-%m-%d"
                    ),  # just datetime object string conversion
                    "end": end.strftime("%Y-%m-%d"),
                    "limit": limit,
                    "feed": "iex",  # required for free tier - limit is 250 candles
                },
                timeout=10,
            )

            if r.status_code != 200:
                log.error(f"Bars failed for {symbol}: {r.status_code}")  # Errorrrrrrrs
                return pd.DataFrame()

            data = r.json()
            bars = data.get("bars", [])
            if not bars:
                log.warning(f"No bars returned for {symbol}")
                return pd.DataFrame()
                # converting to DataFrame -> takes raw list of bars turns into dataframe for column name conversion (to match indicators.py)
            df = pd.DataFrame(bars)
            df = df.rename(
                columns={
                    "t": "timestamp",
                    "o": "open",
                    "h": "high",
                    "l": "low",
                    "c": "close",
                    "v": "volume",
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            return df

        except Exception as e:
            log.error(f"Failed to fetch bars for {symbol}: {e}")
            return pd.DataFrame()

    """deals with placing a market buy -> posts buy order to alpaca. **notional= buy this dollar amount vs # of shares. bless alpaca handles fractional share math <3 """

    def market_buy(self, symbol, amount_usd) -> dict:
        try:
            r = requests.post(
                f"{self.base_url}/v2/orders",
                headers=self.headers,
                json={
                    "symbol": symbol,
                    "notional": str(round(amount_usd, 2)),
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",  # order expires if not placed by end of trading day
                },
                timeout=10,
            )

            if r.status_code not in [200, 201]:
                log.error(f"Buy failed for {symbol}: {r.status_code} — {r.text[:300]}")
                return {"status": "error", "msg": r.text[:300]}

            order = r.json()  # handles order placement printing order id and amount
            log.info(
                f"BUY order placed: {symbol} | ${amount_usd:.2f} | Order: {order.get('id', 'N/A')}"
            )
            return {
                "status": order.get("status", "accepted"),
                "symbol": symbol,
                "side": "buy",
                "quantity": float(order.get("filled_qty", 0) or 0),
                "price": float(order.get("filled_avg_price", 0) or 0),
                "cost": amount_usd,
                "id": order.get("id", ""),
            }

        except Exception as e:
            log.error(f"Buy failed for {symbol}: {e}")
            return {"status": "error", "msg": str(e)}

    def market_sell(
        self, symbol, quantity
    ) -> dict:  # order sell -> similar structure to market_buy
        """Place a market sell order."""
        try:
            r = requests.post(
                f"{self.base_url}/v2/orders",
                headers=self.headers,
                json={
                    "symbol": symbol,
                    "qty": str(
                        quantity
                    ),  # selling shares vs dollar amount bc we are selling owned shares.
                    "side": "sell",
                    "type": "market",
                    "time_in_force": "day",
                },
                timeout=10,
            )

            if r.status_code not in [200, 201]:
                log.error(f"Sell failed for {symbol}: {r.status_code} — {r.text[:300]}")
                return {"status": "error", "msg": r.text[:300]}

            order = (
                r.json()
            )  # pretty much same as buy but will calculate proceeds upon selling.
            price = float(order.get("filled_avg_price", 0) or 0)
            qty = float(order.get("filled_qty", 0) or quantity)
            log.info(
                f"SELL order placed: {symbol} | qty: {qty} | Order: {order.get('id', 'N/A')}"
            )
            return {
                "status": order.get("status", "accepted"),
                "symbol": symbol,
                "side": "sell",
                "quantity": qty,
                "price": price,
                "proceeds": qty * price if price > 0 else 0,
                "id": order.get("id", ""),
            }

        except Exception as e:
            log.error(f"Sell failed for {symbol}: {e}")
            return {"status": "error", "msg": str(e)}

    # =======================================================
    # positions and market status !
    # =======================================================

    def get_positions(self) -> list:
        """gets all open positions on alpaca - "what are we currently holding?" """
        try:
            r = requests.get(
                f"{self.base_url}/v2/positions",  # checks the acctual acct - not locally stored.
                headers=self.headers,  # will help with confusing if bot crashes for some reason.
                timeout=10,
            )
            if r.status_code != 200:
                log.error(f"Positions failed: {r.status_code}")
                return []
            return r.json()
        except Exception as e:
            log.error(f"Failed to get positions: {e}")
            return []

    def is_market_open(self) -> bool:
        """check if the stock market is currently open - >>>>
        will return true/false. This will help the bot know whether claude should run every 5 minutes or every
        four hours. No hardcoded hours needed."""
        try:
            r = requests.get(
                f"{self.base_url}/v2/clock",
                headers=self.headers,
                timeout=10,
            )
            return r.json().get("is_open", False)
        except Exception as e:
            log.error(f"Failed to check market status: {e}")
            return False


# ==============================================================
# standalone test !
# ==============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    client = ExchangeClient()

    print(f"\nCash: ${client.get_balance():,.2f}")
    print(f"Buying Power: ${client.get_buying_power():,.2f}")
    print(f"Market Open: {client.is_market_open()}")

    price = client.get_price("AAPL")
    print(f"AAPL Price: ${price:,.2f}")

    print("\nFetching AAPL bars...")
    df = client.fetch_bars("AAPL", limit=5)
    if not df.empty:
        print(df)

    print("\nPositions:")
    positions = client.get_positions()
    if positions:
        for p in positions:
            print(f"  {p['symbol']}: {p['qty']} shares @ ${p['avg_entry_price']}")
    else:
        print("  No open positions")
