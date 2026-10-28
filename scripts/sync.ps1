# 本地 <-> 服务器同步：推代码、拉分钟数据、推模型产物。
# 服务器地址由 ~/.ssh/config 中的 Host tokyo 提供，脚本不写 IP 与密钥。
# 规格：docs/02-系统架构与部署.md §5.8
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("push-code", "pull-data", "push-models")]
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
    }
    "push-models" {
        if (-not $ModelId) {
            throw "push-models 需要 -ModelId 参数"
        }
        Invoke-Rsync "./artifacts/models/$ModelId/" "${Remote}:${RemoteDir}/artifacts/models/$ModelId/"
    }
}
