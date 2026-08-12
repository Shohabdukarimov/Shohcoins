import os
import json
import urllib.request
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Shoh_coinbot").lstrip("@")
APP_URL = os.getenv("APP_URL", "https://shohcoins.onrender.com").rstrip("/")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10
REFERRAL_REWARD = 100


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL topilmadi")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance BIGINT DEFAULT 0,
            clicks BIGINT DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by BIGINT,
            referral_count INTEGER DEFAULT 0,
            bonus_5_given BOOLEAN DEFAULT FALSE,
            bonus_10_given BOOLEAN DEFAULT FALSE,
            last_daily_bonus DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for q in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_5_given BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_10_given BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT",
    ]:
        cur.execute(q)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            inviter_id BIGINT NOT NULL,
            invited_id BIGINT UNIQUE NOT NULL,
            reward BIGINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_user(tg_id):
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE telegram_id=%s", (int(tg_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_user(tg_user, referral_id=None):
    tg_id = int(tg_user["id"])
    username = tg_user.get("username") or ""
    first_name = tg_user.get("first_name") or "Foydalanuvchi"

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE telegram_id=%s FOR UPDATE", (tg_id,))
    old = cur.fetchone()

    if old:
        cur.execute(
            "UPDATE users SET username=%s, first_name=%s WHERE telegram_id=%s",
            (username, first_name, tg_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        old["username"] = username
        old["first_name"] = first_name
        return old, False

    referrer = None
    if referral_id:
        try:
            candidate = int(str(referral_id))
            if candidate != tg_id:
                cur.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id=%s",
                    (candidate,),
                )
                if cur.fetchone():
                    referrer = candidate
        except (ValueError, TypeError):
            pass

    cur.execute("""
        INSERT INTO users
        (telegram_id, username, first_name, balance, clicks,
         referral_code, referred_by)
        VALUES (%s,%s,%s,%s,0,%s,%s)
        RETURNING *
    """, (tg_id, username, first_name, NEW_USER_BONUS, str(tg_id), referrer))
    user = cur.fetchone()

    if referrer:
        cur.execute(
            "SELECT id FROM referrals WHERE invited_id=%s",
            (tg_id,),
        )
        if not cur.fetchone():
            cur.execute(
                "SELECT referral_count FROM users WHERE telegram_id=%s FOR UPDATE",
                (referrer,),
            )
            r = cur.fetchone()
            if r:
                count = (r["referral_count"] or 0) + 1
                cur.execute("""
                    UPDATE users
                    SET referral_count=%s, balance=balance+%s
                    WHERE telegram_id=%s
                """, (count, REFERRAL_REWARD, referrer))
                cur.execute("""
                    INSERT INTO referrals (inviter_id, invited_id, reward)
                    VALUES (%s,%s,%s)
                    ON CONFLICT (invited_id) DO NOTHING
                """, (referrer, tg_id, REFERRAL_REWARD))

    conn.commit()
    cur.close()
    conn.close()
    return user, True


def telegram(method, data):
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN yo'q"}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print("Telegram:", e)
        return {"ok": False, "description": str(e)}


def set_webhook():
    if BOT_TOKEN:
        result = telegram("setWebhook", {
            "url": APP_URL + "/telegram/webhook",
            "allowed_updates": ["message", "callback_query"],
        })
        print("Webhook:", result)


def send_start(chat_id, name):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🚀 SHOHCOINS'ni ochish",
              "web_app": {"url": APP_URL}}],
            [{"text": "ℹ️ Bot haqida", "callback_data": "about"}],
        ]
    }
    return telegram("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"🪙 <b>SHOHCOINS</b>\n\n"
            f"Salom, <b>{name}</b>! 👋\n\n"
            f"🖱 Har bir klik: <b>+{CLICK_REWARD} SHC</b>\n"
            f"🎁 Daily Bonus: <b>+{DAILY_BONUS} SHC</b>\n"
            f"👥 Har bir referral: <b>+{REFERRAL_REWARD} SHC</b>\n\n"
            "👇 Mini App'ni ochish uchun tugmani bosing."
        ),
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


def send_about(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🚀 SHOHCOINS'ni ochish",
              "web_app": {"url": APP_URL}}]
        ]
    }
    return telegram("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🪙 <b>SHOHCOINS haqida</b>\n\n"
            "SHOHCOINS — Telegram Mini App orqali SHC yig'ish tizimi.\n\n"
            "🖱 Klik — SHC\n"
            "🎁 Daily Bonus\n"
            "👥 Referral\n"
            "🏆 Referral TOP 10 va umumiy TOP 20"
        ),
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


@app.route("/telegram/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    text = (msg.get("text") or "").strip()

    if chat.get("id") and text.startswith("/start"):
        parts = text.split(maxsplit=1)
        param = parts[1] if len(parts) > 1 else ""
        ref = param[4:] if param.startswith("ref_") else (param or None)

        try:
            create_user(sender, ref)
        except Exception as e:
            print("User:", e)

        send_start(chat["id"], sender.get("first_name") or "Foydalanuvchi")

    elif chat.get("id") and text == "/about":
        send_about(chat["id"])

    callback = update.get("callback_query") or {}
    if callback:
        if callback.get("id"):
            telegram("answerCallbackQuery", {
                "callback_query_id": callback["id"]
            })
        if callback.get("data") == "about":
            cmsg = callback.get("message") or {}
            cchat = (cmsg.get("chat") or {}).get("id")
            if cchat:
                send_about(cchat)

    return jsonify({"ok": True})


@app.route("/telegram/set-webhook")
def manual_webhook():
    set_webhook()
    return jsonify({"success": True, "url": APP_URL + "/telegram/webhook"})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "project": "Shohcoins"})


# =========================================================
# API
# =========================================================

def request_user():
    data = request.get_json(silent=True) or {}
    u = data.get("user")
    if not u or not u.get("id"):
        return None
    return get_user(u["id"])


@app.route("/api/me", methods=["POST"])
def me():
    data = request.get_json(silent=True) or {}
    u = data.get("user")
    if not u or not u.get("id"):
        return jsonify({"success": False, "error": "Telegram user topilmadi"}), 400

    param = str(data.get("start_param") or "")
    ref = param[4:] if param.startswith("ref_") else (param or None)

    try:
        user, created = create_user(u, ref)
    except Exception as e:
        print("ME:", e)
        return jsonify({"success": False, "error": "Database xatosi"}), 500

    return jsonify({
        "success": True,
        "new_user": created,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "clicks": user["clicks"],
            "referral_count": user["referral_count"] or 0,
        },
    })


@app.route("/api/click", methods=["POST"])
def click():
    user = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        UPDATE users
        SET balance=balance+%s, clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
    """, (CLICK_REWARD, user["telegram_id"]))
    r = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "balance": r["balance"], "clicks": r["clicks"]})


@app.route("/api/daily", methods=["POST"])
def daily():
    user = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    today = date.today()
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT last_daily_bonus FROM users WHERE telegram_id=%s FOR UPDATE",
        (user["telegram_id"],),
    )
    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"success": False, "error": "Bugungi bonusni oldingiz 🎁"})

    cur.execute("""
        UPDATE users
        SET balance=balance+%s, last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (DAILY_BONUS, today, user["telegram_id"]))
    r = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "bonus": DAILY_BONUS, "balance": r["balance"]})


@app.route("/api/referral", methods=["POST"])
def referral():
    user = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT u.first_name, u.username, r.reward
        FROM referrals r
        JOIN users u ON u.telegram_id=r.invited_id
        WHERE r.inviter_id=%s
        ORDER BY r.created_at DESC
    """, (user["telegram_id"],))
    invited = cur.fetchall()

    cur.execute("""
        SELECT first_name, username, referral_count
        FROM users
        ORDER BY referral_count DESC, balance DESC
        LIMIT 10
    """)
    top10 = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "count": user["referral_count"] or 0,
        "link": f"https://t.me/{BOT_USERNAME}?start=ref_{user['telegram_id']}",
        "invited": invited,
        "top10": top10,
    })


@app.route("/api/top")
def top():
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT first_name, username, balance, clicks, referral_count
        FROM users
        ORDER BY balance DESC, clicks DESC
        LIMIT 20
    """)
    users = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({"success": True, "users": users})


# Your existing Mini App HTML can stay in app.py.
# This fallback is intentionally simple so the bot/webhook part is reliable.
HTML = r"""
<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SHOHCOINS</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
body{margin:0;background:#0b0b10;color:white;font-family:Arial;text-align:center}
main{max-width:500px;margin:auto;padding:25px 16px}
h1{font-size:32px}.balance{font-size:60px;color:#ffc21c;font-weight:900;margin:25px}
button{width:100%;padding:20px;margin:7px 0;border:0;border-radius:20px;font-size:18px;font-weight:800;background:#ffbf18}
.dark{background:#20202a;color:white}.box{padding:20px;margin:15px 0;border-radius:25px;background:#181820}
</style>
</head>
<body>
<main>
<h1>🪙 SHOHCOINS</h1>
<div class="box">Balansingiz<div class="balance" id="balance">0</div>SHC</div>
<button onclick="clickCoin()">🪙 +1 SHC</button>
<button class="dark" onclick="daily()">🎁 Daily Bonus</button>
<button class="dark" onclick="ref()">👥 Referral</button>
<button class="dark" onclick="top10()">🏆 TOP 20</button>
<div id="out"></div>
</main>
<script>
const tg=window.Telegram.WebApp;tg.ready();tg.expand();
const user=tg.initDataUnsafe.user;
const out=document.getElementById("out");
async function post(url){return (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user,start_param:tg.initDataUnsafe.start_param||""})})).json()}
async function start(){if(!user){out.innerText="Telegram bot ichidan oching.";return}let d=await post("/api/me");if(d.success)document.getElementById("balance").innerText=d.user.balance}
async function clickCoin(){let d=await post("/api/click");if(d.success)document.getElementById("balance").innerText=d.balance}
async function daily(){let d=await post("/api/daily");out.innerText=d.success?"🎁 +"+d.bonus+" SHC":d.error;if(d.balance)document.getElementById("balance").innerText=d.balance}
async function ref(){let d=await post("/api/referral");if(!d.success)return;out.innerHTML="<h3>Referral: "+d.count+"</h3>"+d.invited.map(x=>"<p>👤 "+x.first_name+" — +"+x.reward+" SHC</p>").join("")+"<h3>🏆 TOP 10</h3>"+d.top10.map((x,i)=>"<p>#"+(i+1)+" "+x.first_name+" — "+x.referral_count+" ta</p>").join("")}
async function top10(){let d=await (await fetch("/api/top")).json();out.innerHTML=d.users.map((x,i)=>"<p>#"+(i+1)+" "+x.first_name+" — "+x.balance+" SHC</p>").join("")}
start();
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


try:
    init_db()
except Exception as e:
    print("DB init:", e)

try:
    set_webhook()
except Exception as e:
    print("Webhook init:", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
