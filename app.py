import time
import ccxt
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
st.set_page_config(page_title="WEEX / OKX / Gate 实时合约盯盘", layout="wide")
st.title("⚡ WEEX / OKX / Gate 实时盯盘与出单参考")
st.sidebar.header("1. 交易所与行情设置")
exchange_choice = st.sidebar.selectbox("选择交易所", ["weex", "okx", "gate"], index=0)
timeframe = st.sidebar.selectbox("K线周期", ["1m", "5m", "15m", "1h"], index=1)
limit = st.sidebar.slider("K线根数", min_value=50, max_value=200, value=120, step=10)
refresh_sec = st.sidebar.slider("自动刷新间隔(秒)", min_value=5, max_value=60, value=10, step=5)
auto_refresh = st.sidebar.checkbox("开启实时自动刷新", value=True)
st.sidebar.header("2. 短线仓位与出单提示")
direction = st.sidebar.selectbox("持仓方向", ["做多 (Long)", "做空 (Short)"], index=0)
entry_price = st.sidebar.number_input("开仓均价", min_value=0.0, value=0.0, step=0.1, format="%.4f")
sl_pct = st.sidebar.number_input("止损幅度 (%)", min_value=0.1, max_value=20.0, value=1.0, step=0.1, format="%.2f")
tp1_pct = st.sidebar.number_input("第一止盈 TP1 (%)", min_value=0.2, max_value=30.0, value=1.5, step=0.1, format="%.2f")
tp2_pct = st.sidebar.number_input("第二止盈 TP2 (%)", min_value=0.5, max_value=50.0, value=3.0, step=0.1, format="%.2f")
card_coins = ["BTC", "DOGE", "AVAX"]
all_coins = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "SUI"]
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
    df = pd.DataFrame(rows)
    # 兼容列表或字典格式
    if isinstance(rows[0], dict):
        df = pd.DataFrame({
            "ts": pd.to_numeric(df.get("ts", df.get("timestamp", 0))),
            "open": pd.to_numeric(df["open"]),
            "high": pd.to_numeric(df["high"]),
            "low": pd.to_numeric(df["low"]),
            "close": pd.to_numeric(df["close"]),
            "volume": pd.to_numeric(df.get("volume", 0)),
        })
    else:
        df = pd.DataFrame({
            "ts": pd.to_numeric(df[0]),
            "open": pd.to_numeric(df[1]),
            "high": pd.to_numeric(df[2]),
            "low": pd.to_numeric(df[3]),
            "close": pd.to_numeric(df[4]),
            "volume": pd.to_numeric(df[5] if df.shape[1] > 5 else 0),
        })
    df = df.sort_values("ts").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["ts"], unit="ms")
    return df
@st.cache_data(ttl=5)
def fetch_ohlcv_by_exchange(ex_name: str, coin: str, tf: str, lim: int) -> pd.DataFrame:
    coin_u = coin.strip().upper()
    if ex_name == "weex":
        try:
            if hasattr(ccxt, "weex"):
                ex = getattr(ccxt, "weex")({"enableRateLimit": True})
                for sym in (f"{coin_u}/USDT:USDT", f"{coin_u}/USDT"):
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
        # 合约优先尝试线性永续符号，失败回退现货符号
        candidates = [f"{coin_u}/USDT:USDT", f"{coin_u}/USDT"]
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
            raise RuntimeError(last_err or f"无法获取 {ex_name} {coin_u} 行情")
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["ts"], unit="ms")
    df["ema7"] = df["close"].ewm(span=7, adjust=False).mean()
    df["ema25"] = df["close"].ewm(span=25, adjust=False).mean()
    return df
st.subheader(f"📌 {exchange_choice.upper()} 热门币种实时卡片")
cols = st.columns(3)
loaded_frames = {}
for idx, c in enumerate(card_coins):
    with cols[idx]:
        try:
            d = fetch_ohlcv_by_exchange(exchange_choice, c, timeframe, limit)
            loaded_frames[c] = d
            last_p = float(d["close"].iloc[-1])
            prev_p = float(d["close"].iloc[-2])
            chg = ((last_p - prev_p) / prev_p) * 100 if prev_p else 0.0
            st.markdown(f"**{c} / USDT** · `{exchange_choice.upper()}`")
            st.metric(
                label="最新价",
                value=f"${last_p:,.2f}" if last_p >= 100 else f"${last_p:,.4f}",
                delta=f"{chg:+.2f}%",
            )
            st.caption("🟢 数据已加载")
        except Exception as e:
            st.markdown(f"**{c} / USDT**")
            st.warning(f"暂未拉取到：{e}")
st.divider()
st.subheader("📉 币种详情、K线与出单参考")
selected_coin = st.selectbox("选择详情币种", all_coins, index=0)
try:
    df = loaded_frames.get(selected_coin) or fetch_ohlcv_by_exchange(
        exchange_choice, selected_coin, timeframe, limit
    )
    last_p = float(df["close"].iloc[-1])
    prev_p = float(df["close"].iloc[-2])
    chg = ((last_p - prev_p) / prev_p) * 100 if prev_p else 0.0
    st.markdown(f"**{selected_coin}USDT · {exchange_choice.upper()}**")
    st.metric(
        label="实时价格",
        value=f"${last_p:,.2f}" if last_p >= 100 else f"${last_p:,.4f}",
        delta=f"{chg:+.2f}%",
    )
    if entry_price > 0:
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
        k2.metric("止损价", f"{sl_p:.4f}")
        k3.metric("TP1 价", f"{tp1_p:.4f}")
        k4.metric("TP2 价", f"{tp2_p:.4f}")
        if hit_sl:
            st.error("🚨 触发止损条件，建议按止损线出单。")
        elif hit_tp2:
            st.success("🎯 到达 TP2 目标，建议分批/清仓止盈出单。")
        elif hit_tp1:
            st.success("✅ 到达 TP1 目标，可先出一部分落袋并把止损推向保本。")
        else:
            st.info("⏳ 持仓观察中，尚未触及止损或 TP1。")
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
            go.Scatter(x=df["time"], y=df["ema7"], mode="lines", name="EMA7", line=dict(width=1.3)),
            go.Scatter(x=df["time"], y=df["ema25"], mode="lines", name="EMA25", line=dict(width=1.3)),
        ]
    )
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{exchange_choice.upper()} · {timeframe} K线 · {len(df)} 根")
except Exception as e:
    st.error(f"行情加载失败：{e}")
if st.button("立即刷新"):
    st.cache_data.clear()
    st.rerun()
if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()

