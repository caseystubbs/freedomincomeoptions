# 🦅 Freedom Income Options Scanner

A specialized automated tool that scans the market for high-probability credit spread opportunities. It combines fundamental analysis (Finviz) with technical options data (Yahoo Finance).

## 🚀 How It Works
1. **Fundamental Filter:** Scans 8,000+ stocks on Finviz for:
   - Price > $10
   - Uptrend (Price above 200 SMA)
   - Positive Revenue Growth
2. **Volatility Scan:** Checks options chains for liquid expirations (21-50 DTE).
3. **Probability Engine:** Calculates Black-Scholes Delta and Expected Value (EV).
4. **Safety Check:** Filters for 0.15-0.30 Delta and positive mathematical expectancy.

## 🛠 Installation

1. Clone this repository:
   ```bash
   git clone [https://github.com/YOUR_USERNAME/freedom-income-scanner.git](https://github.com/YOUR_USERNAME/freedom-income-scanner.git)
