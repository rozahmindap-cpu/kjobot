"""
KJO Signal Bot - Niru metode analisa KJO Academy VIP
Auto-scan market → kirim sinyal ke Telegram MK

Strategy dari KJO Playbook:
- Coins: BTC, ETH, SOL, BNB, SUI, WLD, HYPE, BCH, JTO
- TF: Weekly (bias) + Daily (macro) + 4H (entry) + 1H (timing)
- Patterns: Trendline Break, Support/Resistance, Double Bottom/Top,
            Demand/Supply Zone, Order Block, Triangle, H&S, Accumulation
- Indicators: MA(7,25,99,200) SMA, Volume, MACD(12,26,9), RSI(14), Stochastic(14,3,3), Supertrend(10,3)
- Macro: BTC Dom + USDT Dom + TOTAL3 + ETH/BTC Ratio
- Risk: Wajib SL, TP bertahap (TP1→TP2→Full TP)
- Features: Market Structure (HH/HL/LH/LL), Fake Breakout Filter, Volume Profile, Accumulation Zone
"""

import ccxt
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime

# ==================== CONFIG ====================
TELEGRAM_BOT_TOKEN = "8099113405:AAFrVLJIULOgyTh0WFAdEA0bkWmRFdihmJY"
TELEGRAM_CHAT_ID = "1603606771"  # Chat ID MK

SCAN_INTERVAL = 60   # cek tiap 1 menit, tapi scan hanya di candle close
SIGNAL_COOLDOWN = 14400  # 4 jam per pair (biar ga spam)

# Jam close candle 4H (WIB = UTC+7)
# Binance 4H candle close: 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 WIB
CANDLE_4H_CLOSE_HOURS_WIB = [1, 5, 9, 13, 17, 21]
SCAN_WINDOW_MINUTES = 5  # scan dalam 5 menit pertama setelah candle close

# Coins KJO sering analisa (dari playbook)
WATCHLIST = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 
    'SUI/USDT', 'WLD/USDT', 'BCH/USDT', 'JTO/USDT',
    'XRP/USDT', 'LINK/USDT', 'TRB/USDT', 'SEI/USDT'
]

# Timeframes untuk analisa (KJO style)
TIMEFRAMES = {
    '1w': 'Weekly',
    '1d': 'Daily',
    '4h': '4H', 
    '1h': '1H',
    '15m': '15M'
}

# Signal tracking (hindari spam)
last_signal = {}

# ==================== EXCHANGE ====================
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# ==================== INDICATORS ====================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(window=period).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def stochastic(high, low, close, k_period=14, d_period=3, smooth_k=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k_raw = 100 * (close - lowest_low) / (highest_high - lowest_low)
    k = k_raw.rolling(window=smooth_k).mean()  # smooth K
    d = k.rolling(window=d_period).mean()
    return k, d

def supertrend(high, low, close, period=10, multiplier=3):
    """Supertrend indicator (10, 3)"""
    hl2 = (high + low) / 2
    atr_val = (high - low).rolling(window=period).mean()
    
    upper_band = hl2 + (multiplier * atr_val)
    lower_band = hl2 - (multiplier * atr_val)
    
    supertrend_vals = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(index=close.index, dtype=int)
    
    for i in range(period, len(close)):
        if i == period:
            supertrend_vals.iloc[i] = lower_band.iloc[i]
            direction.iloc[i] = 1
            continue
        
        prev_st = supertrend_vals.iloc[i-1]
        prev_dir = direction.iloc[i-1]
        
        curr_upper = upper_band.iloc[i]
        curr_lower = lower_band.iloc[i]
        
        if prev_dir == 1:
            curr_st = max(curr_lower, prev_st) if close.iloc[i] >= prev_st else curr_upper
            direction.iloc[i] = 1 if close.iloc[i] >= curr_st else -1
        else:
            curr_st = min(curr_upper, prev_st) if close.iloc[i] <= prev_st else curr_lower
            direction.iloc[i] = -1 if close.iloc[i] <= curr_st else 1
        
        supertrend_vals.iloc[i] = curr_st
    
    return supertrend_vals, direction

def detect_support_resistance(df, lookback=20):
    """Simple S/R detection dari recent highs/lows"""
    recent = df.tail(lookback)
    support = recent['low'].min()
    resistance = recent['high'].max()
    
    pivots_high = []
    pivots_low = []
    
    for i in range(2, len(df) - 2):
        if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i-2] and \
           df['high'].iloc[i] > df['high'].iloc[i+1] and df['high'].iloc[i] > df['high'].iloc[i+2]:
            pivots_high.append(df['high'].iloc[i])
        if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i-2] and \
           df['low'].iloc[i] < df['low'].iloc[i+1] and df['low'].iloc[i] < df['low'].iloc[i+2]:
            pivots_low.append(df['low'].iloc[i])
    
    key_resistance = sorted(pivots_high, reverse=True)[:3] if pivots_high else [resistance]
    key_support = sorted(pivots_low)[:3] if pivots_low else [support]
    
    return key_support, key_resistance

def detect_trendline_break(df):
    """Detect trendline break - pattern #1 KJO"""
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    n = len(closes)
    
    if n < 20:
        return None, None
    
    recent_lows = lows[-20:]
    recent_highs = highs[-20:]
    
    upper_band = np.max(recent_highs[:-3])
    lower_band = np.min(recent_lows[:-3])
    current = closes[-1]
    prev = closes[-2]
    
    if prev <= upper_band and current > upper_band:
        return 'BREAKOUT', upper_band
    elif prev >= lower_band and current < lower_band:
        return 'BREAKDOWN', lower_band
    
    return None, None

# ==================== NEW FEATURE DETECTORS ====================

def detect_double_bottom_top(df, lookback=50):
    """
    Feature 1: Double Bottom / Double Top Detection
    - Double Bottom: 2 similar lows (within 1.5%) separated by a peak → bullish +25
    - Double Top: 2 similar highs (within 1.5%) separated by a trough → bearish +25
    """
    result = {'pattern': None, 'score': 0, 'label': None}
    
    if len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    lows = data['low'].values
    highs = data['high'].values
    
    # Find local lows (pivot lows)
    pivot_lows = []
    pivot_highs = []
    for i in range(2, len(data) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            pivot_lows.append((i, lows[i]))
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            pivot_highs.append((i, highs[i]))
    
    # Check Double Bottom: two similar lows separated by a peak
    if len(pivot_lows) >= 2:
        for i in range(len(pivot_lows) - 1):
            idx1, low1 = pivot_lows[i]
            idx2, low2 = pivot_lows[i+1]
            if idx2 - idx1 < 5:
                continue
            diff = abs(low1 - low2) / max(low1, low2)
            if diff <= 0.015:
                # Check there's a peak between them
                mid_highs = [h for idx, h in pivot_highs if idx1 < idx < idx2]
                if mid_highs:
                    result = {'pattern': 'DOUBLE_BOTTOM', 'score': 25, 'label': '🔵 Double Bottom (Bullish)'}
                    return result
    
    # Check Double Top: two similar highs separated by a trough
    if len(pivot_highs) >= 2:
        for i in range(len(pivot_highs) - 1):
            idx1, high1 = pivot_highs[i]
            idx2, high2 = pivot_highs[i+1]
            if idx2 - idx1 < 5:
                continue
            diff = abs(high1 - high2) / max(high1, high2)
            if diff <= 0.015:
                mid_lows = [l for idx, l in pivot_lows if idx1 < idx < idx2]
                if mid_lows:
                    result = {'pattern': 'DOUBLE_TOP', 'score': 25, 'label': '🔴 Double Top (Bearish)'}
                    return result
    
    return result

def detect_demand_supply_zones(df):
    """
    Feature 2: Demand / Supply Zone Detection
    - Demand zone: 3+ small body candles before large up impulse
    - Supply zone: 3+ small body candles before large down impulse
    - Current price near zone (+/- 1%) → +20 to score
    """
    result = {'zones': [], 'score': 0, 'label': None}
    
    if len(df) < 20:
        return result
    
    close = df['close'].values
    open_ = df['open'].values
    high = df['high'].values
    low = df['low'].values
    curr_price = close[-1]
    
    avg_body = np.mean(np.abs(close - open_))
    
    zones = []
    for i in range(3, len(df) - 1):
        # Check for 3+ small body candles
        small_bodies = all(
            abs(close[j] - open_[j]) < avg_body * 0.5
            for j in range(i-3, i)
        )
        if not small_bodies:
            continue
        
        zone_mid = (high[i-1] + low[i-1]) / 2
        next_move = (close[i] - open_[i]) / open_[i]
        
        if next_move > 0.03:  # Large up move
            zones.append(('DEMAND', zone_mid, high[i-1], low[i-1]))
        elif next_move < -0.03:  # Large down move
            zones.append(('SUPPLY', zone_mid, high[i-1], low[i-1]))
    
    # Check if current price is near any zone
    for zone_type, zone_mid, zone_high, zone_low in zones[-5:]:
        proximity = abs(curr_price - zone_mid) / zone_mid
        if proximity <= 0.01:
            if zone_type == 'DEMAND':
                result['score'] += 20
                result['label'] = f'🟦 Near Demand Zone ~${zone_mid:,.4f}'
                result['zones'].append(f'Demand ~${zone_mid:,.2f}')
            else:
                result['score'] += 20
                result['label'] = f'🟥 Near Supply Zone ~${zone_mid:,.4f}'
                result['zones'].append(f'Supply ~${zone_mid:,.2f}')
    
    return result

def detect_order_blocks(df):
    """
    Feature 3: Order Block Detection
    - Bullish OB: last bearish candle before 3%+ up move
    - Bearish OB: last bullish candle before 3%+ down move
    - Price retesting OB → +20 to score
    """
    result = {'obs': [], 'score': 0, 'label': None}
    
    if len(df) < 10:
        return result
    
    close = df['close'].values
    open_ = df['open'].values
    high = df['high'].values
    low = df['low'].values
    curr_price = close[-1]
    
    order_blocks = []
    
    for i in range(1, len(df) - 3):
        # Look for large move after candle i
        move_pct = (close[i+2] - close[i]) / close[i]
        
        if move_pct > 0.03:  # Up move of 3%+
            # Find last bearish candle before move
            if close[i] < open_[i]:  # Bearish candle
                order_blocks.append(('BULLISH_OB', high[i], low[i], (high[i]+low[i])/2))
        
        elif move_pct < -0.03:  # Down move of 3%+
            # Find last bullish candle before move
            if close[i] > open_[i]:  # Bullish candle
                order_blocks.append(('BEARISH_OB', high[i], low[i], (high[i]+low[i])/2))
    
    # Check if current price is retesting any OB
    for ob_type, ob_high, ob_low, ob_mid in order_blocks[-5:]:
        if ob_low <= curr_price <= ob_high:
            if ob_type == 'BULLISH_OB':
                result['score'] += 20
                result['label'] = f'🟩 Retesting Bullish OB ${ob_mid:,.4f}'
                result['obs'].append(f'Bullish OB ${ob_mid:,.2f}')
            else:
                result['score'] += 20
                result['label'] = f'🟧 Retesting Bearish OB ${ob_mid:,.4f}'
                result['obs'].append(f'Bearish OB ${ob_mid:,.2f}')
    
    return result

def detect_triangles(df, lookback=30):
    """
    Feature 4: Ascending / Descending Triangle Detection
    - Ascending: flat resistance + rising lows → bullish +20
    - Descending: flat support + falling highs → bearish +20
    """
    result = {'pattern': None, 'score': 0, 'label': None}
    
    if len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    highs = data['high'].values
    lows = data['low'].values
    n = len(highs)
    
    # Check Ascending Triangle: flat top + rising lows
    recent_highs = highs[-10:]
    high_range = (max(recent_highs) - min(recent_highs)) / max(recent_highs)
    
    # Flat top: range within 1.5%
    if high_range < 0.015:
        # Check rising lows using linear regression
        low_indices = np.arange(n//2, n)
        recent_lows = lows[n//2:]
        if len(recent_lows) > 3:
            slope = np.polyfit(low_indices, recent_lows, 1)[0]
            if slope > 0:
                result = {'pattern': 'ASCENDING_TRIANGLE', 'score': 20, 'label': '📐 Ascending Triangle (Bullish)'}
                return result
    
    # Check Descending Triangle: flat bottom + falling highs
    recent_lows = lows[-10:]
    low_range = (max(recent_lows) - min(recent_lows)) / max(recent_lows)
    
    if low_range < 0.015:
        high_indices = np.arange(n//2, n)
        recent_highs2 = highs[n//2:]
        if len(recent_highs2) > 3:
            slope = np.polyfit(high_indices, recent_highs2, 1)[0]
            if slope < 0:
                result = {'pattern': 'DESCENDING_TRIANGLE', 'score': 20, 'label': '📐 Descending Triangle (Bearish)'}
                return result
    
    return result

def detect_head_and_shoulders(df, lookback=60):
    """
    Feature 5: Head & Shoulders / Inverse H&S
    - H&S: bearish reversal → +25
    - IH&S: bullish reversal → +25
    Using pivot points in last 60 candles
    """
    result = {'pattern': None, 'score': 0, 'label': None}
    
    if len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    highs = data['high'].values
    lows = data['low'].values
    
    # Find pivot highs (for H&S)
    pivot_highs = []
    pivot_lows = []
    for i in range(3, len(data) - 3):
        if all(highs[i] > highs[i-j] and highs[i] > highs[i+j] for j in range(1, 4)):
            pivot_highs.append((i, highs[i]))
        if all(lows[i] < lows[i-j] and lows[i] < lows[i+j] for j in range(1, 4)):
            pivot_lows.append((i, lows[i]))
    
    # H&S: 3 peaks, middle highest
    if len(pivot_highs) >= 3:
        for i in range(len(pivot_highs) - 2):
            ls_idx, ls = pivot_highs[i]
            h_idx, h = pivot_highs[i+1]
            rs_idx, rs = pivot_highs[i+2]
            
            if h_idx <= ls_idx or rs_idx <= h_idx:
                continue
            
            # Head must be higher than shoulders
            if h > ls * 1.02 and h > rs * 1.02:
                # Shoulders roughly equal (within 3%)
                shoulder_diff = abs(ls - rs) / max(ls, rs)
                if shoulder_diff < 0.03:
                    result = {'pattern': 'HEAD_SHOULDERS', 'score': 25, 'label': '🔻 Head & Shoulders (Bearish)'}
                    return result
    
    # IH&S: 3 troughs, middle lowest
    if len(pivot_lows) >= 3:
        for i in range(len(pivot_lows) - 2):
            ls_idx, ls = pivot_lows[i]
            h_idx, h = pivot_lows[i+1]
            rs_idx, rs = pivot_lows[i+2]
            
            if h_idx <= ls_idx or rs_idx <= h_idx:
                continue
            
            # Head (lowest) must be lower than shoulders
            if h < ls * 0.98 and h < rs * 0.98:
                shoulder_diff = abs(ls - rs) / max(ls, rs)
                if shoulder_diff < 0.03:
                    result = {'pattern': 'INV_HEAD_SHOULDERS', 'score': 25, 'label': '🔺 Inverse H&S (Bullish)'}
                    return result
    
    return result

def detect_accumulation(df, lookback=15):
    """
    Feature 9: Accumulation Detection
    - Sideways + low ATR over 10+ candles + declining volume → accumulation
    - Volume spike after → breakout signal +20
    """
    result = {'detected': False, 'score': 0, 'label': None}
    
    if len(df) < lookback + 5:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    close = data['close'].values
    volume = data['volume'].values
    high = data['high'].values
    low = data['low'].values
    
    # ATR
    atr = np.mean(high - low)
    price_range = (max(close) - min(close)) / np.mean(close)
    
    # Sideways: price range < 3%
    if price_range > 0.03:
        return result
    
    # Declining volume: compare first half vs second half
    mid = len(volume) // 2
    vol_first = np.mean(volume[:mid])
    vol_second = np.mean(volume[mid:])
    
    # Last candle volume spike
    recent_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].tail(20).mean()
    
    if vol_second < vol_first * 0.9:  # Declining volume
        if recent_vol > avg_vol * 1.5:
            # Volume spike = breakout signal
            result = {'detected': True, 'score': 20, 'label': '⚡ Accumulation + Volume Spike (Breakout!)'}
        else:
            result = {'detected': True, 'score': 10, 'label': '🟡 Accumulation Zone (watch for breakout)'}
    
    return result

def detect_market_structure(df, lookback=30):
    """
    Feature 10: Higher High / Lower Low Market Structure
    - HH+HL = Uptrend +15
    - LH+LL = Downtrend +15
    - Otherwise = Ranging
    """
    result = {'structure': 'RANGING', 'score': 0, 'label': '↔️ Market Structure: Ranging'}
    
    if len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    highs = data['high'].values
    lows = data['low'].values
    
    # Find swing highs and lows
    swing_highs = []
    swing_lows = []
    
    for i in range(2, len(data) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return result
    
    # Check last 2 swing highs and lows
    hh = swing_highs[-1] > swing_highs[-2]  # Higher High
    hl = swing_lows[-1] > swing_lows[-2]    # Higher Low
    lh = swing_highs[-1] < swing_highs[-2]  # Lower High
    ll = swing_lows[-1] < swing_lows[-2]    # Lower Low
    
    if hh and hl:
        result = {'structure': 'UPTREND', 'score': 15, 'label': '📈 Market Structure: Uptrend (HH+HL)'}
    elif lh and ll:
        result = {'structure': 'DOWNTREND', 'score': 15, 'label': '📉 Market Structure: Downtrend (LH+LL)'}
    else:
        result = {'structure': 'RANGING', 'score': 0, 'label': '↔️ Market Structure: Ranging'}
    
    return result

def check_fake_breakout(df, break_type):
    """
    Feature 11: Fake Breakout Filter
    - Breakout valid only if volume > 1.5x average
    - If volume < 1.2x → "⚠️ Possible fake breakout" + reduce score by 15
    """
    result = {'is_fake': False, 'score_adj': 0, 'label': None}
    
    if break_type is None:
        return result
    
    recent_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].tail(20).mean()
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
    
    if vol_ratio >= 1.5:
        result = {'is_fake': False, 'score_adj': 0, 'label': f'✅ Breakout confirmed (vol {vol_ratio:.1f}x)'}
    elif vol_ratio < 1.2:
        result = {'is_fake': True, 'score_adj': -15, 'label': f'⚠️ Possible fake breakout (vol only {vol_ratio:.1f}x avg)'}
    else:
        result = {'is_fake': False, 'score_adj': 0, 'label': f'⚠️ Breakout weak volume ({vol_ratio:.1f}x)'}
    
    return result

def detect_volume_profile(df, lookback=50, value_area_pct=0.70):
    """
    Feature 12: Simplified Volume Profile
    - POC = price level with highest volume in last 50 candles
    - VAH/VAL = 70% volume range
    - Price near POC / breaking VAH/VAL → +10
    """
    result = {'poc': None, 'vah': None, 'val': None, 'score': 0, 'label': None}
    
    if len(df) < lookback:
        return result
    
    data = df.tail(lookback).reset_index(drop=True)
    
    # Create price buckets
    price_min = data['low'].min()
    price_max = data['high'].max()
    num_buckets = 50
    bucket_size = (price_max - price_min) / num_buckets
    
    if bucket_size <= 0:
        return result
    
    # Assign volume to price buckets
    vol_profile = np.zeros(num_buckets)
    price_levels = np.linspace(price_min, price_max, num_buckets)
    
    for _, row in data.iterrows():
        # Distribute volume proportionally across candle range
        candle_range = row['high'] - row['low']
        if candle_range <= 0:
            continue
        
        low_bucket = max(0, int((row['low'] - price_min) / bucket_size))
        high_bucket = min(num_buckets - 1, int((row['high'] - price_min) / bucket_size))
        
        buckets_covered = high_bucket - low_bucket + 1
        vol_per_bucket = row['volume'] / buckets_covered if buckets_covered > 0 else row['volume']
        
        for b in range(low_bucket, high_bucket + 1):
            if 0 <= b < num_buckets:
                vol_profile[b] += vol_per_bucket
    
    # POC = highest volume bucket
    poc_idx = np.argmax(vol_profile)
    poc_price = price_levels[poc_idx]
    
    # Value Area (70% of total volume around POC)
    total_vol = np.sum(vol_profile)
    target_vol = total_vol * value_area_pct
    
    va_low_idx = poc_idx
    va_high_idx = poc_idx
    va_vol = vol_profile[poc_idx]
    
    while va_vol < target_vol:
        expand_low = va_low_idx > 0
        expand_high = va_high_idx < num_buckets - 1
        
        if not expand_low and not expand_high:
            break
        
        low_vol = vol_profile[va_low_idx - 1] if expand_low else 0
        high_vol = vol_profile[va_high_idx + 1] if expand_high else 0
        
        if low_vol >= high_vol and expand_low:
            va_low_idx -= 1
            va_vol += low_vol
        elif expand_high:
            va_high_idx += 1
            va_vol += high_vol
        else:
            break
    
    vah = price_levels[va_high_idx]
    val = price_levels[va_low_idx]
    
    result['poc'] = poc_price
    result['vah'] = vah
    result['val'] = val
    
    curr_price = data['close'].iloc[-1]
    prev_price = data['close'].iloc[-2]
    
    # Price near POC
    poc_proximity = abs(curr_price - poc_price) / poc_price
    if poc_proximity < 0.005:
        result['score'] += 10
        result['label'] = f'📊 VP: At POC ${poc_price:,.4f} (high liquidity)'
    
    # Breaking VAH or VAL
    if prev_price <= vah < curr_price:
        result['score'] += 10
        result['label'] = f'📊 VP: Breaking VAH ${vah:,.4f} (bullish!)'
    elif curr_price < val <= prev_price:
        result['score'] += 10
        result['label'] = f'📊 VP: Breaking below VAL ${val:,.4f} (bearish!)'
    
    if result['label'] is None:
        result['label'] = f'📊 VP: POC ${poc_price:,.2f} | VAH ${vah:,.2f} | VAL ${val:,.2f}'
    
    return result

def get_weekly_structure(symbol):
    """
    Feature 8: Weekly Structure Analysis
    - Check price vs Weekly MA(7) and MA(25) SMA
    - Weekly bias contributes +15 to alignment score
    """
    result = {'bias': 'NEUTRAL', 'score': 0, 'label': None, 'ma7': None, 'ma25': None}
    
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, '1w', limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        close = df['close']
        w_ma7 = sma(close, 7).iloc[-1]
        w_ma25 = sma(close, 25).iloc[-1]
        curr_price = close.iloc[-1]
        
        result['ma7'] = w_ma7
        result['ma25'] = w_ma25
        
        if curr_price > w_ma7 > w_ma25:
            result['bias'] = 'BULLISH'
            result['score'] = 15
            result['label'] = f'📅 Weekly Bullish (Price > WMA7 > WMA25)'
        elif curr_price < w_ma7 < w_ma25:
            result['bias'] = 'BEARISH'
            result['score'] = 15
            result['label'] = f'📅 Weekly Bearish (Price < WMA7 < WMA25)'
        else:
            result['bias'] = 'NEUTRAL'
            result['score'] = 0
            result['label'] = f'📅 Weekly Neutral (Mixed MA)'
        
    except Exception as e:
        result['label'] = f'📅 Weekly: N/A'
    
    return result

# ==================== MACRO ====================

def get_macro_context():
    """
    Macro context (KJO style):
    - BTC Dom + USDT Dom → alternative.me (lebih reliable, no rate limit)
    - TOTAL3 → CoinGecko /global (BTC+ETH excluded)
    - ETH/BTC Ratio → Binance ETH/BTC pair langsung (paling akurat)
    """
    macro_notes = []
    btc_dom = 0
    usdt_dom = 0
    eth_dom = 0
    macro_bias = 'NEUTRAL'

    # ---- BTC Dom + USDT Dom via alternative.me ----
    try:
        resp = requests.get(
            'https://api.alternative.me/v2/global/',
            timeout=10
        )
        alt_data = resp.json().get('data', {})
        btc_dom = float(alt_data.get('bitcoin_percentage_of_market_cap', 0))
        total_mcap = float(alt_data.get('total_market_cap_usd', 0) or 0)
        total_vol = float(alt_data.get('total_24h_volume_usd', 0) or 0)

        if btc_dom > 55:
            macro_bias = 'BTC_DOMINANT'
            macro_notes.append(f"⚠️ BTC Dom tinggi ({btc_dom:.1f}%) → fokus BTC, altcoin hati2")
        elif btc_dom < 45:
            macro_bias = 'ALTSEASON'
            macro_notes.append(f"🚀 BTC Dom rendah ({btc_dom:.1f}%) → potensial altseason")
        else:
            macro_notes.append(f"📊 BTC Dom: {btc_dom:.1f}%")
    except Exception:
        # Fallback ke CoinGecko
        try:
            resp = requests.get('https://api.coingecko.com/api/v3/global', timeout=10)
            cg = resp.json()['data']
            btc_dom = cg['market_cap_percentage'].get('btc', 0)
            usdt_dom = cg['market_cap_percentage'].get('usdt', 0)
            eth_dom = cg['market_cap_percentage'].get('eth', 0)
            total_mcap = cg.get('total_market_cap', {}).get('usd', 0)
            macro_notes.append(f"📊 BTC Dom: {btc_dom:.1f}% (CoinGecko)")
        except Exception:
            macro_notes.append("📊 BTC Dom: N/A")

    # ---- USDT Dom via CoinGecko (alternative.me ga kasih USDT dom) ----
    try:
        resp = requests.get('https://api.coingecko.com/api/v3/global', timeout=10)
        cg = resp.json()['data']
        usdt_dom = cg['market_cap_percentage'].get('usdt', 0)
        eth_dom = cg['market_cap_percentage'].get('eth', 0)
        cg_total = cg.get('total_market_cap', {}).get('usd', 0)
        mcap_change_24h = cg.get('market_cap_change_percentage_24h_usd', 0)

        if usdt_dom > 7:
            macro_notes.append(f"🔴 USDT Dom tinggi ({usdt_dom:.1f}%) → market risk-off")
        else:
            macro_notes.append(f"🟢 USDT Dom: {usdt_dom:.1f}%")

        # ---- Feature 6: TOTAL3 ----
        # TOTAL3 = total market cap - BTC - ETH
        btc_pct = cg['market_cap_percentage'].get('btc', 0) / 100
        eth_pct = cg['market_cap_percentage'].get('eth', 0) / 100
        total3_usd = cg_total * (1 - btc_pct - eth_pct)
        total3_b = total3_usd / 1e9

        if mcap_change_24h > 2:
            macro_notes.append(f"🌊 TOTAL3 ~${total3_b:.0f}B (24h: +{mcap_change_24h:.1f}%) → Altcoin inflow ✅")
        elif mcap_change_24h < -2:
            macro_notes.append(f"🌊 TOTAL3 ~${total3_b:.0f}B (24h: {mcap_change_24h:.1f}%) → Altcoin outflow ⚠️")
        else:
            macro_notes.append(f"🌊 TOTAL3 ~${total3_b:.0f}B (24h: {mcap_change_24h:+.1f}%)")
    except Exception:
        macro_notes.append("🟢 USDT Dom: N/A | 🌊 TOTAL3: N/A")

    # ---- Feature 7: ETH/BTC Ratio langsung dari Binance ----
    try:
        # Ambil ETH/BTC pair langsung — paling akurat, 1 API call
        ethbtc_ohlcv = exchange.fetch_ohlcv('ETH/BTC', '1d', limit=22)
        if ethbtc_ohlcv:
            ratio_series = pd.Series([c[4] for c in ethbtc_ohlcv])
            curr_ratio = ratio_series.iloc[-1]
            ratio_ma20 = ratio_series.rolling(20).mean().iloc[-1]

            if curr_ratio > ratio_ma20 * 1.02:
                macro_notes.append(
                    f"💎 ETH/BTC {curr_ratio:.5f} > MA20 ({ratio_ma20:.5f}) → ETH outperforming 🚀 (Altseason!)"
                )
                if macro_bias == 'NEUTRAL':
                    macro_bias = 'ALTSEASON'
            elif curr_ratio < ratio_ma20 * 0.98:
                macro_notes.append(
                    f"📉 ETH/BTC {curr_ratio:.5f} < MA20 ({ratio_ma20:.5f}) → ETH underperform (BTC dominant)"
                )
            else:
                macro_notes.append(
                    f"💎 ETH/BTC: {curr_ratio:.5f} (MA20: {ratio_ma20:.5f}) — Neutral"
                )
    except Exception:
        macro_notes.append("💎 ETH/BTC: N/A")

    return {
        'btc_dom': btc_dom,
        'usdt_dom': usdt_dom,
        'eth_dom': eth_dom,
        'bias': macro_bias,
        'notes': macro_notes
    }

# ==================== FUNDING RATE & OPEN INTEREST ====================

def get_market_sentiment(symbol):
    """
    Ambil Funding Rate + Open Interest dari Binance Futures.
    Return dict dengan info + warning level.
    """
    result = {
        'funding_rate': None,
        'funding_pct': None,
        'oi': None,
        'oi_change': None,
        'warnings': [],
        'level': 'clean'  # clean | caution | danger
    }

    base = symbol.replace('/USDT', '') + 'USDT'

    # ---- Funding Rate ----
    try:
        resp = requests.get(
            'https://fapi.binance.com/fapi/v1/premiumIndex',
            params={'symbol': base},
            timeout=8
        )
        data = resp.json()
        fr = float(data.get('lastFundingRate', 0))
        result['funding_rate'] = fr
        result['funding_pct'] = fr * 100

        if fr > 0.001:   # > 0.1% (sangat extreme long bias)
            result['warnings'].append(f"🔴 Funding Rate: {fr*100:.4f}% (EXTREME — banyak long terjebak!)")
            result['level'] = 'danger'
        elif fr > 0.0005:  # > 0.05% (tinggi)
            result['warnings'].append(f"🟡 Funding Rate: {fr*100:.4f}% (Tinggi — waspadai long squeeze)")
            if result['level'] == 'clean':
                result['level'] = 'caution'
        elif fr < -0.0005:  # < -0.05% (negatif — banyak short)
            result['warnings'].append(f"🟡 Funding Rate: {fr*100:.4f}% (Negatif — banyak short, potensi short squeeze)")
            if result['level'] == 'clean':
                result['level'] = 'caution'
        else:
            result['warnings'].append(f"🟢 Funding Rate: {fr*100:.4f}% (Normal)")
    except Exception as e:
        result['warnings'].append(f"📊 Funding Rate: N/A")

    # ---- Open Interest (sekarang vs 1h lalu) ----
    try:
        # OI sekarang
        resp_now = requests.get(
            'https://fapi.binance.com/fapi/v1/openInterest',
            params={'symbol': base},
            timeout=8
        )
        oi_now = float(resp_now.json().get('openInterest', 0))

        # OI history (last 2 candles = 1 jam lalu)
        resp_hist = requests.get(
            'https://fapi.binance.com/futures/data/openInterestHist',
            params={'symbol': base, 'period': '1h', 'limit': 3},
            timeout=8
        )
        oi_hist = resp_hist.json()
        if oi_hist and len(oi_hist) >= 2:
            oi_prev = float(oi_hist[-2].get('sumOpenInterest', oi_now))
            oi_change_pct = ((oi_now - oi_prev) / oi_prev * 100) if oi_prev else 0
            result['oi'] = oi_now
            result['oi_change'] = oi_change_pct

            if abs(oi_change_pct) > 10:
                direction = "naik" if oi_change_pct > 0 else "turun"
                result['warnings'].append(
                    f"⚡ Open Interest {direction} {abs(oi_change_pct):.1f}% (1h) — ada pergerakan besar!"
                )
                if result['level'] == 'clean':
                    result['level'] = 'caution'
            elif abs(oi_change_pct) > 5:
                direction = "naik" if oi_change_pct > 0 else "turun"
                result['warnings'].append(f"📈 OI {direction} {abs(oi_change_pct):.1f}% (1h)")
            else:
                result['warnings'].append(f"📊 OI stable ({oi_change_pct:+.1f}% / 1h)")
        else:
            result['warnings'].append(f"📊 OI: N/A")
    except Exception as e:
        result['warnings'].append(f"📊 OI: N/A")

    # ---- SL Cluster Zone (dari swing high/low OHLCV) ----
    try:
        ohlcv = exchange.fetch_ohlcv(base.replace('USDT', '/USDT'), '4h', limit=50)
        if ohlcv and len(ohlcv) >= 10:
            highs = [c[2] for c in ohlcv[-20:]]
            lows  = [c[3] for c in ohlcv[-20:]]
            curr_price = ohlcv[-1][4]

            # Swing Low = minimum dari 20 candle terakhir
            swing_low = min(lows)
            # Swing High = maximum dari 20 candle terakhir
            swing_high = max(highs)

            low_dist_pct = abs(curr_price - swing_low) / curr_price * 100
            high_dist_pct = abs(swing_high - curr_price) / curr_price * 100

            sl_cluster_text = f"📍 SL Cluster Zone:\n"
            sl_cluster_text += f"  ⬇️ Bawah: ${swing_low:,.2f} ({low_dist_pct:.1f}% dari harga)"
            if low_dist_pct < 3:
                sl_cluster_text += " ⚠️ DEKAT!"
            sl_cluster_text += f"\n  ⬆️ Atas: ${swing_high:,.2f} ({high_dist_pct:.1f}% dari harga)"
            if high_dist_pct < 3:
                sl_cluster_text += " ⚠️ DEKAT!"

            result['warnings'].append(sl_cluster_text)
    except Exception:
        pass

    # ---- Long/Short Ratio ----
    try:
        resp_ls = requests.get(
            'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
            params={'symbol': base, 'period': '1h', 'limit': 1},
            timeout=8
        )
        ls_data = resp_ls.json()
        if ls_data and len(ls_data) > 0:
            long_ratio = float(ls_data[0].get('longAccount', 0.5)) * 100
            short_ratio = 100 - long_ratio

            if long_ratio > 70:
                result['warnings'].append(
                    f"🔴 L/S Ratio: {long_ratio:.0f}% Long / {short_ratio:.0f}% Short (EXTREME LONG — rawan liquidasi!)"
                )
                result['level'] = 'danger'
            elif long_ratio > 60:
                result['warnings'].append(
                    f"🟡 L/S Ratio: {long_ratio:.0f}% Long / {short_ratio:.0f}% Short (Banyak long — waspadai reversal)"
                )
                if result['level'] == 'clean':
                    result['level'] = 'caution'
            elif short_ratio > 65:
                result['warnings'].append(
                    f"🟡 L/S Ratio: {long_ratio:.0f}% Long / {short_ratio:.0f}% Short (Banyak short — potensi squeeze)"
                )
                if result['level'] == 'clean':
                    result['level'] = 'caution'
            else:
                result['warnings'].append(
                    f"🟢 L/S Ratio: {long_ratio:.0f}% Long / {short_ratio:.0f}% Short (Balanced)"
                )
    except Exception:
        result['warnings'].append("👥 L/S Ratio: N/A")

    return result


# ==================== MAIN ANALYSIS ====================

def analyze_pair(symbol, tf='4h'):
    """Analisa satu pair di satu timeframe - full KJO method"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=300)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        close = df['close']
        high = df['high']
        low = df['low']
        open_ = df['open']
        volume = df['volume']
        
        # ---- MA Indicators (SMA per KJO) ----
        ma7 = sma(close, 7)
        ma25 = sma(close, 25)
        ma99 = sma(close, 99)
        ma200 = sma(close, 200)
        
        # ---- MACD (12, 26, 9) ----
        macd_line, signal_line, histogram = macd(close, 12, 26, 9)
        
        # ---- RSI (14) ----
        rsi_val = rsi(close, 14)
        
        # ---- Stochastic (14, 3, 3) ----
        stoch_k, stoch_d = stochastic(high, low, close, k_period=14, d_period=3, smooth_k=3)
        
        # ---- Volume ----
        vol_ma = sma(volume, 20)
        vol_ratio = volume.iloc[-1] / vol_ma.iloc[-1] if vol_ma.iloc[-1] > 0 else 1
        
        # ---- ATR ----
        atr = (high - low).rolling(14).mean().iloc[-1]
        
        # ---- Current values ----
        curr_price = close.iloc[-1]
        curr_ma7 = ma7.iloc[-1]
        curr_ma25 = ma25.iloc[-1]
        curr_ma99 = ma99.iloc[-1] if not pd.isna(ma99.iloc[-1]) else curr_price
        curr_ma200 = ma200.iloc[-1] if not pd.isna(ma200.iloc[-1]) else curr_price
        curr_rsi = rsi_val.iloc[-1]
        curr_macd = macd_line.iloc[-1]
        curr_signal = signal_line.iloc[-1]
        curr_hist = histogram.iloc[-1]
        prev_hist = histogram.iloc[-2]
        curr_stoch_k = stoch_k.iloc[-1]
        curr_stoch_d = stoch_d.iloc[-1]
        
        # S/R levels
        supports, resistances = detect_support_resistance(df)
        
        # Trendline break detection
        break_type, break_level = detect_trendline_break(df)
        
        # ==================== PATTERN DETECTION ====================
        
        # Feature 1: Double Bottom/Top
        dbt = detect_double_bottom_top(df, lookback=50)
        
        # Feature 2: Demand/Supply Zones
        dsz = detect_demand_supply_zones(df)
        
        # Feature 3: Order Blocks
        ob = detect_order_blocks(df)
        
        # Feature 4: Triangles
        tri = detect_triangles(df, lookback=30)
        
        # Feature 5: H&S / IH&S
        hs = detect_head_and_shoulders(df, lookback=60)
        
        # Feature 9: Accumulation
        acc = detect_accumulation(df, lookback=15)
        
        # Feature 10: Market Structure
        ms = detect_market_structure(df, lookback=30)
        
        # Feature 11: Fake Breakout Filter
        fbf = check_fake_breakout(df, break_type)
        
        # Feature 12: Volume Profile
        vp = detect_volume_profile(df, lookback=50)
        
        # =====================
        # SCORING SYSTEM (KJO style)
        # =====================
        bull_score = 0
        bear_score = 0
        signals = []
        patterns_found = []
        zones_found = []
        
        # 1. MA Stack (SMA per KJO)
        if curr_price > curr_ma7 > curr_ma25 > curr_ma99:
            bull_score += 25
            signals.append("✅ MA Stack Bullish (Price > MA7 > MA25 > MA99)")
        elif curr_price < curr_ma7 < curr_ma25 < curr_ma99:
            bear_score += 25
            signals.append("❌ MA Stack Bearish (Price < MA7 < MA25 < MA99)")
        elif curr_price > curr_ma25:
            bull_score += 10
            signals.append("📊 Price above MA25")
        else:
            bear_score += 10
            signals.append("📊 Price below MA25")
        
        # MA200 check
        if curr_price > curr_ma200:
            bull_score += 5
            signals.append(f"📊 Above MA200 (${curr_ma200:,.4f})")
        else:
            bear_score += 5
            signals.append(f"📊 Below MA200 (${curr_ma200:,.4f})")
        
        # 2. Trendline Break
        if break_type == 'BREAKOUT':
            bull_score += 30
            signals.append(f"🚀 BREAKOUT dari ${break_level:,.4f}")
        elif break_type == 'BREAKDOWN':
            bear_score += 30
            signals.append(f"💥 BREAKDOWN dari ${break_level:,.4f}")
        
        # Feature 11: Fake Breakout Filter (applied here)
        if fbf['label']:
            signals.append(fbf['label'])
            if fbf['is_fake']:
                if bull_score > bear_score:
                    bull_score += fbf['score_adj']
                else:
                    bear_score += fbf['score_adj']
        
        # 3. MACD (12, 26, 9)
        if curr_hist > 0 and prev_hist <= 0:
            bull_score += 20
            signals.append("📈 MACD Golden Cross")
        elif curr_hist < 0 and prev_hist >= 0:
            bear_score += 20
            signals.append("📉 MACD Death Cross")
        elif curr_hist > 0:
            bull_score += 10
            signals.append("📈 MACD Bullish")
        else:
            bear_score += 10
            signals.append("📉 MACD Bearish")
        
        # 4. RSI (14)
        if 40 < curr_rsi < 60:
            signals.append(f"⚖️ RSI Neutral ({curr_rsi:.0f})")
        elif curr_rsi > 60:
            bull_score += 10
            signals.append(f"📊 RSI Bullish ({curr_rsi:.0f})")
        elif curr_rsi < 40:
            bear_score += 10
            signals.append(f"📊 RSI Bearish ({curr_rsi:.0f})")
        if curr_rsi > 75:
            signals.append(f"⚠️ RSI Overbought ({curr_rsi:.0f})")
        elif curr_rsi < 25:
            signals.append(f"⚠️ RSI Oversold ({curr_rsi:.0f})")
        
        # 5. Stochastic (14, 3, 3)
        if curr_stoch_k > curr_stoch_d and curr_stoch_k < 80:
            bull_score += 10
            signals.append(f"📊 Stoch Bullish K:{curr_stoch_k:.0f} D:{curr_stoch_d:.0f}")
        elif curr_stoch_k < curr_stoch_d and curr_stoch_k > 20:
            bear_score += 10
            signals.append(f"📊 Stoch Bearish K:{curr_stoch_k:.0f} D:{curr_stoch_d:.0f}")
        elif curr_stoch_k < 20:
            bull_score += 5
            signals.append(f"📊 Stoch Oversold K:{curr_stoch_k:.0f}")
        elif curr_stoch_k > 80:
            bear_score += 5
            signals.append(f"📊 Stoch Overbought K:{curr_stoch_k:.0f}")
        
        # 6. Volume confirmation
        if vol_ratio > 1.5:
            if bull_score > bear_score:
                bull_score += 15
                signals.append(f"🔥 Volume tinggi {vol_ratio:.1f}x (konfirmasi bullish)")
            else:
                bear_score += 15
                signals.append(f"🔥 Volume tinggi {vol_ratio:.1f}x (konfirmasi bearish)")
        
        # Feature 1: Double Bottom/Top
        if dbt['score'] > 0:
            if dbt['pattern'] == 'DOUBLE_BOTTOM':
                bull_score += dbt['score']
            else:
                bear_score += dbt['score']
            patterns_found.append(dbt['label'])
        
        # Feature 2: Demand/Supply Zones
        if dsz['score'] > 0:
            if 'Demand' in (dsz['label'] or ''):
                bull_score += dsz['score']
            else:
                bear_score += dsz['score']
            if dsz['label']:
                zones_found.append(dsz['label'])
        
        # Feature 3: Order Blocks
        if ob['score'] > 0:
            if 'Bullish OB' in ' '.join(ob['obs']):
                bull_score += ob['score']
            else:
                bear_score += ob['score']
            if ob['label']:
                zones_found.append(ob['label'])
        
        # Feature 4: Triangles
        if tri['score'] > 0:
            if tri['pattern'] == 'ASCENDING_TRIANGLE':
                bull_score += tri['score']
            else:
                bear_score += tri['score']
            patterns_found.append(tri['label'])
        
        # Feature 5: H&S
        if hs['score'] > 0:
            if hs['pattern'] == 'INV_HEAD_SHOULDERS':
                bull_score += hs['score']
            else:
                bear_score += hs['score']
            patterns_found.append(hs['label'])
        
        # Feature 9: Accumulation
        if acc['score'] > 0:
            bull_score += acc['score']
            signals.append(acc['label'])
        
        # Feature 10: Market Structure
        if ms['score'] > 0:
            if ms['structure'] == 'UPTREND':
                bull_score += ms['score']
            elif ms['structure'] == 'DOWNTREND':
                bear_score += ms['score']
        
        # Feature 12: Volume Profile
        if vp['score'] > 0:
            if bull_score > bear_score:
                bull_score += vp['score']
            else:
                bear_score += vp['score']
        
        # ---- Determine bias ----
        total_score = bull_score + bear_score
        if total_score == 0:
            bias = 'NEUTRAL'
            confidence = 0
        elif bull_score > bear_score:
            bias = 'BULLISH'
            confidence = round(bull_score / (bull_score + bear_score) * 100)
        else:
            bias = 'BEARISH'
            confidence = round(bear_score / (bull_score + bear_score) * 100)
        
        # ---- TP/SL (KJO style) ----
        if bias == 'BULLISH':
            nearest_support = max([s for s in supports if s < curr_price], default=curr_price * 0.97)
            sl = nearest_support * 0.99
            tp1 = curr_price + (atr * 1.5)
            tp2 = curr_price + (atr * 3)
            tp3 = max(resistances) if resistances else curr_price + (atr * 5)  # TP3 = highest resistance
        else:
            nearest_resistance = min([r for r in resistances if r > curr_price], default=curr_price * 1.03)
            sl = nearest_resistance * 1.01
            tp1 = curr_price - (atr * 1.5)
            tp2 = curr_price - (atr * 3)
            tp3 = min(supports) if supports else curr_price - (atr * 5)  # TP3 = lowest support
        
        return {
            'symbol': symbol,
            'timeframe': tf,
            'price': curr_price,
            'bias': bias,
            'confidence': confidence,
            'bull_score': bull_score,
            'bear_score': bear_score,
            'signals': signals,
            'patterns': patterns_found,
            'zones': zones_found,
            'market_structure': ms['label'],
            'vp': vp,
            'entry': curr_price,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'tp3': tp3,
            'rsi': curr_rsi,
            'volume_ratio': vol_ratio,
            'break_type': break_type,
            'atr': atr,
        }
    except Exception as e:
        print(f"  Error analyzing {symbol} {tf}: {e}")
        return None

# ==================== TELEGRAM ====================

def send_telegram(message):
    """Kirim pesan ke Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json().get('ok', False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def format_signal(result, macro, weekly=None, sentiment=None):
    """Format signal message ala KJO - full version"""
    symbol = result['symbol'].replace('/USDT', '')
    bias = result['bias']
    conf = result['confidence']
    price = result['price']
    tf_label = TIMEFRAMES.get(result['timeframe'], result['timeframe'])
    
    if bias == 'BULLISH':
        emoji = '🟢'
        dir_emoji = '📈 LONG'
    elif bias == 'BEARISH':
        emoji = '🔴'
        dir_emoji = '📉 SHORT'
    else:
        return None
    
    if conf < 55:
        return None
    
    # Patterns section
    patterns_text = ''
    if result.get('patterns'):
        patterns_text = f"\n📐 <b>Patterns:</b>\n" + '\n'.join(result['patterns'])
    
    # Market Structure section
    ms_text = ''
    if result.get('market_structure'):
        ms_text = f"\n🏗️ <b>Structure:</b> {result['market_structure']}"
    
    # Zones section
    zones_text = ''
    if result.get('zones'):
        zones_text = f"\n📦 <b>Zones:</b>\n" + '\n'.join(result['zones'])
    
    # Volume Profile section
    vp_text = ''
    vp = result.get('vp', {})
    if vp and vp.get('poc'):
        vp_text = f"\n📊 <b>VP:</b> POC ${vp['poc']:,.2f} | VAH ${vp['vah']:,.2f} | VAL ${vp['val']:,.2f}"
    
    # Weekly bias section
    weekly_text = ''
    if weekly and weekly.get('label'):
        weekly_text = f"\n{weekly['label']}"

    # Sentiment (Funding Rate + OI) section
    sentiment_text = ''
    sentiment_header = ''
    if sentiment:
        level = sentiment.get('level', 'clean')
        if level == 'danger':
            sentiment_header = '\n\n⛔ <b>MARKET SENTIMENT — HATI-HATI!</b>'
        elif level == 'caution':
            sentiment_header = '\n\n⚠️ <b>Market Sentiment:</b>'
        else:
            sentiment_header = '\n\n✅ <b>Market Sentiment:</b>'
        
        warnings = sentiment.get('warnings', [])
        if warnings:
            sentiment_text = sentiment_header + '\n' + '\n'.join(warnings)
    
    # Top signals (max 5)
    top_signals = result['signals'][:6]
    
    msg = f"""
{emoji} <b>KJO SIGNAL — {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━━━

📊 <b>Timeframe:</b> {tf_label}
🎯 <b>Direction:</b> {dir_emoji}
💪 <b>Confidence:</b> {conf}% (Bull:{result['bull_score']} Bear:{result['bear_score']})

💰 <b>Price:</b> ${price:,.4f}
🎯 <b>Entry:</b> ${result['entry']:,.4f}
🛑 <b>SL:</b> ${result['sl']:,.4f}
✅ <b>TP1:</b> ${result['tp1']:,.4f}
✅ <b>TP2:</b> ${result['tp2']:,.4f}
✅ <b>TP3:</b> ${result['tp3']:,.4f}
{ms_text}{patterns_text}{zones_text}{vp_text}

📋 <b>Signals:</b>
{chr(10).join(top_signals)}
{weekly_text}

🌍 <b>Macro:</b>
{chr(10).join(macro['notes'])}
{sentiment_text}

⏰ {datetime.now().strftime('%d/%m %H:%M')} WIB
<i>Based on KJO Academy Method</i>"""
    return msg.strip()

# ==================== SCAN ====================

def is_4h_candle_close():
    """Cek apakah sekarang dalam window 5 menit setelah 4H candle close"""
    now = datetime.now()  # WIB (server time)
    return (now.hour in CANDLE_4H_CLOSE_HOURS_WIB and 
            now.minute < SCAN_WINDOW_MINUTES)


def send_market_update_per_pair(symbol, result, weekly, macro):
    """
    Kirim market update kondisi terkini per pair.
    Bukan signal entry — hanya laporan bias & level penting.
    """
    sym = symbol.replace('/USDT', '')
    bias = result.get('bias', 'NEUTRAL')
    price = result.get('price', 0)
    conf = result.get('confidence', 0)
    ms = result.get('market_structure', '')

    if bias == 'BULLISH':
        bias_emoji = '📈'
    elif bias == 'BEARISH':
        bias_emoji = '📉'
    else:
        bias_emoji = '➡️'

    weekly_bias = weekly.get('bias', 'NEUTRAL') if weekly else 'N/A'

    # Key levels
    sl = result.get('sl', 0)
    tp1 = result.get('tp1', 0)
    entry = result.get('entry', 0)

    zones_text = ''
    if result.get('zones'):
        zones_text = '\n' + '\n'.join(result['zones'][:2])

    msg = f"""
📊 <b>4H UPDATE — {sym}/USDT</b>
━━━━━━━━━━━━━━━━━━━━━━

{bias_emoji} <b>Bias:</b> {bias} ({conf}%)
💰 <b>Price:</b> ${price:,.4f}
🏗️ <b>Structure:</b> {ms}
📅 <b>Weekly:</b> {weekly_bias}

🎯 <b>Key Levels:</b>
⬆️ Resistance: ${tp1:,.4f}
📍 Entry zone: ${entry:,.4f}
⬇️ Support/SL: ${sl:,.4f}
{zones_text}

🌍 <b>Macro:</b> {macro['notes'][0] if macro['notes'] else 'N/A'}

⏰ {datetime.now().strftime('%d/%m %H:%M')} WIB
<i>4H Candle Close Update</i>"""

    return send_telegram(msg.strip())


def scan_market():
    """Main scanning function"""
    print(f"\n{'='*50}")
    print(f"Scanning market... {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")
    
    # 1. Macro analysis dulu (KJO style)
    macro = get_macro_context()
    print(f"Macro: BTC Dom={macro['btc_dom']:.1f}% | USDT Dom={macro['usdt_dom']:.1f}%")
    
    signals_found = 0
    
    for symbol in WATCHLIST:
        # Check cooldown
        key = f"{symbol}"
        if key in last_signal:
            elapsed = time.time() - last_signal[key]
            if elapsed < SIGNAL_COOLDOWN:
                remaining = int((SIGNAL_COOLDOWN - elapsed) / 60)
                print(f"  {symbol}: cooldown ({remaining}m left)")
                continue
        
        # Feature 8: Weekly Structure analysis
        weekly = get_weekly_structure(symbol)
        time.sleep(0.5)
        
        # Analyze multiple timeframes (KJO multi-TF approach)
        results = {}
        for tf in ['1d', '4h', '1h']:
            result = analyze_pair(symbol, tf)
            if result:
                results[tf] = result
            time.sleep(0.5)
        
        if not results:
            print(f"  {symbol}: no data")
            continue
        
        # Check alignment across timeframes (KJO: TF harus aligned)
        biases = [results[tf]['bias'] for tf in results if tf in results]
        
        # Alignment score from weekly
        weekly_score = weekly.get('score', 0)
        
        bull_count = biases.count('BULLISH')
        bear_count = biases.count('BEARISH')
        
        # Include weekly in alignment
        if weekly['bias'] == 'BULLISH':
            bull_count += 1
        elif weekly['bias'] == 'BEARISH':
            bear_count += 1
        
        tf_summary = ' | '.join([f"{tf}:{results[tf]['bias']}({results[tf]['confidence']}%)" for tf in results])
        print(f"  {symbol}: {tf_summary} | Weekly:{weekly['bias']}")

        # Selalu kirim market update per pair (kondisi terkini)
        primary_tf_update = '4h' if '4h' in results else list(results.keys())[0]
        send_market_update_per_pair(symbol, results[primary_tf_update], weekly, macro)
        time.sleep(0.5)
        
        # Strong signal: 2+ TF aligned (including weekly) → kirim entry signal
        if bull_count >= 2 or bear_count >= 2:
            # Gunakan 4H result sebagai primary (KJO sering pakai 4H untuk entry)
            primary_tf = '4h' if '4h' in results else list(results.keys())[0]
            primary = results[primary_tf]
            
            # Boost score from weekly alignment
            if weekly['bias'] == primary['bias']:
                if primary['bias'] == 'BULLISH':
                    primary['bull_score'] += weekly_score
                else:
                    primary['bear_score'] += weekly_score
                # Recalculate confidence
                total = primary['bull_score'] + primary['bear_score']
                if primary['bias'] == 'BULLISH':
                    primary['confidence'] = round(primary['bull_score'] / total * 100)
                else:
                    primary['confidence'] = round(primary['bear_score'] / total * 100)
            
            # Skip jika BTC Dom tinggi dan bukan BTC/ETH
            if macro['btc_dom'] > 55 and symbol not in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT']:
                print(f"    → Skipped (BTC Dom tinggi, fokus major coins)")
                continue
            
            # Ambil Funding Rate + OI sebagai info tambahan
            sentiment = get_market_sentiment(symbol)
            time.sleep(0.3)

            msg = format_signal(primary, macro, weekly, sentiment)
            if msg:
                print(f"    → SIGNAL FOUND! Sending to Telegram...")
                success = send_telegram(msg)
                if success:
                    last_signal[key] = time.time()
                    signals_found += 1
                    print(f"    ✅ Sent!")
                else:
                    print(f"    ❌ Failed to send")
        
        time.sleep(1)
    
    print(f"\nScan complete. {signals_found} signals sent.")
    return signals_found

def send_market_summary():
    """Kirim market summary ala KJO"""
    macro = get_macro_context()
    
    prices = {}
    for symbol in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']:
        try:
            ticker = exchange.fetch_ticker(symbol)
            prices[symbol.replace('/USDT', '')] = {
                'price': ticker['last'],
                'change': ticker['percentage']
            }
        except:
            pass
        time.sleep(0.3)
    
    price_lines = []
    for coin, data in prices.items():
        change = data['change']
        emoji = '🟢' if change > 0 else '🔴'
        price_lines.append(f"{emoji} <b>{coin}:</b> ${data['price']:,.2f} ({change:+.1f}%)")
    
    msg = f"""
📊 <b>MARKET UPDATE</b>
━━━━━━━━━━━━━━━━━━━━━━

{chr(10).join(price_lines)}

🌍 <b>Macro:</b>
{chr(10).join(macro['notes'])}

⏰ {datetime.now().strftime('%d/%m %H:%M')} WIB
"""
    send_telegram(msg.strip())

# ==================== MAIN ====================
def main():
    print("🚀 KJO Signal Bot Started! (v2 - Full KJO Academy Method)")
    print(f"Watchlist: {', '.join([s.replace('/USDT','') for s in WATCHLIST])}")
    print(f"Scan interval: {SCAN_INTERVAL}s")
    print(f"Signal cooldown: {SIGNAL_COOLDOWN/3600:.0f}h per pair")
    print("Features: MA(7/25/99/200 SMA) | MACD(12,26,9) | RSI(14) | Stoch(14,3,3)")
    print("Patterns: Double B/T | Demand/Supply | OB | Triangle | H&S | Accumulation")
    print("Structure: HH/HL/LH/LL | Fake Breakout Filter | Volume Profile | Weekly TF")
    print("Macro: BTC Dom + USDT Dom + TOTAL3 + ETH/BTC Ratio")
    
    send_telegram(
        "🤖 <b>KJO Signal Bot v3 aktif!</b>\n\n"
        "⏰ Scan: <b>Tiap 4H Candle Close</b> (01:00, 05:00, 09:00, 13:00, 17:00, 21:00 WIB)\n\n"
        "📊 <b>Market Update</b> — tiap candle close, kondisi terkini semua pair\n"
        "🎯 <b>Entry Signal</b> — kalau ada setup valid (multi-TF aligned)\n"
        "📡 <b>Funding Rate + OI</b> — info tambahan di tiap signal\n\n"
        "Method: KJO Academy Full — MA(7/25/99/200), MACD, RSI, Stoch\n"
        "Patterns: Double B/T, D/S Zone, OB, Triangle, H&S, Accumulation\n"
        "Macro: BTC Dom + USDT Dom + TOTAL3 + ETH/BTC Ratio"
    )
    
    last_scan_hour = -1  # track jam scan terakhir
    
    while True:
        try:
            now = datetime.now()
            
            # Cek apakah ini window 4H candle close
            if is_4h_candle_close():
                current_slot = now.hour  # slot jam ini
                
                # Pastikan belum scan di slot jam yang sama
                if current_slot != last_scan_hour:
                    print(f"\n⏰ 4H Candle Close! Jam {now.strftime('%H:%M')} WIB — Scanning...")
                    scan_market()
                    last_scan_hour = current_slot
                else:
                    print(f"  [{now.strftime('%H:%M')}] Sudah scan slot ini, skip.")
            else:
                # Di luar window — hitung berapa menit lagi
                next_close = None
                for h in CANDLE_4H_CLOSE_HOURS_WIB:
                    if h > now.hour or (h == now.hour and now.minute < SCAN_WINDOW_MINUTES):
                        next_close = h
                        break
                if next_close is None:
                    next_close = CANDLE_4H_CLOSE_HOURS_WIB[0] + 24  # besok
                
                mins_left = (next_close - now.hour) * 60 - now.minute
                print(f"  [{now.strftime('%H:%M')}] Waiting for 4H close... ~{mins_left}m lagi (jam {next_close:02d}:00 WIB)")
            
            time.sleep(SCAN_INTERVAL)  # cek tiap 1 menit
            
        except KeyboardInterrupt:
            print("\nBot stopped.")
            send_telegram("🛑 KJO Signal Bot dihentikan.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
