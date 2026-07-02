"""Retry with exponential backoff and jitter.

Per the cross-client conventions, every outbound API/network call is wrapped with
retries so a single transient failure never aborts a run.
"""

from __future__ import annotations

import functools
import random
import time
from typing import Callable, Iterable, Type, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Raise to signal a transient failure that should be retried."""


def retry(
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 20.0,
    exceptions: Iterable[Type[BaseException]] = (RetryableError,),
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
):
    """Decorator: retry a function with exponential backoff + full jitter.

    Delay for attempt *n* (0-indexed) is a random value in
    ``[0, min(max_delay, base_delay * 2**n)]``. ``sleep`` and ``rng`` are
    injectable so tests run instantly and deterministically.
    """
    exc_tuple = tuple(exceptions)
    _rng = rng or random.Random()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except exc_tuple as exc:  # transient — back off and retry
                    last_exc = exc
                    if attempt == attempts - 1:
                        break
                    ceiling = min(max_delay, base_delay * (2 ** attempt))
                    sleep(_rng.uniform(0, ceiling))
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
