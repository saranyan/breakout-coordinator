"""On-demand startup and honest host diagnostics. Hooks never start services."""
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
from .daemon import request, PROTOCOL_VERSION
from .providers import doctor


def ensure(home, upgrade=True):
    try:
        status = request('ping', home)
    except PermissionError:
        raise
    except (FileNotFoundError, ConnectionRefusedError):
        status = None
    if status and status.get('protocol_version') == PROTOCOL_VERSION:
        return status
    if status:
        if not upgrade:
            raise ValueError('Coordinator protocol mismatch; update/restart while idle')
        runs = request('list', home)
        if status.get('busy') or any(r['status'] in ('working', 'paused') for r in runs):
            raise ValueError('Coordinator needs an upgrade but has active or paused runs; finish or reconcile them first. No run was interrupted.')
        # Verify this host can maintain the runtime BEFORE stopping a healthy service.
        with tempfile.TemporaryFile(dir=Path(home).expanduser().resolve()):
            pass
        request('shutdown', home)
        for _ in range(50):
            try:
                request('ping', home, timeout=.2)
            except (FileNotFoundError, ConnectionRefusedError):
                break
            time.sleep(.1)
        else:
            raise ValueError('Old coordinator is still stopping; retry once it exits')
    root = Path(home).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = root / 'daemon.log'
    with log_path.open('a') as log:
        log_path.chmod(0o600)
        proc = subprocess.Popen([sys.executable, '-m', 'breakout', '--home', str(root), 'daemon', 'serve'],
                                cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        try:
            result = request('ping', root, timeout=.2)
            if result.get('protocol_version') != PROTOCOL_VERSION:
                raise ValueError('A different daemon acquired the socket; check protocol version')
            return result
        except (FileNotFoundError, ConnectionRefusedError):
            if proc.poll() is not None:
                raise ValueError(f'Coordinator could not start; inspect {log_path}')
            time.sleep(.1)
    raise ValueError(f'Coordinator startup timed out; inspect {log_path}')


def host_report(home):
    report = dict(providers=doctor(), sandbox={}, coordinator={})
    try:
        report['coordinator'] = request('ping', home, timeout=1)
        report['sandbox'] = {'socket_access': True}
        if report['coordinator'].get('protocol_version') != PROTOCOL_VERSION:
            report['coordinator']['fix'] = 'Upgrade while idle with breakout daemon start'
    except PermissionError:
        report['sandbox'] = {'socket_access': False, 'fix': 'Allow this host to reach the coordinator socket in its permission profile'}
        report['coordinator'] = {'health': 'unknown from this sandbox'}
    except (FileNotFoundError, ConnectionRefusedError):
        report['coordinator'] = {'status': 'not reachable', 'fix': 'breakout daemon start (launch also starts on demand)'}
        report['sandbox'] = {'socket_access': 'no permission denial observed'}
    return report
