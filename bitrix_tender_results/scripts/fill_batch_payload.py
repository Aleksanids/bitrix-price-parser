#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch wrapper for ChatGPT/Local Worker payloads.

It reuses fill_tender_result.py and processes payload.items one by one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import fill_tender_result  # noqa: E402


def load_json(path_or_text: str) -> Dict[str, Any]:
    p = Path(path_or_text)
    if p.exists() and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(path_or_text)


def normalize_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("batch payload must contain items array")
    normalized: List[Dict[str, Any]] = []
    batch_mode = data.get("mode")
    batch_allow_overwrite = data.get("allow_overwrite", False)
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item #{idx} must be an object")
        item = dict(item)
        if batch_mode and not item.get("mode"):
            item["mode"] = batch_mode
        if "allow_overwrite" not in item:
            item["allow_overwrite"] = batch_allow_overwrite
        normalized.append(item)
    return normalized


def process_item(payload: Dict[str, Any], config: Dict[str, Any], webhook_url: str | None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "deal_id": payload.get("deal_id"),
        "task_id": payload.get("task_id"),
        "procurement_number": payload.get("procurement_number"),
        "result_status": payload.get("result_status"),
        "mode": payload.get("mode"),
    }
    try:
        errors = fill_tender_result.validate_payload(payload)
        errors.extend(
            fill_tender_result.validate_config(
                config,
                update_mode=(payload.get("mode") == "update"),
                config_is_example=False,
            )
        )
        if payload.get("mode") == "update":
            errors.extend(fill_tender_result.validate_update_mode(payload))
        if errors:
            result.update({"status": "validation_error", "errors": errors})
            return result
        if payload.get("mode") == "dry_run":
            result.update({"status": "dry_run", "reason": "not sent"})
            return result
        if not webhook_url:
            result.update({"status": "configuration_error", "reason": "BITRIX_WEBHOOK_URL is required"})
            return result
        update_result = fill_tender_result.apply_update(payload, config, webhook_url)
        result.update(update_result)
        return result
    except fill_tender_result.ControlledStop as exc:
        result.update({"status": exc.status, "reason": exc.reason, **exc.extra})
        return result
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "error", "reason": str(exc)})
        return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process batch payload and update Bitrix24 safely")
    parser.add_argument("--payload-json", required=True, help="Path to batch_payload.json or JSON text")
    parser.add_argument("--max-items", type=int, default=50, help="Safety limit")
    parser.add_argument("--output", default="bitrix_tender_results/out/batch_payload_results.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    data = load_json(args.payload_json)
    items = normalize_items(data)
    if len(items) > args.max_items:
        print(
            json.dumps(
                {"status": "validation_error", "reason": f"too many items: {len(items)} > {args.max_items}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    config, _config_path, _is_example = fill_tender_result.load_config(None)
    webhook_url = os.environ.get("BITRIX_WEBHOOK_URL", "").strip()

    results = [process_item(item, config, webhook_url) for item in items]
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "no_op": sum(1 for r in results if r.get("status") == "no_op"),
        "dry_run": sum(1 for r in results if r.get("status") == "dry_run"),
        "manual_check": sum(1 for r in results if r.get("status") == "manual_check"),
        "validation_error": sum(1 for r in results if r.get("status") == "validation_error"),
        "error": sum(1 for r in results if r.get("status") == "error"),
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["error"] or summary["validation_error"]:
        return 3
    if summary["manual_check"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
