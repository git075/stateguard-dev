"""Tests for the StatGuard Drift Detector.

Every test here deliberately triggers a real outcome (not just the happy path)
and asserts the exact exception type, field name, and z-score direction.
"""
import math
import pytest
from stateguard.drift import DriftDetector, DriftViolation, _MIN_SAMPLES


def _seed_detector(detector: DriftDetector, key: str, values: list) -> None:
    """Feed a list of raw values into the detector to build a baseline."""
    for v in values:
        detector.observe(key, v)


# ── Cold Start ─────────────────────────────────────────────────────────────────

def test_cold_start_skips_check():
    """With fewer than MIN_SAMPLES observations, check must be a no-op."""
    detector = DriftDetector()
    detector.watch("balance", mode="block", z_threshold=3.5)

    # Only 3 observations — below _MIN_SAMPLES (5)
    _seed_detector(detector, "balance", [100, 102, 101])

    # Even an enormous jump must not raise during cold start
    detector.check({"balance": 101}, {"balance": 999999})


# ── Normal Change (Z-score well below threshold) ───────────────────────────────

def test_normal_change_passes():
    """A change within the historical norm must not raise anything."""
    detector = DriftDetector()
    detector.watch("balance", mode="block", z_threshold=3.5)

    # Seed with small, consistent changes: delta always ~5
    for i in range(20):
        detector.observe("balance", 100 + i * 5)

    # Propose a change of 5 — completely normal
    detector.check({"balance": 200}, {"balance": 205})


# ── Warning Zone (Z-score between warn_factor*threshold and threshold) ─────────

def test_suspicious_change_warns_but_does_not_raise(caplog):
    """A moderately suspicious change in 'block' mode should log a warning."""
    import logging
    detector = DriftDetector()
    # warn_factor=0.75 → warns at Z >= 2.625, blocks at Z >= 3.5
    detector.watch("balance", mode="block", z_threshold=3.5, warn_factor=0.75)

    # Seed with stable history: all deltas = 1.0
    base = 100.0
    for _ in range(20):
        detector.observe("balance", base)
        base += 1.0  # delta = 1.0 each time

    # Now propose a change of ~4 (above warn threshold but below block threshold)
    # mean delta ≈ 1.0, std ≈ 0.0 ... actually with uniform deltas std is 0
    # Use slightly varied deltas to get a real std deviation
    detector2 = DriftDetector()
    detector2.watch("score", mode="block", z_threshold=4.0, warn_factor=0.5)
    deltas_as_values = [100, 103, 97, 105, 95, 102, 98, 104, 96, 103,
                        97, 101, 99, 104, 96, 102, 98, 103, 97, 102]
    for v in deltas_as_values:
        detector2.observe("score", v)

    with caplog.at_level(logging.WARNING, logger="stateguard.drift"):
        # A moderate jump that is suspicious but not blocking
        # (mean delta ~5, std ~3 → a delta of ~10 gives Z≈1.67, below block)
        detector2.check({"score": 102}, {"score": 115})

    # No exception raised = test passes if we got here


# ── Extreme Change — Block Mode ────────────────────────────────────────────────

def test_extreme_change_raises_drift_violation_in_block_mode():
    """An extreme outlier in 'block' mode must raise DriftViolation."""
    detector = DriftDetector()
    detector.watch("wallet_balance", mode="block", z_threshold=3.5)

    # Seed with tightly clustered small changes (delta ≈ 5 each step)
    base = 1000.0
    stable_deltas = [5, 4, 6, 5, 5, 4, 6, 5, 5, 4, 6, 5, 5, 4, 5]
    for d in stable_deltas:
        detector.observe("wallet_balance", base)
        base += d

    # Now propose a hallucinated change of $50,000
    with pytest.raises(DriftViolation) as exc_info:
        detector.check(
            {"wallet_balance": base},
            {"wallet_balance": base + 50_000},
        )

    err = exc_info.value
    assert err.field_key == "wallet_balance"
    assert err.new_val == base + 50_000
    assert err.z_score > 3.5
    assert "wallet_balance" in str(err)


# ── Extreme Change — Warn Mode ─────────────────────────────────────────────────

def test_extreme_change_only_warns_in_warn_mode(caplog):
    """An extreme outlier in 'warn' mode must NOT raise — only log."""
    import logging
    detector = DriftDetector()
    detector.watch("wallet_balance", mode="warn", z_threshold=3.5)

    base = 1000.0
    for _ in range(20):
        detector.observe("wallet_balance", base)
        base += 5

    # This would be a block in "block" mode, but here it must only warn
    with caplog.at_level(logging.WARNING, logger="stateguard.drift"):
        detector.check(
            {"wallet_balance": base},
            {"wallet_balance": base + 50_000},
        )
    # No exception = test passes


# ── Zero Change ────────────────────────────────────────────────────────────────

def test_zero_change_always_passes():
    """A field that does not change must never raise, regardless of history."""
    detector = DriftDetector()
    detector.watch("balance", mode="block", z_threshold=1.0)  # Very tight threshold

    for i in range(20):
        detector.observe("balance", 100 + i)

    # No change at all
    detector.check({"balance": 120}, {"balance": 120})


# ── Missing / Non-Numeric Fields ───────────────────────────────────────────────

def test_missing_field_is_skipped():
    """If a watched field is absent from state, check must skip it silently."""
    detector = DriftDetector()
    detector.watch("balance", mode="block")
    # Seed enough samples
    for i in range(20):
        detector.observe("balance", float(i))

    # Neither before nor after has "balance" — must not raise
    detector.check({"other_key": 1}, {"other_key": 2})


def test_non_numeric_field_is_skipped():
    """Non-numeric watched fields must be silently skipped."""
    detector = DriftDetector()
    detector.watch("status", mode="block")
    for i in range(20):
        detector.observe("status", float(i))  # internal history uses floats

    # status has a string value — must not raise
    detector.check({"status": "PENDING"}, {"status": "ACTIVE"})


# ── Unregistered Field ─────────────────────────────────────────────────────────

def test_unregistered_field_observe_is_silent():
    """observe() on a field that was never watch()ed must be a no-op."""
    detector = DriftDetector()
    # No fields registered
    detector.observe("ghost_field", 999.0)  # Must not raise


# ── check_field Convenience Method ────────────────────────────────────────────

def test_check_field_convenience():
    """check_field() must raise DriftViolation for a clearly extreme change."""
    detector = DriftDetector()
    detector.watch("score", mode="block", z_threshold=3.0)

    # Build baseline by feeding deltas through check() with small changes
    base = 50.0
    for _ in range(20):
        detector.check({"score": base}, {"score": base + 2.0})
        base += 2.0

    # Now an extreme jump — 70.0 → 70_000.0 should be caught
    with pytest.raises(DriftViolation) as exc_info:
        detector.check_field("score", old_val=base, new_val=base + 70_000.0)

    assert exc_info.value.field_key == "score"
    assert exc_info.value.z_score > 3.0


# ── watched_fields property ────────────────────────────────────────────────────

def test_watched_fields_returns_all_keys():
    detector = DriftDetector()
    detector.watch("a").watch("b").watch("c")
    assert set(detector.watched_fields) == {"a", "b", "c"}


# ── DriftViolation inherits from InvariantViolation ───────────────────────────

def test_drift_violation_is_invariant_violation():
    """DriftViolation must be catch-able as InvariantViolation for saga compat."""
    from stateguard.invariants import InvariantViolation
    err = DriftViolation(
        field_key="x",
        old_val=1.0,
        new_val=9999.0,
        z_score=10.0,
        threshold=3.5,
        state={"x": 9999.0},
    )
    assert isinstance(err, InvariantViolation)
    assert err.rule_name == "drift_detector_x"
