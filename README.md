# Bitrix Tender Result Automation

Этот репозиторий используется как технический слой для MVP-автоматизации заполнения блока **«Результаты процедуры»** в Bitrix24.

Основной модуль находится здесь:

```text
bitrix_tender_results/
```

GitHub Actions workflow:

```text
.github/workflows/fill_tender_result.yml
```

## Режим работы

По умолчанию используется безопасный режим:

```text
dry_run
```

В этом режиме сделка Bitrix24 не меняется.

Реальная запись возможна только при ручном запуске workflow с:

```text
mode = update
```

и только если настроены реальные коды полей Bitrix24 и GitHub Secret `BITRIX_WEBHOOK_URL`.

## Документация

См. подробную инструкцию:

```text
bitrix_tender_results/README.md
```
