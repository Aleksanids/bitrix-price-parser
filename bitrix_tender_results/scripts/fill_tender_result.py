#!/usr/bin/env python3
"""Safe Bitrix24 tender result updater.

MVP scope:
- update only three Bitrix24 deal fields:
  1) Победитель тендера - аналитика
  2) Цена победителя - аналитика
  3) Количество участников - аналитика
- do not update stageId;
- do not write timeline comments;
- do not update protocol URL, reduction percent, our place, refusal/loss reason;
- dry_run is the default and does not call Bitrix24 update methods;
- update mode is allowed only for result_status=ok;
- no secrets are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

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
    "protocol_not_final",
    "procurement_number_mismatch",
    "already_filled",
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

# Hard MVP allow-list. Only these three logical fields may be written to Bitrix24.
PAYLOAD_TO_CONFIG_FIELD = {
    "winner_name": "winner_name_analytics",
    "winner_price": "winner_price_analytics",
    "participants_count": "participants_count_analytics",
}

REQUIRED_PAYLOAD_FIELDS = ["deal_id", "procurement_number", "result_status", "mode"]


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def load_json_from_text_or_path(value: str) -> Dict[str, Any]:
    candidate = Path(value)
    if candidate.exists() and candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(value)


def load_config(path: Path | None) -> Tuple[Dict[str, Any], Path, bool]:
    if path:
        config_path = path
    elif DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH
    else:
        config_path = EXAMPLE_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    is_example = config_path.name.endswith(".example.json")
    return config, config_path, is_example


def get_winner_price_for_bitrix(payload: Dict[str, Any]) -> Any:
    """Return value for Bitrix field 'Цена победителя - аналитика'."""
    winner_price = payload.get("winner_price")
    if winner_price not in (None, ""):
        return winner_price
    return payload.get("winner_offer_price")


def get_payload_value_for_update(payload_field: str, payload: Dict[str, Any]) -> Any:
    if payload_field == "winner_price":
        return get_winner_price_for_bitrix(payload)
    return payload.get(payload_field)


def validate_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    for field in REQUIRED_PAYLOAD_FIELDS:
        if field not in payload:
            errors.append(f"Missing required payload field: {field}")

    mode = payload.get("mode")
    if mode not in ALLOWED_MODES:
        errors.append(f"Invalid mode: {mode!r}. Allowed: {sorted(ALLOWED_MODES)}")

    status = payload.get("result_status")
    if status not in ALLOWED_STATUSES:
        errors.append(f"Invalid result_status: {status!r}. Allowed: {sorted(ALLOWED_STATUSES)}")

    deal_id = payload.get("deal_id")
    if not isinstance(deal_id, int) or deal_id <= 0:
        errors.append("deal_id must be a positive integer")

    procurement_number = str(payload.get("procurement_number") or "").strip()
    if not procurement_number:
        errors.append("procurement_number must not be empty")

    price_basis = str(payload.get("price_basis") or "contract_price").strip()
    if price_basis not in ALLOWED_PRICE_BASIS:
        errors.append(f"Invalid price_basis: {price_basis!r}. Allowed: {sorted(ALLOWED_PRICE_BASIS)}")

    numeric_fields = ("winner_price", "winner_offer_price", "participants_count")
    for numeric_field in numeric_fields:
        value = payload.get(numeric_field)
        if value in (None, ""):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            errors.append(f"{numeric_field} must be numeric when provided")
            continue
        if numeric_value < 0:
            errors.append(f"{numeric_field} must not be negative")

    return errors


def validate_config(config: Dict[str, Any], *, update_mode: bool, config_is_example: bool) -> List[str]:
    errors: List[str] = []

    if config.get("entityTypeId") != 2:
        errors.append("config.entityTypeId must be 2 for CRM deals")

    fields = config.get("fields")
    if not isinstance(fields, dict):
        errors.append("config.fields must be an object")
        return errors

    for logical_name in PAYLOAD_TO_CONFIG_FIELD.values():
        bitrix_field = fields.get(logical_name)
        if not isinstance(bitrix_field, str) or not bitrix_field.strip():
            errors.append(f"Missing config field mapping: {logical_name}")
        elif update_mode and DISCOVERY_MARKER in bitrix_field:
            errors.append(f"Field mapping is not configured for update: {logical_name}")

    if update_mode and config_is_example:
        errors.append("Update mode requires bitrix_tender_results/config/bitrix_fields.json, not the example config")

    return errors


def build_update_fields(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Build Bitrix24 update payload with strict three-field allow-list."""
    fields_config = config["fields"]
    update_fields: Dict[str, Any] = {}

    for payload_field, logical_config_field in PAYLOAD_TO_CONFIG_FIELD.items():
        value = get_payload_value_for_update(payload_field, payload)
        bitrix_field = fields_config.get(logical_config_field)
        if not bitrix_field or DISCOVERY_MARKER in bitrix_field:
            continue
        if value is None or value == "":
            continue
        update_fields[bitrix_field] = value

    return update_fields


def build_comment_preview(payload: Dict[str, Any]) -> str:
    def value_or_dash(key: str) -> Any:
        value = payload.get(key)
        return value if value not in (None, "") else "не указано"

    return (
        "Предпросмотр результата процедуры.\n\n"
        f"Закупка: {value_or_dash('procurement_number')}\n"
        f"Победитель: {value_or_dash('winner_name')}\n"
        f"Цена победителя для Bitrix: {get_winner_price_for_bitrix(payload) if get_winner_price_for_bitrix(payload) not in (None, '') else 'не указано'}\n"
        f"Количество участников/заявок: {value_or_dash('participants_count')}\n"
        "В Bitrix24 записываются только эти три поля. Остальные данные используются только для проверки."
    )


def bitrix_url(webhook_url: str, method: str) -> str:
    base = webhook_url.rstrip("/")
    return f"{base}/{method}.json"


def bitrix_call(webhook_url: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = bitrix_url(webhook_url, method)
    data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
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


def get_existing_deal_fields(webhook_url: str, entity_type_id: int, deal_id: int) -> Dict[str, Any]:
    response = bitrix_call(webhook_url, "crm.item.get", {"entityTypeId": entity_type_id, "id": deal_id})
    item = response.get("result", {}).get("item")
    if not isinstance(item, dict):
        raise RuntimeError("Bitrix24 crm.item.get returned unexpected response")
    return item


def find_already_filled_fields(existing_item: Dict[str, Any], update_fields: Dict[str, Any]) -> List[str]:
    filled: List[str] = []
    for field_name in update_fields:
        current_value = existing_item.get(field_name)
        if current_value not in (None, "", [], {}):
            filled.append(field_name)
    return filled


def validate_update_mode(payload: Dict[str, Any], update_fields: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if payload.get("result_status") != UPDATE_ALLOWED_STATUS:
        errors.append("update mode is allowed only when result_status = ok")
    if not str(payload.get("winner_name") or "").strip():
        errors.append("update mode requires winner_name")
    if get_winner_price_for_bitrix(payload) in (None, ""):
        errors.append("update mode requires winner_price or winner_offer_price")
    if payload.get("participants_count") in (None, ""):
        errors.append("update mode requires participants_count")
    if not update_fields:
        errors.append("there are no fields to update")

    return errors


def print_plan(payload: Dict[str, Any], update_fields: Dict[str, Any], config_path: Path, mode: str) -> None:
    print("=== Bitrix24 Tender Result Automation ===")
    print(f"Mode: {mode}")
    print(f"Config: {config_path}")
    print(f"Deal ID: {payload.get('deal_id')}")
    print(f"Task ID: {payload.get('task_id')}")
    print(f"Procurement number: {payload.get('procurement_number')}")
    print(f"Result status: {payload.get('result_status')}")
    print(f"Confidence: {payload.get('confidence')}")
    print("\nStrict CRM update allow-list:")
    print("  - winner_name_analytics")
    print("  - winner_price_analytics")
    print("  - participants_count_analytics")
    print("\nFields planned for update:")
    if not update_fields:
        print("  - no fields")
    else:
        for field_name, value in update_fields.items():
            print(f"  - {field_name}: {value}")
    print("\nPreview:")
    print(build_comment_preview(payload))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill exactly three Bitrix24 tender result fields safely")
    parser.add_argument("--payload-json", required=True, help="Payload JSON string or path to JSON file")
    parser.add_argument("--mode", choices=sorted(ALLOWED_MODES), default=None, help="Override payload mode")
    parser.add_argument("--config", default=None, help="Path to bitrix_fields.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        payload = load_json_from_text_or_path(args.payload_json)
        if args.mode:
            payload["mode"] = args.mode

        config, config_path, config_is_example = load_config(Path(args.config) if args.config else None)
        mode = payload.get("mode") or config.get("automation", {}).get("default_mode", "dry_run")
        payload["mode"] = mode

        errors = validate_payload(payload)
        errors.extend(validate_config(config, update_mode=(mode == "update"), config_is_example=config_is_example))

        update_fields = build_update_fields(payload, config)
        if mode == "update":
            errors.extend(validate_update_mode(payload, update_fields))

        if errors:
            eprint("Validation failed:")
            for error in errors:
                eprint(f"- {error}")
            return 2

        print_plan(payload, update_fields, config_path, mode)

        if mode == "dry_run":
            print("\nDry-run completed. No Bitrix24 update was sent.")
            return 0

        webhook_url = os.environ.get("BITRIX_WEBHOOK_URL", "").strip()
        if not webhook_url:
            eprint("BITRIX_WEBHOOK_URL secret is required for update mode")
            return 3

        allow_overwrite = bool(payload.get("allow_overwrite", config.get("automation", {}).get("allow_overwrite_default", False)))
        if not allow_overwrite:
            existing_item = get_existing_deal_fields(webhook_url, int(config["entityTypeId"]), int(payload["deal_id"]))
            filled = find_already_filled_fields(existing_item, update_fields)
            if filled:
                eprint("Refusing to overwrite already filled fields without allow_overwrite=true:")
                for field_name in filled:
                    eprint(f"- {field_name}")
                return 4

        response = bitrix_call(
            webhook_url,
            "crm.item.update",
            {"entityTypeId": int(config["entityTypeId"]), "id": int(payload["deal_id"]), "fields": update_fields},
        )
        print("\nBitrix24 deal update completed. Only three allowed fields were sent.")
        print(json.dumps(response.get("result", {}), ensure_ascii=False, indent=2))
        return 0

    except json.JSONDecodeError as exc:
        eprint(f"Invalid JSON: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary must report safely
        eprint(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
