@echo off
rem ============================================================
rem  Knowledge Base Helper - launcher (double-click to start)
rem  Starts the local service, waits until it is ready,
rem  then opens the browser window automatically.
rem ============================================================
cd /d "%~dp0"

rem ---- kill stale helper instances (old versions may linger) ----
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr "LISTENING" ^| findstr ":876"') do taskkill /F /PID %%p >nul 2>&1
timeout /t 1 /nobreak >nul

rem ---- locate pythonw (no console window) ----
set "PYW="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" set "PYW=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if not defined PYW if exist "D:\python\pythonw.exe" set "PYW=D:\python\pythonw.exe"
if not defined PYW set "PYW=pythonw.exe"

rem ---- start the service ----
start "" "%PYW%" "%~dp0gui_server.py"

rem ---- wait until the service is ready (max ~15 seconds) ----
set "N=0"
:waitloop
curl.exe -s -m 1 http://127.0.0.1:8765/api/status >nul 2>&1
if not errorlevel 1 goto ready
timeout /t 1 /nobreak >nul
set /a N+=1
if %N% lss 15 goto waitloop

rem ---- fallback: read the real URL written by the service ----
if exist "%~dp0gui_url.txt" (
  set /p KBURL=<"%~dp0gui_url.txt"
  if defined KBURL (
    start "" "%KBURL%"
    exit /b
  )
)
goto fail

:ready
start "" "http://127.0.0.1:8765/"
exit /b

:fail
echo [ERROR] The service did not start within 15 seconds.
echo Please check the file: gui_startup.log
echo.
if exist "gui_startup.log" type "gui_startup.log"
echo.
pause
exit /b
