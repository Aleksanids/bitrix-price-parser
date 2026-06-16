#!/usr/bin/env python3
"""Batch 44-FZ EIS collection and Bitrix24 three-field update.

Input is a JSON array or an object with `items`:
[
  {"procurement_number": "0873200005426000019", "deal_id": 15096, "task_id": 42712}
]

The script collects EIS data for each item and then applies the same strict
three-field Bitrix24 update logic from fill_tender_result.py:
- winner_name_analytics
- winner_price_analytics
- participants_count_analytics

Default mode is dry_run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import collect_44fz_result  # noqa: E402
import fill_tender_result  # noqa: E402

ALLOWED_MODES = {"dry_run", "update"}


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def load_batch(path_or_json: str) -> List[Dict[str, Any]]:
    candidate = Path(path_or_json)
    if candidate.exists() and candidate.is_file():
        data = json.loads(candidate.read_text(encoding="utf-8"))
    else:
        data = json.loads(path_or_json)

    if isinstance(data, dict):
        items = data.get("items")
    else:
        items = data

    if not isinstance(items, list):
        raise ValueError("Batch input must be a JSON array or an object with items array")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Batch item #{index} must be an object")
        procurement_number = str(item.get("procurement_number") or item.get("reg_number") or "").strip()
        if not procurement_number:
            raise ValueError(f"Batch item #{index} has no procurement_number")
        deal_id = item.get("deal_id")
        if deal_id in (None, ""):
            raise ValueError(f"Batch item #{index} has no deal_id")
        normalized.append(
            {
                "procurement_number": procurement_number,
                "deal_id": int(deal_id),
                "task_id": int(item["task_id"]) if item.get("task_id") not in (None, "") else None,
                "source_row": item,
            }
        )
    return normalized


def prepare_update(payload: Dict[str, Any], config: Dict[str, Any], config_is_example: bool, mode: str) -> Dict[str, Any]:
    payload = dict(payload)
    payload["mode"] = mode

    errors = fill_tender_result.validate_payload(payload)
    errors.extend(fill_tender_result.validate_config(config, update_mode=(mode == "update"), config_is_example=config_is_example))
    update_fields = fill_tender_result.build_update_fields(payload, config)
    if mode == "update":
        errors.extend(fill_tender_result.validate_update_mode(payload, update_fields))

    return {
        "payload": payload,
        "update_fields": update_fields,
        "errors": errors,
    }


def apply_update_if_needed(payload: Dict[str, Any], update_fields: Dict[str, Any], config: Dict[str, Any], mode: str) -> Dict[str, Any]:
    if mode == "dry_run":
        return {"bitrix_update": "not_sent_dry_run"}

    webhook_url = os.environ.get("BITRIX_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("BITRIX_WEBHOOK_URL secret is required for update mode")

    allow_overwrite = bool(payload.get("allow_overwrite", config.get("automation", {}).get("allow_overwrite_default", False)))
    if not allow_overwrite:
        existing_item = fill_tender_result.get_existing_deal_fields(webhook_url, int(config["entityTypeId"]), int(payload["deal_id"]))
        filled = fill_tender_result.find_already_filled_fields(existing_item, update_fields)
        if filled:
            return {
                "bitrix_update": "refused_already_filled",
                "already_filled_fields": filled,
            }

    response = fill_tender_result.bitrix_call(
        webhook_url,
        "crm.item.update",
        {"entityTypeId": int(config["entityTypeId"]), "id": int(payload["deal_id"]), "fields": update_fields},
    )
    return {"bitrix_update": "sent", "response": response.get("result", {})}


def process_item(item: Dict[str, Any], config: Dict[str, Any], config_is_example: bool, mode: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "procurement_number": item["procurement_number"],
        "deal_id": item["deal_id"],
        "task_id": item.get("task_id"),
        "status": "started",
    }

    try:
        payload = collect_44fz_result.collect_44fz(
            item["procurement_number"],
            item["deal_id"],
            item.get("task_id"),
        )
        prepared = prepare_update(payload, config, config_is_example, mode)
        result["payload"] = prepared["payload"]
        result["update_fields"] = prepared["update_fields"]

        if prepared["errors"]:
            result["status"] = "validation_error"
            result["errors"] = prepared["errors"]
            return result

        update_result = apply_update_if_needed(prepared["payload"], prepared["update_fields"], config, mode)
        result.update(update_result)
        result["status"] = "ok" if update_result.get("bitrix_update") != "refused_already_filled" else "manual_check"
        return result

    except Exception as exc:  # noqa: BLE001 - batch boundary
        result["status"] = "error"
        result["errors"] = [str(exc)]
        return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Batch collect 44-FZ EIS data and update exactly three Bitrix fields")
    parser.add_argument("--batch-json", required=True, help="JSON string or path to JSON batch file")
    parser.add_argument("--mode", choices=sorted(ALLOWED_MODES), default="dry_run")
    parser.add_argument("--max-items", type=int, default=20, help="Safety limit for one workflow run")
    parser.add_argument("--output", default="bitrix_tender_results/out/batch_results.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    items = load_batch(args.batch_json)
    if len(items) > args.max_items:
        eprint(f"Batch contains {len(items)} items, max allowed is {args.max_items}")
        return 2

    config, config_path, config_is_example = fill_tender_result.load_config(None)
    results = []
    for item in items:
        print(f"Processing {item['procurement_number']} / deal {item['deal_id']} / task {item.get('task_id')}")
        results.append(process_item(item, config, config_is_example, args.mode))

    summary = {
        "mode": args.mode,
        "config_path": str(config_path),
        "total": len(results),
        "ok": sum(1 for result in results if result.get("status") == "ok"),
        "manual_check": sum(1 for result in results if result.get("status") == "manual_check"),
        "validation_error": sum(1 for result in results if result.get("status") == "validation_error"),
        "error": sum(1 for result in results if result.get("status") == "error"),
        "results": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["error"] or summary["validation_error"]:
        return 3
    if summary["manual_check"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
