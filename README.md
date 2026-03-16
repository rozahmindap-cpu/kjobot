# KJO Signal Bot

Auto-scan trading signal bot based on KJO Academy VIP method.

## Strategy
- **Indicators:** MA(7/25/99/200 SMA), MACD(12,26,9), RSI(14), Stochastic(14,3,3), Supertrend(10,3)
- **Patterns:** Trendline Break, Double Bottom/Top, Demand/Supply Zone, Order Block, Triangle, Head & Shoulders, Accumulation
- **Structure:** HH/HL/LH/LL Market Structure, Fake Breakout Filter, Volume Profile (POC/VAH/VAL)
- **Macro:** BTC Dom, USDT Dom, TOTAL3, ETH/BTC Ratio
- **Timeframes:** Weekly + Daily + 4H + 1H (multi-TF alignment)
- **Pairs:** BTC, ETH, SOL, BNB, SUI, WLD, BCH, JTO, XRP, LINK, TRB, SEI

## Install
```bash
pip install -r requirements.txt
python kjo_bot.py
```

## Features
- Auto-scan every 15 minutes
- Telegram signal notifications
- 4-hour cooldown per pair (no spam)
- Market summary every 4 hours
