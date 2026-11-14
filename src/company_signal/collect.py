"""Downloads publications, newest first, into raw files and DuckDB."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from datetime import date
from pathlib import Path

from .api import PER_PAGE, Client, Publication
from .store import Store


def pick_recent(publications: list[Publication], months: int) -> list[Publication]:
    """The `months` newest publications by title date. Undated ones are never picked."""
    dated = [p for p in publications if p.title_date is not None]
    dated.sort(key=lambda p: p.title_date or date.min, reverse=True)
    return dated[:months]


def page_path(raw_dir: Path, publication_id: str, page: int) -> Path:
    return raw_dir / publication_id.replace(":", "_") / f"page-{page:06d}.json.gz"


def write_atomic(path: Path, content: bytes) -> None:
    """Never leave a half-written file behind if the write is interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)


def collect_publication(
    client: Client,
    store: Store,
    raw_dir: Path,
    publication: Publication,
    max_pages: int | None = None,
    log=print,
) -> bool:
    """Download one publication. Returns True when it is complete."""
    pid = publication.publication_id
    if store.status_of(pid) == "complete":
        log(f"  skipped (already complete): {publication.title}")
        return True

    done_pages = store.fetched_pages(pid)

    def save(page: int, body: bytes) -> None:
        path = page_path(raw_dir, pid, page)
        write_atomic(path, body)
        store.record_page(pid, page, hashlib.sha256(body).hexdigest(), path)

    # Page 1 tells the total row count. If already fetched, read it from the file.
    if 1 in done_pages:
        with gzip.open(page_path(raw_dir, pid, 1), "rb") as f:
            total = json.loads(f.read())["totalCount"]
    else:
        body = client.fetch_page(pid, 1)
        total = json.loads(body)["totalCount"]
        store.set_totals(pid, total, math.ceil(total / PER_PAGE))
        save(1, body)
        done_pages.add(1)

    page_count = math.ceil(total / PER_PAGE)
    last_page = min(page_count, max_pages) if max_pages else page_count
    log(f"  {publication.title}: {total:,} rows, {page_count} pages ({len(done_pages)} already fetched)")

    for page in range(2, last_page + 1):
        if page in done_pages:
            continue
        save(page, client.fetch_page(pid, page))
        if page % 50 == 0 or page == last_page:
            log(f"    page {page}/{page_count}")

    done, fetched, expected = store.try_complete(pid)
    if done:
        log(f"  complete: {fetched:,} rows")
    elif last_page < page_count:
        log(f"  partial: {fetched:,} of {expected:,} rows")
    else:
        log(f"  row count mismatch: fetched {fetched:,}, expected {expected:,}. Not marked complete")
    return done


def collect(
    client: Client,
    store: Store,
    raw_dir: Path,
    months: int,
    max_pages: int | None = None,
    log=print,
) -> None:
    store.init()
    publications = client.list_publications()
    added = store.upsert_publications(publications)
    log(f"{len(publications)} publications listed in the API spec, {added} newly registered")

    targets = pick_recent(publications, months)
    for i, publication in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}]")
        collect_publication(client, store, raw_dir, publication, max_pages, log)
