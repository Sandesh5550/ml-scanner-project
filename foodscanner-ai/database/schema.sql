PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    brand TEXT,
    nutriscore TEXT,
    ingredients TEXT,
    additives TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nutrition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    calories REAL,
    fat REAL,
    sugar REAL,
    salt REAL,
    protein REAL,
    fiber REAL,
    carbs REAL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL DEFAULT '',
    age INTEGER,
    weight REAL,
    height REAL,
    daily_calorie_limit INTEGER NOT NULL DEFAULT 2000,
    diet_type TEXT,
    allergens TEXT,
    goal_type TEXT,
    goal_target_days INTEGER DEFAULT 30,
    goal_started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    barcode TEXT NOT NULL,
    scan_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_diet_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    max_calories REAL,
    max_sugar REAL,
    max_salt REAL,
    max_fat REAL
);

CREATE TABLE IF NOT EXISTS daily_food_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    barcode TEXT NOT NULL,
    product_name TEXT NOT NULL,
    calories REAL,
    fat REAL,
    sugar REAL,
    salt REAL,
    protein REAL,
    fiber REAL,
    carbs REAL,
    consumed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(product_name);
CREATE INDEX IF NOT EXISTS idx_scan_history_barcode ON scan_history(barcode);
CREATE INDEX IF NOT EXISTS idx_nutrition_product_id ON nutrition(product_id);
CREATE INDEX IF NOT EXISTS idx_daily_food_log_consumed_at ON daily_food_log(consumed_at);

CREATE TABLE IF NOT EXISTS ocr_jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    image_hash TEXT,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_ocr_jobs_user_id ON ocr_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_ocr_jobs_status ON ocr_jobs(status);
