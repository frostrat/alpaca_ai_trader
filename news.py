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
