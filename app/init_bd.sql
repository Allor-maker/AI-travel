--
-- ШАГ 0: Активация расширения PostGIS (должно быть установлено на сервере)
--
CREATE EXTENSION IF NOT EXISTS postgis;

--
-- ШАГ 1: Создание таблицы cultural_objects
--
CREATE TABLE IF NOT EXISTS cultural_objects (
    id INTEGER PRIMARY KEY,
    address VARCHAR(255),
    coordinate_text VARCHAR(255),  -- Временный столбец для приема POINT (Lon Lat)
    description TEXT,
    title VARCHAR(255),
    category_id INTEGER,
    category_url VARCHAR(255),
    geom GEOMETRY(Point, 4326)     -- PostGIS поле
);

--
-- ШАГ 2: Импорт данных из CSV-файла
--
-- Используем точный формат: DELIMITER ';', QUOTE '"', HEADER.
--
\copy cultural_objects(id, address, coordinate_text, description, title, category_id, category_url) FROM '../data/cultural_objects_mnn.csv' DELIMITER ';' CSV HEADER QUOTE '"';

--
-- ШАГ 3: Преобразование текстовых координат в геометрию PostGIS
--
UPDATE cultural_objects
SET geom = ST_GeomFromText(coordinate_text, 4326)
WHERE coordinate_text IS NOT NULL;

--
-- ШАГ 4: Создание пространственного индекса
-- Индекс GIST необходим для быстрой гео-фильтрации (ST_DWithin)
--
CREATE INDEX IF NOT EXISTS cultural_objects_geom_idx 
ON cultural_objects USING GIST (geom);

--
-- ШАГ 5: Удаление временного столбца
--
ALTER TABLE cultural_objects 
DROP COLUMN coordinate_text;