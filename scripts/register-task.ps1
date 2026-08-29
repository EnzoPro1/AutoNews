<#
.SYNOPSIS
    Enregistre (ou retire) la tache planifiee d'ingestion.

.DESCRIPTION
    Aucun droit administrateur requis : la tache est enregistree pour
    l'utilisateur courant et ne s'execute que lorsqu'il est connecte.

    Le reglage central est -StartWhenAvailable. Sans lui, une occurrence
    manquee -- laptop eteint, en veille, ou en deplacement -- est simplement
    sautee, en silence. C'est exactement le mode de panne que ce lot combat.

    A savoir : le Planificateur ne rattrape QU'UNE occurrence manquee, pas
    toutes. Apres trois jours hors ligne il y aura un run de rattrapage, pas
    douze. Sans consequence ici, RSS ne permettant de toute facon aucun
    rattrapage d'historique -- mais autant que ce soit dit plutot que decouvert
    dans /coverage.

    Les horodatages du Planificateur sont en heure LOCALE, contrairement au
    reste du projet qui est en UTC (piege D). C'est sans importance : le
    declencheur decide quand lancer, et tout ce qui est ecrit en base est
    converti en UTC par le script d'enrobage et par le pipeline.

.PARAMETER IntervalHours
    Intervalle entre deux executions. 6 h par defaut, valeur deduite de la
    mesure de rotation des pages (le flux le plus rapide, BleepingComputer,
    ne couvre qu'environ 26 h : 6 h laissent absorber trois runs manques
    consecutifs). Doit rester coherent avec VEILLE_INGEST_INTERVAL_HOURS, dont
    /coverage derive son seuil de trou.

.PARAMETER Unregister
    Retire la tache au lieu de l'enregistrer.

.EXAMPLE
    .\scripts\register-task.ps1
    .\scripts\register-task.ps1 -IntervalHours 4
    .\scripts\register-task.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [double]$IntervalHours = 6,
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'AutoNews - ingestion'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'ingest.ps1'

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "tache '$TaskName' retiree"
    } else {
        Write-Output "tache '$TaskName' absente, rien a faire"
    }
    return
}

if (-not (Test-Path $Script)) { throw "introuvable : $Script" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $RepoRoot

# Declencheur principal : toutes les N heures, indefiniment.
# StartBoundary dans le FUTUR proche. Avec une borne de depart deja passee,
# Register-ScheduledTask enregistre la tache mais la laisse DESACTIVEE : elle
# n'aurait jamais tourne, en silence. Constate a l'enregistrement initial.
$repetition = New-TimeSpan -Hours $IntervalHours
$principal = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval $repetition

# Declencheur additionnel : ouverture / deverrouillage de session. C'est le
# rattrapage le plus utile en pratique -- on rouvre le laptop, la collecte
# repart sans attendre le prochain creneau.
$aLOuverture = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

# Explicite, meme si c'est le defaut : une tache enregistree desactivee est
# indiscernable d'une tache qui fonctionne, jusqu'a ce qu'on constate le trou.
$settings.Enabled = $true

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($principal, $aLOuverture) `
    -Settings $settings `
    -Description "Ingestion des flux AutoNews toutes les $IntervalHours h. Rattrapage actif (StartWhenAvailable)." `
    -Force | Out-Null

# VERIFICATION APRES ENREGISTREMENT. Sans elle, ce script annoncerait un succes
# pour une tache qui ne se declenchera jamais -- meme classe d'erreur qu'une
# sauvegarde qu'on ne restaure jamais.
Enable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
$verif = Get-ScheduledTask -TaskName $TaskName
if ($verif.State -eq 'Disabled' -or -not $verif.Settings.Enabled) {
    throw "la tache '$TaskName' est enregistree mais DESACTIVEE. Elle ne se declenchera pas. Verifier dans le Planificateur de taches."
}
$info = $verif | Get-ScheduledTaskInfo
if (-not $info.NextRunTime) {
    throw "la tache '$TaskName' n'a aucune prochaine execution planifiee."
}

Write-Output "tache '$TaskName' enregistree, intervalle $IntervalHours h"
Write-Output "  etat            : $($verif.State)"
Write-Output "  prochaine exec. : $($info.NextRunTime)"
Write-Output ""
Write-Output "Verifier      : Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Output "Lancer a la main : Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Retirer       : .\scripts\register-task.ps1 -Unregister"
Write-Output ""
Write-Output "La collecte reelle commence maintenant. Noter cette date : c'est"
Write-Output "le debut de la fenetre d'observation, visible sur /coverage."
