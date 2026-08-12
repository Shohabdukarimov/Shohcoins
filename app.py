import os
from datetime import date, datetime, timedelta

from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor


app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")
ADMIN_ID = os.getenv("ADMIN_ID", "")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10

# Referral: har yangi taklif uchun 100 SHC.
REFERRAL_REWARD = 100

# Kuchaytirgichlar.
BOOSTERS = {
    "x2": {"multiplier": 2, "seconds": 30, "price": 50},
    "x5": {"multiplier": 5, "seconds": 30, "price": 150},
    "x10": {"multiplier": 10, "seconds": 30, "price": 500},
}


# =========================================================
# DATABASE
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable topilmadi")
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
            booster_type TEXT DEFAULT '',
            booster_multiplier INTEGER DEFAULT 1,
            booster_expires_at TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS bonus_5_given BOOLEAN DEFAULT FALSE
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS bonus_10_given BOOLEAN DEFAULT FALSE
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS referred_by BIGINT
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS booster_type TEXT DEFAULT ''
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS booster_multiplier INTEGER DEFAULT 1
    """)
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS booster_expires_at TIMESTAMP NULL
    """)

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


# =========================================================
# USER
# =========================================================

def get_user(telegram_id):
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT *
        FROM users
        WHERE telegram_id=%s
    """, (telegram_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user


def create_user(tg_user, referral_id=None):
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username", "") or ""
    first_name = tg_user.get("first_name", "") or ""

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT *
        FROM users
        WHERE telegram_id=%s
    """, (telegram_id,))
    existing = cur.fetchone()

    if existing:
        # Telegramdagi yangi ism/username o'zgargan bo'lsa yangilaymiz.
        cur.execute("""
            UPDATE users
            SET username=%s, first_name=%s
            WHERE telegram_id=%s
        """, (username, first_name, telegram_id))
        conn.commit()

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id=%s
        """, (telegram_id,))
        existing = cur.fetchone()

        cur.close()
        conn.close()
        return existing, False

    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute("""
                    SELECT telegram_id
                    FROM users
                    WHERE telegram_id=%s
                """, (referral_id,))
                referrer = cur.fetchone()

                if referrer:
                    valid_referrer = referral_id
        except (ValueError, TypeError):
            valid_referrer = None

    referral_code = str(telegram_id)

    cur.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            clicks,
            referral_code,
            referred_by
        )
        VALUES (%s, %s, %s, %s, 0, %s, %s)
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

    # Yangi foydalanuvchi referral orqali kelgan bo'lsa,
    # taklif qilgan odamga 100 SHC beriladi.
    if valid_referrer:
        cur.execute("""
            SELECT id
            FROM referrals
            WHERE invited_id=%s
        """, (telegram_id,))
        already = cur.fetchone()

        if not already:
            cur.execute("""
                SELECT referral_count
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (valid_referrer,))
            referrer = cur.fetchone()

            if referrer:
                count = (referrer["referral_count"] or 0) + 1

                cur.execute("""
                    UPDATE users
                    SET
                        referral_count=%s,
                        balance=balance+%s
                    WHERE telegram_id=%s
                """, (count, REFERRAL_REWARD, valid_referrer))

                cur.execute("""
                    INSERT INTO referrals (
                        inviter_id,
                        invited_id,
                        reward
                    )
                    VALUES (%s, %s, %s)
                """, (
                    valid_referrer,
                    telegram_id,
                    REFERRAL_REWARD,
                ))

    conn.commit()

    cur.close()
    conn.close()
    return user, True


def get_current_user():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")

    if not tg_user:
        return None

    try:
        return get_user(int(tg_user["id"]))
    except (ValueError, TypeError):
        return None


def user_public(user):
    now = datetime.utcnow()
    booster_active = (
        user.get("booster_expires_at") is not None
        and user["booster_expires_at"] > now
        and (user.get("booster_multiplier") or 1) > 1
    )

    return {
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "first_name": user["first_name"],
        "balance": user["balance"],
        "clicks": user["clicks"],
        "referral_count": user["referral_count"] or 0,
        "booster_multiplier": user["booster_multiplier"] if booster_active else 1,
        "booster_type": user["booster_type"] if booster_active else "",
        "booster_expires_at": (
            user["booster_expires_at"].isoformat()
            if booster_active else None
        ),
    }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# =========================================================
# ME
# =========================================================

@app.route("/api/me", methods=["POST"])
def me():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")

    if not tg_user:
        return jsonify({
            "success": False,
            "error": "Telegram user topilmadi"
        }), 400

    start_param = data.get("start_param", "")
    referral_id = None

    if start_param:
        if str(start_param).startswith("ref_"):
            referral_id = str(start_param)[4:]
        else:
            referral_id = str(start_param)

    try:
        user, created = create_user(tg_user, referral_id)
        return jsonify({
            "success": True,
            "new_user": created,
            "user": user_public(user),
        })
    except Exception as e:
        app.logger.exception("ME error")
        return jsonify({
            "success": False,
            "error": "Server xatosi: " + str(e)
        }), 500


# =========================================================
# CLICK
# =========================================================

@app.route("/api/click", methods=["POST"])
def click():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    now = datetime.utcnow()

    cur.execute("""
        SELECT
            balance,
            clicks,
            booster_multiplier,
            booster_expires_at,
            booster_type
        FROM users
        WHERE telegram_id=%s
        FOR UPDATE
    """, (user["telegram_id"],))

    row = cur.fetchone()

    multiplier = 1

    if (
        row["booster_expires_at"] is not None
        and row["booster_expires_at"] > now
    ):
        multiplier = max(1, int(row["booster_multiplier"] or 1))

    reward = CLICK_REWARD * multiplier

    cur.execute("""
        UPDATE users
        SET
            balance=balance+%s,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks, booster_multiplier, booster_expires_at
    """, (reward, user["telegram_id"]))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    active = (
        result["booster_expires_at"] is not None
        and result["booster_expires_at"] > now
        and (result["booster_multiplier"] or 1) > 1
    )

    return jsonify({
        "success": True,
        "reward": reward,
        "balance": result["balance"],
        "clicks": result["clicks"],
        "multiplier": result["booster_multiplier"] if active else 1,
        "expires_at": (
            result["booster_expires_at"].isoformat()
            if active else None
        ),
    })


# =========================================================
# DAILY BONUS
# =========================================================

@app.route("/api/daily", methods=["POST"])
def daily():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    today = date.today()

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT last_daily_bonus
        FROM users
        WHERE telegram_id=%s
        FOR UPDATE
    """, (user["telegram_id"],))

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz 🎁"
        })

    cur.execute("""
        UPDATE users
        SET
            balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (DAILY_BONUS, today, user["telegram_id"]))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "bonus": DAILY_BONUS,
        "balance": result["balance"],
    })


# =========================================================
# REFERRAL
# =========================================================

@app.route("/api/referral", methods=["POST"])
def referral():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user['telegram_id']}"
    )

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Shu foydalanuvchi taklif qilganlar.
    cur.execute("""
        SELECT
            first_name,
            username,
            created_at
        FROM users
        WHERE referred_by=%s
        ORDER BY created_at DESC
    """, (user["telegram_id"],))

    invited = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "count": user["referral_count"] or 0,
        "link": link,
        "reward": REFERRAL_REWARD,
        "invited": invited,
    })



# =========================================================
# REFERRAL DETAILS
# =========================================================

@app.route("/api/referral/details", methods=["POST"])
def referral_details():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            first_name,
            username,
            created_at
        FROM users
        WHERE referred_by=%s
        ORDER BY created_at DESC
    """, (user["telegram_id"],))
    invited = cur.fetchall()

    cur.execute("""
        SELECT
            first_name,
            username,
            referral_count
        FROM users
        WHERE referral_count > 0
        ORDER BY referral_count DESC, id ASC
        LIMIT 10
    """)
    top10 = cur.fetchall()

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user['telegram_id']}"
    )

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "count": user["referral_count"] or 0,
        "link": link,
        "invited": invited,
        "top10": top10
    })


# =========================================================
# REFERRAL TOP 10
# =========================================================

@app.route("/api/referral-top")
def referral_top():
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            first_name,
            username,
            referral_count
        FROM users
        WHERE referral_count > 0
        ORDER BY referral_count DESC, id ASC
        LIMIT 10
    """)

    users = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "users": users,
    })


# =========================================================
# TOP 20 BALANCE
# =========================================================

@app.route("/api/top")
def top():
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            first_name,
            username,
            balance,
            clicks,
            referral_count
        FROM users
        ORDER BY balance DESC, id ASC
        LIMIT 20
    """)

    users = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "users": users,
    })


# =========================================================
# BOOSTERS
# =========================================================

@app.route("/api/boosters", methods=["POST"])
def boosters():
    return jsonify({
        "success": True,
        "boosters": BOOSTERS
    })


@app.route("/api/buy-booster", methods=["POST"])
def buy_booster():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    data = request.get_json(silent=True) or {}
    booster_type = str(data.get("type", ""))

    if booster_type not in BOOSTERS:
        return jsonify({
            "success": False,
            "error": "Kuchaytirgich topilmadi"
        }), 400

    booster = BOOSTERS[booster_type]
    price = booster["price"]
    multiplier = booster["multiplier"]
    seconds = booster["seconds"]

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            balance,
            booster_multiplier,
            booster_expires_at
        FROM users
        WHERE telegram_id=%s
        FOR UPDATE
    """, (user["telegram_id"],))

    row = cur.fetchone()

    if row["balance"] < price:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({
            "success": False,
            "error": f"Balans yetarli emas. Kerak: {price} SHC"
        }), 400

    now = datetime.utcnow()

    # Faol booster ustiga yana booster olinadigan bo'lsa,
    # yangi vaqt qolgan vaqtga qo'shiladi.
    current_expiry = row["booster_expires_at"]
    base_time = current_expiry if (
        current_expiry and current_expiry > now
    ) else now

    new_expiry = base_time + timedelta(seconds=seconds)

    # Yuqoriroq multiplier ustun bo'ladi.
    new_multiplier = max(
        multiplier,
        int(row["booster_multiplier"] or 1)
        if current_expiry and current_expiry > now else 1
    )

    cur.execute("""
        UPDATE users
        SET
            balance=balance-%s,
            booster_type=%s,
            booster_multiplier=%s,
            booster_expires_at=%s
        WHERE telegram_id=%s
        RETURNING balance, booster_multiplier, booster_expires_at
    """, (
        price,
        booster_type,
        new_multiplier,
        new_expiry,
        user["telegram_id"],
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "price": price,
        "balance": result["balance"],
        "multiplier": result["booster_multiplier"],
        "expires_at": result["booster_expires_at"].isoformat(),
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "project": "Shohcoins"
    })


# =========================================================
# HTML
# =========================================================

HTML = r"""
<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>SHOHCОINS</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>
* {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

html, body {
    margin: 0;
    padding: 0;
    min-height: 100%;
}

body {
    min-height: 100vh;
    color: #fff;
    font-family: Arial, Helvetica, sans-serif;
    background:
        radial-gradient(circle at 50% -10%, #292733 0%, #111116 45%, #08080c 100%);
}

button {
    font-family: inherit;
}

.container {
    width: 100%;
    max-width: 520px;
    margin: 0 auto;
    padding: 22px 16px 40px;
}

.header {
    text-align: center;
    padding: 8px 0 20px;
}

.logo {
    width: 70px;
    height: 70px;
    margin: 0 auto 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 22px;
    font-size: 42px;
    background: linear-gradient(145deg, #ffe48a, #ffbf18 55%, #d98d00);
    box-shadow: 0 10px 30px rgba(255, 190, 20, .25);
}

.title {
    margin: 0;
    font-size: 29px;
    font-weight: 900;
    letter-spacing: 1px;
}

.subtitle {
    margin-top: 6px;
    color: #9999a5;
    font-size: 13px;
}

.card {
    position: relative;
    overflow: hidden;
    padding: 25px 20px;
    margin-bottom: 16px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 28px;
    background: linear-gradient(145deg, #1c1c25, #121218);
    box-shadow: 0 14px 35px rgba(0,0,0,.25);
}

.balance-title {
    text-align: center;
    color: #a7a7b1;
    font-size: 15px;
}

.balance {
    margin: 7px 0;
    text-align: center;
    color: #ffc21c;
    font-size: 64px;
    line-height: 1;
    font-weight: 900;
}

.currency {
    text-align: center;
    color: #d2d2d8;
    font-size: 16px;
    font-weight: 700;
}

.coin-button {
    position: relative;
    width: 100%;
    min-height: 220px;
    margin-bottom: 16px;
    border: 0;
    border-radius: 34px;
    color: #111;
    cursor: pointer;
    background: linear-gradient(145deg, #ffd84d, #ffb700);
    box-shadow: 0 18px 40px rgba(255,184,0,.22);
    transition: transform .10s ease;
    overflow: hidden;
}

.coin-button:active {
    transform: scale(.96);
}

.coin-image {
    display: block;
    font-size: 82px;
    line-height: 1;
    margin-bottom: 8px;
    filter: drop-shadow(0 8px 8px rgba(0,0,0,.20));
}

.coin-text {
    display: block;
    font-size: 28px;
    font-weight: 900;
}

.plus {
    position: absolute;
    pointer-events: none;
    font-size: 28px;
    font-weight: 900;
    animation: floatUp .7s ease-out forwards;
}

@keyframes floatUp {
    0% { opacity: 1; transform: translateY(0) scale(1); }
    100% { opacity: 0; transform: translateY(-85px) scale(1.2); }
}

.stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 16px 0;
}

.stat {
    padding: 20px 12px;
    text-align: center;
    border: 1px solid rgba(255,255,255,.05);
    border-radius: 22px;
    background: #181820;
}

.number {
    color: #fff;
    font-size: 30px;
    font-weight: 900;
}

.label {
    margin-top: 6px;
    color: #9696a1;
    font-size: 13px;
}

.buttons {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}

.action {
    min-height: 58px;
    padding: 14px 8px;
    border: 1px solid rgba(255,255,255,.05);
    border-radius: 18px;
    color: #fff;
    cursor: pointer;
    font-size: 15px;
    font-weight: 800;
    background: linear-gradient(145deg, #24242e, #1b1b23);
}

.action:active {
    transform: scale(.97);
}

.action.primary {
    color: #111;
    background: linear-gradient(145deg, #ffd34d, #ffb700);
}

.message {
    min-height: 24px;
    margin: 15px 0 5px;
    text-align: center;
    color: #ffcc33;
    font-size: 14px;
    font-weight: 700;
}

.panel {
    display: none;
    margin-top: 16px;
    padding: 20px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 24px;
    background: #181820;
}

.panel h2 {
    margin: 0 0 12px;
    font-size: 20px;
}

.ref-description {
    color: #aaaab5;
    line-height: 1.5;
    font-size: 14px;
}

.ref-count {
    margin: 12px 0;
    color: #ffc21c;
    font-weight: 900;
}

.ref-link {
    margin: 14px 0;
    padding: 13px;
    border-radius: 14px;
    color: #e7e7ed;
    word-break: break-all;
    background: #25252f;
    font-size: 13px;
}

.copy-button {
    width: 100%;
    padding: 14px;
    border: 0;
    border-radius: 15px;
    color: #111;
    background: #ffbf18;
    font-size: 15px;
    font-weight: 900;
}

.list-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: 8px 0;
    padding: 13px;
    border-radius: 15px;
    background: #20202a;
}

.name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.value {
    flex-shrink: 0;
    color: #ffc21c;
    font-weight: 900;
}

.booster-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 10px;
    margin-top: 12px;
}

.booster {
    padding: 15px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 18px;
    background: #20202a;
}

.booster-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.booster-name {
    font-size: 18px;
    font-weight: 900;
}

.booster-info {
    margin-top: 5px;
    color: #aaaab5;
    font-size: 13px;
}

.buy {
    width: 100%;
    margin-top: 10px;
    padding: 12px;
    border: 0;
    border-radius: 13px;
    color: #111;
    background: #ffbf18;
    font-weight: 900;
}

.active-booster {
    margin: 10px 0 0;
    padding: 12px;
    border-radius: 15px;
    text-align: center;
    color: #111;
    background: #ffd34d;
    font-weight: 900;
}

.loading {
    padding: 50px 10px;
    text-align: center;
    color: #aaa;
}

.error {
    padding: 20px;
    text-align: center;
    border-radius: 18px;
    color: #ff8e8e;
    background: #241719;
}

.hidden {
    display: none !important;
}

@media (max-width: 360px) {
    .container {
        padding-left: 12px;
        padding-right: 12px;
    }

    .balance {
        font-size: 55px;
    }

    .coin-button {
        min-height: 190px;
    }

    .coin-image {
        font-size: 70px;
    }
}

.referral-modal {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: none;
    align-items: flex-end;
    justify-content: center;
    background: rgba(0,0,0,.68);
    backdrop-filter: blur(5px);
}
.referral-modal.show { display: flex; }
.referral-sheet {
    width: 100%;
    max-width: 520px;
    max-height: 88vh;
    overflow-y: auto;
    padding: 22px 18px 30px;
    border-radius: 30px 30px 0 0;
    border: 1px solid rgba(255,255,255,.08);
    background: #181820;
    box-shadow: 0 -15px 45px rgba(0,0,0,.45);
}
.referral-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin-bottom: 14px;
}
.referral-head h2 { margin:0; font-size:24px; }
.close-referral {
    width:42px; height:42px; border:0; border-radius:50%;
    color:#fff; background:#292932; font-size:24px;
}
.referral-box {
    padding:16px;
    border-radius:18px;
    background:#22222c;
    margin:12px 0;
}
.referral-link {
    margin-top:10px;
    padding:13px;
    border-radius:14px;
    background:#14141a;
    color:#f0f0f3;
    font-size:13px;
    line-height:1.45;
    word-break:break-all;
}
.referral-count-big {
    color:#ffc21c;
    font-size:32px;
    font-weight:900;
}
.referral-person {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    padding:12px;
    margin-top:8px;
    border-radius:14px;
    background:#22222c;
}
.referral-person-name {
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
}
.referral-person-date {
    color:#92929d;
    font-size:11px;
}
.referral-actions {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:10px;
    margin-top:12px;
}
.referral-action {
    min-height:52px;
    border:0;
    border-radius:15px;
    font-weight:900;
    font-size:14px;
    background:#292932;
    color:#fff;
}
.referral-action.primary {
    background:linear-gradient(145deg,#ffd34d,#ffb700);
    color:#111;
}
.referral-note {
    color:#a5a5af;
    font-size:13px;
    line-height:1.5;
}
.referral-empty {
    padding:18px 8px;
    text-align:center;
    color:#92929d;
}

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
            <div class="balance" id="balance">0</div>
            <div class="currency">SHC</div>
        </div>

        <button class="coin-button" id="coinButton" onclick="clickCoin(event)">
            <span class="coin-image">🪙</span>
            <span class="coin-text" id="clickText">+1 SHC</span>
        </button>

        <div id="activeBooster" class="active-booster hidden"></div>

        <div class="stats">
            <div class="stat">
                <div class="number" id="clicks">0</div>
                <div class="label">Kliklar</div>
            </div>
            <div class="stat">
                <div class="number" id="refs">0</div>
                <div class="label">Referral</div>
            </div>
        </div>

        <div class="buttons">
            <button class="action primary" onclick="daily()">🎁 Daily Bonus</button>
            <button class="action" onclick="showReferral()">👥 Referral</button>
            <button class="action" onclick="showTop()">🏆 TOP 20</button>
            <button class="action" onclick="showBoosters()">⚡ Kuchaytirish</button>
        </div>

        <div class="message" id="message"></div>

        <div class="panel" id="refBox">
            <h2>👥 Referral</h2>
            <div class="ref-description">
                Do‘stlaringizni taklif qiling.
                Har bir yangi referral uchun sizga
                <b>100 SHC</b> beriladi.
            </div>

            <div class="ref-count" id="refCount">Referral: 0</div>

            <div class="ref-link" id="refLink"></div>

            <button class="copy-button" onclick="copyReferral()">
                📋 Linkni nusxalash
            </button>

            <h2 style="margin-top:22px;">👤 Siz qo‘shganlar</h2>
            <div id="invitedList"></div>

            <h2 style="margin-top:22px;">🏆 Referral TOP 10</h2>
            <div id="refTopList"></div>
        </div>

        <div class="panel" id="top">
            <h2>🏆 TOP 20</h2>
            <div id="topList"></div>
        </div>

        <div class="panel" id="boosters">
            <h2>⚡ Klikni kuchaytirish</h2>
            <div class="ref-description">
                SHC bilan kuchaytirgich sotib oling.
                Kuchaytirgich ishlayotgan paytda tangani bosganingizda
                ko‘proq SHC olasiz.
            </div>
            <div class="booster-grid" id="boosterList"></div>
        </div>

    </div>
</div>

<script>
const tg = window.Telegram.WebApp;

tg.ready();
tg.expand();

let telegramUser = null;
let referralLink = "";
let boosterTimer = null;

function message(text) {
    document.getElementById("message").innerText = text || "";
}

async function api(url, options = {}) {
    options.headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
    };
    return fetch(url, options);
}

function hidePanels() {
    document.getElementById("refBox").style.display = "none";
    document.getElementById("top").style.display = "none";
    document.getElementById("boosters").style.display = "none";
}

function update(data) {
    document.getElementById("balance").innerText = data.balance;
    document.getElementById("clicks").innerText = data.clicks;
    document.getElementById("refs").innerText = data.referral_count;

    const multiplier = data.booster_multiplier || 1;
    document.getElementById("clickText").innerText =
        "+" + multiplier + " SHC";

    updateBoosterTimer(data);
}

function updateBoosterTimer(data) {
    const box = document.getElementById("activeBooster");

    if (boosterTimer) {
        clearInterval(boosterTimer);
        boosterTimer = null;
    }

    if (!data.booster_expires_at || (data.booster_multiplier || 1) <= 1) {
        box.classList.add("hidden");
        return;
    }

    box.classList.remove("hidden");

    function tick() {
        const end = new Date(data.booster_expires_at).getTime();
        const left = Math.max(0, Math.floor((end - Date.now()) / 1000));

        if (left <= 0) {
            box.classList.add("hidden");
            document.getElementById("clickText").innerText = "+1 SHC";
            clearInterval(boosterTimer);
            boosterTimer = null;
            return;
        }

        const sec = left % 60;
        const min = Math.floor(left / 60);

        box.innerText =
            "⚡ x" + data.booster_multiplier +
            " faol — " + min + ":" + String(sec).padStart(2, "0");
    }

    tick();
    boosterTimer = setInterval(tick, 1000);
}

async function start() {
    try {
        telegramUser = tg.initDataUnsafe.user;

        if (!telegramUser) {
            document.getElementById("loading").innerHTML =
                '<div class="error">❌ Telegram bot ichidan oching.</div>';
            return;
        }

        const startParam = tg.initDataUnsafe.start_param || "";

        const response = await api("/api/me", {
            method: "POST",
            body: JSON.stringify({
                user: telegramUser,
                start_param: startParam
            })
        });

        const data = await response.json();

        if (!data.success) {
            document.getElementById("loading").innerHTML =
                '<div class="error">❌ ' +
                (data.error || "Xatolik yuz berdi") +
                '</div>';
            return;
        }

        document.getElementById("loading").classList.add("hidden");
        document.getElementById("app").classList.remove("hidden");

        update(data.user);
    } catch (error) {
        console.error(error);
        document.getElementById("loading").innerHTML =
            '<div class="error">❌ Server bilan ulanishda xatolik.</div>';
    }
}

async function clickCoin(event) {
    const button = document.getElementById("coinButton");

    // Bosilganda kichik vizual +reward.
    if (event) {
        const plus = document.createElement("span");
        plus.className = "plus";
        plus.innerText = document.getElementById("clickText").innerText;
        plus.style.left = (event.offsetX || button.clientWidth / 2) + "px";
        plus.style.top = (event.offsetY || button.clientHeight / 2) + "px";
        button.appendChild(plus);
        setTimeout(() => plus.remove(), 750);
    }

    button.disabled = true;

    try {
        const response = await api("/api/click", {
            method: "POST",
            body: JSON.stringify({ user: telegramUser })
        });

        const data = await response.json();

        if (!data.success) {
            message(data.error || "Xatolik");
            return;
        }

        document.getElementById("balance").innerText = data.balance;
        document.getElementById("clicks").innerText = data.clicks;

        const multiplier = data.multiplier || 1;
        document.getElementById("clickText").innerText =
            "+" + multiplier + " SHC";

        updateBoosterTimer({
            booster_multiplier: multiplier,
            booster_expires_at: data.expires_at
        });

        if (tg.HapticFeedback) {
            tg.HapticFeedback.impactOccurred("light");
        }
    } catch (error) {
        console.error(error);
        message("❌ Xatolik yuz berdi");
    } finally {
        button.disabled = false;
    }
}

async function daily() {
    try {
        const response = await api("/api/daily", {
            method: "POST",
            body: JSON.stringify({ user: telegramUser })
        });

        const data = await response.json();

        if (!data.success) {
            message(data.error || "Xatolik");
            return;
        }

        document.getElementById("balance").innerText = data.balance;
        message("🎁 +" + data.bonus + " SHC olindi!");
    } catch (error) {
        console.error(error);
        message("❌ Xatolik yuz berdi");
    }
}


async function showReferral() {
    const modal = document.getElementById("referralModal");
    if (!modal) return;

    modal.classList.add("show");
    document.getElementById("referralModalLink").innerText = "Yuklanmoqda...";
    document.getElementById("myReferralsList").innerHTML =
        '<div class="referral-empty">Yuklanmoqda...</div>';
    document.getElementById("referralTop10List").innerHTML =
        '<div class="referral-empty">Yuklanmoqda...</div>';

    try {
        const response = await api("/api/referral/details", {
            method: "POST",
            body: JSON.stringify({ user: user })
        });

        const data = await response.json();

        if (!data.success) {
            message(data.error || "Referral ma'lumotlarini olishda xatolik");
            return;
        }

        referralLink = data.link;

        document.getElementById("referralModalCount").innerText = data.count;
        document.getElementById("referralModalLink").innerText = data.link;

        const myList = document.getElementById("myReferralsList");

        if (!data.invited || data.invited.length === 0) {
            myList.innerHTML =
                '<div class="referral-empty">Hali hech kim qo‘shilmagan.</div>';
        } else {
            myList.innerHTML = data.invited.map((person, index) => {
                const name = person.first_name ||
                    (person.username ? "@" + person.username : "Foydalanuvchi");
                const username = person.username ? "@" + person.username : "";
                return `
                    <div class="referral-person">
                        <div class="referral-person-name">
                            <b>${index + 1}. ${escapeHtml(name)}</b>
                            <div class="referral-person-date">${escapeHtml(username)}</div>
                        </div>
                        <div style="color:#ffc21c;font-weight:900">+100 SHC</div>
                    </div>
                `;
            }).join("");
        }

        const topList = document.getElementById("referralTop10List");

        if (!data.top10 || data.top10.length === 0) {
            topList.innerHTML =
                '<div class="referral-empty">Hali TOP 10 mavjud emas.</div>';
        } else {
            topList.innerHTML = data.top10.map((person, index) => {
                const name = person.first_name ||
                    (person.username ? "@" + person.username : "Foydalanuvchi");
                const username = person.username ? "@" + person.username : "";
                return `
                    <div class="referral-person">
                        <div class="referral-person-name">
                            <b>${index + 1}. ${escapeHtml(name)}</b>
                            <div class="referral-person-date">${escapeHtml(username)}</div>
                        </div>
                        <div style="color:#ffc21c;font-weight:900">
                            ${person.referral_count} 👥
                        </div>
                    </div>
                `;
            }).join("");
        }

    } catch (error) {
        console.error(error);
        message("❌ Referral ma'lumotlarini yuklashda xatolik");
    }
}

function closeReferral() {
    const modal = document.getElementById("referralModal");
    if (modal) modal.classList.remove("show");
}

function closeReferralOutside(event) {
    if (event.target && event.target.id === "referralModal") {
        closeReferral();
    }
}

async function copyReferralModal() {
    if (!referralLink) return;

    try {
        await navigator.clipboard.writeText(referralLink);
        message("✅ Referral havola nusxalandi");
    } catch (error) {
        const input = document.createElement("textarea");
        input.value = referralLink;
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
        message("✅ Referral havola nusxalandi");
    }
}

function shareReferralModal() {
    if (!referralLink) return;

    const text = "🪙 SHOHCOINS ga qo‘shiling! Har kuni SHC ishlang.";

    if (tg && tg.openTelegramLink) {
        const shareUrl =
            "https://t.me/share/url?url=" +
            encodeURIComponent(referralLink) +
            "&text=" +
            encodeURIComponent(text);

        tg.openTelegramLink(shareUrl);
    } else {
        window.open(
            "https://t.me/share/url?url=" +
            encodeURIComponent(referralLink) +
            "&text=" +
            encodeURIComponent(text),
            "_blank"
        );
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


async function copyReferral() {
    if (!referralLink) return;

    try {
        await navigator.clipboard.writeText(referralLink);
        message("📋 Referral link nusxalandi!");
    } catch (error) {
        message("Linkni nusxalab bo‘lmadi");
    }
}

async function showTop() {
    hidePanels();
    const box = document.getElementById("top");
    box.style.display = "block";

    try {
        const response = await fetch("/api/top");
        const data = await response.json();

        const list = document.getElementById("topList");

        if (!data.users.length) {
            list.innerHTML =
                '<div class="ref-description">Hali reyting mavjud emas.</div>';
            return;
        }

        list.innerHTML = data.users.map((u, i) => {
            const name = u.first_name ||
                (u.username ? "@" + u.username : "Foydalanuvchi");

            return '<div class="list-row">' +
                '<div class="name">#' + (i + 1) + ' ' +
                escapeHtml(name) + '</div>' +
                '<div class="value">' +
                (u.balance || 0) + ' SHC</div>' +
                '</div>';
        }).join("");

        box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        console.error(error);
        message("❌ TOP yuklashda xatolik");
    }
}

async function showBoosters() {
    hidePanels();
    const box = document.getElementById("boosters");
    box.style.display = "block";

    try {
        const response = await fetch("/api/boosters", {
            method: "POST"
        });

        const data = await response.json();
        const list = document.getElementById("boosterList");

        list.innerHTML = Object.entries(data.boosters).map(([key, b]) => {
            return '<div class="booster">' +
                '<div class="booster-top">' +
                    '<div class="booster-name">⚡ ' + key + '</div>' +
                    '<div class="value">' + b.price + ' SHC</div>' +
                '</div>' +
                '<div class="booster-info">' +
                    'Har klik: ' + b.multiplier + ' SHC • ' +
                    b.seconds + ' soniya' +
                '</div>' +
                '<button class="buy" onclick="buyBooster(\'' +
                    key + '\')">' +
                    '🪙 Sotib olish' +
                '</button>' +
            '</div>';
        }).join("");

        box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        console.error(error);
        message("❌ Kuchaytirgichlarni yuklashda xatolik");
    }
}

async function buyBooster(type) {
    try {
        const response = await api("/api/buy-booster", {
            method: "POST",
            body: JSON.stringify({
                user: telegramUser,
                type: type
            })
        });

        const data = await response.json();

        if (!data.success) {
            message(data.error || "Xatolik");
            return;
        }

        document.getElementById("balance").innerText = data.balance;
        document.getElementById("clickText").innerText =
            "+" + data.multiplier + " SHC";

        updateBoosterTimer({
            booster_multiplier: data.multiplier,
            booster_expires_at: data.expires_at
        });

        message(
            "⚡ x" + data.multiplier +
            " kuchaytirgich yoqildi!"
        );
    } catch (error) {
        console.error(error);
        message("❌ Kuchaytirgichni sotib olishda xatolik");
    }
}

function escapeHtml(text) {
    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

start();
</script>


    <div class="referral-modal" id="referralModal" onclick="closeReferralOutside(event)">
        <div class="referral-sheet">
            <div class="referral-head">
                <h2>👥 Referral</h2>
                <button class="close-referral" onclick="closeReferral()">×</button>
            </div>

            <div class="referral-box">
                <div class="referral-note">
                    Do‘stlaringizni taklif qiling. Har bir yangi referral uchun
                    <b style="color:#ffc21c">100 SHC</b> oling.
                </div>

                <div style="margin-top:12px">Siz qo‘shganlar:</div>
                <div class="referral-count-big" id="referralModalCount">0</div>
            </div>

            <div class="referral-box">
                <div class="referral-note">Sizning referral havolangiz:</div>
                <div class="referral-link" id="referralModalLink">Yuklanmoqda...</div>

                <div class="referral-actions">
                    <button class="referral-action primary" onclick="copyReferralModal()">
                        📋 Nusxalash
                    </button>
                    <button class="referral-action" onclick="shareReferralModal()">
                        📤 Ulashish
                    </button>
                </div>
            </div>

            <div class="referral-box">
                <h3 style="margin:0 0 8px">👥 Siz qo‘shgan odamlar</h3>
                <div id="myReferralsList">
                    <div class="referral-empty">Yuklanmoqda...</div>
                </div>
            </div>

            <div class="referral-box">
                <h3 style="margin:0 0 8px">🏆 Referral TOP 10</h3>
                <div id="referralTop10List">
                    <div class="referral-empty">Yuklanmoqda...</div>
                </div>
            </div>
        </div>
    </div>

</body>
</html>
"""


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    init_db()

    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
