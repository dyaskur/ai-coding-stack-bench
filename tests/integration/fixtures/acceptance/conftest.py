import os
import time

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"]


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    deadline = time.monotonic() + 60
    while True:
        try:
            httpx.get(BASE_URL, timeout=2)
            break
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                pytest.fail(f"app did not start at {BASE_URL}")
            time.sleep(1)
    return httpx.Client(base_url=BASE_URL, timeout=10)
