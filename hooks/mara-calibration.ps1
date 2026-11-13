# MARA calibration loader. Runs on every SessionStart (startup, resume, clear, compact).
# Puts the matching model calibration and the rules into context so the model never
# has to decide whether to read them. A failure here must never block session start.

$ErrorActionPreference = 'Stop'

try {
    $claudeRoot = 'C:\Users\shawn\.claude'
    $modelDir   = Join-Path $claudeRoot 'models'
    $rulesDir   = Join-Path $claudeRoot 'rules'
    $allModels  = @('opus-5.5.md', 'opus-4.6.md', 'sonnet-5.5.md', 'fable-5.2.md', 'haiku-5.5.md')
    $ruleFiles  = @()  # rules already load via CLAUDE.md; injecting them pushed output past the size cap

    function Get-ModelFile([string]$name) {
        if (-not $name) { return $null }
        $n = $name.ToLowerInvariant()
        if ($n -match 'haiku')        { return 'haiku-5.5.md' }
        if ($n -match 'sonnet')       { return 'sonnet-5.5.md' }
        if ($n -match 'fable')        { return 'fable-5.2.md' }
        if ($n -match 'opus-?4[-.]6') { return 'opus-4.6.md' }
        if ($n -match 'opus-?5')      { return 'opus-5.5.md' }
        return $null
    }

    $raw = ''
    try {
        $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
        $raw = $reader.ReadToEnd()
    } catch {}

    $source = ''
    $model  = ''
    try {
        if ($raw -and $raw.Trim()) {
            $payload = $raw | ConvertFrom-Json
            if ($payload.source) { $source = [string]$payload.source }
            if ($payload.model -is [string]) { $model = $payload.model }
            elseif ($payload.model -and $payload.model.id) { $model = [string]$payload.model.id }
        }
    } catch {}

    $matched = Get-ModelFile $model
    if (-not $matched) {
        foreach ($cand in @($env:ANTHROPIC_MODEL, $env:CLAUDE_MODEL)) {
            $m = Get-ModelFile $cand
            if ($m) { $model = $cand; $matched = $m; break }
        }
    }
    if (-not $matched) {
        try {
            $cfg = Get-Content -LiteralPath (Join-Path $claudeRoot 'settings.json') -Raw | ConvertFrom-Json
            $m = Get-ModelFile ([string]$cfg.model)
            if ($m) { $model = [string]$cfg.model; $matched = $m }
        } catch {}
    }

    if ($matched) {
        $modelFiles = @($matched)
        $header = "Model identified as '$model'. Apply models/$matched below."
    } else {
        # Never dump all five model files: 30KB+ gets spilled to a side file and never reaches context.
        $modelFiles = @()
        $header = @'
Model not identified by the hook. Your exact model is named in your system prompt ("You are powered by the model named X" / "The exact model ID is Y").
Before your first reply, Read the matching file:
  claude-opus-5-5 / Opus 5.5     -> C:\Users\shawn\.claude\models\opus-5.5.md
  claude-opus-4-6 / Opus 4.6     -> C:\Users\shawn\.claude\models\opus-4.6.md
  claude-sonnet-5-5 / Sonnet 5.5 -> C:\Users\shawn\.claude\models\sonnet-5.5.md
  claude-haiku-5-5 / Haiku 5.5   -> C:\Users\shawn\.claude\models\haiku-5.5.md
  claude-fable-5-2 / Fable 5.2   -> C:\Users\shawn\.claude\models\fable-5.2.md
  no match                       -> C:\Users\shawn\.claude\models\haiku-5.5.md
Also Read C:\Users\shawn\OneDrive\Documents\fun\mara-preferences.txt.
'@
    }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("MARA CALIBRATION, loaded by the SessionStart hook (source: '$source').")
    [void]$sb.AppendLine($header)
    [void]$sb.AppendLine('Also read the matching calibration file and every rules file with the Read tool, every start, resume, clear and compaction, no matter what the request is. The copy below does not replace that read.')
    [void]$sb.AppendLine('')

    foreach ($f in $modelFiles) {
        $p = Join-Path $modelDir $f
        if (Test-Path -LiteralPath $p) {
            [void]$sb.AppendLine("===== models/$f =====")
            [void]$sb.AppendLine((Get-Content -LiteralPath $p -Raw -Encoding UTF8))
        }
    }
    foreach ($f in $ruleFiles) {
        $p = Join-Path $rulesDir $f
        if (Test-Path -LiteralPath $p) {
            [void]$sb.AppendLine("===== rules/$f =====")
            [void]$sb.AppendLine((Get-Content -LiteralPath $p -Raw -Encoding UTF8))
        }
    }

    $json = @{
        hookSpecificOutput = @{
            hookEventName     = 'SessionStart'
            additionalContext = $sb.ToString()
        }
    } | ConvertTo-Json -Depth 5 -Compress

    $bytes  = (New-Object System.Text.UTF8Encoding($false)).GetBytes($json)
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($bytes, 0, $bytes.Length)
    $stdout.Flush()
} catch {
    # Never block session start over a calibration load failure.
}
exit 0
