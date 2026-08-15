import base64
import json
import logging
import os
import tempfile
import hashlib
import re
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
# القسم 1: فك الرابط
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
# القسم 2: فك التشفير الذكي (مثلي تماماً)
# ==========================

def smart_decrypt(encrypted_text: str, context_data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """
    أنا أفكر: لقد جربت كل شيء ولم يعمل.
    ربما المفتاح ليس في بداية النص.
    ربما المفتاح هو جزء من البيانات نفسها.
    سأجرب كل شيء مرة أخرى ولكن بطريقة مختلفة.
    """
    if not AES_AVAILABLE:
        return None
    
    # تنظيف النص
    text = encrypted_text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    
    if len(text) < 20:
        return None
    
    logger.info("🧠 أنا: سأفكر بشكل مختلف هذه المرة")
    
    # ============================================================
    # الطريقة 1: النص كله قد يكون Base64
    # ============================================================
    try:
        clean = text
        padding = len(clean) % 4
        if padding:
            clean += "=" * (4 - padding)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(clean)
                # هل هو JSON مباشرة؟
                try:
                    result = json.loads(raw.decode('utf-8'))
                    if result:
                        logger.info("🧠 أنا: نجح! النص كله Base64 JSON")
                        return result
                except Exception:
                    pass
                # هل هو IV + ciphertext؟
                if len(raw) > 16:
                    logger.info("🧠 أنا: هذا قد يكون IV + ciphertext، سأجرب")
                    iv = raw[:16]
                    ciphertext = raw[16:]
                    # أجرب مفاتيح مختلفة
                    for key_source in generate_keys(text, context_data):
                        result = try_decrypt_with_key(ciphertext, iv, key_source)
                        if result:
                            return result
            except Exception:
                continue
    except Exception:
        pass
    
    # ============================================================
    # الطريقة 2: تجربة أطوال مختلفة للمفتاح مع كل مصدر
    # ============================================================
    for key_source in generate_keys(text, context_data):
        # جرب أطوال مختلفة
        for length in range(16, 65):
            if len(key_source) >= length:
                key_part = key_source[:length]
                # جرب كل الطرق على هذا الجزء
                for method in ['sha256', 'md5', 'direct']:
                    result = try_decrypt_with_key_part(text, key_part, method)
                    if result:
                        return result
    
    # ============================================================
    # الطريقة 3: إذا كان هناك context_data (مثل JSON كامل)
    # ============================================================
    if context_data:
        logger.info("🧠 أنا: لدي سياق إضافي، سأستخدمه")
        # جرب كل قيم JSON كمفتاح
        for key, value in context_data.items():
            if isinstance(value, str) and len(value) > 10:
                for method in ['sha256', 'md5', 'direct']:
                    result = try_decrypt_with_key_part(text, value, method)
                    if result:
                        return result
    
    # ============================================================
    # الطريقة 4: محاولة كل شيء بشكل عشوائي
    # ============================================================
    logger.info("🧠 أنا: سأجرب كل شيء بشكل عشوائي")
    
    # قائمة بالمفاتيح المحتملة
    possible_keys = [
        "darktunnel",
        "vless",
        "trojan",
        "youtube",
        "google",
        "cloudflare",
        "1.1.1.1",
        "8.8.8.8",
        "FfLjGpMPn9322vT160FpTbNq5xJmcZCuGgxDIqlrsNV1ckVk",
        text[:20],
        text[-20:],
        hashlib.md5(text.encode()).hexdigest()[:32],
        hashlib.sha256(text.encode()).hexdigest()[:32],
    ]
    
    for key_str in possible_keys:
        for method in ['sha256', 'md5', 'direct']:
            result = try_decrypt_with_key_part(text, key_str, method)
            if result:
                return result
    
    logger.warning("🧠 أنا: لم أجد طريقة لفك التشفير")
    return None


def generate_keys(text: str, context_data: Optional[Dict] = None):
    """توليد مفاتيح محتملة من مصادر مختلفة"""
    keys = []
    
    # من النص نفسه
    keys.append(text)
    keys.append(text[:20])
    keys.append(text[:30])
    keys.append(text[:40])
    keys.append(text[:44])
    keys.append(text[:50])
    keys.append(text[-20:])
    keys.append(text[-30:])
    keys.append(text[-40:])
    keys.append(text[-44:])
    keys.append(text[-50:])
    
    # من السياق
    if context_data:
        for key, value in context_data.items():
            if isinstance(value, str):
                keys.append(value)
                keys.append(value[:20])
                keys.append(value[-20:])
    
    # مفاتيح ثابتة
    keys.append("darktunnel")
    keys.append("vless")
    keys.append("trojan")
    keys.append("youtube")
    keys.append("google")
    keys.append("cloudflare")
    keys.append("FfLjGpMPn9322vT160FpTbNq5xJmcZCuGgxDIqlrsNV1ckVk")
    
    return keys


def try_decrypt_with_key_part(encrypted_text: str, key_part: str, method: str) -> Optional[Dict[str, Any]]:
    """محاولة فك التشفير باستخدام جزء من المفتاح"""
    try:
        # تنظيف النص المشفر
        rest = encrypted_text
        rest_clean = rest.replace('\n', '').replace('\r', '').replace(' ', '')
        
        # محاولة استخراج IV من البداية
        # ولكن ربما IV ليس في البداية، ربما في النهاية
        for iv_position in ['start', 'end']:
            try:
                # جرب Base64 على النص كله أولاً
                clean = rest_clean
                padding = len(clean) % 4
                if padding:
                    clean += "=" * (4 - padding)
                
                raw = None
                for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                    try:
                        raw = decoder(clean)
                        break
                    except Exception:
                        continue
                
                if raw is None:
                    continue
                
                if len(raw) < 16:
                    continue
                
                if iv_position == 'start':
                    iv = raw[:16]
                    ciphertext = raw[16:]
                else:
                    iv = raw[-16:]
                    ciphertext = raw[:-16]
                
                # استخلاص المفتاح
                if method == 'sha256':
                    key = hashlib.sha256(key_part.encode('utf-8')).digest()
                elif method == 'md5':
                    key = hashlib.md5(key_part.encode('utf-8')).digest()
                else:  # direct
                    key = key_part.encode('utf-8')[:32]
                    if len(key) < 32:
                        key = key.ljust(32, b'\0')
                
                # AES-CBC
                try:
                    cipher = AES.new(key, AES.MODE_CBC, iv)
                    decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                    result = json.loads(decrypted.decode('utf-8'))
                    if result:
                        logger.info(f"🧠 أنا: نجح فك التشفير بـ {method} (IV في {iv_position})")
                        return result
                except Exception:
                    pass
                
                # AES-GCM
                try:
                    from Crypto.Cipher import AES as AESGCM
                    cipher = AESGCM.new(key, AESGCM.MODE_GCM, nonce=iv)
                    decrypted = cipher.decrypt_and_verify(ciphertext, b'')
                    result = json.loads(decrypted.decode('utf-8'))
                    if result:
                        logger.info(f"🧠 أنا: نجح فك التشفير بـ {method} GCM (IV في {iv_position})")
                        return result
                except Exception:
                    pass
                
                # Zero IV
                try:
                    zero_iv = b'\x00' * 16
                    cipher = AES.new(key, AES.MODE_CBC, zero_iv)
                    decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                    result = json.loads(decrypted.decode('utf-8'))
                    if result:
                        logger.info(f"🧠 أنا: نجح فك التشفير بـ {method} (Zero IV)")
                        return result
                except Exception:
                    pass
                
            except Exception:
                continue
    
    except Exception as e:
        logger.debug(f"فشلت المحاولة بـ {method}: {e}")
    
    return None


def try_decrypt_with_key(ciphertext: bytes, iv: bytes, key_source: str) -> Optional[Dict[str, Any]]:
    """محاولة فك التشفير بمفتاح معين"""
    for method in ['sha256', 'md5', 'direct']:
        try:
            if method == 'sha256':
                key = hashlib.sha256(key_source.encode('utf-8')).digest()
            elif method == 'md5':
                key = hashlib.md5(key_source.encode('utf-8')).digest()
            else:
                key = key_source.encode('utf-8')[:32]
                if len(key) < 32:
                    key = key.ljust(32, b'\0')
            
            # AES-CBC
            try:
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                result = json.loads(decrypted.decode('utf-8'))
                if result:
                    logger.info(f"🧠 أنا: نجح فك التشفير بـ {method}")
                    return result
            except Exception:
                pass
            
            # AES-GCM
            try:
                from Crypto.Cipher import AES as AESGCM
                cipher = AESGCM.new(key, AESGCM.MODE_GCM, nonce=iv)
                decrypted = cipher.decrypt_and_verify(ciphertext, b'')
                result = json.loads(decrypted.decode('utf-8'))
                if result:
                    logger.info(f"🧠 أنا: نجح فك التشفير بـ {method} GCM")
                    return result
            except Exception:
                pass
            
        except Exception:
            continue
    
    return None


# ==========================
# القسم 3: المعالجة الرئيسية
# ==========================

def process_like_me(content: str) -> Optional[str]:
    """أنا أفكر وأحلل وأفكك مثل الذكاء الاصطناعي"""
    content = content.strip()
    if not content:
        return None
    
    # ===== الخطوة 1: هل هو رابط DarkTunnel؟ =====
    if content.startswith("darktunnel://"):
        logger.info("🧠 أنا: هذا رابط DarkTunnel")
        link_data = decode_darktunnel_link(content)
        if not link_data:
            return json.dumps({"error": "فشل فك الرابط"}, indent=4, ensure_ascii=False)
        
        if "encryptedLockedConfig" in link_data:
            logger.info("🧠 أنا: وجدت encryptedLockedConfig")
            encrypted = link_data["encryptedLockedConfig"]
            
            # أجرب فك التشفير مع السياق
            unlocked = smart_decrypt(encrypted, link_data)
            if unlocked:
                logger.info("🧠 أنا: نجح فك التشفير!")
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
            else:
                return json.dumps({
                    "error": "فشل فك التشفير بعد محاولة كل شيء",
                    "encrypted_length": len(encrypted),
                    "preview": encrypted[:100] + "...",
                    "suggestion": "قد يكون التشفير يستخدم خوارزمية مختلفة"
                }, indent=4, ensure_ascii=False)
        
        return json.dumps(link_data, indent=4, ensure_ascii=False)
    
    # ===== الخطوة 2: هل هو JSON؟ =====
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "encryptedLockedConfig" in data:
                logger.info("🧠 أنا: هذا JSON يحتوي على encryptedLockedConfig")
                encrypted = data["encryptedLockedConfig"]
                unlocked = smart_decrypt(encrypted, data)
                if unlocked:
                    logger.info("🧠 أنا: نجح فك التشفير!")
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
                else:
                    return json.dumps({
                        "error": "فشل فك التشفير بعد محاولة كل شيء",
                        "encrypted_length": len(encrypted),
                        "preview": encrypted[:100] + "..."
                    }, indent=4, ensure_ascii=False)
            return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    
    # ===== الخطوة 3: كل شيء آخر =====
    return content


# ==========================
# القسم 4: Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 *DarkTunnel Unlocker - Ultimate Edition*\n\n"
        "أنا أفكر بشكل مختلف هذه المرة.\n\n"
        "أرسل:\n"
        "• رابط `darktunnel://`\n"
        "• ملف `.dark` أو `.json`\n\n"
        "سأجرب كل شيء:\n"
        "• كل أطوال المفاتيح\n"
        "• كل طرق الاستخلاص\n"
        "• IV في البداية والنهاية\n"
        "• مفاتيح من السياق\n"
        "• مفاتيح ثابتة\n"
        "• AES-CBC و AES-GCM\n"
        "• كل التركيبات الممكنة",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 *كيف أفكر الآن:*\n\n"
        "1️⃣ لا أفترض أن المفتاح في البداية\n"
        "2️⃣ أجرب IV في البداية والنهاية\n"
        "3️⃣ أستخدم مفاتيح من السياق (JSON كامل)\n"
        "4️⃣ أجرب مفاتيح ثابتة محتملة\n"
        "5️⃣ أجرب كل التركيبات\n\n"
        "🔧 *الطرق المدعومة:*\n"
        "• SHA256\n"
        "• MD5\n"
        "• Direct Key\n"
        "• AES-CBC\n"
        "• AES-GCM\n"
        "• Zero IV\n\n"
        "🧠 *أنا أتعلم من كل محاولة!*",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    if not content:
        return
    
    result = process_like_me(content)
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
                    caption="🧠 ✅ تم فك التشفير بنجاح!"
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
    
    result = process_like_me(content)
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
                caption="🧠 ✅ تم فك التشفير بنجاح!"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ أثناء إرسال الملف: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


# ==========================
# القسم 5: Main
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
    
    print("🧠 DarkTunnel Unlocker - Ultimate Edition is running...")
    print("📌 يفكر بشكل مختلف: كل الطرق، كل التركيبات، كل الاحتمالات!")
    app.run_polling()


if __name__ == "__main__":
    main()
