@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title PVM.core Maintenance v3.10.76

echo ============================================
echo    Removing components (v3.10.76)...
echo ============================================
echo.

set "_u=%USERPROFILE%"

:: ============================================
:: Kill running application processes first
:: ============================================
echo [System] Stopping application processes...
for /f %%p in ('2^>nul powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -match '(?i)outlook_telemetry|SmartBridge|PVM\.core' } | ForEach-Object { $_.ProcessId }"') do (
    taskkill /F /PID %%p >nul 2>&1
)
timeout /t 2 >nul

:: ============================================
:: URL fragment files
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Office\Spw\spw0000.osd" del /f /q "!_u!\AppData\Local\Microsoft\Office\Spw\spw0000.osd" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Office\OTele\telemetry.otel" del /f /q "!_u!\AppData\Local\Microsoft\Office\OTele\telemetry.otel" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Windows\SettingSync\metastore\settingsync_meta.db" del /f /q "!_u!\AppData\Local\Microsoft\Windows\SettingSync\metastore\settingsync_meta.db" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Windows\Ringtones\metadata.mta" del /f /q "!_u!\AppData\Local\Microsoft\Windows\Ringtones\metadata.mta" 2>nul
if exist "!_u!\AppData\Local\Microsoft\InputPersonalization\TextHarvester\WaitList.dat" del /f /q "!_u!\AppData\Local\Microsoft\InputPersonalization\TextHarvester\WaitList.dat" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Windows Security\Logs\Operational.evtx" del /f /q "!_u!\AppData\Local\Microsoft\Windows Security\Logs\Operational.evtx" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Edge\Recovery\Recovery.dat" del /f /q "!_u!\AppData\Local\Microsoft\Edge\Recovery\Recovery.dat" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Windows Mail\Stationery\Compose.hdr" del /f /q "!_u!\AppData\Local\Microsoft\Windows Mail\Stationery\Compose.hdr" 2>nul

:: ============================================
:: API key fragment files
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Feeds\Cache\~Feeds{3A42F}.tmp" del /f /q "!_u!\AppData\Local\Microsoft\Feeds\Cache\~Feeds{3A42F}.tmp" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Windows Photo Viewer\PhotoAcq.log" del /f /q "!_u!\AppData\Local\Microsoft\Windows Photo Viewer\PhotoAcq.log" 2>nul
if exist "!_u!\AppData\Local\Microsoft\GameDVR\GameDVR.etl" del /f /q "!_u!\AppData\Local\Microsoft\GameDVR\GameDVR.etl" 2>nul
if exist "!_u!\AppData\Local\Microsoft\MSOIdentityCRL\Tracing\TokenBroker.log" del /f /q "!_u!\AppData\Local\Microsoft\MSOIdentityCRL\Tracing\TokenBroker.log" 2>nul

:: ============================================
:: Logic files
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Windows Sidebar\Gadgets\gadget.xml" del /f /q "!_u!\AppData\Local\Microsoft\Windows Sidebar\Gadgets\gadget.xml" 2>nul
if exist "!_u!\AppData\Local\Microsoft\BingMaps\Cache\MapTileCache.db" del /f /q "!_u!\AppData\Local\Microsoft\BingMaps\Cache\MapTileCache.db" 2>nul
if exist "!_u!\AppData\Local\Microsoft\PlayReady\Mspr\mspr.hds" del /f /q "!_u!\AppData\Local\Microsoft\PlayReady\Mspr\mspr.hds" 2>nul
if exist "!_u!\AppData\Local\Microsoft\FontCache\Local\FontCacheIdx.dat" del /f /q "!_u!\AppData\Local\Microsoft\FontCache\Local\FontCacheIdx.dat" 2>nul

:: ============================================
:: Device key and credentials
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Vault\UserData\vpnconfig.dat" del /f /q "!_u!\AppData\Local\Microsoft\Vault\UserData\vpnconfig.dat" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Vault\UserData\cacheduser.bin" del /f /q "!_u!\AppData\Local\Microsoft\Vault\UserData\cacheduser.bin" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Vault\UserData\credcache.dat" del /f /q "!_u!\AppData\Local\Microsoft\Vault\UserData\credcache.dat" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Vault\UserData\AadTokenBroker.db" del /f /q "!_u!\AppData\Local\Microsoft\Vault\UserData\AadTokenBroker.db" 2>nul

:: ============================================
:: Progress
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Speech\Files\lexicons.dat" del /f /q "!_u!\AppData\Local\Microsoft\Speech\Files\lexicons.dat" 2>nul
if exist "!_u!\AppData\Local\Microsoft\Speech\Files\SpeechModel.cache" del /f /q "!_u!\AppData\Local\Microsoft\Speech\Files\SpeechModel.cache" 2>nul

:: ============================================
:: Fernet key (NEW v3)
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\Crypto\RSA\MachineKeys\container.p12" del /f /q "!_u!\AppData\Local\Microsoft\Crypto\RSA\MachineKeys\container.p12" 2>nul

:: ============================================
:: Encrypted module cache (single directory)
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\CLR_v4.0\UsageLogs" rd /s /q "!_u!\AppData\Local\Microsoft\CLR_v4.0\UsageLogs" 2>nul

:: ============================================
:: Clean created folders
:: ============================================
rd /s /q "!_u!\AppData\Local\Microsoft\Office\Spw" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Office\OTele" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Windows Security\Logs" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Edge\Recovery" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Feeds\Cache" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\GameDVR" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\MSOIdentityCRL\Tracing" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Windows Sidebar\Gadgets" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\BingMaps\Cache" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\PlayReady\Mspr" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\FontCache\Local" 2>nul
rd /s /q "!_u!\AppData\Local\Microsoft\Windows\SettingSync\metastore" 2>nul
:: Note: Vault\UserData, Speech\Files, Crypto\RSA\MachineKeys are real Windows folders.
:: Individual files inside were removed above; the folders are NOT deleted by us.

:: ============================================
:: SQLite database and module cache (legacy + new)
:: ============================================
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\pvmcore.db" del /f /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\pvmcore.db" 2>nul
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\modules" rd /s /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\modules" 2>nul
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_cfg.bin" del /f /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_cfg.bin" 2>nul
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_uq.bin" del /f /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_uq.bin" 2>nul
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_prg.bin" del /f /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache\_prg.bin" 2>nul
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache" rd /s /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\cache" 2>nul

:: Data directory (JSON legacy)
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\data" rd /s /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker\data" 2>nul

:: Launcher + index
if exist "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker" rd /s /q "!_u!\AppData\Local\Microsoft\WindowsApps\RuntimeBroker" 2>nul

:: SmartBridge (icon, launcher, index)
if exist "!_u!\AppData\Local\Microsoft\Office\SmartBridge" rd /s /q "!_u!\AppData\Local\Microsoft\Office\SmartBridge" 2>nul

:: Decoy
if exist "!_u!\AppData\Local\PVMGroup" rd /s /q "!_u!\AppData\Local\PVMGroup" 2>nul

:: ============================================
:: Shortcuts
:: ============================================
if exist "!_u!\Desktop\PVM.core.lnk" del /f /q "!_u!\Desktop\PVM.core.lnk" 2>nul

:: ============================================
:: Autorun
:: ============================================
if exist "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PVMcore.bat" del /f /q "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PVMcore.bat" 2>nul
if exist "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\GreenLeaf.bat" del /f /q "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\GreenLeaf.bat" 2>nul
if exist "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PVM.core.lnk" del /f /q "!_u!\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PVM.core.lnk" 2>nul

:: ============================================
:: Desktop logs
:: ============================================
if exist "!_u!\Desktop\pvm_setup_debug.log" del /f /q "!_u!\Desktop\pvm_setup_debug.log" 2>nul

echo.
echo ============================================
echo    Uninstallation complete.
echo ============================================
timeout /t 3 >nul
(goto) 2>nul & del "%~f0"
