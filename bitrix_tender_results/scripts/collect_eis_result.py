#!/usr/bin/env python3
"""Collect tender result data from official EIS pages.

This script is intentionally dependency-free so it can run in GitHub Actions
without installing third-party packages.

It does not write to Bitrix24. It only prepares a structured JSON payload that
can be reviewed and then passed to fill_tender_result.py.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EIS_BASE = "https://zakupki.gov.ru"
SUPPLIER_RESULTS_PATH = "/epz/order/notice/zk20/view/supplier-results.html"
COMMON_INFO_PATH = "/epz/order/notice/zk20/view/common-info.html"
PROTOCOL_MAIN_PATH = "/epz/order/notice/zk20/view/protocol/protocol-main-info.html"

MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})*(?:[,.]\d{2})|\d+(?:[,.]\d{2}))(?:\s*(?:₽|руб\.?|RUB))?", re.IGNORECASE)
INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
REGISTRY_CONTRACT_RE = re.compile(r"\b(\d{19,20})\b")

PRICE_BASIS_CONTRACT = "contract_price"
PRICE_BASIS_PARTICIPANT_OFFER = "participant_offer_unit_price"


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def build_url(path: str, procurement_number: str, extra: str = "") -> str:
    sep = "&" if extra else ""
    return f"{EIS_BASE}{path}?regNumber={procurement_number}{sep}{extra}"


def fetch_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TenderResultCollector/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    if match:
        encoding = match.group(1)
    return body.decode(encoding, errors="replace")


def read_source_html(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<svg.*?</svg>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)</(?:div|p|tr|td|th|li|br|section|article|h\d)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def window_around(text: str, keyword: str, before: int = 300, after: int = 1200) -> str:
    low_text = text.lower()
    idx = low_text.find(keyword.lower())
    if idx < 0:
        return ""
    start = max(0, idx - before)
    end = min(len(text), idx + len(keyword) + after)
    return text[start:end]


def parse_money(value: str | None) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace("\u00a0", " ").replace(" ", "")
    cleaned = re.sub(r"[^\d,.-]", "", cleaned)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def find_first_money(text: str) -> Optional[float]:
    match = MONEY_RE.search(text)
    if not match:
        return None
    return parse_money(match.group(1))


def find_money_after(text: str, keywords: Iterable[str], window: int = 1400) -> Optional[float]:
    for keyword in keywords:
        fragment = window_around(text, keyword, before=0, after=window)
        if not fragment:
            continue
        value = find_first_money(fragment[len(keyword):])
        if value is not None:
            return value
    return None


def find_text_between(text: str, start_keywords: Iterable[str], end_keywords: Iterable[str], max_len: int = 800) -> str:
    low_text = text.lower()
    start_idx = -1
    start_word = ""
    for keyword in start_keywords:
        idx = low_text.find(keyword.lower())
        if idx >= 0 and (start_idx < 0 or idx < start_idx):
            start_idx = idx
            start_word = keyword
    if start_idx < 0:
        return ""

    fragment_start = start_idx + len(start_word)
    fragment_end = min(len(text), fragment_start + max_len)
    fragment = text[fragment_start:fragment_end]
    low_fragment = fragment.lower()

    end_idx = len(fragment)
    for keyword in end_keywords:
        idx = low_fragment.find(keyword.lower())
        if idx >= 0:
            end_idx = min(end_idx, idx)

    return compact_text(fragment[:end_idx]).strip(" :-—\n\t")


def extract_name_after(text: str, keywords: Iterable[str]) -> str:
    value = find_text_between(
        text,
        keywords,
        [
            "ИНН",
            "КПП",
            "Почтовый адрес",
            "Место нахождения",
            "Предложение участника",
            "Цена контракта",
            "Реестровый номер контракта",
            "Дата размещения",
        ],
        max_len=1000,
    )
    value = re.sub(r"^(участника|поставщика|подрядчика|исполнителя)\s*", "", value, flags=re.IGNORECASE)
    return value.strip(" .;:")


def extract_inn_near(text: str, keyword: str) -> str:
    fragment = window_around(text, keyword, before=0, after=1200)
    if not fragment:
        return ""
    match = INN_RE.search(fragment)
    return match.group(1) if match else ""


def extract_protocol(text: str, procurement_number: str) -> Tuple[str, str, str]:
    protocol_name = ""
    protocol_date = ""

    protocol_fragment = window_around(text, "Протокол подведения итогов", before=100, after=900)
    if protocol_fragment:
        date_match = DATE_RE.search(protocol_fragment)
        if date_match:
            protocol_date = date_match.group(1)
        name_match = re.search(
            r"(Протокол\s+подведения\s+итогов[^\n]{0,250}?(?:№\s*[А-ЯA-Z0-9-]+)?)",
            compact_text(protocol_fragment),
            flags=re.IGNORECASE,
        )
        if name_match:
            protocol_name = compact_text(name_match.group(1))

    if not protocol_name:
        protocol_name = "Протокол подведения итогов определения поставщика (подрядчика, исполнителя)"

    protocol_url = build_url(PROTOCOL_MAIN_PATH, procurement_number, "type=izk&version=1")
    return protocol_name, protocol_date, protocol_url


def extract_failed_reason(text: str) -> str:
    candidates = [
        "признан несостоявшимся",
        "признана несостоявшейся",
        "подана только одна заявка",
        "только одна заявка",
    ]
    for keyword in candidates:
        fragment = window_around(text, keyword, before=250, after=450)
        if fragment:
            return compact_text(fragment)
    return ""


def extract_participants_count(text: str) -> Optional[int]:
    patterns = [
        r"Количество\s+поданных\s+заявок\s*[:\-]?\s*(\d+)",
        r"Подано\s+заявок\s*[:\-]?\s*(\d+)",
        r"Количество\s+участников\s*[:\-]?\s*(\d+)",
        r"Всего\s+заявок\s*[:\-]?\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    if re.search(r"подан[ао]?\s+только\s+одна\s+заявк", text, flags=re.IGNORECASE):
        return 1
    if re.search(r"только\s+одна\s+заявк", text, flags=re.IGNORECASE):
        return 1

    return None


def extract_purchase_name(text: str) -> str:
    return find_text_between(
        text,
        ["Наименование объекта закупки", "Объект закупки", "Наименование закупки"],
        ["Этап закупки", "Способ определения", "Заказчик", "Организация"],
        max_len=1200,
    )


def extract_customer_name(text: str) -> str:
    value = find_text_between(
        text,
        ["Заказчик", "Наименование заказчика"],
        ["Контактная информация", "Размещение осуществляет", "Ответственное должностное лицо", "Место нахождения"],
        max_len=1000,
    )
    value = re.sub(r"^Наименование\s+заказчика\s*", "", value, flags=re.IGNORECASE)
    return value.strip(" .;:")


def extract_procedure_type(text: str) -> str:
    known = [
        "Запрос котировок в электронной форме",
        "Электронный аукцион",
        "Открытый конкурс в электронной форме",
        "Закупка у единственного поставщика",
    ]
    low = text.lower()
    for item in known:
        if item.lower() in low:
            return item
    return find_text_between(text, ["Способ определения поставщика", "Способ закупки"], ["Размещение", "Этап", "НМЦК"], max_len=500)


def extract_status(text: str) -> str:
    known = [
        "Определение поставщика завершено",
        "Работа комиссии",
        "Подача заявок",
        "Закупка завершена",
        "Отменена",
    ]
    low = text.lower()
    for item in known:
        if item.lower() in low:
            return item
    return ""


def extract_law(text: str) -> str:
    if "44-ФЗ" in text or "44 ФЗ" in text:
        return "44-ФЗ"
    if "223-ФЗ" in text or "223 ФЗ" in text:
        return "223-ФЗ"
    return ""


def extract_registry_contract_number(text: str) -> str:
    fragment = window_around(text, "Реестровый номер контракта", before=0, after=500)
    if fragment:
        match = REGISTRY_CONTRACT_RE.search(fragment)
        if match:
            return match.group(1)
    return ""


def extract_contract_publish_date(text: str) -> str:
    fragment = window_around(text, "Дата размещения подписанного контракта", before=0, after=500)
    if fragment:
        match = DATE_RE.search(fragment)
        if match:
            return match.group(1)
    return ""


def determine_price_basis(contract_price: Optional[float], participant_offer_price: Optional[float], text: str) -> Tuple[str, bool, Optional[float], str]:
    if contract_price is not None and participant_offer_price is not None:
        if participant_offer_price > contract_price * 10:
            return (
                PRICE_BASIS_PARTICIPANT_OFFER,
                False,
                None,
                "Предложение участника существенно больше фиксированной цены контракта; вероятна процедура с ценой за единицу/расчетной базой. Снижение не рассчитывается автоматически.",
            )

    lower = text.lower()
    if "начальная максимальная цена за единицу" in lower or "цена за единицу" in lower:
        return (
            PRICE_BASIS_PARTICIPANT_OFFER,
            False,
            None,
            "В тексте обнаружены признаки процедуры с ценой за единицу. Снижение не рассчитывается автоматически.",
        )

    return PRICE_BASIS_CONTRACT, True, None, "Стандартная ценовая база; снижение может рассчитываться от НМЦК при наличии сопоставимой цены победителя."


def calculate_reduction(nmck: Optional[float], winner_price: Optional[float], auto: bool) -> Optional[float]:
    if not auto or nmck in (None, 0) or winner_price is None:
        return None
    return round(((nmck - winner_price) / nmck) * 100, 2)


def collect_from_text(text: str, procurement_number: str, deal_id: Optional[int], task_id: Optional[int]) -> Dict[str, Any]:
    compact = compact_text(text)
    lower = compact.lower()

    protocol_name, protocol_date, protocol_url = extract_protocol(compact, procurement_number)
    failed_reason = extract_failed_reason(compact)
    participants_count = extract_participants_count(compact)

    participant_offer_price = find_money_after(
        compact,
        [
            "Предложение участника",
            "Предложение о цене",
            "Цена, предложенная участником",
        ],
        window=700,
    )
    contract_price = find_money_after(compact, ["Цена контракта"], window=700)
    nmck = find_money_after(
        compact,
        [
            "Начальная максимальная цена контракта",
            "Начальная (максимальная) цена контракта",
            "НМЦК",
        ],
        window=700,
    )

    planned_participant_name = extract_name_after(compact, ["Наименование участника", "Участник, с которым планируется заключить контракт"])
    planned_participant_inn = extract_inn_near(compact, "Наименование участника")

    supplier_name = extract_name_after(compact, ["Поставщик", "Подрядчик", "Исполнитель"])
    supplier_inn = extract_inn_near(compact, supplier_name) if supplier_name else ""

    winner_name = supplier_name or planned_participant_name
    winner_inn = supplier_inn or planned_participant_inn
    winner_price = participant_offer_price if participant_offer_price is not None else contract_price

    price_basis, auto_calc, reduction_percent, price_logic_comment = determine_price_basis(contract_price, participant_offer_price, compact)
    reduction_percent = calculate_reduction(nmck or contract_price, winner_price, auto_calc)

    contract_registry_number = extract_registry_contract_number(compact)
    contract_publish_date = extract_contract_publish_date(compact)

    warnings: List[str] = []
    if not winner_name:
        warnings.append("Не удалось уверенно определить победителя/поставщика по странице.")
    if participant_offer_price is None:
        warnings.append("Не найдено предложение участника; проверьте вкладку результатов определения поставщика.")
    if contract_price is None:
        warnings.append("Не найдена цена контракта.")
    if participants_count is None:
        warnings.append("Не удалось определить количество участников/заявок.")
    if "несостояв" in lower and winner_name:
        warnings.append("Процедура имеет признак несостоявшейся, но найден участник/поставщик для результата.")

    result_status = "ok" if winner_name and winner_price is not None else "manual_check"
    confidence = "high" if result_status == "ok" and participant_offer_price is not None and contract_price is not None else "medium"

    return {
        "mode": "dry_run",
        "deal_id": deal_id,
        "task_id": task_id,
        "procurement_number": procurement_number,
        "law": extract_law(compact),
        "procedure_type": extract_procedure_type(compact),
        "purchase_name": extract_purchase_name(compact),
        "customer_name": extract_customer_name(compact),
        "procurement_status": extract_status(compact),
        "nmck": nmck or contract_price,
        "contract_price": contract_price,
        "price_basis": price_basis,
        "auto_calculate_reduction": auto_calc,
        "protocol_url": protocol_url,
        "protocol_name": protocol_name,
        "protocol_date": protocol_date,
        "failed_procurement_reason": failed_reason,
        "planned_contract_participant_name": planned_participant_name,
        "planned_contract_participant_inn": planned_participant_inn,
        "winner_name": winner_name,
        "winner_inn": winner_inn,
        "winner_price": winner_price,
        "winner_offer_price": participant_offer_price,
        "reduction_percent": reduction_percent,
        "participants_count": participants_count,
        "our_place": None,
        "contract_registry_number": contract_registry_number,
        "contract_publish_date": contract_publish_date,
        "result_status": result_status,
        "confidence": confidence,
        "comment": (
            "Данные собраны со страницы результатов определения поставщика ЕИС. "
            f"{price_logic_comment} "
            "Стадию сделки не менять. Задачу не закрывать."
        ),
        "target_stage_id": "",
        "allow_overwrite": False,
        "sources": [
            {
                "title": "Результаты определения поставщика ЕИС",
                "url": build_url(SUPPLIER_RESULTS_PATH, procurement_number),
                "what_confirmed": "участник/поставщик, предложение участника, цена контракта, сведения о протоколе и контракте",
            },
            {
                "title": "Итоговый протокол ЕИС",
                "url": protocol_url,
                "what_confirmed": "название и дата итогового протокола",
            },
        ],
        "warnings": warnings,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect EIS tender result and produce Bitrix24 payload JSON")
    parser.add_argument("--procurement-number", required=True, help="EIS notice/reg number")
    parser.add_argument("--deal-id", type=int, default=None, help="Bitrix24 deal id")
    parser.add_argument("--task-id", type=int, default=None, help="Bitrix24 task id")
    parser.add_argument("--source-html", default=None, help="Optional local saved EIS HTML page")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--print-json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(list(argv) if argv is not None else None)

    procurement_number = args.procurement_number.strip()
    if not re.fullmatch(r"\d{19}", procurement_number):
        eprint("procurement-number must be a 19-digit EIS notice number")
        return 2

    source_url = build_url(SUPPLIER_RESULTS_PATH, procurement_number)
    try:
        if args.source_html:
            raw_html = read_source_html(Path(args.source_html))
        else:
            raw_html = fetch_url(source_url)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        eprint(f"Failed to read EIS source page: {exc}")
        return 1

    text = strip_html(raw_html)
    payload = collect_from_text(text, procurement_number, args.deal_id, args.task_id)

    output_text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text + "\n", encoding="utf-8")

    if args.print_json or not args.output:
        print(output_text)

    if payload["result_status"] != "ok":
        eprint("Collection completed with manual_check. Review warnings.")
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
