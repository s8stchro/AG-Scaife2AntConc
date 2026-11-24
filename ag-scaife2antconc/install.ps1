# AG-Scaife2AntConc: installation for Windows. Started by install.bat (double click).
# Installs into %LOCALAPPDATA%\AG-Scaife2AntConc: Python 3.12 (through uv), GreBERTa joint, GreTa, this program
# and AG-Diogenes-SpaCy-txt2xml from the neighbouring folder; creates a desktop shortcut.
# Run it again to update; it needs no administrator rights.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$here = $PSScriptRoot
$lib = Join-Path (Split-Path $here -Parent) 'ag-diogenes-spacy-txt2xml'
$app = Join-Path $env:LOCALAPPDATA 'AG-Scaife2AntConc'
$envdir = Join-Path $app 'env'
$py = Join-Path $envdir 'Scripts\python.exe'
$gui = Join-Path $envdir 'Scripts\ag-scaife2antconc-gui.exe'
$jointUrl = 'https://github.com/ryanpavlicek/pyaegean/releases/download/grc-joint-v3/grc-joint.tar.gz'
$jointArchive = Join-Path $app 'grc-joint-v3.tar.gz'
$jointHash = 'f646d34a08dbf612abbe076c27188f077c2289da0b7bbbc7116bfe807112b06e'
$gretaUrl = 'https://github.com/ryanpavlicek/pyaegean/releases/download/grc-lemma-neural-v1/grc-lemma-neural.tar.gz'
$gretaArchive = Join-Path $app 'grc-lemma-neural-v1.tar.gz'
$gretaHash = '38126872e7a5be6389054062d4789ce5b6fc7e84327b07c2b93649a6f0f1a228'
$diogenesUrl = 'https://d.iogen.es/d/'
$diogenesPlaces = @("${env:ProgramFiles(x86)}\Diogenes", "$env:ProgramFiles\Diogenes", "$env:USERPROFILE\Diogenes")

function Step($n, $text) { Write-Host ''; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ''; Write-Host $text -ForegroundColor Red; exit 1 }
function Find-Diogenes {
    foreach ($p in $diogenesPlaces) {
        if (Test-Path (Join-Path $p 'dependencies\data\greek-analyses.txt')) { return $p }
    }
    return $null
}

Write-Host 'AG-Scaife2AntConc: εγκατάσταση' -ForegroundColor Green
Write-Host 'Χρειάζεται σύνδεση στο διαδίκτυο. GreBERTa joint, εκπαιδευμένο GreTa και πακέτα: περίπου 500 MB.'

if (-not (Test-Path (Join-Path $lib 'pyproject.toml'))) {
    Fail "Δεν βρέθηκε ο φάκελος ag-diogenes-spacy-txt2xml δίπλα στον φάκελο αυτού του προγράμματος.`nΑποσυμπιέστε ολόκληρο το zip και ξανατρέξτε το install.bat."
}

# an open window locks the program files: an update would fail half-way
function Get-Running {
    @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($envdir, [StringComparison]::OrdinalIgnoreCase) })
}
while ((Get-Running).Count) {
    Write-Host 'Το AG-Scaife2AntConc είναι ανοιχτό. Στον browser πατήστε «Κλείσιμο εφαρμογής», ή κλείστε το παράθυρο της παλιάς έκδοσης.' -ForegroundColor Yellow
    Read-Host 'Πατήστε Enter όταν το κλείσετε' | Out-Null
}

try {
    Step 1 'Εργαλείο εγκατάστασης uv'
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        $env:UV_NO_MODIFY_PATH = '1'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    }
    if (-not (Test-Path $uv)) { Fail 'Η εγκατάσταση του uv απέτυχε.' }
    Write-Host "uv: $uv"

    Step 2 'Python 3.12 (σε δικό του φάκελο· δεν αγγίζει άλλες εγκαταστάσεις Python)'
    # Cached wheel directories can carry Windows' read-only attribute. Clear
    # that directory flag before replacing this dedicated environment; never
    # traverse a project or an arbitrary computed installation path.
    $appAbsolute = [IO.Path]::GetFullPath($app)
    $envAbsolute = [IO.Path]::GetFullPath($envdir)
    $expectedEnvironment = [IO.Path]::GetFullPath((Join-Path $appAbsolute 'env'))
    if (-not $envAbsolute.Equals($expectedEnvironment, [StringComparison]::OrdinalIgnoreCase)) { Fail 'Μη αναμενόμενος φάκελος εγκατάστασης.' }
    if (Test-Path -LiteralPath $envAbsolute) {
        $environmentDirectories = @(Get-Item -LiteralPath $envAbsolute -Force) + @(Get-ChildItem -LiteralPath $envAbsolute -Directory -Recurse -Force)
        foreach ($environmentDirectory in $environmentDirectories) {
            $directoryAbsolute = [IO.Path]::GetFullPath($environmentDirectory.FullName)
            if (-not ($directoryAbsolute.Equals($envAbsolute, [StringComparison]::OrdinalIgnoreCase) -or
                $directoryAbsolute.StartsWith($envAbsolute + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) { Fail 'Μη έγκυρη διαδρομή περιβάλλοντος.' }
            $environmentDirectory.Attributes = $environmentDirectory.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
        }
    }
    & $uv venv --python 3.12 --clear --quiet $envdir
    if (-not (Test-Path $app)) { New-Item -ItemType Directory $app | Out-Null }
    if ($LASTEXITCODE) { Fail 'Δεν δημιουργήθηκε το περιβάλλον Python.' }

    Step 3 'GreBERTa joint, GreTa και το πρόγραμμα'
    $rules = Join-Path (Split-Path $here -Parent) 'ag-greek-rules'
    if (-not (Test-Path (Join-Path $rules 'pyproject.toml'))) { Fail 'Λείπει ο φάκελος ag-greek-rules. Κρατήστε τους τρεις φακέλους του προγράμματος μαζί.' }
    & $uv pip install --python $py --quiet --reinstall-package ag-scaife2antconc --reinstall-package ag-diogenes-spacy-txt2xml $lib $rules $here
    if ($LASTEXITCODE) { Fail 'Η εγκατάσταση των πακέτων απέτυχε. Ελέγξτε τη σύνδεση και ξανατρέξτε το install.bat.' }
    & $py -c "import tkinter, aegean, ag_scaife2antconc, onnxruntime, tokenizers, fastapi, uvicorn"
    if ($LASTEXITCODE) { Fail 'Το πρόγραμμα εγκαταστάθηκε, αλλά δεν φορτώνει.' }
    if (-not (Test-Path $gretaArchive) -or (Get-FileHash -LiteralPath $gretaArchive -Algorithm SHA256).Hash -ne $gretaHash) {
        Invoke-WebRequest $gretaUrl -OutFile "$gretaArchive.part" -UseBasicParsing
        if ((Get-FileHash -LiteralPath "$gretaArchive.part" -Algorithm SHA256).Hash -ne $gretaHash) {
            Fail 'Η λήψη του GreTa δεν πέρασε τον έλεγχο SHA-256. Επαναλάβετε την εγκατάσταση.'
        }
        Move-Item -LiteralPath "$gretaArchive.part" -Destination $gretaArchive -Force
    }
    # The application verifies the pinned archive checksum before extracting any model files.
    & $py -X utf8 -m ag_scaife2antconc --prepare-greta --greta-archive $gretaArchive
    if ($LASTEXITCODE) { Fail 'Η προετοιμασία του GreTa απέτυχε. Το πρόγραμμα απαιτεί εκπαιδευμένο GreTa.' }
    & $py -X utf8 -c "from ag_diogenes_spacy_txt2xml.greta import Greta; Greta()"
    if ($LASTEXITCODE) { Fail 'Το εκπαιδευμένο GreTa δεν φορτώνει. Η εγκατάσταση δεν ολοκληρώθηκε.' }

    if (-not (Test-Path $jointArchive) -or (Get-FileHash -LiteralPath $jointArchive -Algorithm SHA256).Hash -ne $jointHash) {
        Invoke-WebRequest $jointUrl -OutFile "$jointArchive.part" -UseBasicParsing
        if ((Get-FileHash -LiteralPath "$jointArchive.part" -Algorithm SHA256).Hash -ne $jointHash) {
            Fail 'Η λήψη GreBERTa δεν πέρασε τον έλεγχο SHA-256.'
        }
        Move-Item -LiteralPath "$jointArchive.part" -Destination $jointArchive -Force
    }
    & $py -X utf8 -m ag_scaife2antconc --prepare-context --context-archive $jointArchive
    if ($LASTEXITCODE) { Fail 'Η προετοιμασία GreBERTa joint απέτυχε.' }
    & $py -X utf8 -c "from ag_diogenes_spacy_txt2xml.greberta import Joint; Joint()"
    if ($LASTEXITCODE) { Fail 'Το GreBERTa joint δεν φορτώνει.' }

    Step 4 'Diogenes (για τα λήμματα)'
    $dio = Find-Diogenes
    if (-not $dio) {
        Write-Host 'Ο Diogenes δεν βρέθηκε. Θα ανοίξει η σελίδα του: κατεβάστε και εγκαταστήστε την έκδοση για Windows.' -ForegroundColor Yellow
        Write-Host 'Χωρίς αυτόν το πρόγραμμα φτιάχνει μόνο το καθαρό κείμενο, όχι λήμματα.'
        Start-Process $diogenesUrl
        Read-Host 'Πατήστε Enter όταν τελειώσει η εγκατάσταση του Diogenes (ή απλώς Enter για να συνεχίσετε χωρίς αυτόν)' | Out-Null
        $dio = Find-Diogenes
    }
    if ($dio) { Write-Host "Diogenes: $dio" } else { Write-Host 'Συνέχεια χωρίς Diogenes. Μπορείτε να τον εγκαταστήσετε αργότερα.' }

    Step 5 'Προετοιμασία των πινάκων του Diogenes (μόνο αυτή τη φορά· 2-5 λεπτά)'
    if ($dio) {
        & $py -m ag_scaife2antconc --prepare --diogenes $dio
        if ($LASTEXITCODE) { Write-Host 'Η προετοιμασία δεν ολοκληρώθηκε· θα γίνει στην πρώτη χρήση.' -ForegroundColor Yellow }
    } else {
        Write-Host 'Παραλείπεται (δεν υπάρχει Diogenes).'
    }

    Step 6 'Συντόμευση στην επιφάνεια εργασίας και στο μενού Έναρξη'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $lnk = $shell.CreateShortcut((Join-Path $dir 'AG-Scaife2AntConc.lnk'))
        $lnk.TargetPath = $gui
        $lnk.WorkingDirectory = [Environment]::GetFolderPath('MyDocuments')
        $lnk.Description = 'Scaife → AntConc: τοπικός χώρος εργασίας στον browser'
        $lnk.Save()
    }
}
catch {
    Fail "Σφάλμα: $($_.Exception.Message)`nΣτείλτε αυτό το μήνυμα στον διδάσκοντα."
}

Write-Host ''
Write-Host 'Έτοιμο! Ανοίξτε το AG-Scaife2AntConc από την αναζήτηση των Windows. Ο χώρος εργασίας ανοίγει στον browser.' -ForegroundColor Green
Write-Host 'Μπορείτε να κρατήσετε σελιδοδείκτη στο http://127.0.0.1:8766. Πρώτα πρέπει να εκτελείται η τοπική εφαρμογή.'
Write-Host 'Η συντόμευση παραμένει διαθέσιμη και δέχεται αρχεία XML με σύρσιμο.'
