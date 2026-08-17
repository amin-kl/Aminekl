import telebot
import time
import threading

TOKEN = "8901983423:AAFmGQvQCkFA4WRRCHs0bm0l6Bq9exehWS4"

bot = telebot.TeleBot(TOKEN)

frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

@bot.message_handler(commands=["start"])
def start(message):
    msg = bot.send_message(message.chat.id, "جاري تجهيز ⠋")

    def animate():
        for _ in range(3):  # عدد دورات الحركة
            for frame in frames:
                try:
                    bot.edit_message_text(
                        f"جاري تجهيز {frame}",
                        message.chat.id,
                        msg.message_id
                    )
                    time.sleep(0.12)
                except Exception:
                    return

        bot.edit_message_text(
            "تم التجهيز ✓",
            message.chat.id,
            msg.message_id
        )

    threading.Thread(target=animate, daemon=True).start()


bot.infinity_polling()
