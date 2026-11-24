"""Token-aware indexing. Serial numbers are normalised and stored as whole tokens."""
import re
import sqlite3
import unicodedata
from typing import List


_DASHES = re.compile("[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")
_NON_TOKEN = re.compile(r"[^\w\-/.]+", re.UNICODE)
_DOT_NOT_BETWEEN_DIGITS = re.compile(r"(?<!\d)\.|\.(?!\d)")
_EDGE = "-_/."


def tokenize(text: str) -> List[str]:
    """Case-fold, keep letters/digits and the separators - _ / (and . between digits)."""
    if not text:
        return []
    t = unicodedata.normalize("NFKC", text)
    t = _DASHES.sub("-", t).casefold()
    t = _NON_TOKEN.sub(" ", t)
    t = _DOT_NOT_BETWEEN_DIGITS.sub(" ", t)
    out = []
    for tok in t.split():
        tok = tok.strip(_EDGE)
        if tok:
            out.append(tok)
    return out


def compact(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKC", text).casefold())


def _is_id_like(tok: str) -> bool:
    return 3 <= len(tok) <= 48 and any(c.isdigit() for c in tok)


def index_page(conn: sqlite3.Connection, page_id: int, text: str) -> None:
    tokens = tokenize(text)
    conn.execute("INSERT INTO pages_fts(rowid, search_text) VALUES(?,?)", (page_id, " ".join(tokens)))
    ids = {t for t in tokens if _is_id_like(t)}
    if ids:
        conn.executemany("INSERT INTO page_tokens(token,page_id) VALUES(?,?)", [(t, page_id) for t in ids])


def remove_document_pages(conn: sqlite3.Connection, document_id: str) -> None:
    """Remove page rows + every index entry of one document (call inside a transaction)."""
    ids = [r[0] for r in conn.execute("SELECT page_id FROM pages WHERE document_id=?", (document_id,))]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM pages_fts WHERE rowid IN ({q})", chunk)
        conn.execute(f"DELETE FROM page_tokens WHERE page_id IN ({q})", chunk)
    conn.execute("DELETE FROM pages WHERE document_id=?", (document_id,))
