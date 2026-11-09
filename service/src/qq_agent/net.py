"""Environment shims.

Some Windows setups set ``NO_PROXY`` with bracketed IPv6 entries such as
``[::1]``.  httpx turns each entry into a mount pattern (``all://[::1]``) and
then fails to parse it, so merely constructing ``httpx.AsyncClient()`` raises
``InvalidURL: Invalid port: ':1]'``.  Stripping the brackets is enough.
"""

from __future__ import annotations

import os


def sanitize_proxy_env() -> None:
    for key in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(key)
        if value and ("[" in value or "]" in value):
            os.environ[key] = value.replace("[", "").replace("]", "")


sanitize_proxy_env()

__all__ = ["sanitize_proxy_env"]
