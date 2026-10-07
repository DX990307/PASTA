import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import run_remote as runner


class RemoteR23Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = runner.read(runner.ROOT / 'plans/r2-r3.json')

    def test_full14(self):
        runner.validate_r23(self.manifest)

    def test_reject_pasta_mix(self):
        job = next(j for j in self.manifest['jobs'] if j['config'] == 'latpc_simple')
        job['command'] = [a.replace('-gmmu-idle-iommu-assist=false', '-gmmu-idle-iommu-assist=true') for a in job['command']]
        with self.assertRaises(RuntimeError): runner.validate_r23(self.manifest)

    def test_reject_duplicates_missing_or_rtm_off(self):
        for change in ('duplicate', 'missing', 'rtm'):
            manifest = copy.deepcopy(self.manifest)
            if change == 'duplicate': manifest['jobs'][0] = copy.deepcopy(manifest['jobs'][1])
            if change == 'missing': manifest['jobs'].pop()
            if change == 'rtm': manifest['jobs'][0]['command'].append('-disable-servers')
            with self.assertRaises(RuntimeError): runner.validate_r23(manifest)

    def test_summary_keeps_simulated_and_wall_time_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            job = self.manifest['jobs'][0]
            directory = output / job['id']
            directory.mkdir()
            runner.write(directory / 'completion.json', {'returncode': 0, 'wall_seconds': 100, 'qualification': 'pending strict central validation'})
            (directory / 'metrics.csv').write_text(', where, what, value\n0, Driver, total_time, 0.000100000000\n')
            runner.summary(output, self.manifest)
            with (output / 'summary.csv').open() as handle: rows = list(runner.csv.DictReader(handle))
            self.assertEqual(len(rows), 84)
            self.assertEqual(rows[0]['driver_time_s'], '0.000100000000')
            self.assertEqual(rows[0]['wall_seconds'], '100')
            self.assertEqual(rows[0]['qualification'], 'pending strict central validation')

    def test_reference_controls_are_not_new_launches(self):
        refs = runner.load_reuse(self.manifest)
        self.assertEqual(len(refs), 42)
        remaining = [j for j in self.manifest['jobs'] if j['id'] not in refs]
        self.assertEqual(len(remaining), 42)
        self.assertEqual({j['config'] for j in remaining}, {'baseline_estimated20', 'neighbor_abstract', 'latpc_simple'})

    def test_external_reuse_and_pending_do_not_claim_completion(self):
        refs = runner.load_reuse(self.manifest)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            runner.summary(output, self.manifest, refs)
            with (output / 'summary.csv').open() as handle: rows = list(runner.csv.DictReader(handle))
            for row in rows:
                if row['id'] in refs:
                    self.assertEqual(row['execution_status'], refs[row['id']]['disposition'])
                    self.assertEqual(row['driver_time_s'], '')
                    self.assertEqual(row['returncode'], '')
                    self.assertTrue(row['source_id'])

    def test_existing_remote_attempt_has_priority_and_is_not_killed(self):
        ref = {'disposition': 'reuse_external'}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            runner.write(directory / 'state.json', {'status': 'running', 'pid': 123, 'identity': {'start': '42'}})
            with patch.object(runner, 'identity', return_value={'start': '42', 'state': 'S'}):
                self.assertEqual(runner.execution_status(directory, ref), 'running')
            runner.write(directory / 'completion.json', {'returncode': 0})
            self.assertEqual(runner.execution_status(directory, ref), 'exited')

    def test_reuse_rejects_changed_configuration(self):
        manifest = copy.deepcopy(self.manifest)
        job = next(j for j in manifest['jobs'] if j['config'] == 'baseline16')
        job['command'].append('-changed-model=true')
        with self.assertRaises(RuntimeError): runner.load_reuse(manifest)

    def test_missing_reference_cannot_silently_become_a_new_run(self):
        policy = copy.deepcopy(runner.read(runner.ROOT / 'plans/r2-r3-reuse.json'))
        policy['refs'].pop(next(iter(policy['refs'])))
        with patch.object(runner, 'read', return_value=policy):
            with self.assertRaises(RuntimeError): runner.load_reuse(self.manifest)

    def test_other_machine_references_are_qualified_completions(self):
        for machine in ('machine15', 'machine5'):
            manifest = runner.read(runner.ROOT / 'plans' / (machine + '.json'))
            refs = runner.load_reuse(manifest, machine)
            self.assertTrue(all(r['disposition'] == 'reuse_external' for r in refs.values()))


if __name__ == '__main__': unittest.main()
