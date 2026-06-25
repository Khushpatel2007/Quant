"""
VWAP Engine
===========
Maintains a rolling 24-hour buffer of trades and computes a continuously
updated Volume-Weighted Average Price (VWAP) with standard-deviation bands.

Design notes
------------
Naively recomputing VWAP by summing the entire trade history on every new
trade would get slower as the day goes on (BTC/USDT can see hundreds of
thousands of trades per day). Instead we maintain three running sums and
update them incrementally:

    sum_pv   = sum(price * size)        for every trade in the window
    sum_v    = sum(size)
    sum_pv2  = sum(size * price**2)     needed for variance

VWAP = sum_pv / sum_v

Weighted variance uses the identity:
    Var(X) = E[X^2] - (E[X])^2
where E[X] is volume-weighted mean (= VWAP) and E[X^2] = sum_pv2 / sum_v.

When a trade ages out of the 24h window, we SUBTRACT its contribution from
all three running sums - this is symmetric with adding a new trade, which
is what makes the "rolling" window cheap (O(1) per trade in/out) instead of
O(n) (re-summing everything each time).
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Optional
import math
import time

WINDOW_SECONDS = 24 * 60 * 60  # rolling 24h


@dataclass
class Trade:
    price: float
    size: float
    timestamp: float  # unix seconds
    side: str          # "buy" or "sell" (aggressor side)


@dataclass
class VWAPResult:
    vwap: float
    std_dev: float
    upper_band_1: float   # vwap + 1 std dev
    lower_band_1: float   # vwap - 1 std dev
    upper_band_2: float   # vwap + 2 std dev
    lower_band_2: float   # vwap - 2 std dev
    sample_count: int
    total_volume: float
    window_seconds: float  # actual age span of data currently in the window


class VWAPEngine:
    """
    Feed it trades via add_trade(). Call current() at any time to get the
    VWAP + bands computed from whatever's currently in the rolling window.
    """

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self.window_seconds = window_seconds
        self._trades: deque[Trade] = deque()

        # Running sums - the whole point of this design
        self._sum_pv: float = 0.0    # sum(price * size)
        self._sum_v: float = 0.0     # sum(size)
        self._sum_pv2: float = 0.0   # sum(size * price^2)

    # ------------------------------------------------------------------

    def add_trade(self, trade: Trade) -> None:
        """Add a new trade and evict anything older than the window."""
        self._trades.append(trade)
        self._add_contribution(trade)
        self._evict_old(now=trade.timestamp)

    def _add_contribution(self, trade: Trade) -> None:
        pv = trade.price * trade.size
        self._sum_pv += pv
        self._sum_v += trade.size
        self._sum_pv2 += trade.size * trade.price * trade.price

    def _remove_contribution(self, trade: Trade) -> None:
        pv = trade.price * trade.size
        self._sum_pv -= pv
        self._sum_v -= trade.size
        self._sum_pv2 -= trade.size * trade.price * trade.price

    def _evict_old(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._trades and self._trades[0].timestamp < cutoff:
            old = self._trades.popleft()
            self._remove_contribution(old)
            # Guard against floating point drift pushing sums slightly
            # negative when volume should be exactly zero.
            if self._sum_v < 1e-12:
                self._sum_v = 0.0
                self._sum_pv = 0.0
                self._sum_pv2 = 0.0

    # ------------------------------------------------------------------

    def current(self, now: Optional[float] = None) -> Optional[VWAPResult]:
        """
        Returns the current VWAP + bands, or None if there's no data yet.
        Pass `now` to also evict stale trades based on wall-clock time even
        if no new trade has arrived recently (e.g. trading went quiet).
        """
        if now is not None:
            self._evict_old(now)

        if self._sum_v <= 0 or not self._trades:
            return None

        vwap = self._sum_pv / self._sum_v
        mean_sq = self._sum_pv2 / self._sum_v
        variance = mean_sq - vwap * vwap
        # Floating point cancellation: mean_sq and vwap**2 can be large,
        # nearly-equal numbers (especially with few/identical-price trades
        # or after many incremental add/evict cycles), so their difference
        # can come out as a tiny negative or spuriously nonzero value
        # instead of a clean zero. Clamp below this epsilon to zero.
        if variance < 1e-8:
            variance = 0.0
        std_dev = math.sqrt(variance)

        oldest_ts = self._trades[0].timestamp
        newest_ts = self._trades[-1].timestamp

        return VWAPResult(
            vwap=vwap,
            std_dev=std_dev,
            upper_band_1=vwap + std_dev,
            lower_band_1=vwap - std_dev,
            upper_band_2=vwap + 2 * std_dev,
            lower_band_2=vwap - 2 * std_dev,
            sample_count=len(self._trades),
            total_volume=self._sum_v,
            window_seconds=newest_ts - oldest_ts,
        )

    def __len__(self) -> int:
        return len(self._trades)
