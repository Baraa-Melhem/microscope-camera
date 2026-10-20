# Builds the signed libusb-win32 driver package for the microscope camera.
#
# 64-bit Windows only installs driver packages whose catalog (.cat) is signed by a trusted publisher.
# Like Zadig/libwdi, this signs the catalog with a freshly generated self-signed certificate and then
# destroys its private key, so the certificate the installer trusts can never sign anything else.
param([Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot '..\driver'

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force "$Out\amd64", "$Out\x86" | Out-Null
Copy-Item "$src\microscope_camera.inf" $Out
Copy-Item "$src\bin\amd64\libusb0.sys", "$src\bin\amd64\libusb0.dll" "$Out\amd64\"
Copy-Item "$src\bin\x86\libusb0_x86.dll" "$Out\x86\"

$cat = Join-Path $Out 'microscope_camera.cat'
New-FileCatalog -Path $Out -CatalogFilePath $cat -CatalogVersion 2 | Out-Null

$cert = New-SelfSignedCertificate -Type CodeSigningCert -CertStoreLocation Cert:\CurrentUser\My `
    -Subject 'CN=Microscope Camera Driver (self-signed; private key destroyed)' `
    -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 -KeyExportPolicy NonExportable `
    -NotAfter (Get-Date).AddYears(30)
try {
    $sig = Set-AuthenticodeSignature -FilePath $cat -Certificate $cert -HashAlgorithm SHA256
    if (-not $sig.SignerCertificate) { throw "signing failed: $($sig.StatusMessage)" }
    Export-Certificate -Cert $cert -FilePath (Join-Path (Split-Path $Out) 'microscope_camera.cer') -Type CERT | Out-Null
} finally {
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -DeleteKey
}
Set-Content -Path (Join-Path (Split-Path $Out) 'thumbprint.txt') -Value $cert.Thumbprint -NoNewline -Encoding ascii
Write-Host "Driver package: $Out (certificate $($cert.Thumbprint), private key deleted)"
