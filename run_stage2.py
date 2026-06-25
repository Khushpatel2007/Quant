"""
Stage 2 smoke test: Order Book (Stage 1) + VWAP Engine (Stage 2), live.

Run:
    python3 run_stage2.py

You should see the same book/trade output as Stage 1, plus a periodic VWAP
line showing the rolling 24h volume-weighted average price and its bands.

Note: a TRUE 24h window will take 24 hours to fully populate. In the first
few minutes of running, the VWAP is accurate for the data it HAS seen so
far (a short rolling window that grows toward 24h) - it's not wrong, it's
just based on less history than the steady-state version will be.
"""

import asyncio
import logging
import sys
import time

from ingestion import BinanceFeed
from order_book import OrderBook
from vwap_engine import VWAPEngine, Trade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

vwap_engine = VWAPEngine()  # default: rolling 24h window

_last_book_print = 0.0
_last_vwap_print = 0.0
BOOK_PRINT_INTERVAL = 1.0
VWAP_PRINT_INTERVAL = 5.0  # VWAP changes slowly - no need to print every second


def on_book_update(book: OrderBook) -> None:
    global _last_book_print
    now = time.time()
    if now - _last_book_print < BOOK_PRINT_INTERVAL:
        return
    _last_book_print = now

    bid, ask = book.best_bid(), book.best_ask()
    if bid and ask:
        print(
            f"  BOOK  bid={bid.price:>10.2f} ({bid.size:.4f})   "
            f"ask={ask.price:>10.2f} ({ask.size:.4f})   "
            f"spread={book.spread():.2f}   "
            f"mid={book.mid_price():.2f}"
        )


def on_trade(trade_dict: dict) -> None:
    # Feed into the VWAP engine
    trade = Trade(
        price=trade_dict["price"],
        size=trade_dict["size"],
        timestamp=trade_dict["timestamp"],
        side=trade_dict["side"],
    )
    vwap_engine.add_trade(trade)

    arrow = "BUY " if trade.side == "buy" else "SELL"
    print(f"  TRADE {arrow} {trade.size:.5f} @ {trade.price:.2f}")

    maybe_print_vwap()


def maybe_print_vwap() -> None:
    global _last_vwap_print
    now = time.time()
    if now - _last_vwap_print < VWAP_PRINT_INTERVAL:
        return
    _last_vwap_print = now

    result = vwap_engine.current(now=now)
    if result is None:
        return

    window_hrs = result.window_seconds / 3600.0
    print(
        f"\n  >>> VWAP={result.vwap:.2f}  std={result.std_dev:.2f}  "
        f"bands=[{result.lower_band_2:.2f} .. {result.lower_band_1:.2f} | "
        f"{result.upper_band_1:.2f} .. {result.upper_band_2:.2f}]  "
        f"vol={result.total_volume:.2f}  n={result.sample_count}  "
        f"window={window_hrs:.3f}h\n"
    )


async def main():
    feed = BinanceFeed(
        symbol="BTCUSDT",
        on_book_update=on_book_update,
        on_trade=on_trade,
    )
    await feed.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
