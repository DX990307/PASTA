#!/usr/bin/env python3
"""Run BERT/GPT as a sequence of small llmop benchmarks.

This mirrors the old DNN sampledrunner style: each transformer sub-op is run as
its own benchmark process, and the per-op metrics can be summed afterward.
"""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import shlex
import shutil

import bertconfig
import gptconfig
import runall2


CONFIG_FLAGS = {name: flags for name, flags in runall2.CONFIGS}
VPN_MSHR_FALLBACK_FLAGS = [
    "-gmmu-vpn-mshr-baseline",
    "-mmutlb-vpn-mshr-baseline",
    "-mmutlb-demand-pte-only",
]
SUMMARY_CONFIG_NAMES = sorted(
    set(CONFIG_FLAGS)
    | set(runall2.PTCL_CONFIG_NAMES)
    | set(getattr(runall2, "EXTRA_PTCL_CONFIG_NAMES", []))
    | {"llm_mixed"},
    key=len,
    reverse=True,
)
PROFILE_NAMES = sorted(set(bertconfig.PROFILES) | set(gptconfig.PROFILES))


@dataclass
class ResultID:
    target: str
    benchmark: str
    model: str
    profile: str
    op_index: int
    op_name: str
    config: str


@dataclass
class MetricRecord:
    result_id: ResultID
    metrics_path: Path
    stdout_path: Path
    driver_kernel_time: float = 0.0
    driver_total_time: float = 0.0
    command_processor_kernel_time: float = 0.0
    max_command_processor_kernel_time: float = 0.0
    stdout_elapsed_seconds: float = 0.0
    return_code: str = ""


@dataclass(frozen=True)
class ModelSpec:
    model: str
    profile: str


RESNET_PROFILES = {
    "resnet-tiny": {
        "mode": "block",
        "depth": 18,
        "batch_size": 1,
        "image_size": 4,
    },
    "resnet-18-block": {
        "mode": "block",
        "depth": 18,
        "batch_size": 4,
        "image_size": 224,
    },
    "resnet-50-full": {
        "mode": "full",
        "depth": 50,
        "batch_size": 4,
        "image_size": 224,
    },
}

MODEL_ALIASES = {
    "bert7b": ("bert", "bert-7b-proxy"),
    "bert-7b": ("bert", "bert-7b-proxy"),
    "bert-7b-proxy": ("bert", "bert-7b-proxy"),
    "gpt7b": ("gpt", "gpt-7b"),
    "gpt-7b": ("gpt", "gpt-7b"),
    "gpt-7b-proxy": ("gpt", "gpt-7b-proxy"),
    "resnet": ("resnet", "resnet-50-full"),
    "resnet50": ("resnet", "resnet-50-full"),
    "resnet-50": ("resnet", "resnet-50-full"),
    "resnet50-full": ("resnet", "resnet-50-full"),
    "resnet-50-full": ("resnet", "resnet-50-full"),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run decomposed BERT/GPT through llmop, optionally alongside "
            "the full ResNet benchmark."
        ))
    parser.add_argument(
        "--model",
        "--models",
        dest="model",
        default="bert",
        help=(
            "Comma-separated model list. Supports legacy bert/gpt plus "
            "aliases such as gpt7B,bert7B,resnet."
        ))
    parser.add_argument(
        "--target",
        default="400latency",
        help="akkalat target binary to run. Default: 400latency.",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_NAMES + sorted(RESNET_PROFILES),
        default="tiny")
    parser.add_argument(
        "--resnet-mode",
        default="",
        help="Override ResNet profile mode: block or full.")
    parser.add_argument(
        "--resnet-depth",
        type=int,
        default=0,
        help="Override ResNet profile depth: 18, 34, or 50.")
    parser.add_argument(
        "--resnet-batch-size",
        type=int,
        default=0,
        help="Override ResNet profile synthetic batch size.")
    parser.add_argument(
        "--resnet-image-size",
        type=int,
        default=0,
        help="Override ResNet profile square image size.")
    parser.add_argument(
        "--model-schedule",
        choices=["round-robin", "grouped"],
        default="round-robin",
        help=(
            "Multi-model queue order. round-robin interleaves models so one "
            "long model tail does not hold back the others; grouped preserves "
            "the old model-by-model order."
        ))
    parser.add_argument(
        "--vpn-mshr-op-kinds",
        default="",
        help=(
            "Comma-separated llmop kinds that should use per-VPN GMMU/MMUTLB "
            "MSHR fallback flags for every config. Useful for isolating ops "
            "that hang in the PTCL-granularity MSHR path, e.g. row-softmax."
        ))
    parser.add_argument(
        "--configs", default="baseline",
        help=(
            "Comma-separated configs. Supports Photon configs such as "
            "baseline,sample_all_loop and PTCL configs such as "
            "baseline,ptcl_mode,camsat."
        ))
    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=runall2.DEFAULT_TIMEOUT_MINUTES,
        help="Kill an operator experiment after this many minutes. 0 disables timeout.")
    parser.add_argument(
        "--adaptive-threshold-low",
        type=int,
        default=runall2.DEFAULT_ADAPTIVE_LOW,
        help="Low threshold for adaptive PTCL configs.")
    parser.add_argument(
        "--adaptive-threshold-high",
        type=int,
        default=runall2.DEFAULT_ADAPTIVE_HIGH,
        help="High threshold for adaptive PTCL configs.")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument(
        "--max-workloads",
        type=int,
        default=runall2.DEFAULT_MAX_WORKLOADS,
        help="Hard cap on concurrently running decomposed operator workloads.")
    parser.add_argument(
        "--min-free-ram-gb",
        type=float,
        default=runall2.DEFAULT_MIN_FREE_RAM_GB,
        help="Minimum MemAvailable, in GiB, required before launching an operator.")
    parser.add_argument(
        "--memory-scan-interval-minutes",
        type=float,
        default=runall2.DEFAULT_MEMORY_SCAN_INTERVAL_MINUTES,
        help="How often to check MemAvailable after initial fill.")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Run only the first N ops. 0 means all ops.")
    parser.add_argument(
        "--op-label-filter",
        "--only-op-labels",
        dest="op_label_filter",
        default="",
        help=(
            "Comma-separated substrings used to keep decomposed ops by label "
            "before dedupe, e.g. mlp_fc2 or layer00_mlp_fc2."
        ))
    parser.add_argument(
        "--layers", type=int, default=0,
        help="Override the profile layer count. 0 uses the profile default.")
    parser.add_argument(
        "--seq-len",
        type=int,
        default=0,
        help="Override the profile sequence length. 0 uses the profile default.")
    parser.add_argument(
        "--bert-num-labels",
        type=int,
        default=2,
        help="Classifier output classes for BERT decomposed workloads.")
    parser.add_argument(
        "--gpt-decode-steps",
        type=int,
        default=1,
        help=(
            "Autoregressive decode tokens to append after GPT prefill. "
            "Use 0 to run prefill only."
        ))
    parser.add_argument(
        "--gpt-decode-context-len",
        type=int,
        default=0,
        help=(
            "KV-cache context length used by GPT decode ops. "
            "0 uses the GPT profile/input seq_len."
        ))
    parser.add_argument(
        "--max-wg",
        type=int,
        default=None,
        help=(
            "Pass -max-wg to each decomposed llmop benchmark. "
            "Default uses runall2.py's cap. Use 0 to disable."
        ))
    parser.add_argument(
        "--split-k", default="1",
        help=(
            "Use split-linear with this K split count for all linear ops, "
            "or 'auto' to choose per-op split counts for multi-GPU WG spread."
        ))
    parser.add_argument(
        "--target-gpus", type=int, default=48,
        help="Target actual GPU count for --split-k auto.")
    parser.add_argument(
        "--cu-per-gpu", type=int, default=32,
        help="Actual CU count per GPU used by --split-k auto.")
    parser.add_argument(
        "--max-split-k", type=int, default=16,
        help="Maximum per-op split count used by --split-k auto.")
    parser.add_argument(
        "--balance-split-k-by-input",
        action="store_true",
        help=(
            "Increase split-linear split-k when input-dim is large so each "
            "split has a bounded K chunk. This keeps fc2-style GEMMs from "
            "having far heavier workgroups than fc1-style GEMMs."
        ))
    parser.add_argument(
        "--split-k-target-input-chunk",
        type=int,
        default=1024,
        help=(
            "Target maximum input-dim per split when "
            "--balance-split-k-by-input is enabled."
        ))
    parser.add_argument("--sampled-warmup", type=int, default=128)
    parser.add_argument("--sampled-granularity", type=int, default=512)
    parser.add_argument(
        "--photon",
        dest="photon",
        action="store_true",
        default=False,
        help="Enable Photon sampled execution for decomposed ops.")
    parser.add_argument(
        "--no-photon",
        dest="photon",
        action="store_false",
        help="Leave Photon sampled execution disabled. This is the default.")
    parser.add_argument("--log-subtasks", action="store_true")
    parser.add_argument(
        "--include-transfers", action="store_true",
        help="Insert llmop transfer benchmarks between ops with different placements.")
    parser.add_argument(
        "--transfer-copy-gpus", default="dst",
        help="Value passed to -copy-gpus for inserted transfer ops.")
    parser.add_argument(
        "--placement-output", default="llm_decomposed_placement.csv",
        help="Operator placement CSV. Relative paths are under results_dir.")
    parser.add_argument(
        "--log2-page-size", type=int, default=12,
        help="Page size log2 used for per-GPU output-memory estimates.")
    parser.add_argument(
        "--summarize", action="store_true",
        help="Write decomposed summary CSVs after all ops finish.")
    parser.add_argument(
        "--analyze-ops",
        action="store_true",
        help=(
            "Before running, group decomposed ops by identical llmop flags and "
            "write llm_decomposed_op_analysis.csv."
        ))
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help=(
            "Create a results directory, write placement/op-analysis CSVs, "
            "print duplicate-op groups, and exit without building or running."
        ))
    parser.add_argument(
        "--dedupe-identical-ops",
        action="store_true",
        help=(
            "Run only one representative for experiments with identical "
            "target/benchmark/common/config/op flags. Duplicate metrics/stdout "
            "files are materialized afterward so summaries still count every "
            "original op occurrence."
        ))
    parser.add_argument(
        "--auto-dedupe",
        action="store_true",
        help=(
            "Shortcut for the common workflow: analyze decomposed op shapes, "
            "write reports, dedupe identical experiments, run only unique "
            "experiments, and materialize duplicate outputs before summary."
        ))
    parser.add_argument(
        "--enable-servers", action="store_true",
        help="Deprecated no-op. Servers are always left enabled.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def config_flags(name, args):
    if name not in CONFIG_FLAGS:
        raise ValueError(f"unknown config {name!r}")

    flags = CONFIG_FLAGS[name][:]
    if "-sampled" in flags:
        flags += [
            f"-sampled-warmup={args.sampled_warmup}",
            f"-sampled-granularity={args.sampled_granularity}",
        ]
    return flags


def benchmark_flags(args):
    if args.max_wg is not None:
        if args.max_wg <= 0:
            return []
        return [f"-max-wg={args.max_wg}"]
    return runall2.DEFAULT_BENCHMARK_FLAGS[:]


def has_photon_flags(flags):
    photon_flags = {
        "-sampled",
        "-branch-sampled",
        "-kernel-sampled",
        "-loop-sampled",
    }
    return any(
        flag in photon_flags
        or flag.startswith("-sampled-warmup=")
        or flag.startswith("-sampled-granularity=")
        for flag in flags
    )


def default_photon_flags(args):
    if not args.photon:
        return []
    return [
        "-sampled",
        "-branch-sampled",
        "-kernel-sampled",
        "-loop-sampled",
        f"-sampled-warmup={args.sampled_warmup}",
        f"-sampled-granularity={args.sampled_granularity}",
    ]


def add_default_photon_flags(args, flags):
    if has_photon_flags(flags):
        return flags
    return flags + default_photon_flags(args)


def parse_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def label_matches_any(label, filters):
    if not filters:
        return True
    label_lower = label.lower()
    return any(token.lower() in label_lower for token in filters)


def normalize_model_token(token):
    return token.strip().lower().replace("_", "-")


def validate_profile(model, profile):
    if model == "bert":
        if profile not in bertconfig.PROFILES:
            raise ValueError(f"unknown BERT profile {profile!r}")
    elif model == "gpt":
        if profile not in gptconfig.PROFILES:
            raise ValueError(f"unknown GPT profile {profile!r}")
    elif model == "resnet":
        if profile not in RESNET_PROFILES:
            raise ValueError(f"unknown ResNet profile {profile!r}")
    else:
        raise ValueError(f"unknown model {model!r}")


def resolve_model_spec(token, default_profile):
    normalized = normalize_model_token(token)
    for sep in (":", "/"):
        if sep in normalized:
            model, profile = normalized.split(sep, 1)
            validate_profile(model, profile)
            return ModelSpec(model, profile)

    if normalized in {"bert", "gpt"}:
        validate_profile(normalized, default_profile)
        return ModelSpec(normalized, default_profile)

    if normalized == "resnet":
        profile = (
            default_profile
            if default_profile in RESNET_PROFILES
            else "resnet-50-full"
        )
        return ModelSpec("resnet", profile)

    if normalized in MODEL_ALIASES:
        model, profile = MODEL_ALIASES[normalized]
        return ModelSpec(model, profile)

    raise ValueError(
        f"unknown model alias {token!r}; use bert, gpt, resnet, "
        "gpt7B, bert7B, or model:profile"
    )


def resolve_model_specs(args):
    specs = []
    seen = set()
    for token in parse_csv(args.model) or ["bert"]:
        spec = resolve_model_spec(token, args.profile)
        key = (spec.model, spec.profile)
        if key in seen:
            continue
        validate_profile(spec.model, spec.profile)
        seen.add(key)
        specs.append(spec)
    return specs


def args_for_model(args, spec):
    model_args = argparse.Namespace(**vars(args))
    model_args.model = spec.model
    model_args.profile = spec.profile
    return model_args


def adaptive_flags(args):
    low = args.adaptive_threshold_low
    high = args.adaptive_threshold_high
    if low > high:
        low, high = high, low
    return [
        "-gmmu-initial-ptcl-mode=false",
        f"-gmmu-ptcl-threshold-low={low}",
        f"-gmmu-ptcl-threshold-high={high}",
    ]


def set_default_attr(args, name, value):
    if not hasattr(args, name):
        setattr(args, name, value)


def add_runall_ptcl_defaults(args):
    set_default_attr(
        args,
        "mmutlb_ptcl_return_latency",
        runall2.DEFAULT_MMUTLB_PTCL_RETURN_LATENCY,
    )
    set_default_attr(
        args,
        "gmmu_pte_lookup_latency",
        runall2.DEFAULT_GMMU_PTE_LOOKUP_LATENCY,
    )
    set_default_attr(
        args,
        "gmmu_num_req_per_cycle",
        runall2.DEFAULT_GMMU_NUM_REQ_PER_CYCLE,
    )
    set_default_attr(
        args,
        "gmmu_pte_lookup_slots",
        runall2.DEFAULT_GMMU_PTE_LOOKUP_SLOTS,
    )
    set_default_attr(
        args,
        "gmmu_flex_pcd_ways",
        runall2.DEFAULT_GMMU_FLEX_PCD_WAYS,
    )
    set_default_attr(
        args,
        "gmmu_flex_promotion_threshold",
        runall2.DEFAULT_GMMU_FLEX_PROMOTION_THRESHOLD,
    )
    set_default_attr(args, "gmmu_ptcl_serial_lookup", False)


def ptcl_config_map(args):
    add_runall_ptcl_defaults(args)
    return runall2.build_ptcl_config_map(args)


def unique_preserving_config_names(configs):
    seen = set()
    unique = []
    for name, flags in configs:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, flags))
    return unique


def append_unique_flags(flags, extra_flags):
    out = flags[:]
    present = set(out)
    for flag in extra_flags:
        if flag not in present:
            out.append(flag)
            present.add(flag)
    return out


def apply_op_fallback_flags(args, flags, op_flags):
    fallback_kinds = set(parse_csv(args.vpn_mshr_op_kinds))
    if not fallback_kinds:
        return flags
    if op_kind(op_flags) not in fallback_kinds:
        return flags
    return append_unique_flags(flags, VPN_MSHR_FALLBACK_FLAGS)


def selected_config_flags(args, op_flags):
    requested = parse_csv(args.configs) or ["baseline"]
    ptcl_configs = ptcl_config_map(args)
    selected = []
    explicit_ptcl = any(
        name == "ptcl_all" or (name in ptcl_configs and name != "baseline")
        for name in requested
    )

    for name in requested:
        if name == "ptcl_all":
            selected += [
                (config_name, ptcl_configs[config_name])
                for config_name in runall2.PTCL_CONFIG_NAMES
            ]
            continue

        if name in ("all", "photon_all"):
            selected += [
                (config_name, config_flags(config_name, args))
                for config_name, _ in runall2.CONFIGS
            ]
            continue

        if name == "baseline" and explicit_ptcl:
            selected.append(("baseline", ptcl_configs["baseline"]))
            continue

        if name in ptcl_configs and name != "baseline":
            selected.append((name, ptcl_configs[name]))
            continue

        actual_name = llm_mixed_config_name(name, op_flags)
        if actual_name in CONFIG_FLAGS:
            selected.append((name, config_flags(actual_name, args)))
            continue

        allowed = sorted(
            set(CONFIG_FLAGS)
            | set(ptcl_configs)
            | {"llm_mixed", "ptcl_all", "photon_all", "all"}
        )
        raise ValueError(
            f"unknown config: {name}. Allowed: {', '.join(allowed)}"
        )

    selected = unique_preserving_config_names(selected)
    return [
        (name, apply_op_fallback_flags(args, flags, op_flags))
        for name, flags in selected
    ]


def op_kind(flags):
    for flag in flags:
        if flag.startswith("-op="):
            return flag.split("=", 1)[1]
        if flag.startswith("-resnet-mode="):
            return "resnet"
    return ""


def parse_split_k(value):
    if value == "auto":
        return value
    try:
        split_k = int(value)
    except ValueError as err:
        raise ValueError("--split-k must be a positive integer or 'auto'") from err
    if split_k <= 0:
        raise ValueError("--split-k must be positive")
    return split_k


def flag_value(flags, name):
    prefix = f"-{name}="
    for flag in flags:
        if flag.startswith(prefix):
            return flag.split("=", 1)[1]
    return None


def replace_or_append_flag(flags, name, value):
    prefix = f"-{name}="
    new_flag = f"-{name}={value}"
    out = []
    replaced = False
    for flag in flags:
        if flag.startswith(prefix):
            out.append(new_flag)
            replaced = True
        else:
            out.append(flag)
    if not replaced:
        out.append(new_flag)
    return out


def ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def uses_target_gpus(total_wg, target_gpus, cu_per_gpu):
    if target_gpus <= 1:
        return total_wg > 0

    total_cu = target_gpus * cu_per_gpu
    wg_per_cu = ceil_div(total_wg, total_cu)
    return total_wg > (target_gpus - 1) * cu_per_gpu * wg_per_cu


def choose_auto_split_k(rows, input_dim, output_dim, args):
    block_size = 16
    base_wg = ceil_div(rows, block_size) * ceil_div(output_dim, block_size)
    max_split = max(1, min(args.max_split_k, input_dim))

    for split_k in range(1, max_split + 1):
        if uses_target_gpus(
            base_wg * split_k, args.target_gpus, args.cu_per_gpu,
        ):
            return split_k

    return max_split


def auto_split_linear_flags(flags, args):
    op = op_kind(flags)
    if op not in {"linear", "split-linear"}:
        return flags

    rows = int(flag_value(flags, "rows"))
    input_dim = int(flag_value(flags, "input-dim"))
    output_dim = int(flag_value(flags, "output-dim"))
    split_k = choose_auto_split_k(rows, input_dim, output_dim, args)
    if split_k <= 1:
        return flags

    out = replace_or_append_flag(flags, "op", "split-linear")
    return replace_or_append_flag(out, "split-k", split_k)


def balance_split_linear_flags(flags, args):
    if not args.balance_split_k_by_input:
        return flags

    op = op_kind(flags)
    if op not in {"linear", "split-linear"}:
        return flags

    input_dim = int(flag_value(flags, "input-dim"))
    current_split_k = int_flag(flags, "split-k", 1)
    target_chunk = max(1, args.split_k_target_input_chunk)
    balanced_split_k = max(
        current_split_k,
        ceil_div(input_dim, target_chunk),
    )
    balanced_split_k = min(balanced_split_k, args.max_split_k)
    if balanced_split_k <= current_split_k:
        return flags

    out = replace_or_append_flag(flags, "op", "split-linear")
    return replace_or_append_flag(out, "split-k", balanced_split_k)


def int_flag(flags, name, default=0):
    value = flag_value(flags, name)
    if value is None:
        return default
    return int(value)


def output_bytes(flags):
    op = op_kind(flags)
    rows = int_flag(flags, "rows")
    hidden = int_flag(flags, "hidden")
    elements = int_flag(flags, "elements")

    if op in {
        "embedding",
        "bert-embedding",
        "layernorm",
        "attention",
        "causal-attention",
        "decode-attention",
    }:
        return rows * hidden * 4
    if op in {"linear", "split-linear", "mlp"}:
        return rows * int_flag(flags, "output-dim") * 4
    if op in {"gelu", "tanh", "residual-add"}:
        if elements == 0:
            elements = rows * hidden
        return elements * 4
    if op == "kv-cache-update":
        kv_rows = int_flag(flags, "kv-rows", rows)
        return 2 * kv_rows * hidden * 4
    if op == "row-softmax":
        return rows * int_flag(flags, "cols", rows) * 4
    if op == "causal-mask":
        return rows * rows * 4
    if op == "batchnorm2d":
        seq_len = int_flag(flags, "seq-len")
        cols = int_flag(flags, "cols", seq_len)
        return rows * hidden * seq_len * cols * 4
    return 0


def estimated_wg(flags):
    op = op_kind(flags)
    rows = int_flag(flags, "rows")
    hidden = int_flag(flags, "hidden")
    elements = int_flag(flags, "elements")

    if op in {"embedding", "bert-embedding"}:
        return ceil_div(rows * hidden, 64)
    if op == "layernorm":
        return ceil_div(rows * hidden, 64)
    if op in {"linear", "split-linear", "mlp"}:
        output_dim = int_flag(flags, "output-dim")
        split_k = int_flag(flags, "split-k", 1)
        return ceil_div(rows, 16) * ceil_div(output_dim, 16) * split_k
    if op in {"gelu", "tanh"}:
        return ceil_div(elements, 64)
    if op == "residual-add":
        return ceil_div(elements, 64)
    if op == "kv-cache-update":
        return ceil_div(2 * rows * hidden, 64)
    if op == "decode-attention":
        kv_rows = int_flag(flags, "kv-rows", rows)
        score_wg = ceil_div(rows, 16) * ceil_div(kv_rows, 16)
        value_wg = ceil_div(rows, 16) * ceil_div(hidden, 16)
        out_wg = ceil_div(rows, 16) * ceil_div(hidden, 16)
        softmax_wg = rows
        return score_wg + value_wg + out_wg + softmax_wg
    if op == "row-softmax":
        return rows
    if op == "causal-mask":
        return ceil_div(rows * rows, 64)
    if op in {"attention", "causal-attention"}:
        return ceil_div(rows, 16) * ceil_div(hidden, 16)
    if op == "batchnorm2d":
        seq_len = int_flag(flags, "seq-len")
        cols = int_flag(flags, "cols", seq_len)
        return ceil_div(rows * hidden * seq_len * cols, 64)
    return 0


def estimated_placement(flags, args):
    total_wg = estimated_wg(flags)
    used_gpus = estimated_used_gpus(total_wg, args)
    if used_gpus <= 1:
        return "first"
    if used_gpus >= args.target_gpus:
        return "all"
    return f"1-{used_gpus}"


def op_output_placement(flags, args):
    if op_kind(flags) == "transfer":
        return flag_value(flags, "dst-gpus") or "first"
    return estimated_placement(flags, args)


def op_compute_placement(flags, args):
    if op_kind(flags) == "transfer":
        src = resolve_placement_spec(flag_value(flags, "src-gpus") or "all", args)
        dst = resolve_placement_spec(flag_value(flags, "dst-gpus") or "first", args)
        return normalize_gpu_spec(
            flag_value(flags, "copy-gpus") or "dst", args, src=src, dst=dst)
    return estimated_placement(flags, args)


def estimated_used_gpus(total_wg, args):
    if total_wg <= 0:
        return 1

    total_cu = args.target_gpus * args.cu_per_gpu
    wg_per_cu = ceil_div(total_wg, total_cu)
    wg_per_gpu = args.cu_per_gpu * wg_per_cu
    return min(args.target_gpus, ceil_div(total_wg, wg_per_gpu))


def resolve_placement_spec(spec, args, src=None, dst=None):
    spec = (spec or "").strip().lower()
    if spec == "all":
        return list(range(1, args.target_gpus + 1))
    if spec == "first":
        return [1]
    if spec == "src":
        return list(src or [])
    if spec == "dst":
        return list(dst or [])

    gpus = []
    seen = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split("-")
        if len(parts) == 1:
            start = end = int(parts[0])
        elif len(parts) == 2:
            start = int(parts[0])
            end = int(parts[1])
        else:
            raise ValueError(f"invalid GPU placement {spec!r}")
        if start <= 0 or end < start or end > args.target_gpus:
            raise ValueError(f"invalid GPU placement {spec!r}")
        for gpu in range(start, end + 1):
            if gpu not in seen:
                gpus.append(gpu)
                seen.add(gpu)
    if not gpus:
        raise ValueError(f"empty GPU placement {spec!r}")
    return gpus


def compact_gpu_list(gpus, args):
    if not gpus:
        return ""
    if gpus == [1]:
        return "first"
    if gpus == list(range(1, args.target_gpus + 1)):
        return "all"

    ranges = []
    start = prev = gpus[0]
    for gpu in gpus[1:]:
        if gpu == prev + 1:
            prev = gpu
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = gpu
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def normalize_gpu_spec(spec, args, src=None, dst=None):
    return compact_gpu_list(resolve_placement_spec(spec, args, src, dst), args)


def distribute_bytes(byte_count, gpus, args):
    page_size = 1 << args.log2_page_size
    num_pages = ceil_div(byte_count, page_size)
    out = {gpu: 0 for gpu in gpus}
    if num_pages == 0:
        return out

    pages_per_gpu = num_pages // len(gpus)
    gpus_to_use = 0
    if pages_per_gpu > 0:
        gpus_to_use = num_pages // pages_per_gpu
    if gpus_to_use > len(gpus):
        gpus_to_use = len(gpus)

    last_gpu_index = 0
    for i in range(gpus_to_use):
        out[gpus[i]] += pages_per_gpu * page_size
        last_gpu_index = i

    remaining_pages = num_pages % len(gpus)
    out[gpus[last_gpu_index]] += remaining_pages * page_size

    if sum(out.values()) > byte_count:
        overage = sum(out.values()) - byte_count
        for gpu in reversed(gpus):
            if out[gpu] >= overage:
                out[gpu] -= overage
                break
    return out


def format_gpu_bytes(gpu_bytes):
    return ";".join(
        f"{gpu}:{byte_count}"
        for gpu, byte_count in gpu_bytes.items()
        if byte_count > 0
    )


def transfer_bytes(flags):
    if op_kind(flags) != "transfer":
        return 0
    return int_flag(flags, "transfer-bytes")


def op_bytes(flags):
    if op_kind(flags) == "transfer":
        return transfer_bytes(flags)
    return output_bytes(flags)


def op_metadata(index, label, flags, args):
    output_spec = op_output_placement(flags, args)
    output_gpus = resolve_placement_spec(output_spec, args)
    compute_spec = op_compute_placement(flags, args)
    compute_gpus = resolve_placement_spec(compute_spec, args)
    byte_count = op_bytes(flags)
    per_gpu_bytes = distribute_bytes(byte_count, output_gpus, args)

    return {
        "op_index": index,
        "label": label,
        "op": op_kind(flags),
        "output_bytes": byte_count,
        "estimated_wg": estimated_wg(flags),
        "compute_gpus": compact_gpu_list(compute_gpus, args),
        "compute_gpu_count": len(compute_gpus),
        "output_gpus": compact_gpu_list(output_gpus, args),
        "output_gpu_count": len(output_gpus),
        "per_gpu_output_bytes": format_gpu_bytes(per_gpu_bytes),
        "flags": " ".join(flags),
    }


def canonical_flags(flags):
    return tuple(sorted(flags))


def op_signature(flags):
    return canonical_flags(flags)


def exp_signature(exp):
    return (
        exp["target"],
        exp["benchmark"],
        tuple(exp.get("common_flags", [])),
        canonical_flags(exp.get("flags", [])),
    )


def analyze_ops(ops, args):
    groups = {}
    for index, (label, flags) in enumerate(ops):
        signature = op_signature(flags)
        group = groups.setdefault(signature, {
            "indices": [],
            "labels": [],
            "flags": flags,
            "metadata": op_metadata(index, label, flags, args),
        })
        group["indices"].append(index)
        group["labels"].append(label)
    return groups


def print_op_analysis(groups):
    total_ops = sum(len(group["indices"]) for group in groups.values())
    duplicate_ops = sum(
        len(group["indices"]) - 1
        for group in groups.values()
        if len(group["indices"]) > 1
    )
    print(
        "LLM op analysis: "
        f"ops={total_ops} unique_op_shapes={len(groups)} "
        f"duplicate_op_occurrences={duplicate_ops}",
        flush=True,
    )
    for signature_id, group in enumerate(
        sorted(groups.values(), key=lambda g: (-len(g["indices"]), g["labels"][0]))
    ):
        if len(group["indices"]) <= 1:
            continue
        metadata = group["metadata"]
        print(
            "  duplicate-shape "
            f"id={signature_id} count={len(group['indices'])} "
            f"op={metadata['op']} wg={metadata['estimated_wg']} "
            f"bytes={metadata['output_bytes']} "
            f"labels={';'.join(group['labels'][:6])}"
            f"{';...' if len(group['labels']) > 6 else ''}",
            flush=True,
        )


def write_op_analysis_report(args, ops):
    groups = analyze_ops(ops, args)
    print_op_analysis(groups)

    path = Path(runall2.output_dir) / "llm_decomposed_op_analysis.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "signature_id",
            "count",
            "op",
            "output_bytes",
            "estimated_wg",
            "compute_gpus",
            "output_gpus",
            "indices",
            "labels",
            "flags",
        ])
        for signature_id, group in enumerate(
            sorted(groups.values(), key=lambda g: (g["indices"][0], g["labels"][0]))
        ):
            metadata = group["metadata"]
            writer.writerow([
                signature_id,
                len(group["indices"]),
                metadata["op"],
                metadata["output_bytes"],
                metadata["estimated_wg"],
                metadata["compute_gpus"],
                metadata["output_gpus"],
                ";".join(str(index) for index in group["indices"]),
                ";".join(group["labels"]),
                " ".join(group["flags"]),
            ])
    print(f"Wrote op analysis: {path}", flush=True)
    return groups


def dedupe_experiments(exps):
    representative_by_signature = {}
    unique = []
    duplicates = []

    for exp in exps:
        signature = exp_signature(exp)
        representative = representative_by_signature.get(signature)
        if representative is None:
            representative_by_signature[signature] = exp
            unique.append(exp)
            continue
        duplicates.append((representative, exp))

    return unique, duplicates


def exp_file_paths(exp):
    stem = Path(runall2.exp_file_stem(exp))
    return {
        "metrics": stem.with_name(stem.name + "_metrics.csv"),
        "stdout": stem.with_name(stem.name + "_out.stdout"),
    }


def write_dedupe_report(duplicates):
    path = Path(runall2.output_dir) / "llm_decomposed_dedup.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "representative_config_name",
            "duplicate_config_name",
            "representative_metrics",
            "duplicate_metrics",
        ])
        for representative, duplicate in duplicates:
            rep_paths = exp_file_paths(representative)
            dup_paths = exp_file_paths(duplicate)
            writer.writerow([
                representative["config_name"],
                duplicate["config_name"],
                rep_paths["metrics"].name,
                dup_paths["metrics"].name,
            ])
    print(f"Wrote dedupe report: {path}", flush=True)


def materialize_duplicate_outputs(duplicates):
    if not duplicates:
        return

    copied = 0
    missing = 0
    for representative, duplicate in duplicates:
        rep_paths = exp_file_paths(representative)
        dup_paths = exp_file_paths(duplicate)
        for key in ("metrics", "stdout"):
            src = rep_paths[key]
            dst = dup_paths[key]
            if not src.exists():
                missing += 1
                print(
                    f"Cannot materialize duplicate {dst.name}: missing {src}",
                    flush=True,
                )
                continue
            shutil.copyfile(src, dst)
            copied += 1
    print(
        f"Materialized duplicate llmop outputs: copied={copied} missing={missing}",
        flush=True,
    )


def insert_transfer_ops(ops, args):
    if len(ops) < 2:
        return ops

    out = []
    for i, (label, flags) in enumerate(ops[:-1]):
        next_label, next_flags = ops[i + 1]
        out.append((label, flags))

        src = op_output_placement(flags, args)
        dst = op_compute_placement(next_flags, args)
        if src == dst:
            continue

        bytes_to_move = output_bytes(flags)
        if bytes_to_move <= 0:
            continue

        transfer_label = f"{label}_to_{next_label}_transfer"
        transfer_flags = [
            "-op=transfer",
            f"-transfer-bytes={bytes_to_move}",
            f"-src-gpus={src}",
            f"-dst-gpus={dst}",
            f"-copy-gpus={args.transfer_copy_gpus}",
        ]
        out.append((transfer_label, transfer_flags))

    out.append(ops[-1])
    return out


def write_placement_report(args, ops):
    path = Path(args.placement_output)
    if not path.is_absolute():
        path = Path(runall2.output_dir) / path

    fieldnames = [
        "op_index",
        "label",
        "op",
        "output_bytes",
        "estimated_wg",
        "compute_gpus",
        "compute_gpu_count",
        "output_gpus",
        "output_gpu_count",
        "per_gpu_output_bytes",
        "flags",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for index, (label, flags) in enumerate(ops):
            writer.writerow(op_metadata(index, label, flags, args))
    print(f"Wrote placement report: {path}")


def llm_mixed_config_name(config, op_flags):
    if config != "llm_mixed":
        return config

    op = op_kind(op_flags)
    if op in {"linear", "split-linear"}:
        return "sample_all_loop"
    return "sample_wf"


def benchmark_for_model(args):
    if args.model == "resnet":
        return "resnet"
    return "llmop"


def build_exps(args, ops=None):
    if ops is None:
        ops = prepare_ops(args)

    common_flags = runall2.BASE_COMMON_FLAGS[:]
    if args.target == "400latency":
        common_flags.append(
            f"-mmutlb-ptcl-return-latency={runall2.DEFAULT_MMUTLB_PTCL_RETURN_LATENCY}"
        )
        common_flags.append(
            f"-gmmu-pte-lookup-latency={runall2.DEFAULT_GMMU_PTE_LOOKUP_LATENCY}"
        )
    else:
        common_flags.append(
            f"-mmutlb-lookup-latency={runall2.DEFAULT_MMUTLB_LOOKUP_LATENCY}"
        )
    exps = []
    benchmark = benchmark_for_model(args)
    for index, (label, flags) in enumerate(ops):
        for config, selected_flags in selected_config_flags(args, flags):
            exp_flags = benchmark_flags(args) + selected_flags + flags
            exp_flags = add_default_photon_flags(args, exp_flags)
            if args.log_subtasks:
                if benchmark == "resnet":
                    exp_flags.append("-resnet-log-subtasks")
                else:
                    exp_flags.append("-llmop-log-subtasks")
            exps.append({
                "target": args.target,
                "benchmark": benchmark,
                "config_name": (
                    f"{args.model}_{args.profile}_{index:03d}_{label}_{config}"
                ),
                "common_flags": common_flags,
                "flags": exp_flags,
            })
    return exps


def resnet_profile(profile_name):
    if profile_name not in RESNET_PROFILES:
        raise ValueError(f"unknown ResNet profile {profile_name!r}")
    return dict(RESNET_PROFILES[profile_name])


def resnet_flags(args):
    profile_config = resnet_profile(args.profile)
    mode = args.resnet_mode or profile_config["mode"]
    depth = args.resnet_depth or profile_config["depth"]
    batch_size = args.resnet_batch_size or profile_config["batch_size"]
    image_size = args.resnet_image_size or profile_config["image_size"]

    if mode not in {"block", "full"}:
        raise ValueError("--resnet-mode must be block or full")
    if depth not in {18, 34, 50}:
        raise ValueError("--resnet-depth must be 18, 34, or 50")
    if batch_size <= 0:
        raise ValueError("--resnet-batch-size must be positive")
    if image_size <= 0:
        raise ValueError("--resnet-image-size must be positive")

    return [
        f"-resnet-mode={mode}",
        f"-resnet-depth={depth}",
        f"-resnet-batch-size={batch_size}",
        f"-resnet-image-size={image_size}",
    ]


def prepare_ops(args):
    split_k = parse_split_k(args.split_k)
    if args.layers < 0:
        raise ValueError("--layers must be non-negative")
    if args.seq_len < 0:
        raise ValueError("--seq-len must be non-negative")
    if args.target_gpus <= 0:
        raise ValueError("--target-gpus must be positive")
    if args.cu_per_gpu <= 0:
        raise ValueError("--cu-per-gpu must be positive")
    if args.max_split_k <= 0:
        raise ValueError("--max-split-k must be positive")
    if args.split_k_target_input_chunk <= 0:
        raise ValueError("--split-k-target-input-chunk must be positive")
    if args.log2_page_size <= 0:
        raise ValueError("--log2-page-size must be positive")

    if args.model == "resnet":
        ops = [("full", resnet_flags(args))]
    else:
        config_split_k = 1 if split_k == "auto" else split_k

        if args.model == "bert":
            profile_config = dict(bertconfig.profile(args.profile))
            if args.layers > 0:
                profile_config["layers"] = args.layers
            if args.seq_len > 0:
                profile_config["seq_len"] = args.seq_len
            if args.bert_num_labels <= 0:
                raise ValueError("--bert-num-labels must be positive")
            profile_config["num_labels"] = args.bert_num_labels
            ops = bertconfig.bert_ops(profile_config, config_split_k)
        elif args.model == "gpt":
            profile_config = dict(gptconfig.profile(args.profile))
            if args.layers > 0:
                profile_config["layers"] = args.layers
            if args.seq_len > 0:
                profile_config["seq_len"] = args.seq_len
            if args.gpt_decode_steps < 0:
                raise ValueError("--gpt-decode-steps must be non-negative")
            if args.gpt_decode_context_len < 0:
                raise ValueError("--gpt-decode-context-len must be non-negative")
            profile_config["decode_steps"] = args.gpt_decode_steps
            profile_config["decode_context_len"] = (
                args.gpt_decode_context_len or profile_config["seq_len"]
            )
            ops = gptconfig.gpt_ops(profile_config, config_split_k)
        else:
            raise ValueError(f"unknown model {args.model!r}")
        if split_k == "auto":
            ops = [
                (label, auto_split_linear_flags(flags, args))
                for label, flags in ops
            ]
        if args.balance_split_k_by_input:
            ops = [
                (label, balance_split_linear_flags(flags, args))
                for label, flags in ops
            ]
    label_filters = parse_csv(args.op_label_filter)
    if label_filters:
        ops = [
            (label, flags)
            for label, flags in ops
            if label_matches_any(label, label_filters)
        ]
    if args.limit > 0:
        ops = ops[:args.limit]
    if args.include_transfers:
        ops = insert_transfer_ops(ops, args)
    return ops


def prepare_model_runs(args):
    runs = []
    for spec in resolve_model_specs(args):
        model_args = args_for_model(args, spec)
        ops = prepare_ops(model_args)
        runs.append((model_args, ops))
    return runs


def round_robin_lists(items_by_model):
    out = []
    max_len = max((len(items) for items in items_by_model), default=0)
    for index in range(max_len):
        for items in items_by_model:
            if index < len(items):
                out.append(items[index])
    return out


def ordered_model_items(args, items_by_model):
    if args.model_schedule == "grouped" or len(items_by_model) <= 1:
        out = []
        for items in items_by_model:
            out += items
        return out
    return round_robin_lists(items_by_model)


def flatten_ops_for_reports(args, model_runs):
    items_by_model = []
    for model_args, ops in model_runs:
        items = []
        for label, flags in ops:
            items.append((
                f"{model_args.model}:{model_args.profile}:{label}",
                flags,
            ))
        items_by_model.append(items)
    return ordered_model_items(args, items_by_model)


def build_model_exps(args, model_runs):
    exps_by_model = []
    for model_args, ops in model_runs:
        exps_by_model.append(build_exps(model_args, ops))
    return ordered_model_items(args, exps_by_model)


def summarize_output(args):
    summarize_results(Path(runall2.output_dir), "", "", True)


def parse_result_id(path):
    suffix = "_metrics.csv"
    name = path.name
    if not name.endswith(suffix):
        raise ValueError(f"not a metrics file: {name}")

    stem = name[: -len(suffix)]
    parts = stem.split("_")
    if len(parts) < 7:
        raise ValueError(f"cannot parse decomposed llmop filename: {name}")

    target = parts[0]
    benchmark = parts[1]
    model = parts[2]
    profile = parts[3]
    try:
        op_index = int(parts[4])
    except ValueError as err:
        raise ValueError(f"invalid op index in {name}") from err

    rest = "_".join(parts[5:])
    for config in SUMMARY_CONFIG_NAMES:
        marker = "_" + config
        if rest.endswith(marker):
            op_name = rest[: -len(marker)]
            if op_name:
                return ResultID(
                    target, benchmark, model, profile, op_index, op_name,
                    config)

    raise ValueError(f"cannot find config suffix in {name}")


def parse_metrics(path):
    driver_kernel_time = 0.0
    driver_total_time = 0.0
    cp_kernel_time = 0.0
    max_cp_kernel_time = 0.0

    with path.open(newline="") as f:
        reader = csv.reader(f, skipinitialspace=True)
        for row in reader:
            if len(row) < 4:
                continue

            where = row[1].strip()
            what = row[2].strip()
            try:
                value = float(row[3].strip())
            except ValueError:
                continue

            if where == "Driver" and what == "kernel_time":
                driver_kernel_time = value
            elif where == "Driver" and what == "total_time":
                driver_total_time = value
            elif where.endswith(".CommandProcessor") and what == "kernel_time":
                cp_kernel_time += value
                max_cp_kernel_time = max(max_cp_kernel_time, value)

    return (
        driver_kernel_time,
        driver_total_time,
        cp_kernel_time,
        max_cp_kernel_time,
    )


def elapsed_to_seconds(text):
    parts = text.strip().split(":")
    if len(parts) != 3:
        return 0.0
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_stdout(path):
    elapsed = 0.0
    return_code = ""
    if not path.exists():
        return elapsed, return_code

    with path.open(errors="replace") as f:
        for line in f:
            if line.startswith("Return code:"):
                return_code = line.split(":", 1)[1].strip()
            elif line.startswith("Elapsed time:"):
                elapsed = elapsed_to_seconds(line.split(":", 1)[1].strip())

    return elapsed, return_code


def matching_records(results_dir, model_filter, profile_filter):
    records = []
    skipped = []
    for metrics_path in sorted(results_dir.glob("*_*_*_metrics.csv")):
        try:
            result_id = parse_result_id(metrics_path)
        except ValueError as err:
            skipped.append((metrics_path.name, str(err)))
            continue

        if model_filter and result_id.model != model_filter:
            continue
        if profile_filter and result_id.profile != profile_filter:
            continue
        if result_id.benchmark not in {"llmop", "resnet"}:
            continue

        stdout_path = metrics_path.with_name(
            metrics_path.name.replace("_metrics.csv", "_out.stdout"))
        metrics = parse_metrics(metrics_path)
        elapsed, return_code = parse_stdout(stdout_path)
        records.append(MetricRecord(
            result_id=result_id,
            metrics_path=metrics_path,
            stdout_path=stdout_path,
            driver_kernel_time=metrics[0],
            driver_total_time=metrics[1],
            command_processor_kernel_time=metrics[2],
            max_command_processor_kernel_time=metrics[3],
            stdout_elapsed_seconds=elapsed,
            return_code=return_code,
        ))

    return records, skipped


def summarize_records(records):
    groups = {}
    for record in records:
        rid = record.result_id
        key = (rid.model, rid.profile, rid.config)
        group = groups.setdefault(key, {
            "ops": 0,
            "driver_total_time": 0.0,
            "driver_kernel_time": 0.0,
            "command_processor_kernel_time": 0.0,
            "max_command_processor_kernel_time_sum": 0.0,
            "stdout_elapsed_seconds": 0.0,
            "ok": 0,
            "missing_return_code": 0,
        })
        group["ops"] += 1
        group["driver_total_time"] += record.driver_total_time
        group["driver_kernel_time"] += record.driver_kernel_time
        group["command_processor_kernel_time"] += (
            record.command_processor_kernel_time)
        group["max_command_processor_kernel_time_sum"] += (
            record.max_command_processor_kernel_time)
        group["stdout_elapsed_seconds"] += record.stdout_elapsed_seconds
        if record.return_code == "0":
            group["ok"] += 1
        elif record.return_code == "":
            group["missing_return_code"] += 1
    return groups


def write_per_op_summary(path, records):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model",
            "profile",
            "config",
            "op_index",
            "op_name",
            "driver_total_time",
            "driver_kernel_time",
            "command_processor_kernel_time_sum",
            "max_command_processor_kernel_time",
            "stdout_elapsed_seconds",
            "return_code",
            "metrics_file",
            "stdout_file",
        ])
        for record in records:
            rid = record.result_id
            writer.writerow([
                rid.model,
                rid.profile,
                rid.config,
                rid.op_index,
                rid.op_name,
                f"{record.driver_total_time:.12f}",
                f"{record.driver_kernel_time:.12f}",
                f"{record.command_processor_kernel_time:.12f}",
                f"{record.max_command_processor_kernel_time:.12f}",
                f"{record.stdout_elapsed_seconds:.6f}",
                record.return_code,
                record.metrics_path.name,
                record.stdout_path.name,
            ])


def write_config_summary(path, groups):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "model",
            "profile",
            "config",
            "ops",
            "driver_total_time_sum",
            "driver_total_time_us",
            "driver_kernel_time_sum",
            "command_processor_kernel_time_sum",
            "command_processor_kernel_time_us",
            "max_command_processor_kernel_time_sum",
            "stdout_elapsed_seconds_sum",
            "return_code_0_count",
            "missing_return_code_count",
        ])
        for (model, profile, config), group in sorted(groups.items()):
            writer.writerow([
                model,
                profile,
                config,
                group["ops"],
                f"{group['driver_total_time']:.12f}",
                f"{group['driver_total_time'] * 1e6:.3f}",
                f"{group['driver_kernel_time']:.12f}",
                f"{group['command_processor_kernel_time']:.12f}",
                f"{group['command_processor_kernel_time'] * 1e6:.3f}",
                f"{group['max_command_processor_kernel_time_sum']:.12f}",
                f"{group['stdout_elapsed_seconds']:.6f}",
                group["ok"],
                group["missing_return_code"],
            ])


def print_summary(groups):
    print("model profile config ops driver_total_us cp_kernel_us stdout_elapsed_s ok")
    for (model, profile, config), group in sorted(groups.items()):
        print(
            f"{model} {profile} {config} {group['ops']} "
            f"{group['driver_total_time'] * 1e6:.3f} "
            f"{group['command_processor_kernel_time'] * 1e6:.3f} "
            f"{group['stdout_elapsed_seconds']:.3f} "
            f"{group['ok']}/{group['ops']}"
        )


def summarize_results(results_dir, model_filter="", profile_filter="", show=False):
    if not results_dir.is_dir():
        raise ValueError(f"results directory does not exist: {results_dir}")

    records, skipped = matching_records(results_dir, model_filter, profile_filter)
    if not records:
        raise RuntimeError(f"no decomposed llmop metrics found in {results_dir}")

    records.sort(key=lambda r: (
        r.result_id.model,
        r.result_id.profile,
        r.result_id.config,
        r.result_id.op_index,
        r.result_id.op_name,
    ))
    groups = summarize_records(records)

    summary_path = results_dir / "llm_decomposed_summary.csv"
    per_op_path = results_dir / "llm_decomposed_per_op_summary.csv"
    write_config_summary(summary_path, groups)
    write_per_op_summary(per_op_path, records)

    print(f"Results dir: {results_dir}")
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote per-op summary: {per_op_path}")
    print(f"Matched metrics: {len(records)}")
    if skipped:
        print(f"Skipped files: {len(skipped)}")
    if show:
        print_summary(groups)


def print_dry_run(exps):
    for exp in exps:
        binary = f'{runall2.ROOT_DIR}/{exp["target"]}/{exp["target"]}'
        cmd = [
            binary,
            f'-benchmark={exp["benchmark"]}',
            *exp["common_flags"],
            *exp["flags"],
            "-metric-file-name=<output>",
        ]
        print(shlex.join(cmd))


def validate_scheduler_args(args):
    if args.max_workers < 0:
        raise ValueError("--max-workers must be non-negative")
    if args.max_workloads <= 0:
        raise ValueError("--max-workloads must be greater than 0")
    if args.max_workloads > runall2.DEFAULT_MAX_WORKLOADS:
        raise ValueError(
            f"--max-workloads cannot exceed {runall2.DEFAULT_MAX_WORKLOADS}"
        )
    if args.min_free_ram_gb < 0:
        raise ValueError("--min-free-ram-gb must be non-negative")
    if args.memory_scan_interval_minutes <= 0:
        raise ValueError("--memory-scan-interval-minutes must be greater than 0")
    if args.timeout_minutes < 0:
        raise ValueError("--timeout-minutes must be non-negative")


def main():
    args = parse_args()
    model_runs = prepare_model_runs(args)
    ops = flatten_ops_for_reports(args, model_runs)
    exps = build_model_exps(args, model_runs)
    validate_scheduler_args(args)
    if args.auto_dedupe:
        args.analyze_ops = True
        args.dedupe_identical_ops = True
    if args.dedupe_identical_ops or args.analyze_only:
        args.analyze_ops = True

    if args.analyze_only:
        runall2.create_output_dir()
        write_placement_report(args, ops)
        write_op_analysis_report(args, ops)
        if args.dedupe_identical_ops:
            unique_exps, duplicates = dedupe_experiments(exps)
            print(
                "LLM dedupe analysis: "
                f"original_experiments={len(exps)} "
                f"unique_experiments={len(unique_exps)} "
                f"saved={len(duplicates)}",
                flush=True,
            )
            write_dedupe_report(duplicates)
        return

    if args.dry_run:
        if args.analyze_ops:
            print_op_analysis(analyze_ops(ops, args))
        dry_run_exps = exps
        if args.dedupe_identical_ops:
            dry_run_exps, duplicates = dedupe_experiments(exps)
            print(
                "LLM dedupe dry-run: "
                f"original_experiments={len(exps)} "
                f"unique_experiments={len(dry_run_exps)} "
                f"saved={len(duplicates)}",
                flush=True,
            )
        print_dry_run(dry_run_exps)
        for index, (label, flags) in enumerate(ops):
            metadata = op_metadata(index, label, flags, args)
            print(
                "# placement "
                f"{metadata['op_index']} {metadata['label']} "
                f"op={metadata['op']} "
                f"bytes={metadata['output_bytes']} "
                f"compute={metadata['compute_gpus']} "
                f"output={metadata['output_gpus']} "
                f"per_gpu={metadata['per_gpu_output_bytes']}"
            )
        return

    runall2.create_output_dir()
    write_placement_report(args, ops)
    if args.analyze_ops:
        write_op_analysis_report(args, ops)

    run_exps = exps
    duplicates = []
    if args.dedupe_identical_ops:
        run_exps, duplicates = dedupe_experiments(exps)
        saved = len(exps) - len(run_exps)
        print(
            "LLM dedupe: "
            f"original_experiments={len(exps)} "
            f"unique_experiments={len(run_exps)} saved={saved}",
            flush=True,
        )
        write_dedupe_report(duplicates)

    timeout_seconds = int(args.timeout_minutes * 60)
    for exp in run_exps:
        exp["timeout_seconds"] = timeout_seconds
    runall2.build_targets(run_exps)
    runall2.memory_gated_run(run_exps, args)

    if args.dedupe_identical_ops:
        materialize_duplicate_outputs(duplicates)

    if args.summarize:
        summarize_output(args)


if __name__ == "__main__":
    main()
