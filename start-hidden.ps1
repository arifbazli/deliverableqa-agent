# Startup-folder launcher for server.py.
#
# PowerShell's own `-WindowStyle Hidden` CLI flag is well known to be
# unreliable at logon time (the window can flash visible, or the hide can
# silently fail, before the shell/desktop is fully ready) -- confirmed on
# this project: it worked every time when tested via a manual double-click,
# but showed a persistent visible window at every real reboot.
#
# The fix isn't a better hide flag -- it's removing every intermediate
# console process that could carry a window for the server's whole
# lifetime. This script's only job is to hand off to Start-Process, which
# launches uv.exe DIRECTLY (no wrapping powershell.exe in between) using its
# own -WindowStyle Hidden -- an OS-level flag set at process-creation time,
# not a hide-after-the-fact CLI argument -- then this script exits
# immediately. Even if this script's own window flashes visible for a
# moment, it's gone in well under a second, and closing it (or any window
# tied to it) can't affect the server: Start-Process does not link the
# child's lifetime to this script's, so uv.exe (and the python.exe it
# starts) keeps running as its own independent process regardless.
$repoRoot = $PSScriptRoot

Start-Process -FilePath "uv" `
    -ArgumentList @("run", "python", "server.py") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $repoRoot "server.log") `
    -RedirectStandardError (Join-Path $repoRoot "server-error.log")
