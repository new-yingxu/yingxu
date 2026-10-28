# 采集 Windows 测试环境信息（失败时随测试记录一起回传）
#
#   powershell -ExecutionPolicy Bypass -File .\collect-env.ps1
#
# 生成 env-<主机名>-<时间>.txt 到当前目录。只读系统与程序状态，
# 不读取素材内容，不上传任何东西。

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path (Get-Location) ('env-{0}-{1}.txt' -f $env:COMPUTERNAME, $stamp)
$lines = @()

function Section($title) {
    $script:lines += ''
    $script:lines += ('===== ' + $title + ' =====')
}
function KV($key, $value) {
    $script:lines += ('{0,-20} {1}' -f $key, [string]$value)
}

Section '采集时间'
KV '时间' (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
KV '主机名' $env:COMPUTERNAME
KV '当前用户' $env:USERNAME

Section '系统'
KV 'Windows' ([System.Environment]::OSVersion.VersionString)
KV '64位系统' ([System.Environment]::Is64BitOperatingSystem)
try {
    $os = Get-CimInstance Win32_OperatingSystem
    KV '系统名称' $os.Caption
    KV 'Build' $os.BuildNumber
    KV '内存GB' ([math]::Round($os.TotalVisibleMemorySize / 1MB, 1))
} catch {
    KV 'CIM查询' ('失败: ' + $_.Exception.Message)
}

Section 'WebView2 运行时（Tauri 必需）'
$keys = @(
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
)
$wv = $null
foreach ($k in $keys) {
    if (Test-Path $k) {
        $pv = (Get-ItemProperty $k -ErrorAction SilentlyContinue).pv
        if ($pv) { $wv = $pv; break }
    }
}
if ($wv) { KV 'WebView2' $wv } else { KV 'WebView2' '未检测到（若程序白屏，请先安装 Evergreen 运行时）' }

Section '安装包'
$exe = Get-ChildItem -Path . -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '*setup*' } | Select-Object -First 3
if ($exe) {
    foreach ($f in $exe) {
        KV '文件' $f.Name
        KV ' 大小' $f.Length
        KV ' SHA256' (Get-FileHash $f.FullName -Algorithm SHA256).Hash
    }
} else {
    KV '安装包' '本目录未找到 *setup*.exe'
}

Section '安装位置'
foreach ($d in @(
        (Join-Path $env:LOCALAPPDATA '影序桌面开发版'),
        (Join-Path $env:LOCALAPPDATA 'Programs\影序桌面开发版'),
        (Join-Path $env:ProgramFiles '影序桌面开发版'))) {
    if (Test-Path $d) {
        KV '目录' $d
        Get-ChildItem $d -ErrorAction SilentlyContinue | Select-Object -First 15 |
            ForEach-Object { KV '  内容' ($_.Name + '  ' + $_.Length) }
    }
}

Section '进程'
$procs = Get-Process -Name '*yingxu*' -ErrorAction SilentlyContinue
if ($procs) {
    foreach ($p in $procs) {
        KV '进程' ('{0} pid={1} 内存={2}MB' -f $p.ProcessName, $p.Id, [math]::Round($p.WorkingSet64 / 1MB, 1))
        try { KV '  路径' $p.Path } catch { }
    }
} else {
    KV '进程' '没有 yingxu 相关进程'
}

Section '应用数据目录'
foreach ($d in @(
        (Join-Path $env:APPDATA 'app.yingxu.desktop.next'),
        (Join-Path $env:LOCALAPPDATA 'app.yingxu.desktop.next'))) {
    if (Test-Path $d) {
        KV '目录' $d
        Get-ChildItem $d -Recurse -ErrorAction SilentlyContinue | Select-Object -First 30 |
            ForEach-Object { KV '  内容' ($_.FullName.Replace($d, '') + '  ' + $_.Length) }
    }
}

Section '启动项残留（卸载后复查）'
$runKeys = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
)
foreach ($k in $runKeys) {
    if (Test-Path $k) {
        $props = Get-ItemProperty $k
        $props.PSObject.Properties |
            Where-Object { $_.Value -like '*yingxu*' -or $_.Name -like '*影序*' } |
            ForEach-Object { KV '启动项' ($k + ' :: ' + $_.Name) }
    }
}
KV '说明' '上面没有输出 = 没有相关启动项'

Section '磁盘'
Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Free -ne $null) {
        KV '磁盘' ('{0}: 可用 {1} GB / 共 {2} GB' -f $_.Name,
            [math]::Round($_.Free / 1GB, 1), [math]::Round(($_.Used + $_.Free) / 1GB, 1))
    }
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ''
Write-Host ('已生成：' + $out) -ForegroundColor Green
Write-Host '请把该文件连同 测试记录表 与 截图/ 一起回传。'
