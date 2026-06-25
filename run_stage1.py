"""
Stage 1 smoke test.

Run this to verify the ingestion layer + order book engine work end to end:
    python3 run_stage1.py

You should see:
  - A "Connected" log line
  - A "Snapshot applied" log line
  - A "Resync complete" log line
  - Then a live-updating line printing best bid/ask/spread as the book updates
  - Occasional trade prints showing aggressor side

Let it run for ~30 seconds, then Ctrl+C. If you see desync warnings followed
by automatic "Resync complete" messages, that's the gap-recovery logic working
correctly, not a bug.
"""

import asyncio
import logging
import sys

from ingestion import BinanceFeed
from order_book import OrderBook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

_last_print = 0.0
PRINT_INTERVAL = 1.0  # don't flood the terminal - print book state at most once/sec


def on_book_update(book: OrderBook) -> None:
    global _last_print
    import time
    now = time.time()
    if now - _last_print < PRINT_INTERVAL:
        return
    _last_print = now

    bid, ask = book.best_bid(), book.best_ask()
    if bid and ask:
        print(
            f"  BOOK  bid={bid.price:>10.2f} ({bid.size:.4f})   "
            f"ask={ask.price:>10.2f} ({ask.size:.4f})   "
            f"spread={book.spread():.2f}   "
            f"mid={book.mid_price():.2f}"
        )


def on_trade(trade: dict) -> None:
    arrow = "BUY " if trade["side"] == "buy" else "SELL"
    print(f"  TRADE {arrow} {trade['size']:.5f} @ {trade['price']:.2f}")


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
