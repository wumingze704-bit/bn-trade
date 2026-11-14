# 本地 <-> 服务器同步：推代码、拉分钟数据与交易范围快照、推模型产物。
# 服务器地址由 ~/.ssh/config 中的 Host tokyo 提供，脚本不写 IP 与密钥。
# 规格：docs/02-系统架构与部署.md §5.8
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("push-code", "pull-data", "verify-data", "push-models", "tunnel")]
    [string]$Action,

    [string]$ModelId
)

$ErrorActionPreference = "Stop"
$Remote = "tokyo"
$RemoteDir = "trade"
# 封存集月份（base.yaml data_window 的最后 holdout_months 个月）：只在服务器保留，
# 不同步到本地、不读取内容（AGENTS.md §6 红线 2）。
$HoldoutMonths = @("202608", "202609")

function Invoke-Rsync {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExtraArgs = @()
    )

    if (Get-Command rsync -ErrorAction SilentlyContinue) {
        & rsync -avz --partial @ExtraArgs $Source $Destination
    }
    else {
        if ($ExtraArgs.Count -gt 0) {
            throw "当前无 rsync，且需排除封存集 M23~M24（--exclude），scp 无法安全回退（避免把封存集拉到本地，红线 2）。请任选其一：1) 安装 Git for Windows（自带 rsync）并把其 usr/bin 加入 PATH；2) 在 WSL 配置 Host tokyo 后运行 scripts/sync.sh pull-data。"
        }
        # 无 rsync 时退回 scp（docs/02 §5.2）
        scp -r $Source $Destination
    }
}

switch ($Action) {
    "push-code" {
        git push $Remote main
    }
    "pull-data" {
        # 封存集 M23~M24 的分钟数据只在服务器保留、不拉到本地（03 §6、AGENTS.md §6 红线 2）。
        # 月份 = base.yaml data_window.end_month 向前 holdout_months 个月；上线重训 end_month 前移时需同步更新。
        $excludes = $HoldoutMonths | ForEach-Object { "--exclude=$_.parquet" }
        Invoke-Rsync "${Remote}:${RemoteDir}/data/bars/" "./data/bars/" $excludes
        Invoke-Rsync "${Remote}:${RemoteDir}/data/universe/" "./data/universe/"
    }
    "verify-data" {
        # 同步后两端逐文件 SHA256 对比（03 §6、T1-6）：排除封存集；封存集只在服务器侧
        # 计算并记录哈希，不下载、不读内容（红线 2）。
        $localRoot = (Resolve-Path "./data/bars").Path
        $local = @{}
        Get-ChildItem $localRoot -Recurse -File -Filter *.parquet | ForEach-Object {
            $name = $_.Name.Replace(".parquet", "")
            if ($HoldoutMonths -notcontains $name) {
                $relative = $_.FullName.Substring($localRoot.Length + 1).Replace("\", "/")
                $local[$relative] = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
            }
        }
        $holdoutFilter = ($HoldoutMonths | ForEach-Object { "! -name '$_.parquet'" }) -join " "
        $remoteLines = ssh $Remote "cd $RemoteDir && find data/bars -name '*.parquet' $holdoutFilter | sort | xargs sha256sum"
        $remote = @{}
        foreach ($line in $remoteLines) {
            if ($line -match "^([0-9a-f]{64})\s+data/bars/(.+)$") {
                $remote[$Matches[2]] = $Matches[1]
            }
        }
        if ($remote.Count -eq 0) {
            throw "服务器未返回任何 bars 哈希（检查 ssh ${Remote} 与远端 data/bars）"
        }
        $onlyLocal = $local.Keys | Where-Object { -not $remote.ContainsKey($_) }
        $onlyRemote = $remote.Keys | Where-Object { -not $local.ContainsKey($_) }
        $diff = $local.Keys | Where-Object { $remote.ContainsKey($_) -and $remote[$_] -ne $local[$_] }
        Write-Output "本地文件 $($local.Count)，服务器文件 $($remote.Count)"
        if ($onlyLocal) { Write-Output "仅本地存在：$($onlyLocal -join ', ')" }
        if ($onlyRemote) { Write-Output "仅服务器存在：$($onlyRemote -join ', ')" }
        if ($diff) { Write-Output "哈希不一致：$($diff -join ', ')" }
        if ($onlyLocal -or $onlyRemote -or $diff) {
            throw "两端 bars 不一致（见上方清单）：请重跑 pull-data 或核对服务器数据"
        }
        # 封存集只记录服务器侧哈希，不下载内容
        $stamp = Get-Date -Format "yyyyMMddTHHmmssZ"
        ssh $Remote "cd $RemoteDir && mkdir -p artifacts/reports && { for m in $($HoldoutMonths -join ' '); do find data/bars -name `"`$m.parquet`" -print0 | sort -z | xargs -0 -r sha256sum; done; } > artifacts/reports/holdout_hashes_$stamp.log"
        Write-Output "封存集哈希已记录在服务器 artifacts/reports/holdout_hashes_$stamp.log（本地不下载、不读内容）"
    }
    "push-models" {
        if (-not $ModelId) {
            throw "push-models 需要 -ModelId 参数"
        }
        Invoke-Rsync "./artifacts/models/$ModelId/" "${Remote}:${RemoteDir}/artifacts/models/$ModelId/"
    }
    "tunnel" {
        # 建立 SSH 隧道访问服务器上的看板（127.0.0.1:8501）并打开浏览器（02 §5.6）。
        # 只转发本机绑定的看板端口，不转发任何币安流量（02 §5.3）。
        Start-Process ssh -ArgumentList "-N", "-L", "8501:127.0.0.1:8501", $Remote
        Start-Sleep -Seconds 2
        Start-Process "http://localhost:8501"
    }
}
