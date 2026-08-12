import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")

NEW_USER_BONUS = 100

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

    # Eski users jadvali bo'lsa ham saqlanadi
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

    # Eski bazaga yangi ustunlar qo'shiladi
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

    # Referral tarixini alohida saqlaymiz
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


def get_user(telegram_id):
    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE telegram_id=%s",
        (telegram_id,)
    )

    user = cur.fetchone()

    cur.close()
    conn.close()

    return user


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

    # User oldin mavjud bo'lsa yangi bonus berilmaydi
    if user:
        cur.close()
        conn.close()
        return user, False

    referral_code = str(telegram_id)

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

    # Yangi userga 100 SHC
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

    # Referral haqiqiy bo'lsa
    if valid_referrer:

        # Shu user oldin referral sifatida ishlatilmaganini tekshiramiz
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

                # Oddiy referral mukofoti
                reward = REFERRAL_REWARDS.get(count, 0)

                cur.execute("""
                    UPDATE users
                    SET referral_count=%s,
                        balance=balance + %s
                    WHERE telegram_id=%s
                """, (
                    count,
                    reward,
                    valid_referrer
                ))

                # 5-referral maxsus bonus
                if count == 5:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance + 500,
                            bonus_5_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_5_given=FALSE
                    """, (valid_referrer,))

                    reward += 500

                # 10-referral maxsus bonus
                if count == 10:

                    cur.execute("""
                        UPDATE users
                        SET balance=balance + 1000,
                            bonus_10_given=TRUE
                        WHERE telegram_id=%s
                          AND bonus_10_given=FALSE
                    """, (valid_referrer,))

                    reward += 1000

                # Referral tarixiga yozamiz
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

    user, _ = create_user(tg_user)

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

    user, created = create_user(tg_user)

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
        SET balance=balance+1,
            clicks=clicks+1
        WHERE telegram_id=%s
        RETURNING balance, clicks
    """, (user["telegram_id"],))

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
    """, (user["telegram_id"],))

    row = cur.fetchone()

    if row["last_daily_bonus"] == today:

        cur.close()
        conn.close()

        return jsonify({
            "success": False,
            "error": "Bugungi bonusni oldingiz"
        })

    bonus = 10

    cur.execute("""
        UPDATE users
        SET balance=balance+%s,
            last_daily_bonus=%s
        WHERE telegram_id=%s
        RETURNING balance
    """, (
        bonus,
        today,
        user["telegram_id"]
    ))

    result = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success": True,
        "bonus": bonus,
        "balance": result["balance"]
    })


# =========================================================
# REFERRAL INFO
# =========================================================

@app.route("/api/referral", methods=["POST"])
def referral():

    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Telegram orqali kiring"
        }), 400

    referral_code = str(user["telegram_id"])

    bot_username = os.getenv(
        "BOT_USERNAME",
        "YOUR_BOT_USERNAME"
    )

    link = (
        f"https://t.me/{bot_username}"
        f"?start=ref_{referral_code}"
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
content="width=device-width, initial-scale=1.0">

<title>Shohcoins</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#0c0c12;
    color:white;
    font-family:Arial,sans-serif;
}

.container {
    max-width:500px;
    margin:auto;
    padding:20px;
}

.title {
    text-align:center;
    font-size:30px;
    font-weight:bold;
    margin:20px 0;
}

.card {
    background:#181820;
    border-radius:28px;
    padding:25px;
    margin-bottom:20px;
}

.balance-title {
    text-align:center;
    color:#aaa;
}

.balance {
    text-align:center;
    color:#ffc21c;
    font-size:70px;
    font-weight:bold;
    margin:10px 0;
}

.click {
    width:100%;
    height:180px;
    border:0;
    border-radius:30px;
    background:#ffb900;
    font-size:35px;
    font-weight:bold;
}

.click:active {
    transform:scale(.97);
}

.stats {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:15px;
    margin:20px 0;
}

.stat {
    background:#181820;
    border-radius:22px;
    padding:20px;
    text-align:center;
}

.number {
    font-size:30px;
    font-weight:bold;
}

.label {
    color:#aaa;
    margin-top:8px;
}

.buttons {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
}

.buttons button {
    border:0;
    padding:18px 10px;
    border-radius:18px;
    background:#24242e;
    color:white;
    font-size:16px;
    font-weight:bold;
}

.message {
    text-align:center;
    margin:20px 0;
    color:#ffcc33;
}

.ref-box {
    display:none;
    background:#181820;
    padding:20px;
    border-radius:22px;
    margin-top:20px;
}

.ref-link {
    background:#25252f;
    padding:12px;
    border-radius:12px;
    word-break:break-all;
    margin:10px 0;
}

.top {
    display:none;
    margin-top:20px;
}

.player {
    display:flex;
    justify-content:space-between;
    background:#20202a;
    padding:15px;
    margin:7px 0;
    border-radius:14px;
}

</style>

</head>

<body>

<div class="container">

<div class="title">
🪙 SHOHCOINS
</div>

<div id="loading" style="text-align:center">
Telegram aniqlanmoqda...
</div>

<div id="app" style="display:none">

<div class="card">

<div class="balance-title">
Balansingiz
</div>

<div class="balance" id="balance">
0
</div>

<div class="balance-title">
SHC
</div>

</div>

<button class="click" onclick="clickCoin()">
🪙<br>+1 SHC
</button>

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

<button onclick="daily()">
🎁 Daily Bonus
</button>

<button onclick="showReferral()">
👥 Referral
</button>

<button onclick="showTop()">
🏆 TOP
</button>

<button onclick="shareReferral()">
📤 Ulashish
</button>

</div>

<div class="message" id="message"></div>

<div class="ref-box" id="refBox">

<h3>👥 Referral</h3>

<div>
Har bir yangi qo‘shilgan odamga
<strong>100 SHC</strong> beriladi.
</div>

<div class="ref-link" id="refLink"></div>

<button
onclick="copyReferral()"
style="
width:100%;
padding:14px;
border:0;
border-radius:14px;
background:#ffb900;
font-weight:bold;
">
📋 Linkni nusxalash
</button>

</div>

<div class="top" id="top">

<h2>🏆 TOP 20</h2>

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


function message(text) {

document.getElementById("message")
.innerText = text;

}


async function api(url, options={}) {

options.headers = {
"Content-Type":"application/json",
...(options.headers || {})
};

return fetch(url, options);

}


async function start() {

user = tg.initDataUnsafe.user;

if (!user) {

document.getElementById("loading")
.innerText =
"❌ Telegram bot ichidan oching.";

return;

}


const response =
await api("/api/me", {

method:"POST",

body:JSON.stringify({
user:user
})

});


const data =
await response.json();


if (!data.success) {

document.getElementById("loading")
.innerText =
"❌ Xatolik";

return;

}


document.getElementById("loading")
.style.display="none";

document.getElementById("app")
.style.display="block";


update(data.user);

}


function update(data) {

document.getElementById("balance")
.innerText=data.balance;

document.getElementById("clicks")
.innerText=data.clicks;

document.getElementById("refs")
.innerText=data.referral_count;

}


async function clickCoin() {

const response =
await api("/api/click", {

method:"POST",

body:JSON.stringify({
user:user
})

});

const data =
await response.json();


if (!data.success) {

message(data.error);
return;

}


document.getElementById("balance")
.innerText=data.balance;

document.getElementById("clicks")
.innerText=data.clicks;

}


async function daily() {

const response =
await api("/api/daily", {

method:"POST",

body:JSON.stringify({
user:user
})

});

const data =
await response.json();


if (!data.success) {

message(data.error);
return;

}


document.getElementById("balance")
.innerText=data.balance;

message(
"🎁 +"+data.bonus+" SHC olindi!"
);

}


async function showReferral() {

const response =
await api("/api/referral", {

method:"POST",

body:JSON.stringify({
user:user
})

});

const data =
await response.json();


if (!data.success) return;


referralLink=data.link;

document.getElementById("refLink")
.innerText=data.link;

document.getElementById("refBox")
.style.display="block";

}


function copyReferral() {

navigator.clipboard.writeText(
referralLink
);

message(
"✅ Referral link nusxalandi!"
);

}


function shareReferral() {

if (!referralLink) {

showReferral();
return;

}

const text =
"🪙 Shohcoins'ga qo‘shiling!\n\n"+
referralLink;

if (navigator.share) {

navigator.share({
title:"Shohcoins",
text:text
});

} else {

navigator.clipboard.writeText(
referralLink
);

message(
"✅ Link nusxalandi!"
);

}

}


async function showTop() {

const response =
await fetch("/api/top");

const data =
await response.json();


if (!data.success) return;


const top =
document.getElementById("top");

const list =
document.getElementById("topList");

list.innerHTML="";


data.users.forEach(
(player,index)=>{

const row =
document.createElement("div");

row.className="player";

const name =
player.first_name ||
player.username ||
"User";

row.innerHTML =
"<span>"+
(index+1)+". "+
name+
"</span>"+
"<b>"+
player.balance+
" SHC</b>";

list.appendChild(row);

});


top.style.display="block";

}


start();

</script>

</body>

HTML = r"""
<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">

<title>SHOCOINS</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>
*{
    box-sizing:border-box;
    -webkit-tap-highlight-color:transparent;
}

:root{
    --bg:#07080d;
    --card:#11131b;
    --card2:#171a24;
    --gold:#ffc400;
    --gold2:#ff9f00;
    --text:#ffffff;
    --muted:#858b9a;
    --border:rgba(255,255,255,.07);
}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        radial-gradient(circle at 50% -10%,rgba(255,196,0,.16),transparent 35%),
        var(--bg);
}

.container{
    width:100%;
    max-width:520px;
    margin:auto;
    padding:18px 16px 35px;
}

.header{
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin-bottom:18px;
}

.logo{
    display:flex;
    align-items:center;
    gap:10px;
}

.logo-coin{
    width:43px;
    height:43px;
    border-radius:50%;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:23px;
    background:linear-gradient(145deg,#ffe16a,#ff9d00);
    box-shadow:0 8px 25px rgba(255,174,0,.25);
}

.logo-text{
    font-size:20px;
    font-weight:900;
    letter-spacing:1.5px;
}

.logo-text span{
    color:var(--gold);
}

.status{
    padding:7px 11px;
    border-radius:20px;
    background:rgba(74,222,128,.09);
    color:#65e39a;
    font-size:11px;
    font-weight:bold;
}

.profile{
    background:linear-gradient(145deg,var(--card2),var(--card));
    border:1px solid var(--border);
    border-radius:24px;
    padding:15px;
    display:flex;
    align-items:center;
    gap:12px;
    margin-bottom:15px;
}

.avatar{
    width:48px;
    height:48px;
    border-radius:50%;
    display:flex;
    align-items:center;
    justify-content:center;
    background:linear-gradient(145deg,#ffd84a,#ff9800);
    color:#111;
    font-size:22px;
    font-weight:900;
}

.profile-name{
    font-weight:800;
    font-size:16px;
}

.profile-id{
    color:var(--muted);
    font-size:12px;
    margin-top:4px;
}

.balance-card{
    position:relative;
    overflow:hidden;
    background:
        radial-gradient(circle at 85% 15%,rgba(255,196,0,.17),transparent 35%),
        linear-gradient(145deg,#171a24,#0f1118);
    border:1px solid rgba(255,196,0,.14);
    border-radius:30px;
    padding:25px 20px;
    text-align:center;
    box-shadow:0 20px 50px rgba(0,0,0,.25);
}

.balance-label{
    color:var(--muted);
    font-size:13px;
    font-weight:bold;
    text-transform:uppercase;
    letter-spacing:1.5px;
}

.balance{
    margin:7px 0 2px;
    font-size:58px;
    line-height:1;
    font-weight:900;
    letter-spacing:-2px;
    background:linear-gradient(180deg,#ffe66b,#ffad00);
    -webkit-background-clip:text;
    background-clip:text;
    color:transparent;
}

.currency{
    color:#aaa;
    font-size:13px;
    font-weight:bold;
}

.click-area{
    display:flex;
    justify-content:center;
    margin:22px 0;
}

.click{
    width:210px;
    height:210px;
    border:0;
    border-radius:50%;
    cursor:pointer;
    color:#16120a;
    font-weight:900;
    background:
        radial-gradient(circle at 35% 25%,#fff1a6,#ffd33d 35%,#ffad00 70%,#f28b00);
    box-shadow:
        0 0 0 10px rgba(255,196,0,.06),
        0 0 0 22px rgba(255,196,0,.025),
        0 18px 50px rgba(255,166,0,.25);
    transition:.12s;
}

.click:active{
    transform:scale(.93);
}

.click-icon{
    font-size:50px;
    display:block;
    margin-bottom:4px;
}

.click-text{
    font-size:24px;
}

.click-sub{
    font-size:11px;
    opacity:.65;
    margin-top:4px;
}

.stats{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
    margin-bottom:15px;
}

.stat{
    background:var(--card);
    border:1px solid var(--border);
    border-radius:20px;
    padding:17px;
}

.stat-top{
    display:flex;
    align-items:center;
    gap:9px;
    color:var(--muted);
    font-size:12px;
    font-weight:bold;
}

.stat-number{
    margin-top:8px;
    font-size:25px;
    font-weight:900;
}

.menu{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
}

.menu button{
    min-height:68px;
    border:1px solid var(--border);
    border-radius:20px;
    background:linear-gradient(145deg,#181b25,#11131b);
    color:#fff;
    font-size:14px;
    font-weight:800;
    cursor:pointer;
    transition:.12s;
}

.menu button:active{
    transform:scale(.96);
}

.menu-icon{
    display:block;
    font-size:24px;
    margin-bottom:6px;
}

.message{
    min-height:24px;
    text-align:center;
    color:var(--gold);
    font-size:13px;
    font-weight:bold;
    margin:15px 0 0;
}

.ref-box,
.top{
    display:none;
    margin-top:15px;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:23px;
    padding:20px;
}

.section-title{
    margin:0 0 12px;
    font-size:19px;
}

.ref-info{
    color:var(--muted);
    font-size:13px;
    line-height:1.5;
}

.ref-link{
    margin:14px 0;
    padding:13px;
    border-radius:14px;
    background:#080a0f;
    border:1px solid var(--border);
    color:#ffd23d;
    font-size:12px;
    word-break:break-all;
}

.gold-button{
    width:100%;
    border:0;
    border-radius:15px;
    padding:14px;
    background:linear-gradient(135deg,#ffd83d,#ff9c00);
    color:#15110a;
    font-weight:900;
    font-size:14px;
}

.player{
    display:flex;
    align-items:center;
    justify-content:space-between;
    padding:13px;
    margin:7px 0;
    border-radius:15px;
    background:#181b24;
}

.player-left{
    display:flex;
    align-items:center;
    gap:9px;
}

.rank{
    width:28px;
    height:28px;
    border-radius:9px;
    display:flex;
    align-items:center;
    justify-content:center;
    background:#252936;
    font-size:12px;
    font-weight:900;
}

.player-name{
    font-size:13px;
    font-weight:700;
}

.player-coins{
    color:var(--gold);
    font-size:13px;
    font-weight:900;
}

#loading{
    padding:80px 20px;
    text-align:center;
    color:var(--muted);
    font-size:14px;
}

.footer{
    text-align:center;
    color:#555b69;
    font-size:10px;
    margin-top:25px;
}
</style>
</head>

<body>

<div class="container">

    <div id="loading">
        🪙 Telegram foydalanuvchisi aniqlanmoqda...
    </div>

    <div id="app" style="display:none">

        <div class="header">
            <div class="logo">
                <div class="logo-coin">🪙</div>
                <div class="logo-text">SHO<span>COINS</span></div>
            </div>

            <div class="status">● ONLINE</div>
        </div>

        <div class="profile">
            <div class="avatar" id="avatar">S</div>

            <div>
                <div class="profile-name" id="profileName">
                    Shohcoins User
                </div>

                <div class="profile-id" id="profileId">
                    Telegram user
                </div>
            </div>
        </div>

        <div class="balance-card">

            <div class="balance-label">
                Sizning balansingiz
            </div>

            <div class="balance" id="balance">
                0
            </div>

            <div class="currency">
                SHC COINS
            </div>

        </div>

        <div class="click-area">

            <button class="click" onclick="clickCoin()">

                <span class="click-icon">🪙</span>

                <span class="click-text">
                    +1 SHC
                </span>

                <span class="click-sub">
                    BOSING VA YIG‘ING
                </span>

            </button>

        </div>

        <div class="stats">

            <div class="stat">

                <div class="stat-top">
                    🖱️ JAMI KLIKLAR
                </div>

                <div class="stat-number" id="clicks">
                    0
                </div>

            </div>

            <div class="stat">

                <div class="stat-top">
                    👥 REFERRALLAR
                </div>

                <div class="stat-number" id="refs">
                    0
                </div>

            </div>

        </div>

        <div class="menu">

            <button onclick="daily()">
                <span class="menu-icon">🎁</span>
                Daily Bonus
            </button>

            <button onclick="showReferral()">
                <span class="menu-icon">👥</span>
                Referral
            </button>

            <button onclick="showTop()">
                <span class="menu-icon">🏆</span>
                TOP 20
            </button>

            <button onclick="shareReferral()">
                <span class="menu-icon">📤</span>
                Ulashish
            </button>

        </div>

        <div class="message" id="message"></div>

        <div class="ref-box" id="refBox">

            <h3 class="section-title">
                👥 Referral
            </h3>

            <div class="ref-info">
                Do‘stlaringizni taklif qiling va SHC yig‘ing.
                Yangi qo‘shilgan foydalanuvchiga
                <b>100 SHC</b> beriladi.
            </div>

            <div class="ref-link" id="refLink"></div>

            <button class="gold-button" onclick="copyReferral()">
                📋 LINKNI NUSXALASH
            </button>

        </div>

        <div class="top" id="top">

            <h3 class="section-title">
                🏆 TOP 20
            </h3>

            <div id="topList"></div>

        </div>

        <div class="footer">
            SHOCOINS • Telegram Mini App
        </div>

    </div>

</div>

<script>

const tg = window.Telegram.WebApp;

tg.ready();
tg.expand();

let user = null;
let referralLink = "";

function message(text){

    document.getElementById("message").innerText = text;

}

async function api(url, options={}){

    options.headers = {
        "Content-Type":"application/json",
        ...(options.headers || {})
    };

    return fetch(url, options);

}

async function start(){

    user = tg.initDataUnsafe.user;

    if(!user){

        document.getElementById("loading").innerText =
            "❌ Saytni Telegram bot ichidan oching.";

        return;

    }

    const response = await api("/api/me",{

        method:"POST",

        body:JSON.stringify({
            user:user
        })

    });

    const data = await response.json();

    if(!data.success){

        document.getElementById("loading").innerText =
            "❌ Foydalanuvchini aniqlab bo‘lmadi.";

        return;

    }

    document.getElementById("loading").style.display="none";
    document.getElementById("app").style.display="block";

    update(data.user);

}

function update(data){

    document.getElementById("balance").innerText =
        data.balance;

    document.getElementById("clicks").innerText =
        data.clicks;

    document.getElementById("refs").innerText =
        data.referral_count;

    const name =
        data.first_name ||
        data.username ||
        "Shohcoins User";

    document.getElementById("profileName").innerText =
        name;

    document.getElementById("profileId").innerText =
        data.username
        ? "@"+data.username
        : "Telegram ID: "+data.telegram_id;

    document.getElementById("avatar").innerText =
        name.charAt(0).toUpperCase();

}

async function clickCoin(){

    if(!user) return;

    const response = await api("/api/click",{

        method:"POST",

        body:JSON.stringify({
            user:user
        })

    });

    const data = await response.json();

    if(!data.success){

        message(data.error || "Xatolik");
        return;

    }

    document.getElementById("balance").innerText =
        data.balance;

    document.getElementById("clicks").innerText =
        data.clicks;

}

async function daily(){

    const response = await api("/api/daily",{

        method:"POST",

        body:JSON.stringify({
            user:user
        })

    });

    const data = await response.json();

    if(!data.success){

        message(data.error || "Bonus olinmadi");
        return;

    }

    document.getElementById("balance").innerText =
        data.balance;

    message("🎁 +"+data.bonus+" SHC olindi!");

}

async function showReferral(){

    const response = await api("/api/referral",{

        method:"POST",

        body:JSON.stringify({
            user:user
        })

    });

    const data = await response.json();

    if(!data.success){

        message("Referralni yuklab bo‘lmadi.");
        return;

    }

    referralLink = data.link;

    document.getElementById("refLink").innerText =
        referralLink;

    document.getElementById("refBox").style.display =
        "block";

    document.getElementById("top").style.display =
        "none";

}

function copyReferral(){

    if(!referralLink) return;

    navigator.clipboard.writeText(referralLink);

    message("✅ Referral link nusxalandi!");

}

function shareReferral(){

    if(!referralLink){

        showReferral();
        return;

    }

    const text =
        "🪙 SHOCOINS'ga qo‘shiling!\n\n"+
        "Men bilan birga SHC yig‘ing!\n\n"+
        referralLink;

    if(navigator.share){

        navigator.share({
            title:"SHOCOINS",
            text:text
        });

    }else{

        navigator.clipboard.writeText(
            referralLink
        );

        message("✅ Link nusxalandi!");

    }

}

async function showTop(){

    const response =
        await fetch("/api/top");

    const data =
        await response.json();

    if(!data.success) return;

    const list =
        document.getElementById("topList");

    list.innerHTML = "";

    data.users.forEach((player,index)=>{

        const row =
            document.createElement("div");

        row.className = "player";

        const name =
            player.first_name ||
            player.username ||
            "User";

        row.innerHTML = `
            <div class="player-left">
                <div class="rank">
                    ${index+1}
                </div>

                <div class="player-name">
                    ${name}
                </div>
            </div>

            <div class="player-coins">
                ${player.balance} SHC
            </div>
        `;

        list.appendChild(row);

    });

    document.getElementById("top").style.display =
        "block";

    document.getElementById("refBox").style.display =
        "none";

}

start();

</script>

</body>
</html>
"""


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    init_db()

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
            )
