import gzip
import hashlib
import json

import duckdb
import pytest

from company_signal.compact import CompactRefused, compact
from company_signal.store import Store


def old_database(tmp_path):
    """A collector database as made before the page files became the only copy:
    two page files, the same records again as JSON, and a disclosure answer."""
    db = tmp_path / "company_signal.duckdb"
    store = Store(db)
    store.init()
    with store.connect() as con:
        con.execute("create table raw.nps_workplace_record (publication_id varchar, page integer, row_no integer, record json)")
        for page in (1, 2):
            body = json.dumps({"data": [{"사업장명": f"회사{page}{n}"} for n in range(3)]}, ensure_ascii=False).encode()
            path = tmp_path / "raw" / "uddi_a" / f"page-{page:06d}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wb") as f:
                f.write(body)
            con.execute("insert into meta.page_fetch (publication_id, page, row_count, sha256, file_path) values ('uddi:a', ?, 3, ?, ?)",
                        [page, hashlib.sha256(body).hexdigest(), str(path)])
            con.execute("insert into raw.nps_workplace_record select 'uddi:a', ?, n, '{}' from range(3) as t (n)", [page])
    store.record_fsc_response("한빛", ["{}"], 1, False)
    return db


def test_compact_keeps_everything_but_the_record_copy_and_keeps_the_old_file(tmp_path):
    db = old_database(tmp_path)
    result = compact(db, log=lambda _: None)

    assert result.pages_checked == 2
    assert result.tables_copied["meta.page_fetch"] == 2 and result.tables_copied["meta.fsc_query"] == 1
    assert "raw.nps_workplace_record" not in result.tables_copied
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select count(*) from information_schema.tables where table_name = 'nps_workplace_record'").fetchone() == (0,)
    assert con.execute("select count(*) from raw.fsc_corp_outline_page").fetchone() == (1,)
    con.close()
    assert result.old_path.exists()  # the user deletes it


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_compact_changes_nothing_when_a_page_file_is_not_as_recorded(tmp_path, damage):
    db = old_database(tmp_path)
    page = tmp_path / "raw" / "uddi_a" / "page-000002.json.gz"
    if damage == "missing":
        page.unlink()
    else:
        with gzip.open(page, "wb") as f:
            f.write(b'{"data": []}')
    size = db.stat().st_size

    with pytest.raises(CompactRefused, match="page 2"):
        compact(db, log=lambda _: None)
    assert db.stat().st_size == size
    assert not list(tmp_path.glob("company_signal.duckdb.*"))
