from flask import Flask, request, jsonify
import json, os, logging

app = Flask(__name__)
DB_FILE      = "data/documents.json"

# ============================================================
#  CLICK WEBHOOK
# ============================================================
import sys, pathlib
# click_payment.py loyiha ildizida joylashgan
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from click_payment import ClickRequest, handle_prepare, handle_complete

log = logging.getLogger(__name__)

# Bot bazasi joylashuvi (main.py dagi DB_FILE bilan bir xil)
BOT_DB_FILE = os.environ.get("DB_FILE", "/data/bot.db")
if not os.path.exists("/data"):
    BOT_DB_FILE = "data/bot.db"

# Bot callback — to'lov tasdiqlanganda bot foydalanuvchiga xabar yuboradi.
# main.py botni ishga tushirganda shu funksiyani o'rnatadi.
_on_payment_confirmed = None

def set_payment_callback(fn):
    """main.py tomonidan chaqiriladi: fn(user_id, amount) -> coroutine"""
    global _on_payment_confirmed
    _on_payment_confirmed = fn


def _parse_click_form(form) -> ClickRequest:
    """Flask form/JSON dan ClickRequest yaratadi."""
    def g(k, default=""):
        return form.get(k, default)
    return ClickRequest(
        click_trans_id      = int(g("click_trans_id",  0)),
        service_id          = int(g("service_id",       0)),
        click_paydoc_id     = int(g("click_paydoc_id",  0)),
        merchant_trans_id   = g("merchant_trans_id",    "0"),
        amount              = float(g("amount",         0)),
        action              = int(g("action",           0)),
        error               = int(g("error",            0)),
        error_note          = g("error_note",           ""),
        sign_time           = g("sign_time",            ""),
        sign_string         = g("sign_string",          ""),
        merchant_prepare_id = int(g("merchant_prepare_id", 0)) or None,
    )


@app.route("/click/prepare", methods=["POST"])
def click_prepare():
    """Click PREPARE so'rovini qabul qiladi (action=0)."""
    try:
        req = _parse_click_form(request.form or request.json or {})
        result = handle_prepare(req, BOT_DB_FILE)
        return jsonify(result)
    except Exception as e:
        log.exception("Click PREPARE xatosi: %s", e)
        return jsonify({"error": -8, "error_note": "Internal error"}), 500


@app.route("/click/complete", methods=["POST"])
def click_complete():
    """Click COMPLETE so'rovini qabul qiladi (action=1)."""
    try:
        req = _parse_click_form(request.form or request.json or {})
        result = handle_complete(req, BOT_DB_FILE)

        # To'lov muvaffaqiyatli tasdiqlangan — botga xabar berish
        if result.get("error") == 0 and "_user_id" in result:
            user_id = result.pop("_user_id")
            amount  = result.pop("_amount")
            if _on_payment_confirmed:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(_on_payment_confirmed(user_id, amount))
                    else:
                        loop.run_until_complete(_on_payment_confirmed(user_id, amount))
                except Exception as cb_err:
                    log.warning("Payment callback xatosi: %s", cb_err)

        return jsonify(result)
    except Exception as e:
        log.exception("Click COMPLETE xatosi: %s", e)
        return jsonify({"error": -8, "error_note": "Internal error"}), 500

def load_docs():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

HTML_PAGE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DMED Documents</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#fff;min-height:100vh;color:#111827}
header{background:#fff;border-bottom:1px solid #e8edf5;padding:0 32px;height:68px;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px;text-decoration:none}
.logo-icon{width:48px;height:48px}
.logo-text{display:flex;flex-direction:column;line-height:1.1}
.logo-dmed{font-size:26px;font-weight:800;color:#1a3a8f}
.logo-docs{font-size:12px;font-weight:600;color:#1e6fd9}
.lang-btn{display:flex;align-items:center;gap:8px;border:1.5px solid #dde3ef;border-radius:50px;padding:8px 16px;background:#fff;font-family:'Inter',sans-serif;font-size:14px;font-weight:600;color:#111827;cursor:pointer}
.flag{display:flex;flex-direction:column;width:24px;height:15px;border-radius:3px;overflow:hidden}
.f1{background:#1B96D4;flex:1}.f2{background:#fff;flex:1}.f3{background:#1EB53A;flex:1}
#pin-page{display:flex;flex-direction:column;align-items:center;padding:80px 20px 60px}
#pin-page h1{font-size:26px;font-weight:700;text-align:center;max-width:440px;margin-bottom:48px;line-height:1.35}
.pin-row{display:flex;gap:12px;margin-bottom:32px}
.pin-input{width:64px;height:68px;border:1.5px solid #d1d9ea;border-radius:14px;background:#fff;font-family:'Inter',sans-serif;font-size:28px;font-weight:700;text-align:center;color:#111827;outline:none;transition:border-color .18s,box-shadow .18s;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.pin-input:focus{border-color:#1e6fd9;box-shadow:0 0 0 3px rgba(30,111,217,.12)}
.btn-open{width:320px;height:52px;border-radius:12px;border:none;background:#e2e8f0;color:#94a3b8;font-family:'Inter',sans-serif;font-size:16px;font-weight:600;cursor:not-allowed;transition:background .2s,color .2s}
.btn-open.active{background:#1e6fd9;color:#fff;cursor:pointer;box-shadow:0 4px 16px rgba(30,111,217,.25)}
.btn-open.active:hover{background:#1557b8}
.error-msg{margin-top:12px;height:20px;color:#ef4444;font-size:13px;font-weight:500;opacity:0;transition:opacity .2s}
.error-msg.show{opacity:1}
@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-8px)}40%{transform:translateX(8px)}60%{transform:translateX(-5px)}80%{transform:translateX(5px)}}
.shake{animation:shake .4s}
.hint-card{margin-top:64px;display:flex;border-radius:16px;overflow:hidden;max-width:500px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08);border:1px solid #e8edf5}
.hint-left{background:#f1f5fb;padding:18px 16px 14px;min-width:160px;display:flex;flex-direction:column;justify-content:space-between;gap:6px}
.doc-lines{display:flex;flex-direction:column;gap:6px}
.dl{height:7px;border-radius:4px;background:#c8d4e8}
.dl.w100{width:100%}.dl.w80{width:80%}.dl.w60{width:60%}.dl.w90{width:90%}
.doc-footer-hint{display:flex;align-items:center;gap:8px;margin-top:8px}
.doc-pin-num{font-size:11px;font-weight:700;color:#6b7a9a;letter-spacing:1px}
.qr-box{width:34px;height:34px;background:#1a2340;border-radius:3px;padding:3px;display:grid;grid-template-columns:repeat(7,1fr);gap:1px}
.qr-box .b{background:#fff;border-radius:1px}.qr-box .w{background:transparent}
.hint-arrow{display:flex;align-items:center;padding:0 8px;background:#f1f5fb;color:#1e6fd9}
.hint-right{padding:20px;flex:1;display:flex;flex-direction:column;justify-content:center;gap:12px;background:#fff}
.hint-text{font-size:13px;font-weight:500;color:#6b7a9a;line-height:1.5}
.pin-badge{display:inline-block;background:#a8d4f5;border-radius:10px;padding:7px 18px;font-size:24px;font-weight:800;color:#1a2340;letter-spacing:2px}
#doc-page{display:none;padding:20px;max-width:820px;margin:0 auto}
.print-btn{display:block;margin:16px auto;background:#1e6fd9;color:#fff;border:none;padding:11px 30px;border-radius:10px;font-family:'Inter',sans-serif;font-size:15px;font-weight:600;cursor:pointer}
.print-btn:hover{background:#1557b8}
.doc-wrap{background:#fff;border:1px solid #bbb;padding:24px 28px;font-size:11.5px;line-height:1.5;font-family:'Times New Roman',Times,serif;color:#000}
.doc-head{display:flex;align-items:flex-start;justify-content:space-between;border-bottom:1px solid #000;padding-bottom:10px;margin-bottom:14px}
.doc-head-left{font-size:10.5px;line-height:1.7;text-align:center}
.doc-head-center{flex:1;display:flex;justify-content:center;padding:0 10px}
.doc-emblem{width:65px;height:65px}
.doc-title{text-align:center;margin-bottom:14px}
.doc-title h2{font-size:12.5px;font-weight:bold;line-height:1.4;margin-bottom:2px}
.doc-title p{font-size:11.5px}
.doc-musassasa{text-align:center;margin-bottom:8px;font-size:11.5px}
.doc-table{width:100%;border-collapse:collapse;margin-top:8px}
.doc-table td{border:1px solid #000;padding:5px 7px;vertical-align:top;font-size:11px;line-height:1.45}
.doc-table .num{width:20px;text-align:center;font-weight:bold;background:#f9f9f9}
.doc-foot{margin-top:16px;border-top:2px solid #000;padding-top:12px;display:flex;align-items:flex-start;gap:16px}
.doc-foot-logo{display:flex;align-items:center;gap:6px;flex-shrink:0}
.doc-foot-logo-name{font-size:18px;font-weight:800;color:#1a3a8f;font-family:'Inter',sans-serif;line-height:1}
.doc-foot-logo-sub{font-size:9px;color:#1e6fd9;font-weight:600;font-family:'Inter',sans-serif}
.doc-foot-text{flex:1;font-size:9px;line-height:1.6;color:#333;font-family:'Inter',sans-serif}
.doc-foot-right{display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0}
.doc-foot-pin{font-size:26px;font-weight:800;color:#000;font-family:'Inter',sans-serif;letter-spacing:2px}
@media print{
  header,.print-btn,#pin-page{display:none!important}
  #doc-page{display:block!important;padding:0;max-width:100%}
  body{background:#fff}
  .doc-wrap{border:none;padding:0}
}
</style>
</head>
<body>

<header>
  <a class="logo" href="/">
    <svg class="logo-icon" viewBox="0 0 48 48" fill="none">
      <rect x="21" y="4" width="6" height="16" rx="3" fill="#1e6fd9"/>
      <rect x="21" y="28" width="6" height="16" rx="3" fill="#1e6fd9"/>
      <rect x="4" y="21" width="16" height="6" rx="3" fill="#00aadd"/>
      <rect x="28" y="21" width="16" height="6" rx="3" fill="#00aadd"/>
      <rect x="9" y="6" width="5" height="16" rx="2.5" fill="#4db8f0" transform="rotate(45 9 6)"/>
      <rect x="27" y="24" width="5" height="16" rx="2.5" fill="#4db8f0" transform="rotate(45 27 24)"/>
      <rect x="34" y="6" width="5" height="16" rx="2.5" fill="#4db8f0" transform="rotate(-45 34 6)"/>
      <rect x="16" y="24" width="5" height="16" rx="2.5" fill="#4db8f0" transform="rotate(-45 16 24)"/>
    </svg>
    <div class="logo-text">
      <span class="logo-dmed">DMED</span>
      <span class="logo-docs">Documents</span>
    </div>
  </a>
  <button class="lang-btn">
    <div class="flag"><div class="f1"></div><div class="f2"></div><div class="f3"></div></div>
    <span>O'zbekcha</span>
  </button>
</header>

<div id="pin-page">
  <h1>Hujjatni ko'rish uchun PIN - kodni kiriting</h1>
  <div class="pin-row" id="pin-row">
    <input class="pin-input" type="text" maxlength="1" inputmode="numeric" autocomplete="off" id="p0">
    <input class="pin-input" type="text" maxlength="1" inputmode="numeric" autocomplete="off" id="p1">
    <input class="pin-input" type="text" maxlength="1" inputmode="numeric" autocomplete="off" id="p2">
    <input class="pin-input" type="text" maxlength="1" inputmode="numeric" autocomplete="off" id="p3">
  </div>
  <button class="btn-open" id="open-btn">Ochish</button>
  <div class="error-msg" id="error-msg">PIN kod noto'g'ri. Qayta urinib ko'ring.</div>
  <div class="hint-card">
    <div class="hint-left">
      <div class="doc-lines">
        <div class="dl w100"></div><div class="dl w80"></div>
        <div class="dl w100"></div><div class="dl w60"></div>
        <div class="dl w90"></div><div class="dl w80"></div>
      </div>
      <div class="doc-footer-hint">
        <span class="doc-pin-num">1234</span>
        <div class="qr-box">
          <div class="b"></div><div class="b"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="b"></div><div class="b"></div>
          <div class="b"></div><div class="w"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="w"></div><div class="b"></div>
          <div class="b"></div><div class="b"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="b"></div><div class="b"></div>
          <div class="w"></div><div class="w"></div><div class="w"></div><div class="b"></div><div class="w"></div><div class="w"></div><div class="w"></div>
          <div class="b"></div><div class="b"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="w"></div><div class="b"></div>
          <div class="b"></div><div class="w"></div><div class="w"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="w"></div>
          <div class="b"></div><div class="b"></div><div class="b"></div><div class="w"></div><div class="b"></div><div class="b"></div><div class="b"></div>
        </div>
      </div>
    </div>
    <div class="hint-arrow">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M5 12h14M13 6l6 6-6 6"/>
      </svg>
    </div>
    <div class="hint-right">
      <p class="hint-text">PIN-kod hujjatning QR-kodi yonida joylashgan</p>
      <div class="pin-badge">1234</div>
    </div>
  </div>
</div>

<div id="doc-page">
  <button class="print-btn" onclick="window.print()">&#128438; Chop etish</button>
  <div id="doc-content"></div>
  <button class="print-btn" onclick="window.print()">&#128438; Chop etish</button>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
(function() {
  var pathParts = window.location.pathname.split('/');
  var DOC_ID = (pathParts[1] === 'doc' && pathParts[2]) ? pathParts[2] : null;

  var p0 = document.getElementById('p0');
  var p1 = document.getElementById('p1');
  var p2 = document.getElementById('p2');
  var p3 = document.getElementById('p3');
  var allInputs = [p0, p1, p2, p3];
  var btn = document.getElementById('open-btn');
  var errEl = document.getElementById('error-msg');

  btn.disabled = true;

  function getPin() {
    return p0.value + p1.value + p2.value + p3.value;
  }

  function refreshBtn() {
    var pin = getPin();
    if (pin.length === 4) {
      btn.disabled = false;
      btn.classList.add('active');
    } else {
      btn.disabled = true;
      btn.classList.remove('active');
    }
  }

  function setupInput(el, nextEl, prevEl) {
    el.addEventListener('input', function() {
      var v = el.value.replace(/[^0-9]/g, '');
      el.value = v ? v[0] : '';
      refreshBtn();
      errEl.classList.remove('show');
      if (el.value && nextEl) {
        nextEl.focus();
      }
    });
    el.addEventListener('keydown', function(e) {
      if (e.key === 'Backspace' && el.value === '' && prevEl) {
        prevEl.value = '';
        prevEl.focus();
        refreshBtn();
      }
      if (e.key === 'Enter') {
        doCheck();
      }
    });
    el.addEventListener('paste', function(e) {
      var text = (e.clipboardData || window.clipboardData).getData('text').replace(/[^0-9]/g, '');
      if (text.length >= 4) {
        p0.value = text[0]; p1.value = text[1]; p2.value = text[2]; p3.value = text[3];
        p3.focus();
        refreshBtn();
      }
      e.preventDefault();
    });
  }

  setupInput(p0, p1, null);
  setupInput(p1, p2, p0);
  setupInput(p2, p3, p1);
  setupInput(p3, null, p2);

  function doCheck() {
    var pin = getPin();
    if (pin.length < 4 || !DOC_ID) return;
    btn.textContent = 'Tekshirilmoqda...';
    btn.disabled = true;

    fetch('/verify', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: DOC_ID, pin: pin})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        showDoc(data.doc, pin);
      } else {
        errEl.textContent = data.error || "PIN kod noto'g'ri";
        errEl.classList.add('show');
        var row = document.getElementById('pin-row');
        row.classList.add('shake');
        setTimeout(function() { row.classList.remove('shake'); }, 400);
        p0.value = ''; p1.value = ''; p2.value = ''; p3.value = '';
        btn.textContent = 'Ochish';
        btn.disabled = true;
        btn.classList.remove('active');
        p0.focus();
      }
    })
    .catch(function() {
      errEl.textContent = 'Xatolik. Internet aloqasini tekshiring.';
      errEl.classList.add('show');
      btn.textContent = 'Ochish';
      refreshBtn();
    });
  }

  btn.addEventListener('click', function() {
    doCheck();
  });

  function xe(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function showDoc(d, pin) {
    document.getElementById('pin-page').style.display = 'none';
    document.getElementById('doc-page').style.display = 'block';
    var docUrl = window.location.href;
    var origin = window.location.origin;

    var h = '<div class="doc-wrap">';
    h += '<div class="doc-head">';
    h += '<div class="doc-head-left">O\'zbekiston Respublikasi Sog\'liqni<br>saqlash vazirligi<br>34 &ndash; sonli oilaviy poliklinika</div>';
    h += '<div class="doc-head-center"><img class="doc-emblem" src="https://upload.wikimedia.org/wikipedia/commons/thumb/8/84/Emblem_of_Uzbekistan.svg/200px-Emblem_of_Uzbekistan.svg.png"></div>';
    h += '<div style="min-width:140px"></div>';
    h += '</div>';

    h += '<div class="doc-title">';
    h += '<h2>Ta\'lim olayotgan shaxslar uchun mehnatga layoqatsizlik ma\'lumotnomasi</h2>';
    h += '<p>Ro\'yhatga olingan sana: ' + xe(d.sana) + '</p>';
    h += '<p><strong>No ' + xe(d.doc_number) + '</strong></p>';
    h += '</div>';
    h += '<div class="doc-musassasa">Tibbiy muassasa nomi: <b>34 &ndash; sonli oilaviy poliklinika</b><br><em>(qaysi muassasa tomonidan berilgan)</em></div>';

    h += '<table class="doc-table">';
    h += '<tr>';
    h += '<td class="num">1</td>';
    h += '<td style="width:40%"><b>Vaqtincha mehnatga layoqatsiz fuqaro haqidagi ma\'lumotlar:</b><br>FISh: ' + xe(d.fish) + '<br>Jinsi: ' + xe(d.jinsi) + '<br>JShShIR: ' + xe(d.jshshr) + '<br>Yoshi: ' + xe(d.yoshi) + ' yosh<br>Bemorga qarindoshligi:</td>';
    h += '<td class="num">1a</td>';
    h += '<td>Bemor bola haqidagi ma\'lumotlar:<br>FISh: &ndash;<br>Jinsi: &ndash;<br>JShShIR: &ndash;<br>Yoshi: &ndash; yosh</td>';
    h += '</tr>';
    h += '<tr><td class="num">2</td><td>Yashash manzili: ' + xe(d.yashash) + '</td><td class="num">3</td><td>Ish/o\'qish joyi: ' + xe(d.ish_joyi) + '</td></tr>';
    h += '<tr><td class="num">4</td><td>Biriktirilgan tibbiy muassasa:<br>34 &ndash; sonli oilaviy poliklinika</td><td class="num">5</td><td>Mehnatga layoqatsizlik sababi:<br>Kasallik</td></tr>';
    h += '<tr><td class="num">6</td><td>Tashxis (KXT-10 kodi va Nomi):<br>' + xe(d.tashxis) + '</td><td class="num">7</td><td>Davolovchi shifokor FISh: ' + xe(d.shifokor) + '<br>Bo\'lim boshlig\'i (mas\'ul shaxs) FISh:<br>' + xe(d.boshlik) + '</td></tr>';
    h += '<tr><td class="num">8</td><td>Yakuniy tashxis (Nomi va KXT-10 kodi):<br>' + xe(d.tashxis).toUpperCase() + '</td><td class="num">9</td><td>VMK raisining FISh:</td></tr>';
    h += '<tr><td class="num">10</td><td>Yuqumli kasallikka chalingan bemor bilan<br>kontaktda bo\'lganligi haqidagi ma\'lumotlar: Yo\'q</td><td class="num">11</td><td>TIEK ma\'lumotlari:<br>Ko\'rikdan o\'tgan sanasi:<br>Xulosa:<br>TIEK raisi FISh:</td></tr>';
    h += '<tr><td class="num">12</td><td>Tartib: Ambulator<br>Tartib buzilganlik to\'g\'risida qaydlar: &ndash;</td><td class="num">13</td><td>Ishdan ozod etilgan kunlar:<br>' + xe(d.boshlanish) + ' &ndash; ' + xe(d.tugash) + '</td></tr>';
    h += '<tr><td class="num">14</td><td>Vaqtincha boshqa ishga o\'tkazilsin: Yo\'q</td><td class="num">15</td><td>Boshqa shahardan kelgan bemorga mehnatga<br>layoqatsizlik varaqasini berish uchun ruhsat etiladi: Yo\'q</td></tr>';
    h += '<tr><td class="num">16</td><td>Ushbu ma\'lumotnoma berilgan muassasa:<br>' + xe(d.muassasa) + '</td><td class="num">17</td><td>Muassasa nomi: \u0422\u0414\u0418\u0423</td></tr>';
    h += '</table>';

    h += '<div class="doc-foot">';
    h += '<div class="doc-foot-logo">';
    h += '<svg viewBox="0 0 48 48" fill="none" style="width:30px;height:30px"><rect x="21" y="4" width="6" height="16" rx="3" fill="#1e6fd9"/><rect x="21" y="28" width="6" height="16" rx="3" fill="#1e6fd9"/><rect x="4" y="21" width="16" height="6" rx="3" fill="#00aadd"/><rect x="28" y="21" width="16" height="6" rx="3" fill="#00aadd"/></svg>';
    h += '<div><div class="doc-foot-logo-name">DMED</div><div class="doc-foot-logo-sub">Documents</div></div>';
    h += '</div>';
    h += '<div class="doc-foot-text">Hujjat DMED Yagona tibbiy axborot tizimida yaratilgan. Hujjatning haqqoniyligini ' + origin + '/doc/' + DOC_ID + ' saytida hujjatning ID kodini kiritish, yoki QR-kod orqali tekshirish mumkin.<br><b>Hujjat ID:</b> ' + DOC_ID + '<br><b>Yaratilgan sana:</b> ' + xe(d.sana) + '</div>';
    h += '<div class="doc-foot-right"><div class="doc-foot-pin">' + pin + '</div><div id="qr-box"></div></div>';
    h += '</div>';
    h += '</div>';

    document.getElementById('doc-content').innerHTML = h;

    setTimeout(function() {
      new QRCode(document.getElementById('qr-box'), {
        text: docUrl, width: 80, height: 80,
        colorDark: '#000', colorLight: '#fff',
        correctLevel: QRCode.CorrectLevel.M
      });
    }, 100);
  }

  if (DOC_ID) p0.focus();
})();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return HTML_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route("/doc/<doc_id>")
def doc_page(doc_id):
    docs = load_docs()
    if doc_id not in docs:
        return "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px'>Hujjat topilmadi</h2>", 404
    return HTML_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
    doc_id = data.get("doc_id")
    pin = data.get("pin")
    docs = load_docs()
    doc = docs.get(doc_id)
    if not doc:
        return jsonify({"ok": False, "error": "Hujjat topilmadi"})
    if doc.get("pin") != pin:
        return jsonify({"ok": False, "error": "PIN kod noto'g'ri"})
    return jsonify({"ok": True, "doc": doc["data"]})

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
