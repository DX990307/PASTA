import argparse
from contextlib import contextmanager
from datetime import datetime
import importlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


ROOT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = ROOT_DIR / "results"
DEFAULT_MIN_MEM_AVAILABLE_GB = 50.0
DEFAULT_POLL_INTERVAL_SEC = 30.0
DEFAULT_SCAN_INTERVAL_SEC = 1800.0

SUPPORTED_STUDIES = {
    "runall2.py": "runall2",
    "hugePage.py": "hugePage",
    "prefetcherCount.py": "prefetcherCount",
    "adaptiveThreshold.py": "adaptiveThreshold",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Unified experiment-level scheduler. It expands supported sweep scripts "
            "into individual experiments and launches them globally when there is "
            "enough free memory headroom."
        )
    )
    parser.add_argument(
        "--cmd",
        dest="commands",
        action="append",
        required=True,
        help=(
            'Queue a study command, for example: --cmd "python3 runall2.py --max-workers 16". '
            "Supported scripts: runall2.py, hugePage.py, prefetcherCount.py, adaptiveThreshold.py. "
            "Unknown commands are treated as opaque one-shot tasks."
        ),
    )
    parser.add_argument(
        "--cwd",
        default=str(ROOT_DIR),
        help="Working directory used for queued commands. Default: akkalat directory.",
    )
    parser.add_argument(
        "--min-mem-available-gb",
        dest="min_mem_available_gb",
        type=float,
        default=DEFAULT_MIN_MEM_AVAILABLE_GB,
        help=(
            "Only launch a new experiment when MemAvailable is at least this many GiB. "
            f"Default: {DEFAULT_MIN_MEM_AVAILABLE_GB}"
        ),
    )
    parser.add_argument(
        "--poll-interval-sec",
        dest="poll_interval_sec",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help=(
            "How often to poll process completion and drive the one-by-one initial fill, in seconds. "
            f"Default: {DEFAULT_POLL_INTERVAL_SEC}"
        ),
    )
    parser.add_argument(
        "--scan-interval-sec",
        dest="scan_interval_sec",
        type=float,
        default=DEFAULT_SCAN_INTERVAL_SEC,
        help=(
            "Once the scheduler first observes memory headroom below the threshold, "
            "it enters steady state and only performs periodic memory-triggered launch "
            "attempts at this cadence, in seconds. Default: 1800 (30 minutes)."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep launching queued studies even if one experiment fails.",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help="Optional master log file path. If not set, a timestamped log file is created under akkalat/results/.",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help=(
            "Optional root directory for per-study experiment outputs. If not set, "
            "a timestamped directory is created under akkalat/results/."
        ),
    )
    return parser.parse_args()


def ensure_log_path(log_file_arg):
    if log_file_arg:
        log_path = Path(log_file_arg).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d-%H-%M-%S")
    return RESULTS_DIR / f"{timestamp}-pending.log"


def ensure_output_root(output_root_arg, log_path):
    if output_root_arg:
        output_root = Path(output_root_arg).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    default_root = log_path.with_suffix("")
    default_root.mkdir(parents=True, exist_ok=True)
    return default_root


def log_line(log_file, message):
    print(message)
    print(message, file=log_file, flush=True)


def build_env():
    env = os.environ.copy()
    env.setdefault("GOCACHE", "/tmp/gocache")
    return env


def read_mem_available_kb():
    meminfo_path = Path("/proc/meminfo")
    try:
        with meminfo_path.open("r") as meminfo_file:
            for line in meminfo_file:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (FileNotFoundError, PermissionError, ValueError):
        return None

    return None


def format_memory_kb(kb):
    if kb is None:
        return "unknown"
    if kb >= 1024 * 1024:
        return f"{kb / (1024 * 1024):.2f} GiB"
    if kb >= 1024:
        return f"{kb / 1024:.2f} MiB"
    return f"{kb} KiB"


@contextmanager
def temporary_argv(argv):
    original_argv = sys.argv[:]
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = original_argv


def detect_study(command):
    tokens = shlex.split(command)
    if not tokens:
        return None

    script_token = None
    arg_tokens = []

    first = os.path.basename(tokens[0])
    if first.startswith("python"):
        if len(tokens) < 2:
            return None
        script_token = tokens[1]
        arg_tokens = tokens[2:]
    else:
        script_token = tokens[0]
        arg_tokens = tokens[1:]

    script_name = os.path.basename(script_token)
    module_name = SUPPORTED_STUDIES.get(script_name)
    if module_name is None:
        return None

    return {
        "script_name": script_name,
        "module_name": module_name,
        "arg_tokens": arg_tokens,
    }


def import_study_module(module_name):
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    return importlib.import_module(module_name)


def parse_study_args(module, study_info):
    with temporary_argv([study_info["script_name"], *study_info["arg_tokens"]]):
        return module.parse_args()


def build_study_experiments(module_name, args):
    module = import_study_module(module_name)

    if module_name == "runall2":
        common_flags = module.build_common_flags(args)
        configs = module.build_ablation_configs(args)
        exps = module.make_exps(configs)
    elif module_name == "hugePage":
        common_flags = module.build_common_flags(args)
        configs = module.build_configs(args)
        page_sizes = module.build_page_sizes(args)
        exps = module.make_exps(configs, page_sizes)
    elif module_name == "prefetcherCount":
        common_flags = module.build_common_flags(args)
        configs = module.build_configs(args)
        counts = module.parse_prefetcher_counts(args.prefetcher_counts)
        exps = module.make_exps(configs, counts)
    elif module_name == "adaptiveThreshold":
        common_flags = module.build_common_flags(args)
        configs = module.build_configs(args)
        exps = module.make_exps(configs)
    else:
        raise RuntimeError(f"unsupported study module: {module_name}")

    return module, common_flags, exps


def resolve_rerun_missing_path(cwd, raw_path):
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (cwd / candidate).resolve()


def attach_study_metadata(module_name, exps, common_flags, output_dir):
    tagged = []
    for exp in exps:
        tagged_exp = dict(exp)
        tagged_exp["_study_name"] = module_name
        tagged_exp["_common_flags"] = common_flags
        tagged_exp["_output_dir"] = str(output_dir)
        tagged.append(tagged_exp)
    return tagged


def expand_command_queue(index, command, cwd, output_root, log_file):
    study_info = detect_study(command)
    if study_info is None:
        return {
            "kind": "raw",
            "label": f"cmd{index}",
            "items": [
                {
                    "kind": "raw",
                    "command": command,
                    "queue_index": index,
                    "queue_label": f"cmd{index}",
                    "_output_dir": str(output_root / "raw"),
                }
            ],
        }

    module = import_study_module(study_info["module_name"])
    args = parse_study_args(module, study_info)
    module_ref, common_flags, exps = build_study_experiments(study_info["module_name"], args)

    if getattr(args, "rerun_missing", ""):
        output_dir = resolve_rerun_missing_path(cwd, args.rerun_missing)
        if not output_dir.is_dir():
            raise RuntimeError(f"rerun directory does not exist: {output_dir}")
        exps = module_ref.filter_missing_metric_exps(exps, str(output_dir))
    else:
        output_dir = output_root / study_info["module_name"]
        output_dir.mkdir(parents=True, exist_ok=True)

    tagged = attach_study_metadata(
        study_info["module_name"],
        exps,
        common_flags,
        output_dir,
    )

    label = f'{study_info["module_name"]}({len(tagged)} exps)'
    log_line(log_file, f"[queue {index}] Expanded {command} -> {len(tagged)} experiment(s)")

    return {
        "kind": "study",
        "label": label,
        "items": tagged,
    }


def exp_file_stem(exp):
    base = Path(exp["_output_dir"])
    target = exp["target"]
    benchmark = exp["benchmark"]
    config_name = exp["config_name"]
    if "page_label" in exp:
        return base / f"{target}_{benchmark}_{config_name}_{exp['page_label']}"
    return base / f"{target}_{benchmark}_{config_name}"


def build_binary_targets(experiment_items, log_file):
    env = build_env()
    targets = sorted({item["target"] for item in experiment_items if item["kind"] == "experiment"})
    for target in targets:
        target_dir = ROOT_DIR / target
        log_line(log_file, f"Building {target} in {target_dir}")
        process = subprocess.Popen(["go", "build"], cwd=str(target_dir), env=env)
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"failed to build {target}")


def build_task_items(queue, queue_index):
    items = []
    for item_index, item in enumerate(queue["items"], start=1):
        if item.get("kind") == "raw":
            task = dict(item)
            task["_queue_index"] = queue_index
            task["_item_index"] = item_index
            items.append(task)
            continue

        task = dict(item)
        task["kind"] = "experiment"
        task["_queue_index"] = queue_index
        task["_item_index"] = item_index
        task["_queue_label"] = queue["label"]
        items.append(task)

    return items


def launch_raw_task(task, master_log_file):
    out_dir = Path(task["_output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"cmd-{task['_queue_index']:02d}.log"
    out_file = out_path.open("w")

    start_time = datetime.utcnow()
    available_kb = read_mem_available_kb()
    log_line(
        master_log_file,
        f"[raw {task['_queue_index']}] Launching: {task['command']}",
    )

    print(f"Executing {task['command']}", file=out_file, flush=True)
    print(f"UTC start time: {start_time.isoformat()}", file=out_file, flush=True)
    print(
        f"Launch MemAvailable: {available_kb} KiB ({format_memory_kb(available_kb)})",
        file=out_file,
        flush=True,
    )

    process = subprocess.Popen(
        shlex.split(task["command"]),
        cwd=str(ROOT_DIR),
        env=build_env(),
        stdout=out_file,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    return {
        "task": task,
        "process": process,
        "out_file": out_file,
        "start_time": start_time,
    }


def launch_experiment_task(task, master_log_file):
    stem = exp_file_stem(task)
    stem.parent.mkdir(parents=True, exist_ok=True)
    metrics_path = f"{stem}_metrics"
    out_path = Path(f"{stem}_out.stdout")
    out_file = out_path.open("w")

    binary = ROOT_DIR / task["target"] / task["target"]
    cmd = [
        str(binary),
        f'-benchmark={task["benchmark"]}',
        *task["_common_flags"],
        *task["flags"],
        f"-metric-file-name={metrics_path}",
    ]
    cmd_str = shlex.join(cmd)

    start_time = datetime.utcnow()
    available_kb = read_mem_available_kb()
    log_line(
        master_log_file,
        f"[exp q{task['_queue_index']}:{task['_item_index']}] Launching {task['_study_name']} "
        f"{task['benchmark']} {task['config_name']}",
    )

    print(f"Executing {cmd_str}", file=out_file, flush=True)
    print(f"Start time: {start_time}", file=out_file, flush=True)
    print(
        f"Launch MemAvailable: {available_kb} KiB ({format_memory_kb(available_kb)})",
        file=out_file,
        flush=True,
    )
    if "prefetcher_count" in task:
        print(f"Prefetcher count: {task['prefetcher_count']}", file=out_file, flush=True)

    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        env=build_env(),
        stdout=out_file,
        stderr=out_file,
        text=True,
        bufsize=1,
    )

    return {
        "task": task,
        "process": process,
        "out_file": out_file,
        "start_time": start_time,
    }


def finalize_running(state, master_log_file):
    process = state["process"]
    task = state["task"]
    end_time = datetime.utcnow()
    elapsed = end_time - state["start_time"]

    print(f"Return code: {process.returncode}", file=state["out_file"], flush=True)
    print(f"End time: {end_time}", file=state["out_file"], flush=True)
    print(f"Elapsed time: {elapsed}", file=state["out_file"], flush=True)
    state["out_file"].close()

    if task["kind"] == "experiment":
        label = f"{task['_study_name']} {task['benchmark']} {task['config_name']}"
    else:
        label = task["command"]

    log_line(master_log_file, f"Finished rc={process.returncode}: {label}")
    log_line(master_log_file, f"UTC end time: {end_time.isoformat()}")
    log_line(master_log_file, f"Elapsed time: {elapsed}")


def main():
    args = parse_args()
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise RuntimeError(f"working directory does not exist: {cwd}")

    log_path = ensure_log_path(args.log_file)
    output_root = ensure_output_root(args.output_root, log_path)
    min_mem_available_kb = int(args.min_mem_available_gb * 1024 * 1024)
    poll_interval_sec = max(args.poll_interval_sec, 0.1)
    scan_interval_sec = max(args.scan_interval_sec, poll_interval_sec)

    with log_path.open("w") as master_log_file:
        log_line(master_log_file, f"Unified scheduler working directory: {cwd}")
        log_line(master_log_file, f"Master log file: {log_path}")
        log_line(master_log_file, f"Output root: {output_root}")
        log_line(master_log_file, f"Queued commands: {len(args.commands)}")
        log_line(
            master_log_file,
            f"Min launch MemAvailable: {min_mem_available_kb} KiB ({format_memory_kb(min_mem_available_kb)})",
        )
        log_line(master_log_file, f"Completion/initial-fill poll interval: {poll_interval_sec:.1f}s")
        log_line(master_log_file, f"Steady-state periodic scan interval: {scan_interval_sec:.1f}s")

        queues = []
        for index, command in enumerate(args.commands, start=1):
            queue = expand_command_queue(index, command, cwd, output_root, master_log_file)
            items = build_task_items(queue, index)
            queues.append(
                {
                    "label": queue["label"],
                    "items": items,
                }
            )

        experiment_items = [
            item
            for queue in queues
            for item in queue["items"]
            if item["kind"] == "experiment"
        ]
        if experiment_items:
            build_binary_targets(experiment_items, master_log_file)

        running = []
        failures = 0
        stop_launching = False
        next_queue_index = 0
        initial_fill_phase = True
        next_periodic_scan_time = time.monotonic() + scan_interval_sec

        while any(queue["items"] for queue in queues) or running:
            still_running = []
            completion_triggered = False
            for state in running:
                if state["process"].poll() is None:
                    still_running.append(state)
                    continue

                finalize_running(state, master_log_file)
                completion_triggered = True
                if state["process"].returncode != 0:
                    failures += 1
                    if not args.continue_on_error:
                        stop_launching = True

            running = still_running

            if not stop_launching and any(queue["items"] for queue in queues):
                now_monotonic = time.monotonic()
                periodic_scan_triggered = (
                    not initial_fill_phase and now_monotonic >= next_periodic_scan_time
                )

                should_attempt_launch = False
                trigger_label = ""
                if initial_fill_phase:
                    should_attempt_launch = True
                    trigger_label = "initial-fill"
                elif completion_triggered:
                    should_attempt_launch = True
                    trigger_label = "completion"
                elif periodic_scan_triggered:
                    should_attempt_launch = True
                    trigger_label = "periodic-scan"

                if should_attempt_launch:
                    available_kb = read_mem_available_kb()

                    if available_kb is not None and available_kb < min_mem_available_kb:
                        if initial_fill_phase:
                            initial_fill_phase = False
                            next_periodic_scan_time = now_monotonic + scan_interval_sec
                            log_line(
                                master_log_file,
                                "Initial fill complete; entering steady state after observing "
                                f"MemAvailable={available_kb} KiB ({format_memory_kb(available_kb)}) below "
                                f"threshold={min_mem_available_kb} KiB ({format_memory_kb(min_mem_available_kb)}).",
                            )
                        elif periodic_scan_triggered:
                            next_periodic_scan_time = now_monotonic + scan_interval_sec

                        log_line(
                            master_log_file,
                            "Waiting for global experiment headroom "
                            f"(trigger={trigger_label}): "
                            f"MemAvailable={available_kb} KiB ({format_memory_kb(available_kb)}), "
                            f"threshold={min_mem_available_kb} KiB ({format_memory_kb(min_mem_available_kb)})",
                        )
                    else:
                        selected_queue = None
                        for offset in range(len(queues)):
                            candidate_index = (next_queue_index + offset) % len(queues)
                            if queues[candidate_index]["items"]:
                                selected_queue = candidate_index
                                break

                        if selected_queue is not None:
                            task = queues[selected_queue]["items"].pop(0)
                            next_queue_index = (selected_queue + 1) % len(queues)
                            if task["kind"] == "experiment":
                                state = launch_experiment_task(task, master_log_file)
                            else:
                                state = launch_raw_task(task, master_log_file)
                            running.append(state)

                        if periodic_scan_triggered:
                            next_periodic_scan_time = now_monotonic + scan_interval_sec

            if running or any(queue["items"] for queue in queues):
                time.sleep(poll_interval_sec)

        log_line(master_log_file, f"Unified queue finished. Failed tasks: {failures}")

    if failures > 0 and not args.continue_on_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
