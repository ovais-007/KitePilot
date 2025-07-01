"""
kitepilot.py

KitePilot — automatically executes Telegram trade signals on Zerodha Kite.
Educational use only.  Requires manual Kite login once per trading day.
"""

import asyncio, json, logging, math, os, re, time, difflib
from datetime import datetime
from decimal import Decimal

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from telethon import TelegramClient, events
import functools  # add near other imports
#from resource.populate_symbol_map import find_symbol

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
#TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID")
TG_CHANNEL_ID = int(os.getenv("TG_CHANNEL_ID"))

KITE_API_KEY       = os.getenv("KITE_API_KEY")
KITE_API_SECRET    = os.getenv("KITE_API_SECRET")
KITE_ACCESS_TOKEN  = os.getenv("KITE_ACCESS_TOKEN")

CASH_PER_TRADE     = Decimal(os.getenv("TRADE_CASH_PER_TRADE", "30000"))
BAND_TOL_PCT       = Decimal(os.getenv("PRICE_BAND_TOLERANCE", "1"))  # ±1 %

# ---------------------------- symbol map ----------------------------------

@functools.lru_cache(maxsize=1)
def get_nse_symbols():
    try:
        return {row["tradingsymbol"] for row in kite.instruments("NSE")}
    except Exception as e:
        log.error("Could not fetch instruments list: %s", e)
        return set()

symbol_map_path = os.path.join(os.path.dirname(__file__), 'resource', 'symbol_map.json')
with open(symbol_map_path, encoding="utf-8") as f:
    SYMBOL_MAP = json.load(f)

def find_symbol(company_name, symbol_map):
    key = company_name.strip().upper()

    # 1️⃣ direct map lookup
    if key in symbol_map:
        log.info("Symbol map hit: '%s' -> '%s'", key, symbol_map[key])
        return symbol_map[key]

    # 2️⃣ key itself is already a tradingsymbol?
    if key in get_nse_symbols():
        log.info("Key '%s' is already a valid NSE trading symbol", key)
        symbol_map[key] = key                 # store for next time
        with open(symbol_map_path, "w", encoding="utf-8") as f:
            json.dump(symbol_map, f, indent=2)
        return key

    # 3️⃣ fuzzy match against map keys
    matches = difflib.get_close_matches(key, symbol_map.keys(), n=1, cutoff=0.75)
    if matches:
        matched = matches[0]
        print(f"⚠️ Unknown '{key}'. Use closest match '{matched}'? [Y/n/m for manual]: ", end="")
        user_input = input().strip().lower()
        if user_input in ("", "y", "yes"):
            symbol_map[key] = symbol_map[matched]
            with open(symbol_map_path, "w", encoding="utf-8") as f:
                json.dump(symbol_map, f, indent=2)
            return symbol_map[matched]
        elif user_input == "m":
            manual = input(f"Enter trading symbol for '{key}': ").strip().upper()
            if manual:
                symbol_map[key] = manual
                with open(symbol_map_path, "w", encoding="utf-8") as f:
                    json.dump(symbol_map, f, indent=2)
                return manual

    # 4️⃣ final manual input if no fuzzy match
    manual = input(f"⚠️ No match for '{key}'. Enter trading symbol manually (or leave blank to skip): ").strip().upper()
    if manual:
        symbol_map[key] = manual
        with open(symbol_map_path, "w", encoding="utf-8") as f:
            json.dump(symbol_map, f, indent=2)
        return manual

    return None

# ---------------------------- regex parser --------------------------------
SIGNAL_RE = re.compile(
    r"""(?i)
    (?:buy(?:ing)?|fresh\s*buying|again\s*in\s*buying\s*range|buy\s*range|buy\s*of|buy\s*at|buy\s*now|buy\s*range|new\s*members\s*can\s*buy)[^\n]*?
    ([A-Za-z0-9 .&'\-]+?)\s*[:\-]?\s*
    (\d{2,5})[\-\\—:](\d{2,5})[^\n]*?
    (?:\n|.)*?
    (?:stop\s*loss|sl)[^\d]*(\d{2,5})
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE
)

# ---------------------------- Kite helpers --------------------------------
kite = KiteConnect(api_key=KITE_API_KEY)
kite.set_access_token(KITE_ACCESS_TOKEN)

def get_ltp(symbol: str) -> Decimal:
    data = kite.ltp([f"NSE:{symbol}"])
    return Decimal(str(data[f"NSE:{symbol}"]["last_price"]))

def qty_for_cash(price: Decimal) -> int:
    return math.floor(CASH_PER_TRADE / price)

def place_market_buy(symbol: str, qty: int) -> str:
    log.info("Placing MARKET BUY %s %s (MTF)", symbol, qty)
    return kite.place_order(
        variety=kite.VARIETY_REGULAR,
        exchange=kite.EXCHANGE_NSE,
        tradingsymbol=symbol,
        transaction_type=kite.TRANSACTION_TYPE_BUY,
        quantity=qty,
        order_type=kite.ORDER_TYPE_MARKET,
        product=kite.PRODUCT_MTF,
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

# ---------------------------- Telegram handler ----------------------------
client = TelegramClient("kitepilot.session", TG_API_ID, TG_API_HASH)

#@client.on(events.NewMessage)
async def handle(event):
    print("event.chat_id:", event.chat_id, "TG_CHANNEL_ID:", TG_CHANNEL_ID)
    if event.chat_id != TG_CHANNEL_ID:
        print("Wrong chat, skipping")
        return

    txt = event.raw_text
    m = SIGNAL_RE.search(txt)
    if not m:
        print("❌ Regex did not match this message:")
        print(txt)
        return

    name, lo, hi, _sl = m.groups()
    lo, hi = Decimal(lo), Decimal(hi)
    

    symbol = find_symbol(name, SYMBOL_MAP)
    if not symbol:
        log.warning("Unknown map for '%s'", name)
        return
    
    log.info("📈 Signal for %s: Buy range %s %s", symbol, lo, hi)

    try:
        ltp = get_ltp(symbol)
    except Exception as e:
        log.error("❌ Failed to fetch LTP for %s: %s", symbol, e)
        return
    
    upper_allowed = hi * (1 + BAND_TOL_PCT / Decimal(100))
    if ltp > upper_allowed:
        log.info(
            "⛔ %s LTP %.2f is above high limit %.2f + %.1f%%, skipping",
            symbol, ltp, hi, BAND_TOL_PCT
        )
        return

    qty = qty_for_cash(ltp)
    if qty > 0:
        oid = place_market_buy(symbol, qty)
        if wait_till_filled(oid):
            log.info("✅ Trade filled for %s: %s shares at %.2f", symbol, qty, ltp)
    else:
        log.info("⚠️ Not enough cash to buy even 1 share of %s at %.2f", symbol, ltp)

async def main():
    await client.start()
    log.info("🛫 KitePilot listening to chat ID %s ...", TG_CHANNEL_ID)
    client.add_event_handler(handle,events.NewMessage(chats=TG_CHANNEL_ID)   )
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
