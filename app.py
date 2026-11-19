import ccxt
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(page_title="WEEX / OKX / Gate 实时合约盯盘", layout="wide")
st.title("⚡ WEEX / OKX / Gate 实时盯盘与出单参考")
# 侧边栏：交易所、选币与刷新设置
st.sidebar.header("1. 交易所与币种")
exchange_choice = st.sidebar.selectbox("选择交易所", ["weex", "okx", "gate"], index=0)
if "symbol_input" not in st.session_state:
    st.session_state["symbol_input"] = "BTC/USDT"
quick_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "AVAX/USDT", "SUI/USDT"]
q_cols = st.sidebar.columns(3)
for idx, qs in enumerate(quick_symbols):
    if q_cols[idx % 3].button(qs.split("/")[0], key=f"quick_{qs}", use_container_width=True):
        st.session_state["symbol_input"] = qs
symbol_input = st.sidebar.text_input(
    "搜索/输入交易对",
    key="symbol_input",
    help="例如 BTC/USDT、DOGE/USDT、SUI/USDT",
).strip().upper()
if "/" not in symbol_input and symbol_input.endswith("USDT"):
    symbol_input = f"{symbol_input[:-4]}/USDT"
elif "/" not in symbol_input and symbol_input:
    symbol_input = f"{symbol_input}/USDT"
timeframe = st.sidebar.selectbox("K线周期", ["1m", "5m", "15m", "1h"], index=1)
limit = st.sidebar.slider("K线根数", min_value=50, max_value=200, value=120, step=10)
st.sidebar.header("2. 自动刷新（不闪白）")
refresh_sec = st.sidebar.slider("刷新间隔(秒)", min_value=5, max_value=60, value=15, step=5)
auto_refresh = st.sidebar.checkbox("开启静默自动刷新", value=True)
st.sidebar.header("3. 持仓与出单提示")
direction = st.sidebar.selectbox("持仓方向", ["做多 (Long)", "做空 (Short)"], index=0)
entry_price = st.sidebar.number_input("开仓均价", min_value=0.0, value=0.0, step=0.1, format="%.4f")
sl_pct = st.sidebar.number_input("止损幅度 (%)", min_value=0.1, max_value=20.0, value=1.0, step=0.1, format="%.2f")
tp1_pct = st.sidebar.number_input("第一止盈 TP1 (%)", min_value=0.2, max_value=30.0, value=1.5, step=0.1, format="%.2f")
tp2_pct = st.sidebar.number_input("第二止盈 TP2 (%)", min_value=0.5, max_value=50.0, value=3.0, step=0.1, format="%.2f")
def _fmt_price(val: float) -> str:
    if abs(val) >= 100:
        return f"${val:,.2f}"
    if abs(val) >= 1:
        return f"${val:,.4f}"
    return f"${val:,.6f}"
def _fetch_weex_contract_klines(coin: str, tf: str, lim: int) -> pd.DataFrame:
    symbol_raw = f"{coin.upper()}USDT"
    url = "https://api-contract.weex.com/capi/v3/market/klines"
    params = {"symbol": symbol_raw, "interval": tf, "limit": min(lim, 100)}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not rows:
        raise ValueError("WEEX 返回空 K 线数据")
    df_raw = pd.DataFrame(rows)
    if isinstance(rows[0], dict):
        df = pd.DataFrame({
            "ts": pd.to_numeric(df_raw.get("ts", df_raw.get("timestamp", 0))),
            "open": pd.to_numeric(df_raw["open"]),
            "high": pd.to_numeric(df_raw["high"]),
            "low": pd.to_numeric(df_raw["low"]),
            "close": pd.to_numeric(df_raw["close"]),
            "volume": pd.to_numeric(df_raw.get("volume", 0)),
        })
    else:
        df = pd.DataFrame({
            "ts": pd.to_numeric(df_raw[0]),
            "open": pd.to_numeric(df_raw[1]),
            "high": pd.to_numeric(df_raw[2]),
            "low": pd.to_numeric(df_raw[3]),
            "close": pd.to_numeric(df_raw[4]),
            "volume": pd.to_numeric(df_raw[5] if df_raw.shape[1] > 5 else 0),
        })
    df = df.sort_values("ts").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["ts"], unit="ms")
    return df
@st.cache_data(ttl=5)
def fetch_ohlcv_by_exchange(ex_name: str, symbol: str, tf: str, lim: int) -> pd.DataFrame:
    coin_u = symbol.split("/")[0].strip().upper()
    if ex_name == "weex":
        try:
            if hasattr(ccxt, "weex"):
                ex = getattr(ccxt, "weex")({"enableRateLimit": True})
                for sym in (f"{coin_u}/USDT:USDT", f"{coin_u}/USDT", symbol):
                    try:
                        raw = ex.fetch_ohlcv(sym, timeframe=tf, limit=lim)
                        if raw:
                            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
                            df["time"] = pd.to_datetime(df["ts"], unit="ms")
                            break
                    except Exception:
                        continue
                else:
                    df = _fetch_weex_contract_klines(coin_u, tf, lim)
            else:
                df = _fetch_weex_contract_klines(coin_u, tf, lim)
        except Exception:
            df = _fetch_weex_contract_klines(coin_u, tf, lim)
    else:
        ex_cls = getattr(ccxt, ex_name)
        ex = ex_cls({"enableRateLimit": True})
        candidates = [f"{coin_u}/USDT:USDT", f"{coin_u}/USDT", symbol]
        raw = None
        last_err = None
        for sym in candidates:
            try:
                raw = ex.fetch_ohlcv(sym, timeframe=tf, limit=lim)
                if raw:
                    break
            except Exception as e:
                last_err = e
        if not raw:
            raise RuntimeError(last_err or f"无法获取 {ex_name} {symbol} 行情")
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["ts"], unit="ms")
    df["ema7"] = df["close"].ewm(span=7, adjust=False).mean()
    df["ema25"] = df["close"].ewm(span=25, adjust=False).mean()
    return df
active_symbol = symbol_input or "BTC/USDT"
st.subheader(f"📌 当前选中币种实时卡片（{active_symbol} · {exchange_choice.upper()}）")
try:
    df = fetch_ohlcv_by_exchange(exchange_choice, active_symbol, timeframe, limit)
    last_p = float(df["close"].iloc[-1])
    prev_p = float(df["close"].iloc[-2]) if len(df) > 1 else last_p
    chg = ((last_p - prev_p) / prev_p) * 100 if prev_p else 0.0
    ema7_val = float(df["ema7"].iloc[-1])
    ema25_val = float(df["ema25"].iloc[-1])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{active_symbol} 最新价", _fmt_price(last_p), f"{chg:+.2f}%")
    c2.metric("EMA7 价格", _fmt_price(ema7_val), f"价差 {last_p - ema7_val:+.4f}")
    c3.metric("EMA25 价格", _fmt_price(ema25_val), f"价差 {last_p - ema25_val:+.4f}")
    c4.metric("数据状态", "🟢 实时在线", f"{exchange_choice.upper()} · {timeframe}")
    if entry_price > 0:
        st.divider()
        st.subheader("🚪 出单参考（止损 / TP1 / TP2）")
        is_long = direction.startswith("做多")
        if is_long:
            pnl = ((last_p - entry_price) / entry_price) * 100
            sl_p = entry_price * (1 - sl_pct / 100)
            tp1_p = entry_price * (1 + tp1_pct / 100)
            tp2_p = entry_price * (1 + tp2_pct / 100)
            hit_sl = last_p <= sl_p
            hit_tp2 = last_p >= tp2_p
            hit_tp1 = last_p >= tp1_p
        else:
            pnl = ((entry_price - last_p) / entry_price) * 100
            sl_p = entry_price * (1 + sl_pct / 100)
            tp1_p = entry_price * (1 - tp1_pct / 100)
            tp2_p = entry_price * (1 - tp2_pct / 100)
            hit_sl = last_p >= sl_p
            hit_tp2 = last_p <= tp2_p
            hit_tp1 = last_p <= tp1_p
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("当前浮盈亏", f"{pnl:+.2f}%")
        k2.metric("止损价", _fmt_price(sl_p))
        k3.metric("TP1 价", _fmt_price(tp1_p))
        k4.metric("TP2 价", _fmt_price(tp2_p))
        if hit_sl:
            st.error("🚨 触发止损条件，建议按止损线出单。")
        elif hit_tp2:
            st.success("🎯 到达 TP2 目标，建议分批/清仓止盈出单。")
        elif hit_tp1:
            st.success("✅ 到达 TP1 目标，可先出一部分落袋并把止损推向保本。")
        else:
            st.info("⏳ 持仓观察中，尚未触及止损或 TP1。")
    st.divider()
    st.subheader(f"📉 {active_symbol} 价格与均线走势（EMA7 / EMA25）")
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["time"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="K线",
            ),
            go.Scatter(
                x=df["time"],
                y=df["ema7"],
                mode="lines",
                name=f"EMA7 ({_fmt_price(ema7_val)})",
                line=dict(width=1.4),
            ),
            go.Scatter(
                x=df["time"],
                y=df["ema25"],
                mode="lines",
                name=f"EMA25 ({_fmt_price(ema25_val)})",
                line=dict(width=1.4),
            ),
        ]
    )
    fig.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"{exchange_choice.upper()} · {active_symbol} · {timeframe}（{len(df)} 根）｜"
        f"最新 EMA7: {_fmt_price(ema7_val)} ｜最新 EMA25: {_fmt_price(ema25_val)}（点按或悬停 K 线可看各时点均线价格）"
    )
except Exception as e:
    st.error(f"行情加载失败：{e}")
if st.button("立即刷新"):
    st.cache_data.clear()
    st.rerun()
if auto_refresh:
    components.html(
        f"""
        <script>
        setTimeout(function() {{
            const doc = window.parent.document;
            const btns = Array.from(doc.querySelectorAll('button'));
            const refreshBtn = btns.find(b => b.innerText && b.innerText.includes('立即刷新'));
            if (refreshBtn) {{
                refreshBtn.click();
            }} else {{
                window.parent.location.reload();
            }}
        }}, {int(refresh_sec * 1000)});
        </script>
        """,
        height=0,
    )
