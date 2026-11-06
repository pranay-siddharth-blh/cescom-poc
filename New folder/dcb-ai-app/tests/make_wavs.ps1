# Speaks a list of phrases to 16 kHz mono WAV files with the Windows voices (including the Indian-English OneCore
# voices Heera and Ravi). Input: a JSON file [{ "path": "...", "voice": "Ravi", "text": "..." }, ...].
# Usage: powershell -File make_wavs.ps1 -List phrases.json
param([Parameter(Mandatory = $true)][string]$List)
$items = Get-Content -Raw -Encoding UTF8 $List | ConvertFrom-Json
$v = New-Object -ComObject SAPI.SpVoice
$cat = New-Object -ComObject SAPI.SpObjectTokenCategory
$cat.SetId('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices', $false)
$tokens = @{}
$all = $cat.EnumerateTokens()
for ($i = 0; $i -lt $all.Count; $i++) { $t = $all.Item($i); $tokens[($t.GetDescription() -split ' ')[1]] = $t }
$done = 0
foreach ($it in $items) {
    if (Test-Path $it.path) { $done++; continue }
    $v.Voice = $tokens[$it.voice]
    $fmt = New-Object -ComObject SAPI.SpAudioFormat
    $fmt.Type = 18                      # SAFT16kHz16BitMono
    $fs = New-Object -ComObject SAPI.SpFileStream
    $fs.Format = $fmt
    $fs.Open($it.path, 3, $false)
    $v.AudioOutputStream = $fs
    [void]$v.Speak($it.text)
    $fs.Close()
    $done++
}
"$done files"
