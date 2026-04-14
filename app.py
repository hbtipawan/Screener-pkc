import streamlit as st
import pandas as pd
import requests
import io
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import your core logic
from vpci_engine import fetch_weekly_data, analyze_stock_v3, DEFAULT_PARAMS

warnings.filterwarnings("ignore")

# Set up the web page
st.set_page_config(page_title="VPCI Screener Dashboard", layout="wide")
st.title("Investor — Weekly Market Screener")
# -------------------------------------------------------------------
# Data Fetching Functions (Cached to avoid repeated API calls)
# -------------------------------------------------------------------
@st.cache_data
def get_nse_stock_tickers():
    try:
        # Read the list directly from the local file in your GitHub repo!
        df = pd.read_csv("EQUITY_L.csv")
        
        # Clean the columns and filter for standard equities (EQ)
        df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]
        if "SERIES" in df.columns:
            df = df[df["SERIES"] == "EQ"]
            
        tickers = [str(t).strip() for t in df["SYMBOL"].tolist()]
        return tickers
        
    except Exception as e:
        # Fallback just in case the file gets deleted
        return ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN",
                "BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","MARUTI","SUNPHARMA",
                "TITAN","BAJFINANCE","WIPRO","NESTLEIND","HCLTECH","NTPC","POWERGRID",
                "ADANIENT","COALINDIA","TATASTEEL","JSWSTEEL","HAL","BEL","TRENT",
                "POLYCAB","PERSISTENT","DIXON","TATAPOWER","IRCTC","PETRONET"]

@st.cache_data
def fetch_us_symbols(min_price, min_mcap):
    try:
        # latin-1 encoding bypasses special character crashes, on_bad_lines skips corrupted rows
        df = pd.read_csv("us_stocks.csv", encoding="latin-1", on_bad_lines="skip")
        
        # Strip hidden spaces from all column names
        df.columns = df.columns.str.strip()
        
        symbols = []
        for _, row in df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if not sym or "/" in sym or "^" in sym or len(sym) > 5 or sym.lower() == "nan": 
                continue
                
            try:
                # Some CSVs use "Last Sale" and some use "LastSale"
                price_val = row.get("Last Sale", row.get("LastSale", "0"))
                price_str = str(price_val).replace("$", "").replace(",", "")
                price = float(price_str)
            except:
                price = 0
                
            try:
                mcap_val = row.get("Market Cap", row.get("MarketCap", "0"))
                mcap_str = str(mcap_val).replace(",", "")
                mcap = float(mcap_str) if mcap_str.strip() != "" and mcap_str.lower() != "nan" else 0
            except:
                mcap = 0
                
            if price >= min_price and mcap >= min_mcap:
                symbols.append(sym)
                
        return symbols
    except Exception as e:
        st.error(f"🚨 Error reading US Stocks CSV: {e}")
        return ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","UNH"]

@st.cache_data
def fetch_etf_symbols(min_price):
    try:
        df = pd.read_csv("us_etfs.csv", encoding="latin-1", on_bad_lines="skip")
        df.columns = df.columns.str.strip()
        
        symbols = []
        for _, row in df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if not sym or "/" in sym or len(sym) > 6 or sym.lower() == "nan": 
                continue
                
            try:
                price_val = row.get("Last Sale", row.get("LastSalePrice", "0"))
                price_str = str(price_val).replace("$", "").replace(",", "")
                price = float(price_str)
            except:
                price = 0
                
            if price >= min_price:
                symbols.append(sym)
                
        return symbols
    except Exception as e:
        st.error(f"🚨 Error reading US ETFs CSV: {e}")
        return ["SPY","QQQ","IWM","DIA","VTI","VOO","XLK","XLF","XLE","XLV"]
# -------------------------------------------------------------------
# Processing Worker
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# Processing Worker
# -------------------------------------------------------------------
def process_symbol(symbol, params, market_type):
    if market_type == "NSE":
        yf_sym = f"{symbol}.NS"
        av_sym = f"{symbol}.BSE"
        df = fetch_weekly_data(yf_sym, av_symbol=av_sym, av_key=params.get("av_key","demo"))
    else:
        df = fetch_weekly_data(symbol)
        
    if df is None: return None
    return analyze_stock_v3(symbol, df, params)
    
    # Inject Market Cap into the result dictionary
    if result:
        mcap = 0
        try:
            ticker_obj = yf.Ticker(yf_sym)
            # Correct property syntax for newer yfinance versions
            mcap = ticker_obj.fast_info.market_cap
        except AttributeError:
            try:
                # Fallback 1: Dictionary access for slightly older versions
                mcap = ticker_obj.fast_info['marketCap']
            except:
                pass
        except Exception:
            pass
            
        # Fallback 2: The classic info dictionary if fast_info fails entirely
        if not mcap:
            try:
                mcap = ticker_obj.info.get('marketCap', 0)
            except:
                mcap = 0

        result['raw_mcap'] = mcap
        result['market_type'] = market_type
        
    return result
# -------------------------------------------------------------------
# Sidebar UI controls
# -------------------------------------------------------------------
st.sidebar.header("Screener Settings")
market_choice = st.sidebar.radio("Select Market", ["NSE Stocks", "US Stocks", "US ETFs"])

relaxed_mode = st.sidebar.checkbox("Relaxed Mode (Allow 6/7)", value=False)
workers = st.sidebar.slider("Parallel Workers", min_value=5, max_value=30, value=15)
test_limit = st.sidebar.number_input("Limit Scan (0 = All)", min_value=0, max_value=10000, value=50, help="Scanning thousands of stocks on web can time out. Test with a smaller number first.")

min_price = 5.0
min_mcap = 500000000.0

if market_choice == "US Stocks":
    min_price = st.sidebar.number_input("Min Price ($)", value=5.0)
    min_mcap = st.sidebar.number_input("Min Market Cap ($)", value=500000000.0)
elif market_choice == "US ETFs":
    min_price = st.sidebar.number_input("Min Price ($)", value=5.0)

run_scan = st.sidebar.button("Run Screener", type="primary")

# -------------------------------------------------------------------
# Main Execution Logic
# -------------------------------------------------------------------
if run_scan:
    params = {**DEFAULT_PARAMS, "relaxed": relaxed_mode, "av_key": "demo"}
    
    # 1. Fetch Symbols
    with st.spinner(f"Fetching active symbols for {market_choice}..."):
        if market_choice == "NSE Stocks":
            symbols = get_nse_stock_tickers()
            market_flag = "NSE"
        elif market_choice == "US Stocks":
            symbols = fetch_us_symbols(min_price, min_mcap)
            market_flag = "US"
        else:
            symbols = fetch_etf_symbols(min_price)
            market_flag = "ETF"
            
    if test_limit > 0:
        symbols = symbols[:test_limit]

    st.info(f"Loaded {len(symbols)} symbols. Beginning scan...")

    # 2. Run the Screener
    results = []
    failed = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_symbol, s, params, market_flag): s for s in symbols}
        done = 0
        for f in as_completed(futs):
            done += 1
            progress_bar.progress(done / len(symbols))
            status_text.text(f"Scanned {done} of {len(symbols)}...")
            
            try:
                r = f.result()
                if r: results.append(r)
                else: failed.append(futs[f])
            except:
                failed.append(futs[f])

    status_text.text(f"Scan complete! Analyzed: {len(results)} | Failed: {len(failed)}")

    # 3. Display Results
    if results:
        df = pd.DataFrame(results)
        
        # Apply custom status labels
        def status_label(row):
            if row.get("fresh_signal"):      return "🔥 FRESH BUY"
            if row.get("fresh_ext_signal"):  return "🔥 FRESH EXT"
            if row.get("full_entry"):        return "★ BUYABLE (7/7)"
            if row.get("gates_ready_ext"):   return "⚡ 7/7 EXTENDED"
            if row.get("relaxed_entry"):     return "★ RELAXED (6/7)"
            if row.get("gate_count", 0) >= 6 and row.get("tier1_pass"): return "◉ WATCHLIST (6/7)"
            if row.get("gate_count", 0) >= 5: return "▲ MOMENTUM (5+)"
            return "Other"

        df["status"] = df.apply(status_label, axis=1)
        
        # Sort data
        df_sorted = df.sort_values(["gate_count", "pct_near_52w"], ascending=[False, False])
        
        # -----------------------------------------------------------
        # SMART MARKET CAP FETCHER (Bypasses Yahoo Rate Limits)
        # -----------------------------------------------------------
        # Fetch only for stocks passing >= 5 gates to avoid IP ban
        top_symbols = df_sorted[df_sorted["gate_count"] >= 5]["symbol"].tolist()
        mcap_dict = {}

        if top_symbols:
            with st.spinner(f"Fetching Market Cap for the top {len(top_symbols)} candidates..."):
                def fetch_mcap(sym):
                    try:
                        import yfinance as yf
                        yf_sym = f"{sym}.NS" if market_choice == "NSE Stocks" else sym
                        tkr = yf.Ticker(yf_sym)
                        # Bulletproof extraction across all yfinance versions
                        try: return sym, tkr.fast_info['marketCap']
                        except:
                            try: return sym, tkr.fast_info.market_cap
                            except: return sym, tkr.info.get('marketCap', 0)
                    except:
                        return sym, 0

                # Max 5 workers is safe for Yahoo Finance metadata endpoints
                with ThreadPoolExecutor(max_workers=5) as executor:
                    for future in as_completed([executor.submit(fetch_mcap, s) for s in top_symbols]):
                        sym, mval = future.result()
                        mcap_dict[sym] = mval

        # Map back to dataframe (stocks with <5 gates will remain 0 and show N/A)
        df_sorted['raw_mcap'] = df_sorted['symbol'].map(mcap_dict).fillna(0)

        # Format Market Cap
        def format_mcap(mcap):
            if not mcap or mcap == 0:
                return "N/A"
            if market_choice == "NSE Stocks":
                crores = mcap / 10000000
                if crores >= 1000:
                    return f"{crores / 1000:.1f}K crore inr".replace(".0K", "K")
                return f"{crores:.0f} crore inr"
            else:
                if mcap >= 1e9:
                    return f"${mcap / 1e9:.1f}B".replace(".0B", "B")
                return f"${mcap / 1e6:.1f}M"

        df_sorted["Market Cap"] = df_sorted["raw_mcap"].apply(format_mcap)

        # -----------------------------------------------------------
        # Create UI Copy and Reorder Columns
        # -----------------------------------------------------------
        df_ui = df_sorted.copy()
        
        # Reorder to put 'Market Cap' directly after 'symbol'
        cols = list(df_ui.columns)
        if "Market Cap" in cols:
            cols.remove("Market Cap")
            symbol_idx = cols.index("symbol") if "symbol" in cols else 0
            cols.insert(symbol_idx + 1, "Market Cap")
            df_ui = df_ui[cols]
            
        # Clean up backend columns so they don't show on the screen
        if "raw_mcap" in df_ui.columns: df_ui = df_ui.drop(columns=["raw_mcap"])
        
        # Create TradingView Links
        if market_choice == "NSE Stocks":
            df_ui["symbol"] = "https://in.tradingview.com/chart/?symbol=NSE:" + df_ui["symbol"]
        else:
            df_ui["symbol"] = "https://www.tradingview.com/chart/?symbol=" + df_ui["symbol"]

        tv_config = {
            "symbol": st.column_config.LinkColumn(
                "Symbol",
                display_text=r".*symbol=(?:NSE:)?(.*)"
            )
        }
        # -----------------------------------------------------------

        st.success(f"Scan complete! Found {len(df_sorted)} candidates.")

        tab1, tab2, tab3, tab4 = st.tabs(["🔥 Fresh Signals", "★ Buyable (7/7)", "◉ Watchlist (6/7)", "All Results"])
        
        with tab1:
            st.subheader("Fresh Buy Signals (Latest Candle)")
            fresh_df = df_ui[df_ui["status"].isin(["🔥 FRESH BUY", "🔥 FRESH EXT"])]
            st.dataframe(fresh_df, use_container_width=True, column_config=tv_config)

        with tab2:
            st.subheader("Buyable (All 7 Gates Passed)")
            buyable_df = df_ui[df_ui["status"] == "★ BUYABLE (7/7)"]
            st.dataframe(buyable_df, use_container_width=True, column_config=tv_config)

        with tab3:
            st.subheader("Watchlist / Relaxed")
            watch_df = df_ui[df_ui["status"].isin(["★ RELAXED (6/7)", "◉ WATCHLIST (6/7)"])]
            st.dataframe(watch_df, use_container_width=True, column_config=tv_config)

        with tab4:
            st.subheader("Full Screener Output")
            st.dataframe(df_ui, use_container_width=True, column_config=tv_config)
        
        # Download Button (clean data without URLs for CSV)
        if "raw_mcap" in df_sorted.columns: df_sorted = df_sorted.drop(columns=["raw_mcap"])
        
        csv = df_sorted.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Full Results as CSV",
            data=csv,
            file_name=f"vpci_{market_flag.lower()}_results_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.warning("No stocks passed the screener criteria.")
        
    if failed:
        with st.expander("View Failed Symbols"):
            st.write(", ".join(failed))
