#!/usr/bin/env python3
"""Export all regular items from one Zotero collection for batch classification."""

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.zotero.org"
SKIP_ITEM_TYPES = {"attachment", "note", "annotation"}


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

    def request(self, path, query=None):
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
                return json.loads(raw) if raw else None, response.headers
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("GET %s failed with HTTP %s: %s" % (path, exc.code, detail))

    def paged(self, path, query=None):
        query = dict(query or {})
        query.setdefault("limit", 100)
        start = 0
        out = []
        while True:
            query["start"] = start
            data, headers = self.request(path, query=query)
            data = data or []
            out.extend(data)
            total = int(headers.get("Total-Results", len(out)))
            if len(out) >= total or not data:
                break
            start += int(query["limit"])
        return out


def collection_key_by_name(client, name):
    collections = client.paged("/collections", {"limit": 100})
    matches = []
    for collection in collections:
        data = collection.get("data", collection)
        if data.get("name") == name:
            matches.append(data)
    if not matches:
        raise SystemExit("No collection named %r found." % name)
    if len(matches) > 1:
        keys = ", ".join("%s:%s" % (m.get("name"), m.get("key")) for m in matches)
        raise SystemExit("Multiple collections named %r found; use --collection-key. Matches: %s" % (name, keys))
    return matches[0]["key"]


def simplify_item(item):
    data = item.get("data", item)
    creators = []
    for creator in data.get("creators", []):
        creators.append(
            {
                "creatorType": creator.get("creatorType", ""),
                "firstName": creator.get("firstName", ""),
                "lastName": creator.get("lastName", ""),
                "name": creator.get("name", ""),
            }
        )
    return {
        "key": data.get("key") or item.get("key", ""),
        "version": data.get("version", item.get("version")),
        "itemType": data.get("itemType", ""),
        "title": data.get("title", ""),
        "abstractNote": data.get("abstractNote", ""),
        "date": data.get("date", ""),
        "publicationTitle": data.get("publicationTitle", ""),
        "journalAbbreviation": data.get("journalAbbreviation", ""),
        "ISSN": data.get("ISSN", ""),
        "DOI": data.get("DOI", ""),
        "url": data.get("url", ""),
        "creators": creators,
        "tags": data.get("tags", []),
        "collections": data.get("collections", []),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True, help="user:<id> or group:<id>")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collection-key", help="Zotero collection key")
    group.add_argument("--collection-name", help="Exact Zotero collection name")
    parser.add_argument("--out", required=True, help="Output items JSON")
    parser.add_argument("--api-key", default=os.environ.get("ZOTERO_API_KEY"))
    parser.add_argument("--include-notes-attachments", action="store_true")
    args = parser.parse_args()

    client = ZoteroClient(library_prefix(args.library), args.api_key)
    collection_key = args.collection_key or collection_key_by_name(client, args.collection_name)
    raw_items = client.paged(
        "/collections/%s/items" % collection_key,
        {"include": "data", "format": "json", "limit": 100},
    )
    items = []
    skipped = 0
    for item in raw_items:
        item_type = item.get("data", item).get("itemType", "")
        if not args.include_notes_attachments and item_type in SKIP_ITEM_TYPES:
            skipped += 1
            continue
        items.append(simplify_item(item))

    output = {
        "library": args.library,
        "collection_key": collection_key,
        "collection_name": args.collection_name or "",
        "item_count": len(items),
        "skipped_child_item_count": skipped,
        "items": items,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Exported %d items to %s" % (len(items), args.out))
    if skipped:
        print("Skipped %d notes/attachments/annotations." % skipped)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
