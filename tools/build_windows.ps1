param(
    [string]$Python = 'F:\Anaconda\envs\deeplearning\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $projectRoot '.build'
$dependencies = Join-Path $buildRoot 'packaging-deps'
$releaseRoot = Join-Path $projectRoot 'release'
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $dependencies 'PyInstaller\__main__.py'))) {
    & $Python -X utf8 -m pip install --only-binary=:all: --target $dependencies 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install isolated packaging dependencies' }
}
$previousPythonPath = $env:PYTHONPATH
$previousSystemPath = $env:PATH
try {
    $env:PYTHONPATH = $dependencies
    $condaDllDirectory = Join-Path (Split-Path -Parent $Python) 'Library\bin'
    $env:PATH = $condaDllDirectory + ';' + $env:PATH
    $buildArgs = @(
        '--noconfirm', '--onedir', '--windowed', '--noupx',
        '--name', 'CoalVolume',
        '--paths', $PSScriptRoot,
        '--distpath', $releaseRoot,
        '--workpath', (Join-Path $buildRoot 'pyinstaller-work'),
        '--specpath', (Join-Path $buildRoot 'spec'),
        '--collect-submodules', 'pyrealsense2',
        '--collect-binaries', 'pyrealsense2',
        '--add-binary', ((Join-Path $condaDllDirectory 'libjpeg.dll') + ';.'),
        '--add-binary', ((Join-Path $condaDllDirectory 'libwebp.dll') + ';.'),
        '--add-binary', ((Join-Path $condaDllDirectory 'libwebpmux.dll') + ';.'),
        '--add-binary', ((Join-Path $condaDllDirectory 'libwebpdemux.dll') + ';.'),
        '--exclude-module', 'torch', '--exclude-module', 'tensorflow',
        '--exclude-module', 'matplotlib', '--exclude-module', 'scipy',
        '--exclude-module', 'pandas', '--exclude-module', 'IPython',
        '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
        '--exclude-module', 'PySide2', '--exclude-module', 'PySide6',
        (Join-Path $PSScriptRoot 'coal_volume_app.py')
    )
    & $Python -X utf8 -m PyInstaller @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\portable_readme.txt') -Destination (Join-Path $releaseRoot 'CoalVolume\使用说明.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\live_volume_usage.md') -Destination (Join-Path $releaseRoot 'CoalVolume\调用说明.md')
    Write-Output (Join-Path $releaseRoot 'CoalVolume\CoalVolume.exe')
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PATH = $previousSystemPath
}
