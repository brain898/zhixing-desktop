"""为本地桌面验收准备隔离测试资料，不连接业务数据库。"""

import sys
import time
from pathlib import Path

import requests


BASE_URL = "http://127.0.0.1:8767/api"
SEARCH_KEYWORD = "二次供水泵房验收联调"


def wait_until(check, label, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError(f"等待超时：{label}")


def main():
    admin = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": "admin", "password": "Admin@Zhixing2026"},
        timeout=10,
    )
    admin.raise_for_status()
    headers = {"Authorization": f"Bearer {admin.json()['token']}"}

    body = (
        f"# {SEARCH_KEYWORD}管理制度\n\n"
        "巡检人员每日检查二次供水泵房水位、压力和运行声音。\n\n"
        "发现异常噪声时，应立即停机并上报工程主管；未完成安全确认前不得重新启动设备。\n"
    ).encode("utf-8")
    upload = requests.post(
        f"{BASE_URL}/documents/upload",
        headers=headers,
        files={"file": ("二次供水泵房验收联调管理制度.md", body, "text/markdown")},
        data={"duplicate_mode": "ask"},
        timeout=20,
    )
    upload.raise_for_status()
    data = upload.json()
    if data["status"] == "duplicate_content":
        document_id = data["existing_document_id"]
        version_id = data["existing_version_id"]
    else:
        document_id = data["document_id"]
        version_id = data["version_id"]

    def parsed():
        detail = requests.get(f"{BASE_URL}/documents/{document_id}", headers=headers, timeout=10)
        detail.raise_for_status()
        version = next(item for item in detail.json()["versions"] if item["id"] == version_id)
        if version["processing_status"] in ("failed", "partial_failed"):
            raise RuntimeError(version.get("error_summary") or "解析失败")
        return version if version["processing_status"] == "completed" else None

    wait_until(parsed, "文档解析")

    current = requests.get(
        f"{BASE_URL}/knowledge/items",
        headers=headers,
        params={"document_id": document_id},
        timeout=10,
    ).json()["items"]
    if not current:
        extract = requests.post(
            f"{BASE_URL}/documents/{document_id}/versions/{version_id}/extract",
            headers=headers,
            timeout=10,
        )
        extract.raise_for_status()

    def extracted():
        response = requests.get(
            f"{BASE_URL}/knowledge/items",
            headers=headers,
            params={"document_id": document_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["items"] or None

    items = wait_until(extracted, "DeepSeek 抽取")
    target = items[0]
    item_id = target["id"]
    detail = requests.get(f"{BASE_URL}/knowledge/items/{item_id}", headers=headers, timeout=10).json()
    if detail["active_version"]["review_status"] != "confirmed":
        confirm = requests.post(
            f"{BASE_URL}/knowledge/items/{item_id}/confirm",
            headers=headers,
            json={"revision_token": detail["active_version"]["revision_token"]},
            timeout=10,
        )
        confirm.raise_for_status()

    def indexed():
        response = requests.get(f"{BASE_URL}/knowledge/items/{item_id}", headers=headers, timeout=10)
        response.raise_for_status()
        active = response.json()["active_version"]
        if active["index_status"] == "failed":
            raise RuntimeError("索引建立失败")
        return active if active["index_status"] == "ready" else None

    active = wait_until(indexed, "BGE 索引")
    requests.put(
        f"{BASE_URL}/documents/{document_id}/access-scope",
        headers=headers,
        json={"access_scope": "org_internal"},
        timeout=10,
    ).raise_for_status()
    requests.put(
        f"{BASE_URL}/knowledge/items/{item_id}/access-scope",
        headers=headers,
        json={"access_scope": "org_internal"},
        timeout=10,
    ).raise_for_status()

    search = requests.get(
        f"{BASE_URL}/knowledge/search",
        headers=headers,
        params={"q": SEARCH_KEYWORD},
        timeout=60,
    )
    search.raise_for_status()
    assert search.json()["items"], "管理员检索未返回已确认知识"
    result = search.json()["items"][0]
    assert result["source"]["document_version_id"] == version_id, "检索结果来源版本不一致"
    print({
        "document_id": document_id,
        "item_id": item_id,
        "knowledge_version_id": active["id"],
        "admin_search_total": search.json()["total"],
        "source_document_version_id": result["source"]["document_version_id"],
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise
