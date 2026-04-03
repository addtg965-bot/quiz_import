import os
import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

# ================== ENV ==================
CLICK_SERVICE_ID = int(os.getenv("CLICK_SERVICE_ID", "0"))
CLICK_MERCHANT_ID = int(os.getenv("CLICK_MERCHANT_ID", "0"))
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")

CLICK_PAY_URL = "https://my.click.uz/services/pay"

# ================== URL YASASH ==================
def build_click_url(amount: int, order_id: int, return_url: str = "") -> str:
    params = {
        "service_id": CLICK_SERVICE_ID,
        "merchant_id": CLICK_MERCHANT_ID,
        "amount": amount,
        "transaction_param": order_id,
    }

    if return_url:
        params["return_url"] = return_url

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{CLICK_PAY_URL}?{query}"

# ================== REQUEST MODEL ==================
@dataclass
class ClickRequest:
    click_trans_id: int
    service_id: int
    merchant_trans_id: str
    amount: float
    action: int
    sign_time: str
    sign_string: str
    merchant_prepare_id: Optional[int] = None

# ================== SIGN TEKSHIRISH ==================
def verify_sign(req: ClickRequest) -> bool:
    if req.action == 0:
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

    expected = hashlib.md5(raw.encode()).hexdigest()
    return hmac.compare_digest(expected, req.sign_string)

# ================== PREPARE ==================
def handle_prepare(req: ClickRequest, db):
    if not verify_sign(req):
        return {"error": -1, "error_note": "SIGN ERROR"}

    cur = db.cursor()
    cur.execute("SELECT id, amount, status FROM payments WHERE id=?", (req.merchant_trans_id,))
    row = cur.fetchone()

    if not row:
        return {"error": -5, "error_note": "NOT FOUND"}

    if float(req.amount) != float(row[1]):
        return {"error": -2, "error_note": "AMOUNT ERROR"}

    return {
        "click_trans_id": req.click_trans_id,
        "merchant_trans_id": req.merchant_trans_id,
        "merchant_prepare_id": int(req.merchant_trans_id),
        "error": 0,
        "error_note": "OK"
    }

# ================== COMPLETE ==================
def handle_complete(req: ClickRequest, db):
    if not verify_sign(req):
        return {"error": -1, "error_note": "SIGN ERROR"}

    cur = db.cursor()
    cur.execute("SELECT id, status FROM payments WHERE id=?", (req.merchant_trans_id,))
    row = cur.fetchone()

    if not row:
        return {"error": -5, "error_note": "NOT FOUND"}

    if row[1] == "paid":
        return {"error": -4, "error_note": "ALREADY PAID"}

    # to'lov tasdiqlandi
    cur.execute("UPDATE payments SET status='paid' WHERE id=?", (req.merchant_trans_id,))
    db.commit()

    return {
        "click_trans_id": req.click_trans_id,
        "merchant_trans_id": req.merchant_trans_id,
        "merchant_confirm_id": int(req.merchant_trans_id),
        "error": 0,
        "error_note": "SUCCESS"
    }
