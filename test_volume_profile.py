"""
Tests for VolumeProfileEngine.
"""

from volume_profile import VolumeProfileEngine


def test_poc_is_highest_volume_bucket():
    eng = VolumeProfileEngine(bin_size=1.0, window_seconds=9999)

    # Trade 5 BTC at 100, 2 BTC at 101, 1 BTC at 99
    eng.add_trade(100.0, 5.0, timestamp=1000)
    eng.add_trade(101.0, 2.0, timestamp=1001)
    eng.add_trade(99.0,  1.0, timestamp=1002)

    r = eng.current()
    assert r.poc == 100.0, f"expected POC=100, got {r.poc}"
    assert r.poc_volume == 5.0
    assert r.total_volume == 8.0

    print("test_poc_is_highest_volume_bucket: PASS")


def test_bucketing_rounds_correctly():
    eng = VolumeProfileEngine(bin_size=5.0, window_seconds=9999)

    # 64283 should go into bucket 64285 (nearest $5)
    eng.add_trade(64283.0, 1.0, timestamp=1000)
    eng.add_trade(64282.0, 1.0, timestamp=1001)  # → bucket 64280
    eng.add_trade(64285.0, 2.0, timestamp=1002)  # → bucket 64285

    r = eng.current()
    # bucket 64285 should have 1+2=3 BTC (64283 rounds to 64285)
    assert r.poc == 64285.0, f"expected POC=64285, got {r.poc}"
    assert r.poc_volume == 3.0

    print("test_bucketing_rounds_correctly: PASS")


def test_value_area_covers_70pct():
    eng = VolumeProfileEngine(bin_size=1.0, window_seconds=9999)

    # Create a known distribution:
    # price 100: 70 BTC  → alone this is 70% of 100 total
    # price 101: 20 BTC
    # price 102: 10 BTC
    eng.add_trade(100.0, 70.0, timestamp=1000)
    eng.add_trade(101.0, 20.0, timestamp=1001)
    eng.add_trade(102.0, 10.0, timestamp=1002)

    r = eng.current()
    assert r.total_volume == 100.0
    assert r.poc == 100.0
    # Value area: POC bucket (100) alone = 70% → VAL=100, VAH=101
    assert r.val == 100.0, f"VAL expected 100, got {r.val}"
    assert r.vah == 101.0, f"VAH expected 101, got {r.vah}"

    print("test_value_area_covers_70pct: PASS")


def test_rolling_eviction_updates_poc():
    eng = VolumeProfileEngine(bin_size=1.0, window_seconds=10)

    # Old large trade at price 100, will age out
    eng.add_trade(100.0, 50.0, timestamp=0)
    # New smaller trade at price 200, stays in window
    eng.add_trade(200.0, 1.0,  timestamp=20)

    r = eng.current(now=20)
    # Old trade at 100 aged out → POC should now be 200
    assert r.poc == 200.0, f"expected POC=200 after eviction, got {r.poc}"
    assert r.total_volume == 1.0

    print("test_rolling_eviction_updates_poc: PASS")


def test_empty_returns_none():
    eng = VolumeProfileEngine()
    assert eng.current() is None

    # Add then fully evict
    eng.add_trade(100.0, 1.0, timestamp=0)
    result = eng.current(now=99999)
    assert result is None

    print("test_empty_returns_none: PASS")


def test_top_buckets_sorted_by_volume():
    eng = VolumeProfileEngine(bin_size=1.0, window_seconds=9999)
    import random
    random.seed(7)
    for i in range(50):
        eng.add_trade(
            price=100.0 + random.randint(0, 9),
            size=random.uniform(0.1, 5.0),
            timestamp=float(i),
        )
    r = eng.current()
    vols = [v for _, v in r.top_buckets]
    assert vols == sorted(vols, reverse=True), "top_buckets not sorted by volume descending"
    assert len(r.top_buckets) <= 10

    print("test_top_buckets_sorted_by_volume: PASS")


if __name__ == "__main__":
    test_poc_is_highest_volume_bucket()
    test_bucketing_rounds_correctly()
    test_value_area_covers_70pct()
    test_rolling_eviction_updates_poc()
    test_empty_returns_none()
    test_top_buckets_sorted_by_volume()
    print("\nAll Volume Profile tests passed.")
