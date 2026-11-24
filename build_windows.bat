@echo off
REM Builds dist\PDFSerialFinder\PDFSerialFinder.exe  (run from the project folder)
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip || goto :err
pip install -r requirements-dev.txt || goto :err
python -m pytest -q || goto :err
pyinstaller pdf_serial_finder.spec --noconfirm || goto :err
echo.
echo Build finished: dist\PDFSerialFinder\PDFSerialFinder.exe
echo Tesseract: install it separately (see README) or copy it to vendor\tesseract before building.
exit /b 0
:err
echo Build failed.
exit /b 1
