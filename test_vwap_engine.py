"""
Tests VWAPEngine against naive/brute-force recomputation to catch any drift
or bugs in the incremental running-sum approach.
"""

import math
from vwap_engine import VWAPEngine, Trade


def naive_vwap(trades: list[Trade]) -> tuple[float, float]:
    """Brute-force recompute VWAP + std_dev from scratch - used as ground truth."""
    sum_pv = sum(t.price * t.size for t in trades)
    sum_v = sum(t.size for t in trades)
    vwap = sum_pv / sum_v
    variance = sum(t.size * (t.price - vwap) ** 2 for t in trades) / sum_v
    return vwap, math.sqrt(variance)


def test_basic_vwap_matches_naive():
    engine = VWAPEngine(window_seconds=999999)
    trades = [
        Trade(price=100.0, size=1.0, timestamp=1000, side="buy"),
        Trade(price=102.0, size=2.0, timestamp=1001, side="sell"),
        Trade(price=98.0, size=0.5, timestamp=1002, side="buy"),
        Trade(price=101.0, size=3.0, timestamp=1003, side="sell"),
    ]
    for t in trades:
        engine.add_trade(t)

    result = engine.current()
    expected_vwap, expected_std = naive_vwap(trades)

    assert math.isclose(result.vwap, expected_vwap, rel_tol=1e-9), \
        f"vwap mismatch: {result.vwap} vs {expected_vwap}"
    assert math.isclose(result.std_dev, expected_std, rel_tol=1e-9), \
        f"std_dev mismatch: {result.std_dev} vs {expected_std}"
    assert result.sample_count == 4
    assert math.isclose(result.total_volume, 6.5)

    print(f"test_basic_vwap_matches_naive: PASS (vwap={result.vwap:.4f}, std={result.std_dev:.4f})")


def test_bands_are_symmetric_around_vwap():
    engine = VWAPEngine(window_seconds=999999)
    for i, price in enumerate([100, 105, 95, 110, 90]):
        engine.add_trade(Trade(price=float(price), size=1.0, timestamp=1000 + i, side="buy"))

    r = engine.current()
    assert math.isclose(r.upper_band_1 - r.vwap, r.vwap - r.lower_band_1)
    assert math.isclose(r.upper_band_2 - r.vwap, 2 * (r.upper_band_1 - r.vwap))

    print("test_bands_are_symmetric_around_vwap: PASS")


def test_rolling_window_evicts_old_trades():
    # 10 second window
    engine = VWAPEngine(window_seconds=10)

    # old trade at t=0, way outside window once we get to t=20
    engine.add_trade(Trade(price=1000.0, size=5.0, timestamp=0, side="buy"))
    # recent trade at t=20
    engine.add_trade(Trade(price=100.0, size=1.0, timestamp=20, side="buy"))

    result = engine.current(now=20)
    # the t=0 trade (price 1000) should have been evicted - vwap should be
    # dominated by/equal to the only remaining trade
    assert math.isclose(result.vwap, 100.0), f"expected old trade evicted, got vwap={result.vwap}"
    assert result.sample_count == 1

    print("test_rolling_window_evicts_old_trades: PASS")


def test_eviction_via_explicit_now_with_no_new_trades():
    engine = VWAPEngine(window_seconds=5)
    engine.add_trade(Trade(price=50.0, size=1.0, timestamp=0, side="buy"))
    engine.add_trade(Trade(price=60.0, size=1.0, timestamp=1, side="buy"))

    # simulate time passing with no new trades - both should age out
    result = engine.current(now=100)
    assert result is None, "expected empty window after all trades aged out"

    print("test_eviction_via_explicit_now_with_no_new_trades: PASS")


def test_running_sums_stay_consistent_over_many_evictions():
    """Stress test: add+evict repeatedly, verify against naive recompute each time."""
    engine = VWAPEngine(window_seconds=10)
    all_trades_ever = []
    import random
    random.seed(42)

    for i in range(500):
        t = Trade(
            price=100.0 + random.uniform(-5, 5),
            size=random.uniform(0.1, 3.0),
            timestamp=float(i),
            side="buy" if random.random() > 0.5 else "sell",
        )
        engine.add_trade(t)
        all_trades_ever.append(t)

        if i % 50 == 0:  # spot check periodically
            cutoff = t.timestamp - 10
            window_trades = [x for x in all_trades_ever if x.timestamp >= cutoff]
            expected_vwap, expected_std = naive_vwap(window_trades)
            result = engine.current()
            assert math.isclose(result.vwap, expected_vwap, rel_tol=1e-6), \
                f"at i={i}: vwap drift {result.vwap} vs {expected_vwap}"
            # abs_tol matters here: when std_dev is near zero (e.g. only one
            # trade in the window), relative tolerance is meaningless since
            # we're comparing two numbers that are both close to 0.
            assert math.isclose(result.std_dev, expected_std, rel_tol=1e-6, abs_tol=1e-5), \
                f"at i={i}: std_dev drift {result.std_dev} vs {expected_std}"

    print("test_running_sums_stay_consistent_over_many_evictions: PASS (500 trades, spot-checked)")


if __name__ == "__main__":
    test_basic_vwap_matches_naive()
    test_bands_are_symmetric_around_vwap()
    test_rolling_window_evicts_old_trades()
    test_eviction_via_explicit_now_with_no_new_trades()
    test_running_sums_stay_consistent_over_many_evictions()
    print("\nAll VWAP engine tests passed.")
