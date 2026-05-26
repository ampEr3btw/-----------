-- =============================================================================
-- Стартовые данные для "чистого запуска" bookstore_mvp
-- Выполнять ПОСЛЕ sql/schema.sql на пустой БД
-- Логин администратора: admin
-- Пароль администратора: admin123
-- =============================================================================

INSERT INTO roles (name) VALUES
  ('admin'),
  ('manager'),
  ('cashier')
ON CONFLICT (name) DO NOTHING;

INSERT INTO admins (id, last_name, first_name, middle_name, login, password_hash, role_id, is_active) VALUES
  (1, 'Сидоров', 'Алексей', 'Игоревич', 'admin', '$2b$12$4pDJLRpkJE1QZnxoPlPEhe2V8KyLl4bCJ5DlnU0Tt4zlkkgx0czs2', (SELECT id FROM roles WHERE name = 'admin' LIMIT 1), TRUE)
ON CONFLICT (id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('admins', 'id'), (SELECT COALESCE(MAX(id), 1) FROM admins));

INSERT INTO authors (id, last_name, first_name, middle_name) VALUES
  (1, 'Толстой', 'Лев', 'Николаевич'),
  (2, 'Достоевский', 'Фёдор', 'Михайлович'),
  (3, 'Булгаков', 'Михаил', 'Афанасьевич'),
  (4, 'Оруэлл', 'Джордж', NULL),
  (5, 'Азимов', 'Айзек', NULL),
  (6, 'Кристи', 'Агата', NULL),
  (7, 'Роулинг', 'Джоан', 'Кэтлин'),
  (8, 'Сент-Экзюпери', 'Антуан', 'де'),
  (9, 'Конан Дойл', 'Артур', NULL),
  (10, 'Лутц', 'Марк', NULL),
  (11, 'Мартин', 'Роберт', 'Сесил'),
  (12, 'Уир', 'Энди', NULL),
  (13, 'Кинг', 'Стивен', NULL),
  (14, 'Ремарк', 'Эрих', 'Мария'),
  (15, 'Брэдбери', 'Рэй', NULL)
ON CONFLICT (id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('authors', 'id'), (SELECT COALESCE(MAX(id), 1) FROM authors));

INSERT INTO categories (id, name) VALUES
  (1, 'Классика'),
  (2, 'Фантастика'),
  (3, 'Детектив'),
  (4, 'Нон-фикшн'),
  (5, 'Детская литература'),
  (6, 'Программирование'),
  (7, 'Психология'),
  (8, 'Современная проза')
ON CONFLICT (id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('categories', 'id'), (SELECT COALESCE(MAX(id), 1) FROM categories));

INSERT INTO books (id, title, isbn, publication_year, description, cover_url, price, quantity) VALUES
  (1, 'Война и мир', '978-5-001-10001-1', 2019, 'Эпопея о русском обществе в эпоху войн 1812 года.', '/static/images/books/imageholder.jpg', 1299.00, 14),
  (2, 'Преступление и наказание', '978-5-001-10002-8', 2020, 'Психологический роман о вине и искуплении.', '/static/images/books/imageholder.jpg', 690.00, 10),
  (3, 'Мастер и Маргарита', '978-5-001-10003-5', 2021, 'Роман о добре, зле и свободе.', '/static/images/books/imageholder.jpg', 590.00, 9),
  (4, '1984', '978-5-001-10004-2', 2021, 'Антиутопия о тоталитарном обществе.', '/static/images/books/imageholder.jpg', 470.00, 22),
  (5, 'Скотный двор', '978-5-001-10005-9', 2020, 'Политическая сатира в форме повести.', '/static/images/books/imageholder.jpg', 390.00, 18),
  (6, 'Основание', '978-5-001-10006-6', 2022, 'Космическая сага о судьбе империи.', '/static/images/books/imageholder.jpg', 790.00, 12),
  (7, 'Я, робот', '978-5-001-10007-3', 2019, 'Сборник рассказов о робототехнике.', '/static/images/books/imageholder.jpg', 640.00, 11),
  (8, 'Десять негритят', '978-5-001-10008-0', 2020, 'Классический закрытый детектив.', '/static/images/books/imageholder.jpg', 520.00, 16),
  (9, 'Убийство в Восточном экспрессе', '978-5-001-10009-7', 2018, 'Эркюль Пуаро и сложное расследование.', '/static/images/books/imageholder.jpg', 560.00, 13),
  (10, 'Гарри Поттер и философский камень', '978-5-001-10010-3', 2017, 'Начало истории юного волшебника.', '/static/images/books/imageholder.jpg', 920.00, 20),
  (11, 'Гарри Поттер и тайная комната', '978-5-001-10011-0', 2018, 'Второй год обучения в Хогвартсе.', '/static/images/books/imageholder.jpg', 930.00, 17),
  (12, 'Маленький принц', '978-5-001-10012-7', 2020, 'Лирическая повесть-притча.', '/static/images/books/imageholder.jpg', 360.00, 26),
  (13, 'Шерлок Холмс: Знак четырёх', '978-5-001-10013-4', 2016, 'Приключения великого сыщика.', '/static/images/books/imageholder.jpg', 510.00, 8),
  (14, 'Шерлок Холмс: Собака Баскервилей', '978-5-001-10014-1', 2016, 'Одна из самых известных повестей.', '/static/images/books/imageholder.jpg', 540.00, 7),
  (15, 'Python для начинающих', '978-5-001-10015-8', 2024, 'Практический старт в программировании.', '/static/images/books/imageholder.jpg', 1190.00, 24),
  (16, 'Изучаем Python', '978-5-001-10016-5', 2023, 'Полный курс языка Python.', '/static/images/books/imageholder.jpg', 1450.00, 14),
  (17, 'Чистый код', '978-5-001-10017-2', 2023, 'Как писать поддерживаемый код.', '/static/images/books/imageholder.jpg', 990.00, 19),
  (18, 'Чистая архитектура', '978-5-001-10018-9', 2023, 'Принципы архитектуры приложений.', '/static/images/books/imageholder.jpg', 1020.00, 15),
  (19, 'Марсианин', '978-5-001-10019-6', 2022, 'История выживания на Марсе.', '/static/images/books/imageholder.jpg', 780.00, 11),
  (20, '451 градус по Фаренгейту', '978-5-001-10020-2', 2019, 'Антиутопия о цензуре и свободе слова.', '/static/images/books/imageholder.jpg', 480.00, 18),
  (21, 'Оно', '978-5-001-10021-9', 2021, 'Роман ужасов о страхе детства.', '/static/images/books/imageholder.jpg', 980.00, 10),
  (22, 'Кладбище домашних животных', '978-5-001-10022-6', 2020, 'Психологический хоррор.', '/static/images/books/imageholder.jpg', 760.00, 9),
  (23, 'Три товарища', '978-5-001-10023-3', 2018, 'Роман о дружбе, любви и времени.', '/static/images/books/imageholder.jpg', 620.00, 14),
  (24, 'На западном фронте без перемен', '978-5-001-10024-0', 2018, 'Классика антивоенной прозы.', '/static/images/books/imageholder.jpg', 670.00, 13),
  (25, 'Психология влияния', '978-5-001-10025-7', 2022, 'Как работают механизмы убеждения.', '/static/images/books/imageholder.jpg', 850.00, 16),
  (26, 'Думай медленно... решай быстро', '978-5-001-10026-4', 2021, 'О когнитивных искажениях и принятии решений.', '/static/images/books/imageholder.jpg', 910.00, 12),
  (27, 'Норвежский лес', '978-5-001-10027-1', 2020, 'Лирический роман о взрослении.', '/static/images/books/imageholder.jpg', 710.00, 10),
  (28, 'Кафка на пляже', '978-5-001-10028-8', 2021, 'Современная магическая проза.', '/static/images/books/imageholder.jpg', 790.00, 9)
ON CONFLICT (id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('books', 'id'), (SELECT COALESCE(MAX(id), 1) FROM books));

INSERT INTO book_authors (book_id, author_id) VALUES
  (1, 1), (2, 2), (3, 3), (4, 4), (5, 4),
  (6, 5), (7, 5), (8, 6), (9, 6), (10, 7),
  (11, 7), (12, 8), (13, 9), (14, 9), (15, 10),
  (16, 10), (17, 11), (18, 11), (19, 12), (20, 15),
  (21, 13), (22, 13), (23, 14), (24, 14), (25, 11),
  (26, 11), (27, 15), (28, 15)
ON CONFLICT DO NOTHING;

INSERT INTO book_categories (book_id, category_id) VALUES
  (1, 1), (2, 1), (3, 1), (4, 2), (5, 2),
  (6, 2), (7, 2), (8, 3), (9, 3), (10, 5),
  (11, 5), (12, 5), (13, 3), (14, 3), (15, 6),
  (16, 6), (17, 6), (18, 6), (19, 2), (20, 2),
  (21, 8), (22, 8), (23, 1), (24, 1), (25, 7),
  (26, 7), (27, 8), (28, 8)
ON CONFLICT DO NOTHING;
