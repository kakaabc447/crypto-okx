import time
import ccxt
import pandas as pd
import streamlit as st
st.set_page_config(page_title="短线交易员实时盯盘与出单", layout="wide")
st.title("🎯 短线交易员实时盯盘与出单信号")
# 侧边栏：行情与仓位参数
st.sidebar.header("1. 行情设置")
exchange_name = st.sidebar.selectbox("交易所", ["okx", "binance", "bybit"], index=0)
symbol = st.sidebar.text_input("交易对", value="BTC/USDT").strip().upper()
timeframe = st.sidebar.selectbox("盯盘周期", ["1m", "3m", "5m", "15m"], index=2)
limit = st.sidebar.slider("K线数量", min_value=60, max_value=250, value=120, step=10)
st.sidebar.header("2. 当前持仓与出单规则")
direction = st.sidebar.selectbox("持仓方向", ["做多 (Long)", "做空 (Short)"], index=0)
entry_price = st.sidebar.number_input("开仓均价", min_value=0.0, value=0.0, step=0.1, format="%.4f")
stop_loss_pct = st.sidebar.number_input("初始止损幅度 (%)", min_value=0.1, max_value=20.0, value=1.0, step=0.1, format="%.2f")
tp1_pct = st.sidebar.number_input("第一批止盈目标 (%)", min_value=0.2, max_value=30.0, value=1.5, step=0.1, format="%.2f")
tp2_pct = st.sidebar.number_input("第二批止盈目标 (%)", min_value=0.5, max_value=50.0, value=3.0, step=0.1, format="%.2f")
trail_pullback_pct = st.sidebar.number_input("盈利后最大回撤出单 (%)", min_value=0.2, max_value=15.0, value=0.8, step=0.1, format="%.2f")
rsi_overbought = st.sidebar.slider("RSI 超买警戒线", min_value=65, max_value=90, value=75, step=1)
rsi_oversold = st.sidebar.slider("RSI 超卖警戒线", min_value=10, max_value=35, value=25, step=1)
auto_refresh = st.sidebar.checkbox("自动刷新（每10秒）", value=True)
def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    df["ema7"] = close.ewm(span=7, adjust=False).mean()
    df["ema25"] = close.ewm(span=25, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50)
    vol_ma20 = df["volume"].rolling(window=20).mean().replace(0, pd.NA)
    df["vol_ratio"] = (df["volume"] / vol_ma20).fillna(1.0)
    df["recent_high"] = df["high"].rolling(window=20).max()
    df["recent_low"] = df["low"].rolling(window=20).min()
    return df
@st.cache_data(ttl=8)
def fetch_data(ex_id: str, sym: str, tf: str, lim: int) -> pd.DataFrame:
    ex_class = getattr(ccxt, ex_id)
    exchange = ex_class({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(sym, timeframe=tf, limit=lim)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return calc_indicators(df)
try:
    df = fetch_data(exchange_name, symbol, timeframe, limit)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last["close"])
    ema7 = float(last["ema7"])
    ema25 = float(last["ema25"])
    rsi = float(last["rsi"])
    vol_ratio = float(last["vol_ratio"])
    recent_high = float(last["recent_high"])
    recent_low = float(last["recent_low"])
    is_long = direction.startswith("做多")
    # 顶部短线盘面指标
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(f"{symbol} 最新价", f"{price:.4f}", f"{price - float(prev['close']):+.4f}")
    m2.metric("EMA7 / EMA25", f"{ema7:.4f}", f"EMA25: {ema25:.4f}")
    m3.metric("RSI(14)", f"{rsi:.1f}")
    m4.metric("量比 (vs 20均量)", f"{vol_ratio:.2f}x")
    m5.metric("近20根高低点", f"{recent_low:.4f}", f"高: {recent_high:.4f}")
    # 短线结构与盘面解读
    st.divider()
    st.subheader("🔍 短线盘面解读")
    bias_notes = []
    if price > ema7 > ema25:
        bias_notes.append("短线多头排列（价格 > EMA7 > EMA25），顺势偏多，回调不破均线可偏多看。")
    elif price < ema7 < ema25:
        bias_notes.append("短线空头排列（价格 < EMA7 < EMA25），顺势偏空，反弹承压注意风险。")
    else:
        bias_notes.append("均线缠绕/震荡结构，短线容易插针扫损，控制仓位、切忌追涨杀跌。")
    if vol_ratio >= 1.8:
        bias_notes.append(f"当前量能放大（量比 {vol_ratio:.2f}x），资金活跃，注意突破真假或冲高派发。")
    elif vol_ratio < 0.6:
        bias_notes.append(f"当前量能偏弱（量比 {vol_ratio:.2f}x），行情延续性一般，目标位建议更务实。")
    for n in bias_notes:
        st.write(f"- {n}")
    # 出单决策区
    st.divider()
    st.subheader("🚪 怎么出单（止损 / 分批止盈 / 移动保利）")
    if entry_price > 0:
        if is_long:
            pnl_pct = ((price - entry_price) / entry_price) * 100
            sl_price = entry_price * (1 - stop_loss_pct / 100)
            tp1_price = entry_price * (1 + tp1_pct / 100)
            tp2_price = entry_price * (1 + tp2_pct / 100)
            peak_price = max(price, float(df["high"].tail(10).max()))
            pullback_from_peak_pct = ((peak_price - price) / peak_price) * 100
            sl_hit = price <= sl_price
            tp1_hit = price >= tp1_price
            tp2_hit = price >= tp2_price
            ema_exit = price < ema25 and pnl_pct > 0
        else:
            pnl_pct = ((entry_price - price) / entry_price) * 100
            sl_price = entry_price * (1 + stop_loss_pct / 100)
            tp1_price = entry_price * (1 - tp1_pct / 100)
            tp2_price = entry_price * (1 - tp2_pct / 100)
            trough_price = min(price, float(df["low"].tail(10).min()))
            pullback_from_peak_pct = ((price - trough_price) / trough_price) * 100
            sl_hit = price >= sl_price
            tp1_hit = price <= tp1_price
            tp2_hit = price <= tp2_price
            ema_exit = price > ema25 and pnl_pct > 0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("当前浮动盈亏", f"{pnl_pct:+.2f}%")
        c2.metric("止损出单价", f"{sl_price:.4f}")
        c3.metric("TP1 出单价", f"{tp1_price:.4f}")
        c4.metric("TP2 出单价", f"{tp2_price:.4f}")
        if sl_hit:
            st.error(f"🚨 **立即止损出单**：当前盈亏 {pnl_pct:+.2f}%，已触及/跌破止损位 `{sl_price:.4f}`，按纪律离场。")
        elif tp2_hit:
            st.success(f"🎯 **TP2 止盈出单**：已到达第二止盈目标 `{tp2_price:.4f}`（盈亏 {pnl_pct:+.2f}%），建议清仓或留极小底仓。")
        elif tp1_hit:
            st.success(f"✅ **TP1 分批止盈出单**：已到达第一目标 `{tp1_price:.4f}`（盈亏 {pnl_pct:+.2f}%），短线常见做法是先出 50% 仓位锁定利润，剩余止损移至开仓价附近。")
        else:
            st.info(f"⏳ **持仓观察中**：当前盈亏 {pnl_pct:+.2f}%，尚未触及 TP1/止损。")
        if pnl_pct > 0 and pullback_from_peak_pct >= trail_pullback_pct:
            st.warning(f"🛡️ **移动止盈/保利出单提示**：近期阶段极值回撤已达 {pullback_from_peak_pct:.2f}%（≥ 设定 {trail_pullback_pct:.2f}%），防止利润回吐可考虑出单。")
        if ema_exit:
            st.warning(f"📉 **均线破位保利出单**：当前处于浮盈状态但价格已反穿 EMA25（`{ema25:.4f}`），短线动能转弱可考虑减仓或离场。")
        if is_long and rsi >= rsi_overbought and pnl_pct > 0:
            st.warning(f"🔥 **超买派发出单提示**：做多且 RSI 达到 {rsi:.1f}（≥ {rsi_overbought}），短线情绪过热，建议分批落袋。")
        elif (not is_long) and rsi <= rsi_oversold and pnl_pct > 0:
            st.warning(f"🔥 **超卖回补出单提示**：做空且 RSI 跌至 {rsi:.1f}（≤ {rsi_oversold}），短线空头动能趋缓，建议分批平空。")
    else:
        st.info("在左侧“2. 当前持仓与出单规则”里填入**开仓均价**，系统就会自动算出止损价、TP1/TP2 出单价以及移动保利/超买出单提示。")
    # 价格与均线图
    st.subheader("📈 价格与均线走势")
    st.line_chart(df.set_index("time")[["close", "ema7", "ema25"]], height=280)
except Exception as e:
    st.error(f"拉取行情失败：{e}")
    st.caption("如遇网络或交易所接口限制，可在左侧切换交易所（如 okx / bybit / binance）或检查交易对格式。")
col_btn1, col_btn2 = st.columns([1, 5])
if col_btn1.button("立即刷新"):
    st.cache_data.clear()
    st.rerun()
if auto_refresh:
    time.sleep(10)
    st.rerun()
