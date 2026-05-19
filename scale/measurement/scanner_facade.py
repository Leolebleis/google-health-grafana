from collections.abc import AsyncGenerator
from typing import Protocol

from scale.measurement.model.measurement import Measurement


class ScannerFacade(Protocol):
    async def scan(self) -> AsyncGenerator[Measurement]: ...
