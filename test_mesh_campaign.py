import json,tempfile,unittest
from pathlib import Path
import remote_campaign_runner as runner
import after_previous_sweeps as gate
class MeshTest(unittest.TestCase):
    def test_only_link_settings_change_and_pairs_match(self):
        historical=json.loads((runner.ROOT/'historical_commands.json').read_text())['commands'];jobs=runner.build_jobs(historical)
        self.assertEqual(len(jobs),56);self.assertEqual(len({j['id'] for j in jobs}),56)
        self.assertEqual({j['config_name'] for j in jobs},{'base_meshlat64','pasta_meshlat64','base_meshbw384','pasta_meshbw384'})
        for j in jobs:
            flags=runner.normalized(j['command']);old=runner.normalized(j['historical_command']);changed={'-switch-latency','-bandwidth'}
            self.assertEqual({k:v for k,v in flags.items() if k not in changed},{k:v for k,v in old.items() if k not in changed})
            expected=(64,48,768) if j['profile']=='meshlat64' else (32,24,384)
            self.assertEqual((int(flags['-switch-latency']),int(flags['-bandwidth']),j['configuration']['mesh_bandwidth_gb_s']),expected)
            self.assertNotIn('-gmmu-plt-extra-latency',flags)
            self.assertEqual(j['configuration']['gmmu_ptw_count'],4)
        for benchmark in runner.BENCHMARKS:
            for profile in runner.PROFILES:
                pair=[j for j in jobs if j['benchmark']==benchmark and j['profile']==profile]
                self.assertEqual({j['mode'] for j in pair},{'baseline','pasta'})
                self.assertEqual(pair[0]['configuration']['mesh_bandwidth_gb_s'],pair[1]['configuration']['mesh_bandwidth_gb_s'])
    def test_waits_for_all_prerequisite_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'manifest.json').write_text(json.dumps({'job_count':2,'jobs':[{'id':'a'},{'id':'b'}]}))
            (p/'results/a').mkdir(parents=True);(p/'results/a/state.json').write_text(json.dumps({'status':'completed'}))
            self.assertFalse(gate.inspect(p)['ready'])
            (p/'results/b').mkdir();(p/'results/b/state.json').write_text(json.dumps({'status':'failed'}))
            self.assertTrue(gate.inspect(p)['ready'])
            (p/'results/b/state.json').write_text(json.dumps({'status':'stale'}))
            self.assertFalse(gate.inspect(p)['ready'])
if __name__=='__main__':unittest.main()
