# ChatGPT → GitHub → Bitrix24: итоговые протоколы АльянсЭкспресс

## Назначение

Документ фиксирует рабочую схему передачи результата итогового протокола из ChatGPT в Bitrix24 через GitHub Actions.

Контур: `allians-express.bitrix24.ru`.

## Основной сценарий

```text
Итоговый протокол / PDF / HTML / скрин / ссылка Bitrix24
→ ChatGPT извлекает результат
→ .github/chatgpt_payloads/current_payload.json
→ GitHub Actions
→ fill_tender_result.py
→ Bitrix24 CRM deal
```

## Обязательные поля payload для update

```json
{
  "mode": "update",
  "procurement_number": "0873200005426000019",
  "winner_name": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ \"ВИТА-АВТО\"",
  "winner_price": 795073736.0,
  "winner_offer_price": 795073736.0,
  "participants_count": 1,
  "result_status": "ok",
  "allow_overwrite": false
}
```

`deal_id` не обязателен, если передан `procurement_number` и сделка однозначно находится по номеру извещения.

## Поиск сделки

Алгоритм:

1. Если `deal_id` передан — используется он.
2. Если `deal_id` не передан — скрипт ищет сделку через `crm.deal.list` по номеру извещения в `TITLE`.
3. Если найдена ровно одна сделка — используется её `ID`.
4. Если сделка не найдена — Bitrix24 не обновляется, статус `manual_check`, причина `deal_not_found_by_procurement_number`.
5. Если найдено несколько сделок — Bitrix24 не обновляется, статус `manual_check`, причина `multiple_deals_found_by_procurement_number`.

Номер закупки нормализуется как строка: пробелы и невидимые символы удаляются, ведущие нули сохраняются.

## Обновляемые поля Bitrix24

Скрипт имеет жёсткий allow-list:

| Логическое поле | Поле Bitrix24 | Назначение |
|---|---|---|
| `winner_name` | `UF_CRM_1693464904935` | Победитель тендера - аналитика |
| `winner_price` / `winner_offer_price` | `UF_CRM_1726788197` | Цена победителя - аналитика |
| `participants_count` | `UF_CRM_1751272530` | Количество участников - аналитика |

Остальные поля результата процедуры, задачи, timeline, файлы, процент снижения, наше место и причины отказа не обновляются.

## Проверка поля ТО и стадия

Поле ТО:

```text
UF_CRM_1689581836
```

Если поле заполнено — стадия не меняется.

Если поле пустое — в update payload добавляются:

```json
{
  "CATEGORY_ID": 0,
  "STAGE_ID": "29"
}
```

Это единственное разрешённое исключение из правила «стадию не трогать».

## allow_overwrite=false

Если `allow_overwrite = false`:

- пустое поле заполняется;
- поле с тем же значением считается `no-op`;
- поле с другим значением не перезаписывается, возвращается `manual_check / existing_value_conflict`.

Цены сравниваются как число, количество участников — как integer, строки — после `trim`.

## Workflow

Файл:

```text
.github/workflows/chatgpt_fill_current_payload.yml
```

Запускается:

- при изменении `.github/chatgpt_payloads/current_payload.json`;
- вручную через `workflow_dispatch`.

Workflow не содержит webhook. Webhook хранится только в GitHub Secret:

```text
BITRIX_WEBHOOK_URL
```

## Безопасность

Запрещено:

- писать webhook в код, README, JSON, YAML, issue, PR или чат;
- печатать полный webhook в логах;
- обновлять поля вне allow-list;
- закрывать задачи;
- писать timeline-комментарии;
- менять стадию по любым причинам, кроме пустого ТО.

## Быстрая проверка

```bash
python -m py_compile bitrix_tender_results/scripts/fill_tender_result.py
python -m pytest -q
```
