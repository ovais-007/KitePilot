"""
kitepilot.py

KitePilot — automatically executes Telegram trade signals on Zerodha Kite.
Educational use only.  Requires manual Kite login once per trading day.
"""

import asyncio, json, logging, math, os, re, time
from datetime import datetime
from decimal import Decimal

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from telethon import TelegramClient, events

# ---------------------------- config & logging ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("KitePilot")

load_dotenv()                                   # read .env

TG_API_ID          = int(os.getenv("TG_API_ID"))
TG_API_HASH        = os.getenv("TG_API_HASH")
TG_CHANNEL         = os.getenv("TG_CHANNEL_USERNAME")   # e.g. @mychannel

KITE_API_KEY       = os.getenv("KITE_API_KEY")
KITE_API_SECRET    = os.getenv("KITE_API_SECRET")
KITE_ACCESS_TOKEN  = os.getenv("KITE_ACCESS_TOKEN")

CASH_PER_TRADE     = Decimal(os.getenv("TRADE_CASH_PER_TRADE", "30000"))
BAND_TOL_PCT       = Decimal(os.getenv("PRICE_BAND_TOLERANCE", "1"))  # ±1 %

# ---------------------------- symbol map ----------------------------------
with open("symbol_map.json", encoding="utf-8") as f:
    SYMBOL_MAP = json.load(f)

# ---------------------------- regex parser --------------------------------
SIGNAL_RE = re.compile(
    r"Buying\\s+([A-Za-z0-9 .&'-]+?)\\s+(\\d+)[-–](\\d+).*?Stop\\s+Loss\\s*[:-]\\s*(\\d+)",
    re.IGNORECASE | re.DOTALL
)

# ---------------------------- Kite helpers --------------------------------
kite = KiteConnect(api_key=KITE_API_KEY)
kite.set_access_token(KITE_ACCESS_TOKEN)

def get_ltp(symbol: str) -> Decimal:
    data = kite.ltp([f"NSE:{symbol}"])
    return Decimal(str(data[f"NSE:{symbol}"]["last_price"]))

def qty_for_cash(price: Decimal) -> int:
    return math.floor(CASH_PER_TRADE / price)

def place_limit_buy(symbol: str, price: Decimal, qty: int) -> str:
    log.info("Placing BUY %s %s@%s", symbol, qty, price)
    return kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange=kite.EXCHANGE_NSE,
        tradingsymbol=symbol,
        transaction_type=kite.TRANSACTION_TYPE_BUY,
        quantity=qty,
        price=float(price),
        order_type=kite.ORDER_TYPE_LIMIT,
        product=kite.PRODUCT_CNC,
        validity=kite.VALIDITY_DAY,
        tag="KitePilot"
    )

def wait_till_filled(order_id: str, timeout: float = 300.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        for o in kite.orders():
            if o["order_id"] == order_id:
                if o["status"] == "COMPLETE":
                    log.info("Order %s filled", order_id)
                    return True
                if o["status"] in ("REJECTED", "CANCELLED"):
                    log.warning("Order %s %s", order_id, o["status"])
                    return False
        time.sleep(2)
    log.warning("Timeout waiting for order %s", order_id)
    return False

def convert_to_mtf(symbol: str, qty: int):
    try:
        kite.convert_position(
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            position_type="day",
            quantity=qty,
            old_product=kite.PRODUCT_CNC,
            new_product=kite.PRODUCT_MTF
        )
        log.info("Converted to MTF")
    except Exception as e:
        log.error("MTF conversion failed: %s", e)

# ---------------------------- Telegram handler ----------------------------
client = TelegramClient("kitepilot.session", TG_API_ID, TG_API_HASH)

async def handle(event):
    if TG_CHANNEL.lstrip("@").lower() not in (event.chat.username or "").lower():
        return                                                # wrong chat
    txt = event.raw_text
    m = SIGNAL_RE.search(txt)
    if not m:
        return
    name, lo, hi, _sl = m.groups()
    lo, hi = Decimal(lo), Decimal(hi)
    symbol = SYMBOL_MAP.get(name.strip().upper())
    if not symbol:
        log.warning("Unknown map for '%s'", name)
        return

    ltp = get_ltp(symbol)
    mid = (lo + hi) / 2
    tol = mid * BAND_TOL_PCT / 100
    if not (mid - tol <= ltp <= mid + tol):
        log.info("%s price %.2f out of ±%.1f%% band, skip", symbol, ltp, BAND_TOL_PCT)
        return

    qty = qty_for_cash(ltp)
    if qty <= 0:
        log.info("Qty zero, skip")
        return

    oid = place_limit_buy(symbol, ltp, qty)
    if wait_till_filled(oid):
        convert_to_mtf(symbol, qty)

async def main():
    await client.start()
    log.info("🛫 KitePilot listening to %s ...", TG_CHANNEL)
    client.add_event_handler(handle, events.NewMessage(chats=TG_CHANNEL))
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())