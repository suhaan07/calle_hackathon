from __future__ import annotations

import os

_client = None


def is_configured() -> bool:
    return bool(os.getenv("CALLE_API_KEY"))


def get_client():
    global _client
    if _client is None:
        from calle import CalleClient

        _client = CalleClient(api_key=os.getenv("CALLE_API_KEY"))
    return _client
