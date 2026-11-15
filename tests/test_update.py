import pytest

from company_signal.api import FetchError
from company_signal.collect import CollectResult
from company_signal.enrich import EnrichResult
from company_signal.pipeline import DISCLOSURE_MODELS, BuildResult, StepFailed, Steps, update

NOTHING_COLLECTED = CollectResult(publications_completed=0, pages_fetched=0)
NOTHING_ASKED = EnrichResult(names_asked=0, calls=0, names_left=0, limit_reached=False)
BUILT = BuildResult(success=True, passed=40, failed=0, other=0)


def fake_steps(calls, collected=NOTHING_COLLECTED, enriched=NOTHING_ASKED, built=BUILT, warehouse=True):
    def step(name, result):
        def run(*args):
            calls.append((name, *args))
            if isinstance(result, Exception):
                raise result
            return result
        return run

    return Steps(
        collect=step("collect", collected),
        enrich=step("enrich", enriched),
        build=step("build", built),
        warehouse_exists=lambda: warehouse,
    )


def test_new_data_is_built_before_and_after_the_disclosures():
    calls = []
    steps = fake_steps(
        calls,
        collected=CollectResult(publications_completed=1, pages_fetched=590),
        enriched=EnrichResult(names_asked=120, calls=130, names_left=5, limit_reached=True),
    )
    summary = update(steps, log=lambda _: None)
    # The full build gives enrich the new workplaces to ask about; the answers need only the disclosure models.
    assert calls == [("collect",), ("build", None), ("enrich",), ("build", DISCLOSURE_MODELS)]
    assert summary[0].startswith("collect: 1 publications completed, 590 pages")
    assert "daily limit reached" in summary[2]


def test_nothing_new_builds_nothing():
    calls = []
    summary = update(fake_steps(calls), log=lambda _: None)
    assert calls == [("collect",), ("enrich",)]
    assert summary[1] == "build: skipped, no new publication"
    assert summary[3] == "build disclosures: skipped, no new answers"


def test_a_missing_warehouse_or_force_builds_anyway():
    calls = []
    update(fake_steps(calls, warehouse=False), log=lambda _: None)
    assert ("build", None) in calls
    calls.clear()
    update(fake_steps(calls), force_build=True, log=lambda _: None)
    assert ("build", None) in calls


def test_a_failed_step_stops_the_steps_after_it():
    calls = []
    with pytest.raises(StepFailed) as failed:
        update(fake_steps(calls, collected=FetchError("HTTP 503")), log=lambda _: None)
    assert calls == [("collect",)]
    assert failed.value.args[0] == ["collect: stopped, HTTP 503"]

    calls.clear()
    broken = BuildResult(success=False, passed=30, failed=2, other=8)
    collected = CollectResult(publications_completed=1, pages_fetched=10)
    with pytest.raises(StepFailed) as failed:
        update(fake_steps(calls, collected=collected, built=broken), log=lambda _: None)
    assert calls == [("collect",), ("build", None)]  # no enrich over a broken build
    assert failed.value.args[0][-1].startswith("build: 30 passed, 2 failed, 8 other")
