"""Schema library shim.

Home Assistant moved from voluptuous to its fork ``probatio``; use whichever
this Home Assistant version ships so the integration loads on both.
"""

from __future__ import annotations

try:
    import probatio as schema
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as schema  # type: ignore[no-redef]

__all__ = ["schema"]
