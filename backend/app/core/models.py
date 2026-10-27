from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field


class BotStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class GridConfig(BaseModel):
    total_budget_quote: float = Field(1000, gt=0)
    upper_price: float = Field(0, ge=0)
    lower_price: float = Field(0, ge=0)
    levels: int = Field(20, ge=2, le=500)
    grid_type: Literal["arithmetic", "geometric"] = "geometric"
    grid_mode: Literal["neutral", "long", "short"] = "neutral"
    spacing_mode: Literal["auto", "manual"] = "auto"
    spacing_value: float = Field(0.0, ge=0)
    per_level_mode: Literal["budget_split", "fixed_quote", "fixed_qty"] = "budget_split"
    per_level_quote: float = 0.0
    per_level_qty: float = 0.0
    fee_buffer_pct: float = Field(0.05, ge=0)
    post_only: bool = True
    infinite: bool = True
    use_inventory: bool = True
    on_exit: Literal["rebuild", "stop", "hold"] = "rebuild"
    trailing_up: bool = False
    skew_enabled: bool = True
    skew_max_shift: int = Field(2, ge=0, le=10)
    rebalance_mode: Literal["inventory", "classic"] = "inventory"
    max_active_orders: int = Field(0, ge=0)
    max_inventory_base: float = Field(0.0, ge=0)
    min_notional_quote: float = Field(0.0, ge=0)
    price_source: Literal["mid", "last"] = "mid"
    order_timeout_min: int = Field(0, ge=0)
    reopen_delay_s: int = Field(0, ge=0)
    stop_loss_pct: float = Field(0.0, ge=0)
    stop_loss_price: float = Field(0.0, ge=0)
    stop_loss_close_inventory: bool = True
    take_profit_pct: float = Field(0.0, ge=0)
    tp_includes_fees: bool = True
    max_drawdown_pct: float = Field(0.0, ge=0)
    pause_volatility_atr_pct: float = Field(0.0, ge=0)


class DcaConfig(BaseModel):
    total_budget_quote: float = Field(1000, gt=0)
    base_order_pct: float = Field(20.0, gt=0, le=100)
    base_order_quote: float = 0.0
    max_safety_orders: int = Field(5, ge=0, le=50)
    safety_order_quote: float = Field(0.0, ge=0)
    safety_size_mode: Literal["fixed_quote", "pct_of_base", "pct_of_capital"] = "fixed_quote"
    safety_deviation_pct: float = Field(2.0, gt=0)
    safety_step_scale: float = Field(1.3, ge=1.0)
    volume_scale: float = Field(1.5, ge=1.0)
    take_profit_pct: float = Field(1.5, gt=0)
    tp_from: Literal["avg", "base"] = "avg"
    min_profit_pct: float = Field(0.0, ge=0)
    tp_includes_fees: bool = True
    trailing_tp_enabled: bool = False
    trailing_tp_deviation_pct: float = Field(0.4, gt=0)
    stop_loss_pct: float = Field(0.0, ge=0)
    short_mode: bool = False
    entry_condition: Literal["immediate", "rsi", "ema"] = "immediate"
    cooldown_minutes: int = Field(0, ge=0)
    trend_filter_timeframe: str = "1h"
    trend_filter_type: Literal["ema", "rsi"] = "ema"
    ema_period: int = Field(50, ge=2)
    ema_side: Literal["above", "below"] = "above"
    rsi_period: int = Field(14, ge=2)
    rsi_max: float = Field(70.0)
    max_active_cycles: int = Field(1, ge=1, le=10)
    new_cycle_deviation_pct: float = Field(3.0, gt=0)
    safety_order_type: Literal["market", "limit"] = "market"


class ComboConfig(BaseModel):
    grid_share_pct: float = Field(60.0, gt=0, lt=100)


class MomentumConfig(BaseModel):
    total_budget_quote: float = Field(1000, gt=0)
    position_pct: float = Field(50.0, gt=0, le=100)
    fast_ema: int = Field(12, ge=2)
    slow_ema: int = Field(30, ge=3)
    volume_filter_enabled: bool = True
    volume_ma_period: int = Field(20, ge=2)
    volume_factor: float = Field(1.3, ge=0.1)
    rsi_filter_enabled: bool = False
    rsi_period: int = Field(14, ge=2)
    rsi_long_max: float = Field(70.0)
    rsi_short_min: float = Field(30.0)
    allow_short: bool = False
    stop_loss_pct: float = Field(3.0, gt=0)
    take_profit_pct: float = Field(6.0, gt=0)
    trailing_enabled: bool = False
    trailing_dev_pct: float = Field(1.5, gt=0)
    exit_on_cross: bool = True
    cooldown_minutes: int = Field(15, ge=0)
    max_hold_hours: float = Field(0.0, ge=0)


class VwapConfig(BaseModel):
    total_budget_quote: float = Field(1000, gt=0)
    position_pct: float = Field(40.0, gt=0, le=100)
    mode: Literal["mean_reversion", "reclaim"] = "mean_reversion"
    vwap_window: int = Field(48, ge=0)
    deviation_pct: float = Field(1.0, gt=0)
    confirm_ema_period: int = Field(0, ge=0)
    volume_factor: float = Field(0.0, ge=0)
    volume_ma_period: int = Field(20, ge=2)
    take_profit_pct: float = Field(2.0, gt=0)
    stop_loss_pct: float = Field(2.0, gt=0)
    allow_short: bool = False
    cooldown_minutes: int = Field(15, ge=0)
    max_trades_per_day: int = Field(5, ge=1)


class AdaptiveConfig(BaseModel):
    total_budget_quote: float = Field(1000, gt=0)
    enable_grid: bool = True
    enable_dca: bool = True
    enable_momentum: bool = True
    grid_budget_pct: float = Field(50.0, ge=0, le=100)
    dca_budget_pct: float = Field(25.0, ge=0, le=100)
    momentum_budget_pct: float = Field(25.0, ge=0, le=100)
    regime_timeframe: str = "1h"
    adx_period: int = Field(14, ge=2)
    adx_trend_threshold: float = Field(23.0, gt=0)
    atr_period: int = Field(14, ge=2)
    atr_high_pct: float = Field(4.0, gt=0)
    bb_period: int = Field(20, ge=2)
    in_trend_pause_grid: bool = False
    volatile_action: Literal["stand_aside", "continue"] = "stand_aside"


class RiskConfig(BaseModel):
    max_position_quote: float = Field(0.0, ge=0)
    daily_max_loss_quote: float = Field(0.0, ge=0)
    max_leverage: float = Field(1.0, gt=0)
    max_open_orders: int = Field(100, ge=1)
    kill_switch_drawdown_pct: float = Field(0.0, ge=0)


class ExecutionConfig(BaseModel):
    leverage: float = Field(1.0, ge=1, le=100)
    margin_mode: Literal["isolated", "cross"] = "isolated"
    time_in_force: Literal["gtc", "ioc", "fok"] = "gtc"
    slippage_tolerance_pct: float = Field(0.0, ge=0)
    log_level: Literal["info", "debug"] = "info"


class AccountConfig(BaseModel):
    id: str = ""
    name: str = "Account"
    exchange: str = "paper"
    underlying: str = "bybit"
    paper: bool = True
    api_key: str = ""
    secret: str = ""
    password: str = ""
    wallet_address: str = ""
    mnemonic: str = ""
    subaccount: str = ""
    testnet: bool = True
    initial_balances: dict = Field(default_factory=lambda: {"USDT": 10000.0})
    maker_fee_pct: float = 0.1
    taker_fee_pct: float = 0.1
    slippage_bps: float = 2.0
    leverage: float = 1.0


class BotConfig(BaseModel):
    id: str = ""
    name: str = "New Bot"
    type: Literal["grid", "dca", "combo", "momentum", "vwap", "adaptive"] = "grid"
    account_id: str = ""
    symbol: str = "BTC/USDT"
    market_type: Literal["spot", "perp"] = "spot"
    timeframe: str = "5m"
    tick_interval_s: float = Field(1.0, ge=0.2)
    grid: Optional[GridConfig] = None
    dca: Optional[DcaConfig] = None
    combo: Optional[ComboConfig] = None
    momentum: Optional[MomentumConfig] = None
    vwap: Optional[VwapConfig] = None
    adaptive: Optional[AdaptiveConfig] = None
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    modules: dict = Field(default_factory=dict)
    enabled: bool = True


class BacktestRequest(BaseModel):
    type: Literal["grid", "dca", "momentum", "vwap"] = "grid"
    symbol: str = "BTC/USDT"
    exchange: str = "binance"
    timeframe: str = "1h"
    start_ms: int = 0
    end_ms: int = 0
    candle_limit: int = Field(1500, ge=100, le=5000)
    grid: Optional[GridConfig] = None
    dca: Optional[DcaConfig] = None
    momentum: Optional[MomentumConfig] = None
    vwap: Optional[VwapConfig] = None
    maker_fee_pct: float = 0.1
    taker_fee_pct: float = 0.1
    initial_balance_quote: float = 1000.0
