import asyncio
import time
from enum import Enum

import httpx


MAX_CONCURRENT_REQUESTS = 100
MAX_RETRIES = 3
BASE_DELAY = 1

FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT = 30


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        recovery_timeout: int = RECOVERY_TIMEOUT
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.failure_count = 0
        self.state = CircuitState.CLOSED

        self.opened_at = None

    def can_execute(self) -> bool:

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:

            if self.opened_at is None:
                return False

            elapsed = time.monotonic() - self.opened_at

            if elapsed >= self.recovery_timeout:

                self.state = CircuitState.HALF_OPEN

                return True

            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_success(self):

        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.opened_at = None

    def record_failure(self):

        self.failure_count += 1

        if self.failure_count >= self.failure_threshold:

            self.state = CircuitState.OPEN

            self.opened_at = time.monotonic()


class MonitoringService:

    def __init__(self):

        self.semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_REQUESTS
        )

        self.client = httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True
        )

        self.circuit_breakers: dict[
            str,
            CircuitBreaker
        ] = {}

    def get_circuit_breaker(
        self,
        url: str
    ) -> CircuitBreaker:

        if url not in self.circuit_breakers:

            self.circuit_breakers[url] = (
                CircuitBreaker()
            )

        return self.circuit_breakers[url]

    async def check_url(
        self,
        url: str
    ) -> dict:

        async with self.semaphore:

            circuit = self.get_circuit_breaker(url)

            if not circuit.can_execute():

                return {
                    "url": url,
                    "is_up": False,
                    "status_code": None,
                    "response_time_ms": None,
                    "error": "Circuit breaker is OPEN",
                    "attempt": 0
                }

            for attempt in range(MAX_RETRIES + 1):

                start_time = time.perf_counter()

                try:

                    response = await self.client.get(url)

                    end_time = time.perf_counter()

                    response_time_ms = round(
                        (end_time - start_time) * 1000
                    )

                    if response.is_success:

                        circuit.record_success()

                        return {
                            "url": url,
                            "is_up": True,
                            "status_code": response.status_code,
                            "response_time_ms": response_time_ms,
                            "error": None,
                            "attempt": attempt + 1,
                            "circuit_state": circuit.state.value
                        }

                    circuit.record_failure()

                    if attempt < MAX_RETRIES:

                        delay = BASE_DELAY * (
                            2 ** attempt
                        )

                        await asyncio.sleep(delay)

                        continue

                    return {
                        "url": url,
                        "is_up": False,
                        "status_code": response.status_code,
                        "response_time_ms": response_time_ms,
                        "error": f"HTTP {response.status_code}",
                        "attempt": attempt + 1,
                        "circuit_state": circuit.state.value
                    }

                except httpx.TimeoutException:

                    circuit.record_failure()

                    if attempt < MAX_RETRIES:

                        delay = BASE_DELAY * (
                            2 ** attempt
                        )

                        print(
                            f"Timeout for {url}. "
                            f"Retrying in {delay}s..."
                        )

                        await asyncio.sleep(delay)

                        continue

                    return {
                        "url": url,
                        "is_up": False,
                        "status_code": None,
                        "response_time_ms": None,
                        "error": "Request timed out",
                        "attempt": attempt + 1,
                        "circuit_state": circuit.state.value
                    }

                except httpx.RequestError as error:

                    circuit.record_failure()

                    if attempt < MAX_RETRIES:

                        delay = BASE_DELAY * (
                            2 ** attempt
                        )

                        print(
                            f"Request failed for {url}. "
                            f"Retrying in {delay}s..."
                        )

                        await asyncio.sleep(delay)

                        continue

                    return {
                        "url": url,
                        "is_up": False,
                        "status_code": None,
                        "response_time_ms": None,
                        "error": str(error),
                        "attempt": attempt + 1,
                        "circuit_state": circuit.state.value
                    }

    async def check_multiple(
        self,
        urls: list[str]
    ) -> list[dict]:

        tasks = [
            self.check_url(url)
            for url in urls
        ]

        return await asyncio.gather(*tasks)

    async def close(self):

        await self.client.aclose()