import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import after_current_sweep as gate
import remote_campaign_runner as runner

class PLTCampaignTest(unittest.TestCase):
    def test_rows_latency_and_shared_baseline(self):
        history = json.loads((runner.ROOT / 'historical_commands.json').read_text())['commands']
        jobs = runner.build_jobs(history)
        self.assertEqual(len(jobs), 56)
        self.assertEqual(len({j['id'] for j in jobs}), 56)
        self.assertEqual(sum(j['mode'] == 'baseline' for j in jobs), 14)
        for j in jobs:
            c = j['configuration']; f = runner.normalized(j['command'])
            self.assertEqual((c['gmmu_ptw_count'], c['iommu_ptw_count']), (4,16))
            if j['mode'] == 'pasta':
                rows, extra = runner.PROFILES[j['profile']]
                self.assertEqual(int(f['-gmmu-flex-pcd-ways']) * 16, rows)
                self.assertEqual(int(f['-gmmu-plt-extra-latency']), extra)
                self.assertEqual(c['ptcl_set_lookup_cycles'], extra)
            else:
                self.assertNotIn('-gmmu-plt-extra-latency', f)
            changed = {'-gmmu-flex-pcd-ways','-gmmu-plt-extra-latency'}
            self.assertEqual({k:v for k,v in f.items() if k not in changed}, {k:v for k,v in runner.normalized(j['historical_command']).items() if k not in changed})

    def test_gate_waits_for_entire_queue_and_live_processes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); jobs=[{'id':str(i)} for i in range(84)]
            (root/'manifest.json').write_text(json.dumps({'jobs':jobs}))
            for j in jobs[:-1]:
                p=root/'results'/j['id'];p.mkdir(parents=True);(p/'state.json').write_text(json.dumps({'status':'completed'}))
            self.assertFalse(gate.inspect(root)['ready'])
            p=root/'results'/'83';p.mkdir();(p/'state.json').write_text(json.dumps({'status':'failed'}))
            self.assertTrue(gate.inspect(root)['ready'])
            (p/'state.json').write_text(json.dumps({'status':'stale'}))
            self.assertFalse(gate.inspect(root)['ready'])

if __name__ == '__main__':
    unittest.main()
