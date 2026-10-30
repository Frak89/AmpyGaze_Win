@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
 echo Prima esegui Installa_e_Avvia.bat, poi chiudi l'app.
 pause
 exit /b 1
)
set "AMPYGAZE_DIR=%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $d=$env:AMPYGAZE_DIR; $s=Join-Path ([Environment]::GetFolderPath('Startup')) 'AmpyGaze.lnk'; $w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut($s); $l.TargetPath=Join-Path $d '.venv\Scripts\pythonw.exe'; $l.Arguments='\"'+(Join-Path $d 'app.py')+'\" --autostart'; $l.WorkingDirectory=$d; $l.Save(); $v=Join-Path ([Environment]::GetFolderPath('Startup')) 'AmpyGaze.vbs'; if(Test-Path $v){Remove-Item $v}"
if errorlevel 1 (
 echo Impossibile attivare l'avvio automatico.
 pause
 exit /b 1
)
echo Attivato. AmpyGaze partira al prossimo accesso a Windows.
echo Non spostare questa cartella. Configura prima MP3 e calibrazione.
pause
