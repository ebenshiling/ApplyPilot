param([string]$Manifest, [string]$Output)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.SelectVoice('Microsoft Zira Desktop')
$speaker.Rate = 0
$speaker.Volume = 100
$scenes = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($scene in $scenes) {
    $file = Join-Path $Output ('voice-{0:D2}.wav' -f [int]$scene.index)
    $speaker.SetOutputToWaveFile($file)
    $speaker.Speak([string]$scene.narration)
    $speaker.SetOutputToNull()
}
$speaker.Dispose()
