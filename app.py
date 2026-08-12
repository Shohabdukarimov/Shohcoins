import os
import json
import urllib.request
from datetime import date, datetime, timedelta

from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME").strip().lstrip("@")
APP_URL = os.getenv("APP_URL", "https://shohcoins.onrender.com").strip().rstrip("/")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10
REFERRAL_REWARD = 100

BOOSTERS = {
    "x2": {"multiplier": 2, "seconds": 60, "price": 50},
    "x5": {"multiplier": 5, "seconds": 60, "price": 150},
    "x10": {"multiplier": 10, "seconds": 60, "price": 500},
}


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_db():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    balance BIGINT NOT NULL DEFAULT 0,
                    clicks BIGINT NOT NULL DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by BIGINT,
                    referral_count INTEGER NOT NULL DEFAULT 0,
                    last_daily_bonus DATE,
                    booster_type TEXT DEFAULT '',
                    booster_multiplier INTEGER NOT NULL DEFAULT 1,
                    booster_expires_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    inviter_id BIGINT NOT NULL,
                    invited_id BIGINT UNIQUE NOT NULL,
                    reward BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Upgrade databases created by older versions.
            upgrades = (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS clicks BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus DATE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS booster_type TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS booster_multiplier INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS booster_expires_at TIMESTAMP NULL",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            )
            for sql in upgrades:
                cur.execute(sql)

            # Existing databases may have duplicate referral rows from old code.
            # The unique constraint below is enough to stop future double rewards.
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS referrals_invited_unique
                ON referrals(invited_id)
            """)

        conn.commit()
    finally:
        conn.close()


def get_user(telegram_id, conn=None, lock=False):
    own_conn = conn is None
    if own_conn:
        conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = "SELECT * FROM users WHERE telegram_id=%s"
            if lock:
                sql += " FOR UPDATE"
            cur.execute(sql, (int(telegram_id),))
            return cur.fetchone()
    finally:
        if own_conn:
            conn.close()


def create_user(tg_user, referral_id=None):
    """Create user and, on first creation only, process referral reward."""
    telegram_id = int(tg_user["id"])
    username = str(tg_user.get("username") or "")
    first_name = str(tg_user.get("first_name") or "")

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE telegram_id=%s FOR UPDATE",
                (telegram_id,),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE users
                    SET username=%s, first_name=%s
                    WHERE telegram_id=%s
                    RETURNING *
                """, (username, first_name, telegram_id))
                user = cur.fetchone()
                conn.commit()
                return user, False

            valid_referrer = None
            if referral_id not in (None, ""):
                try:
                    candidate = int(str(referral_id).strip())
                    if candidate != telegram_id:
                        cur.execute(
                            "SELECT telegram_id FROM users WHERE telegram_id=%s",
                            (candidate,),
                        )
                        if cur.fetchone():
                            valid_referrer = candidate
                except (TypeError, ValueError):
                    valid_referrer = None

            referral_code = str(telegram_id)

            cur.execute("""
                INSERT INTO users (
                    telegram_id, username, first_name, balance,
                    clicks, referral_code, referred_by
                )
                VALUES (%s,%s,%s,%s,0,%s,%s)
                RETURNING *
            """, (
                telegram_id,
                username,
                first_name,
                NEW_USER_BONUS,
                referral_code,
                valid_referrer,
            ))
            user = cur.fetchone()

            if valid_referrer:
                # The invited user is unique, so the inviter can only be rewarded once.
                cur.execute(
                    "SELECT id FROM referrals WHERE invited_id=%s",
                    (telegram_id,),
                )
                if cur.fetchone() is None:
                    cur.execute("""
                        UPDATE users
                        SET referral_count=referral_count+1,
                            balance=balance+%s
                        WHERE telegram_id=%s
                        RETURNING referral_count
                    """, (REFERRAL_REWARD, valid_referrer))
                    referrer = cur.fetchone()

                    if referrer:
                        cur.execute("""
                            INSERT INTO referrals(inviter_id, invited_id, reward)
                            VALUES (%s,%s,%s)
                            ON CONFLICT (invited_id) DO NOTHING
                        """, (valid_referrer, telegram_id, REFERRAL_REWARD))

            conn.commit()
            return user, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def user_json(user):
    return {
        "telegram_id": int(user["telegram_id"]),
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "balance": int(user.get("balance") or 0),
        "clicks": int(user.get("clicks") or 0),
        "referral_count": int(user.get("referral_count") or 0),
        "booster_type": user.get("booster_type") or "",
        "booster_multiplier": int(user.get("booster_multiplier") or 1),
        "booster_expires_at": (
            user["booster_expires_at"].isoformat()
            if user.get("booster_expires_at") else None
        ),
    }


def request_user():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")
    if not tg_user or not tg_user.get("id"):
        return None, data
    return get_user(int(tg_user["id"])), data


# ---------------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------------

def telegram(method, payload):
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN yo'q"}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        app.logger.exception("Telegram API error")
        return {"ok": False, "description": str(exc)}


def send_start(chat_id, name):
    keyboard = {
        "inline_keyboard": [
            [{
                "text": "🚀 SHOHCOINS'ni ochish",
                "web_app": {"url": APP_URL},
            }],
            [{
                "text": "ℹ️ Bot haqida",
                "callback_data": "about",
            }],
        ]
    }

    return telegram("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"🪙 <b>SHOHCOINS</b>\n\n"
            f"Salom, <b>{name}</b>! 👋\n\n"
            f"🪙 Har bir tanga bosish: <b>+{CLICK_REWARD} SHC</b>\n"
            f"🎁 Daily Bonus: <b>+{DAILY_BONUS} SHC</b>\n"
            f"👥 Har bir referral: <b>+{REFERRAL_REWARD} SHC</b>\n"
            f"⚡ Booster: <b>x2 / x5 / x10</b>\n\n"
            "👇 Mini App'ni ochish uchun tugmani bosing."
        ),
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


def set_webhook():
    if not BOT_TOKEN or not APP_URL:
        return
    result = telegram("setWebhook", {
        "url": APP_URL + "/telegram/webhook",
        "allowed_updates": ["message", "callback_query"],
    })
    app.logger.info("Telegram webhook: %s", result)


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    try:
        message = update.get("message") or {}
        if message:
            chat = message.get("chat") or {}
            user = message.get("from") or {}
            text = str(message.get("text") or "")

            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                referral_id = None

                if len(parts) == 2:
                    payload = parts[1].strip()
                    if payload.startswith("ref_"):
                        referral_id = payload[4:]
                    else:
                        referral_id = payload

                if user.get("id"):
                    # IMPORTANT: referral is processed here because the /start
                    # deep-link parameter does not reliably reach a Mini App.
                    create_user(user, referral_id)

                send_start(
                    chat.get("id"),
                    str(user.get("first_name") or "do'st"),
                )

        callback = update.get("callback_query") or {}
        if callback:
            callback_id = callback.get("id")
            data = callback.get("data")
            chat_id = (callback.get("message") or {}).get("chat", {}).get("id")

            if data == "about":
                telegram("answerCallbackQuery", {
                    "callback_query_id": callback_id,
                })
                telegram("sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        "ℹ️ <b>SHOHCOINS</b>\n\n"
                        "🪙 Tanga bosib SHC yig'ing.\n"
                        "🎁 Har kuni Daily Bonus oling.\n"
                        "👥 Do'stlaringizni referral orqali taklif qiling.\n"
                        "⚡ SHC bilan booster sotib olib, bosish daromadini oshiring.\n"
                        "🏆 TOP 20 va Referral TOP 10 da kuch sinashing."
                    ),
                    "parse_mode": "HTML",
                })

    except Exception:
        app.logger.exception("Webhook error")

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Web / API
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/health")
def health():
    try:
        conn = db()
        conn.close()
        return jsonify({
            "status": "ok",
            "database": "ok",
            "project": "Shohcoins",
        })
    except Exception as exc:
        return jsonify({
            "status": "error",
            "database": str(exc),
            "project": "Shohcoins",
        }), 500


@app.route("/api/me", methods=["POST"])
def me():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")

    if not tg_user or not tg_user.get("id"):
        return jsonify({
            "success": False,
            "error": "Telegram user topilmadi",
        }), 400

    # Supports both:
    # 1) Telegram /start ref_ID handled by webhook
    # 2) Mini App start_param if Telegram provides it
    start_param = str(data.get("start_param") or "").strip()
    referral_id = start_param[4:] if start_param.startswith("ref_") else start_param

    try:
        user, created = create_user(tg_user, referral_id)
        return jsonify({
            "success": True,
            "new_user": created,
            "user": user_json(user),
        })
    except Exception as exc:
        app.logger.exception("me error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500


@app.route("/api/click", methods=["POST"])
def click():
    user, _ = request_user()
    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring",
        }), 400

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT balance, clicks, booster_multiplier,
                       booster_expires_at
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (user["telegram_id"],))
            row = cur.fetchone()

            if not row:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "error": "Foydalanuvchi topilmadi",
                }), 404

            now = datetime.utcnow()
            multiplier = int(row["booster_multiplier"] or 1)
            expiry = row["booster_expires_at"]

            if not expiry or expiry <= now:
                multiplier = 1
                cur.execute("""
                    UPDATE users
                    SET booster_type='',
                        booster_multiplier=1,
                        booster_expires_at=NULL
                    WHERE telegram_id=%s
                """, (user["telegram_id"],))

            earned = CLICK_REWARD * multiplier

            cur.execute("""
                UPDATE users
                SET balance=balance+%s,
                    clicks=clicks+1
                WHERE telegram_id=%s
                RETURNING balance, clicks, booster_multiplier,
                          booster_expires_at, booster_type
            """, (earned, user["telegram_id"]))
            result = cur.fetchone()

        conn.commit()

        return jsonify({
            "success": True,
            "balance": int(result["balance"]),
            "clicks": int(result["clicks"]),
            "earned": earned,
            "multiplier": int(result["booster_multiplier"] or 1),
            "booster_type": result["booster_type"] or "",
            "booster_expires_at": (
                result["booster_expires_at"].isoformat()
                if result["booster_expires_at"] else None
            ),
        })
    except Exception as exc:
        conn.rollback()
        app.logger.exception("click error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


@app.route("/api/daily", methods=["POST"])
def daily():
    user, _ = request_user()
    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring",
        }), 400

    today = date.today()
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT last_daily_bonus
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (user["telegram_id"],))
            row = cur.fetchone()

            if not row:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "error": "Foydalanuvchi topilmadi",
                }), 404

            if row["last_daily_bonus"] == today:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "error": "Bugungi bonusni oldingiz 🎁",
                })

            cur.execute("""
                UPDATE users
                SET balance=balance+%s,
                    last_daily_bonus=%s
                WHERE telegram_id=%s
                RETURNING balance
            """, (DAILY_BONUS, today, user["telegram_id"]))
            result = cur.fetchone()

        conn.commit()
        return jsonify({
            "success": True,
            "bonus": DAILY_BONUS,
            "balance": int(result["balance"]),
        })
    except Exception as exc:
        conn.rollback()
        app.logger.exception("daily error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


@app.route("/api/referral", methods=["POST"])
def referral():
    user, _ = request_user()
    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring",
        }), 400

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT first_name, username, created_at
                FROM users
                WHERE referred_by=%s
                ORDER BY created_at DESC
            """, (user["telegram_id"],))
            invited = cur.fetchall()

        link = f"https://t.me/{BOT_USERNAME}?start=ref_{user['telegram_id']}"

        return jsonify({
            "success": True,
            "count": int(user["referral_count"] or 0),
            "link": link,
            "reward": REFERRAL_REWARD,
            "invited": [
                {
                    "first_name": row["first_name"] or "",
                    "username": row["username"] or "",
                    "created_at": (
                        row["created_at"].isoformat()
                        if row["created_at"] else None
                    ),
                }
                for row in invited
            ],
        })
    except Exception as exc:
        app.logger.exception("referral error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


@app.route("/api/referral-top")
def referral_top():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT first_name, username, referral_count
                FROM users
                WHERE referral_count > 0
                ORDER BY referral_count DESC, id ASC
                LIMIT 10
            """)
            users = cur.fetchall()

        return jsonify({
            "success": True,
            "users": users,
        })
    except Exception as exc:
        app.logger.exception("referral top error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


@app.route("/api/top")
def top():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT first_name, username, balance, clicks, referral_count
                FROM users
                ORDER BY balance DESC, clicks DESC, id ASC
                LIMIT 20
            """)
            users = cur.fetchall()

        return jsonify({
            "success": True,
            "users": users,
        })
    except Exception as exc:
        app.logger.exception("top error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


@app.route("/api/boosters", methods=["POST"])
def boosters():
    return jsonify({
        "success": True,
        "boosters": BOOSTERS,
    })


@app.route("/api/buy-booster", methods=["POST"])
def buy_booster():
    user, _ = request_user()
    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring",
        }), 400

    data = request.get_json(silent=True) or {}
    booster_type = str(data.get("type") or "").strip().lower()

    if booster_type not in BOOSTERS:
        return jsonify({
            "success": False,
            "error": "Kuchaytirgich topilmadi",
        }), 400

    booster = BOOSTERS[booster_type]
    price = int(booster["price"])
    multiplier = int(booster["multiplier"])
    seconds = int(booster["seconds"])

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT balance, booster_expires_at
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (user["telegram_id"],))
            row = cur.fetchone()

            if not row:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "error": "Foydalanuvchi topilmadi",
                }), 404

            if int(row["balance"] or 0) < price:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "error": f"Balans yetarli emas. Kerak: {price} SHC",
                }), 400

            now = datetime.utcnow()
            old_expiry = row["booster_expires_at"]

            # If a booster is already active, add the new duration to the
            # remaining active time instead of deleting it.
            base = old_expiry if old_expiry and old_expiry > now else now
            new_expiry = base + timedelta(seconds=seconds)

            cur.execute("""
                UPDATE users
                SET balance=balance-%s,
                    booster_type=%s,
                    booster_multiplier=%s,
                    booster_expires_at=%s
                WHERE telegram_id=%s
                RETURNING balance, booster_type, booster_multiplier,
                          booster_expires_at
            """, (
                price,
                booster_type,
                multiplier,
                new_expiry,
                user["telegram_id"],
            ))
            result = cur.fetchone()

        conn.commit()

        return jsonify({
            "success": True,
            "balance": int(result["balance"]),
            "booster_type": result["booster_type"],
            "multiplier": int(result["booster_multiplier"]),
            "expires_at": result["booster_expires_at"].isoformat(),
        })
    except Exception as exc:
        conn.rollback()
        app.logger.exception("buy booster error")
        return jsonify({
            "success": False,
            "error": f"Server xatosi: {exc}",
        }), 500
    finally:
        conn.close()


HTML = r"""
<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>SHOHCOINS</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;min-height:100%;font-family:Arial,Helvetica,sans-serif;background:#09090d;color:#fff}
body{background:radial-gradient(circle at 50% -10%,#292430 0,#111117 42%,#08080c 100%)}
button{font:inherit}
.container{width:100%;max-width:520px;margin:auto;padding:18px 16px 40px}
.header{text-align:center;padding:8px 0 20px}
.logo{width:68px;height:68px;margin:0 auto 10px;display:grid;place-items:center;border-radius:22px;font-size:38px;background:linear-gradient(145deg,#ffe48a,#ffbf18 55%,#d98d00);box-shadow:0 10px 30px rgba(255,190,20,.22)}
.title{margin:0;font-size:28px;font-weight:900;letter-spacing:1px}
.subtitle{margin-top:6px;color:#9696a2;font-size:13px}
.card{padding:24px 20px;margin-bottom:16px;border:1px solid rgba(255,255,255,.06);border-radius:28px;background:linear-gradient(145deg,#1c1c25,#121218);box-shadow:0 14px 35px rgba(0,0,0,.25);text-align:center}
.balance-title{color:#a7a7b1;font-size:15px}
.balance{margin:8px 0;color:#ffc21c;font-size:64px;line-height:1;font-weight:900}
.currency{color:#d2d2d8;font-size:16px;font-weight:700}
.click{width:100%;min-height:170px;border:0;border-radius:30px;color:#111;cursor:pointer;font-size:28px;font-weight:900;background:linear-gradient(145deg,#ffd34d,#ffb700);box-shadow:0 15px 35px rgba(255,184,0,.2);transition:transform .08s}
.click:active{transform:scale(.97)}
.click:disabled{opacity:.75}
.coin{display:block;margin-bottom:5px;font-size:52px}
.booster-active{margin:12px 0;padding:12px;border-radius:15px;text-align:center;color:#111;font-weight:900;background:linear-gradient(145deg,#ffd34d,#ffb700)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}
.stat{padding:20px 12px;text-align:center;border:1px solid rgba(255,255,255,.05);border-radius:22px;background:#181820}
.number{font-size:30px;font-weight:900}.label{margin-top:6px;color:#9696a1;font-size:13px}
.buttons{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.action{min-height:58px;padding:14px 8px;border:1px solid rgba(255,255,255,.05);border-radius:18px;color:#fff;cursor:pointer;font-size:15px;font-weight:800;background:linear-gradient(145deg,#24242e,#1b1b23)}
.action:active{transform:scale(.97)}
.action.primary{color:#111;background:linear-gradient(145deg,#ffd34d,#ffb700)}
.message{min-height:24px;margin:15px 0 5px;text-align:center;color:#ffcc33;font-size:14px;font-weight:700}
.panel{display:none;margin-top:16px;padding:20px;border:1px solid rgba(255,255,255,.06);border-radius:24px;background:#181820}
.panel h2{margin:0 0 12px;font-size:20px}
.ref-description{color:#aaaab5;line-height:1.5;font-size:14px}
.ref-link{margin:14px 0;padding:13px;border-radius:14px;color:#e7e7ed;word-break:break-all;background:#25252f;font-size:13px}
.copy-button,.share-button{width:100%;padding:14px;border:0;border-radius:15px;color:#111;background:#ffbf18;font-size:15px;font-weight:900}
.share-button{margin-top:9px}
.ref-count{margin-top:12px;color:#ffc21c;font-weight:800}
.player{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:8px 0;padding:14px;border-radius:15px;background:#20202a}
.player-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.player-balance{flex-shrink:0;color:#ffc21c;font-weight:900}
.small{color:#92929d;font-size:12px}
.booster{margin:10px 0;padding:15px;border-radius:17px;background:#20202a}
.booster-title{font-size:17px;font-weight:900}
.booster-info{margin:6px 0 10px;color:#aaaab5;font-size:13px}
.booster-buy{width:100%;padding:12px;border:0;border-radius:13px;background:#ffbf18;color:#111;font-weight:900}
.loading{padding:35px 10px;text-align:center;color:#aaa}.error{padding:20px;text-align:center;border-radius:18px;color:#ff8e8e;background:#241719}
.hidden{display:none!important}
@media(max-width:360px){.container{padding-left:12px;padding-right:12px}.balance{font-size:55px}.click{min-height:150px}}
</style>
</head>
<body>
<div class="container">
<header class="header">
<div class="logo">🪙</div>
<h1 class="title">SHOHCOINS</h1>
<div class="subtitle">SHC • Digital Rewards</div>
</header>

<div id="loading" class="loading">Telegram aniqlanmoqda...</div>

<div id="app" class="hidden">
<div class="card">
<div class="balance-title">Balansingiz</div>
<div id="balance" class="balance">0</div>
<div class="currency">SHC</div>
</div>

<button id="clickButton" class="click" onclick="clickCoin()">
<span class="coin">🪙</span><span id="clickText">+1 SHC</span>
</button>

<div id="activeBooster" class="booster-active hidden"></div>

<div class="stats">
<div class="stat"><div id="clicks" class="number">0</div><div class="label">Kliklar</div></div>
<div class="stat"><div id="refs" class="number">0</div><div class="label">Referral</div></div>
</div>

<div class="buttons">
<button class="action primary" onclick="daily()">🎁 Daily Bonus</button>
<button class="action" onclick="showReferral()">👥 Referral</button>
<button class="action" onclick="showTop()">🏆 TOP 20</button>
<button class="action" onclick="showBoosters()">⚡ Booster</button>
</div>

<div id="message" class="message"></div>

<div id="refBox" class="panel">
<h2>👥 Referral</h2>
<div class="ref-description">Do‘stlaringizni taklif qiling. Har bir yangi referral uchun <b>100 SHC</b> oling.</div>
<div id="refCount" class="ref-count">Referral: 0</div>
<div id="refLink" class="ref-link"></div>
<button class="copy-button" onclick="copyReferral()">📋 Nusxalash</button>
<button class="share-button" onclick="shareReferral()">📤 Ulashish</button>
<h3>👥 Siz qo‘shgan odamlar</h3>
<div id="invitedList"></div>
<h3>🏆 Referral TOP 10</h3>
<div id="refTopList"></div>
</div>

<div id="top" class="panel">
<h2>🏆 TOP 20</h2>
<div id="topList"></div>
</div>

<div id="boosters" class="panel">
<h2>⚡ Kuchaytirgichlar</h2>
<div class="small">Booster sotib oling va tanga bosganda ko‘proq SHC oling.</div>
<div id="boosterList"></div>
</div>
</div>
</div>

<script>
const tg=window.Telegram.WebApp;
tg.ready();
tg.expand();

let tgUser=null;
let referralLink="";

function msg(text){document.getElementById("message").textContent=text}

async function api(url,options={}){
    options.headers={"Content-Type":"application/json",...(options.headers||{})};
    const res=await fetch(url,options);
    return res;
}

function updateUser(data){
    document.getElementById("balance").textContent=data.balance;
    document.getElementById("clicks").textContent=data.clicks;
    document.getElementById("refs").textContent=data.referral_count;
    updateBooster(data);
}

function updateBooster(data){
    const box=document.getElementById("activeBooster");
    const text=document.getElementById("clickText");
    const mult=Number(data.booster_multiplier||1);
    const expiry=data.booster_expires_at;

    if(expiry && mult>1 && new Date(expiry).getTime()>Date.now()){
        box.classList.remove("hidden");
        box.textContent="⚡ Aktiv booster: x"+mult;
        text.textContent="+"+(1*mult)+" SHC";
    }else{
        box.classList.add("hidden");
        text.textContent="+1 SHC";
    }
}

async function start(){
    try{
        tgUser=tg.initDataUnsafe.user;
        if(!tgUser){
            document.getElementById("loading").innerHTML='<div class="error">❌ Telegram bot ichidan oching.</div>';
            return;
        }

        const startParam=tg.initDataUnsafe.start_param||"";
        const res=await api("/api/me",{
            method:"POST",
            body:JSON.stringify({user:tgUser,start_param:startParam})
        });
        const data=await res.json();

        if(!data.success){
            document.getElementById("loading").innerHTML='<div class="error">❌ '+escapeHtml(data.error||"Xatolik")+'</div>';
            return;
        }

        document.getElementById("loading").classList.add("hidden");
        document.getElementById("app").classList.remove("hidden");
        updateUser(data.user);

        if(data.new_user) msg("🎉 Xush kelibsiz! +100 SHC");
    }catch(e){
        console.error(e);
        document.getElementById("loading").innerHTML='<div class="error">❌ Server bilan ulanishda xatolik.</div>';
    }
}

async function clickCoin(){
    const btn=document.getElementById("clickButton");
    btn.disabled=true;
    try{
        const res=await api("/api/click",{
            method:"POST",
            body:JSON.stringify({user:tgUser})
        });
        const data=await res.json();
        if(!data.success){msg(data.error||"Xatolik");return}

        document.getElementById("balance").textContent=data.balance;
        document.getElementById("clicks").textContent=data.clicks;
        updateBooster(data);

        if(tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    }catch(e){
        msg("❌ Xatolik yuz berdi");
    }finally{
        btn.disabled=false;
    }
}

async function daily(){
    try{
        const res=await api("/api/daily",{method:"POST",body:JSON.stringify({user:tgUser})});
        const data=await res.json();
        if(!data.success){msg(data.error||"Xatolik");return}
        document.getElementById("balance").textContent=data.balance;
        msg("🎁 +"+data.bonus+" SHC olindi!");
    }catch(e){msg("❌ Xatolik yuz berdi")}
}

function hidePanels(){
    document.getElementById("refBox").style.display="none";
    document.getElementById("top").style.display="none";
    document.getElementById("boosters").style.display="none";
}

async function showReferral(){
    hidePanels();
    document.getElementById("refBox").style.display="block";

    document.getElementById("invitedList").innerHTML='<div class="loading">Yuklanmoqda...</div>';
    document.getElementById("refTopList").innerHTML='<div class="loading">Yuklanmoqda...</div>';

    try{
        const res=await api("/api/referral",{
            method:"POST",
            body:JSON.stringify({user:tgUser})
        });
        const data=await res.json();

        if(!data.success){msg(data.error||"Xatolik");return}

        referralLink=data.link;
        document.getElementById("refCount").textContent="Referral: "+data.count;
        document.getElementById("refLink").textContent=data.link;

        if(!data.invited.length){
            document.getElementById("invitedList").innerHTML='<div class="loading">Hali hech kim qo‘shilmagan.</div>';
        }else{
            document.getElementById("invitedList").innerHTML=data.invited.map((u)=>{
                const name=escapeHtml(u.first_name||u.username||"User");
                const username=u.username ? "@"+escapeHtml(u.username) : "";
                return '<div class="player"><div><div class="player-name">'+name+'</div><div class="small">'+username+'</div></div><div class="small">qo‘shildi</div></div>';
            }).join("");
        }

        const topRes=await fetch("/api/referral-top");
        const topData=await topRes.json();

        if(!topData.success || !topData.users.length){
            document.getElementById("refTopList").innerHTML='<div class="loading">Hali TOP 10 mavjud emas.</div>';
        }else{
            document.getElementById("refTopList").innerHTML=topData.users.map((u,i)=>{
                const name=escapeHtml(u.first_name||u.username||"User");
                return '<div class="player"><div class="player-name">'+(i+1)+'. '+name+'</div><div class="player-balance">'+Number(u.referral_count||0)+' ta</div></div>';
            }).join("");
        }
    }catch(e){
        msg("❌ Referralni olishda xatolik");
    }
}

async function copyReferral(){
    if(!referralLink) await showReferral();
    if(!referralLink)return;
    try{
        await navigator.clipboard.writeText(referralLink);
        msg("✅ Link nusxalandi!");
    }catch(e){msg("Linkni qo‘lda nusxalang")}
}

async function shareReferral(){
    if(!referralLink) await showReferral();
    if(!referralLink)return;
    const text="🪙 SHOHCOINS ga qo‘shiling! Har kuni SHC ishlang.";
    const shareUrl="https://t.me/share/url?url="+encodeURIComponent(referralLink)+"&text="+encodeURIComponent(text);
    if(tg.openTelegramLink) tg.openTelegramLink(shareUrl);
    else window.open(shareUrl,"_blank");
}

async function showTop(){
    hidePanels();
    const panel=document.getElementById("top");
    panel.style.display="block";
    const list=document.getElementById("topList");
    list.innerHTML="<div class='loading'>Yuklanmoqda...</div>";

    try{
        const res=await fetch("/api/top");
        const data=await res.json();

        if(!data.success){
            list.innerHTML="<div class='error'>Xatolik</div>";
            return;
        }

        if(!data.users.length){
            list.innerHTML="<div class='loading'>Hozircha foydalanuvchilar yo‘q.</div>";
            return;
        }

        list.innerHTML=data.users.map((u,i)=>{
            const name=escapeHtml(u.first_name||u.username||"User");
            return '<div class="player"><div class="player-name">'+(i+1)+'. '+name+'</div><div class="player-balance">'+Number(u.balance||0)+' SHC</div></div>';
        }).join("");
    }catch(e){
        list.innerHTML="<div class='error'>TOP yuklanmadi.</div>";
    }
}

async function showBoosters(){
    hidePanels();
    const panel=document.getElementById("boosters");
    panel.style.display="block";
    const list=document.getElementById("boosterList");
    list.innerHTML='<div class="loading">Yuklanmoqda...</div>';

    try{
        const res=await api("/api/boosters",{method:"POST"});
        const data=await res.json();

        if(!data.success){
            list.innerHTML='<div class="error">Boosterlar yuklanmadi.</div>';
            return;
        }

        list.innerHTML=Object.entries(data.boosters).map(([type,b])=>{
            return '<div class="booster">'+
                '<div class="booster-title">⚡ '+escapeHtml(type.toUpperCase())+' — '+Number(b.multiplier)+'x</div>'+
                '<div class="booster-info">'+Number(b.seconds)+' soniya • '+Number(b.price)+' SHC</div>'+
                '<button class="booster-buy" onclick="buyBooster(\''+type+'\')">Sotib olish</button>'+
                '</div>';
        }).join("");
    }catch(e){
        list.innerHTML='<div class="error">Boosterlar yuklanmadi.</div>';
    }
}

async function buyBooster(type){
    try{
        const res=await api("/api/buy-booster",{
            method:"POST",
            body:JSON.stringify({user:tgUser,type:type})
        });
        const data=await res.json();

        if(!data.success){
            msg(data.error||"Booster olinmadi");
            return;
        }

        document.getElementById("balance").textContent=data.balance;
        updateBooster({
            booster_multiplier:data.multiplier,
            booster_expires_at:data.expires_at
        });
        msg("⚡ x"+data.multiplier+" booster ishga tushdi!");
    }catch(e){
        msg("❌ Booster sotib olishda xatolik");
    }
}

function escapeHtml(value){
    return String(value).replace(/[&<>"']/g,c=>({
        "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"
    }[c]));
}

start();
</script>
</body>
</html>
"""


def startup():
    init_db()
    if BOT_TOKEN and APP_URL:
        set_webhook()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    startup()
    app.run(host="0.0.0.0", port=port)
