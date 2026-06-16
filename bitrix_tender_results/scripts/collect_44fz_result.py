#!/usr/bin/env python3
"""Collect 44-FZ tender result data from official EIS pages.

Business rules for MVP:
- The tab "Результаты определения поставщика, подрядчика, исполнителя" is the main source for winner/supplier.
- The winner is taken from the section "Сведения о заключенном контракте" when available.
- The final protocol is the main source for application/participant count.
- Participant offer is written to Bitrix field "Цена победителя - аналитика" when available.
- Contract price is kept separately as reference.
- This script never writes to Bitrix24.
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
PROTOCOL_MAIN_PATH = "/epz/order/notice/zk20/view/protocol/protocol-main-info.html"
COMMON_INFO_PATH = "/epz/order/notice/zk20/view/common-info.html"

MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})*(?:[,.]\d{2})|\d+(?:[,.]\d{2}))(?:\s*(?:₽|руб\.?|RUB))?", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
CONTRACT_REGISTRY_RE = re.compile(r"\b(\d{19,20})\b")

PRICE_BASIS_CONTRACT = "contract_price"
PRICE_BASIS_PARTICIPANT_OFFER = "participant_offer_unit_price"


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def build_url(path: str, reg_number: str, extra: str = "") -> str:
    return f"{EIS_BASE}{path}?regNumber={reg_number}" + (f"&{extra}" if extra else "")


def fetch_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TenderVest44FZCollector/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
    encoding = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    if match:
        encoding = match.group(1)
    return body.decode(encoding, errors="replace")


def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<svg.*?</svg>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)</(?:div|p|tr|td|th|li|br|section|article|h\d|span|a)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def lines(text: str) -> List[str]:
    return [line.strip(" :-—\t") for line in text.splitlines() if line.strip(" :-—\t")]


def window(text: str, keyword: str, before: int = 250, after: int = 1800) -> str:
    index = text.lower().find(keyword.lower())
    if index < 0:
        return ""
    return text[max(0, index - before): min(len(text), index + len(keyword) + after)]


def parse_money(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    cleaned = raw.replace("\u00a0", " ").replace(" ", "")
    cleaned = re.sub(r"[^\d,.-]", "", cleaned)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def first_money(text: str) -> Optional[float]:
    match = MONEY_RE.search(text)
    return parse_money(match.group(1)) if match else None


def money_after(text: str, labels: Iterable[str], after: int = 1200) -> Optional[float]:
    for label in labels:
        fragment = window(text, label, before=0, after=after)
        if not fragment:
            continue
        value = first_money(fragment[len(label):])
        if value is not None:
            return value
    return None


def label_value(text: str, labels: Iterable[str], stops: Iterable[str], max_lines: int = 10) -> str:
    src_lines = lines(text)
    low_labels = [label.lower() for label in labels]
    low_stops = [stop.lower() for stop in stops]
    for idx, line in enumerate(src_lines):
        low_line = line.lower()
        label = next((label for label in low_labels if label in low_line), None)
        if not label:
            continue
        tail = line[low_line.find(label) + len(label):].strip(" :-—\t")
        if tail and not any(stop in tail.lower() for stop in low_stops):
            return compact(tail)
        collected: List[str] = []
        for next_line in src_lines[idx + 1: idx + 1 + max_lines]:
            low_next = next_line.lower()
            if any(stop in low_next for stop in low_stops):
                break
            collected.append(next_line)
        value = compact(" ".join(collected)).strip(" :-—\t")
        if value:
            return value
    return ""


def clean_org(value: str) -> str:
    value = compact(value)
    value = re.sub(r"^(поставщик|подрядчик|исполнитель|наименование поставщика|наименование участника закупки|участник)\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bИНН\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bКПП\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bЦена контракта\b.*$", "", value, flags=re.IGNORECASE)
    return value.strip(" .;:-—")


def inn_near(text: str, keyword: str) -> str:
    if not keyword:
        return ""
    fragment = window(text, keyword, before=0, after=1400)
    match = INN_RE.search(fragment)
    return match.group(1) if match else ""


def extract_protocol_meta(protocol_text: str, reg_number: str) -> Tuple[str, str, str]:
    protocol_name = "Протокол подведения итогов определения поставщика (подрядчика, исполнителя)"
    protocol_date = ""
    fragment = window(protocol_text, "Протокол подведения итогов", before=100, after=1200)
    if fragment:
        date_match = DATE_RE.search(fragment)
        if date_match:
            protocol_date = date_match.group(1)
        name_match = re.search(
            r"(Протокол\s+подведения\s+итогов[^\n]{0,260}?(?:№\s*[А-ЯA-Z0-9-]+)?)",
            compact(fragment),
            flags=re.IGNORECASE,
        )
        if name_match:
            protocol_name = compact(name_match.group(1))
    protocol_url = build_url(PROTOCOL_MAIN_PATH, reg_number, "type=izk&version=1")
    return protocol_name, protocol_date, protocol_url


def extract_applications_count_from_protocol(protocol_text: str) -> Optional[int]:
    patterns = [
        r"Количество\s+поданных\s+заявок\s*[:\-]?\s*(\d+)",
        r"Количество\s+заявок\s*[:\-]?\s*(\d+)",
        r"Подано\s+заявок\s*[:\-]?\s*(\d+)",
        r"Количество\s+участников\s*[:\-]?\s*(\d+)",
        r"Всего\s+заявок\s*[:\-]?\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, protocol_text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    if re.search(r"подан[ао]?\s+только\s+одна\s+заявк", protocol_text, flags=re.IGNORECASE):
        return 1
    if re.search(r"только\s+одна\s+заявк", protocol_text, flags=re.IGNORECASE):
        return 1
    return None


def extract_contract_section(supplier_text: str) -> str:
    section = window(supplier_text, "Сведения о заключенном контракте", before=0, after=2600)
    return section or supplier_text


def extract_winner_from_supplier_results(supplier_text: str) -> Tuple[str, str]:
    section = extract_contract_section(supplier_text)
    name = label_value(
        section,
        [
            "Поставщик (подрядчик, исполнитель)",
            "Поставщик",
            "Подрядчик",
            "Исполнитель",
            "Наименование поставщика",
            "Наименование участника",
        ],
        ["ИНН", "КПП", "Цена контракта", "Реестровый номер", "Дата размещения", "Предложение участника"],
        max_lines=10,
    )
    name = clean_org(name)
    return name, inn_near(section, name)


def extract_failed_reason(protocol_text: str, supplier_text: str) -> str:
    combined = protocol_text + "\n" + supplier_text
    for key in ["признан несостоявшимся", "признана несостоявшейся", "подана только одна заявка", "только одна заявка"]:
        fragment = window(combined, key, before=250, after=650)
        if fragment:
            return compact(fragment)
    return ""


def extract_text_between(text: str, starts: Iterable[str], stops: Iterable[str], max_len: int = 1000) -> str:
    low = text.lower()
    best = -1
    best_start = ""
    for start in starts:
        pos = low.find(start.lower())
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
            best_start = start
    if best < 0:
        return ""
    frag = text[best + len(best_start): best + len(best_start) + max_len]
    low_frag = frag.lower()
    end = len(frag)
    for stop in stops:
        pos = low_frag.find(stop.lower())
        if pos >= 0:
            end = min(end, pos)
    return compact(frag[:end]).strip(" :-—")


def extract_purchase_name(common_text: str, supplier_text: str) -> str:
    source = common_text or supplier_text
    return extract_text_between(source, ["Наименование объекта закупки", "Объект закупки"], ["Этап закупки", "Способ определения", "Заказчик"], 1400)


def extract_customer_name(common_text: str, supplier_text: str) -> str:
    source = common_text or supplier_text
    value = extract_text_between(source, ["Наименование заказчика", "Заказчик"], ["Контактная информация", "Размещение осуществляет", "Место нахождения"], 1200)
    return re.sub(r"^Наименование\s+заказчика\s*", "", value, flags=re.IGNORECASE).strip(" .;:-—")


def extract_procedure_type(text: str) -> str:
    for known in ["Запрос котировок в электронной форме", "Электронный аукцион", "Открытый конкурс в электронной форме"]:
        if known.lower() in text.lower():
            return known
    return ""


def extract_status(text: str) -> str:
    for known in ["Определение поставщика завершено", "Работа комиссии", "Подача заявок", "Закупка завершена", "Отменена"]:
        if known.lower() in text.lower():
            return known
    return ""


def extract_contract_registry_number(supplier_text: str) -> str:
    section = extract_contract_section(supplier_text)
    fragment = window(section, "Реестровый номер контракта", before=0, after=500)
    match = CONTRACT_REGISTRY_RE.search(fragment)
    return match.group(1) if match else ""


def extract_contract_publish_date(supplier_text: str) -> str:
    section = extract_contract_section(supplier_text)
    fragment = window(section, "Дата размещения подписанного контракта", before=0, after=500)
    match = DATE_RE.search(fragment)
    return match.group(1) if match else ""


def determine_price_basis(contract_price: Optional[float], participant_offer: Optional[float], combined_text: str) -> Tuple[str, bool, str]:
    if contract_price is not None and participant_offer is not None and participant_offer > contract_price * 10:
        return PRICE_BASIS_PARTICIPANT_OFFER, False, "Предложение участника существенно больше цены контракта; цена для Bitrix берется из предложения участника, снижение не рассчитывается."
    lower = combined_text.lower()
    if "начальная максимальная цена за единицу" in lower or "цена за единицу" in lower:
        return PRICE_BASIS_PARTICIPANT_OFFER, False, "Есть признаки процедуры с ценой за единицу; снижение не рассчитывается автоматически."
    return PRICE_BASIS_CONTRACT, True, "Стандартная ценовая база; снижение можно рассчитать от НМЦК при сопоставимой цене победителя."


def calculate_reduction(nmck: Optional[float], winner_price: Optional[float], enabled: bool) -> Optional[float]:
    if not enabled or nmck in (None, 0) or winner_price is None:
        return None
    return round(((nmck - winner_price) / nmck) * 100, 2)


def fetch_page_or_empty(title: str, url: str, warnings: List[str]) -> str:
    try:
        return strip_html(fetch_url(url))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        warnings.append(f"Не удалось открыть {title}: {exc}")
        return ""


def collect_44fz(reg_number: str, deal_id: Optional[int], task_id: Optional[int], supplier_html: str = "", protocol_html: str = "", common_html: str = "") -> Dict[str, Any]:
    warnings: List[str] = []
    supplier_url = build_url(SUPPLIER_RESULTS_PATH, reg_number)
    protocol_url = build_url(PROTOCOL_MAIN_PATH, reg_number, "type=izk&version=1")
    common_url = build_url(COMMON_INFO_PATH, reg_number)

    supplier_text = strip_html(supplier_html) if supplier_html else fetch_page_or_empty("supplier-results", supplier_url, warnings)
    protocol_text = strip_html(protocol_html) if protocol_html else fetch_page_or_empty("final protocol", protocol_url, warnings)
    common_text = strip_html(common_html) if common_html else fetch_page_or_empty("common-info", common_url, warnings)

    combined = "\n".join([common_text, supplier_text, protocol_text])
    protocol_name, protocol_date, protocol_url = extract_protocol_meta(protocol_text or supplier_text, reg_number)

    winner_name, winner_inn = extract_winner_from_supplier_results(supplier_text)
    participants_count = extract_applications_count_from_protocol(protocol_text)
    participant_offer = money_after(supplier_text, ["Предложение участника", "Предложение о цене", "Цена, предложенная участником"], after=900)
    contract_price = money_after(supplier_text, ["Цена контракта"], after=900)
    nmck = money_after(common_text, ["Начальная максимальная цена контракта", "Начальная (максимальная) цена контракта", "НМЦК"], after=900) or contract_price

    winner_price = participant_offer if participant_offer is not None else contract_price
    price_basis, auto_reduction, price_comment = determine_price_basis(contract_price, participant_offer, combined)
    reduction_percent = calculate_reduction(nmck, winner_price, auto_reduction)

    if not winner_name:
        warnings.append("Не найден победитель/поставщик в разделе supplier-results / сведения о заключенном контракте.")
    if participants_count is None:
        warnings.append("Не найдено количество заявок в итоговом протоколе.")
    if participant_offer is None:
        warnings.append("Не найдено предложение участника на странице результатов определения поставщика.")
    if contract_price is None:
        warnings.append("Не найдена цена контракта на странице результатов определения поставщика.")

    result_status = "ok" if winner_name and winner_price is not None and participants_count is not None else "manual_check"
    confidence = "high" if result_status == "ok" and not warnings else "medium"

    return {
        "mode": "dry_run",
        "deal_id": deal_id,
        "task_id": task_id,
        "procurement_number": reg_number,
        "law": "44-ФЗ",
        "source_type": "eis_44_supplier_results_and_final_protocol",
        "procedure_type": extract_procedure_type(combined),
        "purchase_name": extract_purchase_name(common_text, supplier_text),
        "customer_name": extract_customer_name(common_text, supplier_text),
        "procurement_status": extract_status(combined),
        "nmck": nmck,
        "contract_price": contract_price,
        "price_basis": price_basis,
        "auto_calculate_reduction": auto_reduction,
        "protocol_url": protocol_url,
        "protocol_name": protocol_name,
        "protocol_date": protocol_date,
        "failed_procurement_reason": extract_failed_reason(protocol_text, supplier_text),
        "winner_name": winner_name,
        "winner_inn": winner_inn,
        "winner_price": winner_price,
        "winner_offer_price": participant_offer,
        "reduction_percent": reduction_percent,
        "participants_count": participants_count,
        "our_place": None,
        "contract_registry_number": extract_contract_registry_number(supplier_text),
        "contract_publish_date": extract_contract_publish_date(supplier_text),
        "result_status": result_status,
        "confidence": confidence,
        "comment": (
            "44-ФЗ: победитель взят со вкладки 'Результаты определения поставщика, подрядчика, исполнителя' "
            "из раздела 'Сведения о заключенном контракте'. Количество заявок взято из итогового протокола. "
            f"{price_comment} Стадию сделки не менять. Задачу не закрывать."
        ),
        "target_stage_id": "",
        "allow_overwrite": False,
        "sources": [
            {
                "title": "Результаты определения поставщика, подрядчика, исполнителя",
                "url": supplier_url,
                "what_confirmed": "победитель/поставщик, предложение участника, цена контракта, сведения о заключенном контракте",
            },
            {
                "title": "Итоговый протокол",
                "url": protocol_url,
                "what_confirmed": "количество заявок/участников и реквизиты итогового протокола",
            },
        ],
        "warnings": warnings,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect 44-FZ EIS result and produce Bitrix24 payload JSON")
    parser.add_argument("--procurement-number", required=True, help="19-digit EIS notice number")
    parser.add_argument("--deal-id", type=int, default=None, help="Bitrix24 deal ID")
    parser.add_argument("--task-id", type=int, default=None, help="Bitrix24 task ID")
    parser.add_argument("--supplier-results-html", default="", help="Optional saved supplier-results HTML")
    parser.add_argument("--protocol-html", default="", help="Optional saved final protocol HTML")
    parser.add_argument("--common-info-html", default="", help="Optional saved common-info HTML")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--print-json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(list(argv) if argv is not None else None)

    reg_number = args.procurement_number.strip()
    if not re.fullmatch(r"\d{19}", reg_number):
        eprint("procurement-number must be a 19-digit EIS notice number")
        return 2

    payload = collect_44fz(
        reg_number,
        args.deal_id,
        args.task_id,
        supplier_html=Path(args.supplier_results_html).read_text(encoding="utf-8", errors="replace") if args.supplier_results_html else "",
        protocol_html=Path(args.protocol_html).read_text(encoding="utf-8", errors="replace") if args.protocol_html else "",
        common_html=Path(args.common_info_html).read_text(encoding="utf-8", errors="replace") if args.common_info_html else "",
    )

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")
    if args.print_json or not args.output:
        print(output)

    if payload["result_status"] != "ok":
        eprint("Collection completed with manual_check. Review warnings.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
