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
# LATEST_RELEASE_<major> peut désigner une version publiée seulement pour
# Linux. Essayer d'abord la version exacte du runtime, puis la dernière version
# de la même branche si l'archive Windows de la version exacte est absente.
$versions = @($version)
try {
    # Microsoft peut retourner ce texte en UTF-16 avec BOM ; RestMethod le
    # décode parfois comme UTF-8. StreamReader détecte le BOM correctement.
    $latest = Join-Path $env:RUNNER_TEMP 'webview2-driver-version.txt'
    Invoke-WebRequest "https://msedgedriver.microsoft.com/LATEST_RELEASE_$major" -OutFile $latest
    $reader = [System.IO.StreamReader]::new($latest, [System.Text.Encoding]::UTF8, $true)
    try { $candidate = $reader.ReadToEnd().Trim() } finally { $reader.Dispose() }
    if ($candidate -match "^$major\.\d+\.\d+\.\d+$") {
        $versions += $candidate
    } else {
        Write-Host "Version du WebDriver ignorée car invalide : $candidate"
    }
} catch {
    Write-Host "Recherche de la dernière version impossible : $($_.Exception.Message)"
}
$directory = Join-Path $env:RUNNER_TEMP 'webview2-driver'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$archive = Join-Path $directory 'driver.zip'
$driverVersion = $null
foreach ($candidate in ($versions | Select-Object -Unique)) {
    try {
        Write-Host "Téléchargement du WebDriver Windows : $candidate"
        Invoke-WebRequest "https://msedgedriver.microsoft.com/$candidate/edgedriver_win64.zip" -OutFile $archive
        $driverVersion = $candidate
        break
    } catch {
        Write-Host "Archive Windows $candidate indisponible : $($_.Exception.Message)"
        Remove-Item -Force -ErrorAction SilentlyContinue $archive
    }
}
if (-not $driverVersion) { throw "Aucun WebDriver Windows disponible pour WebView2 $version" }
Write-Host "WebDriver correspondant : $driverVersion"
Expand-Archive -Path $archive -DestinationPath $directory -Force
$driver = Join-Path $directory 'msedgedriver.exe'
if (-not (Test-Path $driver)) { throw 'WebDriver téléchargé introuvable' }
& $driver --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"HOOKY_WEBDRIVER_BINARY=$driver" >> $env:GITHUB_ENV
