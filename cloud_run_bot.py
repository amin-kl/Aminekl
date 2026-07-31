"""
╔══════════════════════════════════════════════════════════════╗
║   Cloud Run Bot  v2                                          ║
║   بوت مستقل — يستقبل الرابط الطويل SSO من البوت الأول      ║
║   ① دخول Console عبر SSO                                    ║
║   ② قبول "I understand" / Terms / "Agree and continue"      ║
║   ③ فتح Cloud Run Create مع project_id الصحيح              ║
║   ④ اختيار "Deploy from existing container image"           ║
║   ⑤ Container Image URL                                      ║
║   ⑥ Region → europe-west1                                   ║
║   ⑦ Allow public access                                     ║
║   ⑧ Request-based Billing                                   ║
║   ⑨ Auto scaling  Min=8  Max=16                             ║
║   ⑩ Ingress → All                                           ║
║   ⑪ Containers panel: Timeout=3600  Concurrency=1000        ║
║   ⑫ ضغط Create                                              ║
║   ⑬ انتظار run.app URL وإرساله                             ║
║   ✅ [v2] /screenshot — صورة لحظية من المتصفح في أي وقت    ║
╚══════════════════════════════════════════════════════════════╝

كيفية التشغيل:
    pip install aiogram playwright
    playwright install chromium
    python cloud_run_bot.py

المتغيرات البيئية المطلوبة (أو عدّل CONF أدناه):
    BOT2_TOKEN   — توكن بوت Telegram
    BOT2_OWNER   — chat_id المالك (يستقبل كل الصور والنتائج)
    CONTAINER_URL — docker.io/username/image:latest  (اختياري: يُحفظ لاحقاً)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import random
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from playwright.async_api import Page, async_playwright

# ══════════════════════════════════════════════════════════════
#  إعداد اللوغ
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  الإعدادات — عدّلها أو ضعها في متغيرات البيئة
# ══════════════════════════════════════════════════════════════
CONF = {
    "BOT_TOKEN":     os.getenv("BOT2_TOKEN",     "8901983423:AAFmGQvQCkFA4WRRCHs0bm0l6Bq9exehWS4"),
    "OWNER_ID":      int(os.getenv("BOT2_OWNER", "8372270954")),
    "CONTAINER_URL": os.getenv("CONTAINER_URL",  "docker.io/aminekl2007/vless:latest"),
}

# ══════════════════════════════════════════════════════════════
#  متغير global للصفحة — يُتيح /screenshot في أي وقت
# ══════════════════════════════════════════════════════════════
_active_page: "Page | None" = None      # الصفحة الحالية
_active_chat_id: int | None  = None     # من طلب العملية (لإرسال الصورة له أيضاً)

# إعدادات Cloud Run الثابتة (كما في الصور)
CR = {
    "region":      "europe-west1",
    "min_inst":    "8",
    "max_inst":    "16",
    "timeout":     "3600",
    "concurrency": "1000",
}


# ══════════════════════════════════════════════════════════════
#  FSM States
# ══════════════════════════════════════════════════════════════
class St(StatesGroup):
    waiting_sso_url       = State()   # ينتظر الرابط الطويل
    waiting_container_url = State()   # ينتظر Container Image URL


# ══════════════════════════════════════════════════════════════
#  مساعد: استخرج project_id + email + token من الرابط الطويل
# ══════════════════════════════════════════════════════════════
def _parse_sso_url(url: str) -> dict | None:
    """
    يستخرج project_id و email من رابط SSO الطويل.
    مثال رابط:
      https://accounts.google.com/v3/signin/speedb?...
      أو رابط /api2/... مع project في query string
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # ── project_id ──────────────────────────────────────
        project_id = None
        # طريقة 1: من الـ query params مباشرة
        for key in ("project", "project_id"):
            v = params.get(key, [None])[0]
            if v and v.startswith("qwiklabs-gcp-"):
                project_id = v
                break

        # طريقة 2: من داخل fallback/relay/continue
        if not project_id:
            for key in ("fallback", "relay", "continue", "saml_request_path"):
                raw = unquote(unquote(params.get(key, [""])[0]))
                m = re.search(r"(qwiklabs-gcp-[0-9a-z\-]+)", raw)
                if m:
                    project_id = m.group(1)
                    break

        # طريقة 3: بحث شامل في الرابط كله
        if not project_id:
            m = re.search(r"(qwiklabs-gcp-[0-9a-z\-]+)", unquote(url))
            if m:
                project_id = m.group(1)

        # ── student email ────────────────────────────────────
        email = None
        for key in ("fallback", "relay", "continue", "Email", "email"):
            raw = unquote(unquote(params.get(key, [""])[0]))
            m = re.search(r"(student-\S+@qwiklabs\.net)", raw)
            if m:
                email = m.group(1)
                break
        if not email:
            m = re.search(r"(student-\S+@qwiklabs\.net)", unquote(url))
            if m:
                email = m.group(1)

        if not project_id:
            return None   # رابط غير صالح

        return {
            "project_id": project_id,
            "email":      email or "unknown@qwiklabs.net",
            "raw_url":    url,
        }
    except Exception as e:
        log.warning(f"_parse_sso_url: {e}")
        return None


def _valid_container(url: str) -> bool:
    return bool(re.match(
        r"^(docker\.io/|ghcr\.io/|[\w.\-]+\.io/)?[\w.\-]+/[\w.\-]+(:([\w.\-]+))?$",
        url.strip(),
    ))


# ══════════════════════════════════════════════════════════════
#  مساعد Telegram: أرسل صورة للمالك
# ══════════════════════════════════════════════════════════════
async def _snap(page: Page, bot: Bot, caption: str) -> None:
    try:
        data = await page.screenshot(full_page=False, type="jpeg", quality=72)
        await bot.send_photo(
            chat_id=CONF["OWNER_ID"],
            photo=BufferedInputFile(data, filename="snap.jpg"),
            caption=f"📸 {caption}",
        )
    except Exception as e:
        log.warning(f"_snap: {e}")


async def _msg(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  مساعد: ضغط زر بشري (تحريك + ضغط + انتظار عشوائي)
# ══════════════════════════════════════════════════════════════
async def _human_click(page: Page, locator) -> None:
    """يضغط بطريقة تشبه الإنسان."""
    try:
        box = await locator.bounding_box()
        if box:
            cx = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
            cy = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
            await page.mouse.move(cx, cy, steps=random.randint(3, 8))
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await page.mouse.click(cx, cy)
        else:
            await locator.click()
    except Exception:
        await locator.click(force=True)


async def _try_click(page: Page, selectors: list[str], timeout: int = 3000) -> bool:
    """يجرب قائمة selectors ويضغط أول واحد مرئي."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=timeout):
                await _human_click(page, loc)
                return True
        except Exception:
            continue
    return False


async def _fill_field(page: Page, selectors: list[str], value: str, timeout: int = 3000) -> bool:
    """يملأ حقل نصي."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=timeout):
                await loc.triple_click()
                await asyncio.sleep(0.1)
                await loc.fill(value)
                return True
        except Exception:
            continue
    return False


# ══════════════════════════════════════════════════════════════
#  المنطق الرئيسي: Playwright
# ══════════════════════════════════════════════════════════════
async def _run_cloud_run(
    lab_info:      dict,
    container_url: str,
    bot:           Bot,
    chat_id:       int,
    status_msg:    Message,
) -> str | None:
    """
    يفتح SSO → يدخل Console → ينشئ Cloud Run service.
    يعيد run.app URL أو None.
    """

    async def _edit(text: str) -> None:
        try:
            await status_msg.edit_text(f"⏳ {text}")
        except Exception:
            pass

    project_id = lab_info["project_id"]
    raw_url    = lab_info["raw_url"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,900",
                "--lang=en-US",
            ],
        )
        ctx = await browser.new_context(
            viewport=           {"width": 1366, "height": 900},
            locale=             "en-US",
            timezone_id=        "UTC",
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # حقن stealth أساسي
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)
        page = await ctx.new_page()

        # ── حفظ الصفحة للصورة اللحظية ─────────────────────────
        global _active_page, _active_chat_id
        _active_page    = page
        _active_chat_id = chat_id

        try:
            # ══════════════════════════════════════════════════
            # ① فتح الرابط الطويل SSO
            # ══════════════════════════════════════════════════
            await _edit("جاري فتح رابط الدخول...")
            log.info(f"① فتح SSO: {raw_url[:80]}")
            try:
                await page.goto(raw_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                await page.goto(raw_url, wait_until="commit", timeout=30_000)
            await asyncio.sleep(3)

            # ══════════════════════════════════════════════════
            # ② "I understand" (صفحة Welcome to your new account)
            # ══════════════════════════════════════════════════
            for _wait in range(6):
                _url_now = page.url.lower()
                _content = ""
                try:
                    _content = (await page.content()).lower()
                except Exception:
                    pass

                # "I understand" زر الترحيب
                if "i understand" in _content or "welcome to your new account" in _content:
                    await _edit("ضغط 'I understand'...")
                    clicked = await _try_click(page, [
                        "button:has-text('I understand')",
                        "button:has-text('I Understand')",
                        "[value='I understand']",
                    ])
                    if clicked:
                        await asyncio.sleep(2)
                    break

                # Continue / Next
                if await _try_click(page, [
                    "button:has-text('Continue')",
                    "button:has-text('Next')",
                    "button:has-text('متابعة')",
                ]):
                    await asyncio.sleep(2)
                    continue

                # إذا وصلنا لـ console → نكمل
                if "console.cloud.google.com" in _url_now:
                    break

                await asyncio.sleep(1.5)

            # ══════════════════════════════════════════════════
            # ③ قبول Terms / "Agree and continue"
            # ══════════════════════════════════════════════════
            await _edit("قبول الشروط...")
            for _t in range(4):
                _content = ""
                try:
                    _content = (await page.content()).lower()
                except Exception:
                    pass

                # تفعيل checkboxes
                try:
                    cbs = page.locator("input[type='checkbox']")
                    cnt = await cbs.count()
                    for i in range(cnt):
                        cb = cbs.nth(i)
                        if await cb.is_visible(timeout=800):
                            if not await cb.is_checked():
                                await cb.click()
                                await asyncio.sleep(0.3)
                except Exception:
                    pass

                # ضغط زر الموافقة
                agreed = await _try_click(page, [
                    "button:has-text('Agree and continue')",
                    "button:has-text('Agree')",
                    "button:has-text('Accept')",
                    "button:has-text('I agree')",
                    "button:has-text('Done')",
                    "button:has-text('Confirm')",
                    "[data-value='Agree']",
                ])
                if agreed:
                    await asyncio.sleep(2)
                    break

                if "console.cloud.google.com" in page.url.lower():
                    break
                await asyncio.sleep(1)

            # ══════════════════════════════════════════════════
            # ④ الانتقال لـ Cloud Run Create
            # ══════════════════════════════════════════════════
            await _edit("فتح Cloud Run Create...")
            create_url = (
                f"https://console.cloud.google.com/run/create"
                f"?project={project_id}"
            )
            log.info(f"④ Cloud Run Create: {create_url}")
            try:
                await page.goto(create_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                await page.goto(create_url, wait_until="commit", timeout=30_000)
            await asyncio.sleep(5)

            # تحقق من الدخول
            if "accounts.google.com" in page.url or "signin" in page.url.lower():
                await _snap(page, bot, "❌ لم يتم الدخول — لا يزال في تسجيل الدخول")
                return None

            # إغلاق أي popup إضافي (Terms/Agree) قد يظهر بعد فتح Console
            for _t in range(3):
                try:
                    _content = (await page.content()).lower()
                except Exception:
                    _content = ""
                if "agree and continue" in _content or "terms of service" in _content:
                    try:
                        cbs = page.locator("input[type='checkbox']")
                        cnt = await cbs.count()
                        for i in range(cnt):
                            cb = cbs.nth(i)
                            if await cb.is_visible(timeout=600):
                                if not await cb.is_checked():
                                    await cb.click()
                                    await asyncio.sleep(0.2)
                    except Exception:
                        pass
                    await _try_click(page, [
                        "button:has-text('Agree and continue')",
                        "button:has-text('Agree')",
                        "button:has-text('Accept')",
                    ])
                    await asyncio.sleep(2)
                else:
                    break

            # ══════════════════════════════════════════════════
            # ⑤ "Deploy from existing container image" (radio)
            # ══════════════════════════════════════════════════
            await _edit("اختيار نوع النشر...")
            try:
                # الخيار الأول عادةً هو "Deploy from existing container"
                radios = page.locator("mat-radio-button, [role='radio']")
                if await radios.count() > 0:
                    await _human_click(page, radios.first)
                    await asyncio.sleep(0.8)
            except Exception as e:
                log.warning(f"radio: {e}")

            # ══════════════════════════════════════════════════
            # ⑥ Container Image URL
            # ══════════════════════════════════════════════════
            await _edit("إدخال Container Image URL...")
            img_filled = await _fill_field(page, [
                "input[aria-label*='Container image URL']",
                "input[aria-label*='container image']",
                "input[placeholder*='Container image']",
                "input[placeholder*='container image']",
                "input[formcontrolname*='image']",
                "input[id*='image']",
                "gmat-input input",
                "mat-form-field input",
            ], container_url, timeout=5000)

            if not img_filled:
                log.warning("⚠️ لم يجد حقل Container Image — يحاول JS")
                try:
                    await page.evaluate(f"""
                        () => {{
                            const inputs = document.querySelectorAll('input');
                            for (const inp of inputs) {{
                                const label = (inp.getAttribute('aria-label') || inp.placeholder || '').toLowerCase();
                                if (label.includes('container') || label.includes('image')) {{
                                    inp.value = '{container_url}';
                                    inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                    inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                    break;
                                }}
                            }}
                        }}
                    """)
                except Exception:
                    pass

            await asyncio.sleep(1)

            # ══════════════════════════════════════════════════
            # ⑦ Service name (اتركه auto أو عدّله)
            # ══════════════════════════════════════════════════
            # عادةً يُملأ تلقائياً من اسم الصورة — لا نغيره

            # ══════════════════════════════════════════════════
            # ⑧ Region → europe-west1
            # ══════════════════════════════════════════════════
            await _edit("ضبط الـ Region...")
            region_set = False
            try:
                region_mat = page.locator(
                    "mat-select[formcontrolname='region'],"
                    "mat-select[aria-label*='Region'],"
                    "mat-select[aria-label*='region']"
                ).first
                if await region_mat.is_visible(timeout=5000):
                    await _human_click(page, region_mat)
                    await asyncio.sleep(1.2)
                    opt = page.locator(
                        f"mat-option:has-text('{CR['region']}')"
                    ).first
                    await opt.wait_for(timeout=5000)
                    await _human_click(page, opt)
                    await asyncio.sleep(0.8)
                    region_set = True
            except Exception:
                pass

            if not region_set:
                try:
                    await page.select_option("select[name='region']", value=CR["region"])
                    region_set = True
                except Exception as e:
                    log.warning(f"region fallback: {e}")

            # ══════════════════════════════════════════════════
            # ⑨ Allow public access (Authentication)
            # ══════════════════════════════════════════════════
            await _try_click(page, [
                "mat-radio-button:has-text('Allow unauthenticated')",
                "mat-radio-button:has-text('Allow public')",
                "label:has-text('Allow unauthenticated')",
            ], timeout=3000)
            await asyncio.sleep(0.5)

            # ══════════════════════════════════════════════════
            # ⑩ Billing → Request-based
            # ══════════════════════════════════════════════════
            await _try_click(page, [
                "mat-radio-button:has-text('Request-based')",
            ], timeout=3000)
            await asyncio.sleep(0.4)

            # ══════════════════════════════════════════════════
            # ⑪ Auto scaling
            # ══════════════════════════════════════════════════
            await _try_click(page, [
                "mat-radio-button:has-text('Auto scaling')",
            ], timeout=3000)
            await asyncio.sleep(0.4)

            # Min instances = 8
            await _fill_field(page, [
                "input[formcontrolname='minInstances']",
                "input[aria-label*='Minimum number']",
                "input[aria-label*='minimum']",
                "input[placeholder*='Minimum']",
            ], CR["min_inst"], timeout=3000)

            # Max instances = 16
            await _fill_field(page, [
                "input[formcontrolname='maxInstances']",
                "input[aria-label*='Maximum number']",
                "input[aria-label*='maximum']",
                "input[placeholder*='Maximum']",
            ], CR["max_inst"], timeout=3000)

            # ══════════════════════════════════════════════════
            # ⑫ Ingress → All
            # ══════════════════════════════════════════════════
            try:
                ingress_group = page.locator(
                    "mat-radio-group[formcontrolname='ingressSettings']"
                )
                if await ingress_group.count() > 0:
                    last_radio = ingress_group.locator("mat-radio-button").last
                    await _human_click(page, last_radio)
                else:
                    await _try_click(page, [
                        "mat-radio-button:has-text('All')",
                    ])
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning(f"ingress: {e}")


            # ══════════════════════════════════════════════════
            # ⑬ Containers, Networking, Security → فتح Panel
            # ══════════════════════════════════════════════════
            await _edit("فتح لوحة الإعدادات المتقدمة...")
            try:
                expand = page.locator(
                    "mat-expansion-panel-header:has-text('Containers'),"
                    "button:has-text('Containers, Networking'),"
                    "[aria-label*='Containers, Networking']"
                ).first
                if await expand.is_visible(timeout=4000):
                    await _human_click(page, expand)
                    await asyncio.sleep(2)
            except Exception:
                pass

            # Timeout = 3600
            await _fill_field(page, [
                "input[formcontrolname='timeout']",
                "input[aria-label*='timeout']",
                "input[aria-label*='Request timeout']",
                "input[placeholder*='timeout']",
            ], CR["timeout"], timeout=3000)

            # Concurrency = 1000
            await _fill_field(page, [
                "input[formcontrolname='concurrency']",
                "input[formcontrolname='maxConcurrentRequests']",
                "input[aria-label*='concurrent']",
                "input[aria-label*='Concurrency']",
            ], CR["concurrency"], timeout=3000)


            # ══════════════════════════════════════════════════
            # ⑭ ضغط Create
            # ══════════════════════════════════════════════════
            await _edit("ضغط Create...")
            create_clicked = False
            for sel in [
                "button[type='submit']:has-text('Create')",
                "button:has-text('Create')",
                "[aria-label='Create']",
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=3000):
                        await _human_click(page, loc)
                        create_clicked = True
                        break
                except Exception:
                    continue

            if not create_clicked:
                # JS fallback
                await page.evaluate("""
                    () => {
                        for (const btn of document.querySelectorAll('button')) {
                            if (btn.textContent.trim() === 'Create') {
                                btn.click();
                                break;
                            }
                        }
                    }
                """)
                create_clicked = True

            await asyncio.sleep(3)

            # ══════════════════════════════════════════════════
            # ⑮ انتظار run.app URL (حتى 7 دقائق)
            # ══════════════════════════════════════════════════
            await _edit("انتظار اكتمال الإنشاء... (قد يستغرق 2-5 دقائق)")
            run_app_url = None

            for attempt in range(84):   # 84 × 5s = 7 دقائق
                await asyncio.sleep(5)

                # 1) URL الحالي
                m = re.search(r"(https://[\w\-]+\.run\.app)", page.url)
                if m:
                    run_app_url = m.group(1)
                    break

                # 2) محتوى الصفحة (Endpoint URL)
                try:
                    content = await page.content()
                    m = re.search(
                        r"(https://[\w\-]+\.europe-west1\.run\.app)",
                        content,
                    )
                    if m:
                        run_app_url = m.group(1)
                        break
                    # بحث أعم
                    m = re.search(r"(https://[\w\-]+\.run\.app)", content)
                    if m:
                        run_app_url = m.group(1)
                        break
                except Exception:
                    pass

                # 3) عنصر رابط Endpoint
                try:
                    ep = page.locator("a[href*='.run.app']").first
                    if await ep.is_visible(timeout=500):
                        href = await ep.get_attribute("href") or ""
                        m = re.search(r"(https://[\w\-]+\.run\.app)", href)
                        if m:
                            run_app_url = m.group(1)
                            break
                except Exception:
                    pass

                # كل 30 ثانية: صورة تقدم + رسالة
                if attempt % 6 == 5:
                    await _edit(f"انتظار اكتمال الإنشاء... ({(attempt+1)*5} ثانية)")

                # كشف خطأ
                try:
                    err = page.locator(
                        ".error-message,[class*='error-panel'],[class*='alert-error'],"
                        "[class*='mat-error']"
                    ).first
                    if await err.is_visible(timeout=500):
                        txt = await err.inner_text()
                        await _snap(page, bot, f"❌ خطأ أثناء الإنشاء: {txt[:100]}")
                        return None
                except Exception:
                    pass

            if run_app_url:
                await _snap(page, bot, f"✅ اكتمل!\n🌐 {run_app_url}")
            else:
                await _snap(page, bot, "❌ انتهى الوقت (7 دقائق) دون الحصول على URL")

            return run_app_url

        except Exception as e:
            log.error(f"Playwright error: {e}", exc_info=True)
            try:
                await _snap(page, bot, f"❌ خطأ: {str(e)[:100]}")
            except Exception:
                pass
            return None
        finally:
            _active_page    = None
            _active_chat_id = None
            try:
                await browser.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
#  Telegram Handlers
# ══════════════════════════════════════════════════════════════
dp = Dispatcher(storage=MemoryStorage())


def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ إلغاء")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 <b>Cloud Run Bot v2</b>\n\n"
        "أرسل الرابط الطويل SSO الذي حصلت عليه من البوت الأول\n"
        "(الرابط الذي يبدأ بـ <code>https://accounts.google.com</code> أو يحتوي على <code>qwiklabs</code>)\n\n"
        "📌 أوامر متاحة في أي وقت:\n"
        "• /screenshot — 📸 صورة لحظية من المتصفح\n"
        "• /url — 🌐 الرابط الحالي للمتصفح\n"
        "• /start — إعادة البدء",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(St.waiting_sso_url)


@dp.message(F.text == "❌ إلغاء")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("تم الإلغاء ✅", reply_markup=ReplyKeyboardRemove())


@dp.message(St.waiting_sso_url)
async def handle_sso_url(message: Message, state: FSMContext):
    url = (message.text or "").strip()

    # تحقق بسيط
    if not url.startswith("http"):
        await message.answer("⚠️ أرسل الرابط كاملاً (يبدأ بـ https://)")
        return

    lab_info = _parse_sso_url(url)
    if not lab_info:
        await message.answer(
            "⚠️ لم أتمكن من استخراج <b>project_id</b> من الرابط.\n\n"
            "تأكد أنه الرابط الطويل الكامل من البوت الأول.",
            parse_mode="HTML",
        )
        return

    await state.update_data(lab_info=lab_info)

    # إذا كان Container URL محفوظاً مسبقاً → اسأل فقط عن تأكيد
    default_container = CONF["CONTAINER_URL"]
    if default_container:
        await message.answer(
            f"✅ Project: <code>{lab_info['project_id']}</code>\n"
            f"📧 Email: <code>{lab_info['email']}</code>\n\n"
            f"📦 Container Image المحفوظ:\n<code>{default_container}</code>\n\n"
            f"أرسل <b>OK</b> للاستخدام أو أرسل URL آخر:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="OK"), KeyboardButton(text="❌ إلغاء")]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
    else:
        await message.answer(
            f"✅ Project: <code>{lab_info['project_id']}</code>\n"
            f"📧 Email: <code>{lab_info['email']}</code>\n\n"
            f"📦 أرسل <b>Container Image URL</b>:\n"
            f"مثال: <code>docker.io/username/image:latest</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )

    await state.set_state(St.waiting_container_url)


@dp.message(St.waiting_container_url)
async def handle_container_url(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    lab_info = data.get("lab_info")

    if not lab_info:
        await state.clear()
        await message.answer("❌ انتهت الجلسة. ابدأ من جديد /start")
        return

    # تحديد container_url
    container_url = ""
    if text == "OK" and CONF["CONTAINER_URL"]:
        container_url = CONF["CONTAINER_URL"]
    elif text == "OK":
        await message.answer("⚠️ لا يوجد Container URL محفوظ. أرسل الـ URL:")
        return
    else:
        container_url = text

    # تحقق من صيغة URL
    # نقبل أي نص يحتوي على "/" (مرونة أكبر من regex)
    if "/" not in container_url:
        await message.answer(
            "⚠️ صيغة Container URL غير صحيحة.\n"
            "مثال: <code>docker.io/username/image:latest</code>",
            parse_mode="HTML",
        )
        return

    await state.clear()

    # أرسل إشعاراً للمالك
    bot = message.bot
    await _msg(bot, CONF["OWNER_ID"],
        f"🚀 <b>طلب Cloud Run جديد</b>\n"
        f"👤 {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
        f"🆔 Project: <code>{lab_info['project_id']}</code>\n"
        f"📦 Container: <code>{container_url}</code>"
    )

    status_msg = await message.answer(
        "⏳ <b>جاري الإنشاء...</b>\n"
        f"🆔 Project: <code>{lab_info['project_id']}</code>\n"
        f"📦 Container: <code>{container_url}</code>\n\n"
        "هذا يستغرق عادةً 2-5 دقائق ⏱",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    # ابدأ Playwright
    run_app_url = await _run_cloud_run(
        lab_info=lab_info,
        container_url=container_url,
        bot=bot,
        chat_id=message.chat.id,
        status_msg=status_msg,
    )

    created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if not run_app_url:
        await status_msg.edit_text(
            "❌ <b>فشل إنشاء Cloud Run Service</b>\n\n"
            "الأسباب المحتملة:\n"
            "• انتهت صلاحية رابط SSO\n"
            "• Container Image URL غير صحيح\n"
            "• انتهت مدة المختبر\n\n"
            "أعد الضغط على Start Lab في البوت الأول للحصول على رابط جديد.",
            parse_mode="HTML",
        )
        await _msg(bot, CONF["OWNER_ID"],
            f"❌ <b>فشل Cloud Run</b>\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 {lab_info['project_id']}"
        )
        return

    # نجاح!
    success_text = (
        f"✅ <b>تم إنشاء الخدمة بنجاح! 🎉</b>\n\n"
        f"🌐 <b>Service URL:</b>\n"
        f"<code>{run_app_url}</code>\n\n"
        f"📅 {created_at}"
    )
    await status_msg.edit_text(success_text, parse_mode="HTML")

    # إشعار المالك
    await _msg(bot, CONF["OWNER_ID"],
        f"✅ <b>Cloud Run جاهز!</b>\n"
        f"👤 {message.from_user.full_name} (<code>{message.from_user.id}</code>)\n"
        f"🆔 Project: <code>{lab_info['project_id']}</code>\n"
        f"📦 Container: <code>{container_url}</code>\n"
        f"🌐 URL: <code>{run_app_url}</code>\n"
        f"📅 {created_at}"
    )


@dp.message(F.text.in_({"/screenshot", "📸 صورة لحظية", "صورة"}))
async def cmd_screenshot(message: Message):
    """
    /screenshot — يلتقط صورة فورية من المتصفح الحالي.
    يعمل في أي وقت أثناء تشغيل Playwright.
    """
    global _active_page

    if _active_page is None:
        await message.answer(
            "⚠️ لا يوجد متصفح نشط الآن.\n"
            "ابدأ عملية Cloud Run أولاً ثم اطلب الصورة."
        )
        return

    try:
        now = datetime.utcnow().strftime("%H:%M:%S")
        data = await _active_page.screenshot(
            full_page=False,
            type="jpeg",
            quality=80,
        )
        url_now = _active_page.url[:80]
        await message.answer_photo(
            BufferedInputFile(data, filename="live.jpg"),
            caption=(
                f"📸 <b>صورة لحظية</b>  [{now} UTC]\n"
                f"🌐 <code>{url_now}</code>"
            ),
            parse_mode="HTML",
        )
        # أرسل للمالك أيضاً (إذا الطلب من غير المالك)
        if message.chat.id != CONF["OWNER_ID"]:
            await message.bot.send_photo(
                chat_id=CONF["OWNER_ID"],
                photo=BufferedInputFile(data, filename="live.jpg"),
                caption=(
                    f"📸 <b>صورة لحظية</b> — طلبها "
                    f"{message.from_user.full_name} [{now} UTC]\n"
                    f"🌐 <code>{url_now}</code>"
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        await message.answer(f"❌ فشل التقاط الصورة: {e}")


@dp.message(F.text.in_({"/url", "🌐 الرابط الحالي", "رابط"}))
async def cmd_url(message: Message):
    """يُرسل الـ URL الحالي للمتصفح."""
    global _active_page

    if _active_page is None:
        await message.answer("⚠️ لا يوجد متصفح نشط.")
        return

    try:
        url_now = _active_page.url
        await message.answer(
            f"🌐 <b>الرابط الحالي:</b>\n<code>{url_now}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ خطأ: {e}")


# ══════════════════════════════════════════════════════════════
#  نقطة البداية
# ══════════════════════════════════════════════════════════════
async def main():
    bot = Bot(token=CONF["BOT_TOKEN"])
    log.info("☁️  Cloud Run Bot v2 يعمل... (📸 /screenshot متاح)")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
