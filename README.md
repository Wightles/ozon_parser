# Ozon Parser

Проект собирает карточки товаров Ozon, сохраняет текущие значения и историю
метрик в PostgreSQL, выгружает CSV и отдает подготовленные представления для
дашборда Yandex DataLens.

Поток данных:

```text
Ozon -> parser -> CSV + PostgreSQL -> SQL views -> DataLens
```

## Что уже есть

- авторизация в Ozon через браузер и сохранение cookies;
- чтение кода подтверждения из Gmail через OAuth;
- загрузка HTML карточек товаров Ozon;
- извлечение данных из встроенного JSON и JSON-LD;
- пакетный парсинг нескольких SKU с изоляцией ошибок;
- экспорт `results/products.csv`;
- сохранение текущего состояния товаров в `products`;
- сохранение истории цены, рейтинга и отзывов в `product_history`;
- SQL-представления `datalens_products` и `datalens_product_history`;
- Docker Compose для локального PostgreSQL;
- ежедневный DAG Airflow;
- единая CLI-точка запуска `main.py`.

## Быстрый старт

Создайте локальный `.env`:

```bash
cp .env.example .env
```

Заполните в `.env` как минимум:

```env
OZON_SKUS=2359066702,2829800382
OZON_PHONE=
POSTGRES_PASSWORD=
```

Для Neon или другой облачной базы также задайте:

```env
POSTGRES_HOST=
POSTGRES_PORT=5432
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_SSLMODE=require
POSTGRES_CHANNEL_BINDING=require
```

Секреты остаются только локально. В Git не должны попадать `.env`,
`cookies.json`, `credentials.json`, `token.json` и выгрузки из `results/`.

## Команды

Короткие алиасы:

```bash
make help
make doctor-local
make parse-csv
make test
```

По умолчанию `Makefile` использует тот же путь к зависимостям, который был
проверен в текущем окружении. Если зависимости установлены в другом месте,
переопределите его так:

```bash
make test PROJECT_PYTHONPATH=/path/to/dependencies
```

Основной запуск парсера:

```bash
python3 main.py
```

Чтобы поменять товары для парсинга, обновите список в `.env`:

```env
OZON_SKUS=2359066702,2829800382,123456789
```

То же самое явно:

```bash
python3 main.py parse
```

Разовый запуск без правки `.env`:

```bash
python3 main.py parse --sku 2359066702 --sku 2829800382
```

Или одной строкой:

```bash
python3 main.py parse --sku 2359066702,2829800382
```

Разовый запуск только в CSV, без PostgreSQL:

```bash
python3 main.py parse --csv-only --sku 2359066702
```

Разовая выгрузка в отдельный файл:

```bash
python3 main.py parse --csv-only --output results/check.csv --sku 2359066702
```

Проверить Gmail OAuth:

```bash
python3 main.py gmail --auth-only
```

Дождаться свежего кода Ozon в Gmail:

```bash
python3 main.py gmail --lookback-seconds 30 --timeout 120
```

Проверить локальную настройку без парсинга Ozon:

```bash
python3 main.py doctor
```

Проверить только локальные файлы и cookies, без PostgreSQL:

```bash
python3 main.py doctor --skip-database
```

Авторизоваться в Ozon обычным Playwright-браузером:

```bash
python3 main.py auth
```

Сохранить cookies из уже открытого локального Chrome с DevTools Protocol:

```bash
python3 main.py auth --cdp-url http://127.0.0.1:9223 --capture-only
```

Запустить локальный PostgreSQL:

```bash
docker compose up -d postgres
```

Прогнать тесты:

```bash
PYTHONPATH=/private/tmp/ozon-parser-stage13-deps314 python3 -m pytest -q
```

Если зависимости установлены в обычное окружение, достаточно:

```bash
python3 -m pytest -q
```

## Ручные места

Обычный ежедневный запуск после настройки ручных шагов не требует. Но внешние
сервисы могут снова попросить действие пользователя:

- Ozon может запросить вход, код из SMS/почты, подтверждение в приложении или
  антибот-проверку;
- Google может попросить повторный OAuth, если `token.json` удален или отозван;
- DataLens настраивается в интерфейсе вручную, потому что дашборд хранится в
  Yandex Cloud;
- Neon/DataLens требуют актуальные пароли и TLS-подключение.

Проект не обходит CAPTCHA и антибот-защиту. Если Ozon показывает проверку, ее
нужно пройти вручную в браузере.

## PostgreSQL

Схема находится в `sql/schema.sql`.

Основные таблицы:

- `products` — последнее состояние каждого SKU;
- `product_history` — исторические снимки изменяемых метрик.

Представления для DataLens:

- `datalens_products` — одна строка на SKU;
- `datalens_product_history` — несколько строк на SKU по времени.

Подробная инструкция по DataLens: [docs/datalens.md](docs/datalens.md).

## Airflow

DAG находится в `dags/ozon_parser_dag.py`.

Он выполняет три шага:

1. проверяет cookies и подключение к PostgreSQL;
2. запускает парсер;
3. проверяет, что CSV и PostgreSQL содержат успешно обработанные SKU.

Расписание: каждый день в `06:00`.

## Проверка результата

После успешного запуска должны обновиться:

- `results/products.csv`;
- таблица `products`;
- таблица `product_history`;
- DataLens dashboard после обновления источников.

Минимальная SQL-проверка:

```sql
SELECT COUNT(*) FROM datalens_products;
SELECT sku, price, rating, reviews_total, parsed_at
FROM datalens_product_history
ORDER BY parsed_at DESC;
```

## Безопасность

- Не коммитьте строки подключения, пароли, OAuth JSON, токены и cookies.
- Не добавляйте секреты в Docker build context; `.dockerignore` закрывает
  локальные OAuth-файлы, cookies, `.env`, кэши и CSV-выгрузки.
- Для DataLens используйте отдельную роль только для чтения.
- Для облачной базы включайте TLS: `sslmode=require`.
- После случайной публикации секрета сразу перевыпускайте пароль или OAuth
  client secret.
