import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

TOKEN = "8366243896:AAEwLamsHM11rjU-owvX7UUSQtSDE_Ucs2c" 

# Настройка логирования для вывода информации в консоль
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Асинхронная функция-обработчик для команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f'Привет, {update.effective_user.first_name}! Я эхо-бот. Отправь мне что-нибудь.')

# Асинхронная функция-обработчик для текстовых сообщений (эхо)
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Отправляем в ответ тот же самый текст
    await update.message.reply_text(update.message.text)

def main():
    # Создание приложения бота
    application = ApplicationBuilder().token(TOKEN).build()

    # Регистрация обработчиков
    # При команде /start вызывается функция start
    application.add_handler(CommandHandler("start", start))

    # При получении любого текстового сообщения вызывается функция echo
    # filters.TEXT исключает другие типы сообщений (фото, стикеры и т.д.)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))

    # Запуск бота (Polling - опрос сервера на наличие новых сообщений)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()