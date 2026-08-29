<#
.SYNOPSIS
    Reparation MANUELLE de Docker Desktop bloque par des sockets residuels.
    A NE JAMAIS PLANIFIER.

.DESCRIPTION
    Symptome : Docker Desktop refuse de demarrer avec un message du genre

        starting services: initializing Inference manager: listening on
        unix://C:/Users/.../Docker/run/dockerInference: remove ... :
        The file cannot be accessed by the system.

    Cause : un arret non propre (coupure, redemarrage force, processus tue)
    laisse des sockets AF_UNIX dans %LOCALAPPDATA%. Windows ne sait pas les
    supprimer -- ni Remove-Item, ni del, ni rd /s ne passent. Docker Desktop
    tente de les recreer, echoue, et s'arrete.

    Contournement : renommer les DOSSIERS parents. Renommer un dossier
    n'ouvre pas ses enfants, l'operation aboutit la ou la suppression echoue.
    Docker recree des dossiers propres au demarrage suivant.

    POURQUOI CE SCRIPT N'EST PAS AUTOMATISE
    Il tue des processus et renomme des dossiers dans le profil utilisateur.
    Lance sans surveillance par une tache planifiee, une reparation qui se
    trompe pendant un deplacement est bien pire qu'un trou de collecte signale :
    l'enrobage, lui, consigne l'incident et /coverage le montre. Ce script se
    lance a la main, en regardant ce qu'il fait.

    Les dossiers renommes en *.broken-<horodatage> peuvent etre supprimes plus
    tard, quand Windows aura lache les sockets (typiquement apres un
    redemarrage). Ce script ne les supprime pas.

.PARAMETER WhatIfOnly
    Montre ce qui serait fait, sans rien modifier.

.EXAMPLE
    .\scripts\repair-docker-sockets.ps1 -WhatIfOnly
    .\scripts\repair-docker-sockets.ps1
#>
[CmdletBinding()]
param([switch]$WhatIfOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$Cibles = @(
    (Join-Path $env:LOCALAPPDATA 'Docker\run'),
    (Join-Path $env:LOCALAPPDATA 'docker-secrets-engine')
)

Write-Output "Dossiers concernes :"
foreach ($p in $Cibles) {
    if (Test-Path $p) {
        $n = @(Get-ChildItem -Force $p -ErrorAction SilentlyContinue).Count
        Write-Output "  $p  ($n element(s))"
    } else {
        Write-Output "  $p  (absent, rien a faire)"
    }
}

if ($WhatIfOnly) {
    Write-Output ""
    Write-Output "-WhatIfOnly : rien n'a ete modifie."
    return
}

Write-Output ""
Write-Output "Arret de Docker Desktop..."
Get-Process | Where-Object { $_.Name -like '*docker*' -or $_.Name -like '*vpnkit*' } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 6

$stamp = Get-Date -Format 'yyyyMMddHHmmss'
foreach ($p in $Cibles) {
    if (-not (Test-Path $p)) { continue }
    $nouveau = (Split-Path -Leaf $p) + ".broken-$stamp"
    try {
        Rename-Item -LiteralPath $p -NewName $nouveau -ErrorAction Stop
        Write-Output "  renomme : $(Split-Path -Leaf $p) -> $nouveau"
    } catch {
        Write-Output "  ECHEC sur $p : $($_.Exception.Message)"
    }
}

$exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
if (Test-Path $exe) {
    Start-Process $exe
    Write-Output ""
    Write-Output "Docker Desktop relance. Compter une a deux minutes."
    Write-Output "Verifier avec : docker version"
} else {
    Write-Output ""
    Write-Output "Docker Desktop introuvable a l'emplacement habituel, a relancer a la main."
}
