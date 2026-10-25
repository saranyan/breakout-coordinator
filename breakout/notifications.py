"""Best-effort local completion notice; not a session delivery acknowledgment."""
import subprocess
import sys


def notify(run_id):
    if sys.platform != 'darwin':
        return 'unavailable on this platform; use mailbox'
    script = ('on run argv\n'
              'display notification ("Review ready: " & item 1 of argv) with title "Breakout"\n'
              'end run')
    try:
        result = subprocess.run(['osascript', '-e', script, run_id], capture_output=True, timeout=2)
        return 'submitted to macOS' if result.returncode == 0 else 'unavailable; use mailbox'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable; use mailbox'
