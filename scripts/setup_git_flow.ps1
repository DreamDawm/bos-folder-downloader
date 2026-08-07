[CmdletBinding()]
param(
    [string]$GitHubRemote = "origin",
    [string]$InternalRemote = "BosDownload"
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    & git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args -join ' ') 执行失败，退出码 $LASTEXITCODE"
    }
}

$projectRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $projectRoot) {
    throw "当前目录不是 Git 仓库"
}
Set-Location $projectRoot

$remotes = @(& git remote)
if ($remotes -notcontains $GitHubRemote) {
    throw "缺少 GitHub 远端: $GitHubRemote"
}
$githubUrl = (& git remote get-url $GitHubRemote)
if ($githubUrl -notmatch "github\.com[:/]") {
    throw "$GitHubRemote 不是 GitHub 远端: $githubUrl"
}

if ($remotes -notcontains $InternalRemote) {
    $internalCandidates = @(
        $remotes | Where-Object {
            $url = (& git remote get-url $_)
            $url -match "(?i)[/:]bosdownload(?:\.git)?$"
        }
    )
    if ($internalCandidates.Count -ne 1) {
        throw "无法唯一识别内网 BosDownload 远端"
    }
    Invoke-Git remote rename $internalCandidates[0] $InternalRemote
}

Invoke-Git fetch $GitHubRemote --prune

& git show-ref --verify --quiet "refs/remotes/$GitHubRemote/develop"
if ($LASTEXITCODE -ne 0) {
    & git show-ref --verify --quiet refs/heads/develop
    if ($LASTEXITCODE -ne 0) {
        Invoke-Git branch develop "$GitHubRemote/main"
    }
    Invoke-Git push -u $GitHubRemote develop
    Invoke-Git fetch $GitHubRemote develop --prune
}

& git show-ref --verify --quiet refs/heads/develop
if ($LASTEXITCODE -ne 0) {
    Invoke-Git branch --track develop "$GitHubRemote/develop"
} else {
    Invoke-Git branch --set-upstream-to="$GitHubRemote/develop" develop
}

Invoke-Git config core.hooksPath .githooks

Write-Host "Git Flow 初始化完成"
Write-Host "GitHub 远端: $GitHubRemote ($githubUrl)"
Write-Host "内网仓库: $InternalRemote ($(& git remote get-url $InternalRemote))"
Write-Host "开发基线: $GitHubRemote/develop"
