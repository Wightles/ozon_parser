# Исследование embedded JSON Ozon

Дата исследования: 1 сентября 2026 года. Тестовый SKU: `2359066702`.

## Подтверждённые блоки

- `script[type="application/ld+json"]` с узлом schema.org `Product`. В актуальном
  пользовательском скрипте для страниц Ozon из него читаются `sku`, `name`,
  `brand`, `offers.price`, `offers.priceCurrency`, `aggregateRating.ratingValue`,
  `aggregateRating.reviewCount`, `offers.url`, `image` и `description`.
- `div[id^="state-"][data-state]` с JSON в HTML-атрибуте. Опубликованные примеры
  Ozon подтверждают, в частности, контейнер `state-webPrice-*`. Extractor декодирует
  такие контейнеры универсально, но пока не связывает их поля с моделью товара.

Подтверждённые источники основных данных:

| Данные | Путь | Статус |
|---|---|---|
| title | JSON-LD `name` | подтверждён |
| price | JSON-LD `offers.price` | подтверждён |
| rating | JSON-LD `aggregateRating.ratingValue` | подтверждён |
| reviews | JSON-LD `aggregateRating.reviewCount` | подтверждён |
| gallery / cover | JSON-LD `image` | подтверждён |
| description | JSON-LD `description` | подтверждён |
| characteristics | — | не подтверждён для тестового SKU |
| rich content | — | не подтверждён для тестового SKU |

Extractor намеренно не разбирает произвольный JavaScript и не предполагает наличие
`__NEXT_DATA__`, `widgetStates` либо других неподтверждённых контейнеров в HTML.

## Что не удалось подтвердить

В рабочем каталоге отсутствует `cookies.json`, а публичный запрос точной карточки
SKU попадает в цикл редиректов Ozon. Поэтому расположение gallery,
characteristics и rich content именно для SKU `2359066702` пока не подтверждено.
Эти пути должны быть добавлены только после получения страницы с авторизованной
сессией.

## Локальная проверка

После создания cookies:

```bash
python inspect_embedded_json.py 2359066702
```

Для уже сохранённой страницы:

```bash
python inspect_embedded_json.py 2359066702 --html diagnostics/product.html
```

Диагностика выводит только тип блока, идентификатор и названия верхнеуровневых
ключей. Полные JSON-значения и cookies в лог не попадают.

Источники исследования:

- <https://gist.github.com/qFamouse/baf5ee80a4630744c089f725c3c03a30>
- <https://gist.github.com/br4instormer/24b029f34d00359bb4c0adec62dd5bb9>
