import copy
import json
from pathlib import Path
import tempfile
import unittest
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


if __name__ == '__main__': unittest.main()
