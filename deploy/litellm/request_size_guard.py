import os

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

MAX_REQUEST_MB = float(os.environ.get("REQUEST_SIZE_GUARD_MAX_MB", "32"))
MAX_REQUEST_CHARS = int(MAX_REQUEST_MB * 1024 * 1024)


def _text_size(value, budget):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        items = value.values()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return 0
    total = 0
    for item in items:
        total += _text_size(item, budget - total)
        if total > budget:
            break
    return total


class RequestSizeGuard(CustomLogger):
    # Runs before routing, so an oversized body is refused once instead of being copied down the fallback chain.
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        size = _text_size(data, MAX_REQUEST_CHARS)
        if size > MAX_REQUEST_CHARS:
            raise HTTPException(
                status_code=413,
                detail=f"Request text exceeds {MAX_REQUEST_MB:g} MB ({size} characters counted)",
            )
        return data


proxy_handler_instance = RequestSizeGuard()
