"""M03 验收：每套 M01/M02 测试独立进程，留存完整日志，不接触业务库。"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'test_auth_and_permissions.py', 'test_documents.py', 'test_knowledge_atoms.py',
    'test_deepseek_batching.py', 'test_jev_integration.py', 'test_source_fidelity_regression.py',
    'test_structured_review_quality.py', 'test_audit_relief_review.py',
    'test_version_governance_lifecycle.py', 'test_stage4a_eligibility.py',
    'test_stage4b_hybrid_retrieval.py', 'test_stage4c_admin_search.py',
    'test_stage4e_benchmark_suite.py', 'test_stage4e_full_lifecycle.py',
    'test_m02a_schema_validation.py', 'test_m02b_scene_catalog.py',
    'test_m02c_generation.py', 'test_m02d_review.py', 'test_m02e_stale_review.py',
    'test_m02f_statistics.py', 'test_m02_scene_card_cache.py', 'test_m02_scene_card_async.py',
)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label', default='final')
    args = p.parse_args()
    folder = ROOT / 'artifacts' / 'm03' / ('regression_' + args.label)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    # 子进程导入 config 前先隔离三项路径，测试自身再使用 setup_test_db。
    import tempfile
    isolation = Path(tempfile.mkdtemp(prefix='zhixing_m03_regression_'))
    env.update(ZHIXING_DATA_DIR=str(isolation), ZHIXING_DB_PATH=str(isolation/'zhixing.db'),
               ZHIXING_STORAGE_DIR=str(isolation/'storage'))
    results = []
    for name in FILES:
        command = [sys.executable, str(ROOT / 'tests' / name)]
        try:
            r = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=600)
            output = r.stdout + r.stderr
            code = r.returncode
        except subprocess.TimeoutExpired as e:
            output = 'TIMEOUT after 600 seconds'; code = 124
        (folder/(name+'.log')).write_text(output, encoding='utf-8')
        match = re.search(r'Ran (\d+) tests?', output)
        fail = re.search(r'failures=(\d+)', output)
        error = re.search(r'errors=(\d+)', output)
        skip = re.search(r'skipped=(\d+)', output)
        entry = {'file':name, 'exit_code':code,'total':int(match[1]) if match else 0,
                 'failures':int(fail[1]) if fail else 0,'errors':int(error[1]) if error else 0,
                 'skipped':int(skip[1]) if skip else 0,
                 'failed_tests':re.findall(r'^(?:FAIL|ERROR): (.+)$',output,re.M)}
        results.append(entry)
        print(json.dumps(entry,ensure_ascii=False),flush=True)
        (folder/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return 1 if any(r['exit_code'] for r in results) else 0

if __name__ == '__main__':
    raise SystemExit(main())
