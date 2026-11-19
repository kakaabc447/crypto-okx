import time
import ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

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

st.sidebar.header("2. 自动刷新")
refresh_sec = st.sidebar.slider("刷新间隔(秒)", min_value=5, max_value=60, value=15, step=5)
auto_refresh = st.sidebar.checkbox("开启静默自动刷新", value=True)

st.sidebar.header("3. 策略过滤与去重")
enable_rsi_assist = st.sidebar.checkbox("启用 RSI(14) 辅助提示", value=True)
dedup_pct = st.sidebar.number_input(
    "价格去重间距 (%)",
    min_value=0.1,
    max_value=10.0,
    value=0.6,
    step=0.1,
    format="%.2f",
    help="同交易所、同币种、同档位、同方向下，新价格与上一条参考价差距小于该比例时不重复出单",
)
max_strategy_items = st.sidebar.slider("最多保留策略条数", min_value=3, max_value=15, value=6, step=1)

# 持仓与出单提示状态初始化（支持一键填入短线参考）
for k, default_v in [
    ("pos_direction", "做多 (Long)"),
    ("pos_entry_price", 0.0),
    ("pos_sl_pct", 1.0),
    ("pos_tp1_pct", 1.5),
    ("pos_tp2_pct", 3.0),
]:
    if k not in st.session_state:
        st.session_state[k] = default_v

if "strategy_vault" not in st.session_state:
    st.session_state["strategy_vault"] = {}

st.sidebar.header("4. 持仓与出单提示")
direction = st.sidebar.selectbox("持仓方向", ["做多 (Long)", "做空 (Short)"], key="pos_direction")
entry_price = st.sidebar.number_input(
    "开仓均价", min_value=0.0, step=0.1, format="%.4f", key="pos_entry_price"
)
sl_pct = st.sidebar.number_input(
    "止损幅度 (%)", min_value=0.1, max_value=20.0, step=0.1, format="%.2f", key="pos_sl_pct"
)
tp1_pct = st.sidebar.number_input(
    "第一止盈 TP1 (%)", min_value=0.2, max_value=30.0, step=0.1, format="%.2f", key="pos_tp1_pct"
)
tp2_pct = st.sidebar.number_input(
    "第二止盈 TP2 (%)", min_value=0.5, max_value=50.0, step=0.1, format="%.2f", key="pos_tp2_pct"
)


def _fmt_price(val: float) -> str:
    if abs(val) >= 100:
        return f"${val:,.2f}"
    if abs(val) >= 1:
        return f"${val:,.4f}"
    return f"${val:,.6f}"


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


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
    df["rsi14"] = _calc_rsi(df["close"], period=14)
    return df


def _build_dynamic_advice(
    is_long: bool,
    pnl: float,
    last_p: float,
    prev_p: float,
    ema7_val: float,
    prev_ema7: float,
    ema25_val: float,
    hit_sl: bool,
    hit_tp1: bool,
    hit_tp2: bool,
) -> tuple[str, str]:
    ema7_rising = ema7_val > prev_ema7
    ema7_falling = ema7_val < prev_ema7
    price_rising = last_p > prev_p

    if hit_sl:
        return ("error", "🚨 已触及止损线：建议严格按止损出单，避免扛单扩大亏损。")
    if hit_tp2:
        return ("success", "🎯 已到达 TP2 目标位：冲高/下跌到位，建议分批或大部分止盈出单，锁定利润。")

    if is_long:
        if hit_tp1:
            if last_p < ema7_val or ema7_falling:
                return ("warning", "✅ 已过 TP1 且短线动能减弱（跌破 EMA7 或 EMA7 拐头）：建议先落袋一部分，剩余仓位止损上移。")
            return ("success", "✅ 已到达 TP1 目标位：可先止盈部分仓位，顺势持有剩余仓位博弈 TP2。")
        if pnl > 0 and (last_p < ema7_val or ema7_falling):
            return ("warning", "⚠️ 多单浮盈但短线动能减弱（价格跌破 EMA7 或 EMA7 转弱）：建议考虑减仓一部分或收紧止损，防冲高回落。")
        if last_p < ema25_val:
            return ("warning", "⚠️ 价格处于 EMA25 下方且未到 TP1：多头节奏偏弱，注意控制仓位与防守线。")
        if last_p > ema7_val and ema7_rising and price_rising:
            return ("info", "📈 多头动能仍在（站稳 EMA7 上方且 EMA7 向上）：趋势尚可，可按计划持有并盯紧 TP1 与止损位。")
        return ("info", "⏳ 多单持仓观察中：留意 EMA7 支撑与 TP1/TP2 距离。")
    else:
        if hit_tp1:
            if last_p > ema7_val or ema7_rising:
                return ("warning", "✅ 已过 TP1 且下跌动能减弱（反弹站上 EMA7 或 EMA7 拐头向上）：建议先止盈一部分落袋，空单止损下移保护利润。")
            return ("success", "✅ 已到达 TP1 目标位：可先分批止盈，顺势拿住剩余空单看 TP2。")
        if pnl > 0 and (last_p > ema7_val or ema7_rising):
            return ("warning", "⚠️ 空单浮盈但下跌动能减弱（反弹靠近/站上 EMA7 或均线走平偏升）：建议考虑分批走一部分或下移止损，防反弹洗盘。")
        if last_p > ema25_val:
            return ("warning", "⚠️ 价格处于 EMA25 上方且未到 TP1：空头动能偏弱，注意反弹风险与止损位置。")
        if last_p < ema7_val and ema7_falling and not price_rising:
            return ("info", "📉 空头动能仍在（运行在 EMA7 下方且 EMA7 向下）：顺势节奏尚可，继续盯紧 TP1 与止损线。")
        return ("info", "⏳ 空单持仓观察中：留意 EMA7 阻力与 TP1/TP2 距离。")


def _rsi_note(side: str, rsi_val: float, use_rsi: bool) -> str:
    if not use_rsi:
        return ""
    if side.startswith("做多"):
        if rsi_val >= 72:
            return f"｜RSI(14)={rsi_val:.1f} 偏高，注意短线冲高回落、别追太高"
        if rsi_val <= 35:
            return f"｜RSI(14)={rsi_val:.1f} 偏低回升，顺势留意企稳"
        return f"｜RSI(14)={rsi_val:.1f} 处于中性区间"
    if side.startswith("做空"):
        if rsi_val <= 28:
            return f"｜RSI(14)={rsi_val:.1f} 偏低，防短线超跌反弹、别在地板追空"
        if rsi_val >= 65:
            return f"｜RSI(14)={rsi_val:.1f} 偏高回落，顺势留意阻力"
        return f"｜RSI(14)={rsi_val:.1f} 处于中性区间"
    return f"｜RSI(14)={rsi_val:.1f}"


def _evaluate_candidate_strategies(df: pd.DataFrame, use_rsi: bool) -> list[dict]:
    last_p = float(df["close"].iloc[-1])
    prev_p = float(df["close"].iloc[-2]) if len(df) > 1 else last_p
    ema7 = float(df["ema7"].iloc[-1])
    prev_ema7 = float(df["ema7"].iloc[-2]) if len(df) > 1 else ema7
    ema25 = float(df["ema25"].iloc[-1])
    prev_ema25 = float(df["ema25"].iloc[-2]) if len(df) > 1 else ema25
    rsi_val = float(df["rsi14"].iloc[-1]) if "rsi14" in df.columns else 50.0

    # 震荡过滤：结合 EMA7/EMA25 间距与近 12 根 K 线高低振幅
    spread_pct = abs(ema7 - ema25) / ema25 * 100 if ema25 else 0.0
    win = df.tail(min(len(df), 12))
    range_pct = ((float(win["high"].max()) - float(win["low"].min())) / last_p * 100) if last_p else 0.0
    is_choppy = spread_pct < 0.25 or range_pct < 0.85

    ema7_up = ema7 > prev_ema7
    ema7_down = ema7 < prev_ema7
    ema25_up = ema25 >= prev_ema25
    ema25_down = ema25 <= prev_ema25
    cross_up = prev_ema7 <= prev_ema25 and ema7 > ema25
    cross_down = prev_ema7 >= prev_ema25 and ema7 < ema25

    candidates: list[dict] = []

    # 1. 稳健短线档（偏顺势均线结构 + 震荡过滤）
    if not is_choppy and ema7 > ema25 and ema7_up and ema25_up and last_p >= ema25:
        pullback = abs(last_p - ema7) / ema7 <= 0.008
        reason = (
            "震荡过滤通过，EMA7 高于 EMA25 且双均线向上，价格回踩 EMA7 附近企稳。"
            if pullback
            else "震荡过滤通过，EMA7 高于 EMA25 且双均线向上，多头结构顺势参考。"
        )
        candidates.append({
            "mode": "稳健短线",
            "side": "做多 (Long)",
            "badge": "🟢 稳健做多",
            "reason": reason + _rsi_note("做多 (Long)", rsi_val, use_rsi),
            "entry": last_p,
            "sl_pct": 1.2,
            "tp1_pct": 1.8,
            "tp2_pct": 3.5,
        })
    elif not is_choppy and ema7 < ema25 and ema7_down and ema25_down and last_p <= ema25:
        rebound = abs(last_p - ema7) / ema7 <= 0.008
        reason = (
            "震荡过滤通过，EMA7 低于 EMA25 且双均线向下，价格反弹靠近 EMA7 受阻。"
            if rebound
            else "震荡过滤通过，EMA7 低于 EMA25 且双均线向下，空头结构顺势参考。"
        )
        candidates.append({
            "mode": "稳健短线",
            "side": "做空 (Short)",
            "badge": "🔴 稳健做空",
            "reason": reason + _rsi_note("做空 (Short)", rsi_val, use_rsi),
            "entry": last_p,
            "sl_pct": 1.2,
            "tp1_pct": 1.8,
            "tp2_pct": 3.5,
        })

    # 2. 快节奏短线档（偏近期 EMA7 拐头/突破 + 震荡过滤）
    if not is_choppy and (cross_up or (ema7_up and last_p > ema7 and last_p > prev_p)):
        reason = (
            "EMA7 上穿 EMA25 且价格走高，快节奏偏多信号。"
            if cross_up
            else "价格站上 EMA7 且 EMA7 向上拐头，快节奏短多信号。"
        )
        candidates.append({
            "mode": "快节奏短线",
            "side": "做多 (Long)",
            "badge": "⚡ 快多脉冲",
            "reason": reason + _rsi_note("做多 (Long)", rsi_val, use_rsi),
            "entry": last_p,
            "sl_pct": 0.7,
            "tp1_pct": 1.0,
            "tp2_pct": 1.8,
        })
    elif not is_choppy and (cross_down or (ema7_down and last_p < ema7 and last_p < prev_p)):
        reason = (
            "EMA7 下穿 EMA25 且价格走弱，快节奏偏空信号。"
            if cross_down
            else "价格跌破 EMA7 且 EMA7 向下拐头，快节奏短空信号。"
        )
        candidates.append({
            "mode": "快节奏短线",
            "side": "做空 (Short)",
            "badge": "⚡ 快空脉冲",
            "reason": reason + _rsi_note("做空 (Short)", rsi_val, use_rsi),
            "entry": last_p,
            "sl_pct": 0.7,
            "tp1_pct": 1.0,
            "tp2_pct": 1.8,
        })

    for item in candidates:
        ep = float(item["entry"])
        if item["side"].startswith("做多"):
            item["sl_price"] = ep * (1 - item["sl_pct"] / 100)
            item["tp1_price"] = ep * (1 + item["tp1_pct"] / 100)
            item["tp2_price"] = ep * (1 + item["tp2_pct"] / 100)
        else:
            item["sl_price"] = ep * (1 + item["sl_pct"] / 100)
            item["tp1_price"] = ep * (1 - item["tp1_pct"] / 100)
            item["tp2_price"] = ep * (1 - item["tp2_pct"] / 100)

    return candidates


def _sync_strategy_vault(
    vault_key: str,
    candidates: list[dict],
    min_gap_pct: float,
    max_items: int,
) -> int:
    """将符合条件且价格不重复的策略锁定快照存入独立策略区，返回本次新增条数。"""
    items: list[dict] = list(st.session_state["strategy_vault"].get(vault_key, []))
    added = 0

    for cand in candidates:
        cand_entry = float(cand["entry"])
        is_duplicate = False
        for existing in items:
            if existing["mode"] == cand["mode"] and existing["side"] == cand["side"]:
                prev_entry = float(existing["entry"])
                if prev_entry > 0:
                    gap = abs(cand_entry - prev_entry) / prev_entry * 100
                    if gap < min_gap_pct:
                        is_duplicate = True
                        break
        if not is_duplicate:
            snapshot = {
                "id": f"{int(time.time() * 1000)}_{added}_{cand['mode']}",
                "created_at": time.strftime("%H:%M:%S"),
                "mode": cand["mode"],
                "side": cand["side"],
                "badge": cand["badge"],
                "reason": cand["reason"],
                "entry": cand_entry,
                "sl_pct": float(cand["sl_pct"]),
                "tp1_pct": float(cand["tp1_pct"]),
                "tp2_pct": float(cand["tp2_pct"]),
                "sl_price": float(cand["sl_price"]),
                "tp1_price": float(cand["tp1_price"]),
                "tp2_price": float(cand["tp2_price"]),
            }
            items.insert(0, snapshot)
            added += 1

    items = items[:max_items]
    st.session_state["strategy_vault"][vault_key] = items
    return added


def _apply_snapshot_to_position(snap: dict, sym_key: str) -> None:
    st.session_state["pos_direction"] = snap["side"]
    st.session_state["pos_entry_price"] = float(round(float(snap["entry"]), 6))
    st.session_state["pos_sl_pct"] = float(snap["sl_pct"])
    st.session_state["pos_tp1_pct"] = float(snap["tp1_pct"])
    st.session_state["pos_tp2_pct"] = float(snap["tp2_pct"])
    st.toast(f"已将锁定快照【{snap['mode']} · {snap['side']}】填入左侧出单提示（{sym_key}）")


interval_arg = refresh_sec if auto_refresh else None


@st.fragment(run_every=interval_arg)
def render_live_panel() -> None:
    active_symbol = symbol_input or "BTC/USDT"
    vault_key = f"{exchange_choice}_{active_symbol}_{timeframe}"
    st.subheader(f"📌 当前选中币种实时卡片（{active_symbol} · {exchange_choice.upper()}）")

    try:
        df = fetch_ohlcv_by_exchange(exchange_choice, active_symbol, timeframe, limit)
        last_p = float(df["close"].iloc[-1])
        prev_p = float(df["close"].iloc[-2]) if len(df) > 1 else last_p
        chg = ((last_p - prev_p) / prev_p) * 100 if prev_p else 0.0
        ema7_val = float(df["ema7"].iloc[-1])
        prev_ema7 = float(df["ema7"].iloc[-2]) if len(df) > 1 else ema7_val
        ema25_val = float(df["ema25"].iloc[-1])
        rsi_val = float(df["rsi14"].iloc[-1]) if "rsi14" in df.columns else 50.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{active_symbol} 最新价", _fmt_price(last_p), f"{chg:+.2f}%")
        c2.metric("EMA7 价格", _fmt_price(ema7_val), f"价差 {last_p - ema7_val:+.4f}")
        c3.metric(
            "EMA25 价格",
            _fmt_price(ema25_val),
            f"RSI14 {rsi_val:.1f}" if enable_rsi_assist else f"价差 {last_p - ema25_val:+.4f}",
        )
        c4.metric(
            "刷新状态",
            f"🟢 每 {refresh_sec}s" if auto_refresh else "⏸️ 已暂停",
            f"{exchange_choice.upper()} · {timeframe}",
        )

        if entry_price > 0:
            st.divider()
            st.subheader("🚪 出单参考（止损 / TP1 / TP2 与动态建议）")
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

            level, advice_text = _build_dynamic_advice(
                is_long=is_long,
                pnl=pnl,
                last_p=last_p,
                prev_p=prev_p,
                ema7_val=ema7_val,
                prev_ema7=prev_ema7,
                ema25_val=ema25_val,
                hit_sl=hit_sl,
                hit_tp1=hit_tp1,
                hit_tp2=hit_tp2,
            )
            if level == "error":
                st.error(advice_text)
            elif level == "success":
                st.success(advice_text)
            elif level == "warning":
                st.warning(advice_text)
            else:
                st.info(advice_text)

        # 独立策略区：锁定快照、震荡过滤 + RSI 辅助、价格接近去重
        st.divider()
        hdr_l, hdr_r = st.columns([4, 1])
        with hdr_l:
            st.subheader(f"🎯 独立短线策略区（{active_symbol} · {timeframe} · 锁价去重）")
        with hdr_r:
            if st.button("清空本币策略", key=f"clear_vault_{vault_key}", use_container_width=True):
                st.session_state["strategy_vault"][vault_key] = []
                st.rerun(scope="fragment")

        candidates = _evaluate_candidate_strategies(df, use_rsi=enable_rsi_assist)
        added_cnt = _sync_strategy_vault(
            vault_key=vault_key,
            candidates=candidates,
            min_gap_pct=float(dedup_pct),
            max_items=int(max_strategy_items),
        )
        vault_items = st.session_state["strategy_vault"].get(vault_key, [])

        status_note = (
            f"本次新增 {added_cnt} 条策略快照（去重阈值：{dedup_pct:.2f}%；已锁定参考价，不随现价跳动）"
            if added_cnt > 0
            else f"当前无新不重复单据（去重阈值：{dedup_pct:.2f}%；已有快照已锁定参考价，不随现价跳动）"
        )
        st.caption(status_note)

        if not vault_items:
            st.info(
                "当前处于震荡过滤或暂无新信号，独立策略区暂未生成新单据。满足趋势条件且价格拉开间距后会自动出现在这里。"
            )
        else:
            for idx, snap in enumerate(vault_items):
                with st.container(border=True):
                    top_a, top_b, top_c = st.columns([2, 2, 1.4])
                    top_a.markdown(f"**#{idx + 1} · {snap['mode']}** — {snap['badge']}（**{snap['side']}**）")
                    top_b.caption(f"锁定时间：{snap['created_at']}（价位已冻结）")
                    if top_c.button(
                        "填入出单提示",
                        key=f"use_snap_{vault_key}_{snap['id']}",
                        use_container_width=True,
                    ):
                        _apply_snapshot_to_position(snap, active_symbol)
                        st.rerun()

                    st.caption(snap["reason"])
                    p1, p2, p3, p4 = st.columns(4)
                    p1.write(f"锁定入场：**{_fmt_price(snap['entry'])}**")
                    p2.write(f"锁定止损：**{_fmt_price(snap['sl_price'])}** (-{snap['sl_pct']:.1f}%)")
                    p3.write(f"锁定 TP1：**{_fmt_price(snap['tp1_price'])}** (+{snap['tp1_pct']:.1f}%)")
                    p4.write(f"锁定 TP2：**{_fmt_price(snap['tp2_price'])}** (+{snap['tp2_pct']:.1f}%)")

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
        st.rerun(scope="fragment")


render_live_panel()
