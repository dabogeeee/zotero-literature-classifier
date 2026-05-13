#!/usr/bin/env python3
"""Refine ambiguous Zotero classification-plan items with indexed full text."""

import argparse
import json
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.zotero.org"
REVIEW_TERMS = [
    "review",
    "systematic review",
    "meta-analysis",
    "metaanalysis",
    "scoping review",
    "consensus",
    "guideline",
]
METHOD_TERMS = ["method", "protocol", "pipeline", "benchmark", "database", "algorithm", "tool"]
OPINION_TERMS = ["editorial", "comment", "perspective", "viewpoint", "opinion"]


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


class ZoteroClient:
    def __init__(self, prefix, api_key):
        self.prefix = prefix
        self.api_key = api_key

    def request(self, path, query=None, allow_404=False):
        url = API_BASE + self.prefix + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
                if response.headers.get("Backoff"):
                    time.sleep(int(response.headers["Backoff"]))
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("GET %s failed with HTTP %s: %s" % (path, exc.code, detail))

    def children(self, item_key):
        return self.request("/items/%s/children" % item_key, {"format": "json", "include": "data"}) or []

    def fulltext(self, attachment_key):
        return self.request("/items/%s/fulltext" % attachment_key, allow_404=True)


def tokenize(text):
    return set(re.findall(r"[a-z0-9][a-z0-9-]+", text.lower()))


def contains_phrase(text, terms):
    lower = text.lower()
    return any(term in lower for term in terms)


def classify_role(item, text):
    title = str(item.get("title", "")).lower()
    if "retracted" in text.lower():
        return "exclude", "full-text retraction signal found"
    if contains_phrase(title + " " + text[:2000], REVIEW_TERMS):
        return "core-review", "review signal found in title/full text"
    if contains_phrase(text, OPINION_TERMS):
        return "opinion-editorial", "opinion/editorial signal found in full text"
    if contains_phrase(text, METHOD_TERMS):
        return "methods", "method/resource signal found in full text"
    return item.get("review_role", "primary-study"), "kept first-pass role after full-text refinement"


def score_categories(text, taxonomy):
    tokens = tokenize(text)
    scored = []
    lower = text.lower()
    for cat in taxonomy.get("question_categories", []):
        include = [str(k).lower() for k in cat.get("keywords", [])]
        exclude = [str(k).lower() for k in cat.get("exclude_keywords", [])]
        score = 0
        hits = []
        for keyword in include:
            matched = keyword in lower if " " in keyword else keyword in tokens or keyword in lower
            if matched:
                score += 1
                hits.append(keyword)
        if any(keyword in lower for keyword in exclude):
            score -= 2
        if score > 0:
            scored.append((score, cat.get("id", cat.get("label", "unknown")), cat.get("label", ""), hits))
    scored.sort(reverse=True)
    return scored


def should_refine(item, args):
    if args.item_key and item.get("key") not in args.item_key:
        return False
    if args.item_key:
        return True
    if item.get("confidence") == "low" or item.get("needs_manual_review"):
        return True
    if args.include_core_review and item.get("review_role") == "core-review":
        return True
    return False


def attachment_keys(children):
    keys = []
    for child in children:
        data = child.get("data", child)
        if data.get("itemType") == "attachment":
            keys.append(data.get("key") or child.get("key"))
    return [key for key in keys if key]


def collect_fulltext(client, item_key, max_chars):
    chunks = []
    keys = attachment_keys(client.children(item_key))
    for key in keys:
        payload = client.fulltext(key)
        if not payload:
            continue
        content = payload.get("content", "")
        if content:
            chunks.append(content)
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    text = "\n\n".join(chunks)
    return text[:max_chars], keys


def refine_item(item, fulltext, taxonomy):
    base = " ".join(
        [
            item.get("title", ""),
            item.get("publicationTitle", ""),
            item.get("rationale", ""),
            fulltext,
        ]
    )
    scored = score_categories(base, taxonomy)
    categories = [
        {"id": row[1], "label": row[2], "matched_keywords": row[3], "score": row[0]}
        for row in scored[:3]
    ]
    role, reason = classify_role(item, base)
    updated = dict(item)
    updated["review_role"] = role
    if categories:
        updated["question_categories"] = categories
        updated["confidence"] = "high"
        updated["needs_manual_review"] = False
    else:
        updated["confidence"] = "medium" if fulltext else item.get("confidence", "low")
        updated["needs_manual_review"] = True
    updated["rationale"] = reason
    updated["fulltext_refined"] = True
    updated["fulltext_status"] = "found" if fulltext else "not-found"
    updated["fulltext_excerpt"] = fulltext[:1200]
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="classification-plan.json from classify_plan.py")
    parser.add_argument("--taxonomy", required=True, help="taxonomy JSON with question_categories")
    parser.add_argument("--library", required=True, help="user:<id> or group:<id>")
    parser.add_argument("--out", required=True, help="refined output plan JSON")
    parser.add_argument("--api-key", default=os.environ.get("ZOTERO_API_KEY"))
    parser.add_argument("--include-core-review", action="store_true", help="also refine first-pass core-review items")
    parser.add_argument("--item-key", action="append", default=[], help="specific parent item key to refine; repeatable")
    parser.add_argument("--max-fulltext-chars", type=int, default=25000)
    args = parser.parse_args()

    plan = load_json(args.plan)
    taxonomy = load_json(args.taxonomy)
    client = ZoteroClient(library_prefix(args.library), args.api_key)

    items = plan.get("plan", plan)
    refined = []
    refined_count = 0
    found_count = 0
    for item in items:
        if not should_refine(item, args):
            refined.append(item)
            continue
        text, keys = collect_fulltext(client, item.get("key"), args.max_fulltext_chars)
        updated = refine_item(item, text, taxonomy)
        updated["attachment_keys_checked"] = keys
        refined.append(updated)
        refined_count += 1
        if text:
            found_count += 1

    output = dict(plan) if isinstance(plan, dict) else {"plan": refined}
    output["plan"] = refined
    output.setdefault("summary", {})
    output["summary"]["fulltext_refined_items"] = refined_count
    output["summary"]["fulltext_found_items"] = found_count
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Refined %d items; full text found for %d. Wrote %s" % (refined_count, found_count, args.out))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
