import os
import time
from datetime import date

from company_signal.api import Publication
from company_signal.cli import build_note
from company_signal.config import Settings
from company_signal.store import Store


def test_status_says_whether_the_lookup_tables_are_behind_the_collected_data(tmp_path):
    settings = Settings(root=tmp_path, service_key="")
    settings.data_dir.mkdir()
    store = Store(settings.db_path)
    store.init()
    assert build_note(settings, store) == "lookup tables: not built yet. Run company-signal update."

    store.upsert_publications([Publication("uddi:a", "시험_20261022", date(2026, 10, 22))])
    with store.connect() as con:
        con.execute("update meta.publication set status = 'complete', completed_at = now()")
    store.record_fsc_response("한빛", ["{}"], 1, False)

    # Built before the data came in.
    settings.warehouse_path.touch()
    earlier = time.time() - 3600
    os.utime(settings.warehouse_path, (earlier, earlier))
    note = build_note(settings, store)
    assert "since then 1 publications completed and 1 disclosure answers stored" in note

    # Built after.
    settings.warehouse_path.touch()
    assert build_note(settings, store).endswith(", up to date")
