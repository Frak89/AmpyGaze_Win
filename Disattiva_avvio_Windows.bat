@echo off
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $d=[Environment]::GetFolderPath('Startup'); foreach($n in @('AmpyGaze.lnk','AmpyGaze.vbs')){$p=Join-Path $d $n; if(Test-Path $p){Remove-Item $p}}"
if errorlevel 1 (
 echo Rimozione non riuscita.
 pause
 exit /b 1
)
echo Avvio automatico disattivato.
pause
