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
    Tries multiple methods to derive the key.
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
        
        # Clean up the rest (remove whitespace, newlines)
        rest = rest.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        # Add padding for Base64
        padding = len(rest) % 4
        if padding:
            rest += "=" * (4 - padding)
        
        # Method 1: SHA256 on key part
        try:
            key = hashlib.sha256(key_part.encode('utf-8')).digest()
            raw = base64.b64decode(rest)
            if len(raw) >= 16:
                iv = raw[:16]
                ciphertext = raw[16:]
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                result = json.loads(decrypted.decode('utf-8'))
                if result:
                    return result
        except Exception as e:
            logger.debug(f"AES Method 1 failed: {e}")
        
        # Method 2: Use key part directly (first 32 bytes)
        try:
            key = key_part.encode('utf-8')[:32]
            if len(key) < 32:
                key = key.ljust(32, b'\0')
            raw = base64.b64decode(rest)
            if len(raw) >= 16:
                iv = raw[:16]
                ciphertext = raw[16:]
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                result = json.loads(decrypted.decode('utf-8'))
                if result:
                    return result
        except Exception as e:
            logger.debug(f"AES Method 2 failed: {e}")
        
        # Method 3: Use SHA256 on the full encrypted text
        try:
            key = hashlib.sha256(encrypted_text.encode('utf-8')).digest()
            raw = base64.b64decode(rest)
            if len(raw) >= 16:
                iv = raw[:16]
                ciphertext = raw[16:]
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
                result = json.loads(decrypted.decode('utf-8'))
                if result:
                    return result
        except Exception as e:
            logger.debug(f"AES Method 3 failed: {e}")
        
        return None
        
    except Exception as e:
        logger.debug(f"AES decrypt failed: {e}")
        return None


def try_base64_decrypt(encrypted_text: str) -> Optional[Dict[str, Any]]:
    """
    Fallback: treat the text as plain Base64-encoded JSON.
    """
    try:
        encrypted_text = encrypted_text.strip()
        encrypted_text = encrypted_text.replace('\n', '').replace('\r', '').replace(' ', '')
        padding = len(encrypted_text) % 4
        if padding:
            encrypted_text += "=" * (4 - padding)
        
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


def process_darktunnel_link(link: str) -> Optional[str]:
    """Extract and decrypt from darktunnel:// link."""
    try:
        if not link.startswith("darktunnel://"):
            return None
        encoded = link.replace("darktunnel://", "")
        padding = len(encoded) % 4
        if padding:
            encoded += "=" * (4 - padding)
        decoded = base64.urlsafe_b64decode(encoded)
        data = json.loads(decoded.decode('utf-8'))
        
        # If it has encryptedLockedConfig, decrypt it
        if "encryptedLockedConfig" in data:
            unlocked = decrypt_locked_config(data["encryptedLockedConfig"])
            if unlocked:
                return json.dumps(unlocked, indent=4, ensure_ascii=False)
        
        # Otherwise return the decoded data as is
        return json.dumps(data, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"DarkTunnel link processing failed: {e}")
        return None


def process_locked_file(content: str) -> Optional[str]:
    """Process file content: if it contains 'encryptedLockedConfig', unlock it."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    
    if not isinstance(data, dict):
        return None
    
    # If it has encryptedLockedConfig, decrypt it
    if "encryptedLockedConfig" in data:
        encrypted = data["encryptedLockedConfig"]
        unlocked = decrypt_locked_config(encrypted)
        if unlocked:
            return json.dumps(unlocked, indent=4, ensure_ascii=False)
    
    # If it's a darktunnel link inside the file
    if "darktunnel://" in content:
        return process_darktunnel_link(content)
    
    # If it's valid JSON, return it formatted
    return json.dumps(data, indent=4, ensure_ascii=False)


def process_any_text(content: str) -> Optional[str]:
    """Process any text content - tries multiple methods."""
    content = content.strip()
    if not content:
        return None
    
    # Try 1: DarkTunnel link
    if content.startswith("darktunnel://"):
        result = process_darktunnel_link(content)
        if result:
            return result
    
    # Try 2: JSON with encryptedLockedConfig
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "encryptedLockedConfig" in data:
                encrypted = data["encryptedLockedConfig"]
                unlocked = decrypt_locked_config(encrypted)
                if unlocked:
                    return json.dumps(unlocked, indent=4, ensure_ascii=False)
            # Valid JSON, format it
            return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    
    # Try 3: Base64 encoded JSON
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
                return json.dumps(data, indent=4, ensure_ascii=False)
            except Exception:
                continue
    except Exception:
        pass
    
    # Try 4: If it contains darktunnel:// in the middle
    if "darktunnel://" in content:
        for line in content.split('\n'):
            if "darktunnel://" in line:
                result = process_darktunnel_link(line.strip())
                if result:
                    return result
    
    # If everything fails, return the original text
    return content


# ==========================
# Telegram Handlers
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أرسل لي ملفاً نصياً أو رابطاً، وسأحاول استخراج البيانات منه.\n\n"
        "📌 يدعم:\n"
        "• روابط darktunnel://\n"
        "• ملفات .dark\n"
        "• ملفات .json\n"
        "• ملفات .txt\n"
        "• أي ملف نصي"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 أرسل ملفاً نصياً أو رابط darktunnel://\n\n"
        "سأحاول:\n"
        "1. فك تشفير AES إذا كان مشفراً\n"
        "2. فك Base64 إذا كان مشفراً\n"
        "3. تنسيق JSON إذا كان صحيحاً\n"
        "4. عرض النص كما هو"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = update.message.text.strip()
    if not content:
        return
    
    result = process_any_text(content)
    if result:
        # If result is very long, send as file
        if len(result) > 4000:
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
                    f.write(result)
                    temp_path = f.name
                with open(temp_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename="extracted_data.json",
                        caption="✅ تم الاستخراج بنجاح!"
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
    else:
        await update.message.reply_text(
            "⚠️ لم أتمكن من استخراج بيانات من هذا النص."
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
    
    # Process the content
    result = process_any_text(content)
    if not result:
        await update.message.reply_text(
            "⚠️ لم أتمكن من استخراج بيانات من هذا الملف."
        )
        return
    
    # Send the result
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(result)
            temp_path = f.name
        
        with open(temp_path, "rb") as f:
            original_name = document.file_name or "file"
            base_name = os.path.splitext(original_name)[0] or "extracted"
            await update.message.reply_document(
                document=f,
                filename=f"{base_name}_extracted.json",
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
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit(
            "❌ لم يتم تعيين BOT_TOKEN. الرجاء استبدال القيمة الافتراضية."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
