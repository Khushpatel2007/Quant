"""
Order Book Engine
==================
Maintains a live, locally-reconstructed Level 2 order book for a single symbol.

This class is deliberately "dumb" about networking - it only knows how to:
  1. Absorb a REST snapshot (full book state at a point in time)
  2. Apply incremental diff updates (from the WebSocket depth stream)
  3. Detect when updates have been missed (sequence gap) and report desync

Binance's depth update contract (https://binance-docs.github.io/apidocs):
  - Each diff has `U` (first update ID in event) and `u` (final update ID in event)
  - A diff is valid to apply if: U <= last_applied_id + 1 <= u
  - After applying, last_applied_id becomes `u`
  - A size of 0 at a price level means "remove this level"
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class BookLevel:
    price: float
    size: float


class OrderBookDesyncError(Exception):
    """Raised when a sequence gap is detected - caller must resync from a fresh snapshot."""
    pass


class OrderBook:
    """
    Maintains live bid/ask price levels for one symbol.

    bids: price -> size, conceptually sorted descending (best bid = max price)
    asks: price -> size, conceptually sorted ascending  (best ask = min price)

    We store them as plain dicts and sort only when needed (e.g. best_bid(),
    top_n_levels()) rather than maintaining a sorted structure on every update.
    For BTC/USDT depth update rates (tens of updates/sec), dict + on-demand
    sort is simple and fast enough. If you outgrow this, swap in a sorted
    structure (e.g. `sortedcontainers.SortedDict`) without touching callers.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: Optional[int] = None
        self.synced: bool = False
        self.last_update_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Snapshot handling
    # ------------------------------------------------------------------

    def apply_snapshot(self, last_update_id: int, bids: list[list[str]], asks: list[list[str]]) -> None:
        """
        Wipe and rebuild the book from a REST snapshot.

        bids/asks come in Binance's raw format: [["price", "qty"], ...]
        """
        self.bids = {float(p): float(q) for p, q in bids if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in asks if float(q) > 0}
        self.last_update_id = last_update_id
        self.synced = True
        self.last_update_time = time.time()

    # ------------------------------------------------------------------
    # Diff handling
    # ------------------------------------------------------------------

    def apply_diff(self, first_update_id: int, final_update_id: int,
                    bid_updates: list[list[str]], ask_updates: list[list[str]]) -> None:
        """
        Apply one diff event from the WebSocket depth stream.

        Raises OrderBookDesyncError if this diff doesn't connect contiguously
        to the last applied update - caller must then fetch a fresh snapshot
        and resync (see resync logic in the stream handler).
        """
        if not self.synced or self.last_update_id is None:
            raise OrderBookDesyncError("Book not synced - snapshot required before applying diffs")

        # Contiguity check: this diff's range must cover (last_update_id + 1)
        if final_update_id <= self.last_update_id:
            # Stale diff, already covered by snapshot/previous updates - ignore safely
            return
        if first_update_id > self.last_update_id + 1:
            # Gap detected - we missed at least one update in between
            self.synced = False
            raise OrderBookDesyncError(
                f"Sequence gap: expected first_update_id <= {self.last_update_id + 1}, "
                f"got {first_update_id}"
            )

        for price_str, qty_str in bid_updates:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        for price_str, qty_str in ask_updates:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_update_id = final_update_id
        self.last_update_time = time.time()

    # ------------------------------------------------------------------
    # Read accessors used by the metrics layer
    # ------------------------------------------------------------------

    def best_bid(self) -> Optional[BookLevel]:
        if not self.bids:
            return None
        price = max(self.bids)
        return BookLevel(price, self.bids[price])

    def best_ask(self) -> Optional[BookLevel]:
        if not self.asks:
            return None
        price = min(self.asks)
        return BookLevel(price, self.asks[price])

    def spread(self) -> Optional[float]:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return ask.price - bid.price

    def mid_price(self) -> Optional[float]:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid.price + ask.price) / 2.0

    def top_n_bids(self, n: int) -> list[BookLevel]:
        prices = sorted(self.bids.keys(), reverse=True)[:n]
        return [BookLevel(p, self.bids[p]) for p in prices]

    def top_n_asks(self, n: int) -> list[BookLevel]:
        prices = sorted(self.asks.keys())[:n]
        return [BookLevel(p, self.asks[p]) for p in prices]

    def all_levels(self) -> tuple[list[BookLevel], list[BookLevel]]:
        """Return (bids, asks) fully sorted - used by the liquidity zone detector."""
        bid_levels = [BookLevel(p, q) for p, q in sorted(self.bids.items(), reverse=True)]
        ask_levels = [BookLevel(p, q) for p, q in sorted(self.asks.items())]
        return bid_levels, ask_levels

    def __repr__(self) -> str:
        bb, ba = self.best_bid(), self.best_ask()
        bb_s = f"{bb.price:.2f}@{bb.size:.4f}" if bb else "None"
        ba_s = f"{ba.price:.2f}@{ba.size:.4f}" if ba else "None"
        return f"<OrderBook {self.symbol} bid={bb_s} ask={ba_s} synced={self.synced}>"
