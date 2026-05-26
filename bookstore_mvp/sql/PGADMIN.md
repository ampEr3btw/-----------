# Создание БД и тестовых данных через pgAdmin

Нужны только **доступ к серверу PostgreSQL** (хост, порт, логин/пароль суперпользователя или права `CREATEDB`) и установленный **pgAdmin 4**.

## 1. Создать базу

1. Подключитесь к серверу в pgAdmin (обычно `localhost`, порт `5432`, пользователь `postgres`).
2. ПКМ по **Databases** → **Create** → **Database…**
3. Имя: `bookstore_mvp`  
4. Encoding: `UTF8`  
5. **Save**

## 2. Выполнить схему

1. ПКМ по базе `bookstore_mvp` → **Query Tool**.
2. **File** → **Open** → выберите файл `bookstore_mvp/sql/schema.sql` (или вставьте его содержимое).
3. Нажмите **Execute** (F5).

Если таблицы уже есть и нужно начать с нуля — удалите базу и создайте заново, либо выполните `sql/reset_demo_data.sql`, затем снова `schema.sql` и `seed_demo.sql`.

## 3. Заполнить тестовые данные

1. В том же **Query Tool** (или новом) откройте `bookstore_mvp/sql/seed_demo.sql`.
2. **Execute** (F5).

## 3.1 Если БД уже создана ранее и нужно перезаполнить стартовые данные

1. Откройте `bookstore_mvp/sql/reset_demo_data.sql`
2. Выполните (F5)
3. Откройте `bookstore_mvp/sql/seed_demo.sql`
4. Выполните (F5)

## 4. Подключить приложение

В файле `.env` укажите строку подключения (подставьте свой пароль и хост):

```env
DATABASE_URL=postgresql+asyncpg://postgres:ВАШ_ПАРОЛЬ@localhost:5432/bookstore_mvp
```

Перезапустите `uvicorn`.

## Тестовые учётные записи

| Кто   | Как войти        | Пароль   |
|-------|------------------|----------|
| Админ  | Логин `admin` на `/admin/login` | `admin123` |

В стартовом сиде нет клиентских аккаунтов и заказов — они создаются в процессе работы приложения.

Хэши паролей в `seed_demo.sql` соответствуют `app/security.py`. Пересоздать хэш для своего пароля:

```bash
cd bookstore_mvp
set PYTHONPATH=.
.venv\Scripts\python -c "from app.security import hash_password; print(hash_password('ВашНовыйПароль'))"
```

## Если нет pgAdmin

Из каталога проекта (если в PATH есть `psql`):

```bash
psql -U postgres -c "CREATE DATABASE bookstore_mvp;"
psql -U postgres -d bookstore_mvp -f sql/schema.sql
psql -U postgres -d bookstore_mvp -f sql/seed_demo.sql
```
