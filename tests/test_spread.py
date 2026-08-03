"""Тесты расчёта спреда с учётом комиссий."""

from __future__ import annotations

from arbitrage_robot.config import ExchangeConfig, FeesConfig, StrategyConfig
from arbitrage_robot.core.spread import SpreadFinder
from arbitrage_robot.exchanges import ExchangeClient
from arbitrage_robot.models import Quote, now_ms


def _client(exchange_id: str, taker: float) -> ExchangeClient:
    config = ExchangeConfig(id=exchange_id, maker_fee=taker / 2, taker_fee=taker)
    return ExchangeClient(config, FeesConfig())


def _finder(min_spread: float = 0.2, taker: float = 0.0004) -> SpreadFinder:
    clients = {"a": _client("a", taker), "b": _client("b", taker)}
    strategy = StrategyConfig(min_spread_pct=min_spread, slippage_pct=0.0)
    return SpreadFinder(clients, strategy, FeesConfig())


def _quote(exchange: str, bid: float, ask: float) -> Quote:
    return Quote(
        exchange=exchange,
        symbol="BTC/USDT:USDT",
        bid=bid,
        ask=ask,
        timestamp_ms=now_ms(),
        bid_volume=100.0,
        ask_volume=100.0,
    )


def test_round_trip_fees_includes_both_legs_twice() -> None:
    finder = _finder(taker=0.0004)
    # (0.04 % + 0.04 %) * 2 = 0.16 %
    assert finder.round_trip_fees_pct("BTC/USDT:USDT", "a", "b") == 0.16


def test_direction_is_chosen_by_best_gross_spread() -> None:
    finder = _finder()
    snapshot = finder.evaluate_pair(
        "BTC/USDT:USDT", _quote("a", 100.0, 100.1), _quote("b", 101.0, 101.1)
    )
    assert snapshot is not None
    assert snapshot.buy_exchange == "a"
    assert snapshot.sell_exchange == "b"
    assert snapshot.gross_spread_pct > 0.0
    assert snapshot.net_spread_pct == snapshot.gross_spread_pct - snapshot.fees_pct


def test_no_opportunity_when_spread_below_fees() -> None:
    finder = _finder(min_spread=0.2, taker=0.001)  # комиссии 0.4 %
    quotes = {"a": _quote("a", 100.0, 100.05), "b": _quote("b", 100.2, 100.25)}
    assert finder.find("BTC/USDT:USDT", quotes) == []


def test_opportunity_found_and_amount_calculated() -> None:
    finder = _finder(min_spread=0.2, taker=0.0002)
    quotes = {"a": _quote("a", 99.9, 100.0), "b": _quote("b", 101.0, 101.1)}
    opportunities = finder.find("BTC/USDT:USDT", quotes)
    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.buy_exchange == "a"
    assert opportunity.sell_exchange == "b"
    assert opportunity.amount == opportunity.notional / opportunity.buy_price
    assert opportunity.expected_profit > 0.0


def test_anomaly_spread_is_filtered() -> None:
    finder = _finder(min_spread=0.2)
    quotes = {"a": _quote("a", 100.0, 100.0), "b": _quote("b", 200.0, 200.0)}
    assert finder.find("BTC/USDT:USDT", quotes) == []
