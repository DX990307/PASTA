import json,tempfile,unittest
from pathlib import Path
import remote_campaign_runner as runner
import after_previous_sweeps as gate
class IOTLBTest(unittest.TestCase):
    def test_only_sets_change(self):
        history=json.loads((runner.ROOT/'historical_commands.json').read_text())['commands'];jobs=runner.build_jobs(history)
        self.assertEqual(len(jobs),56);self.assertEqual(len({j['id'] for j in jobs}),56)
        self.assertEqual({j['config_name'] for j in jobs},{'base_iotlb_half','pasta_iotlb_half','base_iotlb_double','pasta_iotlb_double'})
        for j in jobs:
            flags=runner.normalized(j['command']);old=runner.normalized(j['historical_command'])
            self.assertEqual({k:v for k,v in flags.items() if k!='-iotlb-num-sets'},old)
            sets=32 if j['profile']=='iotlb_half' else 128
            self.assertEqual(int(flags['-iotlb-num-sets']),sets)
            c=j['configuration'];self.assertEqual((c['iotlb_num_ways'],c['iotlb_mshrs'],c['iotlb_entries']),(32,64,sets*32))
            self.assertEqual((c['gmmu_ptw_count'],c['iommu_ptw_count']),(4,16))
            self.assertNotIn('-gmmu-plt-extra-latency',flags)
    def test_waits_for_queued_or_stale(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'manifest.json').write_text(json.dumps({'job_count':1,'jobs':[{'id':'a'}]}))
            self.assertFalse(gate.inspect(p)['ready'])
            x=p/'results/a';x.mkdir(parents=True);(x/'state.json').write_text(json.dumps({'status':'stale'}))
            self.assertFalse(gate.inspect(p)['ready'])
            (x/'state.json').write_text(json.dumps({'status':'completed'}));self.assertTrue(gate.inspect(p)['ready'])
if __name__=='__main__':unittest.main()
