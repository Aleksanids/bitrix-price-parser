# Bitrix24 Tender Result Automation

MVP-модуль для безопасного заполнения блока **«Результаты процедуры»** в сделке Bitrix24 по результатам анализа 44-ФЗ закупки в ЕИС.

## Текущий фокус

Сейчас настраиваем только **44-ФЗ**.

223-ФЗ временно не подключаем к автоматическому заполнению, потому что для 223-ФЗ другая структура протоколов/договоров и нужен отдельный этап.

## Основной сценарий MVP

```text
Номер извещения ЕИС 44-ФЗ
→ GitHub Actions запускает сборщик collect_44fz_result.py
→ сборщик открывает supplier-results.html и итоговый протокол на zakupki.gov.ru
→ формирует структурированный JSON
→ JSON проверяется пользователем/ChatGPT
→ fill_tender_result.py делает dry_run или update в Bitrix24
```

## Источники данных 44-ФЗ

### 1. Победитель / поставщик

Основной источник:

```text
Вкладка ЕИС «Результаты определения поставщика, подрядчика, исполнителя»
→ раздел «Сведения о заключенном контракте»
```

Из этого блока берём:

```text
winner_name
winner_inn
contract_price
contract_registry_number
contract_publish_date
```

### 2. Количество заявок / участников

Основной источник:

```text
Итоговый протокол
```

Из итогового протокола берём:

```text
participants_count
protocol_name
protocol_date
protocol_url
failed_procurement_reason, если есть
```

### 3. Цена победителя для Bitrix24

Если на странице результатов есть:

```text
Предложение участника
```

то именно это значение заносится в поле Bitrix24:

```text
«Цена победителя - аналитика»
```

Цена контракта хранится отдельно как справочная величина в JSON и комментарии.

## Важные ограничения безопасности

- Протоколы закупок не хранить в GitHub.
- Webhook Bitrix24 не хранить в коде.
- Webhook Bitrix24 не вставлять в чат.
- Реальные обновления разрешены только при `mode = update`.
- Режим по умолчанию — `dry_run`.
- Массовое обновление сделок запрещено.
- Удаление сделок и задач запрещено.
- Закрытие задач на MVP отключено.
- Смена стадии разрешена только при явно указанном `target_stage_id`.
- Если `target_stage_id` пустой, стадия не меняется.
- Заполненные поля не перезаписываются без `allow_overwrite = true`.

## Структура

```text
.github/workflows/collect_eis_result.yml
.github/workflows/fill_tender_result.yml
bitrix_tender_results/scripts/collect_44fz_result.py
bitrix_tender_results/scripts/collect_eis_result.py
bitrix_tender_results/scripts/fill_tender_result.py
bitrix_tender_results/config/bitrix_fields.example.json
bitrix_tender_results/config/bitrix_fields.schema.json
bitrix_tender_results/examples/collect_eis_result_input_0873200005426000019.example.json
bitrix_tender_results/examples/tender_result_payload.example.json
bitrix_tender_results/examples/tender_result_payload_0873200005426000019.example.json
bitrix_tender_results/README.md
```

`collect_eis_result.py` оставлен как общий ранний сборщик. Рабочий 44-ФЗ контур использует `collect_44fz_result.py`.

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

## Настройка конфигурации Bitrix24

Скопировать пример:

```bash
cp bitrix_tender_results/config/bitrix_fields.example.json bitrix_tender_results/config/bitrix_fields.json
```

Затем заменить все значения `UF_CRM_TO_BE_DISCOVERED` на реальные коды пользовательских полей Bitrix24.

Минимально нужно заполнить:

- `winner_name_analytics`
- `winner_price_analytics`
- `reduction_percent_analytics`
- `participants_count_analytics`
- `our_place_analytics`
- `final_protocol_url`
- `procedure_refusal_or_loss_reason`

## Workflow 1: сбор результата из ЕИС 44-ФЗ

Workflow:

```text
Collect EIS 44-FZ Tender Result
```

Поля запуска:

- `procurement_number` — номер извещения ЕИС;
- `deal_id` — ID сделки Bitrix24, необязательно для сбора, но нужно для итогового payload;
- `task_id` — ID задачи Bitrix24, необязательно;
- `run_fill` — запускать ли сразу обработчик Bitrix24 после сбора;
- `fill_mode` — `dry_run` или `update` для обработчика Bitrix24.

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

Этот JSON также прикладывается как artifact GitHub Actions.

## Workflow 2: заполнение Bitrix24 по готовому JSON

Workflow:

```text
Fill Bitrix24 Tender Result
```

Поля запуска:

- `payload_json` — JSON результата процедуры;
- `mode` — `dry_run` или `update`.

Первый запуск делать только так:

```text
mode = dry_run
```

## Особые процедуры с ценой за единицу / фиксированной ценой контракта

Для части процедур 44-ФЗ цена контракта может быть фиксированной, а победитель определяется по предложению участника по единичным расценкам или суммарному предложению по единичным позициям.

В таких случаях нельзя автоматически считать снижение по формуле:

```text
((НМЦК - предложение участника) / НМЦК) * 100
```

Такой расчет даст некорректный результат, потому что `НМЦК/цена контракта` и `предложение участника` относятся к разным ценовым базам.

Для таких процедур использовать поля payload:

```json
{
  "nmck": 550000.0,
  "contract_price": 550000.0,
  "price_basis": "participant_offer_unit_price",
  "auto_calculate_reduction": false,
  "winner_price": 795073736.0,
  "winner_offer_price": 795073736.0,
  "reduction_percent": null
}
```

Правило для Bitrix24:

```text
Поле «Цена победителя - аналитика» заполняется значением предложения участника.
Цена контракта фиксируется в комментарии/техническом результате.
Процент снижения не рассчитывается автоматически, если price_basis != contract_price.
```

## Реальная запись в Bitrix24

Реальная запись допускается только если одновременно:

- `mode = update`;
- GitHub Secret `BITRIX_WEBHOOK_URL` добавлен;
- `result_status = ok`;
- заполнены `deal_id`, `procurement_number`, `winner_name` и `winner_price` либо `winner_offer_price`;
- нет признаков `manual_check`, `multi_lot`, `cancelled`, `price_not_found`, `winner_not_found`;
- поля сделки не заполнены либо `allow_overwrite = true`;
- если меняется стадия, `target_stage_id` указан явно.

Для текущего MVP стадия не меняется, потому в payload нужно оставлять:

```json
{
  "target_stage_id": ""
}
```

## Тестовые примеры

Общий пример payload:

```text
bitrix_tender_results/examples/tender_result_payload.example.json
```

Специальный пример по закупке `0873200005426000019`:

```text
bitrix_tender_results/examples/tender_result_payload_0873200005426000019.example.json
```

Пример входных данных для workflow сбора:

```text
bitrix_tender_results/examples/collect_eis_result_input_0873200005426000019.example.json
```

## Статусы результата

Разрешенные controlled statuses:

```text
ok
manual_check
no_winner
cancelled
failed_procurement
multi_lot
price_not_found
winner_not_found
protocol_not_final
procurement_number_mismatch
already_filled
error
```

Автоматическое обновление сделки разрешено только при:

```text
result_status = ok
```
