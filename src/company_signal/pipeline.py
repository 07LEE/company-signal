"""One command that brings everything up to date: collect, build, enrich, build.

The steps run in one process, one after another, so no step finds the database
locked by another. Each step resumes where an earlier run stopped, so after a
failure the same command is simply run again.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .collect import CollectResult
from .enrich import EnrichResult

# The models that read company disclosures. New answers need only these rebuilt.
DISCLOSURE_MODELS = "stg_fsc__corp_outline+"

PASSED = {"success", "pass"}
FAILED = {"error", "fail", "runtime error"}


@dataclass(frozen=True)
class BuildResult:
    success: bool
    passed: int  # models built and tests passed
    failed: int
    other: int  # skipped or warned


@dataclass(frozen=True)
class Steps:
    collect: Callable[[], CollectResult]
    enrich: Callable[[], EnrichResult]
    build: Callable[[str | None], BuildResult]  # called with a dbt selector, or None for everything
    warehouse_exists: Callable[[], bool]


class StepFailed(RuntimeError):
    """A step could not finish. Later steps are not run."""


def run_dbt(project_dir: Path, data_dir: Path, select: str | None = None) -> BuildResult:
    """dbt build, in this process, over the data in `data_dir`."""
    from dbt.cli.main import dbtRunner  # slow to import; only the build needs it

    os.environ["COMPANY_SIGNAL_DATA_DIR"] = str(data_dir.resolve())
    args = ["build", "--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
    if select:
        args += ["--select", select]
    outcome = dbtRunner().invoke(args)
    statuses = [str(r.status) for r in outcome.result.results] if outcome.result is not None else []
    return BuildResult(
        success=outcome.success,
        passed=sum(s in PASSED for s in statuses),
        failed=sum(s in FAILED for s in statuses),
        other=sum(s not in PASSED and s not in FAILED for s in statuses),
    )


def update(steps: Steps, force_build: bool = False, log=print) -> list[str]:
    """Run the steps, building only when there is something new. Returns one summary line per step.

    Raises StepFailed when a step fails; the lines done so far are in its args.
    """
    summary: list[str] = []

    def timed(name: str, step: Callable):
        log(f"== {name}")
        start = time.monotonic()
        try:
            result = step()
        except Exception as exc:
            summary.append(f"{name}: stopped, {exc}")
            raise StepFailed(summary) from exc
        return result, _duration(time.monotonic() - start)

    def build(name: str, select: str | None) -> None:
        result, took = timed(name, lambda: steps.build(select))
        line = f"{name}: {result.passed} passed, {result.failed} failed, {result.other} other, {took}"
        summary.append(line)
        if not result.success:
            raise StepFailed(summary)

    collected, took = timed("collect", steps.collect)
    summary.append(
        f"collect: {collected.publications_completed} publications completed, {collected.pages_fetched:,} pages, {took}"
    )

    if collected.publications_completed or force_build or not steps.warehouse_exists():
        build("build", None)
    else:
        summary.append("build: skipped, no new publication")

    enriched, took = timed("enrich", steps.enrich)
    limit = ", daily limit reached" if enriched.limit_reached else ""
    summary.append(
        f"enrich: {enriched.names_asked:,} names, {enriched.calls:,} calls, {enriched.names_left:,} left{limit}, {took}"
    )

    if enriched.names_asked:
        build("build disclosures", DISCLOSURE_MODELS)
    else:
        summary.append("build disclosures: skipped, no new answers")
    return summary


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
