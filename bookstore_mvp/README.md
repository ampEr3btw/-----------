# bookstore_mvp

Витрина + личный кабинет + админ-панель (FastAPI, Jinja2, PostgreSQL, SQLAlchemy 2 async).

## Быстрый старт (5 шагов)

1. **Создать БД** `bookstore_mvp` (через pgAdmin или `psql`).
2. **Накатить схему**: `sql/schema.sql`.
3. **Залить стартовые данные**: `sql/seed_demo.sql`.
4. **Настроить `.env`** (скопировать из `.env.example` и заполнить `DATABASE_URL`, `SECRET_KEY`).
5. **Запустить приложение**:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   uvicorn app.main:app --reload
   ```

- Сайт: http://127.0.0.1:8000/  
- Админка: http://127.0.0.1:8000/admin/login  
- Админ по умолчанию: `admin` / `admin123`

Подробный путь через pgAdmin: [`sql/PGADMIN.md`](sql/PGADMIN.md).

Повторная заливка старта:
1. `sql/reset_demo_data.sql`
2. `sql/seed_demo.sql`

## Переменные окружения

См. `.env.example`.

## Особенности MVP

- Гостевая корзина в подписанной HttpOnly cookie (`bookstore_cart`).
- Оформление заказа только для авторизованных пользователей; списание остатков и создание заказа в одной транзакции с `SELECT … FOR UPDATE` по книгам.
- JWT в HttpOnly cookie: `access_token` (клиенты, path `/`) и `admin_access_token` (сотрудники, path `/admin`).
- CSRF: токен в серверной сессии (SessionMiddleware), проверка на POST в админке, корзине, оформлении, профиле.
- Доставка: заглушка `app/delivery_stub.py` (база + надбавка за длину адреса, потолок).
- Отмена заказа в админке возвращает остатки на склад (переход в статус `отменен`).
- Обложки книг хранятся локально в `app/static/images/books`; при отсутствии файла ставится дефолт `imageholder.jpg`.
