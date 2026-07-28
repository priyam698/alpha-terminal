import sys
import math
import asyncio
import logging
import os  # <-- ADDED THIS
from collections import deque
from datetime import datetime, time
import numpy as np
import pandas as pd
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import urllib.request
import urllib.parse

# ==============================================================================
# 1. CREDENTIALS & QUANTITATIVE RISK PARAMETERS
# ==============================================================================
CONFIG = {
    "client_id": "O01UVCGKG6-200",        
    "access_token": os.getenv("FYERS_ACCESS_TOKEN") or (
        open(r"C:\Users\priya\access_token.txt").read().strip() 
        if os.path.exists(r"C:\Users\priya\access_token.txt") else ""
    ), 
    
    # Institutional Core Filters
    "SIGMA_MULTIPLIER": 2.3,
    "VOL_MOMENTUM_THRESHOLD": 1.35,
    "LOT_SIZE": 65,                  # 2026 Mandated Nifty Lot Matrix Calibration
    "MAX_DAILY_LOSS_RUPEES": 600.0   # 4% Hard Stop protection for ₹15,000 account
}
# Telegram Notification Helper
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram_alert(message: str):
    """Sends a notification message to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

state = {
    "nifty_spot": None,
    "atm_strike": None,
    "ce_symbol": None,   
    "pe_symbol": None,
    "position": None,  
    "ws_data": {},         
    "running": True,
    "last_atm_spot": 0.0,
    "daily_realized_pnl": 0.0,
    
    # Data storage to compute historical metrics dynamically
    "historical_df": pd.DataFrame()
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. HISTORICAL CONTEXT ENGINE & INDICATOR ARRAYS
# ==============================================================================
def sync_market_indicators(fyers):
    """Fetches recent data to compute historical indicators reliably"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (pd.Timestamp.now() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    
    data = {
        "symbol": "NSE:NIFTY50-INDEX", "resolution": "5", "date_format": "1",
        "range_from": start_str, "range_to": today_str, "cont_flag": "1"
    }
    try:
        response = fyers.history(data=data)
        if response and response.get("s") == "ok":
            candles = response.get("candles", [])
            df = pd.DataFrame(candles, columns=['epoch', 'open', 'high', 'low', 'close', 'volume'])
            
            # Mathematical Matrix Formulations
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            df['atr'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(window=14).mean()
            
            df['mean_basis'] = df['close'].rolling(window=20).mean()
            df['rolling_std'] = df['close'].rolling(window=20).std()
            df['upper_envelope'] = df['mean_basis'] + (CONFIG["SIGMA_MULTIPLIER"] * df['rolling_std'])
            df['lower_envelope'] = df['mean_basis'] - (CONFIG["SIGMA_MULTIPLIER"] * df['rolling_std'])
            
            df['volume_ma'] = df['volume'].rolling(window=15).mean()
            df['smart_money_active'] = df['volume'] > (df['volume_ma'] * 1.15)
            df['vol_momentum'] = df['atr'] / df['atr'].shift(5)
            
            state["historical_df"] = df.bfill().ffill().reset_index(drop=True)
    except Exception as e:
        logger.error(f"❌ Indicator Matrix sync failure: {e}")

def get_option_symbols(fyers, spot_price: float):
    if not spot_price or spot_price <= 0: return None
    atm_strike = int(round(spot_price / 50.0) * 50)
    state["atm_strike"] = atm_strike
    try:
        data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 3}
        response = fyers.optionchain(data=data)
        if response and response.get("s") == "ok":
            options_chain = response["data"]["optionsChain"]
            atm_contracts = [opt for opt in options_chain if opt.get("strike_price") == atm_strike]
            ce_symbol, pe_symbol = None, None
            for contract in atm_contracts:
                if contract.get("option_type") == "CE": ce_symbol = contract.get("symbol")
                elif contract.get("option_type") == "PE": pe_symbol = contract.get("symbol")
            if ce_symbol and pe_symbol:
                return {"ce": ce_symbol, "pe": pe_symbol}
    except Exception as e:
        logger.error(f"❌ Option chain network pull error: {e}")
    return None

# ==============================================================================
# 3. CONCURRENT PIPELINE INFRASTRUCTURE
# ==============================================================================
async def market_data_feed_worker(fyers):
    """Maintains active data updates while honoring historical matrix context"""
    while state["running"]:
        try:
            await asyncio.to_thread(sync_market_indicators, fyers)
            if not state["historical_df"].empty:
                state["nifty_spot"] = float(state["historical_df"]['close'].iloc[-1])
        except Exception:
            pass
        await asyncio.sleep(2.5) 

async def websocket_handler():
    def on_message(msg):
        try:
            if msg.get("type") == "sf":
                symbol = msg.get("symbol")
                ltp = msg.get("ltp")
                if symbol and ltp:
                    state["ws_data"][symbol] = float(ltp)
        except Exception:
            pass

    combined_token = f"{CONFIG['client_id']}:{CONFIG['access_token']}"
    fyers_ws = data_ws.FyersDataSocket(
        access_token=combined_token, log_path="", litemode=False, reconnect=True,
        on_connect=lambda: logger.info("🟢 FYERS Realtime Ticker Pipeline Connected"),
        on_close=lambda: None, on_error=lambda err: None, on_message=on_message
    )
    await asyncio.to_thread(fyers_ws.connect)
    
    while state["running"]:
        if state["ce_symbol"] and state["pe_symbol"]:
            current_subs = [state["ce_symbol"], state["pe_symbol"]]
            fyers_ws.subscribe(symbols=current_subs, data_type="SymbolUpdate")
        await asyncio.sleep(2.0)

# ==============================================================================
# 4. PREDACTOR STRATEGY ENGINE & RISKS CIRCUIT
# ==============================================================================
async def execute_strategy(fyers):
    logger.info("🚀 High-Frequency Predatory Scanning Engine Online")
    while state["running"]:
        if not state["nifty_spot"] or state["historical_df"].empty:
            await asyncio.sleep(0.5)
            continue

        spot = state["nifty_spot"]
        current_time = datetime.now().time()

        # Operational Timing Thresholds
        market_open_buffer = time(9, 22) # 9:22 AM Safety Lockout Rule
        max_entry_time = time(15, 10)
        square_off_time = time(15, 27)

        current_atm = int(round(spot / 50) * 50)
        if not state["ce_symbol"] or state.get("atm_strike") != current_atm:
            opt_symbols = get_option_symbols(fyers, spot)
            if opt_symbols:
                state["ce_symbol"] = opt_symbols["ce"]
                state["pe_symbol"] = opt_symbols["pe"]
                state["atm_strike"] = current_atm
                state["last_atm_spot"] = spot
                logger.info(f"⚡ Striking Chain Realignment -> ATM: {state['atm_strike']} | CE: {state['ce_symbol']}")

        metrics = state["historical_df"].iloc[-1]
        ce_ltp = state["ws_data"].get(state["ce_symbol"])
        pe_ltp = state["ws_data"].get(state["pe_symbol"])

        display_ce = ce_ltp if ce_ltp is not None else "Syncing Chain..."
        display_pe = pe_ltp if pe_ltp is not None else "Syncing Chain..."
        print(f"⚡ SCANNING | Spot: {spot:.2f} | ATM: {state['atm_strike']} | CE: {display_ce} | PE: {display_pe}        ", end="\r")

        # Evaluate entry filters if within valid operational execution boundaries
        if market_open_buffer <= current_time <= max_entry_time:
            # THE VOLATILITY REGIME GUARD
            # if metrics['vol_momentum'] > CONFIG["VOL_MOMENTUM_THRESHOLD"]:
            #     await asyncio.sleep(0.1)
            #     continue

            if not state["position"]:
                # LONG CALL CONDITION: Low spikes out below 2.3 Sigma floor
                if metrics['low'] < metrics['lower_envelope'] and metrics['smart_money_active'] and ce_ltp:
                    await place_order(fyers, "CE", ce_ltp, state["ce_symbol"], metrics['atr'])

                # LONG PUT CONDITION: High breaks out above 2.3 Sigma ceiling
                elif metrics['high'] > metrics['upper_envelope'] and metrics['smart_money_active'] and pe_ltp:
                    await place_order(fyers, "PE", pe_ltp, state["pe_symbol"], metrics['atr'])

                # Diagnostic log (runs when neither trade condition is met)
                else:
                    if not metrics['smart_money_active']:
                        logger.info("⏳ Waiting for Smart Money activation...")

            # Active position management block
            else:
                pos = state["position"]
                current_premium = ce_ltp if pos["side"] == "CE" else pe_ltp
                if not current_premium:
                    await asyncio.sleep(0.1)
                    continue

                # Realized execution delta calculation
                current_pnl_pct = ((current_premium - pos["entry_price"]) / pos["entry_price"]) * 100
                print(f"🚨 ACTIVE [{pos['side']}] | Entry: {pos['entry_price']:.2f} | Current: {current_premium:.2f} | Delta PnL: {current_pnl_pct:.2f}%        ", end="\r")

                # Asymmetric Trailing Profit Safe-locks
                if current_pnl_pct > pos["highest_pnl"]:
                    pos["highest_pnl"] = current_pnl_pct
                    if current_pnl_pct >= 22.5:
                        pos["sl_premium"] = max(pos["sl_premium"], pos["entry_price"] + (0.3 * pos["entry_atr"]))

                should_liquidate = False
                exit_reason = ""

                # Standard Volatility Band Trailing Matrix Checks
                if pos["side"] == "CE":
                    if metrics['low'] <= pos["sl_spot"]:
                        should_liquidate = True; exit_reason = "SPOT STOP PIERCED"
                    elif metrics['high'] >= pos["target_spot"]:
                        should_liquidate = True; exit_reason = "SPOT TARGET ACHIEVED"
                    elif current_premium <= pos["sl_premium"]:
                        should_liquidate = True; exit_reason = "PREMIUM STOP SLIPPAGE CUT"
                else:
                    if metrics['high'] >= pos["sl_spot"]:
                        should_liquidate = True; exit_reason = "SPOT STOP PIERCED"
                    elif metrics['low'] <= pos["target_spot"]:
                        should_liquidate = True; exit_reason = "SPOT TARGET ACHIEVED"
                    elif current_premium <= pos["sl_premium"]:
                        should_liquidate = True; exit_reason = "PREMIUM STOP SLIPPAGE CUT"

                # Auto Square-Off Check
                if current_time >= square_off_time:
                    should_liquidate = True; exit_reason = "EOD AUTO SQUARE-OFF TERMINATION"

                if should_liquidate:
                    await place_order(fyers, pos["side"], current_premium, pos["symbol"], pos["entry_atr"], side_type=-1, reason=exit_reason)

        await asyncio.sleep(0.05)

async def place_order(fyers, side: str, premium: float, symbol: str, current_atr: float, side_type=1, reason=""):
    # 🔒 CAPITAL CIRCUIT SWITCH: Block trades instantly if the account is pinned at its daily safety threshold
    if side_type == 1 and state["daily_realized_pnl"] <= -CONFIG["MAX_DAILY_LOSS_RUPEES"]:
        logger.warning(f"🔒 Entry Execution Aborted: Daily Loss Limit cutoff breached.")
        return

    try:
        data = {
            "symbol": symbol,
            "qty": CONFIG["LOT_SIZE"],
            "type": 2,  # 2 = Market order
            "side": int(side_type),  # Explicitly cast to int (1 for BUY, -1 for SELL)
            "productType": "INTRADAY",
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0
        }
        
        order_res = await asyncio.to_thread(fyers.place_order, data=data)
        
        if order_res and order_res.get("s") == "ok":
            if side_type == 1:
                # Map out asymmetric stop channels natively using current index volatility
                state["position"] = {
                    "side": side, "entry_price": premium, "symbol": symbol, "highest_pnl": 0.0,
                    "entry_atr": current_atr,
                    "sl_premium": premium * 0.75, # Deep safety backup stop on the premium contract itself (25% buffer)
                    "sl_spot": state["nifty_spot"] - (0.8 * current_atr) if side == "CE" else state["nifty_spot"] + (0.8 * current_atr),
                    "target_spot": state["nifty_spot"] + (2.2 * current_atr) if side == "CE" else state["nifty_spot"] - (2.2 * current_atr)
                }
                logger.info(f"💥 Predatory Entry Filled via FYERS: {symbol} @ {premium}")
                send_telegram_alert(
                f"""🚀 *ORDER EXECUTED*
• *Side:* {side}
• *Symbol:* `{symbol}`
• *Entry Price:* ₹{premium}
• *ATR:* {current_atr}"""
            )
            else:
                pos = state["position"]
                points = (premium - pos["entry_price"]) if pos["side"] == "CE" else (pos["entry_price"] - premium)
                trade_rupee_pnl = points * CONFIG["LOT_SIZE"]
                state["daily_realized_pnl"] += trade_rupee_pnl
                
                logger.info(f"🟢 Predatory Liquidation Complete | Reason: {reason} | Trade PnL: ₹{trade_rupee_pnl:.2f}")
                logger.info(f"💰 Cumulative Session PnL Balance: ₹{state['daily_realized_pnl']:.2f}")
                state["position"] = None
                send_telegram_alert(
                f"""🎯 *POSITION CLOSED*
• *Symbol:* `{symbol}`
• *Exit Price:* ₹{premium}
• *Trade P&L:* ₹{trade_rupee_pnl:.2f}
• *Session P&L:* ₹{state['daily_realized_pnl']:.2f}"""
            )
                
                if state["daily_realized_pnl"] <= -CONFIG["MAX_DAILY_LOSS_RUPEES"]:
                    logger.critical("🛑 EMERGENCY SHUTDOWN: Daily risk envelope breached. Shutting down system engines.")
                    state["running"] = False
        else:
            logger.error(f"❌ Execution Engine Mismatch: {order_res.get('message')}")
            
    except Exception as e:
        logger.error(f"Transaction Failure: {e}")

# ==============================================================================
# 5. LIFE CONCURRENCY RUNNER
# ==============================================================================
async def main():
    fyers = fyersModel.FyersModel(client_id=CONFIG["client_id"], token=CONFIG["access_token"], is_async=False, log_path="")
    profile = await asyncio.to_thread(fyers.get_profile)
    if not profile or profile.get("s") != "ok":
        logger.error("❌ Invalid App Key or Session Access Token verified against FYERS servers.")
        sys.exit(1)
        
    logger.info(f"🟢 Welcome {profile['data'].get('name')}! FYERS Authentication Confirmed.")
    
    # Sync initial historical structure to calculate indicators before taking real-time ticks
    await asyncio.to_thread(sync_market_indicators, fyers)

    spot_task = asyncio.create_task(market_data_feed_worker(fyers))
    while not state["nifty_spot"]:
        print("📡 Syncing public indices feed variables...", end="\r")
        await asyncio.sleep(0.2)
        
    print(f"\n✅ Live Spot Feed Synchronized: {state['nifty_spot']}")

    tasks = [
        spot_task,
        asyncio.create_task(websocket_handler()),
        asyncio.create_task(execute_strategy(fyers)),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        state["running"] = False

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execution halted by operator.")