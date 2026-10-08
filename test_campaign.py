import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import remote_campaign_runner as runner


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.history = json.loads((runner.ROOT / "historical_commands.json").read_text())["commands"]
        self.jobs = runner.build_jobs(self.history)

    def test_all84_commands_only_have_approved_delta(self):
        self.assertEqual(len(self.jobs), 84)
        self.assertEqual(len({j["id"] for j in self.jobs}), 84)
        self.assertEqual([j["benchmark"] for j in self.jobs[:6]], ["matrixtranspose"]*6)
        self.assertEqual([j["benchmark"] for j in self.jobs[6:12]], ["matrixmultiplication-ptw"]*6)
        for job in self.jobs:
            flags = runner.normalized(job["command"])
            for key in ("-gmmu-ptw-count", "-iommu-ptw-count", "-iommu-pw-queue-capacity", "-gmmu-plt-extra-latency"):
                flags.pop(key, None)
            self.assertEqual(flags, runner.normalized(job["historical_command"]))
            self.assertNotIn("-ptw-demand-pte-only", flags)
            self.assertNotIn("-disable-servers", flags)
            g, i, q, extra = runner.PROFILES[job["profile"]]
            current = runner.normalized(job["command"])
            self.assertEqual(int(current["-gmmu-ptw-count"]), g)
            self.assertEqual(int(current["-iommu-ptw-count"]), i)
            self.assertEqual(int(current.get("-iommu-pw-queue-capacity", 64)), q)
            if job["mode"] == "pasta":
                self.assertEqual(int(current["-gmmu-plt-extra-latency"]), extra)
                self.assertEqual(job["configuration"]["ptcl_set_lookup_cycles"], 64+extra)
            else:
                self.assertNotIn("-gmmu-plt-extra-latency", current)

    def test_wrong_extra_flag_is_rejected(self):
        history = copy.deepcopy(self.history)
        history["matrixtranspose"]["baseline"]["command"].append("-ptw-demand-pte-only")
        with self.assertRaises(RuntimeError):
            runner.build_jobs(history)

    def test_portable_result_path(self):
        with patch.object(runner, "ROOT", Path("/remote/ptw")), patch.object(runner, "RESULTS", Path("/remote/ptw/results")):
            command = runner.command_for(self.jobs[0])
            self.assertEqual(command[0], "/remote/ptw/bin/simulator")
            self.assertIn("-metric-file-name=/remote/ptw/results/"+self.jobs[0]["id"]+"/metrics", command)

    def test_positive_driver_time_and_monitor_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "metrics.csv").write_text("0, Driver, total_time, 0.001\n")
            (directory / "stdout.log").write_text("Monitoring simulation with http://localhost:12345\n")
            self.assertEqual(runner.driver_time(directory), 0.001)
            (directory / "metrics.csv").write_text("0, Driver, total_time, nan\n")
            with self.assertRaises(RuntimeError):
                runner.driver_time(directory)


if __name__ == "__main__":
    unittest.main()
