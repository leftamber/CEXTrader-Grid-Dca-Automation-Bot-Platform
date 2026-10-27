# CEXTrader — Free Open-Source Crypto Trading Bot (Grid Bot + DCA Bot) for Binance, Coinbase, Kraken, Bybit, OKX & 12+ Exchanges

**CEXTrader** is a free, self-hosted **automated crypto trading bot** with a **grid trading bot**, **DCA bot (dollar-cost averaging)**, **momentum**, **VWAP** and **adaptive market-regime** strategies for **spot** and **USDT-M perpetual futures** — BTC, ETH, SOL, XRP, DOGE and hundreds of altcoin pairs on Binance, Coinbase, Kraken, Bybit, OKX, Bitget, Gate.io, KuCoin, MEXC, HTX, **Hyperliquid** and **dYdX v4**. Built with **Python (FastAPI)** and a dark minimal **no-code web dashboard** — no subscription, no cloud, **API keys stay on your machine**. Includes **paper trading** (simulated live-market trading), **crypto backtesting** on real candles, live **order book** depth, and a full **risk management** layer (stop-loss, take-profit, trailing TP, kill-switch).

Runs 100% locally on **Windows, macOS and Linux** — or 24/7 on a cheap VPS. A self-hosted alternative to subscription bot platforms: your bot, your server, your keys, your profits.

## Why CEXTrader?

- **Free crypto trading bot** — no subscription, no profit cut, no cloud account.
- **Self-hosted & private** — trades only through your own exchange API keys; nothing is sent to third-party services.
- **6 bot types in one terminal**: Grid, DCA, Combo (Grid + DCA), Momentum, VWAP, Adaptive — more strategies than most paid **bitcoin trading bot** platforms.
- **Spot + USDT-M perpetual futures** — long, short and hedged setups with leverage control.
- **12+ exchanges via one interface** — Coinbase, Kraken, Binance, OKX, Bybit, Bitget, Gate.io, KuCoin, MEXC, HTX, Hyperliquid, dYdX.
- **Paper trading simulator** with virtual balance — test any **altcoin trading bot** setup against real live prices with zero risk.
- **Backtesting engine** — ROI, APR, max drawdown, fee-aware trade list on real candle data.
- **No-code bot builder** — 5-step wizard for beginners, plus an **Expert JSON editor** for advanced quants.
- **Multi-bot, multi-exchange** — run unlimited bots in parallel across different pairs, markets and accounts.

## Table of Contents

- [Supported Exchanges](#supported-exchanges)
- [Trading Strategies](#trading-strategies)
- [Risk Management & Safety](#risk-management--safety)
- [Paper Trading & Backtesting](#paper-trading--backtesting)
- [Dashboard Overview](#dashboard-overview)
- [Web Interface](#web-interface)
- [Installation & Quick Start](#installation--quick-start)
- [Project Structure](#project-structure)
- [Strategy Settings Reference (Expert JSON)](#strategy-settings-reference-expert-json)
- [Paper Mode Explained](#paper-mode-explained)
- [FAQ](#faq)
- [Security & Deployment Notes](#security--deployment-notes)
- [Disclaimer](#disclaimer)

## Supported Exchanges

Unified exchange abstraction via **ccxt** (REST) with a ccxt.pro-ready architecture, plus **native SDK adapters** for decentralized perp DEXs. Spot and **USDT-M perpetual futures** are supported wherever the venue offers USDT-M contracts (inverse contracts are filtered out automatically).

| Exchange | Markets | Notes |
|---|---|---|
| **Coinbase** | Spot | Regulated exchange, deep BTC/ETH liquidity, quick API-key connect with one-click connection test |
| **Kraken** | Spot | Regulated venue, strong USD/EUR fiat pairs |
| **Binance** | Spot + USDT-M Perps | Highest global liquidity; hundreds of altcoin pairs |
| **OKX** | Spot + USDT-M Perps | Deep order books, popular worldwide |
| **Bybit** | Spot + USDT-M Perps | Futures-first exchange |
| **Bitget** | Spot + USDT-M Perps | Wide perp coverage |
| **Gate.io** | Spot + USDT-M Perps | Wide altcoin listing count |
| **KuCoin** | Spot + USDT-M Perps | Popular altcoin venue |
| **MEXC** | Spot + USDT-M Perps | Large altcoin listing count |
| **HTX (Huobi)** | Spot + USDT-M Perps | Legacy deep liquidity |
| **Hyperliquid** | Perp DEX (native `hyperliquid-python-sdk`, ccxt fallback) | On-chain perps, no KYC, API-wallet auth |
| **dYdX v4** | Perp DEX (native `dydx-v4-client`) | Decentralized perps, mnemonic auth |

API keys are entered locally (Dashboard → **Exchange Config** or the Accounts page) and tested with a one-click connection check.

## Trading Strategies

### Grid Trading Bot — automated grid trading for ranging markets
Arithmetic and **geometric grid** with neutral/long/short grid modes. Budget-split, fixed-quote and fixed-qty level sizing, fee-buffered step spacing, **post-only (ALO) maker orders**, infinity-rebuild when price exits the range (or hold/stop), **inventory skew** rebalancing, sell-from-inventory starts, **stop-loss** with inventory liquidation, realized-PnL take-profit, and max inventory limits. The classic **spot scalping grid** and **futures grid bot** in one engine — for BTC, ETH, SOL, XRP or any pair with enough volatility.

### DCA Bot — dollar-cost averaging with martingale safety orders
Base order (% or fixed quote), configurable **safety orders** with deviation step scaling (**martingale step**) and **volume scaling**, take-profit from average entry price (+ **trailing take-profit**), stop-loss, **short mode**, cooldown between cycles, max active cycles, higher-timeframe **trend filter (EMA/RSI)**, market or limit safety orders. The full **DCA crypto bot** toolkit for accumulating dips on BTC, ETH and altcoins.

### Combo Bot — Grid + DCA on one pair
Runs **grid trading and DCA simultaneously** on the same market with a split budget — for portfolios that want both ranging income and dip accumulation.

### Momentum Bot — EMA crossover trend following
Entries on fast/slow **EMA crossover**, volume filter (vs moving average) and **RSI filter**, SL/TP with trailing after the TP zone, exit on opposite cross, max hold time, cooldown, long or short. A classic **swing trading bot** for trending phases.

### VWAP Bot — mean reversion & reclaim strategies
Rolling **VWAP** (windowed or anchored) with two modes: **mean-reversion** (enter on deviation, exit at VWAP) and **reclaim** (enter on VWAP reclaim with EMA/volume confirmation), take-profit, stop-loss, short mode, daily trade limit, cooldown.

### Adaptive Bot — automatic market-regime switching
**RegimeDetector** using **ADX/DMI, ATR% and Bollinger Band width** classifies the market: ranging → Grid + DCA; trending up/down → Momentum; high volatility → stand aside (or continue). The grid can keep running in trends (**accelerator mode**). Module budgets are set as percentages — a **hands-off adaptive trading bot**.

## Risk Management & Safety

Built-in **crypto risk management** on every bot:

- **Stop-loss** and **take-profit** per strategy (price- or percent-based, fee-inclusive option)
- **Trailing take-profit** to lock in runs
- **Max position size** in quote currency
- **Daily loss limit** with automatic bot pause
- **Max open orders** limit and **leverage cap**
- **Peak-drawdown kill-switch** for emergency flat
- Order precision/min-notional validation, client order IDs, order reconciliation, partial-fill handling
- Full **SQLite event log** of every action + live WebSocket event stream

## Paper Trading & Backtesting

- **Paper trading simulator** — bots trade against **real live prices** with a virtual balance: maker/taker fees, slippage, post-only, partial fills and perp positions are fully simulated. Every trade appears as a chart marker with details.
- **Crypto backtesting engine** — replay **Grid** and **DCA** strategies on real historical candles and get ROI, APR, **max drawdown**, fee impact, and a full trade list with on-chart markers.
- Same strategy code runs in paper and live — flip the account to live, add API keys, and the identical bot trades real funds.

## Dashboard Overview

Watch the **crypto trading bot dashboard** in action — equity, PnL, live bots, candlestick chart with grid levels and trade markers, order book depth and the no-code bot wizard:

[▶ Watch the full demo — demo.mp4](demo.mp4)

<!-- Placeholder for an inline player once the video is hosted (GitHub strips raw <video> from READMEs):
<video src="demo.mp4" controls></video>
-->

## Web Interface

- **Dashboard** — total equity, PnL, active bots, live event feed + **Exchange Config** for fast API-key and paper-account onboarding.
- **Bots** — bot cards with status/PnL + a **5-step no-code wizard** (type → account/pair (spot or perp, live account can be added inline) → all strategy parameters → risk → JSON review). 6 types: Grid, DCA, Combo, Momentum, VWAP, Adaptive.
- **Bot Detail** — custom **canvas candlestick chart** with grid levels (buy/sell), DCA AVG/TP/SL lines, hoverable **trade markers**, live **order book** panel with depth bars and spread in bps, and tabs: Orders / Trades / DCA Cycles / Logs / Settings. Adaptive bots show the current market regime (ADX, ATR%, BB width).
- **Backtest** — configure → statistics + equity chart with trade markers + trades table.
- **Accounts** — paper/live accounts, connection test, editable balances.
- **Logs** — filterable stream of all events.

## Installation & Quick Start

Requirements: **Python 3.10+** (works on Windows, macOS, Linux).

```bash
cd backend
pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:8000** (or just run `start.bat` from the repo root on Windows).

On first launch the server creates a default **paper account**: go to *Exchange Accounts* and create an account (Paper + USDT balance), then create your first bot in *Bots → New Bot*. Optional native SDKs:

```bash
pip install hyperliquid-python-sdk   # Hyperliquid: API wallet private key + main wallet address
pip install dydx-v4-client           # dYdX v4: mnemonic
```

## Project Structure

```
backend/
  app/
    api/          REST + WebSocket
    backtest/     backtesting engine
    core/         models, storage, event bus
    exchanges/    paper engine, ccxt, hyperliquid, dydx
    managers/     market data, order book, orders, risk, balances
    runtime/      bot runtime + manager
    strategies/   grid, dca, combo, momentum, vwap, adaptive
frontend/
  assets/js/      SPA: chart, pages, api client
data/             SQLite database (created automatically)
```

**Stack:** Python 3.10+, FastAPI + WebSocket, ccxt (REST), ccxt.pro-ready structure, SQLite (zero external database dependencies), no-build vanilla ES-module SPA frontend.

## Strategy Settings Reference (Expert JSON)

**Grid** (`grid`): `total_budget_quote`, `upper_price`, `lower_price`, `levels`, `grid_type` (arithmetic/geometric), `grid_mode` (neutral/long/short), `spacing_mode` + `spacing_value` (manual step: % for geometric, quote for arithmetic), `per_level_mode` + `per_level_quote`/`per_level_qty`, `fee_buffer_pct`, `post_only`, `price_source` (mid/last), `max_active_orders`, `order_timeout_min` (auto-replace stuck orders), `min_notional_quote`, `rebalance_mode` (inventory/classic), `infinite`, `on_exit` (rebuild/hold/stop), `trailing_up`, `use_inventory`, `skew_enabled`, `skew_max_shift`, `max_inventory_base`, `reopen_delay_s`, `stop_loss_pct` (below lower bound), `stop_loss_price` (absolute), `max_drawdown_pct` (PnL from peak), `pause_volatility_atr_pct` (pause placements on ATR spikes), `take_profit_pct`, `tp_includes_fees`.

**DCA** (`dca`): `total_budget_quote`, `base_order_pct`/`base_order_quote`, `max_safety_orders`, `safety_order_quote` + `safety_size_mode` (fixed_quote / pct_of_base / pct_of_capital), `safety_deviation_pct`, `safety_step_scale`, `volume_scale`, `take_profit_pct`, `tp_from` (avg/base), `min_profit_pct`, `tp_includes_fees`, `trailing_tp_enabled` + `trailing_tp_deviation_pct`, `stop_loss_pct`, `short_mode`, `entry_condition` (immediate/ema/rsi) + filter params, `cooldown_minutes`, `max_active_cycles` + `new_cycle_deviation_pct` (multi-cycle), `safety_order_type`.

**Momentum** (`momentum`): `fast_ema`, `slow_ema`, `volume_filter_enabled`/`volume_ma_period`/`volume_factor`, `rsi_filter_enabled`/`rsi_period`/`rsi_long_max`/`rsi_short_min`, `allow_short`, `stop_loss_pct`, `take_profit_pct`, `trailing_enabled`/`trailing_dev_pct`, `exit_on_cross`, `cooldown_minutes`, `max_hold_hours`, `position_pct`.

**VWAP** (`vwap`): `mode` (mean_reversion/reclaim), `vwap_window` (0=anchored), `deviation_pct`, `confirm_ema_period`, `volume_factor`, `take_profit_pct`, `stop_loss_pct`, `allow_short`, `max_trades_per_day`, `cooldown_minutes`, `position_pct`.

**Adaptive** (`adaptive`): `enable_grid/dca/momentum`, `grid_budget_pct`/`dca_budget_pct`/`momentum_budget_pct`, `regime_timeframe`, `adx_period`/`adx_trend_threshold`, `atr_period`/`atr_high_pct`, `bb_period`, `in_trend_pause_grid`, `volatile_action` (stand_aside/continue).

**Global** (`risk`): `max_position_quote`, `daily_max_loss_quote`, `max_leverage`, `max_open_orders`, `kill_switch_drawdown_pct`. (`execution`): `leverage`, `margin_mode` (isolated/cross), `time_in_force` (gtc/ioc/fok), `slippage_tolerance_pct`, `log_level`.

## Paper Mode Explained

A paper account behaves exactly like a live one: bots receive a fake balance but trade against **real live exchange prices** (fees, slippage, partial fills), orders appear in the book/tables, trades appear as chart markers, and all logs and reports are identical. When you switch the account to live and enter API keys, the same bot trades real money — strategy code is unchanged; only the execution adapter changes.

## FAQ

**Is CEXTrader really a free crypto trading bot?**
Yes. It is free, self-hosted software with no subscription, no profit sharing and no cloud service. You run it locally and connect it to your own exchange accounts.

**How does it compare to paid grid bot platforms like Pionex, 3Commas, Cryptohopper or Bitsgap?**
CEXTrader is a self-hosted alternative: the same grid and DCA strategy families, but running on your own machine, connected directly to your exchange API keys, with zero monthly fees and no profit cut.

**Do I need to know how to code?**
No. The web dashboard includes a 5-step no-code wizard. Advanced users can fine-tune every parameter through the Expert JSON editor.

**Is my API key safe?**
Keys are stored locally in the SQLite database on your own machine and are sent only to your chosen exchange. Never enable withdrawal permissions on an API key, and use the bot only on a machine you control.

**Can I test strategies without real money?**
Yes. The paper trading engine simulates fills against live prices with fees, slippage and partial fills — plus a backtesting mode on historical candles.

**Does it work on Windows / macOS / Linux?**
Yes — any OS with Python 3.10+. On Windows you can start it with `start.bat`. For 24/7 operation, deploy it on a cheap VPS or home server.

**Which coins can I trade?**
Any market the connected exchange offers — BTC, ETH, SOL, XRP, DOGE and hundreds of altcoin pairs, spot or USDT-M perpetual futures.

**Can I run multiple bots at once?**
Yes — unlimited bots in parallel, each with its own pair, strategy, account and risk settings, with auto-restart after server reboots.

## Security & Deployment Notes

- **Paper mode is safe by default**: bots trade a virtual balance against real prices from the configured market data source. Live mode requires the exchange's API keys.
- Hyperliquid: `pip install hyperliquid-python-sdk` — API Wallet private key + main wallet address. dYdX v4: `pip install dydx-v4-client` — mnemonic. Without the SDKs those adapters are unavailable (Hyperliquid then works via ccxt).
- Exchange fees are picked up automatically (ccxt markets); for paper accounts they are configured manually.
- All data is stored locally in `data/trader.db`. API keys are not encrypted — use this bot only on your own machine.

## Disclaimer

This software is provided for educational and informational purposes and is **not financial advice**. Automated crypto trading involves substantial risk of loss — backtests and paper results do not guarantee future performance. Trading cryptocurrency and perpetual futures may be regulated in your jurisdiction; you are solely responsible for complying with local laws and exchange terms of service. Use at your own risk.

---

**Keywords:** crypto trading bot, bitcoin trading bot, ethereum trading bot, altcoin trading bot, grid trading bot, spot grid bot, futures grid bot, DCA bot, dollar cost averaging bot, combo bot, momentum bot, VWAP bot, adaptive trading bot, automated crypto trading, algorithmic trading, crypto bot open source, free trading bot, self-hosted trading bot, python trading bot, FastAPI trading bot, no-code crypto bot, trading bot for beginners, crypto backtesting, backtest trading strategy, paper trading simulator, spot trading bot, perpetual futures bot, USDT-M futures bot, leverage trading bot, margin trading bot, swing trading bot, scalping bot, mean reversion bot, martingale DCA, EMA crossover strategy, RSI filter, ADX regime detection, Bollinger Bands strategy, trailing take profit, stop loss bot, kill switch drawdown, risk management crypto, Binance trading bot, Coinbase trading bot, Kraken trading bot, Bybit trading bot, OKX trading bot, Bitget bot, Gate.io bot, KuCoin bot, MEXC bot, HTX bot, Hyperliquid bot, dYdX trading bot, decentralized perps, no KYC trading bot, CEX trading bot, crypto trading terminal, multi-exchange bot, live order book, candlestick chart, BTC bot, ETH bot, SOL bot, XRP bot, DOGE bot, memecoin bot, altcoin bot, pionex alternative, 3commas alternative, cryptohopper alternative, bitsgap alternative, crypto bot Windows, VPS trading bot, 24/7 crypto bot
