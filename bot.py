import os
import sys
import time
import logging
from datetime import datetime, timezone
import ccxt
import pandas as pd
from telegram import Bot
from telegram.error import TelegramError
import asyncio

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = [
    "XRP/USDT",
    "SOL/USDT",
    "LINK/USDT",
    "SHIB/USDT",
    "WIF/USDT",
    "BOME/USDT",
    "PEPE/USDT",
    "ENS/USDT",
    "NEIRO/USDT",
    "TRUMP/USDT",
    "ARPA/USDT",
    "CELR/USDT",
    "DYDX/USDT",
    "GALA/USDT",
    "GAS/USDT",
    "ICP/USDT",
    "IMX/USDT",
    "LPT/USDT",
    "MTL/USDT",
    "OP/USDT",
    "SPELL/USDT",
    "STPT/USDT",
    "VET/USDT",
    "WLD/USDT",
    "GRT/USDT",
    "PAXG/USDT",
    "XAUT/USDT",
]

TIMEFRAME = "15m"
EMA_FAST = 9
EMA_SLOW = 21
VOLUME_MA_PERIOD = 20
MIN_BARS_BETWEEN_SIGNALS = 4
CHECK_INTERVAL = 50
# ============================================

# Send logs to stdout so Railway shows INFO as normal logs (not "error")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Stop httpx from logging full request URLs (they contain the bot token)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

bot = Bot(token=TELEGRAM_TOKEN)
exchange = ccxt.binance({"enableRateLimit": True})

last_signal = {symbol: None for symbol in SYMBOLS}

def get_ohlcv(symbol):
    ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def calculate_indicators(df):
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(window=VOLUME_MA_PERIOD).mean()
    return df

def check_signal(df):
    if len(df) < max(EMA_SLOW, VOLUME_MA_PERIOD) + 5:
        return None, None

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    if pd.isna(curr["vol_ma"]) or curr["volume"] <= curr["vol_ma"]:
        return None, None

    cross_up = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    if cross_up:
        return "BUY", curr
    if cross_down:
        return "SELL", curr
    return None, None

def can_send_signal(symbol, current_bar_time):
    last = last_signal[symbol]
    if last is None:
        return True
    bars_passed = (current_bar_time - last).total_seconds() / (15 * 60)
    return bars_passed >= MIN_BARS_BETWEEN_SIGNALS

async def send_alert(symbol, signal_type, candle):
    price = candle["close"]
    time_str = candle["timestamp"].strftime("%H:%M UTC")
    coin = symbol.replace("/USDT", "")

    if signal_type == "BUY":
        msg = (
            f"🟢 <b>{coin} BUY CONFIRMED</b>\n"
            f"15m • Entry around <b>${price:.6f}</b>\n"
            f"Time: {time_str}"
        )
    else:
        msg = (
            f"🔴 <b>{coin} SELL CONFIRMED</b>\n"
            f"15m • Price around <b>${price:.6f}</b>\n"
            f"Time: {time_str}"
        )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
        logger.info(f"Alert sent: {symbol} {signal_type} @ {price}")
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")

async def log_known_chats():
    """If sending fails, log the chat IDs that have messaged this bot."""
    try:
        updates = await bot.get_updates(limit=50)
        seen = {}
        for u in updates:
            msg = u.message or u.edited_message or u.channel_post
            if msg:
                chat = msg.chat
                seen[chat.id] = chat.first_name or chat.title or chat.username or ""
        if seen:
            for cid, name in seen.items():
                logger.info(f"CHAT ID FOUND: {cid} ({name}) -> put this number in CHAT_ID")
        else:
            logger.info("No messages found for this bot. Send any message to the bot in Telegram, then redeploy.")
    except Exception as e:
        logger.error(f"Could not read chat IDs: {e}")

async def main_loop():
    logger.info(f"Bot started. Monitoring {len(SYMBOLS)} pairs | EMA 9/21 + Volume filter")

    # One-time startup message to confirm Telegram delivery works
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"✅ <b>Crypto Alert Bot started</b>\nMonitoring {len(SYMBOLS)} pairs • 15m • EMA 9/21 + Volume filter",
            parse_mode="HTML",
        )
        logger.info("Startup message sent to Telegram")
    except TelegramError as e:
        logger.error(f"Telegram startup message failed: {e}")
        await log_known_chats()

    while True:
        for symbol in SYMBOLS:
            try:
                df = get_ohlcv(symbol)
                df = calculate_indicators(df)

                signal, candle = check_signal(df)

                if signal and can_send_signal(symbol, candle["timestamp"]):
                    await send_alert(symbol, signal, candle)
                    last_signal[symbol] = candle["timestamp"]

            except Exception as e:
                logger.error(f"Error on {symbol}: {e}")

            await asyncio.sleep(1.2)

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main_loop())
