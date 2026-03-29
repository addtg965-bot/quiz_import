"""
Bu scriptni Railway ga yuklamasdan oldin
O'Z KOMPYUTERINGIZDA ishga tushiring!

Maqsad: Telegram sessiya fayllarini yaratish
Keyin data/ papkasidagi .session fayllarni Railway Volume ga yuklaymiz.

Ishlatish:
    python create_sessions.py
"""

import asyncio
import os
from telethon import TelegramClient

# ============================================================
# BU YERNI TO'LDIRING
# ============================================================
API_ID   = 31829658          # my.telegram.org dan
API_HASH = "58f6501ead5528f017bffeb9fd6742d8"   # my.telegram.org dan

PHONE_NUMBERS = [
    #"+92",   # quiz akkauntlar
    # "+998901234568",
    "+998934897111",   # notify akkaunt (@humocardbot uchun)
]
# ============================================================

async def create_session(phone: str):
    os.makedirs("data", exist_ok=True)
    session_name = f"data/userbot_{phone.replace('+','').replace(' ','')}"

    print(f"\n{'='*50}")
    print(f"📱 Akkaunt: {phone}")
    print(f"📁 Sessiya: {session_name}.session")
    print(f"{'='*50}")

    client = TelegramClient(session_name, API_ID, API_HASH)

    async def password_input():
        pwd = input(f"🔐 2FA paroli ({phone}): ")
        return pwd

    await client.start(phone=phone, password=password_input)

    me = await client.get_me()
    print(f"✅ Ulandi: {me.first_name} (@{me.username})")
    await client.disconnect()
    print(f"💾 Sessiya saqlandi: {session_name}.session")


async def main():
    print("🚀 Sessiya yaratuvchi")
    print("=" * 50)
    print(f"Jami {len(PHONE_NUMBERS)} ta akkaunt ulanadi\n")

    for phone in PHONE_NUMBERS:
        try:
            await create_session(phone)
        except Exception as e:
            print(f"❌ {phone} xato: {e}")

    print("\n" + "=" * 50)
    print("✅ Tayyor! data/ papkasidagi .session fayllarni")
    print("   Railway Volume ga yuklang (/data/ mount path)")
    print("=" * 50)

    # Yaratilgan fayllarni ko'rsatish
    print("\n📁 Yaratilgan fayllar:")
    for f in os.listdir("data"):
        if f.endswith(".session"):
            size = os.path.getsize(f"data/{f}")
            print(f"  ✅ data/{f} ({size} bayt)")


if __name__ == "__main__":
    asyncio.run(main())
