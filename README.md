# PDF Serial Finder

A small, fully offline Windows app: pick the folder that holds your certificate PDFs, type a serial number, see the
matching pages, view them and print. It reads the **selectable text** of your PDFs directly with PyMuPDF - no OCR. No network access, no telemetry.

## Using it
1. **First launch:** choose the folder that contains your PDFs. The app remembers it and loads the PDFs automatically
   (a thin loading bar appears in the side panel; search works on what is already loaded).
2. **Search:** type a serial number, press Enter. Every matching page of every PDF is listed with a preview.
   Matching is exact (`A-3837` does not match `A-38370`, `XA-3837B` or `BA-3837`), ignores letter case.
3. **View Page / Full PDF:** opens the built-in viewer (page arrows, zoom, Back). **Print:** opens the Windows print dialog
   (printer, pages/range, copies, printer settings) and sends the job to the chosen printer. The dialog opens on the page you
   are looking at; choose *All* or a page range for more.
4. **Documents:** list of the PDFs in the folder. *View* and *Delete*. Delete asks for confirmation, then **permanently deletes
   the PDF from the folder** and removes it from search.
5. **Change Directory** (side panel) switches to another folder; its PDFs load automatically.

The folder is the source of truth. PDFs added, replaced, renamed or deleted in File Explorer are picked up when the app starts,
when you return to it, and after Delete / Change Directory. The app only adds a hidden `.pdfsf` folder (index + thumbnails)
inside your folder; your PDFs are never moved or modified.

## Setup (Windows)
1. Install Python 3.10-3.12 (64-bit), tick *Add python.exe to PATH*.
2. In this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python main.py
   ```
   (or double-click `run_windows.bat`). The only dependencies are **PySide6** and **PyMuPDF**.

## Tests / packaging
```
pip install -r requirements-dev.txt
python -m pytest -q
build_windows.bat        # -> dist\PDFSerialFinder\PDFSerialFinder.exe
```

## Layout
```
main.py  config/  database/  core/ (library_manager, library_sync, pdf_processor, serial_search, preview_renderer, ...)
workers/ (background sync)   ui/ (main_window, search_page, documents_page, pdf_viewer, printing, theme)   tests/
```

## Known limitations
* **Text PDFs only.** Scanned / image-only PDFs have no selectable text, so they cannot be searched. They are still listed in
  Documents (marked "No selectable text") and can be viewed and printed.
* Developed and tested on Linux. The Windows print dialog, `.exe` packaging and Windows look were **not** run on Windows.
  Printing is tested by driving the same code path into a PDF-file printer.
* Subfolders of the chosen folder are included. Password-protected or damaged PDFs are listed with a note and are not searchable.
* Keep the folder on a local or USB drive (the index uses SQLite WAL mode), and choose a folder you can write to.
