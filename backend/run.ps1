# backend/run.ps1 — 고정 환경값(.env)으로 백엔드 기동 (Windows/PowerShell).
#   PS> .\run.ps1
# .env 의 COMANTLE_SALT 등을 매번 같은 값으로 올린다(값을 새로 생성하지 않는다).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$envPath = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envPath)) { throw ".env 가 없습니다: $envPath" }

Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        Set-Item -Path "Env:$k" -Value $v
    }
}

Write-Host "[run] COMANTLE_SALT set (len=$($env:COMANTLE_SALT.Length)), DEV=$($env:COMANTLE_DEV)"
# --reload 미사용: Windows 에서 reload 워커가 orphan 으로 남아 포트를 물고 안 죽는 문제 +
# data/*.json 변경은 어차피 reload 대상이 아님. 코드/데이터 변경 후엔 이 창을 Ctrl+C 로 끄고 다시 실행.
py -3.13 -m uvicorn main:app --host 127.0.0.1 --port 8000
