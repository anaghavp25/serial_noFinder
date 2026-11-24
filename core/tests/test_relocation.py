import shutil
from pathlib import Path

import pytest

import core.library_relocator as R
from core.importer import import_documents
from core.library_manager import Library, inspect_folder
from core.library_sync import sync_library
from core.serial_search import SerialSearcher
from tests.conftest import make_pdf


def populated(lib, make, no_ocr):
    import_documents(lib, [make("a.pdf", ["Serial R-1001"]), make("b.pdf", ["x", "Serial R-2002"])], ocr=no_ocr)


def hits(lib, q):
    c = lib.connect()
    try:
        return len(SerialSearcher(c, lib.root).search_exact(q))
    finally:
        c.close()


def test_move_library_copy_verify_switch(lib, make, no_ocr, tmp_path):
    populated(lib, make, no_ocr)
    target = tmp_path / "new" / "place"
    new = R.move_library(lib, target)
    assert inspect_folder(target).state == "library"
    assert hits(new, "R-2002") == 1 and hits(new, "R-1001") == 1
    assert new.library_id == lib.library_id                       # identity preserved
    # old library still fully intact (no silent cleanup)
    assert hits(lib, "R-1001") == 1 and (lib.docs_dir / "a.pdf").exists()
    rep = sync_library(new, no_ocr)
    assert rep.summary() == "No changes" and rep.processed == 0   # statuses/mtimes preserved -> nothing reprocessed
    removed = R.cleanup_old_library(lib.root)
    assert not lib.root.exists() and "documents" in removed


def test_move_rejects_bad_targets(lib, make, no_ocr, tmp_path):
    populated(lib, make, no_ocr)
    assert not R.validate_target(lib, lib.root / "inside", "move").ok
    assert not R.validate_target(lib, lib.root, "move").ok
    foreign = tmp_path / "foreign"; foreign.mkdir(); (foreign / "x.txt").write_text("hi")
    assert not R.validate_target(lib, foreign, "move").ok
    other = Library.create(tmp_path / "other")
    assert not R.validate_target(lib, other.root, "move").ok
    assert R.validate_target(lib, other.root, "switch").ok


def test_failed_relocation_rolls_back_and_original_stays_usable(lib, make, no_ocr, tmp_path, monkeypatch):
    populated(lib, make, no_ocr)
    target = tmp_path / "dest"
    calls = {"n": 0}
    real = R.sha256_file
    def flaky(path, cancel=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk exploded")
        return real(path, cancel)
    monkeypatch.setattr(R, "sha256_file", flaky)
    with pytest.raises(R.RelocationError):
        R.move_library(lib, target)
    assert not target.exists()                                    # fully rolled back
    assert inspect_folder(lib.root).state == "library" and hits(lib, "R-1001") == 1


def test_cancelled_relocation_rolls_back(lib, make, no_ocr, tmp_path):
    import threading
    from core.progress import Cancelled
    populated(lib, make, no_ocr)
    ev = threading.Event(); ev.set()
    with pytest.raises(Cancelled):
        R.move_library(lib, tmp_path / "dest", cancel=ev)
    assert not (tmp_path / "dest").exists()


def test_switch_library_keeps_both_separate(lib, make, no_ocr, tmp_path):
    populated(lib, make, no_ocr)
    other_root = tmp_path / "second"
    other = R.switch_library(other_root)                          # new/empty -> fresh library, no merging
    assert hits(other, "R-1001") == 0
    import_documents(other, [make_pdf(tmp_path / "z.pdf", ["Serial S-3003"])], ocr=no_ocr)
    back = R.switch_library(lib.root)                             # switching back opens the original
    assert hits(back, "R-1001") == 1 and hits(back, "S-3003") == 0
    assert hits(R.switch_library(other_root), "S-3003") == 1


def test_switch_into_nonempty_foreign_folder_leaves_files_alone(tmp_path, no_ocr):
    f = tmp_path / "docs"; f.mkdir()
    make_pdf(f / "unrelated.pdf", ["Serial U-1"])
    lib = R.switch_library(f)
    rep = sync_library(lib, no_ocr)
    assert rep.added == []                                        # unrelated PDFs are not treated as library docs
    assert (f / "unrelated.pdf").exists()
