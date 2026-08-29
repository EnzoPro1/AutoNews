<#
.SYNOPSIS
    Enrobage d'ingestion pour le Planificateur de taches Windows.

.DESCRIPTION
    Trois responsabilites, et rien d'autre :

      1. attendre que le demon Docker reponde, avec une attente bornee
         (Docker Desktop met souvent une minute a demarrer apres l'ouverture de
         session, et la tache se declenche justement a ce moment-la) ;

      2. lancer l'ingestion s'il repond ;

      3. si le demon ne repond pas : journaliser l'incident DE MANIERE
         PERSISTANTE. C'est le coeur du probleme -- quand Docker est mort,
         Postgres l'est aussi, on ne peut donc pas ecrire l'echec en base. La
         tentative part dans state/missed-runs.jsonl, et le prochain run reussi
         la remontera (voir veille.ingest.missed).

    Ce script NE REPARE RIEN. Si Docker refuse de demarrer, il le signale et
    s'arrete. Une reparation automatique qui se trompe pendant un deplacement
    est pire qu'un trou signale ; la reparation manuelle est dans
    scripts/repair-docker-sockets.ps1.

    Tous les horodatages ecrits pour la base sont en UTC explicite (piege D) :
    la collecte traverse des fuseaux, un horodatage local produirait des trous
    fantomes ou des runs dates dans le futur.

.PARAMETER DockerTimeoutSeconds
    Attente maximale du demon Docker. 180 s par defaut.

.PARAMETER LogRetentionDays
    Nombre de jours de journaux conserves. 30 par defaut.
#>
[CmdletBinding()]
param(
    [int]$DockerTimeoutSeconds = 180,
    [int]$LogRetentionDays = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot 'logs'
$StateDir = Join-Path $RepoRoot 'state'
$QueueFile = Join-Path $StateDir 'missed-runs.jsonl'

New-Item -ItemType Directory -Force -Path $LogDir, $StateDir | Out-Null

$LogFile = Join-Path $LogDir ("ingest-{0}.log" -f (Get-Date).ToString('yyyyMMdd'))

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    # Horodatage UTC dans le journal aussi : c'est ce qui permet de recouper une
    # ligne de log avec une ligne de feed_run apres un changement de fuseau.
    $stamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line = "{0} {1,-5} {2}" -f $stamp, $Level, $Message
    # Write-Host et non Write-Output : la sortie standard d'une fonction est sa
    # VALEUR DE RETOUR en PowerShell. Journaliser avec Write-Output ferait
    # remonter chaque ligne de log dans le code retour d'Invoke-Docker.
    Write-Host $line
    # UTF-8 SANS BOM : Out-File et Add-Content de PowerShell 5.1 en ajoutent un,
    # et il polluerait la premiere ligne de chaque fichier.
    [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
}

function Add-MissedRun {
    <#
        Ecrit une tentative avortee dans le fichier d'attente. Une ligne JSON
        par tentative, horodatee en UTC. Le fichier est draine par le prochain
        run reussi ; la contrainte d'unicite en base rend l'operation idempotente,
        donc une ligne ecrite deux fois n'a aucune consequence.
    #>
    param([string]$Reason, [string]$Detail)

    $record = [ordered]@{
        attempted_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
        reason       = $Reason
        detail       = $Detail
    }
    $json = ($record | ConvertTo-Json -Compress)
    try {
        [System.IO.File]::AppendAllText($QueueFile, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Write-Log "tentative avortee consignee dans state/missed-runs.jsonl ($Reason)" 'WARN'
    } catch {
        # Dernier recours : si meme le fichier d'attente est inaccessible, on ne
        # peut plus rien tracer. On le dit dans le journal et on s'arrete la.
        Write-Log "IMPOSSIBLE de consigner la tentative avortee : $($_.Exception.Message)" 'ERROR'
    }
}

function Invoke-Docker {
    <#
        Lance docker en journalisant sa sortie complete.

        PowerShell 5.1 : rediriger la sortie d'erreur d'une commande NATIVE avec
        2>&1 emballe chaque ligne dans un ErrorRecord (NativeCommandError). Avec
        $ErrorActionPreference = 'Stop', la premiere ligne de progression de
        docker -- ecrite sur stderr, sans que rien n'aille mal -- fait echouer
        le script. On repasse donc en 'Continue' le temps de l'appel, et on
        s'appuie sur $LASTEXITCODE, seul indicateur fiable ici.
    #>
    param([string[]]$Arguments, [string]$Level = 'INFO')

    $precedent = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @Arguments 2>&1 | ForEach-Object {
            # Une ligne de stderr arrive ici emballee dans un ErrorRecord. On
            # extrait son message : sinon le journal recoit la trace PowerShell
            # complete (CategoryInfo, FullyQualifiedErrorId...) pour une simple
            # ligne de progression de docker.
            $texte = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.Exception.Message
            } else {
                [string]$_
            }
            $texte = $texte.Trim()
            if ($texte) { Write-Log $texte $Level }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $precedent
    }
}

function Remove-OldLogs {
    $limite = (Get-Date).AddDays(-$LogRetentionDays)
    Get-ChildItem -Path $LogDir -Filter 'ingest-*.log' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $limite } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Wait-Docker {
    <# Rend $true des que le demon repond, $false au bout de l'attente. #>
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        # Cas distinct du demon arrete : le binaire lui-meme est absent du PATH.
        # Sans ce test, `& docker` leve une CommandNotFoundException TERMINANTE
        # qui remonte au-dessus de tout et fait abandonner le script sans rien
        # consigner -- l'abandon silencieux que ce lot existe pour empecher.
        Write-Log 'binaire docker introuvable dans le PATH' 'ERROR'
        return $false
    }

    $deadline = (Get-Date).AddSeconds($DockerTimeoutSeconds)
    $premier = $true
    while ((Get-Date) -lt $deadline) {
        $precedent = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & docker version --format '{{.Server.Version}}' 2>$null | Out-Null
            $code = $LASTEXITCODE
        } catch {
            $code = 1
        } finally {
            $ErrorActionPreference = $precedent
        }
        if ($code -eq 0) { return $true }
        if ($premier) {
            Write-Log "demon Docker injoignable, attente jusqu'a $DockerTimeoutSeconds s"
            $premier = $false
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

# --------------------------------------------------------------------------

Remove-OldLogs
Write-Log "--- demarrage de l'ingestion planifiee ---"

# Filet de securite global. Toute erreur imprevue -- y compris une erreur
# TERMINANTE de PowerShell, qui ne passe par aucun code retour -- doit laisser
# une trace persistante avant de sortir. Sans ce bloc, l'enrobage peut mourir
# en silence et le trou de collecte reste inexplique dans /coverage.
try {

if (-not (Wait-Docker)) {
    Write-Log "demon Docker toujours injoignable apres $DockerTimeoutSeconds s, abandon" 'ERROR'
    Add-MissedRun -Reason 'docker_unavailable' -Detail "demon injoignable apres $DockerTimeoutSeconds s"
    exit 1
}

Push-Location $RepoRoot
try {
    Write-Log 'demarrage des services (docker compose up -d)'
    $up = Invoke-Docker -Arguments @('compose', 'up', '-d') -Level 'DEBUG'
    if ($up -ne 0) {
        Write-Log "docker compose up a echoue (code $up)" 'ERROR'
        Add-MissedRun -Reason 'wrapper_error' -Detail "docker compose up code $up"
        exit 1
    }

    Write-Log 'ingestion'
    $code = Invoke-Docker -Arguments @('compose', 'exec', '-T', 'app', 'python', '-m', 'veille', 'ingest')

    switch ($code) {
        0 { Write-Log 'ingestion terminee' }
        3 {
            # Chevauchement avec un `make ingest` manuel. Ce n'est pas une
            # panne, et le pipeline l'a deja consigne dans missed_run : ne
            # RIEN ecrire ici, sinon la meme tentative serait comptee deux fois.
            Write-Log "une autre ingestion etait en cours, rien a faire" 'WARN'
        }
        default {
            Write-Log "ingestion en echec (code $code)" 'ERROR'
            # Pas de missed_run : la base etait joignable, chaque flux a donc
            # deja sa propre ligne feed_run. Une ligne de plus mentirait sur la
            # nature de l'incident.
        }
    }
    exit $code
} finally {
    Pop-Location
}

} catch {
    Write-Log "erreur imprevue de l'enrobage : $($_.Exception.Message)" 'ERROR'
    Add-MissedRun -Reason 'wrapper_error' -Detail $_.Exception.Message
    exit 1
} finally {
    Write-Log "--- fin ---"
}
