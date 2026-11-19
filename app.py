import time
import ccxt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="短线行情与退出策略监测", layout="wide")
st.title("⚡ 短线行情与退出策略监测（手机网页版）")

# 侧边栏参数设置
st.sidebar.header("参数设置")
exchange_name = st.sidebar.selectbox("交易所", ["binance", "okx", "bybit"], index=0)
symbol = st.sidebar.text_input("交易对", value="BTC/USDT").strip().upper()
timeframe = st.sidebar.selectbox("K线周期", ["1m", "3m", "5m", "15m"], index=2)

st.sidebar.header("持仓与退出策略")
entry_price = st.sidebar.number_input("开仓均价 (USDT)", min_value=0.0, value=0.0, step=10.0, format="%.4f")
stop_loss_pct = st.sidebar.slider("止损比例 (%)", min_value=0.2, max_value=10.0, value=1.5, step=0.1)
take_profit_pct = st.sidebar.slider("止盈目标 (%)", min_value=0.5, max_value=20.0, value=3.0, step=0.1)
rsi_overbought = st.sidebar.slider("短线超买 RSI 阈值", min_value=65, max_value=90, value=75, step=1)
auto_refresh = st.sidebar.checkbox("自动刷新（每15秒）", value=False)


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


@st.cache_data(ttl=10)
def fetch_market_data(ex_id: str, sym: str, tf: str, limit: int = 100):
    ex_class = getattr(ccxt, ex_id)
    exchange = ex_class({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(sym, timeframe=tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["rsi"] = calc_rsi(df["close"], period=14)
    return df


try:
    df = fetch_market_data(exchange_name, symbol, timeframe, limit=100)
    latest_price = float(df["close"].iloc[-1])
    latest_rsi = float(df["rsi"].iloc[-1])

    # 顶部核心指标
    col1, col2, col3 = st.columns(3)
    col1.metric(f"{symbol} 当前价", f"{latest_price:.4f}")
    col2.metric(f"RSI(14) [{timeframe}]", f"{latest_rsi:.2f}")

    if entry_price > 0:
        pnl_pct = ((latest_price - entry_price) / entry_price) * 100
        col3.metric("当前浮动盈亏", f"{pnl_pct:+.2f}%")
    else:
        col3.metric("当前浮动盈亏", "未填开仓价")

    st.divider()
    st.subheader("🧭 退出策略建议")

    signals = []
    if entry_price > 0:
        pnl_pct = ((latest_price - entry_price) / entry_price) * 100
        stop_price = entry_price * (1 - stop_loss_pct / 100)
        tp_price = entry_price * (1 + take_profit_pct / 100)

        st.write(f"- **参考止损价**：`{stop_price:.4f}`（-{stop_loss_pct:.1f}%）")
        st.write(f"- **参考止盈价**：`{tp_price:.4f}`（+{take_profit_pct:.1f}%）")

        if pnl_pct <= -stop_loss_pct:
            signals.append(("error", f"🚨 **触发止损线**：当前亏损 {pnl_pct:.2f}%，已跌破设定止损位，建议严格执行退出或减仓。"))
        elif pnl_pct >= take_profit_pct:
            signals.append(("success", f"🎯 **达到止盈目标**：当前盈利 {pnl_pct:.2f}%，可考虑分批止盈或上移保本/移动止损。"))
        else:
            signals.append(("info", f"⏳ **持仓观察中**：当前盈亏 {pnl_pct:+.2f}%，尚未触及止损或止盈线。"))

    if latest_rsi >= rsi_overbought:
        signals.append(("warning", f"⚠️ **短线超买提示**：{timeframe} 周期 RSI 达到 {latest_rsi:.2f}（≥ {rsi_overbought}），短线冲高回落风险增大，可考虑部分落袋为安。"))

    if not signals:
        signals.append(("info", "当前未触发特定退出条件，请在左侧填入“开仓均价”以启用盈亏止盈止损监控。"))

    for level, msg in signals:
        getattr(st, level)(msg)

    # 价格走势图
    st.subheader("📈 近期收盘价走势")
    st.line_chart(df.set_index("time")["close"], height=260)

except Exception as e:
    st.error(f"获取数据失败：{e}")
    st.caption("提示：部分交易所接口在海外/云端节点可能受网络或交易对格式影响，可切换交易所或检查交易对写法（如 BTC/USDT）。")

if st.button("立即刷新"):
    st.cache_data.clear()
    st.rerun()

if auto_refresh:
    time.sleep(15)
    st.rerun()
