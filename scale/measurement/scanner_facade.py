from collections.abc import AsyncIterator
from typing import Protocol

from scale.measurement.model.measurement import Measurement


class ScannerFacade(Protocol):
    async def scan(self) -> AsyncIterator[Measurement]: ...
