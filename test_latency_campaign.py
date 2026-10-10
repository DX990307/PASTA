import json,unittest
import remote_campaign_runner as r
class LatencyCampaignTest(unittest.TestCase):
    def test_partition_and_only_latency_varies(self):
        jobs=r.build_jobs({})
        first=r.select_partition(jobs,"first20");last=r.select_partition(jobs,"last50")
        self.assertEqual((len(jobs),len(first),len(last)),(70,20,50))
        self.assertFalse({j["id"] for j in first}&{j["id"] for j in last})
        expected=json.loads((r.ROOT/"job-partitions.json").read_text())
        self.assertEqual([j["id"] for j in last],expected["last50"])
        for benchmark in r.BENCHMARKS:
            points=[j for j in jobs if j["benchmark"]==benchmark and j["mode"]=="pasta"]
            flags=[r.normalized(j["command"]) for j in points]
            self.assertEqual({int(f["-gmmu-plt-extra-latency"]) for f in flags},{0,16,32,128})
            self.assertTrue(all(f["-gmmu-flex-pcd-ways"]=="2" for f in flags))
            stripped=[{k:v for k,v in f.items() if k!="-gmmu-plt-extra-latency"} for f in flags]
            self.assertTrue(all(f==stripped[0] for f in stripped))
if __name__=="__main__":unittest.main()
