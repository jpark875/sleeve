# Build dist\Sleeve\ and the installer. Optional signing:
#   $env:SIGN_CERT_THUMBPRINT = "<thumbprint of a code-signing cert in the user store>"
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

python -m pip install -e ".[package]"
python packaging\fetch_ffmpeg.py windows-x86_64
python packaging\make_icons.py
pyinstaller packaging\sleeve.spec --noconfirm --clean

$version = python -c "import sleeve; print(sleeve.__version__)"
$sign = $env:SIGN_CERT_THUMBPRINT
if ($sign) {
  signtool sign /sha1 $sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    dist\Sleeve\Sleeve.exe dist\Sleeve\_internal\ffmpeg\ffmpeg.exe
}

& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" packaging\windows\sleeve.iss
if ($sign) {
  signtool sign /sha1 $sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
    "dist\Sleeve-$version-setup.exe"
}
Write-Host "Built dist\Sleeve-$version-setup.exe"
