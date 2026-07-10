# Kiosk

A self-order kiosk with a live queue display and an admin dashboard, built with
Flask + SQLite (no external database needed). Runs fine on a Raspberry Pi or
any PC.

## Pages

- **`/menu`** — customer-facing ordering screen (emoji, name, price per item).
  Customers tap items, optionally enter a phone number, and place their order.
  They get a queue number and, if SMS is turned on, a text receipt.
- **`/queue`** — big "now serving" display for a second screen or TV.
- **`/admin`** — staff dashboard (behind login) to manage the menu, call the
  next number, view orders, and configure SMS receipts.

## Login

Username: `admin`
Password: `Food`

The password is stored as a hash in `app.py` (`ADMIN_PASSWORD_HASH`), not in
plaintext, but it's still hardcoded for a single shared admin account. If you
want a different password, generate a new hash:

```bash
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('yournewpassword'))"
```

and paste the result into `ADMIN_PASSWORD_HASH` in `app.py`.

## Setup

```bash
cd kiosk
pip install -r requirements.txt
python3 app.py
```

The first run creates `kiosk.db` (SQLite) with a few sample menu items. The
server listens on `0.0.0.0:5000`, so from another device on the same network
you can open `http://<the-pi-or-pc's-ip>:5000/menu`.

## SMS receipts (Twilio)

1. Sign up at twilio.com and buy/verify a phone number.
2. In `/admin` → **SMS receipts**, enter your Account SID, Auth Token, and
   Twilio phone number, then check **Enable SMS receipts** and save.
3. Use **Send a test text** to confirm it works.

Customers only get a text if they type a phone number at checkout *and* SMS
is enabled. The auth token is stored in the database, not shown back in the
browser once saved.

## Running for real (kiosk mode)

For an actual kiosk deployment:

- Run the app in the background reliably — e.g. a `systemd` service that
  restarts on crash, rather than `python3 app.py` in a terminal:

  ```ini
  # /etc/systemd/system/kiosk.service
  [Unit]
  Description=Kiosk app
  After=network.target

  [Service]
  WorkingDirectory=/home/pi/kiosk
  ExecStart=/usr/bin/python3 app.py
  Restart=always
  User=pi

  [Install]
  WantedBy=multi-user.target
  ```

  Then `sudo systemctl enable --now kiosk`.

- Point Chromium at the kiosk screen in fullscreen/kiosk mode, e.g.:

  ```bash
  chromium-browser --kiosk http://localhost:5000/menu
  ```

  and on a second screen or another device, `http://localhost:5000/queue`.

- Set a fixed IP or hostname for the Pi/PC so the URL doesn't change.

- Before going live, change `app.secret_key` in `app.py` to a long random
  string (currently a placeholder dev value), and consider putting the app
  behind a real WSGI server (gunicorn/waitress) instead of Flask's dev server.

## Notes

- Prices are always re-looked-up server-side from the menu table when an
  order is placed, so nothing client-side can be tampered with to change a
  price.
- "Reset queue" on the admin Queue tab wipes all orders and starts numbering
  back at 1 — handy at the start of a new day/shift.
