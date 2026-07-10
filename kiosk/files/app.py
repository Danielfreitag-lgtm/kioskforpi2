import os
import sqlite3
import json
import secrets
from datetime import datetime
from functools import wraps

from flask import Flask, g, request, session, redirect, url_for, render_template, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kiosk.db")

app = Flask(__name__)
app.secret_key = os.environ.get("KIOSK_SECRET_KEY", "dev-secret-change-me-in-production")

# ---------------------------------------------------------------------------
# Hardcoded admin login as requested: admin / Food
# Stored as a hash so the plaintext password never sits in memory/session.
# ---------------------------------------------------------------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Food")

# Every new order also texts this number (the business owner/staff phone),
# separate from the customer's optional receipt.
OWNER_NOTIFY_NUMBER = "+17153007086"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emoji TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_number INTEGER NOT NULL,
            phone TEXT,
            items_json TEXT NOT NULL,
            total REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            sms_status TEXT DEFAULT 'not_sent',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    defaults = {
        "next_queue_number": "1",
        "now_serving": "0",
        "sms_enabled": "0",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_from_number": "",
        "business_name": "The Kiosk",
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Migration: older databases won't have this column yet.
    existing_cols = [row["name"] for row in db.execute("PRAGMA table_info(orders)").fetchall()]
    if "ready_sms_status" not in existing_cols:
        db.execute("ALTER TABLE orders ADD COLUMN ready_sms_status TEXT DEFAULT 'not_sent'")
    if "owner_sms_status" not in existing_cols:
        db.execute("ALTER TABLE orders ADD COLUMN owner_sms_status TEXT DEFAULT 'not_sent'")
    if "receipt_token" not in existing_cols:
        db.execute("ALTER TABLE orders ADD COLUMN receipt_token TEXT")

    # A few sample menu items so /menu isn't empty on first run
    row = db.execute("SELECT COUNT(*) AS c FROM menu_items").fetchone()
    if row["c"] == 0:
        samples = [
            ("🌮", "Taco", 3.50, 1),
            ("🍔", "Burger", 6.00, 2),
            ("🍟", "Fries", 2.75, 3),
            ("🥤", "Soda", 1.75, 4),
        ]
        db.executemany(
            "INSERT INTO menu_items (emoji, name, price, sort_order) VALUES (?, ?, ?, ?)",
            samples,
        )
    db.commit()
    db.close()


def get_setting(key, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["logged_in"] = True
            session["username"] = username
            next_url = request.args.get("next") or url_for("admin")
            return redirect(next_url)
        error = "Incorrect username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("menu"))


@app.route("/menu")
def menu():
    db = get_db()
    items = db.execute(
        "SELECT * FROM menu_items ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    business_name = get_setting("business_name", "The Kiosk")
    return render_template("menu.html", items=items, business_name=business_name)


@app.route("/queue")
def queue_page():
    business_name = get_setting("business_name", "The Kiosk")
    return render_template("queue.html", business_name=business_name)


@app.route("/receipt/<token>")
def view_receipt(token):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE receipt_token = ?", (token,)).fetchone()
    business_name = get_setting("business_name", "The Kiosk")
    if not order:
        return render_template("receipt.html", order=None, business_name=business_name), 404
    items = json.loads(order["items_json"])
    return render_template(
        "receipt.html", order=order, items=items, business_name=business_name
    )


@app.route("/admin")
@login_required
def admin():
    business_name = get_setting("business_name", "The Kiosk")
    return render_template("admin.html", business_name=business_name)


# ---------------------------------------------------------------------------
# Public API: place an order, check queue status
# ---------------------------------------------------------------------------
@app.route("/api/order", methods=["POST"])
def api_create_order():
    data = request.get_json(force=True, silent=True) or {}
    cart = data.get("items", [])
    phone = (data.get("phone") or "").strip()

    if not cart:
        return jsonify({"error": "Cart is empty."}), 400

    db = get_db()
    # Re-price server-side from the menu table; never trust client-sent prices.
    total = 0.0
    priced_items = []
    for entry in cart:
        item_id = entry.get("id")
        qty = max(1, int(entry.get("qty", 1)))
        row = db.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            continue
        line_total = row["price"] * qty
        total += line_total
        priced_items.append(
            {
                "emoji": row["emoji"],
                "name": row["name"],
                "price": row["price"],
                "qty": qty,
                "line_total": round(line_total, 2),
            }
        )

    if not priced_items:
        return jsonify({"error": "No valid items in cart."}), 400

    queue_number = int(get_setting("next_queue_number", "1"))
    set_setting("next_queue_number", str(queue_number + 1))

    receipt_token = secrets.token_urlsafe(12)
    created_at = datetime.now().isoformat(timespec="seconds")
    cur = db.execute(
        "INSERT INTO orders (queue_number, phone, items_json, total, status, sms_status, created_at, receipt_token) "
        "VALUES (?, ?, ?, ?, 'waiting', 'not_sent', ?, ?)",
        (queue_number, phone, json.dumps(priced_items), round(total, 2), created_at, receipt_token),
    )
    db.commit()
    order_id = cur.lastrowid
    receipt_url = request.host_url.rstrip("/") + url_for("view_receipt", token=receipt_token)

    sms_result = None
    if phone and get_setting("sms_enabled", "0") == "1":
        sms_result = send_sms_receipt(order_id, queue_number, phone, receipt_url)

    if get_setting("sms_enabled", "0") == "1":
        send_sms_owner_notification(order_id, queue_number, phone, priced_items, total)

    return jsonify(
        {
            "order_id": order_id,
            "queue_number": queue_number,
            "total": round(total, 2),
            "items": priced_items,
            "receipt_url": receipt_url,
            "sms": sms_result,
        }
    )


@app.route("/api/queue-status")
def api_queue_status():
    db = get_db()
    now_serving = int(get_setting("now_serving", "0"))
    waiting = db.execute(
        "SELECT queue_number FROM orders WHERE status = 'waiting' ORDER BY queue_number ASC LIMIT 10"
    ).fetchall()
    business_name = get_setting("business_name", "The Kiosk")
    return jsonify(
        {
            "now_serving": now_serving,
            "waiting": [w["queue_number"] for w in waiting],
            "business_name": business_name,
        }
    )


# ---------------------------------------------------------------------------
# Admin API: menu CRUD
# ---------------------------------------------------------------------------
@app.route("/api/admin/menu", methods=["GET"])
@login_required
def api_admin_menu_list():
    db = get_db()
    items = db.execute(
        "SELECT * FROM menu_items ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return jsonify([dict(i) for i in items])


@app.route("/api/admin/menu", methods=["POST"])
@login_required
def api_admin_menu_add():
    data = request.get_json(force=True, silent=True) or {}
    emoji = (data.get("emoji") or "🍽️").strip()
    name = (data.get("name") or "").strip()
    price = data.get("price")

    if not name:
        return jsonify({"error": "Name is required."}), 400
    try:
        price = round(float(price), 2)
        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Price must be a positive number."}), 400

    db = get_db()
    max_order = db.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM menu_items").fetchone()["m"]
    cur = db.execute(
        "INSERT INTO menu_items (emoji, name, price, sort_order) VALUES (?, ?, ?, ?)",
        (emoji, name, price, max_order + 1),
    )
    db.commit()
    new_item = db.execute("SELECT * FROM menu_items WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(new_item)), 201


@app.route("/api/admin/menu/<int:item_id>", methods=["PUT"])
@login_required
def api_admin_menu_update(item_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": "Item not found."}), 404

    emoji = (data.get("emoji") or row["emoji"]).strip()
    name = (data.get("name") or row["name"]).strip()
    price = data.get("price", row["price"])
    try:
        price = round(float(price), 2)
        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Price must be a positive number."}), 400

    db.execute(
        "UPDATE menu_items SET emoji = ?, name = ?, price = ? WHERE id = ?",
        (emoji, name, price, item_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()
    return jsonify(dict(updated))


@app.route("/api/admin/menu/<int:item_id>", methods=["DELETE"])
@login_required
def api_admin_menu_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM menu_items WHERE id = ?", (item_id,))
    db.commit()
    return jsonify({"deleted": item_id})


# ---------------------------------------------------------------------------
# Admin API: queue control
# ---------------------------------------------------------------------------
@app.route("/api/admin/orders")
@login_required
def api_admin_orders():
    db = get_db()
    orders = db.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 50"
    ).fetchall()
    result = []
    for o in orders:
        d = dict(o)
        d["items"] = json.loads(d.pop("items_json"))
        d.setdefault("ready_sms_status", "not_sent")
        d.setdefault("owner_sms_status", "not_sent")
        result.append(d)
    return jsonify(result)


@app.route("/api/admin/queue/next", methods=["POST"])
@login_required
def api_admin_queue_next():
    db = get_db()
    next_order = db.execute(
        "SELECT * FROM orders WHERE status = 'waiting' ORDER BY queue_number ASC LIMIT 1"
    ).fetchone()
    if not next_order:
        return jsonify({"error": "No orders waiting."}), 400

    db.execute("UPDATE orders SET status = 'served' WHERE id = ?", (next_order["id"],))
    set_setting("now_serving", str(next_order["queue_number"]))
    db.commit()

    sms_result = None
    if next_order["phone"] and get_setting("sms_enabled", "0") == "1":
        sms_result = send_sms_ready(next_order["id"], next_order["queue_number"], next_order["phone"])

    return jsonify({"now_serving": next_order["queue_number"], "sms": sms_result})


@app.route("/api/admin/queue/reset", methods=["POST"])
@login_required
def api_admin_queue_reset():
    db = get_db()
    db.execute("DELETE FROM orders")
    set_setting("now_serving", "0")
    set_setting("next_queue_number", "1")
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin API: settings (business name + Twilio / SMS)
# ---------------------------------------------------------------------------
@app.route("/api/admin/settings", methods=["GET"])
@login_required
def api_admin_settings_get():
    keys = [
        "business_name",
        "sms_enabled",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from_number",
    ]
    data = {k: get_setting(k, "") for k in keys}
    # Mask the auth token so it never round-trips to the browser in full.
    if data["twilio_auth_token"]:
        data["twilio_auth_token_set"] = True
        data["twilio_auth_token"] = ""
    else:
        data["twilio_auth_token_set"] = False
    return jsonify(data)


@app.route("/api/admin/settings", methods=["POST"])
@login_required
def api_admin_settings_update():
    data = request.get_json(force=True, silent=True) or {}

    if "business_name" in data:
        set_setting("business_name", (data.get("business_name") or "The Kiosk").strip())

    if "sms_enabled" in data:
        set_setting("sms_enabled", "1" if data.get("sms_enabled") else "0")

    if "twilio_account_sid" in data:
        set_setting("twilio_account_sid", (data.get("twilio_account_sid") or "").strip())

    if "twilio_from_number" in data:
        set_setting("twilio_from_number", (data.get("twilio_from_number") or "").strip())

    # Only overwrite the stored auth token if the admin actually typed a new one.
    if data.get("twilio_auth_token"):
        set_setting("twilio_auth_token", data["twilio_auth_token"].strip())

    return jsonify({"ok": True})


@app.route("/api/admin/sms-test", methods=["POST"])
@login_required
def api_admin_sms_test():
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "Enter a phone number to test."}), 400
    result = send_sms_receipt(
        order_id=0,
        queue_number=0,
        phone=phone,
        is_test=True,
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# SMS sending via Twilio
# ---------------------------------------------------------------------------
def send_sms_receipt(order_id, queue_number, phone, receipt_url=None, is_test=False):
    account_sid = get_setting("twilio_account_sid", "")
    auth_token = get_setting("twilio_auth_token", "")
    from_number = get_setting("twilio_from_number", "")
    business_name = get_setting("business_name", "The Kiosk")

    if not (account_sid and auth_token and from_number):
        result = {"ok": False, "error": "Twilio is not fully configured in Admin > SMS settings."}
        if order_id:
            _mark_sms_status(order_id, "failed")
        return result

    if is_test:
        body = f"{business_name}: this is a test message from your kiosk's SMS settings."
    else:
        body = (
            f"{business_name}: your order #{queue_number} is confirmed! "
            f"View your receipt: {receipt_url}\n"
            "We'll text you when it's ready!"
        )

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        message = client.messages.create(body=body, from_=from_number, to=phone)
        if order_id:
            _mark_sms_status(order_id, "sent")
        return {"ok": True, "sid": message.sid}
    except ImportError:
        result = {"ok": False, "error": "Twilio library not installed on the server (pip install twilio)."}
    except Exception as exc:  # noqa: BLE001 - surface any Twilio/API error to the admin
        result = {"ok": False, "error": str(exc)}

    if order_id:
        _mark_sms_status(order_id, "failed")
    return result


def send_sms_ready(order_id, queue_number, phone):
    """Text a customer when their queue number is called ("order ready")."""
    account_sid = get_setting("twilio_account_sid", "")
    auth_token = get_setting("twilio_auth_token", "")
    from_number = get_setting("twilio_from_number", "")
    business_name = get_setting("business_name", "The Kiosk")

    if not (account_sid and auth_token and from_number):
        _mark_sms_status(order_id, "failed", column="ready_sms_status")
        return {"ok": False, "error": "Twilio is not fully configured in Admin > SMS settings."}

    body = f"{business_name}: your order #{queue_number} is ready! Please come pick it up."

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        message = client.messages.create(body=body, from_=from_number, to=phone)
        _mark_sms_status(order_id, "sent", column="ready_sms_status")
        return {"ok": True, "sid": message.sid}
    except ImportError:
        result = {"ok": False, "error": "Twilio library not installed on the server (pip install twilio)."}
    except Exception as exc:  # noqa: BLE001 - surface any Twilio/API error to the admin
        result = {"ok": False, "error": str(exc)}

    _mark_sms_status(order_id, "failed", column="ready_sms_status")
    return result


def send_sms_owner_notification(order_id, queue_number, customer_phone, items, total):
    """Text the business owner's phone every time a new order comes in."""
    account_sid = get_setting("twilio_account_sid", "")
    auth_token = get_setting("twilio_auth_token", "")
    from_number = get_setting("twilio_from_number", "")
    business_name = get_setting("business_name", "The Kiosk")

    if not (account_sid and auth_token and from_number):
        _mark_sms_status(order_id, "failed", column="owner_sms_status")
        return {"ok": False, "error": "Twilio is not fully configured."}

    lines = [f"{i['qty']}x {i['emoji']} {i['name']}" for i in items]
    contact = f"\nCustomer: {customer_phone}" if customer_phone else ""
    body = (
        f"{business_name}: new order #{queue_number}\n"
        + "\n".join(lines)
        + f"\nTotal: ${total:.2f}"
        + contact
    )

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        message = client.messages.create(body=body, from_=from_number, to=OWNER_NOTIFY_NUMBER)
        _mark_sms_status(order_id, "sent", column="owner_sms_status")
        return {"ok": True, "sid": message.sid}
    except ImportError:
        result = {"ok": False, "error": "Twilio library not installed on the server (pip install twilio)."}
    except Exception as exc:  # noqa: BLE001 - surface any Twilio/API error to the admin
        result = {"ok": False, "error": str(exc)}

    _mark_sms_status(order_id, "failed", column="owner_sms_status")
    return result


def _mark_sms_status(order_id, status, column="sms_status"):
    db = get_db()
    db.execute(f"UPDATE orders SET {column} = ? WHERE id = ?", (status, order_id))
    db.commit()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
    else:
        init_db()  # safe no-op / adds any missing defaults
    # host 0.0.0.0 so it's reachable from other devices on the network
    # (e.g. a phone hitting the Pi's IP, or a second screen showing /queue)
    app.run(host="0.0.0.0", port=5000, debug=True)
