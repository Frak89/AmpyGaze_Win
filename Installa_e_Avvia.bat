@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto run
py -3.11 -m venv .venv
if errorlevel 1 (
 echo Installa Python 3.11 a 64 bit da https://www.python.org/downloads/windows/ includendo il launcher py.
 pause
 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
 echo Installazione non riuscita. Controlla la connessione e riprova.
 rmdir /s /q .venv
 pause
 exit /b 1
)
:run
.venv\Scripts\python.exe app.py
if errorlevel 1 pause
