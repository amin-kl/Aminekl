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
# Core Decryption - All Methods
# ==========================

def decrypt_aes_all_methods(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    تجربة جميع طرق فك التشفير الممكنة حتى النجاح
    """
    if not AES_AVAILABLE:
        return None
    
    # تنظيف النص
    text = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    
    if len(text) < 44:
        return None
    
    # قائمة بجميع الطرق
    methods = []
    
    # الطريقة 1: أول 44 حرفاً + SHA256
    key_part = text[:44]
    rest = text[44:]
    if rest:
        methods.append(('SHA256 on key part', hashlib.sha256(key_part.encode('utf-8')).digest(), rest))
    
    # الطريقة 2: أول 44 حرفاً مباشرة (32 بايت)
    key_direct = key_part.encode('utf-8')[:32]
    if len(key_direct) < 32:
        key_direct = key_direct.ljust(32, b'\0')
    if rest:
        methods.append(('Direct key (first 32 bytes)', key_direct, rest))
    
    # الطريقة 3: أول 44 حرفاً + MD5
    if rest:
        methods.append(('MD5 on key part', hashlib.md5(key_part.encode('utf-8')).digest(), rest))
    
    # الطريقة 4: النص كاملاً + SHA256
    methods.append(('SHA256 on full text', hashlib.sha256(text.encode('utf-8')).digest(), text))
    
    # الطريقة 5: النص كاملاً + MD5
    methods.append(('MD5 on full text', hashlib.md5(text.encode('utf-8')).digest(), text))
    
    # الطريقة 6: Base64 فقط (بدون تقسيم)
    methods.append(('Base64 only', None, text))
    
    # الطريقة 7: تجربة أطوال مختلفة للمفتاح (40, 44, 48, 52)
    for length in [40, 44, 48, 52]:
        if len(text) > length:
            key_part_var = text[:length]
            rest_var = text[length:]
            if rest_var:
                methods.append((f'SHA256 on {length} chars', hashlib.sha256(key_part_var.encode('utf-8')).digest(), rest_var))
                key_direct_var = key_part_var.encode('utf-8')[:32]
                if len(key_direct_var) < 32:
                    key_direct_var = key_direct_var.ljust(32, b'\0')
                methods.append((f'Direct key from {length} chars', key_direct_var, rest_var))
    
    # تجربة كل طريقة
    for method_name, key, data in methods:
        try:
            # إذا كانت الطريقة Base64 فقط
            if method_name == 'Base64 only':
                for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                    try:
                        padding = len(data) % 4
                        if padding:
                            data_padded = data + "=" * (4 - padding)
                        else:
                            data_padded = data
                        raw = decoder(data_padded)
                        result = json.loads(raw.decode('utf-8'))
                        if result:
                            logger.info(f"✅ نجح فك التشفير باستخدام الطريقة: {method_name}")
                            return result
                    except Exception:
                        continue
                continue
            
            # تنظيف البيانات
            data_clean = data.strip().replace('\n', '').replace('\r', '').replace(' ', '')
            if not data_clean:
                continue
            
            padding = len(data_clean) % 4
            if padding:
                data_clean += "=" * (4 - padding)
            
            raw = base64.b64decode(data_clean)
            
            if len(raw) < 16:
                continue
            
            iv = raw[:16]
            ciphertext = raw[16:]
            
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            
            if result:
                logger.info(f"✅ نجح فك التشفير باستخدام الطريقة: {method_name}")
                return result
                
        except Exception as e:
            logger.debug(f"الطريقة {method_name} فشلت: {e}")
            continue
    
    return None


def decrypt_base64_only(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """احتياطي أخير: فك Base64 فقط"""
    try:
        text = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(text) % 4
        if padding:
            text += "=" * (4 - padding)
        
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(text)
                return json.loads(decoded.decode('utf-8'))
            except Exception:
                continue
    except Exception:
        pass
    return None


def decrypt_config(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """المحاولة الأولى: جميع طرق AES، الثانية: Base64"""
    if not isinstance(encrypted_text, str):
        return None
    
    # تجربة جميع طرق AES
    result = decrypt_aes_all_methods(encrypted_text)
    if result is not None:
        return result
    
    # احتياطي: Base64 فقط
    return decrypt_base64_only(encrypted_text)


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
        else:
            # إذا فشل فك التشفير، نعيد JSON الأصلي مع تنبيه
            return json.dumps({
                "error": "فشل فك التشفير",
                "original": data
            }, indent=4, ensure_ascii=False)
    
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
        "🔧 *طرق فك التشفير المدعومة:*\n"
        "• AES-256-CBC (7 طرق مختلفة لاستخلاص المفتاح)\n"
        "• Base64 (احتياطي)\n\n"
        "سيتم تجربة جميع الطرق تلقائياً حتى النجاح.",
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
    print("📌 سيتم تجربة 7 طرق مختلفة لفك التشفير.")
    app.run_polling()


if __name__ == "__main__":
    main()
