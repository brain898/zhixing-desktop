"""复用 M02-C 真实联调准备流程，在新隔离目录导入四份资料并遍历全部可用场景。

不删除、不复制业务库，不覆盖 M02-C 证据。重复执行续跑同一演示目录。
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("ZHIXING_M02F_DEMO_DIR", str(Path(tempfile.gettempdir()) / "zhixing_m02f_demo"))).resolve()
if DATA == ROOT or ROOT in DATA.parents:
    raise SystemExit("演示目录必须在代码目录外，禁止使用业务目录")
# 当前数据目录显式覆盖，避免环境继承用户库或上传目录。
os.environ["ZHIXING_DATA_DIR"] = str(DATA)
os.environ["ZHIXING_DB_PATH"] = str(DATA / "zhixing.db")
os.environ["ZHIXING_STORAGE_DIR"] = str(DATA / "storage")
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.argv = [str(ROOT / "tests/run_m02c_live_generation.py"), "--data-dir", str(DATA), "--max-scenes", "10000"]
spec = importlib.util.spec_from_file_location("m02f_live_preparation", ROOT / "tests/run_m02c_live_generation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT_DIR = ROOT / "artifacts/m02f"
module.main()
