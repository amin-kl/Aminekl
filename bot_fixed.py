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
# 1. فك رابط DarkTunnel
# ==========================

def decode_darktunnel_link(link: str) -> Optional[Dict[str, Any]]:
    """فك رابط darktunnel://"""
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
# 2. فك التشفير - جميع الطرق
# ==========================

def decrypt_all_methods(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    يجرب كل طرق فك التشفير الممكنة
    """
    if not AES_AVAILABLE:
        return None
    
    text = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    
    if len(text) < 20:
        return None
    
    # ============================================================
    # الطريقة 1: Base64 فقط (بدون AES)
    # ============================================================
    try:
        clean = text.replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(clean) % 4
        if padding:
            clean += "=" * (4 - padding)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(clean)
                result = json.loads(raw.decode('utf-8'))
                if result:
                    logger.info("✅ نجح فك التشفير بـ Base64 فقط")
                    return result
            except Exception:
                continue
    except Exception:
        pass
    
    # ============================================================
    # الطريقة 2: محاولة كل أطوال المفاتيح الممكنة (20-60)
    # ============================================================
    for key_length in range(20, 61):
        if len(text) <= key_length:
            continue
        
        key_part = text[:key_length]
        rest = text[key_length:]
        
        if not rest:
            continue
        
        # تنظيف البيانات
        rest_clean = rest.replace('\n', '').replace('\r', '').replace(' ', '')
        if not rest_clean:
            continue
        
        padding = len(rest_clean) % 4
        if padding:
            rest_clean += "=" * (4 - padding)
        
        try:
            raw = base64.b64decode(rest_clean)
        except Exception:
            continue
        
        if len(raw) < 16:
            continue
        
        iv = raw[:16]
        ciphertext = raw[16:]
        
        # 2.1: SHA256 على المفتاح الجزئي
        try:
            key = hashlib.sha256(key_part.encode('utf-8')).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ SHA256 (طول المفتاح: {key_length})")
                return result
        except Exception:
            pass
        
        # 2.2: MD5 على المفتاح الجزئي
        try:
            key = hashlib.md5(key_part.encode('utf-8')).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ MD5 (طول المفتاح: {key_length})")
                return result
        except Exception:
            pass
        
        # 2.3: المفتاح مباشرة (أول 32 بايت)
        try:
            key = key_part.encode('utf-8')[:32]
            if len(key) < 32:
                key = key.ljust(32, b'\0')
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ Direct Key (طول المفتاح: {key_length})")
                return result
        except Exception:
            pass
        
        # 2.4: SHA256 على النص الكامل
        try:
            key = hashlib.sha256(text.encode('utf-8')).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ SHA256 (النص الكامل)")
                return result
        except Exception:
            pass
        
        # 2.5: MD5 على النص الكامل
        try:
            key = hashlib.md5(text.encode('utf-8')).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ MD5 (النص الكامل)")
                return result
        except Exception:
            pass
        
        # 2.6: AES-GCM (إذا كان متاحاً)
        try:
            from Crypto.Cipher import AES as AESGCM
            key = hashlib.sha256(key_part.encode('utf-8')).digest()
            cipher = AESGCM.new(key, AESGCM.MODE_GCM, nonce=iv)
            decrypted = cipher.decrypt_and_verify(ciphertext, b'')
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ GCM (طول المفتاح: {key_length})")
                return result
        except Exception:
            pass
        
        # 2.7: محاولة بدون IV (IV = 16 صفر)
        try:
            key = hashlib.sha256(key_part.encode('utf-8')).digest()
            zero_iv = b'\x00' * 16
            cipher = AES.new(key, AES.MODE_CBC, zero_iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            result = json.loads(decrypted.decode('utf-8'))
            if result:
                logger.info(f"✅ نجح فك التشفير بـ Zero IV (طول المفتاح: {key_length})")
                return result
        except Exception:
            pass
    
    # ============================================================
    # الطريقة 3: محاولة فك Base64 فقط على النص كاملاً (بدون تقسيم)
    # ============================================================
    try:
        clean = text.replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(clean) % 4
        if padding:
            clean += "=" * (4 - padding)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(clean)
                # محاولة تفسير الناتج كـ JSON
                try:
                    result = json.loads(raw.decode('utf-8'))
                    if result:
                        logger.info("✅ نجح فك التشفير بـ Base64 (كامل)")
                        return result
                except Exception:
                    pass
                # محاولة فك AES على الناتج
                if len(raw) > 16:
                    iv = raw[:16]
                    ciphertext = raw[16:]
                    for key_length in range(20, 61):
                        if len(text) <= key_length:
                            continue
                        key_part = text[:key_length]
                        try:
                            key = hashlib.sha256(key_part.encode('utf-8')).digest()
                            cipher = AES.new(key, AES.MODE_CBC, iv)
                            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                            result = json.loads(decrypted.decode('utf-8'))
                            if result:
                                logger.info(f"✅ نجح فك التشفير بـ AES على Base64 (طول المفتاح: {key_length})")
                                return result
                        except Exception:
                            pass
            except Exception:
                continue
    except Exception:
        pass
    
    return None


# ==========================
# 3. المعالجة الرئيسية
# ==========================

def process_input(content: str) -> Optional[str]:
    """المعالجة الرئيسية"""
    content = content.strip()
    if not content:
        return None
    
    # ===== الخطوة 1: رابط DarkTunnel =====
    if content.startswith("darktunnel://"):
        logger.info("🔍 تم اكتشاف رابط DarkTunnel")
        data = decode_darktunnel_link(content)
        if not data:
            return json.dumps({"error": "فشل فك الرابط"}, indent=4, ensure_ascii=False)
        
        if "encryptedLockedConfig" in data:
            logger.info("🔐 تم العثور على encryptedLockedConfig")
            encrypted = data["encryptedLockedConfig"]
            unlocked = decrypt_all_methods(encrypted)
            if unlocked:
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
            else:
                return json.dumps({
                    "error": "فشل فك التشفير بعد محاولة جميع الطرق",
                    "encrypted_length": len(encrypted),
                    "preview": encrypted[:100] + "..."
                }, indent=4, ensure_ascii=False)
        
        return json.dumps(data, indent=4, ensure_ascii=False)
    
    # ===== الخطوة 2: JSON =====
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "encryptedLockedConfig" in data:
                logger.info("🔐 تم العثور على encryptedLockedConfig في JSON")
                encrypted = data["encryptedLockedConfig"]
                unlocked = decrypt_all_methods(encrypted)
                if unlocked:
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
                else:
                    return json.dumps({
                        "error": "فشل فك التشفير بعد محاولة جميع الطرق",
                        "encrypted_length": len(encrypted),
                        "preview": encrypted[:100] + "..."
                    }, indent=4, ensure_ascii=False)
            return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    
    # ===== الخطوة 3: تجربة جميع الطرق مباشرة =====
    result = decrypt_all_methods(content)
    if result:
        return json.dumps(result, indent=4, ensure_ascii=False)
    
    # ===== الخطوة 4: نص عادي =====
    return content


# ==========================
# 4. Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔓 *DarkTunnel Unlocker Bot v3.0*\n\n"
        "أرسل:\n"
        "• رابط `darktunnel://`\n"
        "• ملف `.dark` أو `.json`\n"
        "• أي ملف نصي\n\n"
        "سأجرب كل طرق فك التشفير الممكنة!\n\n"
        "📌 الأوامر:\n"
        "/start - ترحيب\n"
        "/help - المساعدة",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *كيفية الاستخدام:*\n\n"
        "1️⃣ أرسل رابطاً أو ملفاً\n"
        "2️⃣ سأكتشف النوع تلقائياً\n"
        "3️⃣ سأجرب كل طرق فك التشفير\n"
        "4️⃣ سأرسل النتيجة\n\n"
        "🔧 *طرق فك التشفير المدعومة:*\n"
        "• Base64\n"
        "• AES-256-CBC (أطوال مفاتيح 20-60)\n"
        "• SHA256 على المفتاح\n"
        "• MD5 على المفتاح\n"
        "• Direct Key (أول 32 بايت)\n"
        "• AES-GCM\n"
        "• Zero IV\n"
        "• كل التركيبات الممكنة",
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
        raise SystemExit(
            "❌ لم يتم تعيين BOT_TOKEN.\n"
            "ضع التوكن في ملف .env أو غيّر القيمة مباشرة."
        )
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("🤖 DarkTunnel Unlocker Bot v3.0 is running...")
    print("📌 يدعم: كل طرق فك التشفير الممكنة!")
    app.run_polling()


if __name__ == "__main__":
    main()
