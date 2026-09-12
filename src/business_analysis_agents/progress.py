"""Shared CLI progress reporting with structured JSON Lines logging."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Iterator, TextIO


class ProgressStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    REVISION = "revision"
    COMPLETED = "completed"


_STATUS_LABELS = {
    ProgressStatus.RUNNING: "[RUN]",
    ProgressStatus.PASSED: "[OK]",
    ProgressStatus.FAILED: "[NG]",
    ProgressStatus.WARNING: "[WARN]",
    ProgressStatus.REVISION: "[REV]",
    ProgressStatus.COMPLETED: "[DONE]",
}


class ProgressReporter:
    """Write concise progress to the terminal and metadata-only events to JSONL."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        log_file: Path | str | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.enabled = enabled
        self.log_file = Path(log_file) if log_file is not None else None
        self.stream = stream or sys.stdout
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_file.write_text("", encoding="utf-8")

    def phase(self, index: int, total: int, name: str, phase: str) -> None:
        if self.enabled:
            print(f"\n[Phase {index}/{total}] {name}", file=self.stream, flush=True)
        self._write_event(
            phase=phase,
            step="phase",
            status=ProgressStatus.RUNNING,
            message=name,
        )

    def report(
        self,
        *,
        phase: str,
        step: str,
        status: ProgressStatus,
        message: str,
        iteration: int | None = None,
        max_iterations: int | None = None,
        duration: float | None = None,
        target: str | None = None,
    ) -> None:
        parts = [_STATUS_LABELS[status], message]
        if iteration is not None and max_iterations is not None:
            parts.append(f"{iteration}/{max_iterations}")
        if target:
            parts.append(f"[target: {target}]")
        if duration is not None:
            parts.append(f"({duration:.1f} sec)")
        if self.enabled:
            print(f"  {' '.join(parts)}", file=self.stream, flush=True)
        self._write_event(
            phase=phase,
            step=step,
            status=status,
            message=message,
            iteration=iteration,
            max_iterations=max_iterations,
            duration=duration,
            target=target,
        )

    def _write_event(
        self,
        *,
        phase: str,
        step: str,
        status: ProgressStatus,
        message: str,
        iteration: int | None = None,
        max_iterations: int | None = None,
        duration: float | None = None,
        target: str | None = None,
    ) -> None:
        if self.log_file is None:
            return
        payload = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "phase": phase,
            "step": step,
            "status": status.value,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "duration": round(duration, 3) if duration is not None else None,
            "message": message,
            "target": target,
        }
        with self.log_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def report_progress(
    reporter: ProgressReporter | None,
    *,
    phase: str,
    step: str,
    status: ProgressStatus,
    message: str,
    iteration: int | None = None,
    max_iterations: int | None = None,
    duration: float | None = None,
    target: str | None = None,
) -> None:
    if reporter is not None:
        reporter.report(
            phase=phase,
            step=step,
            status=status,
            message=message,
            iteration=iteration,
            max_iterations=max_iterations,
            duration=duration,
            target=target,
        )


@contextmanager
def progress_operation(
    reporter: ProgressReporter | None,
    *,
    phase: str,
    step: str,
    message: str,
    completed_message: str | None = None,
    iteration: int | None = None,
    max_iterations: int | None = None,
    target: str | None = None,
    start_status: ProgressStatus = ProgressStatus.RUNNING,
) -> Iterator[None]:
    """Report an operation before execution and after completion or failure."""

    report_progress(
        reporter,
        phase=phase,
        step=step,
        status=start_status,
        message=message,
        iteration=iteration,
        max_iterations=max_iterations,
        target=target,
    )
    started = perf_counter()
    try:
        yield
    except Exception:
        report_progress(
            reporter,
            phase=phase,
            step=step,
            status=ProgressStatus.FAILED,
            message=f"{message} failed",
            iteration=iteration,
            max_iterations=max_iterations,
            duration=perf_counter() - started,
            target=target,
        )
        raise
    report_progress(
        reporter,
        phase=phase,
        step=step,
        status=ProgressStatus.PASSED,
        message=completed_message or f"{message} completed",
        iteration=iteration,
        max_iterations=max_iterations,
        duration=perf_counter() - started,
        target=target,
    )
