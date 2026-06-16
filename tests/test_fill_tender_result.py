import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "bitrix_tender_results" / "scripts" / "fill_tender_result.py"
spec = importlib.util.spec_from_file_location("fill_tender_result", MODULE_PATH)
fill = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(fill)


def config():
    return {
        "fields": {
            "winner_name_analytics": "UF_CRM_1693464904935",
            "winner_price_analytics": "UF_CRM_1726788197",
            "participants_count_analytics": "UF_CRM_1751272530",
            "tender_specialist_to": "UF_CRM_1689581836",
        },
        "automation": {"allow_overwrite_default": False, "default_mode": "dry_run"},
        "analytics_stage": {"category_id": 0, "stage_id": "29"},
        "deal_search": {"procurement_number_fields": []},
    }


def payload(**overrides):
    data = {
        "mode": "update",
        "procurement_number": "0873200005426000019",
        "winner_name": 'ООО "ВИТА-АВТО"',
        "winner_price": 795073736.0,
        "winner_offer_price": 795073736.0,
        "participants_count": 1,
        "result_status": "ok",
        "allow_overwrite": False,
    }
    data.update(overrides)
    return data


def test_payload_without_deal_id_is_valid_when_procurement_number_present():
    assert fill.validate_payload(payload(deal_id=None)) == []


def test_procurement_number_leading_zeroes_are_preserved():
    assert fill.normalize_procurement_number(" 0873 200005426000019 ") == "0873200005426000019"


def test_deal_id_from_payload_is_used_without_search(monkeypatch):
    calls = []

    def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(fill, "search_deals_by_procurement_number", fake_search)
    deal_id, source, matches = fill.resolve_deal_id(payload(deal_id=15096), "https://example.invalid", config())

    assert deal_id == 15096
    assert source == "payload"
    assert matches == []
    assert calls == []


def test_deal_is_found_by_procurement_number(monkeypatch):
    monkeypatch.setattr(
        fill,
        "search_deals_by_procurement_number",
        lambda webhook_url, procurement_number, cfg: [{"ID": "15096", "TITLE": "0873200005426000019 test"}],
    )

    deal_id, source, matches = fill.resolve_deal_id(payload(deal_id=None), "https://example.invalid", config())

    assert deal_id == 15096
    assert source == "found_by_procurement_number"
    assert len(matches) == 1


def test_deal_not_found_by_procurement_number_is_controlled(monkeypatch):
    monkeypatch.setattr(fill, "search_deals_by_procurement_number", lambda *args, **kwargs: [])

    try:
        fill.resolve_deal_id(payload(deal_id=None), "https://example.invalid", config())
    except fill.ControlledStop as exc:
        assert exc.status == "manual_check"
        assert exc.reason == "deal_not_found_by_procurement_number"
    else:
        raise AssertionError("ControlledStop was not raised")


def test_multiple_deals_by_procurement_number_is_controlled(monkeypatch):
    monkeypatch.setattr(
        fill,
        "search_deals_by_procurement_number",
        lambda *args, **kwargs: [{"ID": "1"}, {"ID": "2"}],
    )

    try:
        fill.resolve_deal_id(payload(deal_id=None), "https://example.invalid", config())
    except fill.ControlledStop as exc:
        assert exc.status == "manual_check"
        assert exc.reason == "multiple_deals_found_by_procurement_number"
    else:
        raise AssertionError("ControlledStop was not raised")


def test_stage_not_sent_when_tender_specialist_to_is_filled():
    stage_fields, required, reason = fill.build_stage_update({"UF_CRM_1689581836": 123}, config())

    assert stage_fields == {}
    assert required is False
    assert reason == "tender_specialist_to_filled"


def test_stage_sent_when_tender_specialist_to_is_empty():
    stage_fields, required, reason = fill.build_stage_update({"UF_CRM_1689581836": ""}, config())

    assert stage_fields == {"CATEGORY_ID": 0, "STAGE_ID": "29"}
    assert required is True
    assert reason == "tender_specialist_to_empty"


def test_result_status_not_ok_blocks_update_mode():
    errors = fill.validate_update_mode(payload(result_status="manual_check"))

    assert "update mode is allowed only when result_status = ok" in errors


def test_missing_winner_name_blocks_update_mode():
    errors = fill.validate_update_mode(payload(winner_name=""))

    assert "update mode requires winner_name" in errors


def test_missing_winner_price_blocks_update_mode():
    errors = fill.validate_update_mode(payload(winner_price="", winner_offer_price=""))

    assert "update mode requires winner_price or winner_offer_price" in errors


def test_missing_participants_count_blocks_update_mode():
    errors = fill.validate_update_mode(payload(participants_count=""))

    assert "update mode requires participants_count" in errors


def test_allow_overwrite_false_fills_empty_fields():
    existing = {
        "UF_CRM_1693464904935": "",
        "UF_CRM_1726788197": None,
        "UF_CRM_1751272530": "",
    }

    fields, skipped, conflicts = fill.build_update_fields_with_overwrite_policy(
        payload(),
        config(),
        existing,
        allow_overwrite=False,
    )

    assert fields == {
        "UF_CRM_1693464904935": 'ООО "ВИТА-АВТО"',
        "UF_CRM_1726788197": 795073736.0,
        "UF_CRM_1751272530": 1,
    }
    assert skipped == []
    assert conflicts == {}


def test_allow_overwrite_false_same_values_are_noop():
    existing = {
        "UF_CRM_1693464904935": ' ООО "ВИТА-АВТО" ',
        "UF_CRM_1726788197": "795073736,00",
        "UF_CRM_1751272530": "1",
    }

    fields, skipped, conflicts = fill.build_update_fields_with_overwrite_policy(
        payload(),
        config(),
        existing,
        allow_overwrite=False,
    )

    assert fields == {}
    assert set(skipped) == {
        "UF_CRM_1693464904935",
        "UF_CRM_1726788197",
        "UF_CRM_1751272530",
    }
    assert conflicts == {}


def test_allow_overwrite_false_conflict_blocks_overwrite():
    existing = {
        "UF_CRM_1693464904935": "ДРУГОЙ ПОБЕДИТЕЛЬ",
        "UF_CRM_1726788197": "100",
        "UF_CRM_1751272530": "2",
    }

    fields, skipped, conflicts = fill.build_update_fields_with_overwrite_policy(
        payload(),
        config(),
        existing,
        allow_overwrite=False,
    )

    assert fields == {}
    assert skipped == []
    assert set(conflicts) == {
        "UF_CRM_1693464904935",
        "UF_CRM_1726788197",
        "UF_CRM_1751272530",
    }


def test_price_and_participants_are_coerced_to_numeric_values():
    existing = {
        "UF_CRM_1693464904935": "",
        "UF_CRM_1726788197": "",
        "UF_CRM_1751272530": "",
    }

    fields, _, _ = fill.build_update_fields_with_overwrite_policy(
        payload(winner_price="795073736,00", participants_count="1"),
        config(),
        existing,
        allow_overwrite=False,
    )

    assert fields["UF_CRM_1726788197"] == 795073736.0
    assert fields["UF_CRM_1751272530"] == 1
