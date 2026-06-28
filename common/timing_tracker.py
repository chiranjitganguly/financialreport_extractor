"""Pipeline execution timing tracker.

Provides a simple wall-clock timer per named agent/phase.  The tracker is
created at the start of run_report_ingestion() and passed through to the
summary writer so every section of the summary report can include durations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentTiming:
    agent_name: str
    start_time: float
    end_time: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        if self.end_time is None:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    @property
    def duration_str(self) -> str:
        d = self.duration_seconds
        if d < 60:
            return f"{d:.1f}s"
        return f"{d / 60:.1f}m"


class TimingTracker:
    def __init__(self) -> None:
        self._pipeline_start: float = time.time()
        self._timings: list[AgentTiming] = []

    def start(self, agent_name: str) -> AgentTiming:
        timing = AgentTiming(agent_name=agent_name, start_time=time.time())
        self._timings.append(timing)
        return timing

    def stop(self, timing: AgentTiming) -> None:
        timing.end_time = time.time()

    def get_all(self) -> list[AgentTiming]:
        return list(self._timings)

    def total_elapsed_seconds(self) -> float:
        return time.time() - self._pipeline_start

    def total_elapsed_str(self) -> str:
        d = self.total_elapsed_seconds()
        if d < 60:
            return f"{d:.1f}s"
        return f"{d / 60:.1f}m"
