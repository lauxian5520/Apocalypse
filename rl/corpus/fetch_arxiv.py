"""Pull arXiv records into `Doc`s, from either of two sources.

**The API** (`fetch_api`) needs no credentials and is what runs out of the box.
It is paged by *submission-date window* rather than by a single deep `start`
offset: arXiv's paging degrades past a few thousand results, and a windowed
scan also makes the build resumable and its coverage auditable — each window
either completed or it did not.

**A local snapshot** (`read_snapshot`) reads the arXiv metadata dump (the one
published as a single JSONL file, one object per paper). Prefer it when you
have it: it is versioned, citable, carries full version history, and turns a
network-bound build into a disk-bound one. The API path exists so the project
is runnable by someone who has not downloaded it.

Both paths converge on the same `Doc`, so everything downstream is unaware of
which one produced the corpus. The manifest records the answer.
"""
import json
import logging
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Iterator

from harness.corpus.schema import Doc

logger = logging.getLogger(__name__)

API_URL = "http://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# arXiv asks for one request every three seconds. Measured throughput at
# `PAGE_SIZE = 500` is ~4.3s of server time per page, so the sleep roughly
# doubles the build time and is still only minutes for a 50k corpus. Do not
# lower it; a build that gets the caller rate-limited is not faster.
POLITE_SECONDS = 3.0
PAGE_SIZE = 500
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 10.0

# arXiv returns an empty <entry> list for a window with no results, but it also
# returns one transiently under load. Two consecutive empty pages are treated as
# the end of a window; one is retried.
EMPTY_PAGES_BEFORE_DONE = 2


def _category_clause(categories: tuple[str, ...]) -> str:
    return "+OR+".join(f"cat:{c}" for c in categories)


def _window_clause(start: date, end: date) -> str:
    return f"submittedDate:[{start:%Y%m%d}0000+TO+{end:%Y%m%d}2359]"


def month_windows(since: date, until: date) -> Iterator[tuple[date, date]]:
    """Half-open month boundaries covering [since, until], oldest first."""
    cur = date(since.year, since.month, 1)
    while cur <= until:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        yield max(cur, since), min(date.fromordinal(nxt.toordinal() - 1), until)
        cur = nxt


def _get(url: str) -> bytes:
    """One API call, retried on transient failure.

    A 4xx other than 429 is not retried: a malformed query will fail the same
    way forever, and burning four backoffs to rediscover that hides the real
    error behind a timeout.
    """
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
        logger.warning("arxiv request failed (%s); retrying in %.0fs", last, wait)
        time.sleep(wait)
    raise RuntimeError(f"arXiv API unreachable after {MAX_RETRIES} attempts: {last}")


def _parse_entry(entry: ET.Element) -> Doc | None:
    """One Atom <entry> into a Doc, or None if it is unusable.

    A record with no abstract cannot be retrieved by a text query, so it would
    sit in the corpus as an unreachable distractor. Dropping it here keeps the
    index and the document store in agreement.
    """
    raw_id = (entry.findtext("a:id", default="", namespaces=ATOM) or "").strip()
    if not raw_id:
        return None
    tail = raw_id.rsplit("/", 1)[-1]                 # "2609.11917v1"
    arxiv_id, _, version = tail.partition("v")

    title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM) or "").split())
    abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ATOM) or "").split())
    if not title or not abstract:
        return None

    authors = [
        " ".join(n.split())
        for n in (a.findtext("a:name", default="", namespaces=ATOM) for a in entry.findall("a:author", ATOM))
        if n and n.strip()
    ]
    categories = [c.get("term", "") for c in entry.findall("a:category", ATOM) if c.get("term")]
    primary = entry.find("arxiv:primary_category", ATOM)
    if primary is not None and primary.get("term"):
        # Put the primary category first so `Doc.primary_category` is truthful;
        # the Atom feed lists categories in no guaranteed order.
        term = primary.get("term", "")
        categories = [term] + [c for c in categories if c != term]

    published = (entry.findtext("a:published", default="", namespaces=ATOM) or "")[:10]
    updated = (entry.findtext("a:updated", default="", namespaces=ATOM) or "")[:10]

    return Doc(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=categories,
        submitted=published,
        updated=updated,
        version=int(version) if version.isdigit() else 1,
    )


def fetch_window(categories: tuple[str, ...], start: date, end: date, limit: int) -> list[Doc]:
    """Every matching record submitted in [start, end], up to `limit`."""
    out: list[Doc] = []
    empty_streak = 0
    offset = 0
    while len(out) < limit:
        url = (
            f"{API_URL}?search_query=%28{_category_clause(categories)}%29"
            f"+AND+{_window_clause(start, end)}"
            f"&start={offset}&max_results={min(PAGE_SIZE, limit - len(out))}"
            f"&sortBy=submittedDate&sortOrder=ascending"
        )
        root = ET.fromstring(_get(url))
        entries = root.findall("a:entry", ATOM)
        time.sleep(POLITE_SECONDS)

        if not entries:
            empty_streak += 1
            if empty_streak >= EMPTY_PAGES_BEFORE_DONE:
                break
            continue
        empty_streak = 0

        for entry in entries:
            doc = _parse_entry(entry)
            if doc is not None:
                out.append(doc)
        if len(entries) < PAGE_SIZE:
            break
        offset += len(entries)
    return out


def fetch_api(
    categories: tuple[str, ...],
    since: date,
    until: date,
    target: int,
    per_window: int = 4000,
) -> Iterator[Doc]:
    """Stream records across month windows until `target` unique ids are seen.

    Yields as it goes so the caller can write incrementally: a build
    interrupted at 40k documents should not have to start over.
    """
    seen: set[str] = set()
    for w_start, w_end in month_windows(since, until):
        if len(seen) >= target:
            return
        docs = fetch_window(categories, w_start, w_end, min(per_window, target - len(seen)))
        fresh = 0
        for doc in docs:
            if doc.arxiv_id in seen:
                continue
            seen.add(doc.arxiv_id)
            fresh += 1
            yield doc
        logger.info("%s..%s: +%d docs (total %d)", w_start, w_end, fresh, len(seen))


def read_snapshot(
    path: str,
    categories: tuple[str, ...],
    since: date,
    until: date,
    target: int,
) -> Iterator[Doc]:
    """Stream `Doc`s out of a local arXiv metadata dump (JSONL).

    The dump's shape differs from the API's: `categories` is a space-separated
    string, authors arrive as `authors_parsed` (surname, given, suffix) triples,
    and dates live in a `versions` list rather than on the record.
    """
    wanted = set(categories)
    kept = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if kept >= target:
                return
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            cats = (raw.get("categories") or "").split()
            if not wanted.intersection(cats):
                continue

            versions = raw.get("versions") or []
            submitted = _snapshot_date(versions, first=True)
            if not submitted or not (since.isoformat() <= submitted <= until.isoformat()):
                continue

            title = " ".join((raw.get("title") or "").split())
            abstract = " ".join((raw.get("abstract") or "").split())
            if not title or not abstract:
                continue

            yield Doc(
                arxiv_id=str(raw.get("id", "")).strip(),
                title=title,
                abstract=abstract,
                authors=_snapshot_authors(raw),
                categories=cats,
                submitted=submitted,
                updated=_snapshot_date(versions, first=False) or submitted,
                version=len(versions) or 1,
            )
            kept += 1


def _snapshot_authors(raw: dict) -> list[str]:
    parsed = raw.get("authors_parsed") or []
    if parsed:
        names = []
        for part in parsed:
            surname = (part[0] if len(part) > 0 else "").strip()
            given = (part[1] if len(part) > 1 else "").strip()
            names.append(" ".join(x for x in (given, surname) if x))
        return [n for n in names if n]
    # Fall back to the free-text field, which is comma-and-"and" separated.
    return [a.strip() for a in (raw.get("authors") or "").replace(" and ", ", ").split(",") if a.strip()]


def _snapshot_date(versions: list, first: bool) -> str:
    """ISO date of the first (or last) version, or "" when unparseable."""
    if not versions:
        return ""
    entry = versions[0] if first else versions[-1]
    stamp = (entry or {}).get("created", "")
    if not stamp:
        return ""
    try:
        # e.g. "Mon, 2 Apr 2007 19:18:42 GMT"
        return datetime.strptime(stamp[:-4].strip(), "%a, %d %b %Y %H:%M:%S").date().isoformat()
    except ValueError:
        return ""
