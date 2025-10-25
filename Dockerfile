# 1. Базовый образ
# Используем slim-версию Python 3.10 для уменьшения размера образа
FROM python:3.10-slim

# 2. Установка системных зависимостей
# Psycopg2 (драйвер PostGIS/PostgreSQL) требует этих библиотек для сборки
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# 3. Установка рабочей директории внутри контейнера
WORKDIR /app

# 4. Копирование и установка зависимостей
# Копируем только requirements.txt и сразу устанавливаем зависимости.
# Это позволяет Docker кэшировать этот слой, если код приложения не менялся.
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Копирование исходного кода и данных
# Копируем инициализатор, main.py и остальные скрипты в /app
COPY app/ /app/

# Копируем данные (CSV) в /app/data
# Это критически важно для db_initializer.py!
COPY data/ /app/data/

# 6. Определение команды запуска
# В данном случае, мы не используем CMD или ENTRYPOINT здесь, 
# потому что Docker Compose (docker-compose.yml) переопределяет эту команду 
# на сложный запуск: 'python db_initializer.py && python main.py'.

# CMD ["python", "main.py"]  <-- Не нужно, если используется entrypoint в docker-compose