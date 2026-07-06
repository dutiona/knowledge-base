"""Entity-stability invariant (#360): same paper => same entity IDs across
chunking-strategy swap. Pass criterion: 100% ID stability.

The first 10 hand-written fixtures are #360 wave-1 work; this stub pins the
harness shape.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="stub — first 10 hand-written fixtures are #360 wave-1 work")
def test_entity_ids_stable_across_chunking_swap(mb_conn):
    """Ingest + extract with 'mechanical', swap to 'semantic', re-chunk,
    re-extract: canonical entity IDs must be identical."""
    raise NotImplementedError
