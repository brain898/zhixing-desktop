"""顺序复用 M02-B~E UI 脚本，证据重定向，保留前阶段截图和导出。"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
STAGES = {"b": "verify_m02b_scene_catalog_ui.py", "c": "verify_m02c_batch_ui.py",
          "d": "verify_m02d_review_ui.py", "e": "verify_m02e_stale_ui.py"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES)
    args = parser.parse_args()
    out = ROOT / "artifacts/m02f/regression_ui"
    out.mkdir(parents=True, exist_ok=True)
    if args.stage:
        os.environ["ZHIXING_DATA_DIR"] = str(Path(tempfile.gettempdir()) / "zhixing_m02f_ui_regression")
        os.environ["ZHIXING_DB_PATH"] = str(Path(os.environ["ZHIXING_DATA_DIR"]) / "zhixing.db")
        spec = importlib.util.spec_from_file_location("m02f_ui_regression", ROOT / "tests" / STAGES[args.stage])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        dest = out / args.stage
        dest.mkdir(parents=True, exist_ok=True)
        module.SHOTS = dest
        if hasattr(module, "DOWNLOADS"):
            module.DOWNLOADS = dest
        return module.main()
    results = {}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for stage in STAGES:
        with (out / f"{stage}.txt").open("w", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, __file__, "--stage", stage], cwd=ROOT,
                                    env=env, stdout=log, stderr=subprocess.STDOUT)
        results[stage] = result.returncode
        print(f"M02-{stage.upper()} UI exit={result.returncode}", flush=True)
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
