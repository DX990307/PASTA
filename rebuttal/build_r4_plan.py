#!/usr/bin/env python3
"""Derive the aggregate no-PLT ablation from frozen PASTA-Full commands."""
import copy
import run_remote as runner


def make_plan(base):
    jobs = []
    for control in base['jobs']:
        if control['config'] != 'pasta16':
            continue
        job = copy.deepcopy(control)
        job['id'] = job['benchmark'] + '__pasta_no_plt'
        job['config'] = 'pasta_no_plt'
        job['command'] = [
            '-gmmu-plt-disabled=true' if arg == '-gmmu-plt-disabled=false' else
            '-metric-file-name=results/r2-r3/' + job['id'] + '/metrics'
            if arg.startswith('-metric-file-name=') else arg
            for arg in job['command']
        ]
        for key in ('area_qualification', 'measured_N_eq', 'r2_variant', 'scope_amendment'):
            job.pop(key, None)
        job.update(design='pasta_no_plt', group='full14_plt_ablation',
                   scope_task='R4', scope_tasks=['R4'], shared_execution=False,
                   parent_job_id=control['id'], control_reference=control['id'],
                   timing_qualification='inherited_aggregate_lookup_not_physical_port_model')
        job['expected_gmmu_stats'] = {'plt_lookup_disabled': 1, 'plt_total_rows': 0}
        jobs.append(job)
    return {'schema': 1, 'workers': 15, 'benchmarks': base['benchmarks'],
            'binaries': base['binaries'], 'jobs': jobs,
            'parent_manifest_sha256': runner.digest(runner.ROOT / 'plans/r2-r3.json'),
            'qualification': 'Pure PLT removal under inherited aggregate timing; '
                             'not a calibrated SRAM port/maintenance cost study'}


if __name__ == '__main__':
    runner.write(runner.ROOT / 'plans/r4.json',
                 make_plan(runner.read(runner.ROOT / 'plans/r2-r3.json')))
