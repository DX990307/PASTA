#!/usr/bin/env python3
"""Portable frozen experiment runner with durable workers and restart adoption."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

def read(p):
    return json.loads(p.read_text())

def write(p, data):
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(p)

def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()

def available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1048576
    raise RuntimeError('MemAvailable missing')

def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()
        return {'start': fields[19], 'state': fields[0]}
    except FileNotFoundError:
        return None

def worker(directory):
    job = read(directory / 'job.json')
    with (directory / 'claim').open('x'):
        pass
    started = time.time()
    with (directory / 'stdout.log').open('w') as log:
        child = subprocess.Popen(job['command'], cwd=directory, stdout=log,
                                 stderr=subprocess.STDOUT,
                                 env=os.environ | {'GODEBUG': 'randautoseed=0'})
        write(directory / 'state.json', {'status': 'running', 'pid': child.pid,
              'identity': identity(child.pid), 'started_at': started})
        code = child.wait()
        write(directory / 'completion.json', {'returncode': code,
              'wall_seconds': time.time() - started,
              'peak_rss_gib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576,
              'job_sha256': digest(directory / 'job.json'),
              'ended_at': time.time(), 'qualification': 'pending strict central validation'})
        write(directory / 'state.json', {'status': 'exited' if code == 0 else 'failed',
              'returncode': code, 'ended_at': time.time()})

def run(machine, transferred, check):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('Frozen binaries require Linux x86_64')
    manifest = read(ROOT / 'plans' / (machine + '.json'))
    for name, sha in manifest['binaries'].items():
        p = ROOT / 'bin' / name
        if digest(p) != sha:
            raise RuntimeError('Binary checksum mismatch: ' + name)
        p.chmod(p.stat().st_mode | 0o111)
    for job in manifest['jobs']:
        cmd = job['command']
        if '-disable-servers' in cmd or '-max-wg=76800' not in cmd:
            raise RuntimeError('Invalid formal configuration')
    if check:
        print(json.dumps({'machine': machine, 'jobs': len(manifest['jobs']),
                          'workers': manifest['workers'], 'memavailable_gib': available(),
                          'binaries_verified': True}, indent=2))
        return
    if not transferred:
        raise RuntimeError('First remove these logical jobs from the source scheduler; then use --ownership-transferred')
    output = ROOT / 'results' / machine
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / 'scheduler.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    launched = 0
    while True:
        counts = {'queued': 0, 'running': 0, 'exited': 0, 'failed': 0, 'unverified': 0}
        candidate = None
        for job in manifest['jobs']:
            directory = output / job['id']
            state = read(directory / 'state.json') if (directory / 'state.json').exists() else None
            if (directory / 'completion.json').exists():
                status = 'exited' if read(directory / 'completion.json')['returncode'] == 0 else 'failed'
            elif state and state.get('status') == 'running':
                current = identity(state['pid'])
                status = 'running' if current and current['start'] == state['identity']['start'] and current['state'] not in ('Z', 'X') else 'unverified'
            elif (directory / 'claim').exists() or (directory / 'launch.json').exists():
                status = 'unverified'
            else:
                status = 'queued'
                if candidate is None:
                    candidate = job
            counts[status] += 1
        free = available()
        write(output / 'status.json', {'updated_at': time.time(), 'counts': counts,
              'memavailable_gib': free, 'workers': manifest['workers']})
        if counts['queued'] == 0 and counts['running'] == 0:
            print(json.dumps(counts), flush=True)
            return
        if candidate and counts['running'] < manifest['workers'] and free >= 30 + candidate['estimated_peak_gib'] and time.monotonic() - launched >= 60:
            directory = output / candidate['id']
            directory.mkdir(exist_ok=True)
            job = dict(candidate)
            cmd = [str(ROOT / 'bin' / Path(candidate['command'][0]).name)]
            cmd += [('-metric-file-name=' + str(directory / 'metrics')) if a.startswith('-metric-file-name=') else a for a in candidate['command'][1:]]
            job['command'] = cmd
            write(directory / 'job.json', job)
            write(directory / 'launch.json', {'at': time.time(), 'memavailable_gib': free})
            with (directory / 'worker.log').open('a') as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(directory)],
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            launched = time.monotonic()
            print('LAUNCH ' + candidate['id'], flush=True)
        time.sleep(5)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--machine', choices=['machine15', 'machine5'])
    p.add_argument('--ownership-transferred', action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--worker', type=Path)
    a = p.parse_args()
    if a.worker:
        worker(a.worker)
    else:
        if not a.machine:
            p.error('--machine required')
        run(a.machine, a.ownership_transferred, a.check)
