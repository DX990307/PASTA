"""Start this PLT campaign only after the entire specified PTW campaign ends."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
import signal
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
TERMINAL = {'completed', 'failed', 'interrupted'}

def inspect(current):
    manifest = json.loads((current / 'manifest.json').read_text())
    if len(manifest['jobs']) != 84:
        raise RuntimeError('Expected the current 84-job PTW sweep')
    states = []
    for job in manifest['jobs']:
        path = current / 'results' / job['id'] / 'state.json'
        states.append(json.loads(path.read_text()) if path.exists() else {'status': 'queued'})
    counts = dict(Counter(s.get('status', 'queued') for s in states))
    live = []
    for s in states:
        for key in ['pid', 'worker_pid']:
            pid = s.get(key)
            if not isinstance(pid, int):
                continue
            try:
                args = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
                if str(current) + '/' in args and ('simulator' in args or 'remote_campaign_runner.py' in args):
                    live.append(pid)
            except OSError:
                pass
    ready = all(s.get('status') in TERMINAL for s in states) and not live
    return {'counts': counts, 'live_pids': sorted(set(live)), 'ready': ready}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=17)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    current = args.current.resolve()
    if args.check_only:
        print(json.dumps(inspect(current), indent=2))
        return
    with (ROOT / 'after-current.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            state = {'checked_at': datetime.now(timezone.utc).isoformat(), 'current_package': str(current), **inspect(current)}
            temporary = ROOT / 'after-current-status.json.tmp'
            temporary.write_text(json.dumps(state, indent=2) + '\n')
            temporary.replace(ROOT / 'after-current-status.json')
            print(json.dumps(state), flush=True)
            if state['ready']:
                # PLT may already be running at low concurrency alongside PTW.
                # Stop its admission supervisor, retaining independent workers,
                # then resume with the requested full concurrency.
                supervisors = []
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdigit():
                        continue
                    try:
                        cmd = [x.decode(errors='replace') for x in (proc / 'cmdline').read_bytes().split(b'\0') if x]
                        cwd = Path(os.readlink(proc / 'cwd'))
                        if cwd == ROOT and any(x.endswith('remote_campaign_runner.py') for x in cmd) and 'run' in cmd:
                            supervisors.append(int(proc.name))
                    except OSError:
                        pass
                if len(supervisors) > 1:
                    raise RuntimeError('Multiple PLT supervisors found; refusing to scale')
                for pid in supervisors:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(120):
                        try:
                            if not Path(f'/proc/{pid}/cmdline').read_bytes():
                                break
                        except OSError:
                            break
                        time.sleep(0.25)
                    else:
                        raise RuntimeError('PLT supervisor did not stop; workers retained')
                with (ROOT / 'results' / 'supervisor.log').open('a') as output:
                    subprocess.run(['python3', str(ROOT / 'remote_campaign_runner.py'), 'run', '--groups', 'PTW', '--workers', str(args.workers)], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
                return
            time.sleep(30)

if __name__ == '__main__':
    main()
