# Bitrix24 Tender Result Automation

MVP-модуль для заполнения **только трёх полей** блока «Результаты процедуры» в сделке Bitrix24 по 44-ФЗ закупке из ЕИС.

## Текущий фокус

Сейчас автоматизируем только **44-ФЗ**.

223-ФЗ временно не подключаем к автоматическому заполнению.

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
- Массовое обновление сделок запрещено.
- Заполненные поля не перезаписываются без `allow_overwrite = true`.
- В `crm.item.update` передаются только три разрешённых поля.

## Структура

```text
.github/workflows/collect_eis_result.yml
.github/workflows/fill_tender_result.yml
bitrix_tender_results/scripts/collect_44fz_result.py
bitrix_tender_results/scripts/fill_tender_result.py
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

## Конфигурация полей Bitrix24

Создать файл:

```text
bitrix_tender_results/config/bitrix_fields.json
```

На базе примера:

```text
bitrix_tender_results/config/bitrix_fields.example.json
```

Нужно указать реальные коды только трёх полей:

```json
{
  "portal": "allians-express.bitrix24.ru",
  "entityTypeId": 2,
  "categoryId": 0,
  "fields": {
    "winner_name_analytics": "UF_CRM_...",
    "winner_price_analytics": "UF_CRM_...",
    "participants_count_analytics": "UF_CRM_..."
  },
  "automation": {
    "default_mode": "dry_run",
    "allow_overwrite_default": false
  }
}
```

## Workflow 1: сбор результата из ЕИС 44-ФЗ

Workflow:

```text
Collect EIS 44-FZ Tender Result
```

Поля запуска:

```text
procurement_number — номер извещения ЕИС
deal_id — ID сделки Bitrix24
task_id — ID задачи Bitrix24, справочно
run_fill — запускать ли сразу заполнение Bitrix24
fill_mode — dry_run / update
```

Безопасный первый запуск:

```text
procurement_number = 0873200005426000019
deal_id = 15096
task_id = 42712
run_fill = false
fill_mode = dry_run
```

Результат workflow:

```text
bitrix_tender_results/out/collected_tender_result.json
```

JSON прикладывается как artifact GitHub Actions.

## Workflow 2: заполнение Bitrix24 по готовому JSON

Workflow:

```text
Fill Bitrix24 Tender Result
```

Поля запуска:

```text
payload_json — JSON результата процедуры
mode — dry_run / update
```

Первый запуск делать только так:

```text
mode = dry_run
```

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
