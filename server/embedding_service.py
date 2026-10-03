"""Stage 4B 中文 Dense embedding 服务。"""
from __future__ import annotations

import threading
from typing import Iterable, List

from config import EMBEDDING_MODEL_NAME, EMBEDDING_DIM, EMBEDDING_DEVICE

_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "缺少 sentence-transformers，无法执行真实语义检索"
                ) from exc
            from pathlib import Path
            model_path = Path(EMBEDDING_MODEL_NAME)
            is_local = model_path.exists() and (model_path / "config.json").exists()
            model = SentenceTransformer(
                EMBEDDING_MODEL_NAME,
                device=EMBEDDING_DEVICE,
                local_files_only=is_local,
            )
            actual_dim = int(model.get_sentence_embedding_dimension() or 0)
            if actual_dim != EMBEDDING_DIM:
                raise RuntimeError(
                    f"Embedding 维度不一致：config={EMBEDDING_DIM}, actual={actual_dim}"
                )
            _model = model
    return _model


def embed_texts(texts: Iterable[str]) -> List[List[float]]:
    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    if any(not value for value in values):
        raise ValueError("embedding 文本不能为空")

    model = _load_model()
    vectors = model.encode(
        values,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    if len(vectors.shape) != 2 or vectors.shape[1] != EMBEDDING_DIM:
        raise RuntimeError(
            f"Embedding 输出维度异常：expected={EMBEDDING_DIM}, actual={vectors.shape}"
        )
    return [[float(x) for x in row] for row in vectors]


def embed_query(text: str) -> List[float]:
    return embed_texts([text])[0]


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if len(left) != EMBEDDING_DIM or len(right) != EMBEDDING_DIM:
        raise ValueError("cosine 输入向量维度与配置不一致")
    # 文档和查询统一 normalize_embeddings=True，点积即 cosine。
    return float(sum(a * b for a, b in zip(left, right)))


def get_embedding_runtime_info() -> dict:
    model = _load_model()
    return {
        "model_name": EMBEDDING_MODEL_NAME,
        "embedding_dim": int(model.get_sentence_embedding_dimension() or 0),
        "device": EMBEDDING_DEVICE,
        "normalize_embeddings": True,
    }
