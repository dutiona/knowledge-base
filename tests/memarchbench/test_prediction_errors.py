"""Prediction-error invariant (#360): fires iff stale; precision and recall >= 0.9.

Gated on #250 for the baseline signal; this stub pins the harness shape.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="stub — gated on #250 baseline signal")
def test_prediction_error_precision_recall(mb_conn):
    """Over labeled stale/fresh fixture pairs, prediction-error detection must
    reach precision >= 0.9 AND recall >= 0.9 (both tracked in CI)."""
    raise NotImplementedError
