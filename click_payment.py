"""
Click To'lov Tizimi — To'g'rilangan Versiya
=============================================
main.py dagi click_invoices jadvali bilan ishlaydi.

Click ikki bosqichda ishlaydi:
  1. PREPARE  — to'lovni boshlashdan oldin buyurtmani tekshirish
  2. COMPLETE — to'lov o'tgandan keyin tasdiqlash

Rasmiy hujjat: https://docs.click.uz/click-api-en/
"""

import hashlib
import hmac
import logging
import os
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)

# ============================================================
#  SOZLAMALAR — env dan o'qiladi
# ============================================================
CLICK_SERVICE_ID        = os.environ.get("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID       = os.environ.get("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY        = os.environ.get("CLICK_SECRET_KEY", "")
CLICK_MERCHANT_USER_ID  = os.environ.get("CLICK_MERCHANT_USER_ID", "")

CLICK_PAY_URL = "https://my.click.uz/services/pay"


# ============================================================
#  TO'LOV HAVOLASI YARATISH
# ============================================================

def build_click_url(amount: int, merchant_trans_id: str,
                    return_url: str = "https://t.me/quiz_import_bot") -> str:
    """
    Foydalanuvchi Click orqali to'lashi uchun havola yaratadi.

    Parametrlar:
        amount            — so'mda summa (masalan: 10000)
        merchant_trans_id — click_invoices.merchant_trans_id (UUID ko'rinishida)
        return_url        — to'lovdan keyin qaytariladigan URL

    Qaytaradi: https://my.click.uz/services/pay?... ko'rinishidagi URL
    """
    query = (
        f"service_id={CLICK_SERVICE_ID}"
        f"&merchant_id={CLICK_MERCHANT_ID}"
        f"&amount={amount}"
        f"&transaction_param={merchant_trans_id}"
        f"&return_url={return_url}"
    )
    return f"{CLICK_PAY_URL}?{query}"


# ============================================================
#  IMZO TEKSHIRISH  (MUHIM: sign_time kerak!)
# ============================================================

def verify_click_sign(data: dict, action: int) -> bool:
    """
    Click yuborgan so'rovning MD5 imzosini tekshiradi.

    PREPARE (action=0) uchun:
        MD5(click_trans_id + service_id + secret_key + merchant_trans_id
            + amount + action + sign_time)

    COMPLETE (action=1) uchun:
        MD5(click_trans_id + service_id + secret_key + merchant_trans_id
            + merchant_prepare_id + amount + action + sign_time)

    Eslatma: 'amount' float ko'rinishida bo'ladi, masalan "2000.0"
    """
    try:
        click_trans_id    = str(data.get("click_trans_id", ""))
        service_id        = str(CLICK_SERVICE_ID)
        merchant_trans_id = str(data.get("merchant_trans_id", ""))
        amount            = str(data.get("amount", ""))
        action_str        = str(data.get("action", action))
        sign_time         = str(data.get("sign_time", ""))
        received_sign     = str(data.get("sign_string", ""))

        if action == 0:
            # PREPARE
            raw = (
                click_trans_id +
                service_id +
                CLICK_SECRET_KEY +
                merchant_trans_id +
                amount +
                action_str +
                sign_time
            )
        else:
            # COMPLETE — merchant_prepare_id qo'shiladi
            merchant_prepare_id = str(data.get("merchant_prepare_id", ""))
            raw = (
                click_trans_id +
                service_id +
                CLICK_SECRET_KEY +
                merchant_trans_id +
                merchant_prepare_id +
                amount +
                action_str +
                sign_time
            )

        expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
        ok = hmac.compare_digest(expected, received_sign)
        if not ok:
            log.warning(
                "Click imzo XATO | expected=%s | received=%s | raw=%s",
                expected, received_sign, raw
            )
        return ok

    except Exception as e:
        log.error("verify_click_sign xato: %s", e)
        return False


# ============================================================
#  XATO KODLARI
# ============================================================
CLICK_OK                  =  0
CLICK_ERR_SIGN            = -1
CLICK_ERR_INCORRECT_PARAM = -2
CLICK_ERR_ORDER_NOT_FOUND = -5
CLICK_ERR_ALREADY_PAID    = -4
CLICK_ERR_CANCELLED       = -9


# ============================================================
#  DB YORDAMCHI FUNKSIYALAR  (click_invoices jadvali)
# ============================================================

def _get_invoice(db_file: str, merchant_trans_id: str) -> Optional[tuple]:
    """
    click_invoices dan invoice olish.
    Qaytaradi: (id, user_id, amount, status) yoki None
    """
    con = sqlite3.connect(db_file)
    row = con.execute(
        """SELECT id, user_id, amount, status
           FROM click_invoices
           WHERE merchant_trans_id = ?""",
        (merchant_trans_id,),
    ).fetchone()
    con.close()
    return row


def _confirm_invoice(db_file: str, merchant_trans_id: str,
                     click_trans_id: str) -> Optional[tuple]:
    """
    Invoice ni 'paid' deb belgilash.
    Qaytaradi: (user_id, amount) yoki None
    """
    con = sqlite3.connect(db_file)
    row = con.execute(
        """SELECT user_id, amount FROM click_invoices
           WHERE merchant_trans_id = ? AND status = 'pending'""",
        (merchant_trans_id,),
    ).fetchone()
    if row:
        con.execute(
            """UPDATE click_invoices
               SET status='paid',
                   paid_at=datetime('now')
               WHERE merchant_trans_id = ?""",
            (merchant_trans_id,),
        )
        con.commit()
    con.close()
    return row


# ============================================================
#  PREPARE HANDLER
# ============================================================

def handle_prepare(data: dict, db_file: str) -> dict:
    """
    Click PREPARE so'rovini qayta ishlaydi.
    Botning aiohttp handler ichida chaqiriladi.
    """
    action = int(data.get("action", 0))

    if not verify_click_sign(data, action=0):
        return {"error": CLICK_ERR_SIGN, "error_note": "SIGN CHECK FAILED"}

    merchant_trans_id = data.get("merchant_trans_id", "")
    amount = float(data.get("amount", 0))

    invoice = _get_invoice(db_file, merchant_trans_id)
    if not invoice:
        return {"error": CLICK_ERR_ORDER_NOT_FOUND,
                "error_note": "Invoice topilmadi"}

    inv_id, user_id, inv_amount, status = invoice

    if status == "paid":
        return {"error": CLICK_ERR_ALREADY_PAID,
                "error_note": "Allaqachon to'langan"}

    if abs(amount - float(inv_amount)) > 1:
        return {"error": CLICK_ERR_INCORRECT_PARAM,
                "error_note": f"Summa mos kelmaydi. Kerak: {inv_amount}"}

    log.info("Click PREPARE OK: trans=%s, user=%s, amount=%s",
             merchant_trans_id, user_id, amount)
    return {
        "click_trans_id":     data.get("click_trans_id"),
        "merchant_trans_id":  merchant_trans_id,
        "merchant_prepare_id": inv_id,
        "error":              CLICK_OK,
        "error_note":         "Success",
    }


# ============================================================
#  COMPLETE HANDLER
# ============================================================

def handle_complete(data: dict, db_file: str) -> dict:
    """
    Click COMPLETE so'rovini qayta ishlaydi.
    Muvaffaqiyatli bo'lsa user balansini yangilash uchun
    (_user_id, _amount) ni ham qaytaradi — bot ular bilan ishlaydi.
    """
    action = int(data.get("action", 1))
    error  = int(data.get("error", 0))

    if not verify_click_sign(data, action=1):
        return {"error": CLICK_ERR_SIGN, "error_note": "SIGN CHECK FAILED"}

    merchant_trans_id = data.get("merchant_trans_id", "")
    click_trans_id    = str(data.get("click_trans_id", ""))

    # Foydalanuvchi bekor qilgan yoki xato
    if error < 0:
        log.info("Click COMPLETE: foydalanuvchi bekor qildi, trans=%s",
                 merchant_trans_id)
        return {
            "click_trans_id":    click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": 0,
            "error":             CLICK_OK,
            "error_note":        "Cancelled by user",
        }

    # Invoice ni tasdiqlash
    result = _confirm_invoice(db_file, merchant_trans_id, click_trans_id)
    if not result:
        # Allaqachon to'langan yoki topilmadi
        invoice = _get_invoice(db_file, merchant_trans_id)
        if invoice and invoice[3] == "paid":
            return {"error": CLICK_ERR_ALREADY_PAID,
                    "error_note": "Already paid"}
        return {"error": CLICK_ERR_ORDER_NOT_FOUND,
                "error_note": "Invoice topilmadi"}

    user_id, amount = result
    inv_id = _get_invoice(db_file, merchant_trans_id)
    prepare_id = inv_id[0] if inv_id else 0

    log.info("Click COMPLETE OK: trans=%s, user=%s, amount=%s",
             merchant_trans_id, user_id, amount)
    return {
        "click_trans_id":    click_trans_id,
        "merchant_trans_id": merchant_trans_id,
        "merchant_confirm_id": prepare_id,
        "error":             CLICK_OK,
        "error_note":        "Success",
        # Bot uchun — JSON javobga kirmaydi
        "_user_id": user_id,
        "_amount":  int(amount),
    }
