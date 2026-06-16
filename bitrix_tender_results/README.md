# Bitrix24 Tender Result Automation

MVP-модуль для безопасного заполнения блока **«Результаты процедуры»** в сделке Bitrix24 по результатам анализа протокола закупки.

## Назначение

Сценарий MVP:

```text
Пользователь вручную передает протокол процедуры в ChatGPT
→ ChatGPT анализирует протокол
→ ChatGPT формирует структурированный JSON
→ GitHub Actions запускает обработчик
→ Python-скрипт валидирует payload
→ в режиме dry_run показывает план обновления
→ в режиме update обновляет одну сделку Bitrix24 через REST
```

На первом этапе модуль не парсит ЕИС, РТС, ЭТП и другие площадки автоматически.

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
.github/workflows/fill_tender_result.yml
bitrix_tender_results/scripts/fill_tender_result.py
bitrix_tender_results/config/bitrix_fields.example.json
bitrix_tender_results/config/bitrix_fields.schema.json
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

## Настройка конфигурации

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

Пример для закупки `0873200005426000019` находится здесь:

```text
bitrix_tender_results/examples/tender_result_payload_0873200005426000019.example.json
```

## Ручной запуск GitHub Actions

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

## Реальная запись в Bitrix24

Реальная запись допускается только если одновременно:

- `mode = update`;
- GitHub Secret `BITRIX_WEBHOOK_URL` добавлен;
- `result_status = ok`;
- заполнены `deal_id`, `procurement_number`, `winner_name` и `winner_price` либо `winner_offer_price`;
- нет признаков `manual_check`, `multi_lot`, `cancelled`, `price_not_found`, `winner_not_found`;
- поля сделки не заполнены либо `allow_overwrite = true`;
- если меняется стадия, `target_stage_id` указан явно.

## Тестовый payload

Общий пример находится здесь:

```text
bitrix_tender_results/examples/tender_result_payload.example.json
```

Специальный пример по закупке `0873200005426000019`:

```text
bitrix_tender_results/examples/tender_result_payload_0873200005426000019.example.json
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
