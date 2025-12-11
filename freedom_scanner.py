import yfinance as yf
import pandas as pd
import numpy as np
import time
import requests
import json
import logging
from datetime import datetime, timedelta
from scipy.stats import norm

# --- CONFIGURATION ---
DAYS_MIN = 21           
DAYS_MAX = 50
SPREAD_WIDTH = 5        
MIN_ROI = 0.15          
MIN_PRICE = 20.00       
MIN_EV = 0.05           

# --- WATCHLIST ---
WATCHLIST = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "AMD", "NVDA", "TSLA", "AMZN", "MSFT", "GOOGL", "META", "NFLX", 
    "JPM", "BAC", "WFC", "C", "GS", "MS", "BA", "DIS", "XOM", "CVX", "INTC", "CSCO", "VZ", "T", 
    "PFE", "MRK", "JNJ", "PG", "KO", "PEP", "WMT", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", 
    "V", "MA", "PYPL", "SQ", "UBER", "ABNB", "PLTR", "SOFI", "COIN", "MARA", "RIOT", "DKNG", 
    "HOOD", "ROKU", "SHOP", "SNOW", "CRM", "ADBE", "ORCL", "IBM", "CAT", "DE", "GE", "F", "GM",
    "LULU", "CROX", "AFRM", "UPST", "NET", "CRWD", "ZS", "PANW", "FTNT", "NOW", "TEAM", "DDOG",
    "SPOT", "PINS", "SNAP", "ZM", "DOCU", "TWLO", "OKTA", "MDB", "TTD", "RBLX", "U", "GME", "AMC",
    "OXY", "SLB", "HAL", "DVN", "EOG", "COP", "MSTR", "CLSK", "HUT", "WULF"
]

# --- HEAVY STEALTH MODE ---
# We are adding a full browser profile to trick Yahoo's firewall
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://finance.yahoo.com/',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
})

# --- MATH ENGINE ---
def calculate_delta(S, K, T, r, sigma, option_type="put"):
    try:
        if T <= 0 or sigma <= 0: return 0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        if option_type == "call": return norm.cdf(d1)
        return norm.cdf(d1) - 1
    except:
        return 0

def run_scanner():
    opportunities = []
    print(f"--- THE PROFIT HUNTER (DIAGNOSTIC MODE) ---")
    print(f"Scanning {len(WATCHLIST)} stocks...")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")

    for i, ticker in enumerate(WATCHLIST):
        # We wrap this in a TRY block but we PRINT the error now
        try:
            # Random delay
            time.sleep(np.random.uniform(1.0, 2.0))
            
            # Use the custom session
            stock = yf.Ticker(ticker, session=session)
            
            # 1. Get Price
            try:
                # Force a fresh download to test connection
                hist = stock.history(period="1d", proxy=None)
                if hist.empty: 
                    print(f"❌ {ticker}: Empty Price Data (Yahoo Blocked?)")
                    continue
                price = hist['Close'].iloc[-1]
            except Exception as e:
                print(f"❌ {ticker}: Price Error -> {e}")
                continue

            if price < MIN_PRICE: 
                # Silent skip for low price is fine
                continue

            # 2. Get Options
            try:
                exps = stock.options
                if not exps: 
                    print(f"⚠️ {ticker}: No Options Chain Found")
                    continue
            except Exception as e:
                print(f"❌ {ticker}: Options Error -> {e}")
                continue

            valid_dates = []
            for date in exps:
                dte = (datetime.strptime(date, "%Y-%m-%d") - datetime.now()).days
                if DAYS_MIN <= dte <= DAYS_MAX:
                    valid_dates.append(date)
            
            if not valid_dates: continue
            
            # If we get here, connection is working!
            print(f"[{i+1}/{len(WATCHLIST)}] {ticker} (${price:.2f})...")

            for date in valid_dates:
                try:
                    chain = stock.option_chain(date)
                    puts = chain.puts
                    
                    dte = (datetime.strptime(date, "%Y-%m-%d") - datetime.now()).days
                    T = dte / 365.0
                    r = 0.045 

                    # Filter OTM
                    otm_puts = puts[puts['strike'] < price]

                    for index, short_leg in otm_puts.iterrows():
                        strike = short_leg['strike']
                        sigma = short_leg['impliedVolatility']
                        if sigma == 0: continue

                        delta = abs(calculate_delta(price, strike, T, r, sigma, "put"))
                        if delta < 0.15 or delta > 0.35: continue

                        long_strike = strike - SPREAD_WIDTH
                        long_leg = puts[puts['strike'] == long_strike]

                        if not long_leg.empty:
                            short_price = (short_leg['bid'] + short_leg['ask']) / 2
                            long_price = (long_leg.iloc[0]['bid'] + long_leg.iloc[0]['ask']) / 2
                            
                            credit = short_price - long_price
                            max_risk = SPREAD_WIDTH - credit

                            if credit <= 0.10 or max_risk <= 0: continue
                            
                            roi = credit / max_risk
                            win_prob = (1 - delta)
                            loss_prob = delta
                            ev = (win_prob * credit) - (loss_prob * max_risk)

                            if ev >= MIN_EV: 
                                trade = {
                                    "ticker": ticker,
                                    "price": round(price, 2),
                                    "exp": date,
                                    "short_strike": strike,
                                    "long_strike": long_strike,
                                    "credit": round(credit, 2),
                                    "risk": round(max_risk, 2),
                                    "roi": round(roi * 100, 1),
                                    "win_prob": round(win_prob * 100, 1),
                                    "ev": round(ev, 2)
                                }
                                opportunities.append(trade)
                                print(f"   >>> FOUND! {ticker} +EV: ${ev:.2f}")

                except Exception as e:
                    # Only print serious chain errors
                    # print(f"Chain error {ticker}: {e}")
                    continue 

        except Exception as e:
            print(f"CRITICAL ERROR on {ticker}: {e}")
            continue

    # --- GENERATE HTML ---
    print("\nGenerating Report...")
    
    if not opportunities:
        print("⚠️ No trades found. Generating safety report.")
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Freedom Income Results</title>
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 50px; text-align: center; color: #555; }}
                .container {{ background: white; padding: 40px; border-radius: 12px; max-width: 600px; margin: 0 auto; box-shadow: 0 10px 20px rgba(0,0,0,0.05); }}
                h1 {{ color: #2c3e50; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Market Scan Complete</h1>
                <p>No trades met the strict criteria today ({datetime.now().strftime('%Y-%m-%d')}).</p>
                <p><strong>This is a safety feature.</strong> When the market is inefficient or data is delayed, we do not force trades.</p>
                <p>Next scan scheduled for 9:45 AM EST tomorrow.</p>
            </div>
        </body>
        </html>
        """
    else:
        opportunities.sort(key=lambda x: x['ev'], reverse=True)
        top_picks = opportunities[:5]
        rest_picks = opportunities[5:]

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>The Profit Hunter Results</title>
            <style>
                :root {{ --primary: #2c3e50; --accent: #2980b9; --bg: #f4f7f6; --highlight-bg: #fff8e1; --highlight-border: #3498db; }}
                body {{ font-family: 'Segoe UI', sans-serif; background: var(--bg); padding: 30px; color: #333; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                h1 {{ color: var(--primary); text-align: center; margin-bottom: 5px; }}
                .timestamp {{ text-align: center; color: #888; font-size: 14px; margin-bottom: 40px; }}
                .top-picks-box {{ background-color: var(--highlight-bg); border: 3px solid var(--highlight-border); border-radius: 12px; padding: 30px; margin-bottom: 40px; box-shadow: 0 10px 25px rgba(52, 152, 219, 0.15); }}
                .top-header {{ text-align: center; font-size: 22px; font-weight: 800; color: var(--primary); text-transform: uppercase; margin-bottom: 20px; letter-spacing: 1px; }}
                .top-header span {{ color: #e67e22; }}
                table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
                th {{ background: var(--primary); color: white; padding: 15px; text-align: left; font-size: 14px; }}
                td {{ padding: 15px; border-bottom: 1px solid #eee; font-size: 15px; vertical-align: middle; }}
                tr:hover {{ background: #fcfcfc; }}
                .ticker-box {{ font-weight: bold; font-size: 16px; color: var(--primary); }}
                .price-tag {{ font-size: 12px; color: #888; }}
                .roi-cell {{ font-weight: bold; color: var(--primary); }}
                .ev-cell {{ font-weight: 800; color: #27ae60; background: #eafaf1; padding: 8px; border-radius: 4px; }}
                .exp-date {{ font-weight: bold; color: #555; }}
                .top-picks-box table {{ border: 1px solid #e0e0e0; }}
                .top-picks-box th {{ background: #3498db; }}
                h2 {{ margin-top: 0; color: #555; font-size: 18px; margin-bottom: 15px; border-left: 5px solid #ccc; padding-left: 10px; }}
            </style>
        </head>
        <body>
        <div class="container">
            <h1>The Profit Hunter: Daily Opportunities</h1>
            <div class="timestamp">Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (EST)</div>

            <div class="top-picks-box">
                <div class="top-header"><span>⭐</span> Best Trades of the Day <span>⭐</span></div>
                <table>
                    <thead>
                        <tr>
                            <th>Rank</th>
                            <th>Ticker</th>
                            <th>Expiration</th>
                            <th>Strikes</th>
                            <th>Credit / Risk</th>
                            <th>Win Prob</th>
                            <th>ROI</th>
                            <th>EV (Per Trade)</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for idx, trade in enumerate(top_picks):
            row = f"""
                <tr>
                    <td style="font-weight:bold; color:#e67e22; font-size:18px;">#{idx+1}</td>
                    <td>
                        <div class="ticker-box">{trade['ticker']}</div>
                        <div class="price-tag">${trade['price']}</div>
                    </td>
                    <td class="exp-date">{trade['exp']}</td>
                    <td>{trade['short_strike']} / {trade['long_strike']}</td>
                    <td>${trade['credit']} / ${trade['risk']}</td>
                    <td>{trade['win_prob']}%</td>
                    <td class="roi-cell">{trade['roi']}%</td>
                    <td><span class="ev-cell">+${trade['ev']}</span></td>
                </tr>
            """
            html_content += row

        html_content += """
                    </tbody>
                </table>
            </div>

            <h2>All Profitable Opportunities</h2>
            <table>
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Expiration</th>
                        <th>Strikes</th>
                        <th>Credit / Risk</th>
                        <th>Win Prob</th>
                        <th>ROI</th>
                        <th>EV (Per Trade)</th>
                    </tr>
                </thead>
                <tbody>
        """
        for trade in rest_picks:
            row = f"""
                <tr>
                    <td>
                        <div class="ticker-box">{trade['ticker']}</div>
                        <div class="price-tag">${trade['price']}</div>
                    </td>
                    <td class="exp-date">{trade['exp']}</td>
                    <td>{trade['short_strike']} / {trade['long_strike']}</td>
                    <td>${trade['credit']} / ${trade['risk']}</td>
                    <td>{trade['win_prob']}%</td>
                    <td class="roi-cell">{trade['roi']}%</td>
                    <td style="font-weight:bold; color:#27ae60;">+${trade['ev']}</td>
                </tr>
            """
            html_content += row

        html_content += """
                </tbody>
            </table>
        </div>
        </body>
        </html>
        """

    with open("view_results.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n✅ SUCCESS! Found {len(opportunities)} trades.")

if __name__ == "__main__":
    run_scanner()
