import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
portable_root_env = os.getenv("ZHIXING_PORTABLE_ROOT")
if portable_root_env:
    load_dotenv(Path(portable_root_env) / ".env")
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")
if BASE_DIR.parent.parent.exists():
    load_dotenv(BASE_DIR.parent.parent / ".env")

def get_data_dir() -> Path:
    custom = os.getenv("ZHIXING_DATA_DIR")
    if custom:
        d = Path(custom).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    portable_root = os.getenv("ZHIXING_PORTABLE_ROOT")
    if portable_root:
        d = (Path(portable_root) / "data").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    # 便携包运行时检测：位于 <portable_root>/backend/server
    candidate_root = BASE_DIR.parent.parent
    if (candidate_root / "backend" / "python").exists():
        d = (candidate_root / "data").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    # 默认源码开发路径
    d = BASE_DIR / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d

DATA_DIR = get_data_dir()

def ensure_first_run_initialized(data_dir: Path):
    """首次初始化：若用户数据目录中不存在 zhixing.db，从随包快照模板拷贝；后续启动决不覆盖已修改数据。"""
    db_file = data_dir / "zhixing.db"
    if db_file.exists():
        return
    template_candidates = [
        BASE_DIR.parent / "snapshot" / "data",
        BASE_DIR.parent.parent / "backend" / "snapshot" / "data",
        BASE_DIR / "template_data",
    ]
    for tmpl in template_candidates:
        tmpl_db = tmpl / "zhixing.db"
        if tmpl_db.exists():
            import shutil
            data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmpl_db, db_file)
            tmpl_storage = tmpl / "storage"
            target_storage = data_dir / "storage"
            target_storage.mkdir(parents=True, exist_ok=True)
            if tmpl_storage.exists():
                for item in tmpl_storage.iterdir():
                    if item.is_file() and not (target_storage / item.name).exists():
                        shutil.copy2(item, target_storage / item.name)
            break

ensure_first_run_initialized(DATA_DIR)

def get_storage_dir() -> Path:
    custom = os.getenv("ZHIXING_STORAGE_DIR")
    if custom:
        storage_dir = Path(custom).resolve()
    else:
        storage_dir = DATA_DIR / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir

STORAGE_DIR = get_storage_dir()

def resolve_storage_path(storage_ref: str) -> Path:
    """跨机器与便携包路径解析：优先匹配存在的文件，自动容错绝对路径与相对文件名。"""
    if not storage_ref:
        return STORAGE_DIR / "missing_file"
    ref_path = Path(storage_ref)
    if ref_path.is_absolute() and ref_path.exists():
        return ref_path
    # 优先在当前数据 storage 目录按文件名查找
    storage_dir = get_storage_dir()
    candidate = storage_dir / ref_path.name
    if candidate.exists():
        return candidate
    # 相对路径尝试
    rel_candidate = storage_dir / storage_ref
    if rel_candidate.exists():
        return rel_candidate
    return candidate

def get_db_path() -> Path:
    custom = os.getenv("ZHIXING_DB_PATH")
    if custom:
        return Path(custom).resolve()
    db_url = os.getenv("DATABASE_URL")
    if db_url and db_url.startswith("sqlite:///"):
        return Path(db_url[10:]).resolve()
    return DATA_DIR / "zhixing.db"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{get_db_path()}")
SECRET_KEY = os.getenv("ZHIXING_SECRET_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT_SECONDS = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45"))
DEEPSEEK_MAX_OUTPUT_TOKENS = int(os.getenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "4096"))
DEEPSEEK_EXTRACT_BATCH_MAX_BLOCKS = int(os.getenv("DEEPSEEK_EXTRACT_BATCH_MAX_BLOCKS", "8"))
DEEPSEEK_EXTRACT_BATCH_MAX_CHARS = int(os.getenv("DEEPSEEK_EXTRACT_BATCH_MAX_CHARS", "9000"))
DEEPSEEK_EXTRACT_BATCH_OVERLAP = int(os.getenv("DEEPSEEK_EXTRACT_BATCH_OVERLAP", "1"))
# M02 Skill 候选生成：单个候选 JSON 较长，单独给出等待时间与输出长度上限（可用环境变量覆盖）
SKILL_MODEL_TIMEOUT_SECONDS = int(os.getenv("SKILL_MODEL_TIMEOUT_SECONDS", "180"))
SKILL_MODEL_MAX_OUTPUT_TOKENS = int(os.getenv("SKILL_MODEL_MAX_OUTPUT_TOKENS", "8192"))
# 生成提示词文件（server/prompts/ 下）；2026-09-27 经真实对比后默认切换为 v2（用户确认）
SKILL_GENERATE_PROMPT = os.getenv("SKILL_GENERATE_PROMPT", "skill_generate_v2.md")

# Jev 仅承担候选知识的分类建议与语义质检。默认关闭，避免升级后对既有知识库
# 自动产生付费调用；密钥只从服务端环境读取，不进入客户端或数据库。
JEV_ENABLED = os.getenv("JEV_ENABLED", "false").lower() == "true"
JEV_API_KEY = os.getenv("TYPESAFE_API_KEY", os.getenv("JEV_API_KEY", ""))
JEV_API_URL = os.getenv("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
JEV_TIMEOUT_SECONDS = float(os.getenv("JEV_TIMEOUT_SECONDS", "30"))
JEV_MAX_RETRIES = max(0, min(int(os.getenv("JEV_MAX_RETRIES", "2")), 3))
JEV_QUESTION_DEFINITION_VERSION = os.getenv("JEV_QUESTION_DEFINITION_VERSION", "zhixing-jev-v1")
# 尚未用业务标注样本校准，只用于人工审核排序，绝不用于自动确认或免审。
JEV_REVIEW_HIGH_PROBABILITY = float(os.getenv("JEV_REVIEW_HIGH_PROBABILITY", "0.65"))
JEV_REVIEW_MEDIUM_PROBABILITY = float(os.getenv("JEV_REVIEW_MEDIUM_PROBABILITY", "0.45"))

def get_embedding_model_path() -> str:
    custom = os.getenv("EMBEDDING_MODEL_NAME")
    if custom:
        p = Path(custom)
        if p.exists():
            return str(p.resolve())
        return custom
    # 便携包模型目录检测
    candidates = [
        BASE_DIR.parent / "models" / "bge-small-zh-v1.5",
        BASE_DIR.parent.parent / "backend" / "models" / "bge-small-zh-v1.5",
        BASE_DIR / "models" / "bge-small-zh-v1.5",
    ]
    for c in candidates:
        if c.exists() and (c / "config.json").exists():
            return str(c.resolve())
    return "BAAI/bge-small-zh-v1.5"

# Stage 4B：正式混合检索配置。DeepSeek 仅用于知识抽取，不承担 embedding。
EMBEDDING_MODEL_NAME = get_embedding_model_path()
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "512"))
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
RETRIEVAL_INDEX_VERSION = int(os.getenv("RETRIEVAL_INDEX_VERSION", "2"))
RETRIEVAL_CONFIG_VERSION = os.getenv("RETRIEVAL_CONFIG_VERSION", "hybrid-bge-small-zh-v1.5-v1")
RETRIEVAL_RRF_K = int(os.getenv("RETRIEVAL_RRF_K", "60"))
RETRIEVAL_DENSE_MIN_SCORE = float(os.getenv("RETRIEVAL_DENSE_MIN_SCORE", "0.58"))
RETRIEVAL_CANDIDATE_LIMIT = int(os.getenv("RETRIEVAL_CANDIDATE_LIMIT", "50"))

SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8766"))
ENVIRONMENT = os.getenv("ZHIXING_ENV", "development")
ENABLE_DEMO_SEED = os.getenv("ZHIXING_ENABLE_DEMO_SEED", "false").lower() == "true"
ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "ZHIXING_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,null",
    ).split(",")
    if value.strip()
]

# 文件导入与任务规格（默认基准值，支持环境覆盖）
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", str(20 * 1024 * 1024)))  # 20 MB
MAX_BATCH_FILES = int(os.getenv("MAX_BATCH_FILES", "10"))
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}
