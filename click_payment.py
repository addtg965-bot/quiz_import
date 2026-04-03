"""
Click To'lov Tizimi Integratsiyasi
===================================
Click API orqali to'lovlarni qabul qilish uchun modul.

Click ikki xil usulda ishlaydi:
  1. PREPARE  — foydalanuvchi to'lovni boshlaydi
  2. COMPLETE — Click to'lovni tasdiqlaydi yoki bekor qiladi

Rasmiy hujjat: https://docs.click.uz/click-api-en/
"""

import hashlib
import hmac
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ============================================================
#  CLICK SOZLAMALARI  (env dan o'qiladi)
# ============================================================
CLICK_SERVICE_ID  = int(os.environ.get("CLICK_SERVICE_ID", "0"))
CLICK_MERCHANT_ID = int(os.environ.get("CLICK_MERCHANT_ID", "0"))
CLICK_SECRET_KEY  = os.environ.get("CLICK_SECRET_KEY", "")
CLICK_MERCHANT_USER_ID = int(os.environ.get("CLICK_MERCHANT_USER_ID", "0"))

# To'lov havolasi qolipи
CLICK_PAY_URL = "https://my.click.uz/services/pay"


# ============================================================
#  TO'LOV HAVOLASI YARATISH
# ============================================================

def build_click_url(amount: int, order_id: int, return_url: str = "") -> str:
    """
    Foydalanuvchi Click orqali to'lashi uchun havola yaratadi.

    Parametrlar:
        amount   — so'mda summa (masalan: 2000)
        order_id — bazadagi to'lov ID si (merchant_trans_id sifatida yuboriladi)
        return_url — to'lovdan keyin qaytariladigan URL (ixtiyoriy)

    Qaytaradi:
        https://my.click.uz/services/pay?... ko'rinishidagi URL
    """
    params = {
        "service_id": CLICK_SERVICE_ID,
        "merchant_id": CLICK_MERCHANT_ID,
        "amount":      amount,
        "transaction_param": order_id,
    }
    if return_url:
        params["return_url"] = return_url

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{CLICK_PAY_URL}?{query}"


# ============================================================
#  CLICK WEBHOOK SO'ROVLARini TEKSHIRISH
# ============================================================

@dataclass
class ClickRequest:
    """Click dan kelgan PREPARE yoki COMPLETE so'rov ma'lumotlari"""
    click_trans_id:        int
    service_id:            int
    click_paydoc_id:       int
    merchant_trans_id:     str      # bizning order_id
    amount:                float
    action:                int      # 0=PREPARE, 1=COMPLETE
    error:                 int
    error_note:            str
    sign_time:             str
    sign_string:           str
    merchant_prepare_id:   Optional[int] = None


def verify_sign(req: ClickRequest) -> bool:
    """
    Click imzosini tekshiradi.
    Hujjat: https://docs.click.uz/click-api-en/#sign_string
    """
    if req.action == 0:
        # PREPARE imzosi
        raw = (
            f"{req.click_trans_id}"
            f"{req.service_id}"
            f"{CLICK_SECRET_KEY}"
            f"{req.merchant_trans_id}"
            f"{req.amount}"
            f"{req.action}"
            f"{req.sign_time}"
        )
    else:
        # COMPLETE imzosi
        raw = (
            f"{req.click_trans_id}"
            f"{req.service_id}"
            f"{CLICK_SECRET_KEY}"
            f"{req.merchant_trans_id}"
            f"{req.merchant_prepare_id}"
            f"{req.amount}"
            f"{req.action}"
            f"{req.sign_time}"
        )

    expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, req.sign_string)


# ============================================================
#  XATO KODLARI
# ============================================================
CLICK_OK                   =  0
CLICK_ERR_SIGN             = -1
CLICK_ERR_INCORRECT_PARAM  = -2   # Noto'g'ri parametr
CLICK_ERR_ORDER_NOT_FOUND  = -5   # Buyurtma topilmadi
CLICK_ERR_ALREADY_PAID     = -4   # Allaqachon to'langan
CLICK_ERR_CANCELLED        = -9   # Foydalanuvchi bekor qildi


# ============================================================
#  PREPARE HANDLER
# ============================================================

def handle_prepare(req: ClickRequest, db_file: str) -> dict:
    """
    Click PREPARE so'rovini qayta ishlaydi.
    To'lov mavjud va amal qilish muddati o'tmagan bo'lsa ruxsat beradi.
    """
    if not verify_sign(req):
        log.warning("Click PREPARE: imzo noto'g'ri, trans_id=%s", req.click_trans_id)
        return {
            "error": CLICK_ERR_SIGN,
            "error_note": "SIGN CHECK FAILED",
        }

    con = sqlite3.connect(db_file)
    row = con.execute(
        """SELECT id, user_id, amount, status
           FROM payments
           WHERE id=? AND status='pending'
              AND expires_at > datetime('now')""",
        (int(req.merchant_trans_id),),
    ).fetchone()
    con.close()

    if not row:
        return {
            "error": CLICK_ERR_ORDER_NOT_FOUND,
            "error_note": "Order not found or expired",
        }

    pay_id, user_id, expected_amount, status = row

    # Summani tekshirish (so'm)
    if abs(float(req.amount) - float(expected_amount)) > 0.01:
        return {
            "error": CLICK_ERR_INCORRECT_PARAM,
            "error_note": f"Amount mismatch: expected {expected_amount}",
        }

    log.info("Click PREPARE OK: order=%s, user=%s, amount=%s", pay_id, user_id, req.amount)
    return {
        "click_trans_id":   req.click_trans_id,
        "merchant_trans_id": str(pay_id),
        "merchant_prepare_id": pay_id,
        "error":            CLICK_OK,
        "error_note":       "Success",
    }


# ============================================================
#  COMPLETE HANDLER
# ============================================================

def handle_complete(req: ClickRequest, db_file: str) -> dict:
    """
    Click COMPLETE so'rovini qayta ishlaydi.
    To'lov muvaffaqiyatli bo'lsa tasdiqlaydi, bekor bo'lsa — o'chiradi.
    """
    if not verify_sign(req):
        log.warning("Click COMPLETE: imzo noto'g'ri, trans_id=%s", req.click_trans_id)
        return {
            "error": CLICK_ERR_SIGN,
            "error_note": "SIGN CHECK FAILED",
        }

    con = sqlite3.connect(db_file)
    row = con.execute(
        "SELECT id, user_id, amount, status FROM payments WHERE id=?",
        (int(req.merchant_trans_id),),
    ).fetchone()

    if not row:
        con.close()
        return {
            "error": CLICK_ERR_ORDER_NOT_FOUND,
            "error_note": "Order not found",
        }

    pay_id, user_id, expected_amount, status = row

    if status == "confirmed":
        con.close()
        return {
            "error": CLICK_ERR_ALREADY_PAID,
            "error_note": "Already paid",
        }

    # Foydalanuvchi bekor qilgan yoki xato bo'lgan
    if req.error < 0:
        con.execute("UPDATE payments SET status='cancelled' WHERE id=?", (pay_id,))
        con.commit()
        con.close()
        log.info("Click COMPLETE: bekor qilindi, order=%s, error=%s", pay_id, req.error)
        return {
            "click_trans_id":   req.click_trans_id,
            "merchant_trans_id": str(pay_id),
            "merchant_confirm_id": pay_id,
            "error":            CLICK_OK,
            "error_note":       "Cancelled",
        }

    # To'lovni tasdiqlash
    con.execute(
        """UPDATE payments
           SET status='confirmed',
               confirmed_at=datetime('now'),
               click_trans_id=?
           WHERE id=?""",
        (req.click_trans_id, pay_id),
    )
    con.commit()
    con.close()

    log.info("Click COMPLETE OK: order=%s, user=%s, amount=%s", pay_id, user_id, req.amount)
    return {
        "click_trans_id":    req.click_trans_id,
        "merchant_trans_id": str(pay_id),
        "merchant_confirm_id": pay_id,
        "error":             CLICK_OK,
        "error_note":        "Success",
        # Bot uchun qo'shimcha ma'lumot (JSON javobiga kirmaydi)
        "_user_id": user_id,
        "_amount":  int(req.amount),
    }
