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
# AES
# ==========================

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    AES_AVAILABLE = True
except ImportError:
    AES_AVAILABLE = False
    logger.warning("pycryptodome not installed. Run: pip install pycryptodome")


# ==========================
# 1. فك رابط DarkTunnel
# ==========================

def decode_darktunnel_link(link: str) -> Optional[Dict[str, Any]]:
    try:
        if not link.startswith("darktunnel://"):
            return None
        encoded = link.replace("darktunnel://", "")
        padding = len(encoded) % 4
        if padding:
            encoded += "=" * (4 - padding)
        decoded = base64.urlsafe_b64decode(encoded)
        return json.loads(decoded.decode('utf-8'))
    except Exception as e:
        logger.debug(f"فشل فك الرابط: {e}")
        return None


# ==========================
# 2. فك التشفير - الطريقة الصحيحة
# ==========================

def decrypt_correct(encrypted_text: str, context: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """
    فك التشفير بالطريقة الصحيحة:
    1. المفتاح = SHA256(أول 44 حرفاً)
    2. IV = أول 16 بايت من Base64(باقي النص)
    3. ciphertext = باقي البايتات
    4. AES-256-CBC + PKCS7 unpadding
    """
    if not AES_AVAILABLE:
        return None
    
    text = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    
    if len(text) < 44:
        return None
    
    # المفتاح الجزئي (أول 44 حرفاً)
    key_part = text[:44]
    rest = text[44:]
    
    if not rest:
        return None
    
    # تنظيف البيانات
    rest_clean = rest.replace('\n', '').replace('\r', '').replace(' ', '')
    
    # إضافة padding لـ Base64
    padding = len(rest_clean) % 4
    if padding:
        rest_clean += "=" * (4 - padding)
    
    try:
        # استخلاص المفتاح
        key = hashlib.sha256(key_part.encode('utf-8')).digest()
        
        # فك Base64
        raw = base64.b64decode(rest_clean)
        
        if len(raw) < 16:
            return None
        
        # IV = أول 16 بايت
        iv = raw[:16]
        ciphertext = raw[16:]
        
        # فك التشفير
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
        
        # تحويل إلى JSON
        return json.loads(decrypted.decode('utf-8'))
        
    except Exception as e:
        logger.debug(f"فشل فك التشفير: {e}")
        return None


# ==========================
# 3. المعالجة الرئيسية
# ==========================

def process_input(content: str) -> Optional[str]:
    content = content.strip()
    if not content:
        return None
    
    # ===== الخطوة 1: رابط DarkTunnel =====
    if content.startswith("darktunnel://"):
        data = decode_darktunnel_link(content)
        if not data:
            return json.dumps({"error": "فشل فك الرابط"}, indent=4, ensure_ascii=False)
        
        if "encryptedLockedConfig" in data:
            encrypted = data["encryptedLockedConfig"]
            unlocked = decrypt_correct(encrypted, data)
            if unlocked:
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
            else:
                return json.dumps({
                    "error": "فشل فك التشفير",
                    "encrypted_length": len(encrypted),
                    "preview": encrypted[:100] + "..."
                }, indent=4, ensure_ascii=False)
        
        return json.dumps(data, indent=4, ensure_ascii=False)
    
    # ===== الخطوة 2: JSON =====
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "encryptedLockedConfig" in data:
                encrypted = data["encryptedLockedConfig"]
                unlocked = decrypt_correct(encrypted, data)
                if unlocked:
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
                else:
                    return json.dumps({
                        "error": "فشل فك التشفير",
                        "encrypted_length": len(encrypted),
                        "preview": encrypted[:100] + "..."
                    }, indent=4, ensure_ascii=False)
            return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    
    return None


# ==========================
# 4. Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔓 *DarkTunnel Unlocker*\n\n"
        "أرسل رابط `darktunnel://` أو ملف `.dark`\n"
        "سأفك التشفير بالطريقة الصحيحة.",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *الطريقة:*\n"
        "1. أول 44 حرفاً = المفتاح الجزئي\n"
        "2. SHA256 → مفتاح 32 بايت\n"
        "3. باقي النص = Base64(IV + ciphertext)\n"
        "4. IV = أول 16 بايت\n"
        "5. AES-256-CBC + PKCS7 unpadding",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    if not content:
        return
    
    result = process_input(content)
    if result is None:
        await update.message.reply_text("⚠️ لم أتمكن من معالجة هذا النص.")
        return
    
    if len(result) > 4000:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
                f.write(result)
                temp_path = f.name
            with open(temp_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename="unlocked_data.json",
                    caption="✅ تم فك التشفير بنجاح!"
                )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    else:
        await update.message.reply_text(
            f"```\n{result}\n```",
            parse_mode="Markdown"
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return
    
    try:
        file = await document.get_file()
        content_bytes = await file.download_as_bytearray()
        content = content_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        await update.message.reply_text(f"❌ فشل في قراءة الملف: {e}")
        return
    
    result = process_input(content)
    if result is None:
        await update.message.reply_text("⚠️ لم أتمكن من معالجة هذا الملف.")
        return
    
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
# 5. Main
# ==========================

def main():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit("❌ لم يتم تعيين BOT_TOKEN.")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("🔓 DarkTunnel Unlocker is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
