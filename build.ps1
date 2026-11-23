$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    & $compiler /nologo /target:winexe /reference:System.Web.Extensions.dll /out:engine\LaunchWorker.exe engine\LaunchWorker.cs
    if ($LASTEXITCODE) { throw 'LaunchWorker build failed.' }
    python -m PyInstaller --noconfirm --onefile --windowed --name '显存控制台' --noupx --add-binary 'engine/CommandTrayHost.exe;engine' --add-binary 'engine/LaunchWorker.exe;engine' --add-data 'engine/LICENSE-CommandTrayHost.txt;engine' --add-data 'gputaskman/LICENSE;gputaskman' console.py
    if ($LASTEXITCODE) { throw 'PyInstaller build failed.' }
} finally {
    Pop-Location
}
