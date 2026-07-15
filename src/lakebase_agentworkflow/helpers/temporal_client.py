from __future__ import annotations

import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter


async def connect_temporal() -> Client:
    address = os.getenv("TEMPORAL_ADDRESS")
    if not address:
        raise RuntimeError("TEMPORAL_ADDRESS is required; set it in .env or the runtime environment")

    namespace = os.getenv("TEMPORAL_NAMESPACE")
    if not namespace:
        raise RuntimeError("TEMPORAL_NAMESPACE is required; set it in .env or the runtime environment")

    api_key = os.getenv("TEMPORAL_API_KEY") or None
    tls_value = os.getenv("TEMPORAL_TLS")
    tls = api_key is not None if tls_value in {None, ""} else tls_value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return await Client.connect(
        address,
        namespace=namespace,
        api_key=api_key,
        tls=tls,
        data_converter=pydantic_data_converter,
    )
