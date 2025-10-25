import openai
import os
# БД
import psycopg2
import psycopg2.extras  # Для получения результатов в виде словарей
import json

# OSRM
import math
import logging
import requests
from typing import List, Dict, Any, Tuple, Optional

# Для маршрута яндекс карты
import urllib.parse

#бот
from telegram import KeyboardButton, ReplyKeyboardMarkup
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("AI_Travel")

USER_QUERY_CACHE = {}# Кэш для хранения текстовых запросов пользователей по user_id
VISIT_TIME_MINUTES = 15  # Время на посещение каждого объекта в минутах


# --- ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
TOKEN = os.environ.get("TG_BOT_TOKEN", "") #
API_KEY = os.environ.get("LLM_API_KEY", "")
FOLDER_ID = os.environ.get("LLM_FOLDER_ID", "") #
YANDEX_CLOUD_MODEL = "yandexgpt-lite"

DB_HOST = os.environ.get("DB_HOST", "localhost") #
DB_NAME = os.environ.get("DB_NAME", "") # 
DB_USER = os.environ.get("DB_USER", "") # 
DB_PASSWORD = os.environ.get("DB_PASSWORD", "") #""

# TEST_START_LAT = 56.299251
# TEST_START_LON = 43.985146


# --- КОНФИГУРАЦИЯ СКОРОСТЕЙ И КАТЕГОРИЙ ---
# Скорости для оценки максимального радиуса поиска (м/мин)
SPEED_MAPPINGS = {
    "пеший": 67,  # ~4 км/ч
    "автомобиль": 500,  # ~30 км/ч
    "велосипед": 250,  # ~15 км/ч
    "электросамокат": 250  # ~15 км/ч
}

# Маппинг категорий <- надо будет нам самим определить какие id чему соотвествуют
CATEGORY_MAPPING = {
    "историческая достопримечательность": [1, 4, 5],
    "парк": [2],
    "искусство": [3, 6, 7, 8, 10],
    "религия": [],
    "город": [9]
}

YANDEX_TRANSPORT_MAPPING = {
    "пеший": "pedestrian",
    "автомобиль": "auto",
    "велосипед": "bicycle",
    "электросамокат": "scooter"
}

# --- КЛИЕНТ ДЛЯ OSRM (ОБНОВЛЕН ДЛЯ ИСПОЛЬЗОВАНИЯ ПОЛНОЙ МАТРИЦЫ) ---
class OSRMClient:
    def __init__(self, base_url: str = "http://router.project-osrm.org", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.timeout = timeout
        self.profile_map = {
            "пеший": "foot",
            "велосипед": "bike",
            "автомобиль": "driving",
            "электросамокат": "bike"
        }

    def get_route_duration(self, start: Tuple[float, float], end: Tuple[float, float], mode: str) -> Optional[float]:
        """Оставлено для совместимости или отладки, но не используется в новом алгоритме TSP."""
        profile = self.profile_map.get(mode, "foot")
        coords_str = f"{start[1]},{start[0]};{end[1]},{end[0]}"
        url = f"{self.base_url}/route/v1/{profile}/{coords_str}"

        logger.info(
            f"OSRM Route запрос (НЕОПТИМ.): {profile} от {start[0]:.4f},{start[1]:.4f} до {end[0]:.4f},{end[1]:.4f}")

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if data.get("routes"):
                duration = data["routes"][0].get("duration")
                logger.info(f"OSRM Route ответ: {duration:.1f} сек")
                return duration
            logger.warning(f"OSRM не нашел маршрутов между {start} и {end}")
            return None
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе к OSRM Route: {e}")
            return None

    def get_full_travel_time_matrix(self, coordinates: List[Tuple[float, float]], mode: str) -> Optional[
        List[List[Optional[float]]]]:
        """
        [ОПТИМИЗАЦИЯ] Запрашивает полную матрицу времени поездки между всеми точками (один запрос).
        Координаты: (широта, долгота).
        """
        profile = self.profile_map.get(mode, "foot")
        # Координаты для OSRM (долгота, широта)
        coords_str = ";".join([f"{lon},{lat}" for lat, lon in coordinates])

        # Без параметров sources/destinations OSRM возвращает полную матрицу.
        url = f"{self.base_url}/table/v1/{profile}/{coords_str}"

        logger.info(f"OSRM Table запрос (ПОЛНАЯ МАТРИЦА): {profile} для {len(coordinates)} точек.")

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            if data.get("durations"):
                logger.info(
                    f"OSRM Table ответ: получена полная матрица {len(data['durations'])}x{len(data['durations'][0])}")
                return data["durations"]

            logger.warning("OSRM Table не вернул данных для полной матрицы.")
            return None
        except requests.RequestException as e:
            logger.error(f"Ошибка при запросе полной матрицы к OSRM Table: {e}")
            return None


# --- ИНИЦИАЛИЗАЦИЯ КЛИЕНТА YANDEX CLOUD ---
def create_yandex_client():
    client = openai.OpenAI(
        api_key=API_KEY,
        base_url="https://rest-assistant.api.cloud.yandex.net/v1",
        project=FOLDER_ID
    )
    return client


# --- СОЕДИНЕНИЕ С БД ---
def get_db_connection():
    """Устанавливает соединение с базой данных."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except psycopg2.Error as e:
        print(f"Ошибка подключения к БД: {e}")
        return None


# Функция для создания prompt для LLM
def create_prompt(query):
    prompt_template = """
        Твоя роль — AI-аналитик, который преобразует неструктурированные запросы пользователей в структурированные данные в формате JSON.

        ## ЗАДАЧА
        Проанализируй запрос пользователя о маршруте для прогулки и извлеки из него ключевые параметры согласно указанной ниже структуре и правилам.

        ## СТРУКТУРА JSON И ДОПУСТИМЫЕ ЗНАЧЕНИЯ
        {{
        "start_location": "string | null",
        "distance_km": "integer | null",
        "duration_minutes": "integer | null",
        "travel_mode": "string | null",
        "interests": "string | null"
        }}

        ## ПРАВИЛА
        1. Всегда отвечай только валидным JSON объектом и ничем больше.
        2. Если в запросе указано и время, и расстояние, извлеки оба значения.
        3. Если какое-то поле не упоминается в запросе, используй для него значение `null`.
        4. Для `travel_mode` и `interests` выбери наиболее подходящее значение из списков:
        - travel_mode: "пеший", "автомобиль", "велосипед", "электросамокат"
        - interests: "парк", "историческая достопримечательность", "искусство", "религия","город"
        5. Время всегда переводи в минуты. Например, "полтора часа" -> 90.

        ## ПРИМЕРЫ
        ### Пример 1
        Запрос: "Хочу погулять пешком часик по какому-нибудь парку"
        Ответ:
        {{
            "start_location": null,
            "distance_km": null,
            "duration_minutes": 60,
            "travel_mode": "пеший",
            "interests": "парк"
        }}
        ### Пример 2
        Запрос: "Покажи маршрут на 10 км на велосипеде от Кремля, хочу посмотреть на что-то историческое"
        Ответ:
        {{
            "start_location": "Кремль",
            "distance_km": 10,
            "duration_minutes": null,
            "travel_mode": "велосипед",
            "interests": "историческая достопримечательность"
        }}

        ## ЗАПРОС ПОЛЬЗОВАТЕЛЯ
        <query>
        {query}
        </query>
        """
    return prompt_template.format(query=query)


# Функция для формирования json-ответа от LLM
# to use response.output[0].content[0].text
def encode_query(client, query):
    prompt = create_prompt(query)

    response = client.responses.create(
        model=f"gpt://{FOLDER_ID}/{YANDEX_CLOUD_MODEL}",
        input=prompt,
        temperature=0.2,
        max_output_tokens=1500
    )
    return response  # to use response.output[0].content[0].text


def prepare_query_params(llm_output_json, start_lat, start_lon):
    """
    Обрабатывает JSON от LLM и координаты старта для подготовки SQL-параметров.
    """
    data = json.loads(llm_output_json)

    # 1. Определение максимального радиуса поиска (в метрах)

    # 1a. Определение скорости по travel_mode
    travel_mode = data.get("travel_mode", "пеший").lower()
    speed_m_min = SPEED_MAPPINGS.get(travel_mode, SPEED_MAPPINGS["пеший"])

    radius_from_distance = float('inf')
    radius_from_duration = float('inf')

    # 1b. Расчет радиуса из distance_km
    if data.get("distance_km") is not None and data["distance_km"] > 0:
        radius_from_distance = data["distance_km"] * 1000

    # 1c. Расчет радиуса из duration_minutes
    if data.get("duration_minutes") is not None and data["duration_minutes"] > 0:
        # Рассчитываем радиус исходя из времени и скорости выбранного транспорта (туда и обратно)
        # Делим на 2, т.к. время = "туда + обратно + время на объектах"
        radius_from_duration = (data["duration_minutes"] / 2) * speed_m_min

    # Берем самый строгий (минимальный) радиус, чтобы места были гарантированно доступны
    max_distance_m = min(radius_from_distance, radius_from_duration)

    # Если оба поля null, устанавливаем разумный дефолт (например, 5 км)
    if max_distance_m == float('inf') or max_distance_m == 0:
        max_distance_m = 5000

    # 2. Определение подходящих category_id
    # можно возвращать несколько категорий из llm
    llm_interests = data.get("interests", "").split(',')
    target_ids = set()
    for interest_str in llm_interests:
        interest_str = interest_str.strip().lower()
        if interest_str in CATEGORY_MAPPING:
            target_ids.update(CATEGORY_MAPPING[interest_str])

    # Если интересы не определены, ищем все, чтобы дать LLM выбор
    if not target_ids:
        target_ids = [1, 2, 3, 4, 5, 10]

    # В PostGIS координаты передаются как (Lon, Lat) т.е. нужно менять координаты местами
    return start_lon, start_lat, list(target_ids), max_distance_m


def find_suitable_objects(llm_json_input, start_lat, start_lon):
    """
    Выполняет PostGIS-запрос и возвращает подходящие объекты.
    """

    # 1. Подготовка параметров
    user_lon, user_lat, category_ids, max_distance_m = \
        prepare_query_params(llm_json_input, start_lat, start_lon)

    if not category_ids:
        print("Ошибка: Не удалось определить категории интересов.")
        return []

    category_list_sql = ', '.join(map(str, category_ids))
    conn = get_db_connection()
    if not conn: return []
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 2. Основной SQL-запрос с PostGIS
    sql_query = f"""
    SELECT
        id, title, description, category_id, address,
        ST_X(geom) AS longitude,
        ST_Y(geom) AS latitude,
        -- ST_Distance: точное расстояние от точки старта до объекта (в метрах)
        ST_Distance(
            geom::geography, 
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography 
        ) AS distance_m 
    FROM
        cultural_objects
    WHERE
        -- Фильтрация по интересам
        category_id IN ({category_list_sql})
        AND
        -- ST_DWithin: быстрая гео-фильтрация в пределах max_distance_m
        ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s 
        )
    ORDER BY
        distance_m ASC
    LIMIT 20; -- Берем до 20 ближайших объектов для дальнейшей оптимизации маршрута
    """

    # 3. Передача параметров
    # Здесь создается кортеж со всеми значениями, которые нужно подставить в SQL-запрос вместо знаков %s.
    #  Обратите внимание, что координаты пользователя (user_lon, user_lat) используются дважды, так как они нужны и в ST_Distance,
    #  и в ST_DWithin.
    params = (
        user_lon, user_lat,
        user_lon, user_lat, max_distance_m
    )

    try:
        cursor.execute(sql_query, params)
        # Результат (до 20 строк) сохраняется на стороне сервера и становится
        # доступен через объект cursor.
        suitable_objects = [dict(row) for row in cursor.fetchall()]
        # Эта команда забирает все строки результата, которые нашел cursor,
        # и передает их в Python в виде списка. Каждый элемент списка — это тот самый DictRow объект (похожий на словарь).

        print(f"Радиус поиска: {max_distance_m:.0f} м")
        return suitable_objects
    except psycopg2.Error as e:
        print(f"Ошибка выполнения SQL-запроса: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def build_route(start_point: Tuple[float, float], candidate_objects: List[Dict[str, Any]], llm_params: Dict[str, Any],
                osrm_client: OSRMClient) -> Dict[str, Any]:
    """
    Основная функция для построения маршрута, использует оптимизированный алгоритм.
    """
    # 1. Извлекаем параметры из вывода LLM
    mode = llm_params.get("travel_mode", "пеший")
    max_time_s = (llm_params.get("duration_minutes") or 60) * 60  # Если время не задано, берем 60 минут
    visit_time_s = VISIT_TIME_MINUTES * 60

    # 2. Адаптируем формат объектов для алгоритма
    pois = [
        {
            "id": obj["id"],
            "name": obj["title"],
            "lat": obj["latitude"],
            "lon": obj["longitude"],
            "address": obj.get("address"),
            "category_id": obj.get("category_id")
        } for obj in candidate_objects
    ]

    # 3. Запускаем ОПТИМИЗИРОВАННЫЙ "жадный" алгоритм с матрицей
    route_chain, total_travel_time = _greedy_route_with_matrix(
        start_point, pois, mode, max_time_s, visit_time_s, osrm_client
    )

    # 4. Форматируем финальный результат
    return _format_result(route_chain, total_travel_time, visit_time_s)


def _greedy_route_with_matrix(start: Tuple[float, float], pois: List[Dict[str, Any]], mode: str, max_time_s: float,
                              visit_time_s: float, osrm_client: OSRMClient) -> Tuple[List[Dict[str, Any]], float]:
    """
    [НОВЫЙ ОПТИМИЗИРОВАННЫЙ АЛГОРИТМ]
    Использует полную матрицу времени OSRM, чтобы свести все расчеты времени к ОДНОМУ внешнему запросу.
    """

    if not pois:
        return [], 0.0

    # 1. Сбор всех координат и присвоение индексов
    # Индекс 0 - это Старт. Индексы 1..N - это POI.
    all_coords = [start] + [(p["lat"], p["lon"]) for p in pois]

    # Создаем маппинг Индекс -> Исходный Объект POI
    poi_index_to_object = {i + 1: pois[i] for i in range(len(pois))}

    # 2. ОДИН ЗАПРОС к OSRM за ПОЛНОЙ МАТРИЦЕЙ
    travel_time_matrix = osrm_client.get_full_travel_time_matrix(all_coords, mode)

    if not travel_time_matrix:
        logger.error("Не удалось получить матрицу времени от OSRM. Маршрут не построен.")
        return [], 0.0

    # 3. Инициализация алгоритма
    current_index = 0  # Начинаем со Старта (индекс 0)
    remaining_indices = set(range(1, len(all_coords)))  # Индексы POI (1 до N)
    route_indices = []
    total_time = 0
    total_travel_time = 0

    # 4. "Жадный" поиск в памяти (мгновенно)
    while remaining_indices and total_time < max_time_s:
        nearest_poi_index = None
        min_travel_duration = float('inf')

        # Перебираем все оставшиеся POI
        for next_index in remaining_indices:
            # Получаем время из матрицы: travel_time_matrix[от_кого][до_кого]
            travel_duration = travel_time_matrix[current_index][next_index]

            if travel_duration is not None and travel_duration < min_travel_duration:
                min_travel_duration = travel_duration
                nearest_poi_index = next_index

        # Если ближайшая точка найдена
        if nearest_poi_index is not None:
            # Проверяем, укладываемся ли во время
            if total_time + min_travel_duration + visit_time_s <= max_time_s:
                # Обновляем маршрут и время
                route_indices.append(nearest_poi_index)
                total_time += min_travel_duration + visit_time_s
                total_travel_time += min_travel_duration

                # Переходим к следующей точке
                current_index = nearest_poi_index
                remaining_indices.remove(nearest_poi_index)
            else:
                # Времени на следующую точку не хватает
                break
        else:
            # Если не удалось найти маршрут ни до одной из оставшихся точек
            break

    # 5. Формирование финального результата
    final_route_chain = [poi_index_to_object[i] for i in route_indices]

    return final_route_chain, total_travel_time

def _format_result(route_chain: List[Dict[str, Any]], total_travel_time: float, visit_time_s: float) -> Dict[str, Any]:
    """
    Форматирует результат в красивый JSON для вывода.
    (Это почти точная копия метода _format_result из WalkPlanner)
    """
    if not route_chain:
        return {"success": False,
                "message": "Не удалось построить маршрут. Возможно, не хватило времени или не найдено подходящих объектов."}

    total_visit_time = len(route_chain) * visit_time_s
    total_duration = total_travel_time + total_visit_time

    return {
        "success": True,
        "message": f"Построен маршрут через {len(route_chain)} точек.",
        "total_pois": len(route_chain),
        "total_duration_min": round(total_duration / 60, 1),
        "total_travel_time_min": round(total_travel_time / 60, 1),
        "total_visit_time_min": round(total_visit_time / 60, 1),
        "pois": route_chain  # Возвращаем полный список POI со всеми данными
    }


def generate_yandex_route_url(coordinates, transport_type):
    """
    Формирует URL-ссылку на Яндекс Карты с заданным маршрутом.
    """
    coord_strings = [f"{lat},{lon}" for lat, lon in coordinates]
    rtext_value = "~".join(coord_strings)
    base_url = "https://yandex.ru/maps/"
    params = {'rtext': rtext_value, 'rtt': transport_type}
    query_string = urllib.parse.urlencode(params, safe=',')
    final_url = f"{base_url}?{query_string}"
    return final_url


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            KeyboardButton(
                text="📍 Отправить моё местоположение", 
                request_location=True
            )
        ]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Привет! Я помогу построить маршрут.\n"
        "Сначала опиши, куда ты хочешь пойти (например, 'хочу пешком 2 часа по паркам'), а затем нажми на кнопку, чтобы поделиться своим местоположением.",
        reply_markup=reply_markup
    )


async def handle_text_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кэширует текстовый запрос пользователя и просит локацию."""
    user_id = update.effective_user.id
    query = update.message.text
    
    USER_QUERY_CACHE[user_id] = query
    logger.info(f"Кэширован запрос от {user_id}: {query}")

    keyboard = [
        [
            KeyboardButton(
                text="📍 Отправить моё местоположение", 
                request_location=True
            )
        ]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        f"Понял: '{query}'. Теперь мне нужны твои координаты. Пожалуйста, отправь геолокацию.",
        reply_markup=reply_markup
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает координаты, выполняет LLM-анализ и строит маршрут."""
    
    user_id = update.effective_user.id
    user_location = update.message.location
    latitude = user_location.latitude
    longitude = user_location.longitude

    logger.info(f"Получены координаты от {user_id}: LAT={latitude}, LON={longitude}")
    await update.message.reply_text("Получил координаты. Запускаю анализ запроса и построение маршрута, это может занять минуту...")


    # 1. Извлекаем текстовый запрос
    # Если нет кэшированного запроса, используем дефолт
    query = USER_QUERY_CACHE.pop(user_id, "Хочу пешком 90 минут по историческим местам")
    
    try:
        yandex_client = create_yandex_client()
        osrm_client = OSRMClient()
    except Exception as e:
        logger.error(f"Ошибка инициализации клиентов: {e}")
        await update.message.reply_text("Извините, внутренняя ошибка при инициализации сервисов.")
        return

    try:
        response = encode_query(yandex_client, query)
        llm_output_json = response.output[0].content[0].text
        
        # Заглушка для тестирования без LLM
        # llm_output_json = """
        # {
        #     "start_location": null,
        #     "distance_km": null,
        #     "duration_minutes": 150,
        #     "travel_mode": "пеший",
        #     "interests": "историческая достопримечательность"
        # }
        # """

        llm_params = json.loads(llm_output_json)

        # 3. DB: Ищем подходящие объекты
        # Используем полученные от пользователя координаты (latitude, longitude)
        candidate_objects = find_suitable_objects(llm_output_json, latitude, longitude)
        
        if not candidate_objects:
             await update.message.reply_text("Не удалось найти подходящие объекты в заданном радиусе и по интересам. Попробуйте другой запрос.")
             return
        
        # 4. OSRM: Строим маршрут
        start_point = (latitude, longitude)
        final_route = build_route(start_point, candidate_objects, llm_params, osrm_client)

        # 5. Форматируем и отправляем ответ
        if final_route.get("success"):

            all_points_coords = [start_point]
            poi_coords = [(poi['lat'], poi['lon']) for poi in final_route['pois']]
            all_points_coords.extend(poi_coords)
            all_points_coords.append(start_point)

            travel_mode = llm_params.get("travel_mode", "пеший")
            yandex_transport_type = YANDEX_TRANSPORT_MAPPING.get(travel_mode, "pedestrian")

            route_url = generate_yandex_route_url(all_points_coords, yandex_transport_type)
            
            route_text = (
                f"✅ **Маршрут построен!**\n\n"
                f"🚶 Способ передвижения: {travel_mode.capitalize()}\n"
                f"⏱ Общее время маршрута: **{final_route['total_duration_min']:.1f} мин.**\n"
                f"📍 Количество посещений: {final_route['total_pois']}\n\n"
            )
            
            poi_list = "\n".join([
                f"{i+1}. [{poi['name']}]({route_url})" # Ссылка может вести на общий маршрут
                for i, poi in enumerate(final_route['pois'])
            ])
            
            await update.message.reply_text(
                route_text + f"**Объекты для посещения:**\n{poi_list}\n\n"
                f"🗺 [Открыть маршрут на Яндекс Картах]({route_url})",
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        else:
            await update.message.reply_text(f"Не удалось построить оптимальный маршрут: {final_route.get('message', 'Неизвестная ошибка')}")

    except Exception as e:
        logger.error(f"Критическая ошибка при обработке маршрута: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла непредвиденная ошибка при расчете маршрута.")


# -------------------------------------------------------------
# 5. ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# -------------------------------------------------------------
def main() -> None:
    """Запускает бота."""
    
    if not TOKEN:
         logger.error("Токен Telegram-бота не установлен. Проверьте переменную TG_BOT_TOKEN в .env.")
         return

    application = ApplicationBuilder().token(TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    
    # 1. Обрабатываем текстовый запрос (кэшируем)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_query))
    
    # 2. Обрабатываем геолокацию (запускаем маршрутизацию)
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Запуск
    logger.info("Бот запущен и ожидает сообщений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()






# client = create_yandex_client()
# json_query = encode_query(client, query).output[0].content[0].text

# json_query = """
# {
#     "start_location": null,
#     "distance_km": null,
#     "duration_minutes": 150,
#     "travel_mode": "пеший",
#     "interests": "историческая достопримечательность"
# }
# """
# print(f"--- JSON от LLM ---\n{json_query}\n")

# # 2. Ищем подходящие объекты в БД
# candidate_objects = find_suitable_objects(json_query, TEST_START_LAT, TEST_START_LON)
# print("OOOOKKKK1")
# # 3. Строим маршрут
# osrm_client = OSRMClient()
# print("OOOOKKKK2")
# start_point = (TEST_START_LAT, TEST_START_LON)
# print("OOOOKKKK3")
# llm_params = json.loads(json_query)
# print("OOOOKKKK4")
# final_route = build_route(start_point, candidate_objects, llm_params, osrm_client)
# print("OOOOKKKK5")

# # 4. --- ИНТЕГРАЦИЯ: Генерируем ссылку на Яндекс Карты, если маршрут построен ---
# if final_route.get("success"):
#     # Собираем полный список координат: Старт -> Точки -> Старт
#     all_points_coords = [start_point]
#     poi_coords = [(poi['lat'], poi['lon']) for poi in final_route['pois']]
#     all_points_coords.extend(poi_coords)
#     all_points_coords.append(start_point)

#     # Определяем тип транспорта
#     travel_mode = llm_params.get("travel_mode", "пеший")
#     yandex_transport_type = YANDEX_TRANSPORT_MAPPING.get(travel_mode, "pedestrian")

#     # Генерируем URL и добавляем в результат
#     route_url = generate_yandex_route_url(all_points_coords, yandex_transport_type)
#     final_route["yandex_maps_url"] = route_url

# # 5. Выводим итоговый результат
# print("\n--- ИТОГОВЫЙ МАРШРУТ ---")
# print(json.dumps(final_route, indent=2, ensure_ascii=False))
