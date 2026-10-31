# Checks a fresh Windows install of Void on a GitHub test machine. Prints PASS/FAIL lines; fails the job on any FAIL.
$ErrorActionPreference = "Continue"
$ROOT = Join-Path $env:LOCALAPPDATA "void"; $APP = Join-Path $ROOT "app"; $VPY = Join-Path $ROOT "venv\Scripts\python.exe"
$OUT = Join-Path $env:GITHUB_WORKSPACE "ci-out"; New-Item -ItemType Directory -Force $OUT | Out-Null
$env:PYTHONUTF8 = "1"
$script:fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name $detail" } else { Write-Host "FAIL  $name $detail"; $script:fails++ } }

& $VPY -c "import PySide6, sounddevice, PIL, faster_whisper, kokoro_onnx, pycaw; print('ok')" | Out-Null
Check "python libraries import" ($LASTEXITCODE -eq 0)
Check "void.cmd" (Test-Path (Join-Path $ROOT "bin\void.cmd"))
Check "Start menu shortcut" (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Void.lnk"))
Check "start at login" (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Void.lnk"))
Check "Kokoro voice files" ((Test-Path "$ROOT\voices\kokoro\kokoro-v1.0.int8.onnx") -and (Test-Path "$ROOT\voices\kokoro\voices-v1.0.bin"))

# a pretend Claude, so a whole request can run
$fake = Join-Path $env:GITHUB_WORKSPACE ".github\ci\fakeclaude.py"
Set-Content -Encoding ASCII "$ROOT\fakeclaude.cmd" "@echo off`r`n`"$VPY`" `"$fake`" %*"
New-Item -ItemType Directory -Force (Join-Path $env:APPDATA "void") | Out-Null
Set-Content -Encoding UTF8 (Join-Path $env:APPDATA "void\config.json") ('{"claude_bin": "' + ("$ROOT\fakeclaude.cmd" -replace '\\','\\') + '", "wake_word": false, "stt_prewarm": false, "trust": "trust"}')

& "$ROOT\bin\void.cmd" show
for ($i = 0; $i -lt 30 -and -not (Test-Path "$ROOT\window.port"); $i++) { Start-Sleep 1 }   # (slow test machines)
Start-Sleep 4
try { $st = Invoke-RestMethod http://127.0.0.1:47777/api/state -Headers @{"X-Void-Token" = (Get-Content "$ROOT\runtime.json" | ConvertFrom-Json).token}
      Check "server answers" $true "(voice: $($st.tts))" } catch { Check "server answers" $false "$_" }
Check "window running" (Test-Path "$ROOT\window.port") "(port file)"

$tok = (Get-Content "$ROOT\runtime.json" | ConvertFrom-Json).token
try {
  $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://127.0.0.1:47777/api/ask -Headers @{"X-Void-Token" = $tok} -ContentType "application/json" -Body '{"prompt":"hello"}'
  $body = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
  Check "a request through Claude" ($body -match "Hi from Windows") ""
  $said = ($body -split "`n" | Where-Object { $_ -like "data: *" } | ForEach-Object { try { ($_.Substring(6) | ConvertFrom-Json).text } catch {} }) -join " "
  Check "unicode survives" ($said -match [regex]::Escape("ünïcödé ✓")) ""
  Check "tool shows on his terminal" ($body -match '"t": "tool"') ""
} catch { Check "a request through Claude" $false "$_" }

# security: the window ignores commands that don't carry its secret (any program can reach a local port)
$wport = (Get-Content "$ROOT\window.port").Split(" ")[0]
$tcp = New-Object Net.Sockets.TcpClient("127.0.0.1", [int]$wport); $st2 = $tcp.GetStream(); $msg = [Text.Encoding]::ASCII.GetBytes("quit"); $st2.Write($msg, 0, $msg.Length); $tcp.Close()
Start-Sleep 2
Check "window ignores a command without its secret" ([bool](Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*window_win.py*" })) ""

# security: the page and /api/state give nothing away; the token Claude's commands get can't take over
$rt = Get-Content "$ROOT\runtime.json" | ConvertFrom-Json
$page = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:47777/).Content
Check "the page has no token in it" (-not ($page.Contains($rt.token) -or $page.Contains($rt.tool_token))) ""
Check "/api/state without the token is just the version" ((Invoke-RestMethod http://127.0.0.1:47777/api/state).PSObject.Properties.Name -join "," -eq "version") ""
$blocked = $false
try { Invoke-RestMethod -Method Post http://127.0.0.1:47777/api/settings -Headers @{"X-Void-Token" = $rt.tool_token} -ContentType "application/json" -Body '{"trust":"trust"}' | Out-Null } catch { $blocked = $_.Exception.Response.StatusCode.value__ -eq 403 }
Check "Claude's token can't switch on 'Just do it'" $blocked ""

try {
  $d = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47777/api/diagnostics -Headers @{"X-Void-Token" = $tok} -ContentType "application/json" -Body "{}").text
  Set-Content -Encoding utf8 "$OUT\diagnostics.txt" $d
  Check "diagnostics report" ($d -match "Void diagnostics" -and $d -match "claude code") ""
  Check "diagnostics hide your folders, name and token" (-not ($d.Contains($env:USERPROFILE) -or $d.Contains($tok) -or $d -match "(?<![A-Za-z0-9])$([regex]::Escape($env:USERNAME))(?![A-Za-z0-9])")) ""
} catch { Check "diagnostics report" $false "$_" }

$t = & $VPY -c @"
import sys; sys.path.insert(0, r'$APP'); import winsys, time
winsys.dpi_aware()
print('mics', len(winsys.list_mics())); print('play', winsys.can_play())
print('screen', winsys.screenshot('screen', r'$OUT\desktop.png'))
print('window', winsys.screenshot('window', r'$OUT\active-window.png'))
print('volume', winsys.volume_get())
winsys.notify('Void', 'test notification'); time.sleep(3)
"@
Write-Host $t
Check "screenshots" (Test-Path "$OUT\desktop.png")
Set-Clipboard -Value "copied on windows"
$c = & $VPY -c "import sys; sys.path.insert(0, r'$APP'); import winsys; print(winsys.clipboard(r'$OUT\clip.png'))"
Check "clipboard" ($c -match "copied on windows") "$c"

& $VPY (Join-Path $APP "window_win.py") "http://127.0.0.1:47777/?native=1" --selftest "$OUT\face.png" 2>&1 | Out-Host
Check "his window renders" (Test-Path "$OUT\face.png")

# the top bar: dock him on an edge, check where the window went and that the page turned into the notch
foreach ($edge in "top", "left", "right", "bottom") {
  $line = (& $VPY (Join-Path $APP "window_win.py") "http://127.0.0.1:47777/?native=1" --selftest-bar $edge "$OUT\bar-$edge.png" 2>$null | Where-Object { $_ -like "selftest-bar:*" }) | Select-Object -Last 1
  Write-Host $line
  $ok = $false
  if ($line -match "x=(-?\d+) y=(-?\d+) w=(\d+) h=(\d+) area=(-?\d+),(-?\d+),(\d+),(\d+) page=(.*)$") {
    $x, $y, $w, $h = [int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]$Matches[4]
    $ax, $ay, $aw, $ah, $cls = [int]$Matches[5], [int]$Matches[6], [int]$Matches[7], [int]$Matches[8], $Matches[9]
    $cx = $ax + ($aw - $w) / 2; $cy = $ay + ($ah - $h) / 2
    $where = switch ($edge) {
      "top"    { [math]::Abs($x - $cx) -le 2 -and $y -eq $ay }
      "bottom" { [math]::Abs($x - $cx) -le 2 -and [math]::Abs(($y + $h) - ($ay + $ah)) -le 1 }
      "left"   { $x -eq $ax -and [math]::Abs($y - $cy) -le 2 }
      "right"  { [math]::Abs(($x + $w) - ($ax + $aw)) -le 1 -and [math]::Abs($y - $cy) -le 2 } }
    $ok = $where -and $cls -match "\bbar\b" -and $cls -match "\be-$edge\b"
  }
  Check "top bar docks on the $edge edge" $ok ""
}
$st3 = Invoke-RestMethod http://127.0.0.1:47777/api/state -Headers @{"X-Void-Token" = $tok}
Check "the bar is remembered" ($st3.presence -eq "bar" -and $st3.bar_edge -eq "bottom") "($($st3.presence) / $($st3.bar_edge))"
Invoke-RestMethod -Method Post http://127.0.0.1:47777/api/settings -Headers @{"X-Void-Token" = $tok} -ContentType "application/json" -Body '{"presence":"sideways","bar_edge":"middle"}' | Out-Null
$st3 = Invoke-RestMethod http://127.0.0.1:47777/api/state -Headers @{"X-Void-Token" = $tok}
Check "junk bar settings are ignored" ($st3.presence -eq "bar" -and $st3.bar_edge -eq "bottom") ""
Invoke-RestMethod -Method Post http://127.0.0.1:47777/api/settings -Headers @{"X-Void-Token" = $tok} -ContentType "application/json" -Body '{"presence":"orb"}' | Out-Null

# the push-to-talk hotkey: press and hold Win+Space (the default) like a person would, then let go
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Keys {
  [StructLayout(LayoutKind.Sequential)] public struct KI { public ushort vk, scan; public uint flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Explicit, Size = 40)] public struct INPUT { [FieldOffset(0)] public uint type; [FieldOffset(8)] public KI ki; }
  [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] i, int size);
  public static uint Key(ushort vk, bool up) { var i = new INPUT[1]; i[0].type = 1; i[0].ki.vk = vk; i[0].ki.flags = up ? 2u : 0u; return SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); }
}
"@
[Keys]::Key(0x5B, $false) | Out-Null; [Keys]::Key(0x20, $false) | Out-Null
Start-Sleep 1
[Keys]::Key(0x20, $true) | Out-Null; [Keys]::Key(0x5B, $true) | Out-Null
Start-Sleep 2
# (the whole log: the window and the server write it through separate handles, so new lines can land anywhere)
$new = (Get-Content "$ROOT\server.log") -join "`n"
Check "push-to-talk hotkey: held" ($new -match "push-to-talk: down") ""
Check "push-to-talk hotkey: let go" ($new -match "push-to-talk: up") ""

& "$ROOT\bin\void.cmd" stop
Start-Sleep 2
$alive = $false; try { Invoke-RestMethod http://127.0.0.1:47777/api/state -TimeoutSec 2 | Out-Null; $alive = $true } catch {}
Check "void stop" (-not $alive)
Copy-Item "$ROOT\server.log" $OUT -ErrorAction SilentlyContinue
Write-Host "`n$script:fails failure(s)"
exit $script:fails
