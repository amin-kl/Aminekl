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
# خطوة 1: فك رابط DarkTunnel
# ==========================

def decode_darktunnel_link(link: str) -> Optional[Dict[str, Any]]:
    """
    فك رابط darktunnel:// واستخراج JSON
    """
    try:
        if not link.startswith("darktunnel://"):
            return None
        
        # استخراج الجزء المشفر
        encoded = link.replace("darktunnel://", "")
        
        # إضافة padding لـ Base64
        padding = len(encoded) % 4
        if padding:
            encoded += "=" * (4 - padding)
        
        # فك Base64
        decoded = base64.urlsafe_b64decode(encoded)
        
        # تحويل إلى JSON
        return json.loads(decoded.decode('utf-8'))
        
    except Exception as e:
        logger.debug(f"فشل فك الرابط: {e}")
        return None


# ==========================
# خطوة 2: فك التشفير (AES-256-CBC)
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
        
        # المفتاح الجزئي (أول 44 حرفاً)
        key_part = encrypted_text[:44]
        
        # باقي النص (البيانات المشفرة)
        rest = encrypted_text[44:]
        
        if not rest:
            return None
        
        # تنظيف النص
        rest = rest.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        # إضافة padding لـ Base64
        padding = len(rest) % 4
        if padding:
            rest += "=" * (4 - padding)
        
        # استخلاص المفتاح باستخدام SHA256
        key = hashlib.sha256(key_part.encode('utf-8')).digest()
        
        # فك Base64
        raw = base64.b64decode(rest)
        
        if len(raw) < 16:
            return None
        
        # IV = أول 16 بايت
        iv = raw[:16]
        
        # ciphertext = باقي البايتات
        ciphertext = raw[16:]
        
        # فك التشفير
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
        
        # تحويل إلى JSON
        return json.loads(decrypted.decode('utf-8'))
        
    except Exception as e:
        logger.debug(f"فشل فك AES: {e}")
        return None


# ==========================
# خطوة 3: فك Base64 فقط (احتياطي)
# ==========================

def decrypt_base64_only(text: str) -> Optional[Dict[str, Any]]:
    """
    فك Base64 فقط (احتياطي)
    """
    try:
        text = text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
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


# ==========================
# خطوة 4: المعالجة الرئيسية
# ==========================

def process_input(content: str) -> Optional[str]:
    """
    المعالجة الرئيسية: تفكر مثل تفكيري تماماً
    """
    content = content.strip()
    if not content:
        return None
    
    # ===== الخطوة 1: هل هو رابط DarkTunnel؟ =====
    if content.startswith("darktunnel://"):
        logger.info("🔍 تم اكتشاف رابط DarkTunnel")
        
        # فك الرابط
        data = decode_darktunnel_link(content)
        if not data:
            return json.dumps({
                "error": "فشل فك الرابط",
                "details": "الرابط قد يكون تالفاً"
            }, indent=4, ensure_ascii=False)
        
        # هل يحتوي على encryptedLockedConfig؟
        if "encryptedLockedConfig" in data:
            logger.info("🔐 تم العثور على encryptedLockedConfig، جاري فك التشفير...")
            
            encrypted = data["encryptedLockedConfig"]
            
            # محاولة فك AES
            unlocked = decrypt_aes(encrypted)
            if unlocked:
                logger.info("✅ تم فك التشفير بنجاح باستخدام AES")
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
            
            # محاولة فك Base64
            unlocked = decrypt_base64_only(encrypted)
            if unlocked:
                logger.info("✅ تم فك التشفير بنجاح باستخدام Base64")
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
            
            # فشل فك التشفير
            return json.dumps({
                "error": "فشل فك التشفير",
                "encrypted_preview": encrypted[:100] + "...",
                "encrypted_length": len(encrypted),
                "suggestion": "قد يكون التشفير يستخدم خوارزمية مختلفة"
            }, indent=4, ensure_ascii=False)
        
        # ليس لديه encryptedLockedConfig
        return json.dumps(data, indent=4, ensure_ascii=False)
    
    # ===== الخطوة 2: هل هو JSON؟ =====
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            logger.info("🔍 تم اكتشاف JSON")
            
            # هل يحتوي على encryptedLockedConfig؟
            if "encryptedLockedConfig" in data:
                logger.info("🔐 تم العثور على encryptedLockedConfig، جاري فك التشفير...")
                
                encrypted = data["encryptedLockedConfig"]
                
                # محاولة فك AES
                unlocked = decrypt_aes(encrypted)
                if unlocked:
                    logger.info("✅ تم فك التشفير بنجاح باستخدام AES")
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
                
                # محاولة فك Base64
                unlocked = decrypt_base64_only(encrypted)
                if unlocked:
                    logger.info("✅ تم فك التشفير بنجاح باستخدام Base64")
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
                
                # فشل فك التشفير
                return json.dumps({
                    "error": "فشل فك التشفير",
                    "encrypted_preview": encrypted[:100] + "...",
                    "encrypted_length": len(encrypted),
                    "suggestion": "قد يكون التشفير يستخدم خوارزمية مختلفة"
                }, indent=4, ensure_ascii=False)
            
            # JSON عادي
            return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    
    # ===== الخطوة 3: هل هو Base64؟ =====
    try:
        clean = content.replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(clean) % 4
        if padding:
            clean += "=" * (4 - padding)
        
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(clean)
                text = decoded.decode('utf-8')
                data = json.loads(text)
                logger.info("🔍 تم اكتشاف Base64 JSON")
                return json.dumps(data, indent=4, ensure_ascii=False)
            except Exception:
                continue
    except Exception:
        pass
    
    # ===== الخطوة 4: نص عادي =====
    logger.info("📝 تم اكتشاف نص عادي")
    return content


# ==========================
# Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔓 *DarkTunnel Unlocker Bot v2.0*\n\n"
        "أرسل لي:\n"
        "• رابط `darktunnel://`\n"
        "• ملف `.dark`\n"
        "• ملف `.json`\n"
        "• أي ملف نصي\n\n"
        "سأقوم بفك التشفير تلقائياً!\n\n"
        "📌 الأوامر:\n"
        "/start - ترحيب\n"
        "/help - المساعدة",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *كيفية الاستخدام:*\n\n"
        "1️⃣ أرسل رابط `darktunnel://` أو ملفاً\n"
        "2️⃣ سأكتشف النوع تلقائياً\n"
        "3️⃣ إذا كان مشفراً، سأفك التشفير\n"
        "4️⃣ سأعيد النتيجة كملف JSON\n\n"
        "🔧 *الطرق المدعومة:*\n"
        "• رابط DarkTunnel\n"
        "• JSON مع `encryptedLockedConfig`\n"
        "• Base64\n"
        "• نص عادي",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص (الروابط)"""
    content = update.message.text.strip()
    if not content:
        return
    
    result = process_input(content)
    if result is None:
        await update.message.reply_text("⚠️ لم أتمكن من معالجة هذا النص.")
        return
    
    # إذا كان طويلاً، أرسله كملف
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
    """معالجة الملفات"""
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
    result = process_input(content)
    if result is None:
        await update.message.reply_text(
            "⚠️ لم أتمكن من معالجة هذا الملف.\n"
            "تأكد من أنه يحتوي على بيانات صالحة."
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
    
    # الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    
    # النصوص (الروابط)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # الملفات
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("🤖 DarkTunnel Unlocker Bot v2.0 is running...")
    print("📌 يدعم: روابط darktunnel://، ملفات .dark، .json، أي ملف نصي")
    app.run_polling()


if __name__ == "__main__":
    main()
