# Bitrix24 Tender Result Automation

MVP-модуль для заполнения **только трёх полей** блока «Результаты процедуры» в сделке Bitrix24 по 44-ФЗ закупке из ЕИС.

## Что нужно пользователю

Рабочий сценарий должен быть простым:

```text
1. Пользователь присылает в ChatGPT выгрузку задач из Bitrix24.
2. ChatGPT извлекает из задач номера извещений, deal_id и task_id.
3. ChatGPT формирует batch_json.
4. Пользователь запускает GitHub Actions batch workflow.
5. GitHub по каждому номеру извещения собирает ЕИС 44-ФЗ.
6. GitHub заполняет в Bitrix24 только 3 поля.
7. Пользователь проверяет карточки сделок.
```

## Поля Bitrix24, которые разрешено обновлять

В текущем MVP обновляются строго только эти поля:

```text
Победитель тендера - аналитика
Цена победителя - аналитика
Количество участников - аналитика
```

Все остальные поля сделки не обновляются:

```text
Стадия сделки — не меняется.
Задача — не закрывается.
Ссылка на итоговый протокол — не заполняется.
% снижения — не заполняется.
Наше место — не заполняется.
Причина отказа/проигрыша — не заполняется.
Комментарий в timeline — не добавляется.
```

## Реальные коды полей Bitrix24

В рабочем файле `bitrix_tender_results/config/bitrix_fields.json` настроены:

```json
{
  "winner_name_analytics": "UF_CRM_1693464904935",
  "winner_price_analytics": "UF_CRM_1726788197",
  "participants_count_analytics": "UF_CRM_1751272530"
}
```

## Основной сценарий MVP

```text
Номер извещения ЕИС 44-ФЗ
→ GitHub Actions запускает collect_44fz_result.py
→ сборщик открывает supplier-results.html и итоговый протокол на zakupki.gov.ru
→ формирует JSON
→ fill_tender_result.py делает dry_run или update
→ в Bitrix24 уходят только 3 разрешённых поля
```

## Источники данных 44-ФЗ

### 1. Победитель

Основной источник:

```text
Вкладка ЕИС «Результаты определения поставщика, подрядчика, исполнителя»
→ раздел «Сведения о заключенном контракте»
```

В CRM записывается:

```text
winner_name → Победитель тендера - аналитика
```

### 2. Цена победителя

Если на странице результатов есть:

```text
Предложение участника
```

то именно это значение записывается в CRM:

```text
winner_price / winner_offer_price → Цена победителя - аналитика
```

Цена контракта остаётся справочной величиной в JSON и не записывается в отдельное поле CRM.

### 3. Количество участников / заявок

Основной источник:

```text
Итоговый протокол
```

В CRM записывается:

```text
participants_count → Количество участников - аналитика
```

## Важные ограничения безопасности

- Webhook Bitrix24 не хранить в коде.
- Webhook Bitrix24 не вставлять в чат.
- Режим по умолчанию — `dry_run`.
- Реальное обновление только при `mode = update`.
- Заполненные поля не перезаписываются без `allow_overwrite = true`.
- В `crm.item.update` передаются только три разрешённых поля.
- За один batch-запуск действует safety-limit `max_items`.

## Структура

```text
.github/workflows/batch_44fz_results.yml
.github/workflows/collect_eis_result.yml
.github/workflows/fill_tender_result.yml
bitrix_tender_results/scripts/batch_44fz_results.py
bitrix_tender_results/scripts/collect_44fz_result.py
bitrix_tender_results/scripts/fill_tender_result.py
bitrix_tender_results/config/bitrix_fields.json
bitrix_tender_results/config/bitrix_fields.example.json
bitrix_tender_results/config/bitrix_fields.schema.json
bitrix_tender_results/examples/collect_eis_result_input_0873200005426000019.example.json
bitrix_tender_results/examples/tender_result_payload.example.json
bitrix_tender_results/examples/tender_result_payload_0873200005426000019.example.json
bitrix_tender_results/README.md
```

## GitHub Secret

В GitHub нужно добавить secret:

```text
BITRIX_WEBHOOK_URL
```

Путь:

```text
Settings → Secrets and variables → Actions → New repository secret
```

Значение должно быть webhook URL портала Bitrix24 вида:

```text
https://allians-express.bitrix24.ru/rest/.../.../
```

Webhook нельзя публиковать в коде, README, issues, pull requests или чате.

## Основной workflow: batch-заполнение по списку задач

Workflow:

```text
Batch 44-FZ Tender Results to Bitrix
```

Поля запуска:

```text
batch_json — JSON-массив задач
mode — dry_run / update
max_items — ограничение количества строк за один запуск
```

Формат `batch_json`:

```json
[
  {
    "procurement_number": "0873200005426000019",
    "deal_id": 15096,
    "task_id": 42712
  }
]
```

Сначала запускать только:

```text
mode = dry_run
```

После проверки результата:

```text
mode = update
```

## Альтернативные workflow

### Сбор по одной закупке

Workflow:

```text
Collect EIS 44-FZ Tender Result
```

Использовать для ручной проверки одной сделки.

### Заполнение одной сделки по готовому JSON

Workflow:

```text
Fill Bitrix24 Tender Result
```

Использовать, если JSON уже подготовлен вручную.

## Реальная запись в Bitrix24

Реальная запись допускается только если одновременно:

```text
mode = update
GitHub Secret BITRIX_WEBHOOK_URL добавлен
result_status = ok
winner_name заполнен
winner_price или winner_offer_price заполнен
participants_count заполнен
поля сделки не заполнены либо allow_overwrite = true
```

## Процедуры с ценой за единицу / фиксированной ценой контракта

Если цена контракта фиксированная, а предложение участника относится к единичной/расчётной базе, то в CRM всё равно записывается:

```text
Цена победителя - аналитика = предложение участника
```

Например:

```json
{
  "contract_price": 550000.0,
  "winner_price": 795073736.0,
  "winner_offer_price": 795073736.0,
  "price_basis": "participant_offer_unit_price",
  "auto_calculate_reduction": false
}
```

В CRM при этом уйдут только:

```text
winner_name
winner_price
participants_count
```
