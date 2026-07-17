"""
Volume Profile Engine
=====================
Maintains a rolling 24-hour histogram of traded volume bucketed by price level.

Computes three key derived values on demand:
  - POC  (Point of Control)  — price bucket with the highest volume
  - VAH  (Value Area High)   — upper bound of the range containing 70% of volume
  - VAL  (Value Area Low)    — lower bound of the range containing 70% of volume

Design
------
Same rolling-window pattern as VWAPEngine:
  - A deque of (timestamp, price_bucket, size) entries ordered by time
  - A dict mapping price_bucket -> total_volume for O(1) lookup/update
  - On new trade: add to bucket and deque
  - On eviction: subtract from bucket, remove bucket if it hits zero

Bucket size (bin_size) is configurable. Default $1 for BTC/USDT.
At BTC ~$64k with a $2k daily range, this produces ~2000 active buckets —
trivial memory, and gives meaningful granularity for microstructure analysis.

Value Area computation (O(n log n) where n = number of active buckets):
  1. Sort buckets by volume descending
  2. Walk from highest-volume bucket outward, accumulating volume
  3. Stop when accumulated >= 70% of total
  4. The min/max price among included buckets = VAL/VAH
"""

from __future__ import annotations
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Optional

WINDOW_SECONDS = 24 * 60 * 60   # rolling 24h
VALUE_AREA_PCT  = 0.70           # 70% of total volume defines the value area


@dataclass
class VolumeProfileResult:
    poc:          float           # Point of Control price
    poc_volume:   float           # Volume at POC
    vah:          float           # Value Area High
    val:          float           # Value Area Low
    total_volume: float           # Total volume in window
    bucket_count: int             # Number of active price buckets
    top_buckets:  list[tuple[float, float]]  # [(price, volume), ...] top 10 by volume


class VolumeProfileEngine:
    """
    Feed trades via add_trade(). Call current() for the latest profile snapshot.
    """

    def __init__(self, bin_size: float = 1.0, window_seconds: float = WINDOW_SECONDS):
        self.bin_size       = bin_size
        self.window_seconds = window_seconds

        # Deque entries: (timestamp, bucket_price, size)
        self._trades: deque[tuple[float, float, float]] = deque()

        # Core data structure: price bucket -> accumulated volume
        self._profile: defaultdict[float, float] = defaultdict(float)
        self._total_volume: float = 0.0

    # ------------------------------------------------------------------
    # Bucket helper
    # ------------------------------------------------------------------

    def _bucket(self, price: float) -> float:
        """Round price down to the nearest bin_size boundary."""
        return round(round(price / self.bin_size) * self.bin_size, 10)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_trade(self, price: float, size: float, timestamp: float) -> None:
        bucket = self._bucket(price)
        self._trades.append((timestamp, bucket, size))
        self._profile[bucket] += size
        self._total_volume    += size
        self._evict_old(now=timestamp)

    def _evict_old(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._trades and self._trades[0][0] < cutoff:
            _, bucket, size = self._trades.popleft()
            self._profile[bucket] -= size
            self._total_volume    -= size
            if self._profile[bucket] <= 1e-12:
                del self._profile[bucket]

        # Guard float drift
        if self._total_volume < 1e-12:
            self._total_volume = 0.0

    def current(self, now: Optional[float] = None) -> Optional[VolumeProfileResult]:
        """
        Compute and return the current profile snapshot.
        Pass `now` to also evict stale entries based on wall-clock time.
        """
        if now is not None:
            self._evict_old(now)

        if not self._profile or self._total_volume <= 0:
            return None

        # Sort buckets by volume descending - O(n log n)
        sorted_buckets = sorted(self._profile.items(), key=lambda x: x[1], reverse=True)

        # POC = highest volume bucket
        poc_price, poc_volume = sorted_buckets[0]

        # Value Area: accumulate from highest-volume bucket downward
        # until we've covered VALUE_AREA_PCT of total volume.
        # Track which price levels are included to find VAH and VAL.
        target = self._total_volume * VALUE_AREA_PCT
        accumulated = 0.0
        included_prices: list[float] = []

        for price, vol in sorted_buckets:
            accumulated += vol
            included_prices.append(price)
            if accumulated >= target:
                break

        vah = max(included_prices) + self.bin_size  # top of the highest included bucket
        val = min(included_prices)                   # bottom of lowest included bucket

        return VolumeProfileResult(
            poc=poc_price,
            poc_volume=poc_volume,
            vah=vah,
            val=val,
            total_volume=self._total_volume,
            bucket_count=len(self._profile),
            top_buckets=sorted_buckets[:10],
        )

    def __len__(self) -> int:
        return len(self._trades)
