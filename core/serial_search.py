"""Exact (token-aware) and approximate serial-number search over the page-level index."""
import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from database import schema as S
from database.search_index import compact, tokenize
from utils.path_utils import resolve_rel


@dataclass
class PageMatch:
    page_id: int
    page_number: int
    matched_text: str
    snippet: str
    similarity: float = 1.0


@dataclass
class DocumentResult:
    document_id: str
    filename: str
    relative_path: str
    abs_path: Path
    page_count: int
    availability: str
    status: str
    exact: bool
    matches: List[PageMatch] = field(default_factory=list)
    file_mtime_ns: int = 0
    file_size: int = 0


def _find_subsequence(hay: List[str], needle: List[str]) -> int:
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def _snippet(text: str, qtokens: List[str]) -> tuple:
    """(matched text as written on the page, short context snippet)."""
    sep = r"[\s\W_]*" if len(qtokens) > 1 else ""
    pat = r"(?<![\w\-/])" + (r"\s+".join(re.escape(t) for t in qtokens)) + r"(?![\w\-/])"
    m = re.search(pat, text, re.IGNORECASE)
    if m:
        s, e = m.span()
        ctx = re.sub(r"\s+", " ", text[max(0, s - 45): e + 45]).strip()
        return text[s:e], ctx
    flat = re.sub(r"\s+", " ", text)
    return " ".join(qtokens).upper(), flat[:90]


class SerialSearcher:
    def __init__(self, conn: sqlite3.Connection, library_root: Path, settings: Optional[dict] = None):
        self.conn = conn
        self.root = Path(library_root)
        self.settings = settings or {}

    # --- helpers -------------------------------------------------------------
    def _doc_result(self, row, exact: bool) -> DocumentResult:
        try:
            ap = resolve_rel(self.root, row["relative_path"])
        except ValueError:
            ap = self.root / "invalid"
        return DocumentResult(row["document_id"], row["filename"], row["relative_path"], ap, row["page_count"],
                              row["availability_status"], row["processing_status"], exact,
                              file_mtime_ns=row["modified_ns"] or 0, file_size=row["file_size"] or 0)

    _SEARCHABLE = ("d.availability_status='available' AND d.processing_status IN (%s)" %
                   ",".join("'%s'" % s for s in S.SEARCHABLE_STATUSES))

    # --- exact ---------------------------------------------------------------
    def search_exact(self, query: str, max_documents: int = 1000) -> List[DocumentResult]:
        qtokens = tokenize(query)
        if not qtokens:
            return []
        fts = '"' + " ".join(qtokens) + '"'  # phrase of whole tokens; tokens contain only [\w-/.]
        rows = self.conn.execute(
            "SELECT p.page_id, p.page_number, p.extracted_text, d.* FROM pages_fts "
            "JOIN pages p ON p.page_id = pages_fts.rowid "
            "JOIN documents d ON d.document_id = p.document_id "
            f"WHERE pages_fts MATCH ? AND {self._SEARCHABLE} "
            "ORDER BY d.filename COLLATE NOCASE, d.document_id, p.page_number", (fts,)).fetchall()
        results: Dict[str, DocumentResult] = {}
        for r in rows:
            text = r["extracted_text"] or ""
            # second, independent check on the page's own tokens (guards against tokenizer quirks)
            if _find_subsequence(tokenize(text), qtokens) < 0:
                continue
            res = results.get(r["document_id"])
            if res is None:
                if len(results) >= max_documents:
                    continue
                res = results[r["document_id"]] = self._doc_result(r, True)
            matched, snip = _snippet(text, qtokens)
            res.matches.append(PageMatch(r["page_id"], r["page_number"], matched, snip))
        return list(results.values())

    # --- approximate ---------------------------------------------------------
    def search_fuzzy(self, query: str, exclude_page_ids: Optional[Set[int]] = None,
                     max_documents: int = 200) -> List[DocumentResult]:
        qc = compact(query)
        if len(qc) < 3:
            return []
        exclude_page_ids = exclude_page_ids or set()
        cutoff = float(self.settings.get("fuzzy_cutoff", 0.8))
        exact_tokens = set(tokenize(query))
        scored: Dict[str, float] = {}
        for (tok,) in self.conn.execute("SELECT DISTINCT token FROM page_tokens"):
            if tok in exact_tokens:
                continue
            tc = compact(tok)
            if not tc or abs(len(tc) - len(qc)) > max(3, len(qc) // 3):
                if not (len(qc) >= 4 and qc in tc):
                    continue
            if len(qc) >= 4 and qc in tc and tc != qc:
                scored[tok] = max(scored.get(tok, 0), 0.9)   # serial embedded in a longer id
                continue
            sm = difflib.SequenceMatcher(None, qc, tc)
            if sm.real_quick_ratio() >= cutoff and sm.quick_ratio() >= cutoff:
                ratio = sm.ratio()
                if ratio >= cutoff:
                    scored[tok] = ratio
        if not scored:
            return []
        toks = list(scored)
        results: Dict[str, DocumentResult] = {}
        for i in range(0, len(toks), 400):
            chunk = toks[i:i + 400]
            q = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT pt.token, p.page_id, p.page_number, p.extracted_text, d.* FROM page_tokens pt "
                "JOIN pages p ON p.page_id = pt.page_id JOIN documents d ON d.document_id = p.document_id "
                f"WHERE pt.token IN ({q}) AND {self._SEARCHABLE} "
                "ORDER BY d.filename COLLATE NOCASE, p.page_number", chunk).fetchall()
            for r in rows:
                if r["page_id"] in exclude_page_ids:
                    continue
                res = results.get(r["document_id"])
                if res is None:
                    if len(results) >= max_documents:
                        continue
                    res = results[r["document_id"]] = self._doc_result(r, False)
                if any(m.page_id == r["page_id"] for m in res.matches):
                    continue
                matched, snip = _snippet(r["extracted_text"] or "", [r["token"]])
                res.matches.append(PageMatch(r["page_id"], r["page_number"], matched, snip, scored[r["token"]]))
        out = list(results.values())
        out.sort(key=lambda d: (-max(m.similarity for m in d.matches), d.filename.lower()))
        return out

    def search(self, query: str, fuzzy: bool = False) -> Dict[str, List[DocumentResult]]:
        exact = self.search_exact(query)
        approx: List[DocumentResult] = []
        if fuzzy:
            seen = {m.page_id for d in exact for m in d.matches}
            approx = self.search_fuzzy(query, seen)
        return {"exact": exact, "approximate": approx}
