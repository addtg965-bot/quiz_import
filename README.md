# 🚂 Railway ga Deploy qilish

## 1-qadam: Sessiya fayllarini tayyorlash

Railway serverida interaktiv kod kiritish imkoni yo'q.
Shuning uchun **avval lokal** (kompyuterda) sessiya fayllarini yaratib, keyin Railway ga yuklaymiz.

### Lokal kompyuterda:
```bash
pip install telethon
python create_sessions.py
```

Bu `data/` papkasida `.session` fayllarini yaratadi.

---

## 2-qadam: Railway da yangi loyiha yaratish

1. [railway.app](https://railway.app) ga kiring
2. **New Project** → **Deploy from GitHub repo** yoki **Empty Project**
3. Fayllarni yuklang

---

## 3-qadam: Environment Variables kiritish

Railway Dashboard → Project → **Variables** bo'limiga kiring va `.env.example` dagi barcha qiymatlarni kiriting.

**Majburiy o'zgaruvchilar:**
```
BOT_TOKEN        = @BotFather dan
API_ID           = my.telegram.org dan
API_HASH         = my.telegram.org dan
GROQ_API_KEY     = console.groq.com dan
ADMIN_IDS        = sizning Telegram ID ingiz
NOTIFY_PHONE     = @humocardbot xabar keladigan raqam
PHONE_NUMBERS    = +998901234567,+998901234568
HUMO_CARDS       = 9860 XXXX XXXX 0001,9860 XXXX XXXX 0002
```

---

## 4-qadam: Volume (persistent storage) qo'shish

Railway da ma'lumotlar saqlanishi uchun **Volume** kerak:

1. Project → **Add Volume**
2. Mount path: `/data`
3. Sessiya fayllarini Volume ga yuklang

---

## 5-qadam: Sessiya fayllarini yuklash

```bash
# Railway CLI orqali
railway run cp data/*.session /data/
```

Yoki Railway Dashboard → Volume → fayllarni drag & drop qiling.

---

## Muhim eslatmalar

- `.session` fayllar `/data/` papkasida saqlanadi
- `bot.db` ham `/data/bot.db` da saqlanadi
- Bot qayta ishga tushganda ma'lumotlar saqlanib qoladi
