# Builds packaging\output\MicroscopeCamera-Setup-<version>.exe:
#   1. signed driver package (make_driver.ps1)  2. standalone app (PyInstaller)  3. installer (Inno Setup)
# Build environment: a venv with numpy, opencv-python-headless, PySide6-Essentials==6.7.3, pyinstaller, pillow.
param(
    [string]$Python = "$env:LOCALAPPDATA\mcb\Scripts\python.exe",
    [string]$Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    [string]$CondaBin = "$env:USERPROFILE\anaconda3\Library\bin"   # venv base is Anaconda: bundle its loose DLL deps
)
$ErrorActionPreference = 'Continue'   # native tools write warnings to stderr; exit codes are checked instead
$here = $PSScriptRoot
$root = Split-Path $here
$build = Join-Path $here 'build'
$version = (Select-String -Path "$root\microscope_viewer.py" -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "== Microscope Camera $version"

Write-Host "== driver package"
& "$here\make_driver.ps1" -Out "$build\driver"

Write-Host "== app icon"
& $Python -I "$here\make_icon.py" "$here\icon.ico"

Write-Host "== PyInstaller"
$extra = @()
foreach ($dll in 'ffi.dll', 'libbz2.dll', 'liblzma.dll') {
    if (Test-Path "$CondaBin\$dll") { $extra += @('--add-binary', "$CondaBin\$dll;.") }
}
$py = (Split-Path $Python) + '\pyinstaller.exe'
& $py --noconfirm --clean --log-level ERROR --windowed --name MicroscopeCamera --icon "$here\icon.ico" `
    --add-data "$here\icon.ico;." @extra --exclude-module tkinter --exclude-module unittest `
    --distpath "$build\dist" --workpath "$build\work" --specpath $build "$root\microscope_viewer.py"
if ($LASTEXITCODE) { throw "PyInstaller failed" }
# Software OpenGL fallback: unused by this widgets-only app.
Remove-Item "$build\dist\MicroscopeCamera\_internal\PySide6\opengl32sw.dll" -ErrorAction SilentlyContinue

Write-Host "== Inno Setup"
& $Iscc /Q "/DAppVersion=$version" "$here\installer.iss"
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-Item "$here\output\MicroscopeCamera-Setup-$version.exe" | Select-Object FullName, @{n='MB'; e={[math]::Round($_.Length / 1MB, 1)}}
