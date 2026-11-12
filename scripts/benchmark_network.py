"""Compare ramp extraction against saved baseline code on the same checkpoint."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from roadmri.config import load_config
from roadmri.network.ramps import extract_ramps
from roadmri.pipeline import load_state


def signature(ramps):
    def encode(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        return str(value)

    return json.dumps([asdict(r) for r in ramps], default=encode, sort_keys=True, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", default="california")
    parser.add_argument("--limit", type=int, default=3, help="0 means all facilities")
    parser.add_argument("--baseline-dir", type=Path, default=Path("data/optimization-baseline"))
    args = parser.parse_args()
    cfg = load_config(Path.cwd())
    ctx = load_state(cfg, args.range, "facilities")
    facs = ctx.facs[: args.limit] if args.limit else ctx.facs
    path = args.baseline_dir / "src/roadmri/network/ramps.py"
    spec = importlib.util.spec_from_file_location("roadmri.network.baseline_ramps", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    timings = {}
    results = []
    for name, func in (("before", module.extract_ramps), ("after", extract_ramps)):
        start = time.perf_counter()
        ramps = func(ctx.rg, facs, float(cfg.analysis["curvature"]["loop_turn_deg"]))
        timings[name] = time.perf_counter() - start
        results.append(signature(ramps))
    if results[0] != results[1]:
        raise AssertionError("Baseline and optimized ramp outputs differ")
    print(
        json.dumps(
            {
                "region": args.range,
                "facilities": len(facs),
                "ramps": len(ramps),
                "seconds": timings,
                "speedup": timings["before"] / timings["after"],
                "outputs_equal": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
