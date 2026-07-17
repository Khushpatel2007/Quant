"""
Stage 3: Order Book + VWAP + Volume Profile, live.

Run:
    python run_stage3.py

New output (every 10 seconds):
    >>> PROFILE  POC=64281.00 (12.41 BTC)
                 VAL=64270.00  VAH=64292.00
                 Top buckets: 64281=12.41  64280=9.32  64282=7.11 ...
"""

import asyncio
import logging
import sys
import time

from ingestion import BinanceFeed
from order_book import OrderBook
from vwap_engine import VWAPEngine, Trade
from volume_profile import VolumeProfileEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

vwap_engine    = VWAPEngine()
profile_engine = VolumeProfileEngine(bin_size=1.0)  # $1 buckets for BTC/USDT

_last_book_print    = 0.0
_last_vwap_print    = 0.0
_last_profile_print = 0.0

BOOK_PRINT_INTERVAL    = 1.0
VWAP_PRINT_INTERVAL    = 5.0
PROFILE_PRINT_INTERVAL = 10.0  # Profile changes slowly - print every 10s


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
            f"spread={book.spread():.2f}   mid={book.mid_price():.2f}"
        )


def on_trade(trade_dict: dict) -> None:
    trade = Trade(
        price=trade_dict["price"],
        size=trade_dict["size"],
        timestamp=trade_dict["timestamp"],
        side=trade_dict["side"],
    )
    vwap_engine.add_trade(trade)
    profile_engine.add_trade(
        price=trade_dict["price"],
        size=trade_dict["size"],
        timestamp=trade_dict["timestamp"],
    )

    arrow = "BUY " if trade.side == "buy" else "SELL"
    print(f"  TRADE {arrow} {trade.size:.5f} @ {trade.price:.2f}")

    maybe_print_vwap()
    maybe_print_profile()


def maybe_print_vwap() -> None:
    global _last_vwap_print
    now = time.time()
    if now - _last_vwap_print < VWAP_PRINT_INTERVAL:
        return
    _last_vwap_print = now
    r = vwap_engine.current(now=now)
    if r is None:
        return
    print(
        f"\n  >>> VWAP={r.vwap:.2f}  std={r.std_dev:.2f}  "
        f"bands=[{r.lower_band_2:.2f} .. {r.lower_band_1:.2f} | "
        f"{r.upper_band_1:.2f} .. {r.upper_band_2:.2f}]  "
        f"vol={r.total_volume:.2f}  n={r.sample_count}  window={r.window_seconds/3600:.3f}h\n"
    )


def maybe_print_profile() -> None:
    global _last_profile_print
    now = time.time()
    if now - _last_profile_print < PROFILE_PRINT_INTERVAL:
        return
    _last_profile_print = now
    r = profile_engine.current(now=now)
    if r is None:
        return

    top = "  ".join(f"{p:.0f}={v:.2f}" for p, v in r.top_buckets[:5])
    print(
        f"\n  >>> PROFILE  POC={r.poc:.2f} ({r.poc_volume:.2f} BTC)\n"
        f"               VAL={r.val:.2f}  VAH={r.vah:.2f}  "
        f"total_vol={r.total_volume:.2f}  buckets={r.bucket_count}\n"
        f"               Top5: {top}\n"
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
