FROM python:3.11-slim

WORKDIR /app

# تثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# تثبيت Playwright + Chromium
RUN playwright install chromium
RUN playwright install-deps chromium

# نسخ الكود
COPY cloud_run_bot.py .

CMD ["python", "cloud_run_bot.py"]
