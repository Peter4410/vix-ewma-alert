#!/usr/bin/env python3
"""
vix_alert.py — Downloads VIX, computes EWMA(λ=0.97) and EWMA(λ=0.95), and sends Telegram message.
"""

import os
import sys
import time
import logging
from datetime import datetime

import yfinance as yf
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

RETRIES = 3
RETRY_DELAY = 5
DEFAULT_START = os.getenv("START_DATE", "2010-01-01")


def fetch_vix(start_date=DEFAULT_START):
    for attempt in range(1, RETRIES + 1):
        try:
            logging.info("Downloading VIX data (start=%s)...", start_date)
            df = yf.download("^VIX", start=start_date, progress=False, auto_adjust=False)
            if df.empty or "Close" not in df.columns:
                raise RuntimeError("VIX data missing 'Close' or empty")
            logging.info("Downloaded %d rows", len(df))
            return df["Close"]
        except Exception as e:
            logging.warning("Attempt %d: failed to download VIX: %s", attempt, e)
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise


def compute_ewma(series, lambda_=0.97):
    alpha = 1 - lambda_
    return series.ewm(alpha=alpha, adjust=False).mean()


def _signal_line(label, vix_val, ewma_val):
    icon = "🟢" if vix_val < ewma_val else "🔴"
    direction = "below" if vix_val < ewma_val else "above"
    return f"{icon} VIX {direction} EWMA(λ={label}): {ewma_val:.2f}"


def create_message(date_str, vix_val, ewma97, ewma95):
    both_below = vix_val < ewma97 and vix_val < ewma95
    summary = (
        "\n✅ Both EWMAs confirm: favorable for short-vol trades (per Sinclair)."
        if both_below
        else "\n⚠️ VIX elevated vs at least one EWMA — exercise caution."
    )
    return (
        f"📅 {date_str}\n"
        f"VIX: {vix_val:.2f}\n\n"
        f"{_signal_line('0.97', vix_val, ewma97)}\n"
        f"{_signal_line('0.95', vix_val, ewma95)}"
        f"{summary}"
    )


def send_telegram(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(url, data=payload, timeout=15)
            r.raise_for_status()
            logging.info("Telegram message sent (status_code=%s)", r.status_code)
            return r.json()
        except Exception as e:
            logging.warning("Attempt %d: failed to send Telegram message: %s", attempt, e)
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise


def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logging.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables")
        sys.exit(2)

    try:
        vix = fetch_vix()
        ewma97 = compute_ewma(vix, lambda_=0.97)
        ewma95 = compute_ewma(vix, lambda_=0.95)
        df = pd.concat([vix, ewma97, ewma95], axis=1)
        df.columns = ["VIX", "EWMA97", "EWMA95"]
        df = df.dropna()
        if df.empty:
            raise RuntimeError("Resulting DataFrame empty after dropping NA")

        latest = df.iloc[-1]
        date_str = latest.name.strftime("%Y-%m-%d")
        vix_val = float(latest["VIX"])
        ewma97_val = float(latest["EWMA97"])
        ewma95_val = float(latest["EWMA95"])

        message = create_message(date_str, vix_val, ewma97_val, ewma95_val)
        logging.info("Prepared message for %s", date_str)

        send_telegram(bot_token, chat_id, message)
        logging.info("Done")

    except Exception as e:
        logging.exception("Unhandled error in vix_alert")
        try:
            send_telegram(bot_token, chat_id, f"⚠️ VIX alert failed: {e}")
        except Exception:
            logging.exception("Also failed to send failure message to Telegram")
        sys.exit(1)


if __name__ == "__main__":
    main()

