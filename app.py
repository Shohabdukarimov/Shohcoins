import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10

REFERRAL_REWARDS = {
    1: 500,
    2: 600,
    3: 700,
    4: 800,
    5: 900,
    10: 1400
}


def db():
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
            last_daily_bonus DATE,
            referral_count INTEGER DEFAULT 0,
            bonus_5_given BOOLEAN DEFAULT FALSE,
            bonus_10_given BOOLEAN DEFAULT FALSE,
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


def create_user(tg_user, referral_id=None):
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username", "")
    first_name = tg_user.get("first_name", "")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE telegram_id=%s",
        (telegram_id,)
    )

    user = cur.fetchone()

    if user:
        cur.close()
        conn.close()
        return user, False

    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id=%s",
                    (referral_id,)
                )

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
        valid_referrer
    ))

    user = cur.fetchone()

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

                reward = REFERRAL_REWARDS.get(count, 0)

                cur.execute("""
                    UPDATE users
                    SET referral_count=%s,
                        balance=balance+%s
                    WHERE telegram_id=%s
                """, (
                    count,
                    reward,
                    valid_referrer
                ))

                if count == 5:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+500,
                            bonus_5_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_5_given=FALSE
                    """, (valid_referrer,))

                    reward += 500

                if count == 10:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+1000,
                            bonus_10_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_10_given=FALSE
                    """, (valid_referrer,))

                    reward += 1000

                cur.execute("""
                    INSERT INTO referrals
                    (inviter_id, invited_id, reward)
                    VALUES (%s, %s, %s)
                """, (
                    valid_referrer,
                    telegram_id,
                    reward
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

    referral_id = data.get("referral_id")

    user, _ = create_user(
        tg_user,
        referral_id
    )

    return user


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# =========================================================
# USER
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

    referral_id = data.get("referral_id")

    user, created = create_user(
        tg_user,
        referral_id
    )

    return jsonify({
        "success": True,
        "new_user": created,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "clicks": user["clicks"],
            "referral_count": user["referral_count"]
        }
    })


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

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
    """, (
        CLICK_REWARD,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "balance": result["balance"],
        "clicks": result["clicks"]
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
    """, (
        user["telegram_id"],
    ))

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:

        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz"
        })

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (
        DAILY_BONUS,
        today,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "bonus": DAILY_BONUS,
        "balance": result["balance"]
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

    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "link": link
    })


# =========================================================
# TOP
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
        ORDER BY balance DESC
        LIMIT 20
    """)

    users = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "users": users
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

<meta
name="viewport"
content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
>

<title>SHOHCОINS</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

* {
    box-sizing:border-box;
    -webkit-tap-highlight-color:transparent;
}

:root {
    --bg:#08090d;
    --card:#14161e;
    --card2:#1b1e28;
    --gold:#ffbd18;
    --gold2:#ffca38;
    --text:#ffffff;
    --muted:#9296a3;
    --border:rgba(255,255,255,.06);
}

html,
body {
    margin:0;
    padding:0;
    width:100%;
    min-height:100vh;
}

body {
    color:var(--text);
    font-family:
        Arial,
        Helvetica,
        sans-serif;import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10

REFERRAL_REWARDS = {
    1: 500,
    2: 600,
    3: 700,
    4: 800,
    5: 900,
    10: 1400
}


def db():
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
            last_daily_bonus DATE,
            referral_count INTEGER DEFAULT 0,
            bonus_5_given BOOLEAN DEFAULT FALSE,
            bonus_10_given BOOLEAN DEFAULT FALSE,
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


def create_user(tg_user, referral_id=None):
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username", "")
    first_name = tg_user.get("first_name", "")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE telegram_id=%s",
        (telegram_id,)
    )

    user = cur.fetchone()

    if user:
        cur.close()
        conn.close()
        return user, False

    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id=%s",
                    (referral_id,)
                )

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
        valid_referrer
    ))

    user = cur.fetchone()

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

                reward = REFERRAL_REWARDS.get(count, 0)

                cur.execute("""
                    UPDATE users
                    SET referral_count=%s,
                        balance=balance+%s
                    WHERE telegram_id=%s
                """, (
                    count,
                    reward,
                    valid_referrer
                ))

                if count == 5:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+500,
                            bonus_5_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_5_given=FALSE
                    """, (valid_referrer,))

                    reward += 500

                if count == 10:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+1000,
                            bonus_10_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_10_given=FALSE
                    """, (valid_referrer,))

                    reward += 1000

                cur.execute("""
                    INSERT INTO referrals
                    (inviter_id, invited_id, reward)
                    VALUES (%s, %s, %s)
                """, (
                    valid_referrer,
                    telegram_id,
                    reward
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

    referral_id = data.get("referral_id")

    user, _ = create_user(
        tg_user,
        referral_id
    )

    return user


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# =========================================================
# USER
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

    referral_id = data.get("referral_id")

    user, created = create_user(
        tg_user,
        referral_id
    )

    return jsonify({
        "success": True,
        "new_user": created,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "clicks": user["clicks"],
            "referral_count": user["referral_count"]
        }
    })


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

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
    """, (
        CLICK_REWARD,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "balance": result["balance"],
        "clicks": result["clicks"]
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
    """, (
        user["telegram_id"],
    ))

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:

        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz"
        })

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (
        DAILY_BONUS,
        today,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "bonus": DAILY_BONUS,
        "balance": result["balance"]
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

    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "link": link
    })


# =========================================================
# TOP
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
        ORDER BY balance DESC
        LIMIT 20
    """)

    users = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "users": users
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

<meta
name="viewport"
content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
>

<title>SHOHCОINS</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

* {
    box-sizing:border-box;
    -webkit-tap-highlight-color:transparent;
}

:root {
    --bg:#08090d;
    --card:#14161e;
    --card2:#1b1e28;
    --gold:#ffbd18;
    --gold2:#ffca38;
    --text:#ffffff;
    --muted:#9296a3;
    --border:rgba(255,255,255,.06);
}

html,
body {
    margin:0;
    padding:0;
    width:100%;
    min-height:100vh;
}

body {
    color:var(--text);
    font-family:
        Arial,
        Helvetica,
        sans-serif;import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10

REFERRAL_REWARDS = {
    1: 500,
    2: 600,
    3: 700,
    4: 800,
    5: 900,
    10: 1400
}


def db():
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
            last_daily_bonus DATE,
            referral_count INTEGER DEFAULT 0,
            bonus_5_given BOOLEAN DEFAULT FALSE,
            bonus_10_given BOOLEAN DEFAULT FALSE,
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


def create_user(tg_user, referral_id=None):
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username", "")
    first_name = tg_user.get("first_name", "")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE telegram_id=%s",
        (telegram_id,)
    )

    user = cur.fetchone()

    if user:
        cur.close()
        conn.close()
        return user, False

    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id=%s",
                    (referral_id,)
                )

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
        valid_referrer
    ))

    user = cur.fetchone()

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

                reward = REFERRAL_REWARDS.get(count, 0)

                cur.execute("""
                    UPDATE users
                    SET referral_count=%s,
                        balance=balance+%s
                    WHERE telegram_id=%s
                """, (
                    count,
                    reward,
                    valid_referrer
                ))

                if count == 5:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+500,
                            bonus_5_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_5_given=FALSE
                    """, (valid_referrer,))

                    reward += 500

                if count == 10:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance+1000,
                            bonus_10_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_10_given=FALSE
                    """, (valid_referrer,))

                    reward += 1000

                cur.execute("""
                    INSERT INTO referrals
                    (inviter_id, invited_id, reward)
                    VALUES (%s, %s, %s)
                """, (
                    valid_referrer,
                    telegram_id,
                    reward
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

    referral_id = data.get("referral_id")

    user, _ = create_user(
        tg_user,
        referral_id
    )

    return user


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# =========================================================
# USER
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

    referral_id = data.get("referral_id")

    user, created = create_user(
        tg_user,
        referral_id
    )

    return jsonify({
        "success": True,
        "new_user": created,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "clicks": user["clicks"],
            "referral_count": user["referral_count"]
        }
    })


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

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
    """, (
        CLICK_REWARD,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "balance": result["balance"],
        "clicks": result["clicks"]
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
    """, (
        user["telegram_id"],
    ))

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:

        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz"
        })

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (
        DAILY_BONUS,
        today,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "bonus": DAILY_BONUS,
        "balance": result["balance"]
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

    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "link": link
    })


# =========================================================
# TOP
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
        ORDER BY balance DESC
        LIMIT 20
    """)

    users = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "users": users
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

<meta
name="viewport"
content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
>

<title>SHOHCОINS</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

* {
    box-sizing:border-box;
    -webkit-tap-highlight-color:transparent;
}

:root {
    --bg:#08090d;
    --card:#14161e;
    --card2:#1b1e28;
    --gold:#ffbd18;
    --gold2:#ffca38;
    --text:#ffffff;
    --muted:#9296a3;
    --border:rgba(255,255,255,.06);
}

html,
body {
    margin:0;
    padding:0;
    width:100%;
    min-height:100vh;
}

body {
    color:var(--text);
    font-family:
        Arial,
        Helvetica,
        sans-serif;
