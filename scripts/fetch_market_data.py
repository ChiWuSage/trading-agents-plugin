#!/usr/bin/env python3
"""
Usage:
  python scripts/fetch_market_data.py --ticker NVDA --type technical --date 2026-05-02
  python scripts/fetch_market_data.py --ticker NVDA --type news --date 2026-05-02
  python scripts/fetch_market_data.py --ticker NVDA --type fundamentals --date 2026-05-02

Price bars and news come from Alpaca when APCA_API_KEY_ID and APCA_API_SECRET_KEY
(or ALPACA_API_KEY and ALPACA_SECRET_KEY) are set (in the environment or a .env
file at the repo root), otherwise from Yahoo
Finance. Fundamentals always come from Yahoo; Alpaca has no fundamentals data.
"""
import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yfinance as yf
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ALPACA_DATA_URL = "https://data.alpaca.markets"


def _safe(val):
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else round(f, 2)
    except (TypeError, ValueError):
        return None


def _alpaca_keys() -> tuple[str | None, str | None]:
    # Accept both Alpaca's SDK names and the ALPACA_* names people often use.
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    return key, secret


def _alpaca_enabled() -> bool:
    return all(_alpaca_keys())


def _alpaca_get(path: str, params: dict) -> dict:
    url = ALPACA_DATA_URL + path + "?" + urllib.parse.urlencode(params)
    key, secret = _alpaca_keys()
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _alpaca_end(as_of: date) -> str:
    # Free plans may not query SIP data from the last 15 minutes.
    end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), timezone.utc)
    end = min(end, datetime.now(timezone.utc) - timedelta(minutes=16))
    return end.strftime("%Y-%m-%dT%H:%M:%SZ")


def _alpaca_history(ticker: str, start: date, as_of: date) -> pd.DataFrame:
    params = {
        "timeframe": "1Day",
        "start": start.isoformat(),
        "end": _alpaca_end(as_of),
        "adjustment": "all",
        "feed": os.environ.get("ALPACA_DATA_FEED", "sip"),
        "limit": 10000,
    }
    bars = []
    while True:
        page = _alpaca_get(f"/v2/stocks/{urllib.parse.quote(ticker)}/bars", params)
        bars.extend(page.get("bars") or [])
        if not page.get("next_page_token"):
            break
        params["page_token"] = page["next_page_token"]
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df.index = pd.to_datetime(df["t"])
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _history(ticker: str, start: date, as_of: date) -> tuple[pd.DataFrame, str, str | None]:
    alpaca_error = None
    if _alpaca_enabled():
        try:
            hist = _alpaca_history(ticker, start, as_of)
            if not hist.empty:
                return hist, "alpaca", None
            alpaca_error = "no bars returned"
        except Exception as e:
            alpaca_error = str(e)
    end = as_of + timedelta(days=1)
    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    return hist, "yahoo", alpaca_error


def fetch_technical(ticker: str, as_of: date) -> dict:
    start = as_of - timedelta(days=520)
    hist, source, alpaca_error = _history(ticker, start, as_of)
    if hist.empty:
        return {"error": f"No price data for {ticker}", "alpaca_error": alpaca_error}

    close = hist["Close"]
    volume = hist["Volume"]

    ema10 = close.ewm(span=10).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100)

    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()

    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    boll_upper = sma20 + 2 * std20
    boll_lower = sma20 - 2 * std20

    atr_high = hist["High"]
    atr_low = hist["Low"]
    tr = pd.concat([
        atr_high - atr_low,
        (atr_high - close.shift()).abs(),
        (atr_low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    last = hist.index[-1].date().isoformat()
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else None

    result = {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "source": source,
        "last_trading_day": last,
        "price": {
            "current": _safe(close.iloc[-1]),
            "prev_close": _safe(prev_close) if prev_close is not None else None,
            "change_pct": _safe((float(close.iloc[-1]) / prev_close - 1) * 100) if prev_close is not None and prev_close != 0 else None,
            "52w_high": _safe(close.rolling(252).max().iloc[-1]),
            "52w_low": _safe(close.rolling(252).min().iloc[-1]),
        },
        "indicators": {
            "ema10": _safe(ema10.iloc[-1]),
            "sma50": _safe(sma50.iloc[-1]),
            "sma200": _safe(sma200.iloc[-1]),
            "rsi14": _safe(rsi.iloc[-1]),
            "macd": _safe(macd.iloc[-1]),
            "macd_signal": _safe(signal.iloc[-1]),
            "macd_hist": _safe(macd.iloc[-1] - signal.iloc[-1]),
            "boll_upper": _safe(boll_upper.iloc[-1]),
            "boll_mid": _safe(sma20.iloc[-1]),
            "boll_lower": _safe(boll_lower.iloc[-1]),
            "atr14": _safe(atr.iloc[-1]),
        },
        "recent_closes": {
            row.date().isoformat(): _safe(val)
            for row, val in close.tail(10).items()
        },
    }
    if alpaca_error:
        result["alpaca_error"] = alpaca_error
    return result


def _yf_news(symbol: str, limit: int) -> list[dict]:
    items = []
    for n in (yf.Ticker(symbol).news or [])[:limit]:
        c = n.get("content", {})
        items.append({
            "title": c.get("title", n.get("title", "")),
            "summary": c.get("summary", ""),
            "publisher": c.get("provider", {}).get("displayName", ""),
            "published": (c.get("pubDate") or "")[:10],
        })
    return items


def _rss_news(symbol: str, limit: int) -> list[dict]:
    # Yahoo's per-symbol RSS feed; used when yfinance's news endpoint returns
    # nothing (it has broken more than once while the feed kept working).
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode(
        {"s": symbol, "region": "US", "lang": "en-US"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for it in root.iter("item"):
        try:
            published = parsedate_to_datetime(it.findtext("pubDate", "")).date().isoformat()
        except (TypeError, ValueError):
            published = ""
        link = it.findtext("link", "")
        items.append({
            "title": it.findtext("title", ""),
            "summary": it.findtext("description", ""),
            "publisher": urllib.parse.urlparse(link).hostname or "",
            "published": published,
        })
        if len(items) >= limit:
            break
    return items


def _alpaca_news(symbol: str, limit: int, as_of: date) -> list[dict]:
    page = _alpaca_get("/v1beta1/news", {
        "symbols": symbol,
        "start": (as_of - timedelta(days=30)).isoformat(),
        "end": _alpaca_end(as_of),
        "limit": limit,
        "sort": "desc",
    })
    return [{
        "title": n.get("headline", ""),
        "summary": n.get("summary", ""),
        "publisher": n.get("source", ""),
        "published": (n.get("created_at") or "")[:10],
    } for n in page.get("news") or []]


def _news(symbol: str, limit: int, as_of: date, alpaca_symbol: str | None = None) -> tuple[list[dict], str]:
    # Alpaca honours as_of; the Yahoo sources only ever return the latest news.
    if _alpaca_enabled():
        try:
            items = _alpaca_news(alpaca_symbol or symbol, limit, as_of)
            if items:
                return items, "alpaca"
        except Exception:
            pass
    try:
        items = _yf_news(symbol, limit)
    except Exception:
        items = []
    if not items:
        try:
            items = _rss_news(symbol, limit)
        except Exception:
            items = []
    return items, "yahoo"


def fetch_news(ticker: str, as_of: date) -> dict:
    items, source = _news(ticker, 15, as_of)

    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "source": source,
        "news_count": len(items),
        "items": items,
    }


def fetch_macro(as_of: date) -> dict:
    # Pull news from broad market proxies to get global macro context.
    # Alpaca has no index or futures symbols, so it uses ETF stand-ins.
    macro_tickers = {
        "^GSPC": ("S&P 500", "SPY"),
        "^TNX": ("10Y Treasury Yield", "IEF"),
        "GC=F": ("Gold Futures", "GLD"),
        "CL=F": ("Crude Oil Futures", "USO"),
    }
    items = []
    seen = set()
    for sym, (label, alpaca_sym) in macro_tickers.items():
        news, _ = _news(sym, 5, as_of, alpaca_sym)
        for n in news:
            if n["title"] and n["title"] not in seen:
                seen.add(n["title"])
                items.append({"source": label, **n})

    return {
        "as_of": as_of.isoformat(),
        "macro_news_count": len(items),
        "items": items[:20],
    }


def fetch_fundamentals(ticker: str, as_of: date) -> dict:
    tk = yf.Ticker(ticker)
    info = tk.info or {}

    keys = [
        "marketCap", "trailingPE", "forwardPE", "priceToBook",
        "revenueGrowth", "earningsGrowth", "grossMargins", "operatingMargins",
        "profitMargins", "returnOnEquity", "returnOnAssets",
        "totalRevenue", "totalCash", "totalDebt", "freeCashflow",
        "dividendYield", "beta", "shortRatio",
        "recommendationMean", "numberOfAnalystOpinions",
        "targetMeanPrice", "targetHighPrice", "targetLowPrice",
        "sector", "industry", "longBusinessSummary",
    ]
    data = {k: info.get(k) for k in keys}
    data["ticker"] = ticker
    data["as_of"] = as_of.isoformat()
    for k in list(data.keys()):
        if isinstance(data[k], float):
            data[k] = _safe(data[k])

    def _stmt_to_dict(df):
        if df is None or df.empty:
            return {}
        out = {}
        for col in df.columns[:2]:
            label = col.date().isoformat() if hasattr(col, "date") else str(col)
            out[label] = {
                str(idx): (_safe(v) if isinstance(v, float) else v)
                for idx, v in df[col].items()
                if v is not None and not (isinstance(v, float) and math.isnan(v))
            }
        return out

    try:
        data["quarterly_income_stmt"] = _stmt_to_dict(tk.quarterly_income_stmt)
    except Exception:
        data["quarterly_income_stmt"] = {}
    try:
        data["quarterly_balance_sheet"] = _stmt_to_dict(tk.quarterly_balance_sheet)
    except Exception:
        data["quarterly_balance_sheet"] = {}
    try:
        data["quarterly_cashflow"] = _stmt_to_dict(tk.quarterly_cashflow)
    except Exception:
        data["quarterly_cashflow"] = {}

    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--type", required=True, choices=["technical", "news", "fundamentals", "macro"])
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    as_of = date.fromisoformat(args.date)
    fetchers = {
        "technical": fetch_technical,
        "news": fetch_news,
        "fundamentals": fetch_fundamentals,
        "macro": lambda ticker, as_of: fetch_macro(as_of),
    }

    try:
        result = fetchers[args.type](args.ticker, as_of)
    except Exception as e:
        result = {"error": str(e), "ticker": args.ticker, "type": args.type}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
