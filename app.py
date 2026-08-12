import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10
REFERRAL_REWARD = 100


# =========================================================
# DATABASE
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable topilmadi")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


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
            last_daily_bonus DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
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

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS referred_by BIGINT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS last_daily_bonus DATE
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

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = %s
        """,
        (telegram_id,)
    )

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

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = %s
        """,
        (telegram_id,)
    )

    existing = cur.fetchone()

    if existing:
        cur.execute(
            """
            UPDATE users
            SET
                username = %s,
                first_name = %s
            WHERE telegram_id = %s
            """,
            (
                username,
                first_name,
                telegram_id
            )
        )

        conn.commit()

        cur.execute(
            """
            SELECT *
            FROM users
            WHERE telegram_id = %s
            """,
            (telegram_id,)
        )

        existing = cur.fetchone()

        cur.close()
        conn.close()

        return existing, False

    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute(
                    """
                    SELECT telegram_id
                    FROM users
                    WHERE telegram_id = %s
                    """,
                    (referral_id,)
                )

                referrer = cur.fetchone()

                if referrer:
                    valid_referrer = referral_id

        except (ValueError, TypeError):
            valid_referrer = None

    referral_code = str(telegram_id)

    cur.execute(
        """
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            clicks,
            referral_code,
            referred_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            telegram_id,
            username,
            first_name,
            NEW_USER_BONUS,
            0,
            referral_code,
            valid_referrer
        )
    )

    user = cur.fetchone()

    # -----------------------------------------------------
    # REFERRAL
    # -----------------------------------------------------

    if valid_referrer:

        cur.execute(
            """
            SELECT id
            FROM referrals
            WHERE invited_id = %s
            """,
            (telegram_id,)
        )

        already = cur.fetchone()

        if not already:

            cur.execute(
                """
                UPDATE users
                SET
                    balance = balance + %s,
                    referral_count = referral_count + 1
                WHERE telegram_id = %s
                RETURNING referral_count
                """,
                (
                    REFERRAL_REWARD,
                    valid_referrer
                )
            )

            result = cur.fetchone()

            if result:

                cur.execute(
                    """
                    INSERT INTO referrals (
                        inviter_id,
                        invited_id,
                        reward
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (
                        valid_referrer,
                        telegram_id,
                        REFERRAL_REWARD
                    )
                )

    conn.commit()

    cur.close()
    conn.close()

    return user, True


# =========================================================
# CURRENT USER
# =========================================================

def get_current_user():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")

    if not tg_user:
        return None

    try:
        telegram_id = int(tg_user["id"])
    except (KeyError, ValueError, TypeError):
        return None

    return get_user(telegram_id)


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# =========================================================
# LOGIN / USER DATA
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

    start_param = str(
        data.get("start_param", "") or ""
    ).strip()

    referral_id = None

    if start_param.startswith("ref_"):
        referral_id = start_param[4:]
    elif start_param:
        referral_id = start_param

    try:
        user, created = create_user(
            tg_user,
            referral_id
        )
    except Exception as e:
        print("ME ERROR:", e)

        return jsonify({
            "success": False,
            "error": "Server xatosi"
        }), 500

    return jsonify({
        "success": True,
        "new_user": created,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"] or "",
            "first_name": user["first_name"] or "",
            "balance": user["balance"],
            "clicks": user["clicks"],
            "referral_count": user["referral_count"] or 0
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

    cur.execute(
        """
        UPDATE users
        SET
            balance = balance + %s,
            clicks = clicks + 1
        WHERE telegram_id = %s
        RETURNING balance, clicks
        """,
        (
            CLICK_REWARD,
            user["telegram_id"]
        )
    )

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

    cur.execute(
        """
        SELECT last_daily_bonus
        FROM users
        WHERE telegram_id = %s
        FOR UPDATE
        """,
        (user["telegram_id"],)
    )

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:

        conn.rollback()
        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni allaqachon oldingiz 🎁"
        })

    cur.execute(
        """
        UPDATE users
        SET
            balance = balance + %s,
            last_daily_bonus = %s
        WHERE telegram_id = %s
        RETURNING balance
        """,
        (
            DAILY_BONUS,
            today,
            user["telegram_id"]
        )
    )

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
        "https://t.me/"
        + BOT_USERNAME
        + "?start=ref_"
        + str(user["telegram_id"])
    )

    return jsonify({
        "success": True,
        "count": user["referral_count"] or 0,
        "reward": REFERRAL_REWARD,
        "link": link
    })


# =========================================================
# TOP
# =========================================================

@app.route("/api/top", methods=["GET"])
def top():
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT
            first_name,
            username,
            balance,
            clicks,
            referral_count
        FROM users
        ORDER BY balance DESC
        LIMIT 20
        """
    )

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
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({
            "status": "ok",
            "database": "connected",
            "project": "Shohcoins"
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "database": "disconnected",
            "project": "Shohcoins"
        }), 500


# =========================================================
# FRONTEND
# =========================================================

HTML = r'''
<!DOCTYPE html>
<html lang="uz">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"
>

<title>SHOHCOINS</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

* {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

html,
body {
    margin: 0;
    padding: 0;
    min-height: 100%;
}

body {
    min-height: 100vh;
    color: #fff;
    font-family: Arial, Helvetica, sans-serif;
    background:
        radial-gradient(
            circle at 50% -10%,
            #30291b 0%,
            #121217 40%,
            #07070a 100%
        );
}

button {
    font-family: inherit;
}

.container {
    width: 100%;
    max-width: 520px;
    margin: 0 auto;
    padding: 18px 16px 35px;
}

.header {
    text-align: center;
    padding: 8px 0 20px;
}

.logo {
    width: 72px;
    height: 72px;
    margin: 0 auto 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 23px;
    font-size: 40px;
    background:
        linear-gradient(
            145deg,
            #ffe88c,
            #ffc21b 55%,
            #db9200
        );
    box-shadow:
        0 12px 35px rgba(255,190,20,.22);
}

.title {
    margin: 0;
    font-size: 29px;
    font-weight: 900;
    letter-spacing: 1px;
}

.subtitle {
    margin-top: 6px;
    color: #9999a4;
    font-size: 13px;
}

.card {
    position: relative;
    overflow: hidden;
    padding: 25px 18px;
    margin-bottom: 16px;
    border: 1px solid rgba(255,255,255,.07);
    border-radius: 28px;
    background:
        linear-gradient(
            145deg,
            #202027,
            #131319
        );
    box-shadow:
        0 15px 40px rgba(0,0,0,.28);
}

.card:before {
    content: "";
    position: absolute;
    width: 150px;
    height: 150px;
    right: -70px;
    top: -70px;
    border-radius: 50%;
    background: rgba(255,194,28,.08);
}

.balance-title {
    position: relative;
    text-align: center;
    color: #a5a5ae;
    font-size: 15px;
}

.balance {
    position: relative;
    margin: 8px 0;
    text-align: center;
    color: #ffc21c;
    font-size: 60px;
    line-height: 1;
    font-weight: 900;
}

.currency {
    text-align: center;
    color: #d3d3d8;
    font-size: 16px;
    font-weight: 800;
}

.click {
    width: 100%;
    min-height: 175px;
    border: 0;
    border-radius: 30px;
    color: #111;
    cursor: pointer;
    font-size: 28px;
    font-weight: 900;
    background:
        linear-gradient(
            145deg,
            #ffd951,
            #ffb600
        );
    box-shadow:
        0 16px 35px rgba(255,184,0,.20);
    transition: transform .12s ease;
}

.click:active {
    transform: scale(.965);
}

.click:disabled {
    opacity: .75;
}

.coin {
    display: block;
    margin-bottom: 6px;
    font-size: 48px;
}

.stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 16px 0;
}

.stat {
    padding: 19px 10px;
    text-align: center;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 22px;
    background: #18181f;
}

.number {
    color: #fff;
    font-size: 29px;
    font-weight: 900;
}

.label {
    margin-top: 6px;
    color: #9898a3;
    font-size: 13px;
}

.buttons {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 11px;
}

.action {
    min-height: 58px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 18px;
    color: #fff;
    cursor: pointer;
    font-size: 14px;
    font-weight: 800;
    background:
        linear-gradient(
            145deg,
            #25252e,
            #1b1b23
        );
}

.action:active {
    transform: scale(.97);
}

.action.primary {
    color: #111;
    background:
        linear-gradient(
            145deg,
            #ffd951,
            #ffb600
        );
}

.message {
    min-height: 25px;
    margin: 15px 0 4px;
    text-align: center;
    color: #ffc21c;
    font-size: 14px;
    font-weight: 800;
}

.panel {
    display: none;
    margin-top: 16px;
    padding: 20px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 24px;
    background: #18181f;
}

.panel h2 {
    margin: 0 0 13px;
    font-size: 20px;
}

.ref-description {
    color: #aaaab4;
    line-height: 1.5;
    font-size: 14px;
}

.ref-count {
    margin-top: 13px;
    color: #ffc21c;
    font-weight: 900;
}

.ref-link {
    margin: 14px 0;
    padding: 13px;
    border-radius: 14px;
    color: #e5e5eb;
    word-break: break-all;
    background: #25252e;
    font-size: 13px;
}

.copy-button {
    width: 100%;
    padding: 14px;
    border: 0;
    border-radius: 15px;
    color: #111;
    cursor: pointer;
    background: #ffc21c;
    font-size: 15px;
    font-weight: 900;
}

.player {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: 8px 0;
    padding: 14px;
    border-radius: 15px;
    background: #21212a;
}

.player-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.player-balance {
    flex-shrink: 0;
    color: #ffc21c;
    font-weight: 900;
}

.loading {
    padding: 50px 10px;
    text-align: center;
    color: #aaaab2;
}

.error {
    padding: 20px;
    text-align: center;
    border-radius: 18px;
    color: #ff9999;
    background: #26181a;
}

.hidden {
    display: none !important;
}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <div class="logo">
            🪙
        </div>

        <h1 class="title">
            SHOHCOINS
        </h1>

        <div class="subtitle">
            SHC • Digital Rewards
        </div>

    </div>

    <div id="loading" class="loading">
        Telegram aniqlanmoqda...
    </div>

    <div id="app" class="hidden">

        <div class="card">

            <div class="balance-title">
                Balansingiz
            </div>

            <div id
