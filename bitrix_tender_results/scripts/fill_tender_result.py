#!/usr/bin/env python3
"""Safe Bitrix24 tender result updater.

Scope:
- write only three analytical fields;
- move deal to analytics stage only as a second step after three fields are filled;
- move stage only when tender specialist ТО field is empty;
- resolve deal by deal_id, linked task CRM binding, or procurement number;
- never print secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ALLOWED_MODES = {"dry_run", "update"}
ALLOWED_STATUSES = {
    "ok",
    "manual_check",
    "no_winner",
    "cancelled",
    "failed_procurement",
    "multi_lot",
    "price_not_found",
    "winner_not_found",
    "participants_count_not_found",
    "protocol_not_final",
    "procurement_number_mismatch",
    "deal_not_found_by_procurement_number",
    "multiple_deals_found_by_procurement_number",
    "multiple_deals_found_by_task_id",
    "already_filled",
    "skipped_existing_value",
    "existing_value_conflict",
    "error",
}
ALLOWED_PRICE_BASIS = {
    "contract_price",
    "participant_offer_unit_price",
    "participant_offer_total_unit_price",
    "unit_price_procedure",
    "not_applicable",
}
UPDATE_ALLOWED_STATUS = "ok"
DISCOVERY_MARKER = "TO_BE_DISCOVERED"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "bitrix_fields.json"
EXAMPLE_CONFIG_PATH = ROOT / "config" / "bitrix_fields.example.json"

PAYLOAD_TO_CONFIG_FIELD = {
    "winner_name": "winner_name_analytics",
    "winner_price": "winner_price_analytics",
    "participants_count": "participants_count_analytics",
}


class ControlledStop(Exception):
    def __init__(self, status: str, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.extra = extra or {}


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalize_procurement_number(value: Any) -> str:
    return re.sub(r"[\s\u00a0\u200b\u200c\u200d\ufeff]+", "", str(value or "")).strip()


def is_empty_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return True
    return isinstance(value, str) and normalize_text(value) == ""


def is_empty_employee_value(value: Any) -> bool:
    """Bitrix employee fields can come back as '', None, false, 0, '0', [] or {}."""
    if is_empty_value(value):
        return True
    if value is False or value == 0:
        return True
    if isinstance(value, str) and normalize_text(value).lower() in {"0", "false", "none", "null", "[]", "{}"}:
        return True
    return False


def to_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    text = normalize_text(value).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    dec = to_decimal(value)
    return int(dec) if dec is not None else None


def get_winner_price_for_bitrix(payload: Dict[str, Any]) -> Any:
    return payload.get("winner_price") if payload.get("winner_price") not in (None, "") else payload.get("winner_offer_price")


def get_payload_value_for_update(payload_field: str, payload: Dict[str, Any]) -> Any:
    return get_winner_price_for_bitrix(payload) if payload_field == "winner_price" else payload.get(payload_field)


def coerce_update_value(payload_field: str, value: Any) -> Any:
    if payload_field == "winner_name":
        return normalize_text(value)
    if payload_field == "participants_count":
        parsed = to_int(value)
        return parsed if parsed is not None else value
    if payload_field == "winner_price":
        parsed = to_decimal(value)
        return float(parsed) if parsed is not None else value
    return value


def values_equal(payload_field: str, old: Any, new: Any) -> bool:
    if payload_field == "winner_price":
        return to_decimal(old) is not None and to_decimal(old) == to_decimal(new)
    if payload_field == "participants_count":
        return to_int(old) is not None and to_int(old) == to_int(new)
    return normalize_text(old) == normalize_text(new)


def load_json_from_text_or_path(value: str) -> Dict[str, Any]:
    candidate = Path(value)
    return json.loads(candidate.read_text(encoding="utf-8")) if candidate.exists() and candidate.is_file() else json.loads(value)


def load_config(path: Path | None) -> Tuple[Dict[str, Any], Path, bool]:
    config_path = path or (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8")), config_path, config_path.name.endswith(".example.json")


def validate_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    mode = payload.get("mode")
    status = payload.get("result_status")
    deal_id = payload.get("deal_id")
    procurement_number = normalize_procurement_number(payload.get("procurement_number"))

    if mode not in ALLOWED_MODES:
        errors.append(f"Invalid mode: {mode!r}. Allowed: {sorted(ALLOWED_MODES)}")
    if status not in ALLOWED_STATUSES:
        errors.append(f"Invalid result_status: {status!r}. Allowed: {sorted(ALLOWED_STATUSES)}")
    if deal_id in (None, "") and not procurement_number and payload.get("task_id") in (None, ""):
        errors.append("deal_id, task_id, or procurement_number must be provided")
    if deal_id not in (None, "") and (not isinstance(deal_id, int) or deal_id <= 0):
        errors.append("deal_id must be a positive integer when provided")

    price_basis = str(payload.get("price_basis") or "contract_price").strip()
    if price_basis not in ALLOWED_PRICE_BASIS:
        errors.append(f"Invalid price_basis: {price_basis!r}. Allowed: {sorted(ALLOWED_PRICE_BASIS)}")

    for field in ("winner_price", "winner_offer_price", "participants_count"):
        value = payload.get(field)
        if value in (None, ""):
            continue
        numeric = to_decimal(value)
        if numeric is None:
            errors.append(f"{field} must be numeric when provided")
        elif numeric < 0:
            errors.append(f"{field} must not be negative")
    return errors


def validate_config(config: Dict[str, Any], *, update_mode: bool, config_is_example: bool) -> List[str]:
    errors: List[str] = []
    fields = config.get("fields")
    if not isinstance(fields, dict):
        return ["config.fields must be an object"]

    for logical_name in PAYLOAD_TO_CONFIG_FIELD.values():
        bitrix_field = fields.get(logical_name)
        if not isinstance(bitrix_field, str) or not bitrix_field.strip():
            errors.append(f"Missing config field mapping: {logical_name}")
        elif update_mode and DISCOVERY_MARKER in bitrix_field:
            errors.append(f"Field mapping is not configured for update: {logical_name}")

    if update_mode and not fields.get("tender_specialist_to"):
        errors.append("Missing config field mapping: tender_specialist_to")
    stage = config.get("analytics_stage", {})
    if update_mode and (not isinstance(stage, dict) or not isinstance(stage.get("stage_id"), str)):
        errors.append("config.analytics_stage.stage_id must be a string")
    if update_mode and config_is_example:
        errors.append("Update mode requires bitrix_tender_results/config/bitrix_fields.json, not the example config")
    return errors


def validate_update_mode(payload: Dict[str, Any], update_fields: Optional[Dict[str, Any]] = None) -> List[str]:
    errors: List[str] = []
    if payload.get("result_status") != UPDATE_ALLOWED_STATUS:
        errors.append("update mode is allowed only when result_status = ok")
    if not normalize_text(payload.get("winner_name")):
        errors.append("update mode requires winner_name")
    if get_winner_price_for_bitrix(payload) in (None, ""):
        errors.append("update mode requires winner_price or winner_offer_price")
    if payload.get("participants_count") in (None, ""):
        errors.append("update mode requires participants_count")
    return errors


def build_update_fields(payload: Dict[str, Any], config: Dict[str, Any], existing_item: Optional[Dict[str, Any]] = None, *, allow_overwrite: bool = True) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for payload_field, config_field in PAYLOAD_TO_CONFIG_FIELD.items():
        bitrix_field = config["fields"].get(config_field)
        value = get_payload_value_for_update(payload_field, payload)
        if not bitrix_field or DISCOVERY_MARKER in bitrix_field or value in (None, ""):
            continue
        new_value = coerce_update_value(payload_field, value)
        if existing_item is None or is_empty_value(existing_item.get(bitrix_field)) or allow_overwrite:
            result[bitrix_field] = new_value
    return result


def build_update_fields_with_overwrite_policy(payload: Dict[str, Any], config: Dict[str, Any], existing_item: Dict[str, Any], *, allow_overwrite: bool) -> Tuple[Dict[str, Any], List[str], Dict[str, Dict[str, Any]]]:
    result: Dict[str, Any] = {}
    skipped_same: List[str] = []
    conflicts: Dict[str, Dict[str, Any]] = {}
    for payload_field, config_field in PAYLOAD_TO_CONFIG_FIELD.items():
        bitrix_field = config["fields"].get(config_field)
        value = get_payload_value_for_update(payload_field, payload)
        if not bitrix_field or DISCOVERY_MARKER in bitrix_field or value in (None, ""):
            continue
        new_value = coerce_update_value(payload_field, value)
        old_value = existing_item.get(bitrix_field)
        if is_empty_value(old_value):
            result[bitrix_field] = new_value
        elif values_equal(payload_field, old_value, new_value):
            skipped_same.append(bitrix_field)
        elif allow_overwrite:
            result[bitrix_field] = new_value
        else:
            conflicts[bitrix_field] = {"payload_field": payload_field, "existing_value": old_value, "new_value": new_value}
    return result, skipped_same, conflicts


def bitrix_url(webhook_url: str, method: str) -> str:
    return f"{webhook_url.rstrip('/')}/{method}.json"


def bitrix_call(webhook_url: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        bitrix_url(webhook_url, method),
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bitrix24 HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Bitrix24 connection error: {exc.reason}") from exc
    result = json.loads(body)
    if "error" in result:
        raise RuntimeError(f"Bitrix24 API error: {result.get('error')} - {result.get('error_description')}")
    return result


def get_existing_deal_fields(webhook_url: str, entity_type_id_or_deal_id: int, deal_id: Optional[int] = None) -> Dict[str, Any]:
    resolved_deal_id = int(deal_id if deal_id is not None else entity_type_id_or_deal_id)
    response = bitrix_call(webhook_url, "crm.deal.get", {"id": resolved_deal_id})
    if not isinstance(response.get("result"), dict):
        raise RuntimeError("Bitrix24 crm.deal.get returned unexpected response")
    return response["result"]


def deal_contains_procurement_number(deal: Dict[str, Any], procurement_number: str) -> bool:
    return normalize_procurement_number(procurement_number) in normalize_procurement_number(deal.get("TITLE"))


def search_deals_by_procurement_number(webhook_url: str, procurement_number: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    number = normalize_procurement_number(procurement_number)
    if not number:
        return []
    select = ["ID", "TITLE"]
    for field in (config.get("deal_search", {}) or {}).get("procurement_number_fields", []) or []:
        if isinstance(field, str) and field not in select:
            select.append(field)

    found: Dict[str, Dict[str, Any]] = {}
    response = bitrix_call(webhook_url, "crm.deal.list", {"filter": {"%TITLE": number}, "select": select, "order": {"ID": "ASC"}})
    for deal in response.get("result", []) if isinstance(response.get("result"), list) else []:
        if isinstance(deal, dict) and deal_contains_procurement_number(deal, number) and deal.get("ID"):
            found[str(deal["ID"])] = deal

    for field in (config.get("deal_search", {}) or {}).get("procurement_number_fields", []) or []:
        if not isinstance(field, str) or not field.strip():
            continue
        response = bitrix_call(webhook_url, "crm.deal.list", {"filter": {field: number}, "select": select, "order": {"ID": "ASC"}})
        for deal in response.get("result", []) if isinstance(response.get("result"), list) else []:
            if isinstance(deal, dict) and deal.get("ID"):
                found[str(deal["ID"])] = deal
    return list(found.values())


def extract_deal_ids_from_task_crm(value: Any) -> List[int]:
    """Extract Bitrix deal IDs from task CRM bindings such as D_15096."""
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        values = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]

    deal_ids: List[int] = []
    for item in values:
        text = str(item or "")
        for match in re.finditer(r"(?:^|[^A-ZА-Я])D[_-]?(\d+)", text, flags=re.IGNORECASE):
            deal_id = int(match.group(1))
            if deal_id not in deal_ids:
                deal_ids.append(deal_id)
    return deal_ids


def task_response_to_deal_ids(response: Dict[str, Any]) -> List[int]:
    result = response.get("result")
    candidates: List[Any] = []
    if isinstance(result, dict):
        task = result.get("task") if isinstance(result.get("task"), dict) else result
        for key in ("ufCrmTask", "UF_CRM_TASK", "uf_crm_task"):
            if key in task:
                candidates.append(task.get(key))
    for candidate in candidates:
        deal_ids = extract_deal_ids_from_task_crm(candidate)
        if deal_ids:
            return deal_ids
    return []


def find_deal_ids_by_task_id(webhook_url: str, task_id: Any) -> List[int]:
    if task_id in (None, ""):
        return []
    task_id_int = int(task_id)

    response = bitrix_call(
        webhook_url,
        "tasks.task.get",
        {"taskId": task_id_int, "select": ["ID", "TITLE", "UF_CRM_TASK"]},
    )
    deal_ids = task_response_to_deal_ids(response)
    if deal_ids:
        return deal_ids

    response = bitrix_call(webhook_url, "task.item.getdata", {"TASKID": task_id_int})
    return task_response_to_deal_ids(response)


def resolve_deal_id(payload: Dict[str, Any], webhook_url: str, config: Dict[str, Any]) -> Tuple[int, str, List[Dict[str, Any]]]:
    if payload.get("deal_id") not in (None, ""):
        return int(payload["deal_id"]), "payload", []

    task_id = payload.get("task_id")
    if task_id not in (None, ""):
        task_deal_ids = find_deal_ids_by_task_id(webhook_url, task_id)
        if len(task_deal_ids) == 1:
            return int(task_deal_ids[0]), "found_by_task_id", []
        if len(task_deal_ids) > 1:
            raise ControlledStop("manual_check", "multiple_deals_found_by_task_id", {"task_id": task_id, "matched_deal_ids": task_deal_ids})

    number = normalize_procurement_number(payload.get("procurement_number"))
    deals = search_deals_by_procurement_number(webhook_url, number, config)
    if not deals:
        raise ControlledStop("manual_check", "deal_not_found_by_procurement_number", {"procurement_number": number})
    if len(deals) > 1:
        raise ControlledStop("manual_check", "multiple_deals_found_by_procurement_number", {"procurement_number": number, "matched_deal_ids": [deal.get("ID") for deal in deals]})
    return int(deals[0]["ID"]), "found_by_procurement_number", deals


def find_already_filled_fields(existing_item: Dict[str, Any], update_fields: Dict[str, Any]) -> List[str]:
    return [field for field in update_fields if not is_empty_value(existing_item.get(field))]


def all_analytics_fields_filled(existing_item: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    missing: List[str] = []
    for config_field in PAYLOAD_TO_CONFIG_FIELD.values():
        bitrix_field = config["fields"].get(config_field)
        if bitrix_field and is_empty_value(existing_item.get(bitrix_field)):
            missing.append(bitrix_field)
    return not missing, missing


def build_stage_update_after_result_fields(existing_item: Dict[str, Any], config: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
    fields_are_filled, missing_fields = all_analytics_fields_filled(existing_item, config)
    if not fields_are_filled:
        return {}, False, "analytics_fields_not_filled:" + ",".join(missing_fields)

    stage = config.get("analytics_stage", {})
    target_stage_id = str(stage.get("stage_id", "29"))
    current_stage_id = normalize_text(existing_item.get("STAGE_ID"))
    if current_stage_id == target_stage_id:
        return {}, False, "already_on_analytics_stage"

    to_field = config.get("fields", {}).get("tender_specialist_to")
    if not to_field:
        return {}, False, "tender_specialist_field_not_configured"
    if to_field not in existing_item:
        return {}, False, "tender_specialist_field_missing_in_deal_response"
    if not is_empty_employee_value(existing_item.get(to_field)):
        return {}, False, "tender_specialist_to_filled"

    return {"STAGE_ID": target_stage_id}, True, "analytics_fields_filled_and_tender_specialist_to_empty"


def build_comment_preview(payload: Dict[str, Any]) -> str:
    price = get_winner_price_for_bitrix(payload)
    return (
        "Предпросмотр результата процедуры.\n\n"
        f"Закупка: {payload.get('procurement_number') or 'не указано'}\n"
        f"Победитель: {payload.get('winner_name') or 'не указано'}\n"
        f"Цена победителя для Bitrix: {price if price not in (None, '') else 'не указано'}\n"
        f"Количество участников/заявок: {payload.get('participants_count') if payload.get('participants_count') not in (None, '') else 'не указано'}\n"
        "В Bitrix24 записываются только эти три поля. Остальные данные используются только для проверки."
    )


def safe_log(data: Dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def dry_run_log(payload: Dict[str, Any], config: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    fields = build_update_fields(payload, config)
    return {
        "payload_loaded": True,
        "mode": payload.get("mode"),
        "task_id": payload.get("task_id"),
        "procurement_number": normalize_procurement_number(payload.get("procurement_number")),
        "deal_id_source": "payload" if payload.get("deal_id") else "not_resolved_dry_run",
        "deal_id": payload.get("deal_id"),
        "result_status": payload.get("result_status"),
        "fields_to_update": sorted(fields.keys()),
        "stage_move_required": None,
        "stage_move_reason": "not_checked_dry_run",
        "allow_overwrite": bool(payload.get("allow_overwrite", config.get("automation", {}).get("allow_overwrite_default", False))),
        "status": "dry_run",
        "reason": "no Bitrix24 update was sent",
        "config": str(config_path),
        "preview": build_comment_preview(payload),
    }


def apply_update(payload: Dict[str, Any], config: Dict[str, Any], webhook_url: str) -> Dict[str, Any]:
    deal_id, source, matches = resolve_deal_id(payload, webhook_url, config)
    existing_before = get_existing_deal_fields(webhook_url, deal_id)
    allow_overwrite = bool(payload.get("allow_overwrite", config.get("automation", {}).get("allow_overwrite_default", False)))
    analytics_fields, skipped, conflicts = build_update_fields_with_overwrite_policy(
        payload,
        config,
        existing_before,
        allow_overwrite=allow_overwrite,
    )

    log: Dict[str, Any] = {
        "payload_loaded": True,
        "mode": payload.get("mode"),
        "task_id": payload.get("task_id"),
        "procurement_number": normalize_procurement_number(payload.get("procurement_number")),
        "deal_id_source": source,
        "deal_id": deal_id,
        "matched_deals_count": len(matches) if matches else None,
        "result_status": payload.get("result_status"),
        "allow_overwrite": allow_overwrite,
        "skipped_same_value_fields": skipped,
        "fields_to_update": sorted(analytics_fields),
        "stage_fields_to_update": [],
    }

    if conflicts:
        log.update({
            "status": "manual_check",
            "reason": "existing_value_conflict",
            "conflict_fields": sorted(conflicts),
            "analytics_update": "not_sent_conflict",
            "stage_update": "not_sent_result_fields_conflict",
        })
        return log

    analytics_sent = False
    if analytics_fields:
        response = bitrix_call(webhook_url, "crm.deal.update", {"id": deal_id, "fields": analytics_fields})
        analytics_sent = True
        log["analytics_update"] = "sent"
        log["analytics_response"] = response.get("result", {})
    else:
        log["analytics_update"] = "not_sent_no_new_values"

    # Stage transition is deliberately evaluated only after the result fields are filled.
    existing_after_analytics = get_existing_deal_fields(webhook_url, deal_id) if analytics_sent else existing_before
    stage_fields, stage_required, stage_reason = build_stage_update_after_result_fields(existing_after_analytics, config)
    log["stage_move_required"] = stage_required
    log["stage_move_reason"] = stage_reason
    log["stage_fields_to_update"] = sorted(stage_fields)

    if stage_fields:
        stage_response = bitrix_call(webhook_url, "crm.deal.update", {"id": deal_id, "fields": stage_fields})
        log["stage_update"] = "sent"
        log["stage_response"] = stage_response.get("result", {})
    else:
        log["stage_update"] = "not_sent"

    if analytics_sent or stage_fields:
        log.update({"status": "ok", "reason": "updated"})
    else:
        log.update({"status": "no_op", "reason": "all_values_already_match_and_stage_not_required"})
    return log


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill exactly three Bitrix24 tender result fields safely")
    parser.add_argument("--payload-json", dest="payload_json", default=None, help="Payload JSON string or path to JSON file")
    parser.add_argument("--payload", dest="payload_json", default=None, help="Alias for --payload-json")
    parser.add_argument("--mode", choices=sorted(ALLOWED_MODES), default=None, help="Override payload mode")
    parser.add_argument("--config", default=None, help="Path to bitrix_fields.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if not args.payload_json:
            eprint("Missing required argument: --payload or --payload-json")
            return 2

        payload = load_json_from_text_or_path(args.payload_json)
        config, config_path, is_example = load_config(Path(args.config) if args.config else None)
        if args.mode:
            payload["mode"] = args.mode
        if not payload.get("mode"):
            payload["mode"] = config.get("automation", {}).get("default_mode", "dry_run")
        mode = payload.get("mode")

        errors = validate_payload(payload) + validate_config(config, update_mode=(mode == "update"), config_is_example=is_example)
        if mode == "update":
            errors += validate_update_mode(payload)

        if errors:
            safe_log({
                "payload_loaded": True,
                "mode": mode,
                "task_id": payload.get("task_id"),
                "procurement_number": normalize_procurement_number(payload.get("procurement_number")),
                "deal_id": payload.get("deal_id"),
                "result_status": payload.get("result_status"),
                "status": "validation_error",
                "reason": "payload_or_config_validation_failed",
                "errors": errors,
            })
            return 2

        if mode == "dry_run":
            safe_log(dry_run_log(payload, config, config_path))
            return 0

        webhook_url = os.environ.get("BITRIX_WEBHOOK_URL", "").strip()
        if not webhook_url:
            safe_log({"payload_loaded": True, "mode": mode, "status": "configuration_error", "reason": "BITRIX_WEBHOOK_URL secret is required for update mode"})
            return 3

        result = apply_update(payload, config, webhook_url)
        safe_log(result)
        return 0 if result.get("status") in {"ok", "no_op"} else 4 if result.get("status") == "manual_check" else 1

    except ControlledStop as exc:
        safe_log({"payload_loaded": True, "status": exc.status, "reason": exc.reason, **exc.extra})
        return 4
    except json.JSONDecodeError as exc:
        eprint(f"Invalid JSON: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        eprint(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
