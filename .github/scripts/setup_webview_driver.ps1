# Le WebDriver doit correspondre au runtime WebView2, pas au navigateur Edge.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$keys = @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$id",
          "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id",
          "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id")
$version = $null
foreach ($key in $keys) {
    $candidate = (Get-ItemProperty -Path $key -Name pv -ErrorAction SilentlyContinue).pv
    if ($candidate -match '^\d+\.\d+\.\d+\.\d+$' -and $candidate -ne '0.0.0.0') {
        $version = $candidate
        break
    }
}
if (-not $version) { throw 'Version du runtime WebView2 introuvable' }
$major = $version.Split('.')[0]
Write-Host "Runtime WebView2 : $version"
try {
    $driverVersion = (Invoke-RestMethod "https://msedgedriver.microsoft.com/LATEST_RELEASE_$major").Trim()
} catch {
    Write-Host "Recherche de la dernière version impossible : $($_.Exception.Message). Essai de la version exacte."
    $driverVersion = $version
}
if ($driverVersion -notmatch "^$major\.\d+\.\d+\.\d+$") { throw "Version du WebDriver invalide : $driverVersion" }
Write-Host "WebDriver correspondant : $driverVersion"
$directory = Join-Path $env:RUNNER_TEMP 'webview2-driver'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$archive = Join-Path $directory 'driver.zip'
Invoke-WebRequest "https://msedgedriver.microsoft.com/$driverVersion/edgedriver_win64.zip" -OutFile $archive
Expand-Archive -Path $archive -DestinationPath $directory -Force
$driver = Join-Path $directory 'msedgedriver.exe'
if (-not (Test-Path $driver)) { throw 'WebDriver téléchargé introuvable' }
& $driver --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"HOOKY_WEBDRIVER_BINARY=$driver" >> $env:GITHUB_ENV
