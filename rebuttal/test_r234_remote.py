import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_r4_plan
import run_remote as runner


class R234Tests(unittest.TestCase):
    def setUp(self):
        self.base = runner.read(runner.ROOT / 'plans/r2-r3.json')
        self.r4 = runner.read(runner.ROOT / 'plans/r4.json')

    def test_reproducible_full14_r4(self):
        self.assertEqual(self.r4, build_r4_plan.make_plan(self.base))
        runner.validate_r4(self.r4, self.base)
        controls = {j['benchmark']: j for j in self.base['jobs'] if j['config'] == 'pasta16'}
        for job in self.r4['jobs']:
            self.assertEqual(job['expected_metrics'], controls[job['benchmark']]['expected_metrics'])
            self.assertEqual(job['expected_gmmu_stats'], {'plt_lookup_disabled': 1, 'plt_total_rows': 0})

    def test_combined84_executions(self):
        manifest = runner.load_manifest('r2-r3-r4')
        refs = runner.load_reuse(manifest, 'r2-r3-r4')
        self.assertEqual(len(manifest['jobs']), 98)
        self.assertEqual(len(manifest['jobs']) - len(refs), 84)
        self.assertEqual(len({j['id'] for j in manifest['jobs']}), 98)
        self.assertEqual(manifest['jobs'][:84], self.base['jobs'])
        controls = {j['id'] for j in self.base['jobs'] if j['config'] == 'pasta16'}
        self.assertEqual({j['control_reference'] for j in self.r4['jobs']}, controls)
        self.assertFalse(runner.load_reuse(self.r4, 'r4'))

    def test_reject_not_pure_plt_removal(self):
        for old, new in [('-gmmu-flex-tlb', '-gmmu-flex-tlb=false'),
                         ('-gmmu-idle-iommu-assist=true', '-gmmu-idle-iommu-assist=false'),
                         ('-gmmu-mshr-entries=16', '-gmmu-mshr-entries=20'),
                         ('-gmmu-plt-disabled=true', '-gmmu-plt-disabled=false')]:
            manifest = copy.deepcopy(self.r4)
            manifest['jobs'][0]['command'] = [new if a == old else a for a in manifest['jobs'][0]['command']]
            with self.assertRaises(RuntimeError): runner.validate_r4(manifest, self.base)

    def test_reject_missing_duplicate_and_rtm_off(self):
        for change in ('missing', 'duplicate', 'rtm'):
            manifest = copy.deepcopy(self.r4)
            if change == 'missing': manifest['jobs'].pop()
            if change == 'duplicate': manifest['jobs'][0] = manifest['jobs'][1]
            if change == 'rtm': manifest['jobs'][0]['command'].append('-disable-servers')
            with self.assertRaises(RuntimeError): runner.validate_r4(manifest, self.base)

    def test_count_adopted_workers_outside_selected_subset(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            active = output / self.base['jobs'][0]['id']
            active.mkdir()
            runner.write(active / 'state.json', {'status': 'running', 'pid': 123, 'identity': {'start': '42'}})
            with patch.object(runner, 'identity', return_value={'start': '42', 'state': 'S'}):
                self.assertEqual(runner.other_running(output, self.r4), 1)
                self.assertEqual(runner.other_running(output, runner.load_manifest('r2-r3-r4')), 0)


if __name__ == '__main__':
    unittest.main()
