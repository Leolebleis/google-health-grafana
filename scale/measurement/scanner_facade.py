from typing import Protocol, AsyncIterator
from scale.measurement.model.measurement import Measurement


class ScannerFacade(Protocol):
    async def scan(self) -> AsyncIterator[Measurement]: ...
