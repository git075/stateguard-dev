"""Statistical Drift Detector for StateGuard.

Catches 'soft hallucinations' — state changes that pass all hard business
rules but are statistically improbable given the field's recent history.

Uses a rolling Z-score calculation. No external dependencies required —
only Python stdlib (math, statistics, collections).

Example usage::

    from stateguard.drift import DriftDetector, DriftViolation

    detector = DriftDetector()
    detector.watch("wallet_balance", z_threshold=3.5, mode="block")

    # Feed historical observations to build baseline
    detector.observe("wallet_balance", 100.0)
    detector.observe("wallet_balance", 105.0)
    detector.observe("wallet_balance", 98.0)
    # ... (needs at least `min_samples` observations before checking)

    # Check a proposed new value
    detector.check_field("wallet_balance", old_val=100.0, new_val=50000.0)
    # ^ Raises DriftViolation: Z-score 49.7 >> 3.5 threshold
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

from stateguard.invariants import InvariantViolation

logger = logging.getLogger("stateguard.drift")

# ── Constants ──────────────────────────────────────────────────────────────────

_DEFAULT_WINDOW      = 50   # Rolling window size (number of recent deltas stored)
_DEFAULT_THRESHOLD   = 3.5  # Z-score above which a change is flagged
_DEFAULT_WARN_FACTOR = 0.75 # Fraction of threshold that triggers a warning (not a block)
_MIN_SAMPLES         = 5    # Minimum observations before drift detection is active


# ── Exceptions ─────────────────────────────────────────────────────────────────

class DriftViolation(InvariantViolation):
    """Raised when a field's change is a statistical outlier.

    Inherits from InvariantViolation so existing saga rollback and
    exception-handling code works without modification.
    """

    def __init__(
        self,
        field_key: str,
        old_val: float,
        new_val: float,
        z_score: float,
        threshold: float,
        state: Dict[str, Any],
    ) -> None:
        self.field_key = field_key
        self.old_val   = old_val
        self.new_val   = new_val
        self.z_score   = z_score
        self.threshold = threshold
        msg = (
            f"Field '{field_key}' changed by a statistically improbable amount "
            f"({old_val!r} → {new_val!r}). "
            f"Z-score={z_score:.2f} exceeds threshold={threshold}."
        )
        super().__init__(
            rule_name=f"drift_detector_{field_key}",
            message=msg,
            state=state,
        )


# ── Internal data structures ───────────────────────────────────────────────────

@dataclass
class _FieldWatch:
    """Internal record for a single watched field."""

    key:           str
    mode:          str            # "block" | "warn"
    window_size:   int
    z_threshold:   float
    warn_factor:   float
    # Rolling window of *absolute deltas* (|new - old|) observed so far
    deltas:        Deque[float]   = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.mode not in ("block", "warn"):
            raise ValueError(
                f"DriftDetector mode must be 'block' or 'warn', got '{self.mode}'."
            )
        if self.z_threshold <= 0:
            raise ValueError("z_threshold must be a positive number.")
        if self.window_size < _MIN_SAMPLES:
            raise ValueError(
                f"window_size must be >= {_MIN_SAMPLES}, got {self.window_size}."
            )
        self.deltas = deque(maxlen=self.window_size)

    @property
    def has_enough_samples(self) -> bool:
        return len(self.deltas) >= _MIN_SAMPLES

    def record_delta(self, delta: float) -> None:
        """Append an observed absolute delta to the rolling window."""
        self.deltas.append(abs(delta))

    def z_score_of(self, proposed_delta: float) -> Optional[float]:
        """Compute the Z-score of a proposed absolute delta.

        Returns None if there are not enough samples yet (cold start).
        Returns 0.0 if the standard deviation of the window is zero
        (all observed deltas are identical — change is in-line with history).
        """
        if not self.has_enough_samples:
            return None

        abs_delta = abs(proposed_delta)
        n         = len(self.deltas)
        mean      = sum(self.deltas) / n
        variance  = sum((x - mean) ** 2 for x in self.deltas) / n
        std_dev   = math.sqrt(variance)

        if std_dev == 0.0:
            # All historical changes were identical in magnitude.
            # If the proposed change is meaningfully different from the mean,
            # it is infinitely anomalous relative to a zero-variance baseline.
            if abs(abs_delta - mean) > 1e-9:
                return float("inf")
            return 0.0

        return (abs_delta - mean) / std_dev


# ── Public API ─────────────────────────────────────────────────────────────────

class DriftDetector:
    """Watches specified state fields for statistically improbable changes.

    The detector maintains a rolling window of observed absolute deltas
    (changes) for each watched field. When ``check()`` is called, it
    computes the Z-score of the proposed change for each watched field.

    Modes:
        ``"block"``: Raises ``DriftViolation`` if Z-score ≥ ``z_threshold``.
                     Raises a logged ``WARNING`` if Z-score ≥ ``z_threshold × warn_factor``.
        ``"warn"``:  Only logs a warning, never raises — even for extreme outliers.

    Cold Start Safety:
        If fewer than ``_MIN_SAMPLES`` deltas have been observed for a field,
        drift checking is silently skipped for that field. This prevents
        false positives when the system first starts up.
    """

    def __init__(self) -> None:
        self._watches: Dict[str, _FieldWatch] = {}

    def watch(
        self,
        key:         str,
        mode:        str   = "block",
        window_size: int   = _DEFAULT_WINDOW,
        z_threshold: float = _DEFAULT_THRESHOLD,
        warn_factor: float = _DEFAULT_WARN_FACTOR,
    ) -> "DriftDetector":
        """Register a field for drift monitoring.

        Args:
            key:         State dictionary key to monitor (e.g. ``"wallet_balance"``).
            mode:        ``"block"`` to raise ``DriftViolation``,
                         ``"warn"`` to log only.
            window_size: Rolling window of recent deltas to keep. Default: 50.
            z_threshold: Z-score at or above which a change is flagged. Default: 3.5.
            warn_factor: Fraction of threshold that triggers a warning log
                         before a full block. Default: 0.75 (warn at Z ≥ 2.625).

        Returns:
            self — enables fluent chaining: ``detector.watch("a").watch("b")``.
        """
        self._watches[key] = _FieldWatch(
            key=key,
            mode=mode,
            window_size=window_size,
            z_threshold=z_threshold,
            warn_factor=warn_factor,
        )
        logger.debug(f"[DRIFT] Watching field '{key}' (mode={mode}, threshold={z_threshold})")
        return self

    def observe(self, key: str, value: float) -> None:
        """Feed an observed raw value for a watched field.

        Internally, the detector converts consecutive raw values into
        absolute deltas. If only one value has been observed (no prior
        value to diff against), the delta is treated as 0.0.

        Args:
            key:   The field key (must already be registered via ``watch()``).
            value: The observed numeric value of the field.
        """
        if key not in self._watches:
            return  # Silently ignore unregistered fields
        watch = self._watches[key]

        # Use the last known value in the deque to compute delta.
        # If the window is empty, the first observation sets the baseline (delta=0).
        if watch.deltas:
            # Reconstruct the last raw value is not stored — store deltas only.
            # So we treat the first observation as baseline; subsequent calls compute delta.
            pass
        # Store the raw value as a sentinel using a private attribute if needed.
        # Simple approach: maintain a `_last_value` per watch.
        last = getattr(watch, "_last_value", None)
        if last is not None:
            watch.record_delta(abs(value - last))
        watch._last_value = value  # type: ignore[attr-defined]

    def check(
        self,
        state_before: Dict[str, Any],
        state_after:  Dict[str, Any],
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Check all watched fields for drift between two state snapshots.

        Automatically feeds the observed delta into each field's rolling
        window if the check passes (i.e., the change was legitimate).

        Args:
            state_before:   State dict BEFORE the agent's mutations.
            state_after:    State dict AFTER the agent's mutations (proposed).
            state_snapshot: Full state snapshot to attach to any raised
                            ``DriftViolation`` (defaults to ``state_after``).

        Raises:
            DriftViolation: If any watched field's change is a statistical
                            outlier AND the field's mode is ``"block"``.
        """
        snap = state_snapshot or state_after

        for key, watch in self._watches.items():
            old_val = state_before.get(key)
            new_val = state_after.get(key)

            # Skip if either value is missing or non-numeric
            if old_val is None or new_val is None:
                continue
            if not isinstance(old_val, (int, float)) or not isinstance(new_val, (int, float)):
                continue

            proposed_delta = new_val - old_val

            # Skip if no change
            if proposed_delta == 0:
                watch.record_delta(0.0)
                continue

            z = watch.z_score_of(proposed_delta)

            if z is None:
                # Cold start — not enough history yet. Record delta and move on.
                watch.record_delta(abs(proposed_delta))
                logger.debug(
                    f"[DRIFT] '{key}': cold start — skipping check "
                    f"({len(watch.deltas)}/{_MIN_SAMPLES} samples)."
                )
                continue

            warn_threshold  = watch.z_threshold * watch.warn_factor
            is_extreme      = z >= watch.z_threshold
            is_suspicious   = z >= warn_threshold

            if is_suspicious:
                logger.warning(
                    f"[DRIFT WARNING] Field '{key}' changed by a suspicious amount "
                    f"({old_val!r} → {new_val!r}). Z-score={z:.2f} "
                    f"(warn_threshold={warn_threshold:.2f}, block_threshold={watch.z_threshold})."
                )

            if is_extreme:
                if watch.mode == "block":
                    raise DriftViolation(
                        field_key=key,
                        old_val=float(old_val),
                        new_val=float(new_val),
                        z_score=z,
                        threshold=watch.z_threshold,
                        state=snap,
                    )
                # warn mode: already logged above, continue

            # Change was accepted — record it so future checks can learn from it
            if not is_extreme:
                watch.record_delta(abs(proposed_delta))

    def check_field(
        self,
        key:      str,
        old_val:  float,
        new_val:  float,
        state:    Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convenience method to check a single field directly.

        Args:
            key:     The field key to check.
            old_val: Value before the agent's mutation.
            new_val: Value after the agent's mutation (proposed).
            state:   Optional state dict to attach to any raised exception.
        """
        self.check(
            state_before={key: old_val},
            state_after={key: new_val},
            state_snapshot=state or {key: new_val},
        )

    @property
    def watched_fields(self) -> list:
        """Return a list of all currently watched field keys."""
        return list(self._watches.keys())
