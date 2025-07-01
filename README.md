# 🛫 KitePilot

**KitePilot** is a personal project that connects Telegram stock tips to Zerodha's Kite Connect API.

It automatically:

- Parses trade signals like:  
  `Buying Eris Life science 1630-1650 Rs (Swing + Short Term)`
- Checks live price via Kite
- Places a LIMIT BUY if price is within ±1%
- Converts filled positions to MTF (Margin Trading Facility)

### ⚙️ Technologies
- Python 3.12
- Telethon
- kiteconnect
- dotenv

> 📌 Educational use only. Not financial advice. Manual token login required daily.

---

## 🚀 Run

```bash
source .venv/bin/activate
python kitepilot.py