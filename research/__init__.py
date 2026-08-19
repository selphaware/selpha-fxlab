"""Phase 2 research code: the judged surface for analysis tasks.

Four pieces, deliberately small:

* :mod:`research.seal` -- one definition of the holdout cutoff;
* :mod:`research.loader` -- the only way research reads stored data, in one of
  two declared modes, recording everything it serves;
* :mod:`research.ledger` -- append-only record of every experiment, written
  before results exist;
* :mod:`research.walkforward` -- purged and embargoed walk-forward splitting
  and execution;
* :mod:`research.experiment` and :mod:`research.run` -- config, hashing and the
  runner that ties the three together.

Analysis tasks live alongside them as entry points, one module per task card.
Nothing here decides anything: hypothesis selection, universe membership and
advancing or killing a candidate are checkpoint decisions (pre-reg #3).
"""

from __future__ import annotations

__all__ = ["experiment", "ledger", "loader", "seal", "walkforward"]
