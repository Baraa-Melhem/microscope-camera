# Removes the Microscope Camera driver package and its signing certificate (run elevated by the uninstaller).
param([string]$Thumbprint)
$ErrorActionPreference = 'Continue'
Get-WindowsDriver -Online | Where-Object { $_.OriginalFileName -like '*\microscope_camera.inf' } | ForEach-Object {
    & "$env:SystemRoot\System32\pnputil.exe" /delete-driver $_.Driver /uninstall /force
}
if ($Thumbprint) {
    foreach ($store in 'Root', 'TrustedPublisher') {
        $p = "Cert:\LocalMachine\$store\$Thumbprint"
        if (Test-Path $p) { Remove-Item $p }
    }
}
