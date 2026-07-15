"""Side-effect-only import: silence one cosmetic upstream deprecation notice.

`langchain_core.__init__` calls `surface_langchain_deprecation_warnings()` on
every import, which re-inserts its own "default" (show) filter for
`LangChainPendingDeprecationWarning` at the *front* of `warnings.filters` --
after whatever filter existed before, always winning regardless of import
order. A plain `warnings.filterwarnings("ignore", ...)` placed before
importing anything from `langchain_core`/`langgraph` therefore has no effect:
it is always pushed behind langchain's own re-assertion. Importing
`langchain_core` here first lets that self-registration happen, then
registers our "ignore" filter *after* it, so ours is the one in front for the
rest of the process -- including inside `langgraph.checkpoint.serde.jsonplus`
(pulled in by `demand_radar.graph.build`), which triggers this exact warning
from an unrelated deprecated default. `pyproject.toml`'s
`[tool.pytest.ini_options] filterwarnings` covers the same warning for the
test suite (pytest applies its filters at a point that already sees past
langchain's re-assertion); this covers plain `demand-radar ...` invocations,
where nothing else suppresses it. Cosmetic only -- never touches behavior,
only stderr noise on an otherwise-successful run.
"""

from __future__ import annotations

import warnings

import langchain_core.runnables  # noqa: F401

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change in a future version.*",
    category=PendingDeprecationWarning,  # LangChainPendingDeprecationWarning subclasses this
)
