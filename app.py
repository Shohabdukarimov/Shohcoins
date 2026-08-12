import os
from datetime import date
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME").strip()

NEW_USER_BONUS = 100
CLICK_REWARD = 1
DAILY_BONUS = 10
REFERRAL_REWARD = 100


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

            # Safe upgrades for databases created by an older version.
            for sql in (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS clicks BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus DATE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            ):
                cur.execute(sql)

        conn.commit()
    finally:
        conn.close()


def get_user(telegram_id, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = db()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s",
                (int(telegram_id),),
            )
            return cur.fetchone()
    finally:
        if own_conn:
            conn.close()


def create_user(tg_user, referral_id=None):
    telegram_id = int(tg_user["id"])
    username = str(tg_user.get("username") or "")
    first_name = str(tg_user.get("first_name") or "")

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s FOR UPDATE",
                (telegram_id,),
            )
            existing = cur.fetchone()

            if existing:
                # Keep Telegram profile information current.
                cur.execute(
                    """
                    UPDATE users
                    SET username = %s, first_name = %s
                    WHERE telegram_id = %s
                    RETURNING *
                    """,
                    (username, first_name, telegram_id),
                )
                user = cur.fetchone()
                conn.commit()
                return user, False

            valid_referrer = None
            if referral_id not in (None, ""):
                try:
                    candidate = int(str(referral_id).strip())
                    if candidate != telegram_id:
                        cur.execute(
                            "SELECT telegram_id FROM users WHERE telegram_id = %s",
                            (candidate,),
                        )
                        if cur.fetchone():
                            valid_referrer = candidate
                except (TypeError, ValueError):
                    valid_referrer = None

            referral_code = str(telegram_id)

            cur.execute(
                """
                INSERT INTO users (
                    telegram_id, username, first_name, balance,
                    clicks, referral_code, referred_by
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

            if valid_referrer:
                # Unique invited_id prevents duplicate referral rewards.
                cur.execute(
                    "SELECT id FROM referrals WHERE invited_id = %s",
                    (telegram_id,),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        """
                        UPDATE users
                        SET referral_count = referral_count + 1,
                            balance = balance + %s
                        WHERE telegram_id = %s
                        RETURNING referral_count
                        """,
                        (REFERRAL_REWARD, valid_referrer),
                    )
                    referrer = cur.fetchone()

                    if referrer:
                        cur.execute(
                            """
                            INSERT INTO referrals (inviter_id, invited_id, reward)
                            VALUES (%s, %s, %s)
                            """,
                            (valid_referrer, telegram_id, REFERRAL_REWARD),
                        )

            conn.commit()
            return user, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_user():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")
    if not tg_user or not tg_user.get("id"):
        return None, data
    return get_user(int(tg_user["id"])), data


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/health")
def health():
    try:
        conn = db()
        conn.close()
        return jsonify({"status": "ok", "database": "ok", "project": "Shohcoins"})
    except Exception as exc:
        return jsonify({"status": "error", "database": str(exc), "project": "Shohcoins"}), 500


@app.route("/api/me", methods=["POST"])
def me():
    data = request.get_json(silent=True) or {}
    tg_user = data.get("user")

    if not tg_user or not tg_user.get("id"):
        return jsonify({"success": False, "error": "Telegram user topilmadi"}), 400

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
        return jsonify({"success": False, "error": f"Server xatosi: {exc}"}), 500


@app.route("/api/click", methods=["POST"])
def click():
    user, _ = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE users
                SET balance = balance + %s,
                    clicks = clicks + 1
                WHERE telegram_id = %s
                RETURNING balance, clicks
                """,
                (CLICK_REWARD, user["telegram_id"]),
            )
            result = cur.fetchone()
            if not result:
                conn.rollback()
                return jsonify({"success": False, "error": "Foydalanuvchi topilmadi"}), 404
        conn.commit()
        return jsonify({"success": True, **dict(result)})
    except Exception as exc:
        conn.rollback()
        app.logger.exception("click error")
        return jsonify({"success": False, "error": f"Server xatosi: {exc}"}), 500
    finally:
        conn.close()


@app.route("/api/daily", methods=["POST"])
def daily():
    user, _ = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    today = date.today()
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT last_daily_bonus
                FROM users
                WHERE telegram_id = %s
                FOR UPDATE
                """,
                (user["telegram_id"],),
            )
            row = cur.fetchone()

            if not row:
                conn.rollback()
                return jsonify({"success": False, "error": "Foydalanuvchi topilmadi"}), 404

            if row["last_daily_bonus"] == today:
                conn.rollback()
                return jsonify({"success": False, "error": "Bugungi bonusni oldingiz 🎁"})

            cur.execute(
                """
                UPDATE users
                SET balance = balance + %s,
                    last_daily_bonus = %s
                WHERE telegram_id = %s
                RETURNING balance
                """,
                (DAILY_BONUS, today, user["telegram_id"]),
            )
            result = cur.fetchone()

        conn.commit()
        return jsonify({
            "success": True,
            "bonus": DAILY_BONUS,
            "balance": result["balance"],
        })
    except Exception as exc:
        conn.rollback()
        app.logger.exception("daily error")
        return jsonify({"success": False, "error": f"Server xatosi: {exc}"}), 500
    finally:
        conn.close()


@app.route("/api/referral", methods=["POST"])
def referral():
    user, _ = request_user()
    if not user:
        return jsonify({"success": False, "error": "Telegram orqali kiring"}), 400

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user['telegram_id']}"
    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "link": link,
        "reward": REFERRAL_REWARD,
    })


@app.route("/api/top")
def top():
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT first_name, username, balance, clicks, referral_count
                FROM users
                ORDER BY balance DESC, clicks DESC, id ASC
                LIMIT 20
                """
            )
            users = cur.fetchall()

        return jsonify({"success": True, "users": users})
    except Exception as exc:
        app.logger.exception("top error")
        return jsonify({"success": False, "error": f"Server xatosi: {exc}"}), 500
    finally:
        conn.close()


def user_json(user):
    return {
        "telegram_id": user["telegram_id"],
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "balance": int(user.get("balance") or 0),
        "clicks": int(user.get("clicks") or 0),
        "referral_count": int(user.get("referral_count") or 0),
    }


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
.container{width:100%;max-width:520px;margin:auto;padding:18px 16px 36px}
.header{text-align:center;padding:10px 0 22px}
.logo{width:68px;height:68px;margin:0 auto 10px;display:grid;place-items:center;border-radius:22px;font-size:38px;background:linear-gradient(145deg,#ffe48a,#ffbf18 55%,#d98d00);box-shadow:0 10px 30px rgba(255,190,20,.22)}
.title{margin:0;font-size:28px;font-weight:900;letter-spacing:1px}
.subtitle{margin-top:6px;color:#9696a2;font-size:13px}
.card{padding:24px 20px;margin-bottom:16px;border:1px solid rgba(255,255,255,.06);border-radius:28px;background:linear-gradient(145deg,#1c1c25,#121218);box-shadow:0 14px 35px rgba(0,0,0,.25);text-align:center}
.balance-title{color:#a7a7b1;font-size:15px}
.balance{margin:8px 0;color:#ffc21c;font-size:64px;line-height:1;font-weight:900}
.currency{color:#d2d2d8;font-size:16px;font-weight:700}
.click{width:100%;min-height:170px;border:0;border-radius:30px;color:#111;cursor:pointer;font-size:28px;font-weight:900;background:linear-gradient(145deg,#ffd34d,#ffb700);box-shadow:0 15px 35px rgba(255,184,0,.2);transition:transform .1s}
.click:active{transform:scale(.97)}
.click:disabled{opacity:.75}
.coin{display:block;margin-bottom:5px;font-size:45px}
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
.copy-button{width:100%;padding:14px;border:0;border-radius:15px;color:#111;background:#ffbf18;font-size:15px;font-weight:900}
.ref-count{margin-top:12px;color:#ffc21c;font-weight:800}
.player{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:8px 0;padding:14px;border-radius:15px;background:#20202a}
.player-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.player-balance{flex-shrink:0;color:#ffc21c;font-weight:900}
.loading{padding:50px 10px;text-align:center;color:#aaa}.error{padding:20px;text-align:center;border-radius:18px;color:#ff8e8e;background:#241719}
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
<span class="coin">🪙</span>+1 SHC
</button>

<div class="stats">
<div class="stat"><div id="clicks" class="number">0</div><div class="label">Kliklar</div></div>
<div class="stat"><div id="refs" class="number">0</div><div class="label">Referral</div></div>
</div>

<div class="buttons">
<button class="action primary" onclick="daily()">🎁 Daily Bonus</button>
<button class="action" onclick="showReferral()">👥 Referral</button>
<button class="action" onclick="showTop()">🏆 TOP 20</button>
<button class="action" onclick="shareReferral()">📤 Ulashish</button>
</div>

<div id="message" class="message"></div>

<div id="refBox" class="panel">
<h2>👥 Referral</h2>
<div class="ref-description">Do‘stlaringizni taklif qiling va har bir yangi referral uchun <b>100 SHC</b> oling.</div>
<div id="refCount" class="ref-count">Referral: 0</div>
<div id="refLink" class="ref-link"></div>
<button class="copy-button" onclick="copyReferral()">📋 Linkni nusxalash</button>
</div>

<div id="top" class="panel">
<h2>🏆 TOP 20</h2>
<div id="topList"></div>
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
        if(tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    }catch(e){msg("❌ Xatolik yuz berdi")}
    finally{btn.disabled=false}
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

async function showReferral(){
    document.getElementById("top").style.display="none";
    const box=document.getElementById("refBox");
    box.style.display="block";
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
    }catch(e){msg("❌ Referralni olishda xatolik")}
}

async function copyReferral(){
    if(!referralLink) await showReferral();
    try{
        await navigator.clipboard.writeText(referralLink);
        msg("✅ Link nusxalandi!");
    }catch(e){
        msg("Linkni qo‘lda nusxalang");
    }
}

async function shareReferral(){
    if(!referralLink) await showReferral();
    const text="🪙 SHOHCOINS ga qo‘shiling! Har kuni SHC ishlang.";
    const shareUrl="https://t.me/share/url?url="+encodeURIComponent(referralLink)+"&text="+encodeURIComponent(text);
    if(tg.openTelegramLink) tg.openTelegramLink(shareUrl);
    else window.open(shareUrl,"_blank");
}

async function showTop(){
    document.getElementById("refBox").style.display="none";
    const panel=document.getElementById("top");
    panel.style.display="block";
    const list=document.getElementById("topList");
    list.innerHTML="<div class='loading'>Yuklanmoqda...</div>";

    try{
        const res=await fetch("/api/top");
        const data=await res.json();
        if(!data.success){list.innerHTML="<div class='error'>Xatolik</div>";return}
        if(!data.users.length){list.innerHTML="<div class='loading'>Hozircha foydalanuvchilar yo‘q.</div>";return}

        list.innerHTML=data.users.map((u,i)=>{
            const name=escapeHtml(u.first_name||u.username||"User");
            return '<div class="player"><div class="player-name">'+(i+1)+'. '+name+'</div><div class="player-balance">'+Number(u.balance||0)+' SHC</div></div>';
        }).join("");
    }catch(e){
        list.innerHTML="<div class='error'>TOP yuklanmadi.</div>";
    }
}

function escapeHtml(value){
    return String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[c]));
}

start();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # Render provides PORT. Binding to 0.0.0.0 is required for deployment.
    port = int(os.getenv("PORT", "10000"))

    try:
        init_db()
        app.logger.info("Database initialized successfully")
    except Exception:
        app.logger.exception("Database initialization failed")
        raise

    app.run(host="0.0.0.0", port=port)
