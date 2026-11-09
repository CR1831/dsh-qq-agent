"""SQLite storage with a CJK-friendly FTS5 index.

Chinese has no spaces, and SQLite's ``trigram`` tokenizer cannot match 1-2
character words — which rules out queries like "原神".  So the index column
stores *pre-segmented* text (CJK bigrams + latin words, space separated) and is
queried with the ``unicode61`` tokenizer: every bigram becomes a real token, and
2-character words match exactly.

Trade-off: matching is bigram-AND, not strict substring.  For a retrieval layer
whose output is immediately reranked by BM25 + rules that is the right trade.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import Doc
from .retrieval.text import normalize, tokenize

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    rowid        INTEGER PRIMARY KEY,
    doc_id       TEXT UNIQUE NOT NULL,
    platform     TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'message',
    channel_id   TEXT NOT NULL DEFAULT '',
    channel_name TEXT NOT NULL DEFAULT '',
    author_id    TEXT NOT NULL DEFAULT '',
    author_name  TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    ts           REAL NOT NULL DEFAULT 0,
    meta         TEXT NOT NULL DEFAULT '{}',
    seg          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_docs_platform_ts ON docs(platform, ts DESC);
CREATE INDEX IF NOT EXISTS idx_docs_channel ON docs(platform, channel_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_docs_kind ON docs(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    seg,
    content='docs',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, seg) VALUES (new.rowid, new.seg);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, seg) VALUES ('delete', old.rowid, old.seg);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, seg) VALUES ('delete', old.rowid, old.seg);
    INSERT INTO docs_fts(rowid, seg) VALUES (new.rowid, new.seg);
END;

CREATE TABLE IF NOT EXISTS seen (
    doc_id     TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    run_id     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,
    started  REAL NOT NULL,
    finished REAL NOT NULL,
    items    INTEGER NOT NULL DEFAULT 0,
    tokens   INTEGER NOT NULL DEFAULT 0,
    mode     TEXT NOT NULL DEFAULT '',
    summary  TEXT NOT NULL DEFAULT '',
    payload  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_uid TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_log_ts ON dm_log(ts);

CREATE TABLE IF NOT EXISTS dm_seen (
    msg_key  TEXT PRIMARY KEY,
    peer_uid TEXT NOT NULL DEFAULT '',
    seen_at  REAL NOT NULL,
    replied  INTEGER NOT NULL DEFAULT 0
);
"""

_COLUMNS = (
    "doc_id, platform, kind, channel_id, channel_name, author_id, author_name, "
    "title, text, url, ts, meta, seg"
)

_TERM_SPLIT = re.compile(r"[\s,，、;；|]+")


def segment(text: str) -> str:
    """Index form: space-joined bigrams/words, so unicode61 sees real tokens."""
    return " ".join(tokenize(text))


def _fts_query(query: str) -> str:
    """OR the tokens and let BM25 sort it out.

    Strict AND collapses recall as soon as one query word is missing from the
    corpus ("在 原神 …" returns nothing because nobody wrote "在").  Single-char
    CJK tokens are dropped: they carry almost no signal and match everything.
    """
    tokens = [
        t
        for t in tokenize(query)
        if len(t) > 1 or t.isascii()
    ]
    # de-duplicate, keep order
    seen: set[str] = set()
    unique = [t for t in tokens if not (t in seen or seen.add(t))]
    if not unique:
        return ""
    # Quote every token: FTS5 operators (AND/OR/NEAR/*) become plain data.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in unique)


def _like_terms(query: str) -> tuple[str, list[str]]:
    terms = [t for t in _TERM_SPLIT.split(query.strip()) if t]
    if not terms:
        return "", []
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(d.text LIKE ? ESCAPE '\\' OR d.title LIKE ? ESCAPE '\\')")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    return " AND ".join(clauses), params


def highlight(text: str, query: str, width: int = 140) -> str:
    """Cheap excerpt around the first query term (FTS snippet can't be used on
    the segmented column, it would return spaced-out bigrams)."""
    body = " ".join((text or "").split())
    if not body:
        return ""
    terms = [t for t in _TERM_SPLIT.split(query.strip()) if t]
    lowered = body.lower()
    pos = -1
    for term in terms:
        idx = lowered.find(term.lower())
        if idx >= 0:
            pos = idx
            break
    if pos < 0:
        return body[:width] + ("…" if len(body) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(body), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- writes
    def upsert(self, docs: Iterable[Doc]) -> int:
        rows = []
        for doc in docs:
            row = list(doc.to_row())
            row.append(segment(f"{doc.title} {doc.text}"))
            rows.append(tuple(row))
        if not rows:
            return 0
        with self._lock:
            cur = self._conn.executemany(
                f"INSERT OR IGNORE INTO docs ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
            # rowcount, not total_changes: FTS triggers would inflate the latter
            return max(0, cur.rowcount or 0)

    def delete(self, doc_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            self._conn.commit()

    def prune(self, older_than_days: int) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM docs WHERE ts < ? AND ts > 0", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # -------------------------------------------------------------- reads
    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])

    def get(self, doc_id: str) -> Doc | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
        return _row_to_doc(row) if row else None

    def search(
        self,
        query: str,
        *,
        platform: str | None = None,
        kind: str | None = None,
        channel_id: str | None = None,
        author_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
        with_snippet: bool = True,
    ) -> list[tuple[Doc, float, str]]:
        """Return ``(doc, rank, snippet)``; lower rank is better (bm25)."""
        query = (query or "").strip()
        limit = max(1, min(int(limit), 500))
        filters: list[str] = []
        params: list[Any] = []
        if platform:
            filters.append("d.platform = ?")
            params.append(platform)
        if kind:
            filters.append("d.kind = ?")
            params.append(kind)
        if channel_id:
            filters.append("d.channel_id = ?")
            params.append(channel_id)
        if author_id:
            filters.append("d.author_id = ?")
            params.append(author_id)
        if since is not None:
            filters.append("d.ts >= ?")
            params.append(float(since))
        if until is not None:
            filters.append("d.ts <= ?")
            params.append(float(until))

        if not query:
            sql = "SELECT d.*, 0.0 AS rank FROM docs d"
            if filters:
                sql += " WHERE " + " AND ".join(filters)
            sql += " ORDER BY d.ts DESC LIMIT ?"
            with self._lock:
                rows = self._conn.execute(sql, (*params, limit)).fetchall()
            return [(_row_to_doc(r), 0.0, "") for r in rows]

        match = _fts_query(query)
        if match:
            where = ["docs_fts MATCH ?"] + filters
            sql = (
                "SELECT d.*, bm25(docs_fts) AS rank FROM docs_fts "
                "JOIN docs d ON d.rowid = docs_fts.rowid WHERE "
                + " AND ".join(where)
                + " ORDER BY rank ASC, d.ts DESC LIMIT ?"
            )
            try:
                with self._lock:
                    rows = self._conn.execute(sql, (match, *params, limit)).fetchall()
                if rows:
                    return [
                        (_row_to_doc(r), float(r["rank"]), highlight(r["text"], query) if with_snippet else "")
                        for r in rows
                    ]
            except sqlite3.OperationalError:
                pass  # malformed MATCH -> LIKE fallback

        like_sql, like_params = _like_terms(query)
        if not like_sql:
            return []
        sql = (
            "SELECT d.*, 0.0 AS rank FROM docs d WHERE "
            + " AND ".join(["(" + like_sql + ")"] + filters)
            + " ORDER BY d.ts DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (*like_params, *params, limit)).fetchall()
        return [(_row_to_doc(r), 0.0, highlight(r["text"], query) if with_snippet else "") for r in rows]

    def recent(
        self,
        *,
        platform: str | None = None,
        channel_id: str | None = None,
        kind: str | None = None,
        since: float | None = None,
        limit: int = 50,
    ) -> list[Doc]:
        where: list[str] = []
        params: list[Any] = []
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if channel_id:
            where.append("channel_id = ?")
            params.append(channel_id)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if since is not None:
            where.append("ts >= ?")
            params.append(float(since))
        sql = "SELECT * FROM docs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*params, max(1, int(limit)))).fetchall()
        return [_row_to_doc(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            by_platform = {
                row["platform"]: row["n"]
                for row in self._conn.execute("SELECT platform, COUNT(*) AS n FROM docs GROUP BY platform")
            }
            by_kind = {
                row["kind"]: row["n"]
                for row in self._conn.execute("SELECT kind, COUNT(*) AS n FROM docs GROUP BY kind")
            }
            oldest = self._conn.execute("SELECT MIN(ts) FROM docs").fetchone()[0]
            newest = self._conn.execute("SELECT MAX(ts) FROM docs").fetchone()[0]
        return {
            "total": sum(by_platform.values()),
            "by_platform": by_platform,
            "by_kind": by_kind,
            "oldest": oldest,
            "newest": newest,
        }

    def channels(self, platform: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT platform, channel_id, MAX(channel_name) AS channel_name, COUNT(*) AS n, "
            "MAX(ts) AS last_ts FROM docs WHERE kind = 'message'"
        )
        params: Sequence[Any] = ()
        if platform:
            sql += " AND platform = ?"
            params = (platform,)
        sql += " GROUP BY platform, channel_id ORDER BY last_ts DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------- digest state
    def seen_ids(self, doc_ids: Sequence[str]) -> set[str]:
        ids = [str(i) for i in doc_ids]
        if not ids:
            return set()
        out: set[str] = set()
        with self._lock:
            for chunk_start in range(0, len(ids), 400):
                chunk = ids[chunk_start : chunk_start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._conn.execute(
                    f"SELECT doc_id FROM seen WHERE doc_id IN ({placeholders})", chunk
                ).fetchall()
                out.update(row[0] for row in rows)
        return out

    def mark_seen(self, doc_ids: Sequence[str], run_id: str = "") -> int:
        ids = [str(i) for i in doc_ids if i]
        if not ids:
            return 0
        now = time.time()
        with self._lock:
            cur = self._conn.executemany(
                "INSERT OR IGNORE INTO seen (doc_id, first_seen, run_id) VALUES (?,?,?)",
                [(doc_id, now, run_id) for doc_id in ids],
            )
            self._conn.commit()
            return max(0, cur.rowcount or 0)

    def record_run(
        self,
        *,
        run_id: str,
        kind: str,
        started: float,
        finished: float,
        items: int = 0,
        tokens: int = 0,
        mode: str = "",
        summary: str = "",
        payload: str = "{}",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, kind, started, finished, items, tokens, mode, summary, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, kind, float(started), float(finished), int(items), int(tokens), mode, summary, payload),
            )
            self._conn.commit()

    def recent_runs(self, kind: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        sql = (
            "SELECT run_id, kind, started, finished, items, tokens, mode, summary, payload "
            "FROM runs"
        )
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY started DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*params, max(1, int(limit)))).fetchall()
        return [dict(row) for row in rows]

    def meta_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ------------------------------------------------------------ dm state
    def dm_seen(self, msg_key: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM dm_seen WHERE msg_key = ?", (str(msg_key),)).fetchone()
        return row is not None

    def dm_mark(self, msg_key: str, peer_uid: str = "", replied: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO dm_seen (msg_key, peer_uid, seen_at, replied) VALUES (?,?,?,?)",
                (str(msg_key), str(peer_uid), time.time(), int(replied)),
            )
            self._conn.commit()

    def dm_record(self, peer_uid: str, role: str, text: str, ts: float | None = None) -> None:
        """把私信往来记下来，号主问起时能答得上来。"""
        body = " ".join(str(text or "").split())[:400]
        if not body:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO dm_log (peer_uid, role, text, ts) VALUES (?,?,?,?)",
                (str(peer_uid), str(role), body, float(ts or time.time())),
            )
            self._conn.commit()

    def dm_recent(self, peer_uid: str | None = None, *, hours: float = 12, limit: int = 40) -> list[dict[str, Any]]:
        since = time.time() - hours * 3600
        sql = "SELECT peer_uid, role, text, ts FROM dm_log WHERE ts >= ?"
        params: list[Any] = [since]
        if peer_uid:
            sql += " AND peer_uid = ?"
            params.append(str(peer_uid))
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, (*params,)).fetchall()
        return [
            {"peer_uid": r["peer_uid"], "role": r["role"], "text": r["text"], "ts": r["ts"]}
            for r in reversed(rows)
        ]

    def dm_peers_recent(self, *, hours: float = 12) -> list[str]:
        since = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_uid, MAX(ts) AS t FROM dm_log WHERE ts >= ? GROUP BY peer_uid ORDER BY t DESC",
                (since,),
            ).fetchall()
        return [r["peer_uid"] for r in rows]

    def dm_claim(self, msg_key: str, peer_uid: str = "") -> bool:
        """原子占位：多个进程同时看到同一条消息时，只有一个能拿到。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO dm_seen (msg_key, peer_uid, seen_at, replied) VALUES (?,?,?,0)",
                (str(msg_key), str(peer_uid), time.time()),
            )
            self._conn.commit()
            return bool(cur.rowcount)

    def dm_set_replied(self, msg_key: str, replied: int = 1) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE dm_seen SET replied = ?, seen_at = ? WHERE msg_key = ?",
                (int(replied), time.time(), str(msg_key)),
            )
            self._conn.commit()

    def dm_release(self, msg_key: str) -> None:
        """占位失败（发送出错）时撤回，下一轮还能重试。"""
        with self._lock:
            self._conn.execute("DELETE FROM dm_seen WHERE msg_key = ?", (str(msg_key),))
            self._conn.commit()

    def dm_replied_since(self, since: float) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM dm_seen WHERE replied = 1 AND seen_at >= ?", (float(since),)
            ).fetchone()
        return int(row[0])

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _row_to_doc(row: sqlite3.Row) -> Doc:
    try:
        meta = json.loads(row["meta"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    return Doc(
        doc_id=row["doc_id"],
        platform=row["platform"],
        kind=row["kind"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        title=row["title"],
        text=row["text"],
        url=row["url"],
        ts=float(row["ts"] or 0),
        meta=meta,
    )


__all__ = ["Store", "segment", "highlight", "normalize"]
