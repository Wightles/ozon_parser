# Исследование embedded JSON Ozon

Дата первоначального исследования: 1 сентября 2026 года. Сквозная проверка на
реальных SKU `2359066702` и `2829800382`: 4 сентября 2026 года.

## Подтверждённые блоки

- `script[type="application/ld+json"]` с узлом schema.org `Product`. В актуальном
  пользовательском скрипте для страниц Ozon из него читаются `sku`, `name`,
  `brand`, `offers.price`, `offers.priceCurrency`, `aggregateRating.ratingValue`,
  `aggregateRating.reviewCount`, `offers.url`, `image` и `description`.
- `div[id^="state-"][data-state]` с JSON в HTML-атрибуте. Опубликованные примеры
  Ozon и сквозная проверка подтвердили контейнеры `state-webGallery-*` и
  `state-webShortCharacteristics-*`. Парсер использует их для галереи, видео,
  цвета, материала и артикула, когда соответствующая характеристика присутствует.

Подтверждённые источники основных данных:

| Данные | Путь | Статус |
|---|---|---|
| title | JSON-LD `name` | подтверждён |
| price | JSON-LD `offers.price` | подтверждён |
| rating | JSON-LD `aggregateRating.ratingValue` | подтверждён |
| reviews | JSON-LD `aggregateRating.reviewCount` | подтверждён |
| gallery / cover | `state-webGallery-*` (`images`, `coverImage`) с fallback на JSON-LD `image` | подтверждён |
| seller videos | `state-webGallery-*` (`videos`) | подтверждён |
| description | JSON-LD `description` | подтверждён |
| characteristics | `state-webShortCharacteristics-*` (`characteristics`) | подтверждён |
| rich content | структурный HTML в JSON-LD `description` | подтверждён; отсутствует у двух проверенных SKU |

Extractor намеренно не разбирает произвольный JavaScript и не предполагает наличие
`__NEXT_DATA__`, `widgetStates` либо других неподтверждённых контейнеров в HTML.

## Ограничения подтверждённых данных

Ozon отдаёт только краткий набор характеристик в исходном `data-state`. Если
`Артикул`, `Артикул производителя` или `Комплектация` в нём отсутствуют, поле
`art_set` остаётся `NULL`. Парсер не угадывает значение и не разбирает произвольный
JavaScript. У двух проверенных SKU поле `art_set` отсутствует.

Обычный HTTP-запрос с валидными cookies был отклонён Ozon кодом 403. Для локальной
сквозной проверки использовался явно настроенный транспорт через уже
авторизованный обычный Chrome на loopback-CDP. CAPTCHA и другие защитные проверки
не обходятся: их выполняет пользователь в браузере.

## Локальная проверка

После запуска Chrome с локальным DevTools endpoint и создания cookies:

```bash
python get_cookies.py --cdp-url http://127.0.0.1:9223
OZON_CDP_URL=http://127.0.0.1:9223 python inspect_embedded_json.py 2359066702
OZON_CDP_URL=http://127.0.0.1:9223 python parse_ozon.py
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
