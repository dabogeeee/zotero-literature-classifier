#!/usr/bin/env python3
"""Create a first-pass collection-only literature classification plan."""

import argparse
import json
import re
from collections import Counter


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


def norm_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(norm_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(norm_text(v) for v in value.values())
    return str(value)


def item_data(item):
    data = item.get("data", item)
    tag_text = []
    for tag in data.get("tags", []):
        if isinstance(tag, dict):
            tag_text.append(tag.get("tag", ""))
        else:
            tag_text.append(str(tag))
    return {
        "key": data.get("key") or item.get("key", ""),
        "title": data.get("title", ""),
        "abstract": data.get("abstractNote", ""),
        "itemType": data.get("itemType", ""),
        "year": str(data.get("date", ""))[:4],
        "publicationTitle": data.get("publicationTitle", ""),
        "ISSN": data.get("ISSN", ""),
        "tags": tag_text,
        "notes": item.get("notes", data.get("notes", [])),
    }


def tokenize(text):
    return set(re.findall(r"[a-z0-9][a-z0-9-]+", text.lower()))


def contains_phrase(text, terms):
    lower = text.lower()
    return any(term in lower for term in terms)


def classify_role(data, text):
    item_type = data["itemType"].lower()
    title = data["title"].lower()
    if "retracted" in text.lower():
        return "exclude", "retraction signal found"
    if contains_phrase(title + " " + item_type, REVIEW_TERMS):
        return "core-review", "review term in title or item type"
    if contains_phrase(text, OPINION_TERMS):
        return "opinion-editorial", "opinion/editorial signal found"
    if contains_phrase(text, METHOD_TERMS):
        return "methods", "method/resource signal found"
    if item_type in ("journalarticle", "conferencepaper", "preprint", "thesis"):
        return "primary-study", "original scholarly item type"
    return "primary-study", "default scholarly classification"


def score_categories(text, taxonomy):
    tokens = tokenize(text)
    scored = []
    for cat in taxonomy.get("question_categories", []):
        include = [str(k).lower() for k in cat.get("keywords", [])]
        exclude = [str(k).lower() for k in cat.get("exclude_keywords", [])]
        score = 0
        hits = []
        for keyword in include:
            matched = keyword in text.lower() if " " in keyword else keyword in tokens or keyword in text.lower()
            if matched:
                score += 1
                hits.append(keyword)
        if any(keyword in text.lower() for keyword in exclude):
            score -= 2
        if score > 0:
            scored.append((score, cat.get("id", cat.get("label", "unknown")), cat.get("label", ""), hits))
    scored.sort(reverse=True)
    return scored


def confidence(scored, data):
    has_abstract = len(data["abstract"].strip()) > 80
    if scored and has_abstract:
        return "high"
    if scored or has_abstract:
        return "medium"
    return "low"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", required=True, help="JSON array, Zotero data objects, or export_collection_items wrapper")
    parser.add_argument("--taxonomy", required=True, help="Taxonomy JSON with question_categories")
    parser.add_argument("--out", required=True, help="Output classification plan JSON")
    args = parser.parse_args()

    with open(args.items, "r", encoding="utf-8") as f:
        loaded_items = json.load(f)
    items = loaded_items.get("items", loaded_items) if isinstance(loaded_items, dict) else loaded_items
    with open(args.taxonomy, "r", encoding="utf-8") as f:
        taxonomy = json.load(f)

    plan = []
    for raw in items:
        data = item_data(raw)
        text = " ".join([data["title"], data["abstract"], data["itemType"], norm_text(data["tags"]), norm_text(data["notes"])])
        role, role_reason = classify_role(data, text)
        scored = score_categories(text, taxonomy)
        categories = [
            {"id": item[1], "label": item[2], "matched_keywords": item[3], "score": item[0]}
            for item in scored[:3]
        ]
        conf = confidence(scored, data)
        plan.append(
            {
                "key": data["key"],
                "title": data["title"],
                "year": data["year"],
                "publicationTitle": data["publicationTitle"],
                "ISSN": data["ISSN"],
                "review_role": role,
                "question_categories": categories,
                "confidence": conf,
                "needs_manual_review": conf == "low" or not categories,
                "rationale": role_reason,
            }
        )

    output = {
        "summary": {
            "topic": taxonomy.get("topic", ""),
            "item_count": len(plan),
            "review_roles": Counter(p["review_role"] for p in plan),
            "confidence": Counter(p["confidence"] for p in plan),
        },
        "plan": plan,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
