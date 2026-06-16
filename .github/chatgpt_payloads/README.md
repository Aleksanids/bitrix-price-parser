# ChatGPT payloads

Эта папка предназначена для передачи структурированного результата из ChatGPT в GitHub Actions.

## Рабочий файл

```text
.github/chatgpt_payloads/current_payload.json
```

При изменении этого файла запускается workflow:

```text
ChatGPT Protocol Payload to Bitrix
```

## Безопасный режим

Файл-заглушка хранится в `mode = dry_run` и не обновляет Bitrix24.

Для реального обновления payload должен содержать:

```json
{
  "mode": "update",
  "procurement_number": "0873200005426000019",
  "winner_name": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ \"ВИТА-АВТО\"",
  "winner_price": 795073736.0,
  "winner_offer_price": 795073736.0,
  "participants_count": 1,
  "result_status": "ok",
  "confidence": "high",
  "allow_overwrite": false
}
```

`deal_id` можно не передавать, если сделка однозначно находится по номеру извещения в названии сделки.

## Что меняется в Bitrix24

Разрешены только три аналитических поля:

- `UF_CRM_1693464904935` — победитель;
- `UF_CRM_1726788197` — цена победителя;
- `UF_CRM_1751272530` — количество участников.

Стадия меняется только в одном случае: если поле ТО `UF_CRM_1689581836` пустое, сделка переносится на `CATEGORY_ID = 0`, `STAGE_ID = "29"`.

Webhook Bitrix24 хранится только в GitHub Secret `BITRIX_WEBHOOK_URL` и не должен попадать в файлы репозитория.
