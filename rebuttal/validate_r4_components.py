#!/usr/bin/env python3
"""CPU-only tests in a temporary copy of the published frozen source."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import run_remote as runner


def main():
    root = runner.ROOT
    archive = root / 'sources/source-r23-simple-v13.tar.gz'
    tests = []
    env = os.environ | {'GOCACHE': '/tmp/gocache', 'GOTOOLCHAIN': 'local',
                        'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOMAXPROCS': '1',
                        'GOMEMLIMIT': '1GiB', 'R4_PLAN_JSON': str(root / 'plans/r4.json')}
    runner.validate_r4(runner.read(root / 'plans/r4.json'), runner.read(root / 'plans/r2-r3.json'))
    with tempfile.TemporaryDirectory(prefix='pasta-r4-validation-') as temp:
        source = Path(temp)
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter='data')
        shutil.copyfile(root / 'tests/r4_runner_configuration_test.go',
                        source / 'akkalat/400latency/runner/r4_runner_configuration_test.go')
        for name, relative, fixtures in [
            ('plt-only', 'akita/mem/vm/tlb_gmmu', ['plt_disabled_test.go', 'plt_legacy_cost_test.go']),
            ('actual-runner', 'akkalat/400latency/runner', ['r4_runner_configuration_test.go'])]:
            cwd = source / relative
            production = json.loads(subprocess.run(['go', 'list', '-mod=readonly', '-json', '.'],
                cwd=cwd, env=env, check=True, text=True, capture_output=True).stdout)['GoFiles']
            for race in (False, True):
                command = ['go', 'test', '-p=1', '-mod=readonly', '-buildvcs=false',
                           '-count=1', '-timeout=180s', '-json'] + (['-race'] if race else []) + production + fixtures
                result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=240)
                events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
                passed = [e['Test'] for e in events if e.get('Action') == 'pass' and e.get('Test')]
                print(name, 'race=' + str(race), 'returncode=' + str(result.returncode), flush=True)
                if result.returncode or not passed:
                    print(result.stdout, result.stderr)
                    raise RuntimeError('R4 component tests failed')
                tests.append({'name': name, 'race': race, 'returncode': result.returncode,
                              'passed_tests': passed, 'command': command})
    runner.write(root / 'provenance/r4-aggregate.json', {
        'schema': 1, 'passed': True, 'no_gpu_workloads': True, 'tests': tests,
        'manifest_sha256': runner.digest(root / 'plans/r4.json'),
        'binary_sha256': runner.digest(root / 'bin/simulator-r23-simple-v13'),
        'archive_sha256': runner.digest(archive),
        'runner_fixture_sha256': runner.digest(root / 'tests/r4_runner_configuration_test.go'),
        'control_campaign': 'r2-r3', 'control_config': 'pasta16',
        'qualification': 'Functional PLT-only ablation under inherited aggregate lookup timing',
        'limits': 'No physical SRAM port arbitration, energy model or maintenance timing. '
                  'Tag comparisons are logical counts, not physical reads. No new PASTA-Full runs for R4.'})


if __name__ == '__main__':
    main()
