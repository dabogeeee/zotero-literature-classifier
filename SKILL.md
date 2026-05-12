---
name: zotero-literature-classifier
description: Batch-classify papers in a local Zotero library after broad topic collections have already been manually curated. Use when the user asks to divide a Zotero topic collection into Chinese subtopic/question collections, organize review literature by problem categories, or write batch classification results back to Zotero collections without adding tags, including Chinese requests such as Zotero文献分类、课题细分、按问题归类文献、大类文献批量分类.
---

# Zotero Literature Classifier

Use this skill to batch-classify an already-curated Zotero topic collection into review-ready Chinese subcollections. The goal is to divide a broad topic collection into finer problem-oriented categories, not to tag items or rank journals.

Rely on the existing Zotero skill for connection checks and general Zotero context. This skill governs the batch export, classification plan, and collection-only write-back workflow.

## Core Workflow

1. Start with Zotero readiness using the Zotero skill or Web API access.
2. Identify the broad topic collection. If the user has not named it, list likely collections and ask them to choose.
3. Ask for or infer a taxonomy:
   - If the user provides subquestions/categories, use them as authoritative labels.
   - If they only give a broad topic, draft a Chinese taxonomy and ask for confirmation before writing back.
   - For a reusable JSON format, read `references/taxonomy-template.json`.
4. Batch-export the whole target collection with `scripts/export_collection_items.py`.
5. Classify each item:
   - Assign `review_role`: `core-review`, `background-review`, `primary-study`, `methods`, `dataset-resource`, `opinion-editorial`, or `exclude`.
   - Assign one or more `question_categories` from the confirmed taxonomy.
   - Add `confidence`: `high`, `medium`, or `low`.
   - Record `rationale` with short evidence from title/abstract/tags/notes.
   - Mark `needs_manual_review` when evidence is sparse, categories conflict, or the item appears important but ambiguous.
6. Produce a dry-run preview before Zotero write-back:
   - Counts by category and review role.
   - High-confidence placements.
   - Low-confidence or uncategorized items.
   - Proposed Chinese collection paths.
7. Write back to Zotero only after explicit confirmation. Write-back creates/uses collections and adds items to all matching collections. It must not add or modify item tags.

## Classification Heuristics

Prefer abstracts and publication type over title-only inference. Use full text only when the user asks or metadata is insufficient for important items.

Classify as `core-review` when the item is a recent systematic review, meta-analysis, scoping review, authoritative narrative review, consensus statement, guideline, or major field synthesis directly matching the topic.

Classify as `background-review` when the item is a broad conceptual review, older review, adjacent-field review, or useful overview that supports introduction/discussion framing but is not central evidence.

Classify as `primary-study` when the item reports original experiments, clinical cohorts/trials, computational analyses, datasets, surveys, or mechanistic studies.

Classify as `methods` when the main value is assay design, statistical method, benchmark, pipeline, protocol, database construction, model architecture, or measurement approach.

Classify as `exclude` only with a clear reason, such as wrong domain, duplicate, retracted item, non-scholarly item, inaccessible metadata after retry, or outside the user-defined scope.

## Subtopic Design

For review writing, avoid categories that merely repeat keywords. Prefer categories that answer the review's questions:

- Mechanism: what pathway, causal process, or conceptual model does the paper address?
- Population/context: what disease, organism, sample type, cohort, region, condition, or setting?
- Intervention/exposure/tool: what treatment, perturbation, technology, or computational method?
- Outcome/evidence: what endpoint, phenotype, benchmark, diagnostic performance, or biological readout?
- Controversy/gap: what unresolved issue, inconsistency, limitation, or future direction?

Keep categories mutually understandable but allow multi-label assignment. A paper may support more than one subquestion, so it can be placed in multiple Zotero subcollections.

## Scripts

Batch-export a Zotero broad-topic collection first. Use collection name when it is unique, or collection key when names are duplicated:

```bash
python scripts/export_collection_items.py --library user:20019824 --collection-name "大类集合名称" --out items.json
python scripts/export_collection_items.py --library user:20019824 --collection-key ABCD1234 --out items.json
```

Generate the classification plan:

```bash
python scripts/classify_plan.py --items items.json --taxonomy taxonomy.json --out classification-plan.json
```

Expected item JSON may be either the wrapper produced by `export_collection_items.py`, Zotero API-style objects with a `data` field, or flat objects containing `key`, `title`, `abstractNote`, `itemType`, `tags`, and `notes`.

## Collection-Only Zotero Write-Back

Use `scripts/apply_to_zotero.py` only after the user approves the classification plan. This script writes through the Zotero Web API, not the read-only local API. It requires a Zotero API key with write permission and the target library id.

Dry run first:

```bash
python scripts/apply_to_zotero.py --plan classification-plan.json --library user:20019824 --root-collection "综述分类"
```

Apply after confirmation:

```bash
$env:ZOTERO_API_KEY="..."
python scripts/apply_to_zotero.py --plan classification-plan.json --library user:20019824 --root-collection "综述分类" --apply
```

The script:

- Creates Chinese collection paths under the root collection, such as `综述分类/文献类型/背景` and `综述分类/研究问题/机制与概念模型`.
- Adds each item to every matching category collection, so one paper can appear in multiple Zotero collections.
- Fetches each item before updating and merges new collection membership with existing collection membership.
- Preserves all existing item tags exactly as they are.
- Uses Zotero item versions so concurrent edits are rejected instead of silently overwritten.

Do not use direct SQLite editing for normal Zotero writes. Prefer Web API writes so Zotero sync and conflict protection remain intact.

## Output Standards

Return a concise classification report in Chinese unless the user requests another language. Use Chinese display labels for roles and categories in reports and Zotero collection names.

Include:

- Target Zotero collection name.
- Taxonomy used.
- Counts by review role and subquestion.
- Table of proposed classifications with Zotero item key, short title, year, role, category, confidence, and rationale.
- Separate list of low-confidence or manual-review items.
- Exact proposed Chinese collection paths before asking for confirmation.

If the user asks for direct Zotero updates, first show the proposed changes and ask for confirmation unless their latest prompt already explicitly approves applying the plan.
