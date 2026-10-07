#!/usr/bin/env python3
"""Portable frozen experiment runner with durable workers and restart adoption."""
import argparse
import csv
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

def command_hash(command):
    return hashlib.sha256(json.dumps(command, separators=(',', ':')).encode()).hexdigest()

def load_reuse(manifest, machine='r2-r3'):
    policy = read(ROOT / 'plans' / (machine + '-reuse.json'))
    if policy.get('schema') != 1 or policy['manifest_sha256'] != digest(ROOT / 'plans' / (machine + '.json')):
        raise RuntimeError('Reuse policy belongs to a different frozen plan')
    jobs = {job['id']: job for job in manifest['jobs']}
    controls = {key for key, job in jobs.items() if job['config'] in ('baseline16', 'pasta16', 'm1_demand_only')}
    if machine == 'r2-r3' and set(policy['refs']) != controls:
        raise RuntimeError('Reuse policy must assign every existing control exactly once')
    for identity, ref in policy['refs'].items():
        job = jobs.get(identity)
        if (job is None or (machine == 'r2-r3' and job['config'] not in ('baseline16', 'pasta16', 'm1_demand_only'))
                or ref['target_command_sha256'] != command_hash(job['command'])
                or ref['disposition'] not in ('reuse_external', 'external_pending')
                or not ref.get('source_batch') or not ref.get('source_id')):
            raise RuntimeError('Invalid reference assignment: ' + identity)
        if ref['disposition'] == 'reuse_external' and (ref['source_status_at_audit'] != 'completed' or not ref.get('evidence_files')):
            raise RuntimeError('Reuse has no qualified completed source: ' + identity)
    return policy['refs']

def execution_status(directory, reference=None):
    # Preserve previously started remote attempts even when a new reuse policy
    # makes another source canonical; never kill or overwrite their evidence.
    if (directory / 'completion.json').exists():
        return 'exited' if read(directory / 'completion.json')['returncode'] == 0 else 'failed'
    state = read(directory / 'state.json') if (directory / 'state.json').exists() else None
    if state and state.get('status') == 'running':
        current = identity(state['pid'])
        return 'running' if current and state.get('identity') and current['start'] == state['identity']['start'] and current['state'] not in ('Z', 'X') else 'unverified'
    if (directory / 'claim').exists() or (directory / 'launch.json').exists():
        return 'unverified'
    if reference:
        return reference['disposition']
    return 'queued'

def flags(command):
    result = {}
    for arg in command[1:]:
        name, sep, value = arg.partition('=')
        if not name.startswith('-') or name in result:
            raise RuntimeError('Invalid or duplicate flag: ' + arg)
        result[name] = value if sep else 'true'
    return result

def validate_r23(manifest):
    variants = {'baseline16', 'baseline_estimated20', 'pasta16', 'm1_demand_only',
                'neighbor_abstract', 'latpc_simple'}
    expected = {(b, v) for b in manifest['benchmarks'] for v in variants}
    found = {(j['benchmark'], j['config']) for j in manifest['jobs']}
    if len(manifest['benchmarks']) != 14 or len(expected) != 84 or found != expected or len(manifest['jobs']) != 84:
        raise RuntimeError('R2/R3 FULL14 matrix is incomplete or duplicated')
    if len({j['id'] for j in manifest['jobs']}) != 84:
        raise RuntimeError('Duplicate R2/R3 job IDs')
    for job in manifest['jobs']:
        f = flags(job['command'])
        v = job['config']
        for key, value in {'-max-wg': '76800', '-num-gpms': '48',
                           '-iommu-mshr-entries': '64', '-gmmu-ptw-count': '4',
                           '-iommu-ptw-count': '16', '-log2-page-size': '12',
                           '-iommu-fixed-walk-latency': '0', '-mmu-walk-coalescing': 'false',
                           '-gmmu-mshr-entries': '20' if v == 'baseline_estimated20' else '16',
                           '-benchmark': job['benchmark'],
                           '-neighbor-abstract': 'full' if v == 'neighbor_abstract' else 'off',
                           '-latpc-simple': 'true' if v == 'latpc_simple' else 'false'}.items():
            # These are frozen, CPU-validated false defaults in v13.
            default = 'false' if key == '-mmu-walk-coalescing' else None
            if f.get(key, default) != value:
                raise RuntimeError(f'{job["id"]}: incorrect {key}')
        if '-disable-servers' in f or job.get('akita_rtm_required') is not True:
            raise RuntimeError('AkitaRTM must remain enabled')
        if v in ('neighbor_abstract', 'latpc_simple'):
            for key in ('-gmmu-vpn-mshr-baseline', '-mmutlb-vpn-mshr-baseline',
                        '-ptw-demand-pte-only', '-mmutlb-demand-pte-only'):
                if f.get(key) != 'true':
                    raise RuntimeError('R3 must use ordinary VPN/demand-only Baseline')
            for key in ('-gmmu-flex-tlb', '-mmutlb-flex-tlb', '-gmmu-idle-iommu-assist', '-gmmu-initial-ptcl-mode'):
                if f.get(key, 'false') != 'false':
                    raise RuntimeError('R3 cannot mix PASTA and SOTA flags')

def summary(output, manifest, refs=None):
    refs = refs or {}
    rows = []
    for job in manifest['jobs']:
        directory = output / job['id']
        completion = read(directory / 'completion.json') if (directory / 'completion.json').exists() else {}
        driver = ''
        metrics = directory / 'metrics.csv'
        if completion.get('returncode') == 0 and metrics.exists():
            with metrics.open() as handle:
                for row in csv.reader(handle):
                    if len(row) >= 4 and row[1].strip() == 'Driver' and row[2].strip() == 'total_time':
                        driver = row[3].strip()
        reference = refs.get(job['id'], {})
        status = execution_status(directory, reference)
        external = status in ('reuse_external', 'external_pending')
        rows.append({'id': job['id'], 'benchmark': job['benchmark'], 'config': job['config'],
                     'execution_status': status,
                     'returncode': completion.get('returncode', ''), 'driver_time_s': driver,
                     'wall_seconds': completion.get('wall_seconds', ''),
                     'source_batch': reference.get('source_batch', ''), 'source_id': reference.get('source_id', ''),
                     'qualification': reference['qualification'] if external else completion.get('qualification', 'not completed')})
    temp = output / 'summary.csv.tmp'
    with temp.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(output / 'summary.csv')

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
              'identity': identity(child.pid), 'started_at': started,
              'runtime_env_overrides': {'GODEBUG': 'randautoseed=0'},
              'binary_sha256': digest(Path(job['command'][0])),
              'job_sha256': digest(directory / 'job.json')})
        code = child.wait()
        write(directory / 'completion.json', {'returncode': code,
              'wall_seconds': time.time() - started,
              'peak_rss_gib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576,
              'job_sha256': digest(directory / 'job.json'),
              'ended_at': time.time(), 'qualification': 'pending strict central validation'})
        write(directory / 'state.json', {'status': 'exited' if code == 0 else 'failed',
              'returncode': code, 'ended_at': time.time()})

def run(machine, transferred, check, workers=None):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('Frozen binaries require Linux x86_64')
    manifest = read(ROOT / 'plans' / (machine + '.json'))
    if workers is not None:
        if not 1 <= workers <= 15:
            raise RuntimeError('Workers must be between 1 and 15')
        manifest['workers'] = workers
    if machine == 'r2-r3':
        validate_r23(manifest)
        provenance = read(ROOT / 'provenance/r23-simple-v13.json')
        if (provenance.get('passed') is not True
                or provenance['manifest_sha256'] != digest(ROOT / 'plans/r2-r3.json')
                or provenance['archive_sha256'] != digest(ROOT / 'sources/source-r23-simple-v13.tar.gz')
                or manifest['binaries'].get('simulator-r23-simple-v13') != provenance['binary_sha256']):
            raise RuntimeError('R2/R3 validation or source provenance mismatch')
    for name, sha in manifest['binaries'].items():
        p = ROOT / 'bin' / name
        if digest(p) != sha:
            raise RuntimeError('Binary checksum mismatch: ' + name)
        p.chmod(p.stat().st_mode | 0o111)
    for job in manifest['jobs']:
        cmd = job['command']
        if '-disable-servers' in cmd or '-max-wg=76800' not in cmd:
            raise RuntimeError('Invalid formal configuration')
    refs = load_reuse(manifest, machine)
    if check:
        print(json.dumps({'machine': machine, 'jobs': len(manifest['jobs']),
                          'new_remote_jobs': len(manifest['jobs']) - len(refs),
                          'completed_external_references': sum(r['disposition'] == 'reuse_external' for r in refs.values()),
                          'pending_external_references': sum(r['disposition'] == 'external_pending' for r in refs.values()),
                          'workers': manifest['workers'], 'memavailable_gib': available(),
                          'binaries_verified': True}, indent=2))
        return
    if not transferred:
        raise RuntimeError('First remove these logical jobs from the source scheduler; then use --ownership-transferred')
    output = ROOT / 'results' / machine
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / 'scheduler.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if machine == 'r2-r3':
        plan_identity = output / 'plan_identity.json'
        current = {'manifest_sha256': digest(ROOT / 'plans' / 'r2-r3.json')}
        if plan_identity.exists() and read(plan_identity) != current:
            raise RuntimeError('This results directory belongs to a different frozen plan')
        if not plan_identity.exists():
            write(plan_identity, current)
    launched = 0
    while True:
        refs = load_reuse(manifest, machine)
        counts = {'queued': 0, 'running': 0, 'exited': 0, 'failed': 0, 'unverified': 0,
                  'reuse_external': 0, 'external_pending': 0}
        candidate = None
        for job in manifest['jobs']:
            directory = output / job['id']
            status = execution_status(directory, refs.get(job['id']))
            if status == 'queued':
                if candidate is None:
                    candidate = job
            counts[status] += 1
        free = available()
        write(output / 'status.json', {'updated_at': time.time(), 'counts': counts,
              'memavailable_gib': free, 'workers': manifest['workers']})
        summary(output, manifest, refs)
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
    p.add_argument('--machine', choices=['machine15', 'machine5', 'r2-r3'])
    p.add_argument('--workers', type=int)
    p.add_argument('--ownership-transferred', action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--worker', type=Path)
    a = p.parse_args()
    if a.worker:
        worker(a.worker)
    else:
        if not a.machine:
            p.error('--machine required')
        run(a.machine, a.ownership_transferred, a.check, a.workers)
