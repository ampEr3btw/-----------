-- Очистка данных перед повторной заливкой seed_demo.sql.
-- Роли остаются (admin/manager/cashier).

TRUNCATE TABLE
  admin_audit_logs,
  stock_movements,
  import_batches,
  order_items,
  orders,
  favorites,
  book_authors,
  book_categories,
  books,
  authors,
  categories,
  users,
  admins
RESTART IDENTITY CASCADE;
