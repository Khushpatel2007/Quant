"""
Tests OrderBook logic in isolation with synthetic data (no network needed).
This validates the snapshot/diff/desync logic is correct before you run it
against live Binance data on your own machine.
"""

from order_book import OrderBook, OrderBookDesyncError


def test_snapshot_then_clean_diffs():
    book = OrderBook("BTCUSDT")
    book.apply_snapshot(
        last_update_id=100,
        bids=[["50000.0", "1.5"], ["49999.0", "2.0"]],
        asks=[["50001.0", "1.0"], ["50002.0", "3.0"]],
    )
    assert book.best_bid().price == 50000.0
    assert book.best_ask().price == 50001.0
    assert book.spread() == 1.0
    assert book.mid_price() == 50000.5

    # contiguous diff: U=101, u=101 -> covers last_update_id+1=101
    book.apply_diff(101, 101, bid_updates=[["50000.0", "0.5"]], ask_updates=[])
    assert book.bids[50000.0] == 0.5

    # remove a level via qty=0
    book.apply_diff(102, 102, bid_updates=[["49999.0", "0"]], ask_updates=[])
    assert 49999.0 not in book.bids

    print("test_snapshot_then_clean_diffs: PASS")


def test_stale_diff_ignored():
    book = OrderBook("BTCUSDT")
    book.apply_snapshot(100, bids=[["50000.0", "1.0"]], asks=[["50001.0", "1.0"]])

    # this diff's range is entirely before the snapshot - should be silently ignored
    book.apply_diff(50, 99, bid_updates=[["50000.0", "999"]], ask_updates=[])
    assert book.bids[50000.0] == 1.0  # unchanged
    assert book.last_update_id == 100  # unchanged

    print("test_stale_diff_ignored: PASS")


def test_gap_detected():
    book = OrderBook("BTCUSDT")
    book.apply_snapshot(100, bids=[["50000.0", "1.0"]], asks=[["50001.0", "1.0"]])

    # gap: jumps straight to U=105 when we expected U <= 101
    try:
        book.apply_diff(105, 106, bid_updates=[], ask_updates=[])
        assert False, "expected OrderBookDesyncError"
    except OrderBookDesyncError:
        assert book.synced is False

    print("test_gap_detected: PASS")


def test_diff_before_sync_raises():
    book = OrderBook("BTCUSDT")
    try:
        book.apply_diff(1, 1, bid_updates=[], ask_updates=[])
        assert False, "expected OrderBookDesyncError"
    except OrderBookDesyncError:
        pass

    print("test_diff_before_sync_raises: PASS")


def test_top_n_levels_sorted_correctly():
    book = OrderBook("BTCUSDT")
    book.apply_snapshot(
        1,
        bids=[["100", "1"], ["102", "1"], ["101", "1"], ["99", "1"]],
        asks=[["110", "1"], ["108", "1"], ["109", "1"]],
    )
    bid_prices = [lvl.price for lvl in book.top_n_bids(3)]
    ask_prices = [lvl.price for lvl in book.top_n_asks(3)]
    assert bid_prices == [102.0, 101.0, 100.0]  # descending
    assert ask_prices == [108.0, 109.0, 110.0]  # ascending

    print("test_top_n_levels_sorted_correctly: PASS")


if __name__ == "__main__":
    test_snapshot_then_clean_diffs()
    test_stale_diff_ignored()
    test_gap_detected()
    test_diff_before_sync_raises()
    test_top_n_levels_sorted_correctly()
    print("\nAll Stage 1 logic tests passed.")
