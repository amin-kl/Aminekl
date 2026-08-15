import base64
import json
import logging
import os
import tempfile
import hashlib
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
# AES (pycryptodome)
# ==========================

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    AES_AVAILABLE = True
except ImportError:
    AES_AVAILABLE = False
    logger.warning("pycryptodome not installed. Run: pip install pycryptodome")


# ==========================
# Core Decryption
# ==========================

def decrypt_aes(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    فك تشفير AES-256-CBC بالطريقة الصحيحة:
    1. أول 44 حرفاً = المفتاح الجزئي
    2. SHA256 → مفتاح 32 بايت
    3. باقي النص = Base64(IV + ciphertext)
    4. IV = أول 16 بايت
    5. ciphertext = باقي البايتات
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
        
        # تنظيف النص
        rest = rest.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        # إضافة padding لـ Base64
        padding = len(rest) % 4
        if padding:
            rest += "=" * (4 - padding)
        
        # استخلاص المفتاح
        key = hashlib.sha256(key_part.encode('utf-8')).digest()
        
        # فك Base64
        raw = base64.b64decode(rest)
        
        if len(raw) < 16:
            return None
        
        iv = raw[:16]
        ciphertext = raw[16:]
        
        # فك التشفير
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
        
        # تحويل إلى JSON
        return json.loads(decrypted.decode('utf-8'))
        
    except Exception as e:
        logger.debug(f"AES decrypt failed: {e}")
        return None


def decrypt_base64(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """احتياطي: فك Base64 فقط"""
    try:
        rest = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(rest) % 4
        if padding:
            rest += "=" * (4 - padding)
        
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(rest)
                return json.loads(decoded.decode('utf-8'))
            except Exception:
                continue
    except Exception:
        pass
    return None


def decrypt_config(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """المحاولة الأولى: AES، الثانية: Base64"""
    if not isinstance(encrypted_text, str):
        return None
    
    result = decrypt_aes(encrypted_text)
    if result is not None:
        return result
    
    return decrypt_base64(encrypted_text)


def process_file(content: str) -> Optional[str]:
    """
    معالجة الملف:
    - إذا كان JSON يحتوي على encryptedLockedConfig → فك التشفير
    - وإلا اعرض JSON منسقاً
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    
    if not isinstance(data, dict):
        return None
    
    # إذا كان يحتوي على encryptedLockedConfig
    if "encryptedLockedConfig" in data:
        encrypted = data["encryptedLockedConfig"]
        unlocked = decrypt_config(encrypted)
        if unlocked:
            return json.dumps(unlocked, indent=4, ensure_ascii=False)
    
    # إذا كان JSON عادي، أعده منسقاً
    return json.dumps(data, indent=4, ensure_ascii=False)


# ==========================
# Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔓 *DarkTunnel Unlocker Bot*\n\n"
        "أرسل ملفاً يحتوي على `encryptedLockedConfig`، وسأقوم بفك تشفيره.\n\n"
        "📌 يدعم:\n"
        "• ملفات .dark\n"
        "• ملفات .json\n"
        "• ملفات .txt\n"
        "• أي ملف نصي\n\n"
        "📌 الأوامر:\n"
        "/start - ترحيب\n"
        "/help - المساعدة",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *كيفية الاستخدام:*\n\n"
        "1. أرسل ملفاً نصياً\n"
        "2. سأقرأ الملف وأبحث عن `encryptedLockedConfig`\n"
        "3. سأفك التشفير وأعيد الملف المفكك\n\n"
        "🔧 *طريقة التشفير المدعومة:*\n"
        "• AES-256-CBC مع مفتاح مدمج (أول 44 حرفاً)\n"
        "• Base64 (احتياطي)",
        parse_mode="Markdown"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return
    
    # تحميل الملف
    try:
        file = await document.get_file()
        content_bytes = await file.download_as_bytearray()
        content = content_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        await update.message.reply_text(f"❌ فشل في قراءة الملف: {e}")
        return
    
    # معالجة المحتوى
    result = process_file(content)
    
    if result is None:
        await update.message.reply_text(
            "⚠️ هذا الملف ليس بصيغة مدعومة.\n"
            "تأكد من أنه يحتوي على `encryptedLockedConfig`."
        )
        return
    
    # إرسال النتيجة
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            f.write(result)
            temp_path = f.name
        
        with open(temp_path, 'rb') as f:
            original_name = document.file_name or 'file'
            base_name = os.path.splitext(original_name)[0] or 'unlocked'
            await update.message.reply_document(
                document=f,
                filename=f"{base_name}_unlocked.json",
                caption="✅ تم فك التشفير بنجاح!"
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
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit(
            "❌ لم يتم تعيين BOT_TOKEN.\n"
            "ضع التوكن في ملف .env أو غيّر القيمة مباشرة."
        )
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("🤖 DarkTunnel Unlocker Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
