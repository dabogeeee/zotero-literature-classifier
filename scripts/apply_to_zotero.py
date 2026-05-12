#!/usr/bin/env python3
"""Apply a classification plan to Zotero collections without adding tags."""

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.zotero.org"
ROLE_LABELS_ZH = {
    "core-review": "核心综述",
    "background-review": "背景",
    "primary-study": "原始研究",
    "methods": "方法",
    "dataset-resource": "数据资源",
    "opinion-editorial": "观点评论",
    "exclude": "排除",
    "uncategorized": "未分类",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def library_prefix(value):
    if value.startswith("user:"):
        return "/users/" + value.split(":", 1)[1]
    if value.startswith("group:"):
        return "/groups/" + value.split(":", 1)[1]
    if value.startswith("/users/") or value.startswith("/groups/"):
        return value
    raise SystemExit("--library must look like user:<id>, group:<id>, /users/<id>, or /groups/<id>")


def safe_name(value):
    text = str(value or "未分类").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", " - ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:120] or "未分类"


def category_label(cat):
    if isinstance(cat, str):
        return cat
    return cat.get("label") or cat.get("id") or "未分类"


def role_label(role, language):
    if language == "zh":
        return ROLE_LABELS_ZH.get(role, safe_name(role))
    return safe_name(role)


def unique_paths(paths):
    seen = set()
    out = []
    for path in paths:
        key = tuple(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def planned_updates(plan, root, include_low_confidence, include_exclude, language):
    items = plan.get("plan", plan)
    updates = []
    for item in items:
        key = item.get("key")
        if not key:
            continue
        role = item.get("review_role", "uncategorized")
        confidence = item.get("confidence", "")
        manual = bool(item.get("needs_manual_review"))
        if role == "exclude" and not include_exclude:
            continue
        if (confidence == "low" or manual) and not include_low_confidence:
            continue

        role_group = "文献类型" if language == "zh" else "Roles"
        question_group = "研究问题" if language == "zh" else "Questions"
        manual_group = "需要人工复核" if language == "zh" else "Manual review"
        paths = [[root, role_group, role_label(role, language)]]
        if manual:
            paths.append([root, manual_group])
        for cat in item.get("question_categories", []):
            paths.append([root, question_group, safe_name(category_label(cat))])

        updates.append(
            {
                "key": key,
                "title": item.get("title", ""),
                "collection_paths": unique_paths(paths),
            }
        )
    return updates


class ZoteroClient:
    def __init__(self, prefix, api_key):
        self.prefix = prefix
        self.api_key = api_key

    def request(self, method, path, body=None, headers=None, query=None):
        url = API_BASE + self.prefix + path
        if query:
            url += "?" + urlencode(query)
        request_headers = {
            "Zotero-API-Version": "3",
            "Zotero-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, headers=request_headers, method=method)
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
                if response.headers.get("Backoff"):
                    time.sleep(int(response.headers["Backoff"]))
                if not raw:
                    return None, response.headers
                return json.loads(raw), response.headers
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("%s %s failed with HTTP %s: %s" % (method, path, exc.code, detail))

    def all_collections(self):
        start = 0
        collections = []
        while True:
            data, headers = self.request("GET", "/collections", query={"limit": 100, "start": start})
            data = data or []
            collections.extend(data)
            total = int(headers.get("Total-Results", len(collections)))
            if len(collections) >= total or not data:
                break
            start += 100
        return collections

    def create_collection(self, name, parent_key):
        payload = [{"name": name, "parentCollection": parent_key or False}]
        data, _headers = self.request(
            "POST",
            "/collections",
            body=payload,
            headers={"Zotero-Write-Token": uuid.uuid4().hex},
        )
        failed = (data or {}).get("failed") or {}
        if failed:
            raise RuntimeError("Collection creation failed: %s" % json.dumps(failed, ensure_ascii=False))
        successful = (data or {}).get("successful") or (data or {}).get("success") or {}
        saved = successful.get("0") or successful.get(0)
        if isinstance(saved, dict):
            return saved.get("key") or saved.get("data", {}).get("key")
        if isinstance(saved, str):
            return saved
        raise RuntimeError("Could not determine created collection key from response: %s" % data)

    def item(self, item_key):
        data, _headers = self.request("GET", "/items/" + item_key)
        return data["data"]

    def patch_item_collections(self, item_key, version, collections):
        self.request(
            "PATCH",
            "/items/" + item_key,
            body={"collections": collections},
            headers={"If-Unmodified-Since-Version": str(version)},
        )


def collection_index(collections):
    by_parent_name = {}
    for collection in collections:
        data = collection.get("data", collection)
        parent = data.get("parentCollection") or False
        by_parent_name[(parent, data.get("name"))] = data.get("key")
    return by_parent_name


def ensure_path(client, by_parent_name, path):
    parent = False
    for name in path:
        name = safe_name(name)
        existing = by_parent_name.get((parent, name))
        if existing:
            parent = existing
            continue
        key = client.create_collection(name, parent)
        by_parent_name[(parent, name)] = key
        parent = key
    return parent


def merge_collections(existing, new_keys):
    out = []
    seen = set()
    for key in existing or []:
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    for key in new_keys:
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def print_plan(updates):
    collection_counts = defaultdict(int)
    for update in updates:
        for path in update["collection_paths"]:
            collection_counts[" / ".join(path)] += 1
    print("Items to update: %d" % len(updates))
    print("\nCollections:")
    for name, count in sorted(collection_counts.items()):
        print("  [%d] %s" % (count, name))
    print("\nNo tags will be added or modified.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="classification-plan.json from classify_plan.py")
    parser.add_argument("--library", required=True, help="user:<id> or group:<id>")
    parser.add_argument("--root-collection", default="综述分类")
    parser.add_argument("--api-key", default=os.environ.get("ZOTERO_API_KEY"))
    parser.add_argument("--apply", action="store_true", help="actually write to Zotero; default is dry-run")
    parser.add_argument("--include-low-confidence", action="store_true")
    parser.add_argument("--include-exclude", action="store_true")
    parser.add_argument("--language", choices=["zh", "en"], default="zh", help="collection display language")
    args = parser.parse_args()

    updates = planned_updates(
        load_json(args.plan),
        args.root_collection,
        args.include_low_confidence,
        args.include_exclude,
        args.language,
    )
    print_plan(updates)

    if not args.apply:
        print("\nDry run only. Add --apply and provide ZOTERO_API_KEY to write collection membership to Zotero.")
        return
    if not args.api_key:
        raise SystemExit("Set ZOTERO_API_KEY or pass --api-key before using --apply.")

    client = ZoteroClient(library_prefix(args.library), args.api_key)
    by_parent_name = collection_index(client.all_collections())
    collection_keys_by_path = {}
    for update in updates:
        for path in update["collection_paths"]:
            path_key = tuple(path)
            if path_key not in collection_keys_by_path:
                collection_keys_by_path[path_key] = ensure_path(client, by_parent_name, path)

    for update in updates:
        item = client.item(update["key"])
        new_collection_keys = [collection_keys_by_path[tuple(path)] for path in update["collection_paths"]]
        collections = merge_collections(item.get("collections", []), new_collection_keys)
        client.patch_item_collections(update["key"], item["version"], collections)
        print("Updated %s: +%d collection targets" % (update["key"], len(new_collection_keys)))

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
