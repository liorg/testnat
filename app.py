"""
testNat — רואים מה עובר ב-NATS, בלי להפריע לאף טלפון.

מושך credentials מ-whqueue בדיוק כמו קונטיינר טלפון, ומשתמש ב-`inbound.py`
**האמיתי** למיפוי — כך שמה שאתה רואה בדף הוא בדיוק מה שה-Manager היה מקבל.

═══════════════════════════════════════════════════════════════════════════
שני מצבים, וההבדל ביניהם קריטי
───────────────────────────────────────────────────────────────────────────

MODE=observe   (ברירת מחדל)
    מנוי core NATS ל-subject. מקבל **עותק** של כל הודעה שמתפרסמת, בלי
    לגעת ב-consumer של JetStream, בלי durable ובלי ack. הטלפון האמיתי
    ממשיך לקבל את ההודעות שלו כרגיל.

    בטוח מול פרודקשן. אבל: רואה רק מה שמתפרסם **בזמן שהוא מחובר** —
    אין היסטוריה ואין replay.

MODE=consume
    pull subscribe אמיתי מ-JetStream, עם durable ו-ack. **גונב הודעות
    מהטלפון**: ה-stream הוא work-queue, והודעה שנמסרה לאחד נעלמת מהשני.

    רק לטלפון בדיקה ייעודי — שורה ב-phones עם phone_number_id שאינו של
    אף מספר אמיתי.
═══════════════════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════════════════
המספר המתואם — PHONE_NUMBER_ID
───────────────────────────────────────────────────────────────────────────
זה **המספר שאתה מקליד בשני מקומות**: כאן ב-env, ובסביבת ה-Postman שממנה
אתה מזריק. הוא מה שמטא שמה ב-`metadata.phone_number_id`, והוא מה שקובע את
ה-subject — `wa.in.111111`.

שני הקצוות נבדקים, ולכן טעות הקלדה נתפסת מיד ולא מתחפשת ל"אין הודעות":

  1. testNat שולח אותו ב-`/register`. whqueue **לא** סומך עליו — הוא לוקח
     את ה-pnid מה-DB ומשווה. אי-התאמה → 409.
  2. testNat משווה את ה-subject שחזר מול המספר הזה. אי-התאמה → לא מתחבר
     בכלל, והדף אומר במה ההבדל.

בלי זה היה אפשר להקליד 11111 בפוסטמן ו-111111 כאן, לראות דף ריק, ולחפש
את הבאג בכל מקום אחר.

env:
    WHQUEUE_BASE_URL       https://whqueue.grossman.bot  (או http://whqueue:8080 בפנים)
    PHONE_ID               phones.id (GUID) — מי אתה מול whqueue
    PHONE_NUMBER_ID        111111 — **המספר המתואם**. אותו ערך בפוסטמן.
    WHQUEUE_PHONE_TOKEN    HMAC(master, "v1:{phone_id}") — או:
    WHQUEUE_MASTER_SECRET  ואז הטוקן נגזר כאן
    MODE                   observe (ברירת מחדל) · consume
    MAX_EVENTS             כמה לשמור בזיכרון, ברירת מחדל 200
    LISTEN_PORT            8000
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import sys
import time
from collections import deque
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, "/app")
import inbound  # noqa: E402  — ה-inbound.py האמיתי מה-image של הטלפון

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s [testnat] %(message)s")
log = logging.getLogger("testnat")

VERSION = "1.0.0"

# אותו חישוב בדיוק כמו ב-main.py של הטלפון. הערכים חייבים להיות זהים —
# הקובץ נמשך מה-image שלו ב-build, ולכן הפרש = testNat נבנה מול tag אחר.
def _inbound_sha() -> str:
    try:
        with open(inbound.__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return "?"


INBOUND_SHA = _inbound_sha()


def env(k: str, d: str = "") -> str:
    v = os.getenv(k)
    return v if v is not None else d


WHQUEUE  = env("WHQUEUE_BASE_URL", "http://whqueue:8080").rstrip("/")
PHONE_ID = env("PHONE_ID")
PNID     = env("PHONE_NUMBER_ID").strip()
MODE     = env("MODE", "observe").strip().lower()
MAX_EV   = int(env("MAX_EVENTS", "200"))
PORT     = int(env("LISTEN_PORT", "8000"))
TOKEN_V  = env("WHQUEUE_TOKEN_VERSION", "1")

_token  = env("WHQUEUE_PHONE_TOKEN")
_master = env("WHQUEUE_MASTER_SECRET")
if not _token and _master and PHONE_ID:
    _token = hmac.new(_master.encode(), f"v{TOKEN_V}:{PHONE_ID}".encode(),
                      hashlib.sha256).hexdigest()

app = FastAPI(title="testNat", version=VERSION)

state: dict = {
    "mode": MODE, "connected": False, "error": None, "creds": None,
    "pnid": PNID or None, "inbound_sha": INBOUND_SHA,
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "seen": 0, "bad": 0,
}
events: deque = deque(maxlen=MAX_EV)
_nc: Any = None


# ── credentials ───────────────────────────────────────────────────────────

async def fetch_creds() -> Optional[dict]:
    if not PHONE_ID:
        state["error"] = "PHONE_ID לא הוגדר"
        return None
    if not _token:
        state["error"] = "אין WHQUEUE_PHONE_TOKEN ואין WHQUEUE_MASTER_SECRET"
        return None
    # שולחים את המספר המתואם כדי ש-whqueue ישווה אותו מול ה-DB. הוא לא סומך
    # עליו כמקור אמת — הוא רק פוסל אי-התאמה ב-409, וזה בדיוק מה שרוצים.
    body: dict = {"phone_id": PHONE_ID}
    if PNID:
        body["phone_number_id"] = PNID
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{WHQUEUE}/register", json=body,
                             headers={"X-Whqueue-Token": _token})
        if r.status_code != 200:
            state["error"] = f"/register → {r.status_code}: {r.text[:200]}"
            if r.status_code == 401:
                state["error"] += "  (ה-master secret לא תואם)"
            if r.status_code == 404:
                state["error"] += "  (הטלפון לא ב-phones או שאינו provider=cloudapi)"
            if r.status_code == 409:
                state["error"] += (f"  (PHONE_NUMBER_ID={PNID} אינו ה-phone_number_id "
                                   f"של PHONE_ID={PHONE_ID} ב-DB)")
            return None
        return r.json()["nats"]
    except Exception as e:  # noqa: BLE001
        state["error"] = f"whqueue לא נגיש: {e}"
        return None


def pnid_mismatch(creds: dict) -> Optional[str]:
    """
    הצד השני של הבדיקה: ה-subject שחזר מ-whqueue מול המספר שהוקלד ב-env.

    ה-subject הוא `{prefix}.{pnid}`, ולכן משווים את החלק שאחרי הנקודה
    האחרונה ולא בונים את הקידומת מחדש — הקידומת היא של whqueue, לא שלנו.
    """
    if not PNID:
        return None
    subject = str(creds.get("inbound_subject") or "")
    got = subject.rsplit(".", 1)[-1]
    if got == PNID:
        return None
    return (f"המספר לא מתואם: ב-env PHONE_NUMBER_ID={PNID}, אבל whqueue הנפיק "
            f"subject={subject} (כלומר {got or '—'}). הזרקה ל-{PNID} לא תגיע לכאן. "
            f"תקן את ה-env או את הערך בפוסטמן — צריך להיות אותו מספר בשניהם.")


def claims(jwt: str) -> dict:
    try:
        b = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except Exception:  # noqa: BLE001
        return {}


# ── קליטה ─────────────────────────────────────────────────────────────────

def record(subject: str, raw: bytes) -> None:
    state["seen"] += 1
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        state["bad"] += 1
        events.appendleft({"at": time.strftime("%H:%M:%S"), "subject": subject,
                           "error": f"JSON פגום: {e}", "raw": raw[:400].decode("utf-8", "replace")})
        return

    # אותו מיפוי בדיוק שהקונטיינר האמיתי מפעיל לפני שהוא שולח ל-Manager
    try:
        mapped = inbound.events_from_value(PHONE_ID, "http://testnat", f"testnat-{VERSION}", value)
    except Exception as e:  # noqa: BLE001
        mapped = [{"error": f"המיפוי נכשל: {e}"}]

    events.appendleft({"at": time.strftime("%H:%M:%S"), "subject": subject,
                       "raw": value, "mapped": mapped})
    for m in mapped:
        log.info("%s · %s %s", subject, m.get("event"), m.get("messageId") or "")


async def run_observe(creds: dict) -> None:
    """מנוי core — עותק של כל פרסום, בלי לגעת ב-stream."""
    import nats  # type: ignore
    import nkeys  # type: ignore

    def jwt_cb() -> bytes:
        return creds["jwt"].encode()

    def sign_cb(nonce: str) -> bytes:
        kp = nkeys.from_seed(creds["seed"].encode())
        try:
            return base64.b64encode(kp.sign(nonce.encode()))
        finally:
            kp.wipe()

    async def on_err(e):
        state["error"] = str(e)
        log.error("nats: %s", e)

    global _nc
    _nc = await nats.connect(
        servers=[s.strip() for s in str(creds["servers"]).split(",") if s.strip()],
        user_jwt_cb=jwt_cb, signature_cb=sign_cb, error_cb=on_err,
        name=f"testnat-{PHONE_ID[:8]}", max_reconnect_attempts=-1, reconnect_time_wait=2)

    subject = creds["inbound_subject"]

    async def handler(msg):
        record(msg.subject, msg.data)

    await _nc.subscribe(subject, cb=handler)
    state["connected"] = True
    log.info("observe · %s · core subscribe — לא נוגע ב-consumer", subject)


async def run_consume(creds: dict) -> None:
    """pull subscribe אמיתי. גונב הודעות מהטלפון — רק לטלפון בדיקה."""
    import nats  # type: ignore
    import nkeys  # type: ignore
    from nats.errors import TimeoutError as NatsTimeout  # type: ignore

    def jwt_cb() -> bytes:
        return creds["jwt"].encode()

    def sign_cb(nonce: str) -> bytes:
        kp = nkeys.from_seed(creds["seed"].encode())
        try:
            return base64.b64encode(kp.sign(nonce.encode()))
        finally:
            kp.wipe()

    global _nc
    _nc = await nats.connect(
        servers=[s.strip() for s in str(creds["servers"]).split(",") if s.strip()],
        user_jwt_cb=jwt_cb, signature_cb=sign_cb,
        name=f"testnat-{PHONE_ID[:8]}", max_reconnect_attempts=-1, reconnect_time_wait=2)

    js = _nc.jetstream()
    sub = await js.pull_subscribe(creds["inbound_subject"],
                                  durable=creds["inbound_durable"],
                                  stream=creds["stream"])
    state["connected"] = True
    log.warning("consume · %s · durable=%s — **גונב הודעות מהטלפון הזה**",
                creds["inbound_subject"], creds["inbound_durable"])

    while True:
        try:
            for msg in await sub.fetch(1, timeout=20):
                record(msg.subject, msg.data)
                await msg.ack()
        except NatsTimeout:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("fetch: %s", e)
            await asyncio.sleep(3)


async def boot() -> None:
    delay = 5
    while True:
        creds = await fetch_creds()
        if creds:
            bad = pnid_mismatch(creds)
            if bad:
                # לא מתחברים בכלל. חיבור ל-subject הלא נכון היה מציג דף ריק
                # ותקין למראה, וזה הכי גרוע — אז עוצרים ואומרים במה ההבדל.
                state["error"] = bad
                state["creds"] = {"subject": creds.get("inbound_subject")}
                log.error("%s", bad)
                await asyncio.sleep(30)
                continue
            state["creds"] = {
                "subject":  creds.get("inbound_subject"),
                "durable":  creds.get("inbound_durable"),
                "stream":   creds.get("stream"),
                "servers":  creds.get("servers"),
                "allow":    ((claims(creds["jwt"]).get("nats") or {}).get("sub") or {}).get("allow"),
                "exp_in_h": round((claims(creds["jwt"]).get("exp", 0) - time.time()) / 3600, 1),
            }
            try:
                if MODE == "consume":
                    asyncio.create_task(run_consume(creds))
                else:
                    await run_observe(creds)
                state["error"] = None
                return
            except Exception as e:  # noqa: BLE001
                state["error"] = f"חיבור ל-NATS נכשל: {e}"
        log.error("%s — מנסה שוב בעוד %ss", state["error"], delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


@app.on_event("startup")
async def _up():
    if MODE not in ("observe", "consume"):
        log.error("MODE=%s לא מוכר — משתמש ב-observe", MODE)
        state["mode"] = "observe"
    asyncio.create_task(boot())


@app.on_event("shutdown")
async def _down():
    if _nc is not None:
        try:
            await _nc.close()
        except Exception:  # noqa: BLE001
            pass


# ── הדף ───────────────────────────────────────────────────────────────────

@app.get("/events")
async def ev() -> JSONResponse:
    return JSONResponse({"state": state, "events": list(events)})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": VERSION, "mode": state["mode"],
            "inbound_sha": INBOUND_SHA,
            "phone_number_id": PNID or None,
            "subject": (state.get("creds") or {}).get("subject"),
            "connected": state["connected"], "seen": state["seen"],
            "error": state["error"]}


PAGE = """<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>testNat</title><style>
:root{--bg:#0f1115;--fg:#e6e6e6;--dim:#8b93a1;--line:#242832;--ok:#4ade80;--warn:#fbbf24;--err:#f87171;--acc:#60a5fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;padding:16px}
h1{font-size:16px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:12px;margin-bottom:14px}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{background:#171a21;border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:12px}
.chip b{color:var(--acc);font-weight:600}
.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}.dim{color:var(--dim)}
.banner{border-radius:6px;padding:10px 12px;margin-bottom:14px;font-size:13px}
.b-obs{background:#0d2818;border:1px solid #1e5f3a}
.b-con{background:#2d1a0a;border:1px solid #7c4a12}
.b-err{background:#2a1214;border:1px solid #7f1d1d}
.ev{border:1px solid var(--line);border-radius:6px;margin-bottom:8px;overflow:hidden}
.ev>summary{cursor:pointer;padding:8px 12px;background:#141821;list-style:none;
display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ev>summary::-webkit-details-marker{display:none}
.t{color:var(--dim);font-size:12px}
.tag{background:#1e2430;border-radius:4px;padding:1px 7px;font-size:11px;color:var(--acc)}
pre{margin:0;padding:10px 12px;background:#0b0d11;overflow-x:auto;font-size:12px;
border-top:1px solid var(--line);white-space:pre-wrap;word-break:break-word}
.empty{color:var(--dim);text-align:center;padding:40px 0}
</style><body>
<h1>testNat</h1>
<div class="sub" id="sub">טוען…</div>
<div id="banner"></div>
<div class="bar" id="bar"></div>
<div id="list"><div class="empty">ממתין להודעות…</div></div>
<script>
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const J = o => esc(JSON.stringify(o, null, 2));

async function tick(){
  let d; try { d = await (await fetch('/events')).json(); }
  catch(e){ document.getElementById('sub').textContent = 'אין קשר לשרת'; return; }
  const s = d.state, c = s.creds || {};

  document.getElementById('sub').innerHTML =
    'עלה ' + esc(s.started_at) + ' · ' +
    (s.connected ? '<span class="ok">מחובר ל-NATS</span>'
                 : '<span class="err">לא מחובר</span>');

  const B = document.getElementById('banner');
  if (s.error) {
    B.className = 'banner b-err';
    B.innerHTML = '<b class="err">✗ ' + esc(s.error) + '</b>';
  } else if (s.mode === 'consume') {
    B.className = 'banner b-con';
    B.innerHTML = '<b class="warn">MODE=consume</b> — צורך מה-stream עם durable ' +
      '<b>' + esc(c.durable||'') + '</b>. work-queue מוסר פעם אחת, ולכן ' +
      'הטלפון האמיתי <b>לא</b> יקבל את מה שנקלט כאן.';
  } else {
    B.className = 'banner b-obs';
    B.innerHTML = '<b class="ok">MODE=observe</b> — מנוי core, עותק בלבד. ' +
      'לא נוגע ב-consumer, לא עושה ack, והטלפון האמיתי ממשיך לקבל הכול. ' +
      '<span class="dim">רואה רק מה שמתפרסם בזמן שהוא מחובר.</span>';
  }

  document.getElementById('bar').innerHTML = [
    ['המספר המתואם', s.pnid], ['inbound.py', s.inbound_sha],
    ['subject', c.subject], ['stream', c.stream],
    ['התקבלו', s.seen], ['פגומים', s.bad],
    ['JWT', (c.exp_in_h ?? '?') + 'ש'],
    ['מותר ב-JWT', (c.allow||[]).join(' · ')]
  ].filter(x => x[1] !== undefined && x[1] !== null && x[1] !== '')
   .map(([k,v]) => '<div class="chip">' + esc(k) + ' <b>' + esc(v) + '</b></div>').join('');

  const L = document.getElementById('list');
  if (!d.events.length) { L.innerHTML = '<div class="empty">ממתין להודעות…</div>'; return; }
  L.innerHTML = d.events.map((e,i) => {
    const m = (e.mapped||[])[0] || {};
    const title = e.error ? '<span class="err">' + esc(e.error) + '</span>'
      : '<span class="tag">' + esc(m.event||'?') + '</span>' +
        (m.type ? '<span class="tag">' + esc(m.type) + '</span>' : '') +
        '<span>' + esc((m.data&&m.data.text) || m.messageId || '') + '</span>';
    return '<details class="ev"' + (i===0?' open':'') + '><summary>' +
      '<span class="t">' + esc(e.at) + '</span>' + title +
      '<span class="t" style="margin-inline-start:auto">' + esc(e.subject) + '</span>' +
      '</summary>' +
      '<pre><b class="dim">מה שה-Manager היה מקבל:</b>\\n' + J(e.mapped) +
      '\\n\\n<b class="dim">מה שהגיע מ-NATS:</b>\\n' + J(e.raw) + '</pre></details>';
  }).join('');
}
tick(); setInterval(tick, 1500);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    return PAGE


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
