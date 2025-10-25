import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    MessageHandler, 
    filters
)

# --- НАСТРОЙКИ ---
# ВАЖНО: В реальном проекте токен лучше брать из переменных окружения!
# Например, token = os.environ.get("BOT_TOKEN")
TOKEN = "8366243896:AAEwLamsHM11rjU-owvX7UUSQtSDE_Ucs2c" 

# Настройка логирования для вывода информации в консоль
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# 1. ОБРАБОТЧИК КОМАНДЫ /start (запрашивает локацию)
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветствие и создает клавиатуру с кнопкой запроса локации."""
    
    # Создание клавиатуры с одной специальной кнопкой
    keyboard = [
        [
            KeyboardButton(
                text="📍 Отправить моё местоположение", 
                request_location=True # !!! Ключевой параметр для запроса координат
            )
        ]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Привет! Для построения маршрута мне нужны твои координаты.\n"
        "Пожалуйста, нажми на кнопку ниже, чтобы поделиться своим текущим местоположением.",
        reply_markup=reply_markup
    )

# -------------------------------------------------------------
# 2. ОБРАБОТЧИК СООБЩЕНИЯ С КООРДИНАТАМИ (LOCATION)
# -------------------------------------------------------------
async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает координаты от пользователя и выводит их."""
    
    # Получаем объект Location из сообщения
    user_location = update.message.location
    
    # Извлекаем широту (latitude) и долготу (longitude)
    latitude = user_location.latitude
    longitude = user_location.longitude
    
    logger.info(f"Получены координаты от пользователя {update.effective_user.id}: LAT={latitude}, LON={longitude}")

    # --- ЗДЕСЬ БУДЕТ ВАША ЛОГИКА ---
    # 1. Запрос к LLM на основе текста пользователя (если он был перед отправкой локации)
    # 2. Обращение к базе данных (PostgreSQL) для получения признаков/точек маршрута
    # 3. Вызов Maps API для построения маршрута
    # ---------------------------------
    
    await update.message.reply_text(
        f"Спасибо! Я получил твои координаты:\n"
        f"Широта (Lat): `{latitude}`\n"
        f"Долгота (Lon): `{longitude}`\n\n"
        "Теперь я могу использовать их для построения маршрута."
    )
    
# -------------------------------------------------------------
# 3. ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# -------------------------------------------------------------
def main() -> None:
    """Запускает бота."""
    
    # 1. Создание приложения
    application = ApplicationBuilder().token(TOKEN).build()

    # 2. Регистрация обработчиков
    # Обработчик для команды /start
    application.add_handler(CommandHandler("start", start))
    
    # Обработчик для сообщений с типом content_types='location'
    # Это сработает, когда пользователь отправит геолокацию
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # 3. Запуск бота (Polling)
    logger.info("Бот запущен и ожидает сообщений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()