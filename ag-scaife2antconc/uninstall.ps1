# AG-Scaife2AntConc: removal. Started by uninstall.bat (double click).
# Removes the program folder, the shortcuts and the settings. The Diogenes tables in
# %LOCALAPPDATA%\AG-Diogenes-SpaCy-txt2xml stay: AG-Diogenes-SpaCy-txt2xml uses them too.
$app = Join-Path $env:LOCALAPPDATA 'AG-Scaife2AntConc'
$settings = Join-Path $env:APPDATA 'AG-Scaife2AntConc'
$links = @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'AG-Scaife2AntConc.lnk'),
           (Join-Path ([Environment]::GetFolderPath('Programs')) 'AG-Scaife2AntConc.lnk'))

Write-Host 'Θα αφαιρεθούν:'
foreach ($p in @($app, $settings) + $links) { if (Test-Path $p) { Write-Host "  $p" } }
$answer = Read-Host 'Συνέχεια; (Ν/Ο)'
if ($answer -notmatch '^[ΝνNnYy]') { Write-Host 'Ακυρώθηκε.'; exit 0 }
foreach ($p in @($app, $settings) + $links) {
    if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
Write-Host 'Το AG-Scaife2AntConc αφαιρέθηκε. Τα αρχεία που φτιάξατε με αυτό δεν άλλαξαν.' -ForegroundColor Green
