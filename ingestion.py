"""
Ingestion Layer
================
Connects to Binance's combined WebSocket stream (depth diffs + aggTrades),
performs the snapshot-then-buffer-then-reconcile dance required to safely
initialize a local order book, and detects/recovers from desyncs.

Reference: https://binance-docs.github.io/apidocs/spot/en/#how-to-manage-a-local-order-book-correctly

The correct sequence (matches Binance's documented procedure):
  1. Open the WebSocket stream and buffer every depth event (don't apply yet).
  2. Fetch a REST snapshot of the order book (has its own lastUpdateId).
  3. Discard any buffered events where event.u <= snapshot.lastUpdateId.
  4. The first event to apply must satisfy: event.U <= lastUpdateId+1 <= event.u
  5. Apply that event and all subsequent buffered events in order.
  6. Continue applying new events live as they arrive.
  7. If a gap is ever detected, drop the book and restart from step 1.
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Optional

import aiohttp
import websockets

from order_book import OrderBook, OrderBookDesyncError

logger = logging.getLogger("ingestion")

BINANCE_REST_BASE = "https://api.binance.com"
BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"

# How many buffered diff events to keep before giving up on resync (safety valve)
MAX_BUFFER_SIZE = 2000


class BinanceFeed:
    """
    Manages the full lifecycle for one symbol:
      - WebSocket connection (depth diffs + aggTrades) with auto-reconnect
      - REST snapshot fetch for initial sync and resync after gaps
      - Dispatches clean events to callbacks: on_book_update, on_trade
    """

    def __init__(
        self,
        symbol: str,
        on_book_update: Callable[[OrderBook], None],
        on_trade: Callable[[dict], None],
        depth_levels: str = "20",   # Binance partial depth stream level: 5/10/20, or use diff depth for full book
    ):
        self.symbol = symbol.lower()
        self.on_book_update = on_book_update
        self.on_trade = on_trade
        self.book = OrderBook(symbol.upper())

        self._diff_buffer: deque[dict] = deque(maxlen=MAX_BUFFER_SIZE)
        self._buffering = True
        self._running = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop - connects, syncs, streams, and auto-reconnects on failure."""
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                await self._connect_and_stream()
                backoff = 1.0  # reset backoff after a clean run
            except Exception as exc:
                logger.warning(f"Feed error: {exc!r} - reconnecting in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)  # exponential backoff, capped at 30s

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal: connection + sync
    # ------------------------------------------------------------------

    async def _connect_and_stream(self) -> None:
        streams = f"{self.symbol}@depth@100ms/{self.symbol}@aggTrade"
        url = f"{BINANCE_WS_BASE}?streams={streams}"

        self._diff_buffer.clear()
        self._buffering = True
        self.book = OrderBook(self.symbol.upper())

        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            logger.info(f"Connected: {url}")

            # Start consuming messages immediately - buffer depth events
            # while we fetch the snapshot, so we don't miss the gap between
            # "snapshot taken" and "stream starts being applied".
            sync_task = asyncio.create_task(self._fetch_snapshot_and_sync())

            async for raw_msg in ws:
                msg = json.loads(raw_msg)
                payload = msg.get("data", {})
                stream = msg.get("stream", "")

                if "depth" in stream:
                    await self._handle_depth_event(payload)
                elif "aggTrade" in stream:
                    self._handle_trade_event(payload)

                if sync_task.done() and sync_task.exception():
                    raise sync_task.exception()

    async def _fetch_snapshot_and_sync(self) -> None:
        """Fetch REST snapshot, then reconcile against buffered diff events."""
        async with aiohttp.ClientSession() as session:
            url = f"{BINANCE_REST_BASE}/api/v3/depth"
            params = {"symbol": self.symbol.upper(), "limit": 1000}
            async with session.get(url, params=params) as resp:
                snapshot = await resp.json()

        self.book.apply_snapshot(
            last_update_id=snapshot["lastUpdateId"],
            bids=snapshot["bids"],
            asks=snapshot["asks"],
        )
        logger.info(f"Snapshot applied: lastUpdateId={snapshot['lastUpdateId']}")

        # Reconcile buffered events that arrived before/during the snapshot fetch
        applied = 0
        for event in list(self._diff_buffer):
            u = event["u"]
            if u <= self.book.last_update_id:
                continue  # older than snapshot, discard
            self._apply_depth_event(event)
            applied += 1

        self._diff_buffer.clear()
        self._buffering = False
        logger.info(f"Resync complete - {applied} buffered diffs applied")

    # ------------------------------------------------------------------
    # Internal: event handlers
    # ------------------------------------------------------------------

    async def _handle_depth_event(self, event: dict) -> None:
        if self._buffering:
            self._diff_buffer.append(event)
            return

        try:
            self._apply_depth_event(event)
            self.on_book_update(self.book)
        except OrderBookDesyncError as exc:
            logger.warning(f"Desync detected: {exc} - forcing resync")
            self._buffering = True
            self._diff_buffer.clear()
            self._diff_buffer.append(event)
            # Trigger a fresh snapshot fetch; run() loop's outer try/except
            # isn't involved here, so we resync inline.
            await self._fetch_snapshot_and_sync()

    def _apply_depth_event(self, event: dict) -> None:
        self.book.apply_diff(
            first_update_id=event["U"],
            final_update_id=event["u"],
            bid_updates=event["b"],
            ask_updates=event["a"],
        )

    def _handle_trade_event(self, event: dict) -> None:
        """
        Binance aggTrade fields:
          p = price, q = quantity, T = trade time (ms), m = was buyer the market maker?
          m=True  -> a sell-initiated trade hit the bid (aggressor was seller)
          m=False -> a buy-initiated trade hit the ask  (aggressor was buyer)
        """
        trade = {
            "symbol": self.symbol.upper(),
            "price": float(event["p"]),
            "size": float(event["q"]),
            "timestamp": event["T"] / 1000.0,
            "is_buyer_maker": event["m"],
            "side": "sell" if event["m"] else "buy",  # aggressor side
        }
        self.on_trade(trade)
