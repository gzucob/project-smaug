"""Read already computed summaries for the home and portfolio screens."""

from smaug.analysis.domain.summary import AnalysisSummary, AnalysisSummaryReader


class ReadAnalysisSummariesUseCase:
    """Return persisted indicator values without calculating or changing them."""

    def __init__(self, reader: AnalysisSummaryReader) -> None:
        self._reader = reader

    async def execute(self) -> list[AnalysisSummary]:
        return await self._reader.all_latest()
