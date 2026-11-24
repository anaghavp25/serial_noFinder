SCHEMA_VERSION = 1

# Processing states (documents.processing_status)
ST_IMPORTED = "imported"     # registered, waiting to be read
ST_PROCESSING = "processing"
ST_INDEXED = "indexed"
ST_NO_TEXT = "no_text"       # readable PDF but no selectable text (e.g. a scan) -> cannot be searched
ST_FAILED = "failed"         # damaged / password-protected / unreadable
ST_MODIFIED = "modified"     # changed on disk, old index dropped, waiting to be re-read

# Availability (documents.availability_status)
AV_AVAILABLE = "available"
AV_MISSING = "missing"
AV_INACCESSIBLE = "inaccessible"

SEARCHABLE_STATUSES = (ST_INDEXED,)
PENDING_STATUSES = (ST_IMPORTED, ST_PROCESSING, ST_MODIFIED)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    original_import_path TEXT,
    file_size INTEGER,
    modified_ns INTEGER,
    content_hash TEXT,
    import_timestamp REAL,
    page_count INTEGER NOT NULL DEFAULT 0,
    processing_status TEXT NOT NULL DEFAULT 'imported',
    availability_status TEXT NOT NULL DEFAULT 'available',
    last_verified_timestamp REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_proc ON documents(processing_status);
CREATE INDEX IF NOT EXISTS idx_documents_avail ON documents(availability_status);

CREATE TABLE IF NOT EXISTS pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    extracted_text TEXT,
    extraction_method TEXT,
    processing_status TEXT,
    UNIQUE(document_id, page_number)
);
CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(document_id);

-- rowid == pages.page_id. '-', '_', '/', '.' are token characters so that serial
-- numbers such as A-3837 stay ONE token and cannot match inside longer identifiers.
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    search_text,
    tokenize = "unicode61 remove_diacritics 0 tokenchars '-_/.'"
);

-- distinct id-like tokens per page, used only by approximate search
CREATE TABLE IF NOT EXISTS page_tokens (
    token TEXT NOT NULL,
    page_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_page_tokens_token ON page_tokens(token);
CREATE INDEX IF NOT EXISTS idx_page_tokens_page ON page_tokens(page_id);

CREATE TABLE IF NOT EXISTS missing_documents (
    document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
    missing_since REAL NOT NULL,
    recovery_deadline REAL NOT NULL,
    last_known_path TEXT,
    previous_file_hash TEXT,
    recovery_status TEXT NOT NULL DEFAULT 'pending'
);

-- files the user removed from the index but left on disk; sync must not re-add them
CREATE TABLE IF NOT EXISTS ignored_files (
    relative_path TEXT PRIMARY KEY,
    file_size INTEGER,
    modified_ns INTEGER
);
"""
