[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$GitHubRemote = "origin",
    [string]$InternalRemote = "BosDownload",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$tag = "v$Version"

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') 执行失败，退出码 $LASTEXITCODE"
    }
}

function Test-GitAncestor {
    param([string]$Ancestor, [string]$Descendant)

    & git merge-base --is-ancestor $Ancestor $Descendant
    return $LASTEXITCODE -eq 0
}

function Get-RemoteTagCommit {
    param([string]$Remote, [string]$Tag)

    $peeled = @(& git ls-remote $Remote "refs/tags/$Tag^{}")
    if ($LASTEXITCODE -ne 0) {
        throw "无法查询 $Remote 上的标签 $Tag"
    }
    if ($peeled.Count -gt 0) {
        return ($peeled[0] -split "\s+")[0]
    }
    $direct = @(& git ls-remote $Remote "refs/tags/$Tag")
    if ($LASTEXITCODE -ne 0) {
        throw "无法查询 $Remote 上的标签 $Tag"
    }
    if ($direct.Count -eq 0) {
        return $null
    }
    return ($direct[0] -split "\s+")[0]
}

function Wait-GitHubWorkflow {
    param(
        [string]$Repository,
        [string]$Workflow,
        [string]$CommitSha,
        [string]$Event,
        [int]$MaxAttempts = 60
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $json = (& gh run list --repo $Repository --workflow $Workflow --commit $CommitSha `
            --event $Event --limit 20 --json databaseId,status,conclusion,headSha 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "查询 GitHub Actions 失败: $json"
        }
        $runs = @($json | ConvertFrom-Json | Where-Object { $_.headSha -eq $CommitSha })
        if ($runs.Count -gt 0) {
            $failed = @($runs | Where-Object {
                $_.status -eq "completed" -and $_.conclusion -ne "success"
            })
            if ($failed.Count -gt 0) {
                throw "$Workflow 在提交 $CommitSha 上执行失败"
            }
            $pending = @($runs | Where-Object { $_.status -ne "completed" })
            if ($pending.Count -eq 0) {
                Write-Host "$Workflow 已在提交 $CommitSha 上成功"
                return
            }
        }
        Start-Sleep -Seconds 10
    }
    throw "等待 $Workflow 在提交 $CommitSha 上成功超时"
}

if ($Version -notmatch "^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$") {
    throw "版本号必须是稳定 SemVer: MAJOR.MINOR.PATCH"
}

$projectRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $projectRoot) {
    throw "当前目录不是 Git 仓库"
}
Set-Location $projectRoot

if (& git status --porcelain) {
    throw "工作区必须干净"
}

$remotes = @(& git remote)
foreach ($remote in @($GitHubRemote, $InternalRemote)) {
    if ($remotes -notcontains $remote) {
        throw "缺少远端: $remote"
    }
}
if ((& git remote get-url $InternalRemote) -notmatch "(?i)[/:]bosdownload(?:\.git)?$") {
    throw "$InternalRemote 未指向内网 BosDownload 仓库"
}

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI 尚未认证"
}
$repository = (& gh repo view --json nameWithOwner --jq .nameWithOwner)
if ($LASTEXITCODE -ne 0 -or -not $repository) {
    throw "无法确定 GitHub 仓库"
}

Invoke-Git fetch $GitHubRemote --prune --tags
Invoke-Git fetch $InternalRemote main --prune --tags

$developRef = "$GitHubRemote/develop"
$mainRef = "$GitHubRemote/main"
$developSha = (& git rev-parse $developRef)
if (-not (Test-GitAncestor $mainRef $developRef)) {
    throw "$mainRef 不是 $developRef 的祖先"
}
if (-not (Test-GitAncestor "$InternalRemote/main" $developRef)) {
    throw "$InternalRemote/main 不能 fast-forward 到 $developRef"
}

$pyproject = Get-Content pyproject.toml -Raw
if ($pyproject -notmatch "(?m)^version\s*=\s*`"$([regex]::Escape($Version))`"\s*$") {
    throw "pyproject.toml 版本不是 $Version"
}
$changelog = Get-Content CHANGELOG.md -Raw
if ($changelog -notmatch "(?m)^## \[$([regex]::Escape($Version))\]") {
    throw "CHANGELOG.md 缺少 $Version 版本段落"
}

Wait-GitHubWorkflow -Repository $repository -Workflow "ci.yml" `
    -CommitSha $developSha -Event "push"

if ($ValidateOnly) {
    Write-Host "v$Version 发布门禁验证通过"
    exit 0
}

Invoke-Git switch main
Invoke-Git merge --ff-only $developRef

& git rev-parse --verify --quiet "refs/tags/$tag^{}"
if ($LASTEXITCODE -eq 0) {
    $localTagCommit = (& git rev-parse "$tag^{}")
    if ($localTagCommit -ne $developSha) {
        throw "本地标签 $tag 已存在但未指向 $developSha"
    }
} else {
    Invoke-Git tag -a $tag -m "发布 $tag"
}

$env:BOS_RELEASE_PUSH = "1"
try {
    $originTagCommit = Get-RemoteTagCommit $GitHubRemote $tag
    if ($originTagCommit -and $originTagCommit -ne $developSha) {
        throw "$GitHubRemote/$tag 已存在但指向其他提交"
    }
    Invoke-Git push --atomic $GitHubRemote main $tag
} finally {
    Remove-Item Env:BOS_RELEASE_PUSH -ErrorAction SilentlyContinue
}

Wait-GitHubWorkflow -Repository $repository -Workflow "ci.yml" `
    -CommitSha $developSha -Event "push"

$env:BOS_RELEASE_PUSH = "1"
try {
    $internalTagCommit = Get-RemoteTagCommit $InternalRemote $tag
    if ($internalTagCommit -and $internalTagCommit -ne $developSha) {
        throw "$InternalRemote/$tag 已存在但指向其他提交"
    }
    Invoke-Git push --atomic $InternalRemote main $tag
} finally {
    Remove-Item Env:BOS_RELEASE_PUSH -ErrorAction SilentlyContinue
}

& gh release view $tag --repo $repository *> $null
if ($LASTEXITCODE -ne 0) {
    $escapedVersion = [regex]::Escape($Version)
    $match = [regex]::Match(
        $changelog,
        "(?ms)^## \[$escapedVersion\][^\r\n]*\r?\n(?<body>.*?)(?=^## \[|\z)"
    )
    if (-not $match.Success) {
        throw "无法从 CHANGELOG.md 提取 $Version 发布说明"
    }
    $notesPath = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -LiteralPath $notesPath -Value $match.Groups["body"].Value.Trim() `
            -Encoding utf8 -NoNewline
        & gh release create $tag --repo $repository --verify-tag --title $tag `
            --notes-file $notesPath
        if ($LASTEXITCODE -ne 0) {
            throw "创建 GitHub Release $tag 失败"
        }
    } finally {
        Remove-Item -LiteralPath $notesPath -Force -ErrorAction SilentlyContinue
    }
}

Wait-GitHubWorkflow -Repository $repository -Workflow "release.yml" `
    -CommitSha $developSha -Event "release"

$releaseJson = (& gh release view $tag --repo $repository `
    --json isDraft,isPrerelease,assets,tagName 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw "读取 GitHub Release $tag 失败: $releaseJson"
}
$release = $releaseJson | ConvertFrom-Json
if ($release.isDraft -or $release.isPrerelease) {
    throw "GitHub Release $tag 不是正式发布"
}
if (@($release.assets).Count -lt 2) {
    throw "GitHub Release $tag 缺少 Python 分发包资产"
}

if (-not (Test-Path Dockerfile)) {
    Write-Host "仓库没有 Dockerfile，容器镜像与 ACR 校验不适用"
}
Write-Host "$tag 发布完成，GitHub 与 BosDownload main 均指向 $developSha"
