import pandas as pd
import yfinance as yf
import requests
import time
import random
import paramiko
import os
from finvizfinance.screener.overview import Overview
from datetime import datetime, timedelta

# --- CONFIGURATION (SECURE) ---
TRADIER_ACCESS_TOKEN = os.environ.get("TRADIER_TOKEN")
FTP_HOST = os.environ.get("FTP_HOST")
FTP_USER = os.environ.get("FTP_USER")
FTP_PASS = os.environ.get("FTP_PASS")
FTP_DIR  = "/"

# --- ROBUST PORT HANDLING ---
# This fixes the "ValueError" by checking if the secret is empty.
# If empty, it defaults to 2222.
port_str = os.environ.get("FTP_PORT")
if port_str and port_str.strip():
    FTP_PORT = int(port_str)
else:
    FTP_PORT = 2222

# --- TELEGRAM SECRETS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- SCANNER SETTINGS ---
MIN_PRICE = 15.0          
MIN_AVG_VOLUME = 500_000  
TARGET_DELTA = 0.30       
MIN_PROFIT_FACTOR = -10.0 
MAX_EXPIRATION_WEEKS = 8

# --- CHECK FOR SECRETS ---
if not TRADIER_ACCESS_TOKEN or not FTP_PASS:
    print("❌ ERROR: Secrets not found! Make sure TRADIER_TOKEN and FTP_PASS are set in GitHub Settings.")
    # We don't exit here so the code can handle partial failures gracefully if needed

# --- TELEGRAM ALERT FUNCTION ---
def send_telegram_alert(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram keys missing. Skipping alert.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown" 
    }
    try:
        requests.post(url, json=payload)
        print("🚀 Telegram Alert Sent!")
    except Exception as e:
        print(f"❌ Failed to send Telegram: {e}")

def get_finviz_candidates():
    print("--- Step 1: Scanning Finviz (Broad Search) ---")
    filters_dict = {
        'Price': 'Over $15', 
        'Average Volume': 'Over 500K',
        'Option/Short': 'Optionable',
        'Volatility': 'Month - Over 3%', 
        'RSI (14)': 'Not Overbought (<60)' 
    }
    
    try:
        foverview = Overview()
        foverview.set_filter(filters_dict=filters_dict)
        
        # FIX: We tell Finviz to sort by 'Volatility' BEFORE downloading.
        # This ensures we get the true top movers, not just tickers starting with 'A'.
        df_finviz = foverview.screener_view(order='Volatility', ascend=False)
        
        # Double check locally just in case
        if 'Volatility' in df_finviz.columns:
            df_finviz['Vol_Num'] = df_finviz['Volatility'].astype(str).str.replace('%', '').astype(float)
            df_finviz = df_finviz.sort_values(by='Vol_Num', ascending=False)
            
        print(f"Found {len(df_finviz)} candidates.")
        return df_finviz['Ticker'].tolist()
    except Exception as e:
        print(f"Error connecting to Finviz: {e}")
        return []

def check_10day_volume(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo")
        if len(hist) < 10: return None
        return { "Ticker": ticker, "Price": hist['Close'].iloc[-1] }
    except: return None

def get_tradier_expirations(symbol):
    url = "https://api.tradier.com/v1/markets/options/expirations"
    headers = {"Authorization": f"Bearer {TRADIER_ACCESS_TOKEN}", "Accept": "application/json"}
    params = {"symbol": symbol, "includeAllRoots": "true"}
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            data = response.json()
            dates = data.get('expirations', {}).get('date', [])
            if isinstance(dates, str): dates = [dates]
            return dates
        return []
    except: return []

def get_tradier_chain(symbol, expiration):
    url = "https://api.tradier.com/v1/markets/options/chains"
    headers = {"Authorization": f"Bearer {TRADIER_ACCESS_TOKEN}", "Accept": "application/json"}
    params = {"symbol": symbol, "expiration": expiration, "greeks": "true"}
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            data = response.json()
            options = data.get('options', {}).get('option', [])
            if isinstance(options, dict): options = [options]
            return options
        return []
    except: return []

def scan_spreads_tradier(ticker, current_price):
    opportunities = []
    all_exps = get_tradier_expirations(ticker)
    if not all_exps: return []
    valid_exps = [e for e in all_exps if e > datetime.now().strftime('%Y-%m-%d')]
    check_exps = valid_exps[:MAX_EXPIRATION_WEEKS]
    
    if current_price < 50: target_width = 1.0
    elif current_price < 150: target_width = 2.5
    else: target_width = 5.0

    for exp_date in check_exps:
        chain = get_tradier_chain(ticker, exp_date)
        if not chain: continue
        puts = [opt for opt in chain if opt.get('option_type') == 'put' and opt.get('greeks')]
        
        short_leg = None
        closest_delta_diff = 999
        for p in puts:
            delta = p['greeks'].get('delta')
            if delta is None: continue
            diff = abs(abs(delta) - TARGET_DELTA)
            if diff < closest_delta_diff:
                closest_delta_diff = diff
                short_leg = p
        if not short_leg: continue

        short_strike = float(short_leg['strike'])
        target_long_strike = short_strike - target_width
        long_leg = None
        closest_strike_diff = 999
        for p in puts:
            s = float(p['strike'])
            if s >= short_strike: continue 
            diff = abs(s - target_long_strike)
            if diff < closest_strike_diff:
                closest_strike_diff = diff
                long_leg = p
        if not long_leg or closest_strike_diff > (target_width * 0.6): continue

        long_strike = float(long_leg['strike'])
        try:
            short_bid = float(short_leg.get('bid', 0)) or float(short_leg.get('last', 0))
            long_ask = float(long_leg.get('ask', 0)) or float(long_leg.get('last', 0))
            net_credit = short_bid - long_ask
            if net_credit < 0.05: continue
            actual_width = short_strike - long_strike
            max_risk = actual_width - net_credit
            short_delta = abs(float(short_leg['greeks']['delta']))
            prob_win = 1.0 - short_delta
            prob_loss = short_delta
            profit_factor = (net_credit * prob_win) - (max_risk * prob_loss)
            
            if profit_factor > MIN_PROFIT_FACTOR:
                opportunities.append({
                    "Ticker": ticker,
                    "Price": current_price,
                    "Expiration": exp_date,
                    "Short_Strike": short_strike,
                    "Long_Strike": long_strike,
                    "Spread_Str": f"{short_strike} / {long_strike}",
                    "Net_Credit": round(net_credit, 2),
                    "Prob_Win": round(prob_win * 100, 1),
                    "Freedom_Factor": round(profit_factor, 2)
                })
        except: continue
        
        # Speed Limit for Tradier (Safe)
        time.sleep(0.6)
        
    return opportunities

def generate_tabbed_html(df_results):
    df_results['Expiration'] = df_results['Expiration'].astype(str)
    unique_dates = sorted(df_results['Expiration'].unique())
    top_8_dates = unique_dates[:8]
    
    top_3_trades = df_results.sort_values('Freedom_Factor', ascending=False).head(3)
    
    # EST Time Adjustment
    est_time = datetime.utcnow() - timedelta(hours=5)
    formatted_date = est_time.strftime("Date: %B, %d %Y %I:%M %p EST")

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Freedom Income Options - Spread Scanner</title>
        <link rel="icon" href="https://freedomincomeoptions.com/wp-content/uploads/2025/03/freedom-income-options-512-x-512.png" sizes="32x32" />
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background-color: #f4f4f9; }
            header { background-color: #ffffff; border-bottom: 3px solid #4CAF50; padding: 15px 20px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .logo-container { display: flex; align-items: center; }
            .logo-container img { height: 60px; margin-right: 15px; }
            .header-title { font-size: 24px; font-weight: bold; color: #333; }
            .date-display { font-size: 16px; color: #333; font-weight: bold; }
            .tab-container { background-color: #333; padding: 10px 0; text-align: center; }
            .tab-label { color: #ccc; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; display: block; }
            .tab { overflow: hidden; display: flex; justify-content: center; flex-wrap: wrap; }
            .tab button { background-color: inherit; border: none; outline: none; cursor: pointer; padding: 10px 15px; font-size: 14px; font-weight: bold; color: white; transition: 0.3s; border-radius: 4px; margin: 2px; }
            .tab button:hover { background-color: #4CAF50; }
            .tab button.active { background-color: #4CAF50; }
            .tabcontent { display: none; padding: 20px; max-width: 1200px; margin: 0 auto; }
            .best-trades-box { background-color: #FFD700; border: 4px solid #0000FF; padding: 20px; border-radius: 10px; margin-bottom: 30px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); }
            .best-trades-title { text-align: center; color: #0000FF; font-size: 22px; font-weight: 900; text-transform: uppercase; margin-top: 0; }
            table { width: 100%; border-collapse: collapse; background-color: white; border-radius: 5px; overflow: hidden; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
            th { background-color: #2E8B57; color: white; text-transform: uppercase; font-size: 0.9em; }
            tr:hover { background-color: #f1f1f1; }
            .freedom-factor { color: #2E8B57; font-weight: 900; font-size: 1.1em; }
            .best-row { background-color: #fff9c4; border-bottom: 2px solid #ccc; }
        </style>
    </head>
    <body>

    <header>
        <div class="logo-container">
            <img src="https://freedomincomeoptions.com/wp-content/uploads/2025/03/Freedom-income-options-440-x-100.png" alt="Freedom Income Options">
            <div class="header-title">Daily Spread Scanner</div>
        </div>
        <div class="date-display">""" + formatted_date + """</div>
    </header>

    <div class="tab-container">
        <span class="tab-label">Available Expirations (Days Out)</span>
        <div class="tab">
            <button class="tablinks active" onclick="openCity(event, 'BestTrades')">★ BEST TRADES</button>
    """
    
    today = datetime.now()
    
    for i, date_str in enumerate(top_8_dates):
        exp_date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        days_out = (exp_date_obj - today).days + 1 
        safe_id = f"tab_{date_str.replace('-', '')}"
        button_label = f"{date_str} ({days_out} Days)"
        html += f'<button class="tablinks" onclick="openCity(event, \'{safe_id}\')">{button_label}</button>'
    
    html += """
        </div>
    </div>

    <div id="BestTrades" class="tabcontent" style="display: block;">
        <div class="best-trades-box">
            <h3 class="best-trades-title">🏆 Top 3 Freedom Spreads (8-Week Max)</h3>
            <table>
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Ticker</th>
                        <th>Expiration</th>
                        <th>Spread (Short/Long)</th>
                        <th>Prob. Win</th>
                        <th>Net Credit</th>
                        <th>Freedom Factor</th>
                    </tr>
                </thead>
                <tbody>
    """
    
    rank = 1
    for _, row in top_3_trades.iterrows():
        ff_color = "green" if row['Freedom_Factor'] > 0 else "red"
        html += f"""
        <tr class="best-row">
            <td><b>#{rank}</b></td>
            <td><b>{row['Ticker']}</b></td>
            <td>{row['Expiration']}</td>
            <td>{row['Spread_Str']}</td>
            <td>{row['Prob_Win']}%</td>
            <td><b>${row['Net_Credit']:.2f}</b></td>
            <td class="freedom-factor" style="color: {ff_color};">{row['Freedom_Factor']:.2f}</td>
        </tr>
        """
        rank += 1
        
    html += """
                </tbody>
            </table>
        </div>
        <p style="text-align:center; color:#666;">*Negative Freedom Factor = Risk currently outweighs mathematical reward.</p>
    </div>
    """
    
    for i, date in enumerate(top_8_dates):
        safe_id = f"tab_{date.replace('-', '')}"
        daily_df = df_results[df_results['Expiration'] == date].copy()
        daily_df = daily_df.sort_values('Freedom_Factor', ascending=False).head(10)
        
        html += f'<div id="{safe_id}" class="tabcontent" style="display: none;">'
        html += f'<h3>Top Spreads for {date}</h3>'
        
        if daily_df.empty:
            html += "<p>No trades found.</p>"
        else:
            html += "<table><thead><tr><th>Ticker</th><th>Spread</th><th>Prob. Win</th><th>Net Credit</th><th>Freedom Factor</th></tr></thead><tbody>"
            for _, row in daily_df.iterrows():
                ff_color = "green" if row['Freedom_Factor'] > 0 else "red"
                html += f"""<tr>
                    <td><b>{row['Ticker']}</b></td>
                    <td>{row['Spread_Str']}</td>
                    <td>{row['Prob_Win']}%</td>
                    <td>${row['Net_Credit']:.2f}</td>
                    <td class="freedom-factor" style="color: {ff_color};">{row['Freedom_Factor']:.2f}</td>
                </tr>"""
            html += "</tbody></table></div>"

    html += """
    <script>
    function openCity(evt, cityName) {
        var i, tabcontent, tablinks;
        tabcontent = document.getElementsByClassName("tabcontent");
        for (i = 0; i < tabcontent.length; i++) { tabcontent[i].style.display = "none"; }
        tablinks = document.getElementsByClassName("tablinks");
        for (i = 0; i < tablinks.length; i++) { tablinks[i].className = tablinks[i].className.replace(" active", ""); }
        document.getElementById(cityName).style.display = "block";
        evt.currentTarget.className += " active";
    }
    </script>
    </body></html>
    """
    return html

def upload_to_sftp(filename):
    print(f"\n--- Step 3: Uploading {filename} via SFTP (Port {FTP_PORT}) ---")
    try:
        transport = paramiko.Transport((FTP_HOST, FTP_PORT))
        transport.connect(username=FTP_USER, password=FTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.chdir(FTP_DIR)
        sftp.put(filename, filename)
        sftp.close()
        transport.close()
        print(f"✅ SUCCESS! Results are live at: https://freedomincomeoptions.com/{filename}")
    except Exception as e:
        print(f"❌ SFTP Error: {e}")

def main():
    if not TRADIER_ACCESS_TOKEN or not FTP_PASS:
        print("❌ ERROR: Missing Secrets. Check your GitHub Settings.")
        return

    candidates = get_finviz_candidates()
    if not candidates: return
    
    if len(candidates) > 60:
        print(f"Selecting Top 60 High-Volatility candidates...")
        candidates = candidates[:60]

    print(f"\n--- Deep Analysis on {len(candidates)} Tickers (Showing ALL Spreads) ---")
    all_opportunities = []
    
    for i, t in enumerate(candidates):
        if i % 5 == 0: print(f"Scanning {i}/{len(candidates)} ({t})...")
        valid_data = check_10day_volume(t)
        if valid_data:
            ops_list = scan_spreads_tradier(valid_data['Ticker'], valid_data['Price'])
            if ops_list:
                for op in ops_list:
                    all_opportunities.append(op)

    print("\n" + "="*40)
    
    if all_opportunities:
        df = pd.DataFrame(all_opportunities)
        html_file = "credit_spread.html" 
        html_content = generate_tabbed_html(df)
        with open(html_file, "w") as f:
            f.write(html_content)
        print(f"✅ Scan Complete. HTML Generated: {html_file}")
        
        # --- SEND TELEGRAM ALERT ---
        top_3 = df.sort_values('Freedom_Factor', ascending=False).head(3)
        if not top_3.empty:
            msg = f"🚀 *Freedom Scan Complete!*\nFound {len(df)} opportunities.\n\n"
            msg += "*🏆 Top 3 Trades:*\n"
            for _, row in top_3.iterrows():
                msg += f"• *{row['Ticker']}*: {row['Spread_Str']} | Win: {row['Prob_Win']}% | Credit: ${row['Net_Credit']:.2f}\n"
            
            msg += "\nView full list: https://freedomincomeoptions.com/credit_spread.html"
            send_telegram_alert(msg)
        
        upload_to_sftp(html_file)
    else:
        print("No trades found matching criteria.")

if __name__ == "__main__":
    main()