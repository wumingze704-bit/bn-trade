# 本地 <-> 服务器同步：推代码、拉分钟数据与交易范围快照、推模型产物。
# 服务器地址由 ~/.ssh/config 中的 Host tokyo 提供，脚本不写 IP 与密钥。
# 规格：docs/02-系统架构与部署.md §5.8
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("push-code", "pull-data", "push-models", "tunnel")]
    [string]$Action,

    [string]$ModelId
)

$ErrorActionPreference = "Stop"
$Remote = "tokyo"
$RemoteDir = "trade"

function Invoke-Rsync {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (Get-Command rsync -ErrorAction SilentlyContinue) {
        & rsync -avz --partial $Source $Destination
    }
    else {
        # 无 rsync 时退回 scp（docs/02 §5.2）
        scp -r $Source $Destination
    }
}

switch ($Action) {
    "push-code" {
        git push $Remote main
    }
    "pull-data" {
        Invoke-Rsync "${Remote}:${RemoteDir}/data/bars/" "./data/bars/"
        Invoke-Rsync "${Remote}:${RemoteDir}/data/universe/" "./data/universe/"
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
