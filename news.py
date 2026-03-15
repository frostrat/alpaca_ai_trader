"""
Stock Bot - Financial News from Finnhub + Alpha Vantage
========================================================
Pulls news from two sources for better coverage.
Finnhub: general market news + company-specific news
Alpha Vantage: sector-based news with built-in sentiment scores
"""

import json  # read and write json files
import logging  # logging system- replaces print with log.info(0)- writes to terminal and the log file w/ timestamps
import requests  # raw http calls. This was discussed as i was confused with dotenv and os bc you need os to request from apis but not SDKs.
from datetime import datetime  # used to grab timestamps obvi

import config  # pulls config.py to use the news api keys here

log = logging.getLogger(
    "StockBot"
)  # creates a logger named Stockbot - any file that uses this same name shares the same logger.

NEWS_FILE = "news_cache.json"  # file names for caching news info- saved as JSON files
ANALYSIS_FILE = "news_analysis.json"  # this is so the bot can read the past news when refreshing every 30 mins.

# ============================================================
# Finnhub news Fetcher
# ============================================================


def fetch_finnhub_news(sectors=None) -> list:
    """
    Fetch general market news from Finnhub.
    Returns a list of article dicts.
    """
    if not config.FINNHUB_API_KEY:
        log.error("FINNHUB_API_KEY not set")  # lets you know if theres an api key issue
        return []  # returns an empty list "I have no articles to give you!" this helps prevent a crash.

    try:  # try everything in this block and it anything goes wrong, catch it and print an error vs crashing
        response = requests.get(
            "https://finnhub.io/api/v1/news",
            params={
                "category": "general",
                "token": config.FINNHUB_API_KEY,
            },
            timeout=10,  # no answer in 10 seconds = give up. prevents waiting for a downed server to answer.
        )

        if (
            response.status_code != 200
        ):  # anything other than 200 means error or unusable data
            log.warning(
                f"Finnhub HTTP {response.status_code}"
            )  # we want that code to be logged and printed
            return []

        articles = []  # empty list
        for item in response.json()[
            :15
        ]:  # turns Finnhub's response into a Python list of dictionaries -> ea item is 1 article from finhub.
            title = item.get(
                "headline", ""
            )  # grab the headline, if empty skip and move to the next
            if not title:
                continue  # go back to start of the loooop
            articles.append(
                {  # this is to append the articles in
                    "title": title,  #
                    "source": item.get("source", ""),  #
                    "summary": item.get("summary", "")[
                        :200
                    ],  # grabs headline and first 200 char of summary
                    "published": item.get("datetime", 0),  #
                    "category": item.get("category", ""),  #
                    "url": item.get("url", ""),  #
                }
            )

        log.info(f"Finnhub: fetched {len(articles)} articles")
        return articles  # returns a clean list of dictionaries (think back to learning about "structured output")

    except Exception as e:
        log.error(f"Finnhub fetch failed: {e}")
        return []  # if theres an error this will print vs crashing the program.


# ============================================================
# Alpha vantage news Fetcher
# ============================================================


def fetch_alphavantage_news(sectors=None) -> list:
    """
    Fetch sector-based news from Alpha Vantage in a single API call.
    Returns a list of article dicts with sentiment scores.
    """
    if not config.ALPHAVANTAGE_API_KEY:
        log.error(
            "ALPHAVANTAGE_API_KEY not set"
        )  # same cant find api key warning as before
        return []

    if sectors is None:
        sectors = (
            config.SECTORS
        )  # if no sectors were passed in use the 3 specified in config.py

    # Alpha uses specific topic names
    topic_map = {
        "Technology": "technology",  # "translation dictionary" the config names "healthcare" but AlphaVantages api expects "life_sciences"
        "Energy": "energy_transportation",  # just maps our names to their names - can add on more sectors later.
        "Healthcare": "life_sciences",
    }

    # Combine all sector topics into one comma-separated string
    topics = ",".join(
        topic_map.get(s, s.lower()) for s in sectors
    )  # one api call instead of original 3 due to api limits

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "NEWS_SENTIMENT",
                "topics": topics,
                "limit": 50,
                "apikey": config.ALPHAVANTAGE_API_KEY,
            },
            timeout=10,
        )

        if response.status_code != 200:
            log.warning(f"AlphaVantage HTTP {response.status_code}")
            return []

        data = response.json()
        feed = data.get("feed", [])

        all_articles = []
        for item in feed:
            title = item.get("title", "")
            if not title:
                continue

            # Figure out which sector this article belongs to
            article_topics = [t.get("topic", "") for t in item.get("topics", [])]
            sector = "General"
            for s, t in topic_map.items():
                if t in article_topics:
                    sector = s
                    break

            all_articles.append(
                {
                    "title": title,
                    "source": item.get("source", ""),
                    "summary": item.get("summary", "")[:200],
                    "published": item.get("time_published", ""),
                    "sentiment": item.get("overall_sentiment_label", ""),
                    "sentiment_score": item.get("overall_sentiment_score", 0),
                    "sector": sector,
                    "url": item.get("url", ""),
                }
            )

        log.info(
            f"AlphaVantage: fetched {len(all_articles)} articles across all sectors"
        )
        return all_articles

    except Exception as e:
        log.error(f"AlphaVantage fetch failed: {e}")
        return []


# ============================================================
# Combined news fetch
# ============================================================


def fetch_all_news(sectors=None) -> dict:  # fetches and puts in one dict with two keys
    """
    Fetch from both sources and combine into one dict.
    Returns: { "finnhub": [...], "alphavantage": [...] }
    """
    finnhub = fetch_finnhub_news()
    alphavantage = fetch_alphavantage_news(sectors)

    return {
        "finnhub": finnhub,
        "alphavantage": alphavantage,
    }


def save_news(news: dict):
    """Save combined news to cache file!!!"""
    data = {
        "fetched_at": datetime.now().astimezone().isoformat(),
        "news": news,
    }
    with open(NEWS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"News saved to {NEWS_FILE}")


def load_news() -> dict:
    """Load news from cache, other parts of the bot use this to get cached news without re-fetching"""
    try:
        with open(NEWS_FILE, "r") as f:
            data = json.load(f)
        return data.get("news", {})
    except FileNotFoundError:
        return {}


# ============================================================
# claude analysis ->>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# ============================================================


def analyze_with_claude(
    news: dict,
) -> dict:  # takes the combined news dict  and returns claudes analysis as a dict.
    """
    Send combined news to Claude for sector sentiment analysis.
    Returns: { "Technology": { "sentiment": "bullish", ... }, ... }
    """
    finnhub = news.get("finnhub", [])
    alphavantage = news.get("alphavantage", [])
    # Pull out both article lists and count them. If we got
    total = len(finnhub) + len(
        alphavantage
    )  # zero articles from both sources, don't waste an API call, just return empty.
    if total == 0:
        log.warning("No news to analyze")
        return {}

    if not config.ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set")  # key check
        return {}

    # build news text for the prompt, claude will read this!
    news_text = "\n### General Market News (Finnhub)\n"
    for a in finnhub[:10]:  # first section is the 10 finnhub articles
        news_text += f"- [{a['source']}] {a['title']}\n"

    for sector in config.SECTORS:  # second is the 10 AV articles.
        sector_articles = [
            a for a in alphavantage if a.get("sector") == sector
        ]  # this loop cycles through ea sector
        news_text += f"\n### {sector} Sector News (Alpha Vantage)\n"
        if not sector_articles:
            news_text += "No recent news.\n"
        else:
            for a in sector_articles:
                av_sentiment = a.get(
                    "sentiment", ""
                )  # factor in the auto news sentiment given from AV
                news_text += (
                    f"- [{a['source']}] {a['title']} (AV sentiment: {av_sentiment})\n"
                )

    prompt = f"""You are a stock market analyst. Analyze the following news for each sector and the general market.   

Your goal is to achieve {config.MONTHLY_PROFIT_TARGET * 100:.0f}% portfolio growth per month. Pick sectors and stocks that have the best probability of reaching this target. Don't force trades if the setups aren't there. Missing the target is better than losing money chasing it.

For each sector, provide:
1. Overall sentiment: bullish, bearish, or neutral
2. Confidence score: 0.0 to 1.0
3. A 2-3 sentence summary of key themes
4. Top stock ticker to watch in this sector based on the news

Respond ONLY with JSON, no markdown, no extra text:
{{
  "market_overview": {{
    "sentiment": "bullish/bearish/neutral",
    "confidence": 0.0 to 1.0,
    "summary": "..."
  }},
  "Technology": {{
    "sentiment": "bullish/bearish/neutral",
    "confidence": 0.0 to 1.0,
    "summary": "...",
    "top_ticker": "AAPL"
  }},
  "Energy": {{
    "sentiment": "bullish/bearish/neutral",
    "confidence": 0.0 to 1.0,
    "summary": "...",
    "top_ticker": "XOM"
  }},
  "Healthcare": {{
    "sentiment": "bullish/bearish/neutral",
    "confidence": 0.0 to 1.0,
    "summary": "...",
    "top_ticker": "UNH"
  }}
}}

NEWS:
{news_text}
"""
    # ^ just telling claude his job and the way he should respond. tickers arent hardcoded allowing for the top companies to be picked.
    try:
        response = requests.post(  # POST request- sending claude a prompt.
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,  # headers- metadata that goes with the request. xapi is auth, and anthropic vers tells the api which
                "anthropic-version": "2023-06-01",  # ver to use.
                "content-type": "application/json",  # says "Im sending you JSON"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "messages": [
                    {"role": "user", "content": prompt}
                ],  # same as in the .messages documentation for anthropic api-
            },
            timeout=30,
        )

        data = response.json()  # parse claudes respons

        if "error" in data:
            log.error(
                f"Claude API error: {data['error'].get('message', 'unknown')}"
            )  # errors (bad key, no credits,...)
            return {}

        if "content" not in data or not data["content"]:
            log.error(
                f"Unexpected Claude response: {json.dumps(data)[:500]}"
            )  # catches if response isnt correct, not an error but also not right.
            return {}

        raw = data["content"][0][
            "text"
        ].strip()  # get rid of extra crud when printing a response
        log.info(f"Raw Claude response: {raw[:500]}")
        if raw.startswith("```"):  # more to help with JSON formatting craziness
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        analysis = json.loads(raw)  # converting json string into a py dictionary
        log.info("Claude sector analysis complete")
        return analysis

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response: {e}")
        return {}
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        return {}


# catches errors, invalid JSON, markdown stipping failed, network timeouts, connection errors, ...


def save_analysis(analysis: dict):
    """save Claude's analysis to file."""
    data = {
        "analyzed_at": datetime.now().astimezone().isoformat(),
        "analysis": analysis,
    }
    with open(ANALYSIS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Analysis saved to {ANALYSIS_FILE}")


def load_analysis() -> dict:
    """load latest analysis from cache to read, other parts like strategy will use this to get claudes sector opinions"""
    try:
        with open(ANALYSIS_FILE, "r") as f:
            data = json.load(f)
        return data.get("analysis", {})
    except FileNotFoundError:
        return {}


def run_news_cycle(sectors=None) -> dict:
    """
    full cycle: fetch news from both sources -> save -> Claude analysis -> save.
    Returns the analysis dict
    """
    log.info("Starting news cycle...")

    news = fetch_all_news(sectors=sectors)
    save_news(news)

    analysis = analyze_with_claude(news)
    if analysis:
        save_analysis(analysis)

    return analysis


# ============================================================
# standalone test ->>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# ============================================================

# this is for if you run news.py directly !!!

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    print("\n--- News Module Test ---\n")

    analysis = run_news_cycle()

    if analysis:
        print("\n========== CLAUDE SECTOR ANALYSIS ==========")

        overview = analysis.get("market_overview", {})
        print(
            f"\nMARKET: {overview.get('sentiment', 'N/A').upper()} ({overview.get('confidence', 0):.0%})"
        )
        print(f"  {overview.get('summary', '')}")

        for sector in config.SECTORS:
            data = analysis.get(sector, {})
            sentiment = data.get("sentiment", "N/A").upper()
            confidence = data.get("confidence", 0)
            summary = data.get("summary", "")
            ticker = data.get("top_ticker", "N/A")
            print(f"\n{sector}: {sentiment} ({confidence:.0%}) — Top pick: {ticker}")
            print(f"  {summary}")

        print("\n=============================================\n")
    else:
        print("No analysis generated. Check API keys.")
