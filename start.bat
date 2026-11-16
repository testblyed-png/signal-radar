@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Signal-Radar

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo Python wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo WICHTIG: Beim Installieren "Add python.exe to PATH" anhaken.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Erster Start: richte alles ein, das dauert 1-3 Minuten ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Fehler beim Anlegen der Python-Umgebung.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Installation der Pakete fehlgeschlagen. Internetverbindung pruefen.
  pause
  exit /b 1
)

echo.
echo Signal-Radar startet im Browser. Dieses Fenster offen lassen - schliessen beendet die App.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
