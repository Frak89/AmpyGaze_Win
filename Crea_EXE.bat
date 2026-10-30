@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Esegui prima Installa_e_Avvia.bat.
 pause
 exit /b 1
)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name AmpyGaze --collect-all mediapipe app.py
if errorlevel 1 (
 pause
 exit /b 1
)
echo EXE creato in dist\AmpyGaze\AmpyGaze.exe. Conserva l'intera cartella.
pause
