import os
from datetime import date

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

REFERRAL_REWARDS = {
    1: 500,
    2: 600,
    3: 700,
    4: 800,
    5: 900,
    10: 1400,
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
# USER FUNCTIONS
# =========================================================

def get_user(telegram_id):
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id=%s
        """,
        (telegram_id,),
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
        WHERE telegram_id=%s
        """,
        (telegram_id,),
    )

    existing_user = cur.fetchone()

    if existing_user:
        cur.close()
        conn.close()
        return existing_user, False

    referral_code = str(telegram_id)
    valid_referrer = None

    if referral_id:
        try:
            referral_id = int(referral_id)

            if referral_id != telegram_id:
                cur.execute(
                    """
                    SELECT telegram_id
                    FROM users
                    WHERE telegram_id=%s
                    """,
                    (referral_id,),
                )

                referrer = cur.fetchone()

                if referrer:
                    valid_referrer = referral_id

        except (ValueError, TypeError):
            valid_referrer = None

    # Yangi foydalanuvchi
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
        VALUES (%s, %s, %s, %s, 0, %s, %s)
        RETURNING *
        """,
        (
            telegram_id,
            username,
            first_name,
            NEW_USER_BONUS,
            referral_code,
            valid_referrer,
        ),
    )

    user = cur.fetchone()

    # =====================================================
    # REFERRAL
    # =====================================================

    if valid_referrer:

        cur.execute(
            """
            SELECT id
            FROM referrals
            WHERE invited_id=%s
            """,
            (telegram_id,),
        )

        already_referred = cur.fetchone()

        if not already_referred:

            cur.execute(
                """
                SELECT referral_count
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
                """,
                (valid_referrer,),
            )

            referrer = cur.fetchone()

            if referrer:

                count = (referrer["referral_count"] or 0) + 1

                reward = REFERRAL_REWARDS.get(count, 0)

                # Oddiy referral mukofoti
                cur.execute(
                    """
                    UPDATE users
                    SET
                        referral_count=%s,
                        balance=balance+%s
                    WHERE telegram_id=%s
                    """,
                    (
                        count,
                        reward,
                        valid_referrer,
                    ),
                )

                # 5 ta referral bonusi
                if count == 5:

                    cur.execute(
                        """
                        UPDATE users
                        SET
                            balance=balance+500,
                            bonus_5_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_5_given=FALSE
                        """,
                        (valid_referrer,),
                    )

                    reward += 500

                # 10 ta referral bonusi
                if count == 10:

                    cur.execute(
                        """
                        UPDATE users
                        SET
                            balance=balance+1000,
                            bonus_10_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_10_given=FALSE
                        """,
                        (valid_referrer,),
                    )

                    reward += 1000

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
                        reward,
                    ),
                )

    conn.commit()

    cur.close()
    conn.close()

    return user, True


def get_current_user():
    data = request.get_json(silent=True) or {}

    tg_user = data.get("user")

    if not tg_user:
        return None

    return get_user(int(tg_user["id"]))


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
            "error": "Telegram user topilmadi",
        }), 400

    # Telegram WebApp referral start_param
    start_param = data.get("start_param", "")

    referral_id = None

    if start_param:
        if str(start_param).startswith("ref_"):
            referral_id = str(start_param)[4:]
        else:
            referral_id = str(start_param)

    user, created = create_user(
        tg_user,
        referral_id=referral_id,
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
            "referral_count": user["referral_count"],
        },
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
            "error": "Telegram orqali kiring",
        }), 400

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        UPDATE users
        SET
            balance=balance+%s,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
        """,
        (
            CLICK_REWARD,
            user["telegram_id"],
        ),
    )

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "balance": result["balance"],
        "clicks": result["clicks"],
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
            "error": "Telegram orqali kiring",
        }), 400

    today = date.today()

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT last_daily_bonus
        FROM users
        WHERE telegram_id=%s
        FOR UPDATE
        """,
        (user["telegram_id"],),
    )

    row = cur.fetchone()

    if row and row["last_daily_bonus"] == today:

        conn.rollback()
        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz 🎁",
        })

    cur.execute(
        """
        UPDATE users
        SET
            balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
        """,
        (
            DAILY_BONUS,
            today,
            user["telegram_id"],
        ),
    )

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
            "error": "Telegram orqali kiring",
        }), 400

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user['telegram_id']}"
    )

    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "link": link,
    })


# =========================================================
# TOP
# =========================================================

@app.route("/api/top")
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
        "users": users,
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "project": "Shohcoins",
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
    color: #ffffff;
    font-family:
        Arial,
        Helvetica,
        sans-serif;
    background:
        radial-gradient(
            circle at 50% -10%,
            #25212d 0%,
            #101016 42%,
            #08080c 100%
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
    padding: 10px 0 22px;
}

.logo {
    width: 68px;
    height: 68px;
    margin: 0 auto 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 22px;
    font-size: 38px;
    background:
        linear-gradient(
            145deg,
            #ffe48a,
            #ffbf18 55%,
            #d98d00
        );
    box-shadow:
        0 10px 30px rgba(255, 190, 20, 0.25);
}

.title {
    margin: 0;
    font-size: 28px;
    font-weight: 900;
    letter-spacing: 1px;
}

.subtitle {
    margin-top: 6px;
    color: #9696a2;
    font-size: 13px;
}

.card {
    position: relative;
    overflow: hidden;
    padding: 25px 20px;
    margin-bottom: 16px;
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 28px;
    background:
        linear-gradient(
            145deg,
            #1c1c25,
            #121218
        );
    box-shadow:
        0 14px 35px rgba(0,0,0,.25);
}

.card::before {
    content: "";
    position: absolute;
    width: 140px;
    height: 140px;
    right: -65px;
    top: -65px;
    border-radius: 50%;
    background: rgba(255,194,25,.08);
}

.balance-title {
    position: relative;
    text-align: center;
    color: #a7a7b1;
    font-size: 15px;
}

.balance {
    position: relative;
    margin: 7px 0;
    text-align: center;
    color: #ffc21c;
    font-size: 64px;
    line-height: 1;
    font-weight: 900;
    text-shadow:
        0 8px 25px rgba(255,194,28,.15);
}

.currency {
    position: relative;
    text-align: center;
    color: #d2d2d8;
    font-size: 16px;
    font-weight: 700;
}

.click {
    width: 100%;
    min-height: 170px;
    border: 0;
    border-radius: 30px;
    color: #111111;
    cursor: pointer;
    font-size: 28px;
    font-weight: 900;
    background:
        linear-gradient(
            145deg,
            #ffd34d,
            #ffb700
        );
    box-shadow:
        0 15px 35px rgba(255,184,0,.20);
    transition:
        transform .12s ease,
        box-shadow .12s ease;
}

.click:active {
    transform: scale(.97);
    box-shadow:
        0 7px 15px rgba(255,184,0,.12);
}

.coin {
    display: block;
    margin-bottom: 5px;
    font-size: 45px;
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
    color: #ffffff;
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
    color: #ffffff;
    cursor: pointer;
    font-size: 15px;
    font-weight: 800;
    background:
        linear-gradient(
            145deg,
            #24242e,
            #1b1b23
        );
    transition:
        transform .12s ease,
        background .12s ease;
}

.action:active {
    transform: scale(.97);
}

.action.primary {
    color: #111111;
    background:
        linear-gradient(
            145deg,
            #ffd34d,
            #ffb700
        );
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
    color: #111111;
    background: #ffbf18;
    font-size: 15px;
    font-weight: 900;
}

.ref-count {
    margin-top: 12px;
    color: #ffc21c;
    font-weight: 800;
}

.player {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: 8px 0;
    padding: 14px;
    border-radius: 15px;
    background: #20202a;
}

.player-name {
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
    color: #aaaaaf;
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

    .click {
        min-height: 150px;
    }

}

</style>

</head>

<body>

<div class="container">

    <header class="header">

        <div class="logo">
            🪙
        </div>

        <h1 class="title">
            SHOHCOINS
        </h1>

        <div class="subtitle">
            SHC • Digital Rewards
        </div>

    </header>


    <div id="loading" class="loading">
        Telegram aniqlanmoqda...
    </div>


    <div id="app" class="hidden">

        <div class="card">

            <div class="balance-title">
                Balansingiz
            </div>

            <div
                class="balance"
                id="balance"
            >
                0
            </div>

            <div class="currency">
                SHC
            </div>

        </div>


        <button
            class="click"
            id="clickButton"
            onclick="clickCoin()"
        >

            <span class="coin">
                🪙
            </span>

            +1 SHC

        </button>


        <div class="stats">

            <div class="stat">

                <div
                    class="number"
                    id="clicks"
                >
                    0
                </div>

                <div class="label">
                    Kliklar
                </div>

            </div>


            <div class="stat">

                <div
                    class="number"
                    id="refs"
                >
                    0
                </div>

                <div class="label">
                    Referral
                </div>

            </div>

        </div>


        <div class="buttons">

            <button
                class="action primary"
                onclick="daily()"
            >
                🎁 Daily Bonus
            </button>


            <button
                class="action"
                onclick="showReferral()"
            >
                👥 Referral
            </button>


            <button
                class="action"
                onclick="showTop()"
            >
                🏆 TOP 20
            </button>


            <button
                class="action"
                onclick="shareReferral()"
            >
                📤 Ulashish
            </button>

        </div>


        <div
            class="message"
            id="message"
        ></div>


        <div
            class="panel"
            id="refBox"
        >

            <h2>
                👥 Referral
            </h2>

            <div class="ref-description">

                Do‘stlaringizni taklif qiling
                va SHC mukofotlarini oling.

            </div>

            <div
                class="ref-count"
                id="refCount"
            >
                Referral: 0
            </div>

            <div
                class="ref-link"
                id="refLink"
            ></div>

            <button
                class="copy-button"
                onclick="copyReferral()"
            >
                📋 Linkni nusxalash
            </button>

        </div>


        <div
            class="panel"
            id="top"
        >

            <h2>
                🏆 TOP 20
            </h2>

            <div id="topList"></div>

        </div>

    </div>

</div>


<script>

const tg = window.Telegram.WebApp;

tg.ready();
tg.expand();

let user = null;
let referralLink = "";


function message(text) {

    document.getElementById("message").innerText = text;

}


async function api(url, options = {}) {

    options.headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
    };

    return fetch(url, options);

}


async function start() {

    try {

        user = tg.initDataUnsafe.user;

        if (!user) {

            document.getElementById("loading").innerHTML =
                '<div class="error">' +
                '❌ Telegram bot ichidan oching.' +
                '</div>';

            return;
        }


        const startParam =
            tg.initDataUnsafe.start_param || "";


        const response = await api(
            "/api/me",
            {
                method: "POST",

                body: JSON.stringify({
                    user: user,
                    start_param: startParam
                })
            }
        );


        const data = await response.json();


        if (!data.success) {

            document.getElementById("loading").innerHTML =
                '<div class="error">' +
                '❌ ' +
                (data.error || "Xatolik yuz berdi") +
                '</div>';

            return;
        }


        document.getElementById("loading")
            .classList.add("hidden");

        document.getElementById("app")
            .classList.remove("hidden");


        update(data.user);

    }

    catch (error) {

        console.error(error);

        document.getElementById("loading").innerHTML =
            '<div class="error">' +
            '❌ Server bilan ulanishda xatolik.' +
            '</div>';

    }

}


function update(data) {

    document.getElementById("balance").innerText =
        data.balance;

    document.getElementById("clicks").innerText =
        data.clicks;

    document.getElementById("refs").innerText =
        data.referral_count;

}


async function clickCoin() {

    const button =
        document.getElementById("clickButton");

    button.disabled = true;


    try {

        const response = await api(
            "/api/click",
            {
                method: "POST",

                body: JSON.stringify({
                    user: user
                })
            }
        );


        const data = await response.json();


        if (!data.success) {

            message(data.error || "Xatolik");
            return;

        }


        document.getElementById("balance").innerText =
            data.balance;

        document.getElementById("clicks").innerText =
            data.clicks;

    }

    catch (error) {

        console.error(error);
        message("❌ Xatolik yuz berdi");

    }

    finally {

        button.disabled = false;

    }

}


async function daily() {

    try {

        const response = await api(
            "/api/daily",
            {
                method: "POST",

                body: JSON.stringify
