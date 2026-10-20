@echo off
rem Installs (or repairs) the Microscope Camera USB driver: trusts the package's signing certificate
rem and adds the libusb-win32 driver for USB\VID_1578&PID_800C. Pass /quiet when run by the installer.
setlocal
set "QUIET=%~1"
net session >nul 2>&1
if errorlevel 1 (
    rem Not elevated: relaunch as administrator (shows the Windows UAC prompt).
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
set "LOG=%~dp0driver_install.log"
echo [%date% %time%] installing driver > "%LOG%"
certutil -f -addstore Root microscope_camera.cer >> "%LOG%" 2>&1
certutil -f -addstore TrustedPublisher microscope_camera.cer >> "%LOG%" 2>&1
"%SystemRoot%\System32\pnputil.exe" /add-driver "%~dp0package\microscope_camera.inf" /install >> "%LOG%" 2>&1
set RC=%errorlevel%
echo pnputil exit code %RC% >> "%LOG%"
rem 0 = installed, 259 = already up to date / no device present yet, 3010 = installed, restart needed
if "%RC%"=="259" set RC=0
if "%RC%"=="3010" set RC=0
if /i not "%QUIET%"=="/quiet" (
    if "%RC%"=="0" (echo. & echo Camera driver installed. Unplug and re-plug the camera if it was connected.) else (echo. & echo Driver installation failed ^(code %RC%^). Details: "%LOG%")
    pause
)
exit /b %RC%
