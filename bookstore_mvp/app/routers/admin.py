from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.orm import selectinload

from app.constants import (
    ALLOWED_TRANSITIONS,
    DELIVERY_PICKUP,
    ORDER_STATUSES,
    STATUS_ASSEMBLED,
    STATUS_CANCELLED,
    STATUS_ISSUED,
    STATUS_LABELS,
    STATUS_NEW,
    STATUS_PROCESSING,
)
from app.csrf import ensure_csrf_token, verify_csrf
from app.database import get_db
from app.helpers import DEFAULT_BOOK_COVER_URL, day_range_for_db
from app.limits import limiter
from app.models import (
    Admin,
    AdminAuditLog,
    Author,
    Book,
    Category,
    ImportBatch,
    Order,
    OrderItem,
    Role,
    StockMovement,
    User,
    book_authors,
    book_categories,
)
from app.order_number import generate_order_number
from app.security import (
    clear_admin_auth_cookie,
    create_admin_token,
    get_admin_context_from_request,
    hash_password,
    set_admin_auth_cookie,
    verify_password,
)

router = APIRouter()
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_CASHIER = "cashier"
BOOK_COVERS_DIR = Path("app/static/images/books")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COVER_SOURCE = PROJECT_ROOT.parent / "книги" / "imageholder.jpg"
DEFAULT_COVER_TARGET_NAME = "imageholder.jpg"
DEFAULT_COVER_URL = DEFAULT_BOOK_COVER_URL
ALLOWED_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}


async def _load_admin(request: Request, db) -> Admin | None:
    ctx = get_admin_context_from_request(request)
    if not ctx:
        return None
    r = await db.execute(
        select(Admin).options(selectinload(Admin.role)).where(Admin.id == ctx["admin_id"])
    )
    admin = r.scalar_one_or_none()
    if not admin or not admin.is_active:
        return None
    if admin.role.name != ctx["role"]:
        return None
    return admin


def _ensure_role(admin: Admin, *allowed_roles: str) -> None:
    role_name = admin.role.name if admin and admin.role else ""
    if role_name not in allowed_roles:
        raise HTTPException(status_code=403, detail="Недостаточно прав для этого раздела")


async def _store_cover_file(upload: UploadFile, book_id: int) -> str:
    filename = (upload.filename or "").strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_COVER_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Допустимы файлы: jpg, jpeg, png, webp, gif, svg")
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл обложки пустой")

    BOOK_COVERS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    stored_name = f"book_{book_id}_{ts}{ext}"
    (BOOK_COVERS_DIR / stored_name).write_bytes(content)
    return f"/static/images/books/{stored_name}"


def _get_default_cover_url() -> str:
    BOOK_COVERS_DIR.mkdir(parents=True, exist_ok=True)
    default_target = BOOK_COVERS_DIR / DEFAULT_COVER_TARGET_NAME
    if not default_target.exists() and DEFAULT_COVER_SOURCE.exists():
        shutil.copy2(DEFAULT_COVER_SOURCE, default_target)
    if default_target.exists():
        return DEFAULT_COVER_URL
    return "/static/images/placeholder.svg"


def _to_decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    val = raw.strip().replace(",", ".")
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _split_multi(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split("|") if item.strip()]


def _parse_author_name(raw: str) -> tuple[str, str, str | None]:
    parts = [x for x in raw.strip().split() if x]
    if not parts:
        return "", "", None
    last_name = parts[0]
    first_name = parts[1] if len(parts) > 1 else "—"
    middle_name = " ".join(parts[2:]) if len(parts) > 2 else None
    return last_name, first_name, middle_name


async def _audit_log(
    db,
    admin_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    payload: dict | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )
    )


async def _stock_movement(
    db,
    *,
    book_id: int,
    qty_delta: int,
    reason: str,
    source_ref: str | None,
    admin_id: int | None,
) -> None:
    db.add(
        StockMovement(
            book_id=book_id,
            qty_delta=qty_delta,
            reason=reason,
            source_ref=source_ref,
            admin_id=admin_id,
        )
    )


def _csv_response(filename: str, columns: list[tuple[str, str]], rows: list[dict]) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in columns])
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _xlsx_response(filename: str, columns: list[tuple[str, str]], rows: list[dict]) -> StreamingResponse:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append([label for _, label in columns])

    for row in rows:
        ws.append([row.get(key, "") for key, _ in columns])

    # Умеренно удобные ширины колонок
    for idx, (_, label) in enumerate(columns, start=1):
        width = max(12, min(40, len(label) + 6))
        ws.column_dimensions[chr(64 + idx)].width = width if idx <= 26 else 20

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(
        iter([stream.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _build_report_dataset(db, report_key: str, start_dt: datetime, end_dt: datetime) -> tuple[str, list[tuple[str, str]], list[dict]]:
    if report_key == "orders":
        rows_raw = (
            await db.execute(
                select(Order)
                .options(selectinload(Order.user), selectinload(Order.items))
                .where(Order.created_at >= start_dt, Order.created_at <= end_dt)
                .order_by(desc(Order.created_at))
                .limit(2000)
            )
        ).scalars().all()
        cols = [
            ("order_number", "Номер"),
            ("created_at", "Дата"),
            ("customer", "Клиент"),
            ("status", "Статус"),
            ("delivery_type", "Получение"),
            ("items_count", "Позиций"),
            ("delivery_cost", "Доставка"),
            ("total_amount", "Сумма"),
            ("pickup_code", "Код выдачи"),
        ]
        rows = []
        for o in rows_raw:
            rows.append(
                {
                    "order_number": o.order_number,
                    "created_at": o.created_at,
                    "customer": o.user.email if o.user else "",
                    "status": STATUS_LABELS.get(o.status, o.status),
                    "delivery_type": o.delivery_type,
                    "items_count": len(o.items),
                    "delivery_cost": o.delivery_cost,
                    "total_amount": o.total_amount,
                    "pickup_code": o.pickup_code or "",
                }
            )
        return "Заказы", cols, rows

    if report_key == "stock":
        rows_raw = (
            await db.execute(
                select(Book).order_by(Book.quantity.asc(), Book.title.asc()).limit(5000)
            )
        ).scalars().all()
        cols = [
            ("id", "ID"),
            ("title", "Название"),
            ("isbn", "ISBN"),
            ("price", "Цена"),
            ("quantity", "Остаток"),
            ("created_at", "Создана"),
        ]
        rows = [
            {
                "id": b.id,
                "title": b.title,
                "isbn": b.isbn,
                "price": b.price,
                "quantity": b.quantity,
                "created_at": b.created_at,
            }
            for b in rows_raw
        ]
        return "Остатки", cols, rows

    if report_key == "movements":
        rows_raw = (
            await db.execute(
                select(StockMovement, Book.title, Admin.login)
                .join(Book, Book.id == StockMovement.book_id)
                .outerjoin(Admin, Admin.id == StockMovement.admin_id)
                .where(StockMovement.created_at >= start_dt, StockMovement.created_at <= end_dt)
                .order_by(desc(StockMovement.created_at))
                .limit(5000)
            )
        ).all()
        cols = [
            ("created_at", "Дата"),
            ("book_title", "Книга"),
            ("qty_delta", "Δ"),
            ("reason", "Причина"),
            ("source_ref", "Источник"),
            ("admin_login", "Админ"),
        ]
        rows = [
            {
                "created_at": m.created_at,
                "book_title": title,
                "qty_delta": m.qty_delta,
                "reason": m.reason,
                "source_ref": m.source_ref or "",
                "admin_login": login or "",
            }
            for m, title, login in rows_raw
        ]
        return "Движение товара", cols, rows

    if report_key == "imports":
        rows_raw = (
            await db.execute(
                select(ImportBatch).where(
                    ImportBatch.created_at >= start_dt,
                    ImportBatch.created_at <= end_dt,
                ).order_by(desc(ImportBatch.created_at)).limit(2000)
            )
        ).scalars().all()
        cols = [
            ("created_at", "Дата"),
            ("file_name", "Файл"),
            ("total_rows", "Строк"),
            ("created_books", "Создано"),
            ("updated_books", "Обновлено"),
            ("error_rows", "Ошибок"),
            ("imported_by_admin_id", "ID админа"),
        ]
        rows = [
            {
                "created_at": b.created_at,
                "file_name": b.file_name,
                "total_rows": b.total_rows,
                "created_books": b.created_books,
                "updated_books": b.updated_books,
                "error_rows": b.error_rows,
                "imported_by_admin_id": b.imported_by_admin_id or "",
            }
            for b in rows_raw
        ]
        return "Импорты CSV", cols, rows

    # admin_actions
    rows_raw = (
        await db.execute(
            select(AdminAuditLog, Admin.login)
            .outerjoin(Admin, Admin.id == AdminAuditLog.admin_id)
            .where(AdminAuditLog.created_at >= start_dt, AdminAuditLog.created_at <= end_dt)
            .order_by(desc(AdminAuditLog.created_at))
            .limit(5000)
        )
    ).all()
    cols = [
        ("created_at", "Дата"),
        ("admin_login", "Админ"),
        ("action", "Действие"),
        ("entity_type", "Сущность"),
        ("entity_id", "ID сущности"),
        ("payload", "Детали"),
    ]
    rows = [
        {
            "created_at": a.created_at,
            "admin_login": login or "",
            "action": a.action,
            "entity_type": a.entity_type or "",
            "entity_id": a.entity_id or "",
            "payload": json.dumps(a.payload, ensure_ascii=False) if a.payload is not None else "",
        }
        for a, login in rows_raw
    ]
    return "Действия админов", cols, rows


@router.get("/login")
async def admin_login_page(request: Request, db=Depends(get_db)):
    if await _load_admin(request, db):
        return RedirectResponse(url="/admin/", status_code=303)
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/login.html",
        {"request": request, "csrf_token": csrf_token},
    )


@router.post("/login")
@limiter.limit("20/minute")
async def admin_login_post(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    login = (form.get("login") or "").strip()
    password = form.get("password")

    result = await db.execute(
        select(Admin).options(selectinload(Admin.role)).where(Admin.login == login)
    )
    admin = result.scalar_one_or_none()

    if not admin or not admin.is_active or not verify_password(str(password), admin.password_hash):
        csrf_token = ensure_csrf_token(request)
        return request.state.templates.TemplateResponse(
            request,
            "admin/login.html",
            {"request": request, "error": "Неверный логин или пароль", "csrf_token": csrf_token},
        )

    token = create_admin_token(admin.id, admin.role.name)
    start_url = "/admin/orders" if admin.role.name == ROLE_CASHIER else "/admin/"
    response = RedirectResponse(url=start_url, status_code=303)
    set_admin_auth_cookie(response, request, token)
    return response


@router.get("/logout")
async def admin_logout(request: Request):
    response = RedirectResponse(url="/admin/login", status_code=303)
    clear_admin_auth_cookie(response)
    return response


@router.get("/")
async def admin_dashboard(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    if admin.role.name == ROLE_CASHIER:
        return RedirectResponse(url="/admin/orders", status_code=303)
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER)

    today = date.today()
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    try:
        date_from = date.fromisoformat(raw_from) if raw_from else (today - timedelta(days=30))
    except ValueError:
        date_from = today - timedelta(days=30)
    try:
        date_to = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        date_to = today

    start_dt, end_dt = day_range_for_db(date_from, date_to)

    orders_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Order).where(
                    Order.created_at >= start_dt,
                    Order.created_at <= end_dt,
                )
            )
        ).scalar()
        or 0
    )

    revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status == STATUS_ISSUED,
                Order.created_at >= start_dt,
                Order.created_at <= end_dt,
            )
        )
    ).scalar() or Decimal("0")

    top_rows = (
        (
            await db.execute(
                select(Book.title, func.sum(OrderItem.quantity).label("qty"))
                .join(OrderItem, OrderItem.book_id == Book.id)
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    Order.status == STATUS_ISSUED,
                    Order.created_at >= start_dt,
                    Order.created_at <= end_dt,
                )
                .group_by(Book.id, Book.title)
                .order_by(desc(func.sum(OrderItem.quantity)))
                .limit(5)
            )
        )
        .all()
    )

    in_work_orders = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.user))
            .where(Order.status.in_([STATUS_NEW, STATUS_PROCESSING, STATUS_ASSEMBLED]))
            .order_by(Order.created_at.asc())
            .limit(20)
        )
    ).scalars().all()

    low_stock_rows = (
        await db.execute(
            select(Book)
            .where(Book.quantity <= 5)
            .order_by(Book.quantity.asc(), Book.title.asc())
            .limit(10)
        )
    ).scalars().all()

    recent_movements = (
        await db.execute(
            select(StockMovement, Book.title, Admin.login)
            .join(Book, Book.id == StockMovement.book_id)
            .outerjoin(Admin, Admin.id == StockMovement.admin_id)
            .order_by(desc(StockMovement.created_at))
            .limit(10)
        )
    ).all()

    recent_imports = (
        await db.execute(
            select(ImportBatch)
            .order_by(desc(ImportBatch.created_at))
            .limit(10)
        )
    ).scalars().all()

    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "request": request,
            "admin": admin,
            "csrf_token": csrf_token,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "orders_count": orders_count,
            "revenue": revenue,
            "status_labels": STATUS_LABELS,
            "top_rows": top_rows,
            "in_work_orders": in_work_orders,
            "low_stock_rows": low_stock_rows,
            "recent_movements": recent_movements,
            "recent_imports": recent_imports,
        },
    )


@router.get("/reports")
async def admin_reports(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER)

    report_key = (request.query_params.get("report") or "orders").strip()
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    export = (request.query_params.get("export") or "").strip().lower()

    today = date.today()
    try:
        date_from = date.fromisoformat(raw_from) if raw_from else (today - timedelta(days=30))
    except ValueError:
        date_from = today - timedelta(days=30)
    try:
        date_to = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        date_to = today

    start_dt, end_dt = day_range_for_db(date_from, date_to)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
        start_dt, end_dt = day_range_for_db(date_from, date_to)

    report_options = [
        ("orders", "Заказы"),
        ("stock", "Остатки"),
        ("movements", "Движение товара"),
        ("imports", "Импорты CSV"),
        ("admin_actions", "Действия админов"),
    ]
    valid_keys = {k for k, _ in report_options}
    if report_key not in valid_keys:
        report_key = "orders"

    title, columns, rows = await _build_report_dataset(db, report_key, start_dt, end_dt)

    if export == "csv":
        filename = f"report_{report_key}_{date_from.isoformat()}_{date_to.isoformat()}.csv"
        return _csv_response(filename, columns, rows)
    if export == "xlsx":
        filename = f"report_{report_key}_{date_from.isoformat()}_{date_to.isoformat()}.xlsx"
        return _xlsx_response(filename, columns, rows)

    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/reports.html",
        {
            "request": request,
            "admin": admin,
            "csrf_token": csrf_token,
            "report_options": report_options,
            "current_report": report_key,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "report_title": title,
            "columns": columns,
            "rows": rows,
        },
    )


@router.get("/books")
async def admin_books_list(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)
    q = (request.query_params.get("q") or "").strip()
    sort = (request.query_params.get("sort") or "newest").strip()
    stmt = select(Book)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(
            or_(
                Book.title.ilike(term),
                Book.isbn.ilike(term),
            )
        )

    if sort == "title_asc":
        stmt = stmt.order_by(Book.title.asc(), Book.id.desc())
    elif sort == "title_desc":
        stmt = stmt.order_by(Book.title.desc(), Book.id.desc())
    elif sort == "price_asc":
        stmt = stmt.order_by(Book.price.asc(), Book.id.desc())
    elif sort == "price_desc":
        stmt = stmt.order_by(Book.price.desc(), Book.id.desc())
    elif sort == "stock_asc":
        stmt = stmt.order_by(Book.quantity.asc(), Book.id.desc())
    elif sort == "stock_desc":
        stmt = stmt.order_by(Book.quantity.desc(), Book.id.desc())
    else:
        sort = "newest"
        stmt = stmt.order_by(Book.id.desc())

    rows = (await db.execute(stmt.limit(500))).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/books_list.html",
        {
            "request": request,
            "admin": admin,
            "books": rows,
            "csrf_token": csrf_token,
            "search_query": q,
            "current_sort": sort,
        },
    )


@router.get("/books/new")
async def admin_book_new(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)
    authors = (await db.execute(select(Author).order_by(Author.last_name, Author.first_name))).scalars().all()
    categories = (await db.execute(select(Category).order_by(Category.name))).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/book_form.html",
        {
            "request": request,
            "admin": admin,
            "book": None,
            "authors": authors,
            "categories": categories,
            "csrf_token": csrf_token,
        },
    )


@router.get("/books/{book_id}/edit")
async def admin_book_edit(request: Request, book_id: int, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)
    result = await db.execute(
        select(Book)
        .options(selectinload(Book.authors), selectinload(Book.categories))
        .where(Book.id == book_id)
    )
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    authors = (await db.execute(select(Author).order_by(Author.last_name, Author.first_name))).scalars().all()
    categories = (await db.execute(select(Category).order_by(Category.name))).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/book_form.html",
        {
            "request": request,
            "admin": admin,
            "book": book,
            "authors": authors,
            "categories": categories,
            "csrf_token": csrf_token,
        },
    )


@router.post("/books/save")
async def admin_book_save(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    book_id_raw = form.get("book_id")
    book_id = int(book_id_raw) if book_id_raw and str(book_id_raw).strip().isdigit() else None

    title = (form.get("title") or "").strip()
    isbn = (form.get("isbn") or "").strip()
    publication_year = form.get("publication_year")
    description = (form.get("description") or "").strip() or None
    cover_url = (form.get("cover_url") or form.get("existing_cover_url") or "").strip() or None
    cover_file = form.get("cover_file")
    price_raw = form.get("price")
    quantity_raw = form.get("quantity")

    if not title or not isbn or not price_raw or quantity_raw is None:
        raise HTTPException(status_code=400, detail="Заполните обязательные поля")

    try:
        price = Decimal(str(price_raw).replace(",", "."))
        quantity = int(quantity_raw)
    except (ArithmeticError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные числовые поля")

    if price <= 0 or quantity < 0:
        raise HTTPException(status_code=400, detail="Некорректные числовые поля")

    year_val: int | None = None
    if publication_year:
        try:
            year_val = int(publication_year)
        except ValueError:
            year_val = None

    author_ids = [int(x) for x in form.getlist("author_ids") if str(x).strip().isdigit()]
    category_ids = [int(x) for x in form.getlist("category_ids") if str(x).strip().isdigit()]

    old_qty = 0
    if book_id:
        result = await db.execute(
            select(Book)
            .options(selectinload(Book.authors), selectinload(Book.categories))
            .where(Book.id == book_id)
        )
        book = result.scalar_one_or_none()
        if not book:
            raise HTTPException(status_code=404, detail="Книга не найдена")
        old_qty = int(book.quantity)
    else:
        book = Book(title=title, isbn=isbn, price=price, quantity=quantity)
        db.add(book)
        await db.flush()
        old_qty = 0

    has_uploaded_cover = (
        cover_file is not None
        and hasattr(cover_file, "filename")
        and hasattr(cover_file, "read")
        and str(getattr(cover_file, "filename", "")).strip() != ""
    )
    if has_uploaded_cover:
        cover_url = await _store_cover_file(cover_file, book.id)
    if not cover_url:
        cover_url = _get_default_cover_url()

    book.title = title
    book.isbn = isbn
    book.publication_year = year_val
    book.description = description
    book.cover_url = cover_url
    book.price = price
    book.quantity = quantity

    resolved_author_ids = (
        (await db.execute(select(Author.id).where(Author.id.in_(author_ids)))).scalars().all()
        if author_ids
        else []
    )
    resolved_category_ids = (
        (await db.execute(select(Category.id).where(Category.id.in_(category_ids)))).scalars().all()
        if category_ids
        else []
    )
    await db.execute(delete(book_authors).where(book_authors.c.book_id == book.id))
    await db.execute(delete(book_categories).where(book_categories.c.book_id == book.id))
    for aid in resolved_author_ids:
        await db.execute(
            pg_insert(book_authors)
            .values(book_id=book.id, author_id=aid)
            .on_conflict_do_nothing()
        )
    for cid in resolved_category_ids:
        await db.execute(
            pg_insert(book_categories)
            .values(book_id=book.id, category_id=cid)
            .on_conflict_do_nothing()
        )

    qty_delta = int(book.quantity) - old_qty
    if qty_delta != 0:
        await _stock_movement(
            db,
            book_id=book.id,
            qty_delta=qty_delta,
            reason="manual_adjust",
            source_ref=f"admin_book_save:{book.id}",
            admin_id=admin.id,
        )

    await _audit_log(
        db,
        admin.id,
        action="book_save",
        entity_type="book",
        entity_id=book.id,
        payload={
            "isbn": book.isbn,
            "quantity": int(book.quantity),
            "qty_delta": qty_delta,
        },
    )

    return RedirectResponse(url="/admin/books", status_code=303)


@router.post("/books/{book_id}/delete")
async def admin_book_delete(request: Request, book_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if book:
        await _audit_log(
            db,
            admin.id,
            action="book_delete",
            entity_type="book",
            entity_id=book.id,
            payload={"isbn": book.isbn, "title": book.title},
        )
        await db.delete(book)
    return RedirectResponse(url="/admin/books", status_code=303)


@router.get("/authors")
async def admin_authors(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)
    q = (request.query_params.get("q") or "").strip()
    sort = (request.query_params.get("sort") or "name_asc").strip()
    stmt = select(Author)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(
            or_(
                Author.last_name.ilike(term),
                Author.first_name.ilike(term),
                Author.middle_name.ilike(term),
            )
        )
    if sort == "name_desc":
        stmt = stmt.order_by(Author.last_name.desc(), Author.first_name.desc(), Author.id.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Author.id.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Author.id.asc())
    else:
        sort = "name_asc"
        stmt = stmt.order_by(Author.last_name.asc(), Author.first_name.asc(), Author.id.asc())

    rows = (await db.execute(stmt)).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/authors.html",
        {
            "request": request,
            "admin": admin,
            "authors": rows,
            "csrf_token": csrf_token,
            "search_query": q,
            "current_sort": sort,
        },
    )


@router.post("/authors/save")
async def admin_author_save(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    author_id = form.get("author_id")
    last_name = (form.get("last_name") or "").strip()
    first_name = (form.get("first_name") or "").strip()
    middle_name = (form.get("middle_name") or "").strip() or None
    if not last_name or not first_name:
        raise HTTPException(status_code=400, detail="Фамилия и имя обязательны")

    if author_id and str(author_id).strip().isdigit():
        a = await db.get(Author, int(author_id))
        if not a:
            raise HTTPException(status_code=404, detail="Не найдено")
    else:
        a = Author(last_name=last_name, first_name=first_name, middle_name=middle_name)
        db.add(a)

    a.last_name = last_name
    a.first_name = first_name
    a.middle_name = middle_name

    return RedirectResponse(url="/admin/authors", status_code=303)


@router.post("/authors/{author_id}/delete")
async def admin_author_delete(request: Request, author_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)
    a = await db.get(Author, author_id)
    if a:
        await db.delete(a)
    return RedirectResponse(url="/admin/authors", status_code=303)


@router.get("/categories")
async def admin_categories(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)
    q = (request.query_params.get("q") or "").strip()
    sort = (request.query_params.get("sort") or "name_asc").strip()
    stmt = select(Category)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(Category.name.ilike(term))
    if sort == "name_desc":
        stmt = stmt.order_by(Category.name.desc(), Category.id.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Category.id.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Category.id.asc())
    else:
        sort = "name_asc"
        stmt = stmt.order_by(Category.name.asc(), Category.id.asc())

    rows = (await db.execute(stmt)).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/categories.html",
        {
            "request": request,
            "admin": admin,
            "categories": rows,
            "csrf_token": csrf_token,
            "search_query": q,
            "current_sort": sort,
        },
    )


@router.post("/categories/save")
async def admin_category_save(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    cid = form.get("category_id")
    name = (form.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название обязательно")

    if cid and str(cid).strip().isdigit():
        c = await db.get(Category, int(cid))
        if not c:
            raise HTTPException(status_code=404, detail="Не найдено")
    else:
        c = Category(name=name)
        db.add(c)

    c.name = name
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.post("/categories/{category_id}/delete")
async def admin_category_delete(request: Request, category_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)
    c = await db.get(Category, category_id)
    if c:
        await db.delete(c)
    return RedirectResponse(url="/admin/categories", status_code=303)


@router.get("/staff")
async def admin_staff_list(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)

    q = (request.query_params.get("q") or "").strip()
    sort = (request.query_params.get("sort") or "id_asc").strip()
    stmt = select(Admin).options(selectinload(Admin.role))
    if q:
        term = f"%{q}%"
        stmt = stmt.where(
            or_(
                Admin.last_name.ilike(term),
                Admin.first_name.ilike(term),
                Admin.middle_name.ilike(term),
                Admin.login.ilike(term),
            )
        )

    if sort == "id_desc":
        stmt = stmt.order_by(Admin.id.desc())
    elif sort == "name_asc":
        stmt = stmt.order_by(Admin.last_name.asc(), Admin.first_name.asc(), Admin.id.asc())
    elif sort == "name_desc":
        stmt = stmt.order_by(Admin.last_name.desc(), Admin.first_name.desc(), Admin.id.desc())
    elif sort == "login_asc":
        stmt = stmt.order_by(Admin.login.asc(), Admin.id.asc())
    elif sort == "login_desc":
        stmt = stmt.order_by(Admin.login.desc(), Admin.id.desc())
    elif sort == "role_asc":
        stmt = stmt.join(Role, Admin.role_id == Role.id).order_by(Role.name.asc(), Admin.id.asc())
    elif sort == "role_desc":
        stmt = stmt.join(Role, Admin.role_id == Role.id).order_by(Role.name.desc(), Admin.id.desc())
    else:
        sort = "id_asc"
        stmt = stmt.order_by(Admin.id.asc())

    staff = (await db.execute(stmt.limit(500))).scalars().all()
    roles = (await db.execute(select(Role).order_by(Role.name.asc()))).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/staff.html",
        {
            "request": request,
            "admin": admin,
            "staff": staff,
            "roles": roles,
            "csrf_token": csrf_token,
            "msg": request.query_params.get("msg") or "",
            "search_query": q,
            "current_sort": sort,
        },
    )


@router.post("/staff/create")
async def admin_staff_create(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    last_name = (form.get("last_name") or "").strip()
    first_name = (form.get("first_name") or "").strip()
    middle_name = (form.get("middle_name") or "").strip() or None
    login = (form.get("login") or "").strip().lower()
    password = (form.get("password") or "").strip()
    role_id_raw = (form.get("role_id") or "").strip()
    is_active = bool(form.get("is_active"))

    if not last_name or not first_name or not login or not password or not role_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Заполните обязательные поля сотрудника")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 6 символов")

    role = await db.get(Role, int(role_id_raw))
    if not role:
        raise HTTPException(status_code=400, detail="Выбрана некорректная роль")

    existing = (await db.execute(select(Admin).where(Admin.login == login))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Логин уже занят")

    new_admin = Admin(
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        login=login,
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=is_active,
    )
    db.add(new_admin)
    await db.flush()

    await _audit_log(
        db,
        admin.id,
        action="staff_create",
        entity_type="admin",
        entity_id=new_admin.id,
        payload={"login": new_admin.login, "role": role.name, "is_active": is_active},
    )
    return RedirectResponse(url="/admin/staff?msg=Сотрудник+добавлен", status_code=303)


@router.get("/staff/{staff_id}/edit")
async def admin_staff_edit_page(request: Request, staff_id: int, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)

    target = (
        await db.execute(select(Admin).options(selectinload(Admin.role)).where(Admin.id == staff_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    roles = (await db.execute(select(Role).order_by(Role.name.asc()))).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/staff_edit.html",
        {
            "request": request,
            "admin": admin,
            "staff_member": target,
            "roles": roles,
            "csrf_token": csrf_token,
        },
    )


@router.post("/staff/{staff_id}/update")
async def admin_staff_update(request: Request, staff_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)

    target = (
        await db.execute(select(Admin).options(selectinload(Admin.role)).where(Admin.id == staff_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    last_name = (form.get("last_name") or "").strip()
    first_name = (form.get("first_name") or "").strip()
    middle_name = (form.get("middle_name") or "").strip() or None
    login = (form.get("login") or "").strip().lower()
    role_id_raw = (form.get("role_id") or "").strip()
    is_active = bool(form.get("is_active"))
    new_password = (form.get("new_password") or "").strip()

    if not last_name or not first_name or not login or not role_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Заполните обязательные поля сотрудника")
    role = await db.get(Role, int(role_id_raw))
    if not role:
        raise HTTPException(status_code=400, detail="Выбрана некорректная роль")

    existing = (
        await db.execute(select(Admin).where(Admin.login == login, Admin.id != staff_id))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Логин уже занят")

    if staff_id == admin.id:
        if not is_active:
            raise HTTPException(status_code=400, detail="Нельзя отключить самого себя")
        if role.name != ROLE_ADMIN:
            raise HTTPException(status_code=400, detail="Нельзя изменить свою роль администратора")

    if new_password and len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Новый пароль должен быть не короче 6 символов")

    target.last_name = last_name
    target.first_name = first_name
    target.middle_name = middle_name
    target.login = login
    target.role_id = role.id
    target.is_active = is_active
    if new_password:
        target.password_hash = hash_password(new_password)

    await _audit_log(
        db,
        admin.id,
        action="staff_update",
        entity_type="admin",
        entity_id=target.id,
        payload={"login": target.login, "role": role.name, "is_active": is_active},
    )
    return RedirectResponse(url="/admin/staff?msg=Сотрудник+обновлен", status_code=303)


@router.post("/staff/{staff_id}/delete")
async def admin_staff_delete(request: Request, staff_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)
    if staff_id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")

    target = (
        await db.execute(select(Admin).options(selectinload(Admin.role)).where(Admin.id == staff_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    if target.role and target.role.name == ROLE_ADMIN and target.is_active:
        active_admins_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Admin)
                    .join(Role, Role.id == Admin.role_id)
                    .where(Admin.is_active.is_(True), Role.name == ROLE_ADMIN)
                )
            ).scalar()
            or 0
        )
        if active_admins_count <= 1:
            raise HTTPException(status_code=400, detail="Нельзя удалить последнего активного администратора")

    await _audit_log(
        db,
        admin.id,
        action="staff_delete",
        entity_type="admin",
        entity_id=target.id,
        payload={"login": target.login, "role": target.role.name if target.role else None},
    )
    await db.delete(target)
    return RedirectResponse(url="/admin/staff?msg=Сотрудник+удален", status_code=303)


@router.get("/pos")
async def admin_pos_page(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

    books = (
        await db.execute(
            select(Book).where(Book.quantity > 0).order_by(Book.title.asc()).limit(1000)
        )
    ).scalars().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/pos_order.html",
        {
            "request": request,
            "admin": admin,
            "books": books,
            "csrf_token": csrf_token,
        },
    )


@router.post("/pos/create")
async def admin_pos_create(request: Request, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

    phone = (form.get("phone") or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Телефон обязателен")

    user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is None:
        synthetic_email = f"pos_{generate_order_number()}@local.invalid"
        user = User(
            last_name="Покупатель",
            first_name="Гость",
            middle_name=None,
            phone=phone,
            email=synthetic_email,
            password_hash=hash_password(generate_order_number()),
        )
        db.add(user)
        await db.flush()

    book_ids_raw = form.getlist("book_id")
    qty_raw = form.getlist("qty")
    lines: list[tuple[int, int]] = []
    for bid, qty in zip(book_ids_raw, qty_raw):
        bid = str(bid).strip()
        qty = str(qty).strip()
        if not bid or not qty:
            continue
        if not bid.isdigit():
            continue
        try:
            q = int(qty)
        except ValueError:
            continue
        if q <= 0:
            continue
        lines.append((int(bid), q))

    if not lines:
        raise HTTPException(status_code=400, detail="Добавьте хотя бы одну позицию заказа")

    aggregated: dict[int, int] = {}
    for bid, q in lines:
        aggregated[bid] = aggregated.get(bid, 0) + q

    book_ids = sorted(aggregated.keys())
    locked = await db.execute(
        select(Book).where(Book.id.in_(book_ids)).order_by(Book.id).with_for_update()
    )
    books = {b.id: b for b in locked.scalars().all()}
    if len(books) != len(book_ids):
        raise HTTPException(status_code=400, detail="Некоторые книги не найдены")

    total_amount = Decimal("0")
    for bid, q in aggregated.items():
        b = books[bid]
        if b.quantity < q:
            raise HTTPException(
                status_code=400,
                detail=f"Недостаточно остатков для «{b.title}» (доступно {b.quantity})",
            )
        total_amount += b.price * q

    order = Order(
        order_number=generate_order_number(),
        user_id=user.id,
        status=STATUS_ISSUED,
        delivery_type=DELIVERY_PICKUP,
        pickup_code=None,
        delivery_address=None,
        delivery_cost=Decimal("0"),
        total_amount=total_amount,
        admin_comment="Продажа на кассе",
    )
    db.add(order)
    await db.flush()

    for bid, q in aggregated.items():
        b = books[bid]
        db.add(
            OrderItem(
                order_id=order.id,
                book_id=b.id,
                quantity=q,
                price_at_purchase=b.price,
            )
        )
        b.quantity -= q
        await _stock_movement(
            db,
            book_id=b.id,
            qty_delta=-q,
            reason="order_create_admin",
            source_ref=f"order:{order.order_number}",
            admin_id=admin.id,
        )

    await _audit_log(
        db,
        admin.id,
        action="order_create_admin",
        entity_type="order",
        entity_id=order.id,
        payload={
            "order_number": order.order_number,
            "user_id": user.id,
            "lines": [{"book_id": bid, "qty": q} for bid, q in aggregated.items()],
        },
    )

    return RedirectResponse(url=f"/admin/orders/{order.id}", status_code=303)


@router.get("/orders")
async def admin_orders(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

    status_filter = request.query_params.get("status") or ""
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    stmt = select(Order).options(selectinload(Order.user)).order_by(desc(Order.created_at)).limit(500)
    if status_filter in ORDER_STATUSES:
        stmt = stmt.where(Order.status == status_filter)

    if raw_from:
        try:
            d_from = date.fromisoformat(raw_from)
            start_f, _ = day_range_for_db(d_from, d_from)
            stmt = stmt.where(Order.created_at >= start_f)
        except ValueError:
            pass
    if raw_to:
        try:
            d_to = date.fromisoformat(raw_to)
            _, end_f = day_range_for_db(d_to, d_to)
            stmt = stmt.where(Order.created_at <= end_f)
        except ValueError:
            pass

    rows = (await db.execute(stmt)).scalars().unique().all()
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/orders_list.html",
        {
            "request": request,
            "admin": admin,
            "orders": rows,
            "status_filter": status_filter,
            "period_from": raw_from or "",
            "period_to": raw_to or "",
            "status_labels": STATUS_LABELS,
            "csrf_token": csrf_token,
        },
    )


@router.get("/orders/{order_id}")
async def admin_order_detail(request: Request, order_id: int, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

    result = await db.execute(
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.book))
        .where(Order.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    allowed = sorted(ALLOWED_TRANSITIONS.get(order.status, set()))
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/order_detail.html",
        {
            "request": request,
            "admin": admin,
            "order": order,
            "allowed_next": allowed,
            "status_labels": STATUS_LABELS,
            "csrf_token": csrf_token,
        },
    )


@router.post("/orders/{order_id}/update")
async def admin_order_update(request: Request, order_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

    result = await db.execute(
        select(Order).options(selectinload(Order.items).selectinload(OrderItem.book)).where(Order.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    new_status = (form.get("status") or "").strip()
    comment = (form.get("admin_comment") or "").strip() or None

    if new_status and new_status != order.status:
        if new_status not in ALLOWED_TRANSITIONS.get(order.status, set()):
            raise HTTPException(status_code=400, detail="Недопустимый переход статуса")

        if new_status == STATUS_CANCELLED and order.status != STATUS_CANCELLED:
            for item in order.items:
                book = await db.get(Book, item.book_id)
                if book:
                    book.quantity += int(item.quantity)
                    await _stock_movement(
                        db,
                        book_id=book.id,
                        qty_delta=int(item.quantity),
                        reason="order_cancel_admin",
                        source_ref=f"order:{order.id}",
                        admin_id=admin.id,
                    )

        order.status = new_status

    if comment is not None:
        order.admin_comment = comment

    order.updated_at = datetime.now()
    await _audit_log(
        db,
        admin.id,
        action="order_update",
        entity_type="order",
        entity_id=order.id,
        payload={
            "new_status": new_status,
            "admin_comment": comment,
        },
    )

    return RedirectResponse(url=f"/admin/orders/{order_id}", status_code=303)


@router.post("/orders/{order_id}/issue-by-code")
async def admin_order_issue_by_code(request: Request, order_id: int, db=Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN, ROLE_MANAGER)

    input_code = (form.get("pickup_code_input") or "").strip()
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    if order.delivery_type != "самовывоз":
        raise HTTPException(status_code=400, detail="Код выдачи только для самовывоза")
    if order.status != "собран":
        raise HTTPException(status_code=400, detail="Выдача по коду возможна только для статуса 'Собран'")
    if not order.pickup_code or input_code != order.pickup_code:
        raise HTTPException(status_code=400, detail="Неверный код получения")

    order.status = STATUS_ISSUED
    order.updated_at = datetime.now()
    extra = f"Выдан по коду сотрудником {admin.login}"
    order.admin_comment = f"{order.admin_comment}\n{extra}" if order.admin_comment else extra

    await _audit_log(
        db,
        admin.id,
        action="order_issue_by_code",
        entity_type="order",
        entity_id=order.id,
        payload={"pickup_code": input_code},
    )
    return RedirectResponse(url=f"/admin/orders/{order_id}", status_code=303)


@router.get("/imports")
async def admin_imports_page(request: Request, db=Depends(get_db)):
    admin = await _load_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    _ensure_role(admin, ROLE_ADMIN)

    batches = (
        await db.execute(select(ImportBatch).order_by(desc(ImportBatch.created_at)).limit(50))
    ).scalars().all()
    import_errors = request.session.pop("import_errors", [])
    csrf_token = ensure_csrf_token(request)
    return request.state.templates.TemplateResponse(
        request,
        "admin/import_csv.html",
        {
            "request": request,
            "admin": admin,
            "batches": batches,
            "csrf_token": csrf_token,
            "msg": request.query_params.get("msg"),
            "import_errors": import_errors,
        },
    )


@router.post("/imports/csv")
async def admin_imports_csv(
    request: Request,
    csrf_token: str = Form(...),
    file: UploadFile = File(...),
    db=Depends(get_db),
):
    verify_csrf(request, csrf_token)

    admin = await _load_admin(request, db)
    if not admin:
        raise HTTPException(status_code=403, detail="Нет доступа")
    _ensure_role(admin, ROLE_ADMIN)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Нужен CSV файл")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")

    reader = csv.DictReader(io.StringIO(text))
    required = {"isbn", "title", "authors", "categories", "price", "qty_delta"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=400,
            detail="Неверный формат CSV. Нужны колонки: isbn,title,authors,categories,price,qty_delta",
        )

    total_rows = 0
    created_books = 0
    updated_books = 0
    error_rows = 0
    row_errors: list[str] = []
    source_ref = f"import:{datetime.now().isoformat(timespec='seconds')}"

    for row in reader:
        total_rows += 1
        try:
            isbn = (row.get("isbn") or "").strip()
            title = (row.get("title") or "").strip()
            if not isbn or not title:
                raise ValueError("isbn/title required")

            qty_delta = int((row.get("qty_delta") or "0").strip())
            if qty_delta == 0:
                continue

            price = _to_decimal(row.get("price"))
            publication_year_raw = (row.get("publication_year") or "").strip()
            publication_year = int(publication_year_raw) if publication_year_raw.isdigit() else None
            description = (row.get("description") or "").strip() or None
            cover_url = (row.get("cover_url") or "").strip() or None

            book_result = await db.execute(
                select(Book)
                .options(selectinload(Book.authors), selectinload(Book.categories))
                .where(Book.isbn == isbn)
            )
            book = book_result.scalar_one_or_none()

            if book is None:
                if price is None or price <= 0:
                    raise ValueError("price required for new book")
                applied_delta = max(0, qty_delta)
                book = Book(
                    title=title,
                    isbn=isbn,
                    price=price,
                    quantity=applied_delta,
                    publication_year=publication_year,
                    description=description,
                    cover_url=cover_url,
                )
                db.add(book)
                await db.flush()
                created_books += 1
            else:
                if price is not None and price > 0:
                    book.price = price
                if publication_year is not None:
                    book.publication_year = publication_year
                if description is not None:
                    book.description = description
                if cover_url is not None:
                    book.cover_url = cover_url
                book.title = title
                new_qty = int(book.quantity) + qty_delta
                if new_qty < 0:
                    raise ValueError("negative stock")
                book.quantity = new_qty
                updated_books += 1
                applied_delta = qty_delta

            for author_raw in _split_multi(row.get("authors")):
                ln, fn, mn = _parse_author_name(author_raw)
                if not ln:
                    continue
                a_res = await db.execute(
                    select(Author).where(
                        Author.last_name == ln,
                        Author.first_name == fn,
                        Author.middle_name == mn,
                    )
                )
                author = a_res.scalar_one_or_none()
                if author is None:
                    author = Author(last_name=ln, first_name=fn, middle_name=mn)
                    db.add(author)
                    await db.flush()
                await db.execute(
                    pg_insert(book_authors)
                    .values(book_id=book.id, author_id=author.id)
                    .on_conflict_do_nothing(index_elements=["book_id", "author_id"])
                )

            for cat_name in _split_multi(row.get("categories")):
                c_res = await db.execute(select(Category).where(Category.name == cat_name))
                category = c_res.scalar_one_or_none()
                if category is None:
                    category = Category(name=cat_name)
                    db.add(category)
                    await db.flush()
                await db.execute(
                    pg_insert(book_categories)
                    .values(book_id=book.id, category_id=category.id)
                    .on_conflict_do_nothing(index_elements=["book_id", "category_id"])
                )

            if applied_delta != 0:
                await _stock_movement(
                    db,
                    book_id=book.id,
                    qty_delta=int(applied_delta),
                    reason="import_csv",
                    source_ref=source_ref,
                    admin_id=admin.id,
                )
        except Exception as exc:
            error_rows += 1
            if len(row_errors) < 20:
                row_errors.append(f"Строка {total_rows}: {str(exc)}")
            continue

    batch = ImportBatch(
        file_name=file.filename,
        imported_by_admin_id=admin.id,
        total_rows=total_rows,
        created_books=created_books,
        updated_books=updated_books,
        error_rows=error_rows,
    )
    db.add(batch)
    await db.flush()

    await _audit_log(
        db,
        admin.id,
        action="import_csv",
        entity_type="import_batch",
        entity_id=batch.id,
        payload={
            "file_name": file.filename,
            "total_rows": total_rows,
            "created_books": created_books,
            "updated_books": updated_books,
            "error_rows": error_rows,
        },
    )

    request.session["import_errors"] = row_errors
    msg = f"Импорт: строк={total_rows}, создано={created_books}, обновлено={updated_books}, ошибок={error_rows}"
    return RedirectResponse(url=f"/admin/imports?msg={msg}", status_code=303)
