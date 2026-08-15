import base64
import json
import logging
import os
import tempfile
from typing import Optional, Dict, Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==========================
# Settings
# ==========================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================
# Optional AES (if pycryptodome is installed)
# ==========================

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    import hashlib
    AES_AVAILABLE = True
except ImportError:
    AES_AVAILABLE = False
    logger.warning("pycryptodome not installed. AES decryption disabled.")

# ==========================
# Decryption Functions
# ==========================

def try_aes_decrypt(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to decrypt using AES-256-CBC with embedded key.
    Assumes: first 44 chars = key part, rest = Base64(IV + ciphertext).
    """
    if not AES_AVAILABLE:
        return None

    try:
        if len(encrypted_text) < 44:
            return None

        key_part = encrypted_text[:44]
        rest = encrypted_text[44:]

        if not rest:
            return None

        key = hashlib.sha256(key_part.encode('utf-8')).digest()
        raw = base64.b64decode(rest)

        if len(raw) < 16:
            return None

        iv = raw[:16]
        ciphertext = raw[16:]

        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return json.loads(decrypted.decode('utf-8'))

    except Exception as e:
        logger.debug(f"AES decrypt failed: {e}")
        return None


def try_base64_decrypt(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    Fallback: treat the text as plain Base64-encoded JSON.
    """
    try:
        encrypted_text = encrypted_text.strip()
        padding = len(encrypted_text) % 4
        if padding:
            encrypted_text += "=" * (4 - padding)

        # Try standard and URL-safe Base64
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded_bytes = decoder(encrypted_text)
                decoded_str = decoded_bytes.decode('utf-8')
                return json.loads(decoded_str)
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Base64 decrypt failed: {e}")

    return None


def decrypt_locked_config(encrypted_data: str) -> Optional[Dict[str, Any]]:
    """
    Main decrypt function: tries AES first, then Base64.
    """
    if not isinstance(encrypted_data, str):
        return None

    # Try AES
    result = try_aes_decrypt(encrypted_data)
    if result is not None:
        return result

    # Fallback to Base64
    return try_base64_decrypt(encrypted_data)


def process_locked_file(content: str) -> Optional[str]:
    """
    Process file content: if it contains 'encryptedLockedConfig', unlock it.
    Returns the unlocked JSON as a string, or None if not locked or decryption fails.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or "encryptedLockedConfig" not in data:
        return None

    encrypted = data["encryptedLockedConfig"]
    unlocked = decrypt_locked_config(encrypted)
    if unlocked is None:
        return None

    return json.dumps(unlocked, indent=4, ensure_ascii=False)

# ==========================
# Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أرسل لي ملفاً نصياً، وسأحاول استخراج البيانات منه."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 أرسل ملفاً نصياً (JSON) يحتوي على حقل مشفر، وسأعيد الملف المفكك إن أمكن."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return

    # Download file
    try:
        file = await document.get_file()
        content_bytes = await file.download_as_bytearray()
        content = content_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل في قراءة الملف: {e}")
        return

    # Process
    unlocked_json = process_locked_file(content)
    if unlocked_json is None:
        await update.message.reply_text(
            "⚠️ هذا الملف ليس بصيغة مدعومة، أو فشل الاستخراج.\n"
            "تأكد من أنه يحتوي على الحقل المناسب وأن التشفير مدعوم."
        )
        return

    # Send the unlocked file
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(unlocked_json)
            temp_path = f.name

        with open(temp_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="unlocked_data.json",
                caption="✅ تم الاستخراج بنجاح!"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ أثناء إرسال الملف: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

# ==========================
# Main
# ==========================

def main():
    # إصلاح: التحقق من أن التوكن تم تعيينه فعلياً قبل محاولة التشغيل،
    # بدلاً من ترك المكتبة تفشل برسالة غامضة غير واضحة للمستخدم.
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit(
            "❌ لم يتم تعيين BOT_TOKEN. الرجاء استبدال القيمة الافتراضية "
            "'PUT_YOUR_TOKEN_HERE' بتوكن البوت الحقيقي قبل التشغيل."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
