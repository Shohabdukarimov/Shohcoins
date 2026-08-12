[12.08.2026 20:15] Реп))): import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template_string, request
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(name)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")

NEW_USER_BONUS = 100
DAILY_BONUS = 10

REFERRAL_REWARDS = {
    1: 500,
    2: 600,
    3: 700,
    4: 800,
    5: 900,
    10: 1400,
}

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is missing")


def db():
    return psycopg2.connect(DATABASE_URL)


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
                    balance BIGINT DEFAULT 0,
                    clicks BIGINT DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    referred_by BIGINT,
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
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    inviter_id BIGINT NOT NULL,
                    invited_id BIGINT UNIQUE NOT NULL,
                    reward BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

    finally:
        conn.close()


def get_user(telegram_id):
    conn = db()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s",
                (telegram_id,)
            )

            return cur.fetchone()

    finally:
        conn.close()


def create_user(tg_user, referral_id=None):

    telegram_id = int(tg_user["id"])
    username = (tg_user.get("username") or "")[:255]
    first_name = (tg_user.get("first_name") or "")[:255]

    conn = db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s",
                (telegram_id,)
            )

            existing = cur.fetchone()

            if existing:

                cur.execute("""
                    UPDATE users
                    SET username = %s,
                        first_name = %s
                    WHERE telegram_id = %s
                    RETURNING *
                """, (
                    username,
                    first_name,
                    telegram_id
                ))

                user = cur.fetchone()

                conn.commit()

                return user, False

            valid_referrer = None

            if referral_id:

                try:

                    ref_id = int(referral_id)

                    if ref_id != telegram_id:

                        cur.execute(
                            """
                            SELECT telegram_id
                            FROM users
                            WHERE telegram_id = %s
                            """,
                            (ref_id,)
                        )

                        if cur.fetchone():
                            valid_referrer = ref_id
[12.08.2026 20:15] Реп))): except (TypeError, ValueError):
                    pass

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
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    0,
                    %s,
                    %s
                )
                RETURNING *
            """, (
                telegram_id,
                username,
                first_name,
                NEW_USER_BONUS,
                str(telegram_id),
                valid_referrer
            ))

            user = cur.fetchone()

            if valid_referrer:

                cur.execute("""
                    SELECT id
                    FROM referrals
                    WHERE invited_id = %s
                """, (telegram_id,))

                already = cur.fetchone()

                if not already:

                    cur.execute("""
                        SELECT referral_count
                        FROM users
                        WHERE telegram_id = %s
                        FOR UPDATE
                    """, (valid_referrer,))

                    referrer = cur.fetchone()

                    if referrer:

                        count = (referrer["referral_count"] or 0) + 1

                        reward = REFERRAL_REWARDS.get(
                            count,
                            0
                        )

                        cur.execute("""
                            UPDATE users
                            SET referral_count = %s,
                                balance = balance + %s
                            WHERE telegram_id = %s
                        """, (
                            count,
                            reward,
                            valid_referrer
                        ))

                        if count == 5:

                            cur.execute("""
                                UPDATE users
                                SET balance = balance + 500,
                                    bonus_5_given = TRUE
                                WHERE telegram_id = %s
                                  AND bonus_5_given = FALSE
                            """, (valid_referrer,))

                            reward += 500

                        if count == 10:

                            cur.execute("""
                                UPDATE users
                                SET balance = balance + 1000,
                                    bonus_10_given = TRUE
                                WHERE telegram_id = %s
                                  AND bonus_10_given = FALSE
                            """, (valid_referrer,))

                            reward += 1000

                        cur.execute("""
                            INSERT INTO referrals (
                                inviter_id,
                                invited_id,
                                reward
                            )
                            VALUES (
                                %s,
                                %s,
                                %s
                            )
                        """, (
                            valid_referrer,
                            telegram_id,
                            reward
                        ))

            conn.commit()

            return user, True

    except Exception:

        conn.rollback()

        raise

    finally:
        conn.close()


def get_request_user():

    data = request.get_json(silent=True) or {}

    tg_user = data.get("user")

    if not tg_user or not tg_user.get("id"):
        return None

    referral_id = data.get("referral_id")

    user, _ = create_user(
        tg_user,
        referral_id
    )

    return user
[12.08.2026 20:15] Реп))): @app.route("/")
def home():

    return render_template_string(HTML)


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "project": "Shohcoins"
    })


@app.route("/api/me", methods=["POST"])
def me():

    data = request.get_json(silent=True) or {}

    tg_user = data.get("user")

    if not tg_user or not tg_user.get("id"):

        return jsonify({
            "success": False,
            "error": "Telegram user topilmadi"
        }), 400

    user, created = create_user(
        tg_user,
        data.get("referral_id")
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
            "referral_count": user["referral_count"] or 0
        }
    })


@app.route("/api/click", methods=["POST"])
def click():

    user = get_request_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    conn = db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                UPDATE users
                SET balance = balance + 1,
                    clicks = clicks + 1
                WHERE telegram_id = %s
                RETURNING balance, clicks
            """, (
                user["telegram_id"],
            ))

            result = cur.fetchone()

            conn.commit()

            return jsonify({
                "success": True,
                "balance": result["balance"],
                "clicks": result["clicks"]
            })

    finally:
        conn.close()


@app.route("/api/daily", methods=["POST"])
def daily():

    user = get_request_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    today = datetime.now(
        ZoneInfo("Asia/Tashkent")
    ).date()

    conn = db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT last_daily_bonus
                FROM users
                WHERE telegram_id = %s
                FOR UPDATE
            """, (
                user["telegram_id"],
            ))

            row = cur.fetchone()

            if row["last_daily_bonus"] == today:

                conn.rollback()

                return jsonify({
                    "success": False,
                    "error": "Bugungi bonusni oldingiz"
                })

            cur.execute("""
                UPDATE users
                SET balance = balance + %s,
                    last_daily_bonus = %s
                WHERE telegram_id = %s
                RETURNING balance
            """, (
                DAILY_BONUS,
                today,
                user["telegram_id"]
            ))

            result = cur.fetchone()

            conn.commit()

            return jsonify({
                "success": True,
                "bonus": DAILY_BONUS,
                "balance": result["balance"]
            })

    finally:
        conn.close()


@app.route("/api/referral", methods=["POST"])
def referral():

    user = get_request_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    username = BOT_USERNAME.lstrip("@").strip()

    if username == "YOUR_BOT_USERNAME":

        link = ""

    else:

        link = (
            f"https://t.me/{username}"
            f"?start=ref_{user['telegram_id']}"
        )

    return jsonify({
        "success": True,
        "count": user["referral_count"] or 0,
        "link": link
    })


@app.route("/api/top")
def top():

    conn = db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
[12.08.2026 20:15] Реп))): cur.execute("""
                SELECT
                    first_name,
                    username,
                    balance,
                    clicks,
                    referral_count
                FROM users
                ORDER BY balance DESC,
                         clicks DESC
                LIMIT 20
            """)

            users = cur.fetchall()

            return jsonify({
                "success": True,
                "users": users
            })

    finally:
        conn.close()


HTML = r"""
<!DOCTYPE html>
<html lang="uz">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0">

<title>Shohcoins</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

:root{

    --bg:#08090d;
    --card:#151720;
    --card2:#1d202b;
    --gold:#ffbd19;
    --gold2:#ff9500;
    --text:#ffffff;
    --muted:#8e93a3;
    --line:rgba(255,255,255,.07);

}

*{

    box-sizing:border-box;
    -webkit-tap-highlight-color:transparent;

}

body{

    margin:0;
    min-height:100vh;

    color:var(--text);

    font-family:
        Inter,
        Arial,
        Helvetica,
        sans-serif;

    background:
        radial-gradient(
            circle at 50% -10%,
            rgba(255,189,25,.15),
            transparent 38%
        ),
        var(--bg);

}

.container{

    width:100%;
    max-width:520px;

    margin:auto;

    padding:
        18px
        16px
        35px;

}

.header{

    text-align:center;

    padding:
        12px
        0
        20px;

}

.logo{

    width:62px;
    height:62px;

    margin:auto;

    display:flex;

    align-items:center;
    justify-content:center;

    border-radius:50%;

    background:
        linear-gradient(
            145deg,
            #ffd84d,
            #f49a00
        );

    box-shadow:
        0 12px 35px
        rgba(255,174,0,.25);

    font-size:35px;

}

.title{

    margin-top:12px;

    font-size:28px;

    font-weight:900;

    letter-spacing:-1px;

}

.subtitle{

    margin-top:5px;

    color:var(--muted);

    font-size:13px;

}

.card{

    background:
        linear-gradient(
            145deg,
            #181b25,
            #11131a
        );

    border:
        1px solid
        var(--line);

    border-radius:28px;

    padding:25px 20px;

    box-shadow:
        0 15px 40px
        rgba(0,0,0,.22);

}

.balance-title{

    text-align:center;

    color:var(--muted);

    font-size:14px;

}

.balance{

    text-align:center;

    margin:5px 0;

    font-size:64px;

    line-height:1;

    font-weight:900;

    color:var(--gold);

    letter-spacing:-3px;

}

.coin-name{

    text-align:center;

    color:#b5b8c3;

    font-size:14px;

    margin-top:8px;

}

.click{

    width:100%;

    min-height:175px;

    margin-top:18px;

    border:0;

    border-radius:30px;

    color:#111;

    background:
        linear-gradient(
            145deg,
            #ffd22f,
            #ffad00
        );

    box-shadow:
        0 15px 35px
        rgba(255,174,0,.22);

    font-size:29px;

    font-weight:900;

    cursor:pointer;

    transition:.12s;

}

.click span{

    display:block;

    font-size:48px;

    margin-bottom:4px;

}

.click:active{

    transform:scale(.965);

}

.click:disabled{

    opacity:.85;

}

.stats{

    display:grid;

    grid-template-columns:
        1fr 1fr;

    gap:12px;

    margin-top:15px;

}

.stat{

    padding:19px 10px;

    text-align:center;

    background:var(--card);

    border:
        1px solid
        var(--line);

    border-radius:21px;

}

.number{

    font-size:27px;

    font-weight:900;

}

.label{

    margin-top:5px;

    color:var(--muted);

    font-size:13px;

}

.buttons{

    display:grid;

    grid-template-columns:
        1fr 1fr;

    gap:12px;

    margin-top:15px;

}

.buttons button{

    min-height:58px;

    border:
        1px solid
        var(--line);

    border-radius:18px;

    background:var(--card2);

    color:#fff;

    font-size:15px;

    font-weight:800;
[12.08.2026 20:15] Реп))): cursor:pointer;

}

.buttons button:active{

    transform:scale(.97);

}

.message{

    min-height:24px;

    margin:14px 0 0;

    text-align:center;

    color:var(--gold);

    font-weight:700;

}

.panel{

    display:none;

    margin-top:15px;

    padding:20px;

    border-radius:22px;

    background:var(--card);

    border:
        1px solid
        var(--line);

}

.panel h2{

    margin:
        0
        0
        12px;

    font-size:20px;

}

.ref-link{

    padding:13px;

    margin:
        12px
        0;

    border-radius:13px;

    background:#0d0f15;

    color:#cdd0d9;

    font-size:13px;

    word-break:break-all;

}

.action{

    width:100%;

    padding:14px;

    border:0;

    border-radius:14px;

    background:var(--gold);

    color:#111;

    font-size:15px;

    font-weight:900;

}

.player{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:10px;

    margin:8px 0;

    padding:13px;

    border-radius:15px;

    background:#20232e;

}

.player-name{

    overflow:hidden;

    text-overflow:ellipsis;

    white-space:nowrap;

}

.player-balance{

    color:var(--gold);

    font-weight:900;

    white-space:nowrap;

}

.loading{

    padding:50px 10px;

    text-align:center;

    color:var(--muted);

}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <div class="logo">
            🪙
        </div>

        <div class="title">
            SHOHCOINS
        </div>

        <div class="subtitle">
            NotCoin ruhidagi SHC platformasi
        </div>

    </div>


    <div
        id="loading"
        class="loading"
    >
        Telegram aniqlanmoqda...
    </div>


    <div
        id="app"
        style="display:none"
    >

        <div class="card">

            <div class="balance-title">
                Balansingiz
            </div>

            <div
                id="balance"
                class="balance"
            >
                0
            </div>

            <div class="coin-name">
                SHC
            </div>

        </div>


        <button
            class="click"
            id="clickButton"
            onclick="clickCoin()"
        >

            <span>🪙</span>

            +1 SHC

        </button>


        <div class="stats">

            <div class="stat">

                <div
                    id="clicks"
                    class="number"
                >
                    0
                </div>

                <div class="label">
                    Kliklar
                </div>

            </div>


            <div class="stat">

                <div
                    id="refs"
                    class="number"
                >
                    0
                </div>

                <div class="label">
                    Referral
                </div>

            </div>

        </div>


        <div class="buttons">

            <button onclick="daily()">
                🎁 Daily Bonus
            </button>

            <button onclick="showReferral()">
                👥 Referral
            </button>

            <button onclick="showTop()">
                🏆 TOP 20
            </button>

            <button onclick="shareReferral()">
                📤 Ulashish
            </button>

        </div>


        <div
            id="message"
            class="message"
        ></div>


        <div
            id="refBox"
            class="panel"
        >

            <h2>
                👥 Referral
            </h2>

            <div>
                Do‘stlaringizni taklif qiling
                va SHC bonuslar oling.
            </div>

            <div
                id="refLink"
                class="ref-link"
            >
                Referral link yuklanmoqda...
            </div>

            <button
                class="action"
                onclick="copyReferral()"
            >
                📋 Linkni nusxalash
            </button>

        </div>
[12.08.2026 20:15] Реп))): <div
            id="top"
            class="panel"
        >

            <h2>
                🏆 TOP 20
            </h2>

            <div id="topList"></div>

        </div>

    </div>

</div>


<script>

const tg =
    window.Telegram.WebApp;

tg.ready();

tg.expand();


let user = null;

let referralLink = "";


function showMessage(text){

    document.getElementById(
        "message"
    ).innerText = text;

    setTimeout(() => {

        if(
            document.getElementById(
                "message"
            ).innerText === text
        ){

            document.getElementById(
                "message"
            ).innerText = "";

        }

    },3000);

}


async function api(
    url,
    options = {}
){

    options.headers = {

        "Content-Type":
            "application/json",

        ...(options.headers || {})

    };

    const response =
        await fetch(
            url,
            options
        );

    return response.json();

}


function updateUser(data){

    document.getElementById(
        "balance"
    ).innerText =
        data.balance;

    document.getElementById(
        "clicks"
    ).innerText =
        data.clicks;

    document.getElementById(
        "refs"
    ).innerText =
        data.referral_count || 0;

}


async function start(){

    user =
        tg.initDataUnsafe &&
        tg.initDataUnsafe.user;


    if(!user){

        document.getElementById(
            "loading"
        ).innerText =
            "❌ Mini Appni Telegram bot ichidan oching.";

        return;

    }


    const startParam =
        (
            tg.initDataUnsafe &&
            tg.initDataUnsafe.start_param
        ) || "";


    let referralId = "";


    if(
        startParam.startsWith("ref_")
    ){

        referralId =
            startParam.substring(4);

    }


    try{

        const data =
            await api(
                "/api/me",
                {
                    method:"POST",

                    body:
                        JSON.stringify({
                            user:user,
                            referral_id:
                                referralId
                        })
                }
            );


        if(!data.success){

            throw new Error(
                data.error ||
                "Xatolik"
            );

        }


        document.getElementById(
            "loading"
        ).style.display =
            "none";


        document.getElementById(
            "app"
        ).style.display =
            "block";


        updateUser(
            data.user
        );


        if(data.new_user){

            showMessage(
                "🎉 Xush kelibsiz! +100 SHC"
            );

        }

    }catch(error){

        document.getElementById(
            "loading"
        ).innerText =
            "❌ " +
            error.message;

    }

}


async function clickCoin(){

    const button =
        document.getElementById(
            "clickButton"
        );


    button.disabled = true;


    try{

        const data =
            await api(
                "/api/click",
                {
                    method:"POST",

                    body:
                        JSON.stringify({
                            user:user
                        })
                }
            );


        if(!data.success){

            showMessage(
                data.error ||
                "Xatolik"
            );

            return;

        }


        document.getElementById(
            "balance"
        ).innerText =
            data.balance;


        document.getElementById(
            "clicks"
        ).innerText =
            data.clicks;


    }catch(error){

        showMessage(
            "❌ Internet yoki server xatosi"
        );

    }finally{

        setTimeout(
            () => {

                button.disabled =
                    false;

            },
            80
        );

    }

}


async function daily(){

    try{
[12.08.2026 20:15] Реп))): const data =
            await api(
                "/api/daily",
                {
                    method:"POST",

                    body:
                        JSON.stringify({
                            user:user
                        })
                }
            );


        if(!data.success){

            showMessage(
                "⚠️ " +
                data.error
            );

            return;

        }


        document.getElementById(
            "balance"
        ).innerText =
            data.balance;


        showMessage(
            "🎁 +" +
            data.bonus +
            " SHC olindi!"
        );


    }catch(error){

        showMessage(
            "❌ Server xatosi"
        );

    }

}


async function showReferral(){

    try{

        const data =
            await api(
                "/api/referral",
                {
                    method:"POST",

                    body:
                        JSON.stringify({
                            user:user
                        })
                }
            );


        if(!data.success){

            showMessage(
                data.error ||
                "Xatolik"
            );

            return;

        }


        referralLink =
            data.link;


        document.getElementById(
            "refBox"
        ).style.display =
            "block";


        if(referralLink){

            document.getElementById(
                "refLink"
            ).innerText =
                referralLink;

        }else{

            document.getElementById(
                "refLink"
            ).innerText =
                "BOT_USERNAME ni Render Environment Variables ichida kiriting.";

        }

    }catch(error){

        showMessage(
            "❌ Server xatosi"
        );

    }

}


async function copyReferral(){

    if(!referralLink){

        showMessage(
            "⚠️ Referral link hali sozlanmagan"
        );

        return;

    }


    try{

        await navigator.clipboard.writeText(
            referralLink
        );

        showMessage(
            "✅ Referral link nusxalandi!"
        );

    }catch(error){

        showMessage(
            "❌ Nusxalab bo‘lmadi"
        );

    }

}


async function shareReferral(){

    if(!referralLink){

        await showReferral();

    }


    if(!referralLink){

        return;

    }


    const text =
        "🪙 Shohcoins'ga qo‘shiling!\n\n" +
        "SHC yig‘ing va do‘stlaringizni taklif qiling!\n\n" +
        referralLink;


    if(tg.openTelegramLink){

        const shareUrl =
            "https://t.me/share/url?url=" +
            encodeURIComponent(
                referralLink
            ) +
            "&text=" +
            encodeURIComponent(
                "🪙 Shohcoins'ga qo‘shiling!"
            );


        tg.openTelegramLink(
            shareUrl
        );

    }else{

        await navigator.clipboard.writeText(
            text
        );

        showMessage(
            "✅ Link nusxalandi!"
        );

    }

}


async function showTop(){

    try{

        const data =
            await fetch(
                "/api/top"
            ).then(
                r => r.json()
            );


        if(!data.success){

            showMessage(
                "❌ TOP yuklanmadi"
            );

            return;

        }


        const list =
            document.getElementById(
                "topList"
            );


        list.innerHTML = "";


        if(
            !data.users.length
        ){

            list.innerHTML =
                "<div>" +
                "Hozircha foydalanuvchilar yo‘q." +
                "</div>";

        }


        data.users.forEach(
            (player,index) => {

                const row =
                    document.createElement(
                        "div"
                    );


                row.className =
                    "player";
[12.08.2026 20:15] Реп))): const name =
                    player.first_name ||
                    (
                        player.username
                            ? "@" +
                              player.username
                            : "User"
                    );


                row.innerHTML =
                    '<div class="player-name">' +
                    (index + 1) +
                    ". " +
                    escapeHtml(name) +
                    "</div>" +

                    '<div class="player-balance">' +
                    player.balance +
                    " SHC</div>";


                list.appendChild(
                    row
                );

            }
        );


        document.getElementById(
            "top"
        ).style.display =
            "block";


    }catch(error){

        showMessage(
            "❌ TOP yuklanmadi"
        );

    }

}


function escapeHtml(value){

    return String(value)

        .replaceAll(
            "&",
            "&amp;"
        )

        .replaceAll(
            "<",
            "&lt;"
        )

        .replaceAll(
            ">",
            "&gt;"
        )

        .replaceAll(
            '"',
            "&quot;"
        )

        .replaceAll(
            "'",
            "&#039;"
        );

}


start();

</script>

</body>

</html>
"""


if name == "main":

    init_db()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
