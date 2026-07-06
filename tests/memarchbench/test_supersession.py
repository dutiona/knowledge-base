"""Supersession invariant (#360): reingest(newer) => search returns newer, not older.

Pass criterion: 100% on hand-written fixtures, 95% on generated paraphrases.
The first 10 hand-written fixtures are #360 in-scope work (Tier-2 wave 1);
this stub pins the harness shape so they land against a stable seam.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="stub — first 10 hand-written fixtures are #360 wave-1 work")
def test_supersession_handwritten(mb_conn):
    """For each (old, new, query) fixture: ingest old, reingest new at the same
    source_uri, assert search(query) top hit is the NEW content."""
    raise NotImplementedError
