# Подключение PostgreSQL к Yandex DataLens

В этом проекте поток данных выглядит так:

`Ozon parser -> PostgreSQL -> DataLens connection -> 2 datasets -> charts -> dashboard`

DataLens получает данные из двух SQL-представлений:

- `datalens_products` — последнее состояние каждого товара, источник таблицы и KPI;
- `datalens_product_history` — снимки изменяемых показателей, источник графиков по времени.

Представления намеренно разделены на два датасета. Если связать текущее
состояние с историей по `sku` в одном датасете, одна строка товара размножится
по числу исторических снимков и итоговые KPI окажутся завышены.

## 1. Запуск локального PostgreSQL

Создайте `.env` из `.env.example`, задайте надежный локальный
`POSTGRES_PASSWORD`, затем запустите контейнер:

```bash
docker compose up -d postgres
docker compose ps
```

При первом запуске нового тома Docker автоматически применит
`sql/schema.sql`. Для уже существующего тома примените актуальную схему
вручную:

```bash
docker compose exec -T postgres sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < sql/schema.sql
```

Проверьте таблицы, представления и наличие данных:

```bash
docker compose exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dt"'
docker compose exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dv datalens_*"'
docker compose exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT COUNT(*) FROM datalens_products;"'
```

Локальный контейнер предназначен для разработки. Облачный DataLens не сможет
подключиться к `localhost`, `127.0.0.1`, имени Docker-сервиса `postgres` или
ноутбуку за NAT. Не публикуйте порт домашнего компьютера в интернет только
ради DataLens. Для постоянного дашборда рекомендуемый вариант — перенести базу
в Managed Service for PostgreSQL в Yandex Cloud.

## 2. Подготовка базы, доступной DataLens

На целевом PostgreSQL примените `sql/schema.sql` и загрузите данные парсером.
Для DataLens создайте отдельного пользователя только для чтения. В сеансе
`psql` выполните команды ниже, заменив `ozon_parser`, если база называется
иначе:

```sql
CREATE ROLE datalens_reader LOGIN;
\password datalens_reader

GRANT CONNECT ON DATABASE ozon_parser TO datalens_reader;
GRANT USAGE ON SCHEMA public TO datalens_reader;
GRANT SELECT ON datalens_products TO datalens_reader;
GRANT SELECT ON datalens_product_history TO datalens_reader;
```

Команда `\password` запросит секрет интерактивно, поэтому пароль не попадет в
репозиторий или историю shell. Не используйте для DataLens владельца базы и не
коммитьте пароль в `.env`.

Для Managed Service for PostgreSQL включите доступ из DataLens в настройках
кластера. Для внешней базы нужны публичный FQDN/IP, TLS-сертификат от
доверенного центра сертификации и правила firewall для актуальных IP-адресов
DataLens. Перед настройкой сверяйте список адресов в официальной инструкции:
<https://yandex.cloud/en/docs/datalens/operations/connection/create-postgresql>.

## 3. Вход и рабочая книга

Ручные действия этого раздела относятся к следующему этапу и здесь не
выполнялись.

1. Откройте <https://datalens.yandex.cloud/> и войдите в Yandex ID, которому
   доступна нужная организация Yandex Cloud.
2. Если DataLens еще не активирован, выберите организацию и активируйте сервис.
3. Откройте **Collections and workbooks**.
4. Нажмите **Create -> Create workbook** и создайте книгу `Ozon analytics`.

Соединение, оба датасета, графики и дашборд сохраняйте в этой рабочей книге.

## 4. Соединение PostgreSQL

В книге `Ozon analytics` нажмите **Create -> Connection -> PostgreSQL**.

Для Managed Service for PostgreSQL выберите кластер из организации. Для
внешней базы выберите ручное указание параметров и заполните:

| Поле | Значение |
|---|---|
| Host name | Публичный FQDN PostgreSQL, не `localhost` |
| Port | `6432` для Managed PostgreSQL по умолчанию; для внешней базы ее реальный порт |
| Database | `ozon_parser` или имя целевой базы |
| Username | `datalens_reader` |
| Password | Пароль, заданный интерактивно |
| Cache TTL | `300` секунд |
| TLS | Включен; для внешней базы при необходимости загрузите CA-сертификат |
| Raw SQL level | Отключен: подготовленных SQL views достаточно |

Нажмите **Check connection**, убедитесь, что проверка успешна, затем
**Create connection**. Название: `Ozon PostgreSQL`.

## 5. Датасет текущего состояния

1. На странице соединения нажмите **Create dataset**.
2. Перетащите представление `public.datalens_products` в рабочую область.
3. Перейдите на вкладку **Fields** и задайте типы и агрегации:

| Поле | Тип | Роль / агрегация |
|---|---|---|
| `sku` | Строка | Измерение, без агрегации |
| `title` | Строка | Измерение |
| `price` | Дробное число | Показатель, среднее |
| `rating` | Дробное число | Показатель, среднее |
| `reviews_total` | Целое число | Показатель, сумма |
| `cover_image` | Строка | Измерение; можно скрыть в wizard |
| `photos_seller` | Целое число | Показатель, сумма |
| `videos_seller` | Целое число | Показатель, сумма |
| `color` | Строка | Измерение |
| `material` | Строка | Измерение |
| `art_set` | Строка | Измерение |
| `has_rich_content` | Логический | Измерение |
| `parsed_at` | Дата и время | Измерение |

4. Сохраните датасет как `Ozon products`.

## 6. Вычисляемые поля KPI

На вкладке **Fields** датасета `Ozon products` нажмите **Add field ->
Calculated field** и создайте показатели:

| Название | Формула | Формат |
|---|---|---|
| `Количество товаров` | `COUNTD([sku])` | Целое число |
| `Средняя цена` | `AVG([price])` | Число с двумя знаками / RUB |
| `Средний рейтинг` | `AVG([rating])` | Число с двумя знаками |
| `Всего отзывов` | `SUM([reviews_total])` | Целое число |
| `Товаров с rich content` | `COUNTD_IF([sku], [has_rich_content] = TRUE)` | Целое число |

Формулы чувствительны к регистру имен полей. После создания сохраните датасет
и убедитесь, что редактор не показывает ошибки формул.

## 7. Датасет истории

Создайте второй датасет из того же соединения:

1. Добавьте только `public.datalens_product_history`.
2. Оставьте `sku` и `parsed_at` измерениями.
3. Для `price` и `rating` задайте агрегацию **Average**, для
   `reviews_total` — **Maximum**.
4. Сохраните датасет как `Ozon product history`.

Один исторический снимок содержит одну строку на товар и момент времени.
Для нескольких запусков в один день используйте точное `parsed_at`, а не
округление до даты, иначе точки могут агрегироваться вместе.

## 8. KPI-чарты

Для каждого из пяти вычисляемых полей датасета `Ozon products`:

1. Нажмите **Create chart**.
2. Выберите визуализацию **Indicator**.
3. Перетащите одно KPI-поле в секцию **Measure**.
4. Сохраните чарты как `KPI - Количество товаров`, `KPI - Средняя цена`,
   `KPI - Средний рейтинг`, `KPI - Всего отзывов` и
   `KPI - Rich content`.

## 9. Таблица товаров

Создайте chart из `Ozon products`, выберите визуализацию **Table** и добавьте
в **Columns** в таком порядке:

`sku`, `title`, `price`, `rating`, `reviews_total`, `photos_seller`,
`videos_seller`, `color`, `material`, `art_set`, `has_rich_content`,
`parsed_at`.

Для полей в таблице используйте значения без итоговой агрегации либо `MAX`,
если wizard требует агрегацию показателя. Отсортируйте по `parsed_at` по
убыванию и сохраните chart как `Товары Ozon`.

## 10. Графики истории

Из датасета `Ozon product history` создайте три **Line chart**:

| Chart | X | Y | Colors / series |
|---|---|---|---|
| `История цены` | `parsed_at` | `price` | `sku` |
| `История рейтинга` | `parsed_at` | `rating` | `sku` |
| `История отзывов` | `parsed_at` | `reviews_total` | `sku` |

Для каждого chart добавьте `sku` в **Filters** или позже создайте общий
селектор на дашборде. Если товаров много, фильтр по одному или нескольким SKU
сохранит графики читаемыми.

## 11. Дашборд

1. В книге нажмите **Create -> Dashboard**.
2. Перетащите пять виджетов **Chart** в верхний ряд и выберите KPI-чарты.
3. Ниже добавьте таблицу товаров и три графика истории.
4. Добавьте selector по `sku` из `Ozon product history` для исторических
   графиков и selector по `parsed_at` с типом **Calendar** и режимом диапазона.
5. Назовите дашборд `Ozon products dashboard` и нажмите **Save**.

Selector влияет на виджеты с совместимым датасетом. Поскольку текущие данные
и история разделены, при необходимости создайте отдельный selector `sku` для
таблицы `Ozon products` и отдельный — для исторических графиков.

## Контрольный список

- PostgreSQL доступен DataLens по публичному или облачному адресу и через TLS.
- Пользователь `datalens_reader` имеет только `CONNECT`, `USAGE` и `SELECT`.
- В `datalens_products` одна строка на SKU.
- `datalens_product_history` содержит несколько снимков на SKU.
- KPI построены только по `Ozon products`, без join с историей.
- Графики истории построены только по `Ozon product history`.
- Пароли, сертификаты и другие секреты не находятся в Git.

## Официальная документация

- [Подключение PostgreSQL](https://yandex.cloud/en/docs/datalens/operations/connection/create-postgresql)
- [Подключение Managed PostgreSQL](https://yandex.cloud/en/docs/managed-postgresql/operations/datalens-connect)
- [Создание и настройка датасета](https://yandex.cloud/en/docs/datalens/dataset/create-dataset)
- [Вычисляемые поля](https://yandex.cloud/en/docs/datalens/concepts/calculations/)
- [Создание charts и dashboard](https://yandex.cloud/en/docs/datalens/quickstart)
