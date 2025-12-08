from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx
import sqlite3
import hashlib
import json
from datetime import datetime, timedelta
import jwt
import bcrypt
import pyotp
import qrcode
from io import BytesIO
import base64
import secrets
import re
import requests
import tempfile
import subprocess
import uuid
from urllib.parse import parse_qs, urlparse
from moviepy import ImageSequenceClip
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
from typing import List, Dict

app = FastAPI()

# Хранилище для временных файлов
temp_files = {}

# ======================== CONFIG ========================
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 1  # 1 час для access token
REFRESH_TOKEN_EXPIRE_DAYS = 30  # 30 дней для refresh token
REMEMBER_ME_EXPIRE_DAYS = 365  # 1 год для "запомнить меня"
SESSION_TIMEOUT_MINUTES = 30  # 30 минут неактивности

# OpenRouter API Config
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "tngtech/deepseek-r1t2-chimera:free"

# Яндекс Диск API Config
YANDEX_DISK_CLIENT_ID = os.getenv("YANDEX_DISK_CLIENT_ID", "af8c0ef742e744ce98baa1ba0ef63b1a")
YANDEX_DISK_CLIENT_SECRET = os.getenv("YANDEX_DISK_CLIENT_SECRET", "508175c2190a492e960d5b23dcfcfad1")
YANDEX_DISK_TOKEN = os.getenv("YANDEX_DISK_TOKEN", "y0__xDcvuO9AhiMkjwg9tf9xxUuc7fCnccH5CtvuIU98zvtgmP2PQ")  # OAuth токен
YANDEX_DISK_FOLDER = "marketplace-media"  # Папка на Яндекс Диске для хранения файлов (можно использовать любую папку с расширенными правами)

# ======================== DATABASE ========================
DB_PATH = Path(__file__).parent.parent / "app.db"

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email_verified BOOLEAN DEFAULT 0,
            email_verification_token TEXT,
            email_verification_expires TIMESTAMP,
            two_factor_enabled BOOLEAN DEFAULT 0,
            two_factor_secret TEXT,
            theme TEXT DEFAULT 'light' CHECK(theme IN ('light', 'dark')),
            last_activity TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Создаем частичный индекс для username (UNIQUE только для не-NULL значений)
    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique 
            ON users(username) WHERE username IS NOT NULL
        """)
    except sqlite3.OperationalError:
        pass  # Индекс может уже существовать
    # Добавляем новые колонки, если их нет (для существующих БД)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN email_verification_token TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN email_verification_expires TIMESTAMP")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN two_factor_secret TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_activity TIMESTAMP")
    except sqlite3.OperationalError:
        pass
    # Создаем таблицу для refresh токенов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    # Создаем таблицу для токенов сброса пароля
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    # Создаем таблицу для токенов подтверждения email
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            new_email TEXT,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            key_name TEXT,
            key_value TEXT NOT NULL,
            encrypted BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            offer_id TEXT NOT NULL,
            product_data JSON NOT NULL,
            status TEXT DEFAULT 'pending',
            response JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            marketplace TEXT,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            request_data JSON,
            response_data JSON,
            status_code INTEGER,
            error_message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            marketplace TEXT NOT NULL CHECK(marketplace IN ('ozon', 'wildberries')),
            brand TEXT,
            description_text TEXT,
            price REAL DEFAULT 0,
            stock INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")

def migrate_normalize_emails():
    """Миграция: нормализация всех email в базе данных"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        # Получаем всех пользователей
        cursor.execute("SELECT id, email FROM users")
        users = cursor.fetchall()
        
        updated_count = 0
        duplicate_emails = {}
        
        for user_id, email in users:
            normalized = normalize_email(email)
            if normalized != email:
                # Проверяем, нет ли уже пользователя с таким нормализованным email
                cursor.execute("SELECT id FROM users WHERE LOWER(TRIM(email)) = ? AND id != ?", 
                             (normalized, user_id))
                existing = cursor.fetchone()
                
                if existing:
                    # Найден дубликат - помечаем для удаления (оставляем более старую запись)
                    duplicate_emails[user_id] = (email, normalized, existing[0])
                else:
                    # Обновляем email
                    cursor.execute("UPDATE users SET email = ? WHERE id = ?", (normalized, user_id))
                    updated_count += 1
        
        # Удаляем дубликаты (оставляем только первую запись)
        for user_id, (old_email, normalized, existing_id) in duplicate_emails.items():
            print(f"⚠️  Removing duplicate user {user_id} (email: {old_email}, normalized: {normalized})")
            # Удаляем более новую запись (дубликат)
            if user_id > existing_id:
                cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            else:
                cursor.execute("DELETE FROM users WHERE id = ?", (existing_id,))
        
        conn.commit()
        if updated_count > 0 or duplicate_emails:
            print(f"✅ Email normalization migration completed: {updated_count} emails normalized, {len(duplicate_emails)} duplicates removed")
    except Exception as e:
        conn.rollback()
        print(f"⚠️  Email normalization migration error: {e}")
    finally:
        conn.close()

def migrate_fix_username_constraint():
    """Миграция: исправление UNIQUE constraint на username (разрешаем несколько NULL)"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        # Проверяем, существует ли таблица users
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            print("✅ Users table doesn't exist yet, will be created correctly")
            conn.close()
            return
        
        # Проверяем индексы на таблице users
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='users'")
        indexes = cursor.fetchall()
        
        # Проверяем, есть ли UNIQUE constraint на username в определении таблицы или индексе
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
        table_sql = cursor.fetchone()
        has_username_unique = False
        
        if table_sql and table_sql[0]:
            # Проверяем в SQL определении таблицы
            if 'username TEXT UNIQUE' in table_sql[0] or 'username TEXT UNIQUE,' in table_sql[0]:
                has_username_unique = True
        
        # Проверяем индексы
        for idx_name, idx_sql in indexes:
            if idx_sql and 'username' in idx_sql and 'UNIQUE' in idx_sql.upper():
                # Проверяем, не является ли это уже частичным индексом
                if 'WHERE username IS NOT NULL' not in idx_sql:
                    has_username_unique = True
                    # Удаляем старый индекс
                    try:
                        cursor.execute(f"DROP INDEX IF EXISTS {idx_name}")
                    except:
                        pass
        
        if has_username_unique:
            print("⚠️  Found UNIQUE constraint on username, attempting to fix...")
            
            # В SQLite нельзя напрямую удалить UNIQUE constraint через ALTER TABLE
            # Нужно пересоздать таблицу
            # Сначала создаем временную таблицу без UNIQUE на username
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email_verified BOOLEAN DEFAULT 0,
                    email_verification_token TEXT,
                    email_verification_expires TIMESTAMP,
                    two_factor_enabled BOOLEAN DEFAULT 0,
                    two_factor_secret TEXT,
                    theme TEXT DEFAULT 'light' CHECK(theme IN ('light', 'dark')),
                    last_activity TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Копируем данные
            cursor.execute("""
                INSERT INTO users_new 
                (id, username, email, password_hash, email_verified, email_verification_token, 
                 email_verification_expires, two_factor_enabled, two_factor_secret, theme, last_activity, created_at)
                SELECT 
                id, username, email, password_hash, email_verified, email_verification_token,
                email_verification_expires, two_factor_enabled, two_factor_secret, theme, last_activity, created_at
                FROM users
            """)
            
            # Удаляем старую таблицу
            cursor.execute("DROP TABLE users")
            
            # Переименовываем новую таблицу
            cursor.execute("ALTER TABLE users_new RENAME TO users")
            
            # Создаем индекс для username, но только для не-NULL значений (через частичный индекс)
            # В SQLite это можно сделать через CREATE UNIQUE INDEX с WHERE условием
            try:
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique 
                    ON users(username) WHERE username IS NOT NULL
                """)
            except sqlite3.OperationalError:
                pass  # Индекс может уже существовать
            
            conn.commit()
            print("✅ Username constraint migration completed: UNIQUE constraint removed, partial index created")
        else:
            print("✅ Username constraint is already correct (no UNIQUE on NULL values)")
    except Exception as e:
        conn.rollback()
        print(f"⚠️  Username constraint migration error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

init_db()
migrate_normalize_emails()
migrate_fix_username_constraint()

# ======================== MODELS ========================
from pydantic import BaseModel

class UserRegister(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

class UserLogin(BaseModel):
    email: str
    password: str
    remember_me: Optional[bool] = False

class PasswordResetRequest(BaseModel):
    email: str

class PasswordReset(BaseModel):
    token: str
    new_password: str

class ChangePassword(BaseModel):
    current_password: str
    new_password: str

class ChangeEmail(BaseModel):
    new_email: str
    password: str

class ChangeUsername(BaseModel):
    new_username: str

class VerifyEmail(BaseModel):
    token: str

class TwoFactorSetup(BaseModel):
    password: str

class TwoFactorVerify(BaseModel):
    code: str

class ProductCreate(BaseModel):
    offer_id: str
    name: str
    brand: Optional[str] = None
    price: float
    stock: int
    description: Optional[str] = None
    images: Optional[List[str]] = []
    barcode: Optional[str] = None
    primary_image: Optional[int] = None
    video_url: Optional[str] = None
    wb_sku: Optional[str] = None
    wb_images: Optional[List[str]] = []
    wb_video: Optional[str] = None
    # Категории и характеристики
    category: Optional[int] = None  # category_id для Ozon, parentID для WB
    type: Optional[int] = None  # type_id для Ozon
    characteristics: Optional[Dict[str, Any]] = None  # Характеристики товара

class BatchProducts(BaseModel):
    products: List[ProductCreate]

class ProductTemplate(BaseModel):
    name: str
    description: Optional[str] = None
    marketplace: str
    brand: Optional[str] = None
    description_text: Optional[str] = None
    price: Optional[float] = 0
    stock: Optional[int] = 0

class GenerateDescriptionRequest(BaseModel):
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    key_features: Optional[List[str]] = []
    marketplace: str = "ozon"

class GenerateCoverVideoRequest(BaseModel):
    images: List[str]
    duration: float = 5.0

# ======================== FASTAPI APP ========================
app = FastAPI(
    title="Ozon & Wildberries Product Manager v3.5",
    description="API с OpenRouter AI и категориями товаров",
    version="3.5.0",
    debug=True
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
BASE_DIR = Path(__file__).parent.parent
FRONTEND_PATH = BASE_DIR / "frontend"

# ======================== AUTH HELPERS ========================
def hash_password(password: str) -> str:
    """Хеширование пароля с использованием bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    """Проверка пароля"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except:
        # Fallback для старых паролей с SHA256
        old_hash = hashlib.sha256(password.encode()).hexdigest()
        return old_hash == password_hash

def validate_password_strength(password: str) -> tuple[bool, str]:
    """Валидация сложности пароля"""
    if len(password) < 8:
        return False, "Пароль должен содержать минимум 8 символов"
    if not re.search(r'[A-Za-z]', password):
        return False, "Пароль должен содержать хотя бы одну букву"
    if not re.search(r'[0-9]', password):
        return False, "Пароль должен содержать хотя бы одну цифру"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Пароль должен содержать хотя бы один специальный символ"
    return True, "Пароль соответствует требованиям"

def normalize_email(email: str) -> str:
    """Нормализация email: приведение к нижнему регистру и удаление пробелов"""
    return email.strip().lower()

def validate_email(email: str) -> bool:
    """Валидация email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def create_access_token(user_id: int, email: str, remember_me: bool = False) -> str:
    """Создание access token"""
    expire_hours = REMEMBER_ME_EXPIRE_DAYS * 24 if remember_me else ACCESS_TOKEN_EXPIRE_HOURS
    payload = {
        "user_id": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(hours=expire_hours)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int, remember_me: bool = False) -> str:
    """Создание refresh token"""
    expire_days = REMEMBER_ME_EXPIRE_DAYS if remember_me else REFRESH_TOKEN_EXPIRE_DAYS
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=expire_days)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO refresh_tokens (user_id, token, expires_at)
        VALUES (?, ?, ?)
    """, (user_id, token, expires_at))
    conn.commit()
    conn.close()
    
    return token

def verify_refresh_token(token: str) -> dict:
    """Проверка refresh token"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, expires_at FROM refresh_tokens
        WHERE token = ? AND expires_at > ? AND user_id IN (SELECT id FROM users)
    """, (token, datetime.utcnow()))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    
    return {"user_id": result[0]}

def revoke_refresh_token(token: str):
    """Отзыв refresh token"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def verify_token(token: str) -> dict:
    """Проверка access token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def generate_verification_token() -> str:
    """Генерация токена для верификации email"""
    return secrets.token_urlsafe(32)

def generate_password_reset_token() -> str:
    """Генерация токена для сброса пароля"""
    return secrets.token_urlsafe(32)

def update_user_activity(user_id: int):
    """Обновление времени последней активности пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET last_activity = ? WHERE id = ?
    """, (datetime.utcnow(), user_id))
    conn.commit()
    conn.close()

def check_session_timeout(user_id: int) -> bool:
    """Проверка таймаута сессии"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT last_activity FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not result[0]:
        return True
    
    last_activity = datetime.fromisoformat(result[0])
    timeout = datetime.utcnow() - timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    return last_activity > timeout

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Проверка типа токена
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Проверка таймаута сессии
        if not check_session_timeout(user_id):
            raise HTTPException(status_code=401, detail="Session expired")
        
        # Обновляем активность
        update_user_activity(user_id)
        
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ======================== DATABASE HELPERS ========================
# ======================== YANDEX DISK HELPERS ========================
try:
    import yadisk
except ImportError:
    yadisk = None
    print("⚠️  yadisk library not installed. Run: pip install yadisk")

def get_yandex_disk_client():
    """Получение клиента Яндекс Диска"""
    if not yadisk:
        raise HTTPException(status_code=500, detail="yadisk library not installed")
    if not YANDEX_DISK_TOKEN:
        raise HTTPException(
            status_code=400, 
            detail="Yandex Disk token not configured. Use /api/yandex-disk/auth-url to get authorization URL, then /api/yandex-disk/get-token to get token"
        )
    client = yadisk.Client(token=YANDEX_DISK_TOKEN)
    try:
        if not client.check_token():
            raise HTTPException(
                status_code=401, 
                detail="Invalid or expired Yandex Disk token. Token may not have required permissions (cloud_api:disk:write, cloud_api:disk:read). Get a new token with /api/yandex-disk/auth-url"
            )
    except Exception as e:
        error_msg = str(e)
        if "Forbidden" in error_msg or "403" in error_msg or "ForbiddenError" in error_msg:
            raise HTTPException(
                status_code=403,
                detail="Token does not have required permissions. Please get a new token with scopes: cloud_api:disk:write, cloud_api:disk:read. Use /api/yandex-disk/auth-url"
            )
        raise HTTPException(status_code=401, detail=f"Token validation failed: {error_msg}")
    return client

def upload_to_yandex_disk(file_content: bytes, filename: str, username: str, content_type: str = None) -> str:
    """Загрузка файла на Яндекс Диск в папку пользователя и получение публичной ссылки"""
    tmp_path = None
    try:
        client = get_yandex_disk_client()
        
        # Используем папку пользователя на диске
        # Создаем безопасное имя папки (убираем спецсимволы)
        safe_username = "".join(c for c in username if c.isalnum() or c in ('-', '_')).strip()
        if not safe_username:
            safe_username = f"user_{hash(username) % 10000}"
        
        media_folder_path = f"/{YANDEX_DISK_FOLDER}"
        user_folder_path = f"{media_folder_path}/{safe_username}"
        
        # Создаем основную папку, если её нет
        try:
            if not client.exists(media_folder_path):
                client.mkdir(media_folder_path)
        except Exception as e:
            print(f"Warning: Could not create folder {media_folder_path}: {str(e)}")
        
        # Создаем папку пользователя, если её нет
        try:
            if not client.exists(user_folder_path):
                client.mkdir(user_folder_path)
                print(f"✅ Created user folder: {user_folder_path}")
        except Exception as e:
            print(f"Warning: Could not create user folder {user_folder_path}: {str(e)}")
            # Продолжаем, возможно папка уже существует
        
        # Генерируем уникальное имя файла
        file_ext = Path(filename).suffix if filename else ""
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        # Путь к файлу в папке пользователя
        disk_path = f"{user_folder_path}/{unique_filename}"
        
        # Сохраняем файл во временный файл для загрузки
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name
        
        # Загружаем файл на Яндекс Диск
        try:
            client.upload(tmp_path, disk_path, overwrite=True)
        except Exception as upload_error:
            error_msg = str(upload_error)
            print(f"Upload error: {error_msg}")
            # Пробуем альтернативный способ - загрузка через BytesIO
            try:
                from io import BytesIO
                client.upload(BytesIO(file_content), disk_path, overwrite=True)
            except Exception as e2:
                raise Exception(f"Failed to upload file: {error_msg}. Alternative method also failed: {str(e2)}")
        
        # Всегда используем прокси через наш сервер (download ссылки не работают из-за CORS/Referrer)
        # Прокси загружает файл с Яндекс Диска и отдает его напрямую
        public_url = f"/api/media-proxy?path={disk_path}"
        print(f"✅ Using proxy URL: {public_url}")
        
        return public_url
    except Exception as e:
        error_detail = str(e)
        print(f"Full error details: {error_detail}")
        raise Exception(f"Error uploading to Yandex Disk: {error_detail}")
    finally:
        # Удаляем временный файл
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

def clear_user_media_folder(username: str) -> bool:
    """Очистка папки пользователя на Яндекс Диске при авторизации"""
    try:
        client = get_yandex_disk_client()
        
        # Создаем безопасное имя папки
        safe_username = "".join(c for c in username if c.isalnum() or c in ('-', '_')).strip()
        if not safe_username:
            safe_username = f"user_{hash(username) % 10000}"
        
        user_folder_path = f"/{YANDEX_DISK_FOLDER}/{safe_username}"
        
        # Проверяем существование папки
        if not client.exists(user_folder_path):
            print(f"User folder does not exist: {user_folder_path}")
            return True  # Папки нет, считаем что очистка выполнена
        
        # Получаем все файлы в папке пользователя
        try:
            items = list(client.listdir(user_folder_path))
            deleted_count = 0
            for item in items:
                try:
                    if hasattr(item, 'path'):
                        item_path = item.path
                    elif isinstance(item, dict):
                        item_path = item.get('path')
                    else:
                        item_path = str(item)
                    
                    client.remove(item_path, permanently=True)
                    deleted_count += 1
                except Exception as e:
                    print(f"Warning: Could not delete {item_path}: {str(e)}")
            
            print(f"✅ Cleared user folder {user_folder_path}: deleted {deleted_count} items")
            return True
        except Exception as e:
            print(f"Error listing user folder: {str(e)}")
            return False
    except Exception as e:
        print(f"Error clearing user media folder: {str(e)}")
        return False

def delete_from_yandex_disk(file_url: str) -> bool:
    """Удаление файла с Яндекс Диска по публичной ссылке или пути"""
    try:
        client = get_yandex_disk_client()
        
        # Используем основную папку на диске (файлы могут быть в подпапках пользователей)
        media_folder_path = f"/{YANDEX_DISK_FOLDER}"
        
        # Пытаемся найти файл по публичной ссылке или прокси URL
        if "/api/media-proxy" in file_url:
            # Это прокси URL, извлекаем путь
            try:
                parsed = urlparse(file_url)
                params = parse_qs(parsed.query)
                if 'path' in params:
                    disk_path = params['path'][0]
                    if client.exists(disk_path):
                        client.remove(disk_path, permanently=True)
                        return True
            except Exception as e:
                print(f"Error parsing proxy URL: {str(e)}")
        elif "disk.yandex.ru" in file_url or "yadi.sk" in file_url:
            # Это публичная ссылка, ищем файл
            try:
                # Пробуем найти в папке с медиа
                try:
                    items = client.listdir(media_folder_path)
                    search_path = media_folder_path
                except:
                    items = []
                    search_path = None
                
                for item in items:
                    try:
                        meta = client.get_meta(item.path)
                        if hasattr(meta, 'public_url') and meta.public_url:
                            if meta.public_url == file_url or file_url in meta.public_url:
                                client.remove(item.path, permanently=True)
                                return True
                    except:
                        continue
                
                # Если не нашли по публичной ссылке, пытаемся по имени файла из URL
                if search_path:
                    filename = file_url.split("/")[-1].split("?")[0]
                    disk_path = f"{search_path}/{filename}"
                    if client.exists(disk_path):
                        client.remove(disk_path, permanently=True)
                        return True
            except Exception as e:
                print(f"Error searching file: {str(e)}")
        else:
            # Предполагаем, что это прямой путь
            disk_path = file_url
            if client.exists(disk_path):
                client.remove(disk_path, permanently=True)
                return True
        
        return False
    except Exception as e:
        print(f"Error deleting from Yandex Disk: {str(e)}")
        return False

def get_user_by_email(email: str) -> Optional[dict]:
    """Поиск пользователя по email с нормализацией"""
    normalized_email = normalize_email(email)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Поиск с учетом регистра (нормализованный email)
    cursor.execute("SELECT * FROM users WHERE LOWER(TRIM(email)) = ?", (normalized_email,))
    user = cursor.fetchone()
    
    # Также проверяем точное совпадение (на случай, если в базе уже нормализованный email)
    if not user:
        cursor.execute("SELECT * FROM users WHERE email = ?", (normalized_email,))
        user = cursor.fetchone()
    
    conn.close()
    if user:
        user_dict = dict(user)
        if 'theme' not in user_dict or user_dict['theme'] is None:
            user_dict['theme'] = 'light'
        return user_dict
    return None

def get_user_by_username(username: str) -> Optional[dict]:
    """Обратная совместимость - поиск по username"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    if user:
        user_dict = dict(user)
        if 'theme' not in user_dict or user_dict['theme'] is None:
            user_dict['theme'] = 'light'
        return user_dict
    return None

def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        user_dict = dict(user)
        if 'theme' not in user_dict or user_dict['theme'] is None:
            user_dict['theme'] = 'light'
        return user_dict
    return None

def update_user_theme(user_id: int, theme: str) -> bool:
    """Обновление темы пользователя"""
    if theme not in ('light', 'dark'):
        return False
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        # Проверяем, есть ли колонка theme
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'theme' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'light' CHECK(theme IN ('light', 'dark'))")
            conn.commit()
        
        cursor.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating user theme: {str(e)}")
        conn.close()
        return False

def save_user(email: str, password_hash: str, username: Optional[str] = None) -> int:
    """Сохранение нового пользователя с нормализованным email"""
    normalized_email = normalize_email(email)
    verification_token = generate_verification_token()
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Дополнительная проверка перед вставкой - проверяем все возможные варианты email
    cursor.execute("SELECT id, email FROM users WHERE email = ? OR LOWER(TRIM(email)) = ?", 
                   (normalized_email, normalized_email))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        print(f"⚠️  User with email already exists in DB: id={existing[0]}, email={existing[1]}")
        raise HTTPException(status_code=400, detail="Email already exists")
    
    try:
        # Если username не указан, не вставляем его (оставляем NULL)
        if username:
            cursor.execute(
                "INSERT INTO users (email, password_hash, username, email_verification_token, email_verification_expires) VALUES (?, ?, ?, ?, ?)",
                (normalized_email, password_hash, username, verification_token, expires_at)
            )
        else:
            cursor.execute(
                "INSERT INTO users (email, password_hash, email_verification_token, email_verification_expires) VALUES (?, ?, ?, ?)",
                (normalized_email, password_hash, verification_token, expires_at)
            )
        user_id = cursor.lastrowid
        
        # Сохраняем токен верификации
        cursor.execute("""
            INSERT INTO email_verification_tokens (user_id, token, expires_at)
            VALUES (?, ?, ?)
        """, (user_id, verification_token, expires_at))
        
        conn.commit()
        conn.close()
        return user_id
    except sqlite3.IntegrityError as e:
        conn.rollback()
        error_str = str(e).lower()
        # Логируем ошибку для отладки
        print(f"⚠️  IntegrityError during user registration: {e}")
        print(f"   Email: {normalized_email}, Username: {username}")
        
        # Проверяем, что именно вызвало ошибку - проверяем все записи в базе
        cursor.execute("SELECT id, email, username FROM users")
        all_users = cursor.fetchall()
        print(f"   All users in DB: {all_users}")
        conn.close()
        
        # Более точная проверка: ищем конкретное поле в сообщении об ошибке SQLite
        # SQLite обычно выдает ошибки вида "UNIQUE constraint failed: users.email" или "UNIQUE constraint failed: users.username"
        if "users.email" in error_str or ("email" in error_str and "username" not in error_str):
            raise HTTPException(status_code=400, detail="Email already exists")
        if "users.username" in error_str or ("username" in error_str and "email" not in error_str):
            raise HTTPException(status_code=400, detail="Username already exists")
        # Если оба присутствуют или не можем определить, проверяем email (так как он обязателен)
        if "email" in error_str:
            raise HTTPException(status_code=400, detail="Email already exists")
        raise HTTPException(status_code=400, detail="User already exists")

def verify_user_email(token: str) -> bool:
    """Верификация email пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id FROM email_verification_tokens
        WHERE token = ? AND expires_at > ? AND used = 0
    """, (token, datetime.utcnow()))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return False
    
    user_id = result[0]
    cursor.execute("""
        UPDATE users SET email_verified = 1, email_verification_token = NULL, email_verification_expires = NULL
        WHERE id = ?
    """, (user_id,))
    cursor.execute("""
        UPDATE email_verification_tokens SET used = 1 WHERE token = ?
    """, (token,))
    conn.commit()
    conn.close()
    return True

def save_password_reset_token(user_id: int) -> str:
    """Сохранение токена сброса пароля"""
    token = generate_password_reset_token()
    expires_at = datetime.utcnow() + timedelta(hours=24)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO password_reset_tokens (user_id, token, expires_at)
        VALUES (?, ?, ?)
    """, (user_id, token, expires_at))
    conn.commit()
    conn.close()
    return token

def verify_password_reset_token(token: str) -> Optional[int]:
    """Проверка токена сброса пароля"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id FROM password_reset_tokens
        WHERE token = ? AND expires_at > ? AND used = 0
    """, (token, datetime.utcnow()))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    return None

def use_password_reset_token(token: str):
    """Использование токена сброса пароля"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE password_reset_tokens SET used = 1 WHERE token = ?
    """, (token,))
    conn.commit()
    conn.close()

def update_user_password(user_id: int, new_password_hash: str):
    """Обновление пароля пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET password_hash = ? WHERE id = ?
    """, (new_password_hash, user_id))
    conn.commit()
    conn.close()

def update_user_username(user_id: int, new_username: Optional[str]) -> bool:
    """Обновление имени пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        # Если username пустой, устанавливаем NULL
        username_value = new_username.strip() if new_username and new_username.strip() else None
        
        # Проверяем уникальность, если username не NULL
        if username_value:
            cursor.execute("SELECT id FROM users WHERE username = ? AND id != ?", (username_value, user_id))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                raise HTTPException(status_code=400, detail="Username already exists")
        
        cursor.execute("""
            UPDATE users SET username = ? WHERE id = ?
        """, (username_value, user_id))
        conn.commit()
        conn.close()
        return True
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=f"Error updating username: {str(e)}")

def save_email_change_token(user_id: int, new_email: str) -> str:
    """Сохранение токена для смены email с нормализацией"""
    normalized_email = normalize_email(new_email)
    token = generate_verification_token()
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO email_verification_tokens (user_id, token, new_email, expires_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, token, normalized_email, expires_at))
    conn.commit()
    conn.close()
    return token

def verify_email_change_token(token: str) -> Optional[tuple[int, str]]:
    """Проверка токена смены email"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, new_email FROM email_verification_tokens
        WHERE token = ? AND expires_at > ? AND used = 0 AND new_email IS NOT NULL
    """, (token, datetime.utcnow()))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return (result[0], result[1])
    return None

def update_user_email(user_id: int, new_email: str):
    """Обновление email пользователя с нормализацией"""
    normalized_email = normalize_email(new_email)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET email = ?, email_verified = 1 WHERE id = ?
    """, (normalized_email, user_id))
    conn.commit()
    conn.close()

def setup_2fa(user_id: int) -> tuple[str, str]:
    """Настройка 2FA для пользователя"""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=get_user_by_id(user_id)["email"],
        issuer_name="Marketplace Manager"
    )
    
    # Генерируем QR код
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    # Формируем полный data URI для использования в <img src>
    qr_code_data_uri = f"data:image/png;base64,{qr_code_base64}"
    
    # Сохраняем секрет (пока не включен)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET two_factor_secret = ? WHERE id = ?
    """, (secret, user_id))
    conn.commit()
    conn.close()
    
    return secret, qr_code_data_uri

def enable_2fa(user_id: int):
    """Включение 2FA для пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET two_factor_enabled = 1 WHERE id = ?
    """, (user_id,))
    conn.commit()
    conn.close()

def verify_2fa(user_id: int, code: str) -> bool:
    """Проверка 2FA кода"""
    user = get_user_by_id(user_id)
    if not user or not user.get("two_factor_secret"):
        return False
    
    totp = pyotp.TOTP(user["two_factor_secret"])
    return totp.verify(code, valid_window=1)

def disable_2fa(user_id: int):
    """Отключение 2FA для пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET two_factor_enabled = 0, two_factor_secret = NULL WHERE id = ?
    """, (user_id,))
    conn.commit()
    conn.close()

def save_api_keys(user_id: int, marketplace: str, keys: dict) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys WHERE user_id = ? AND marketplace = ?", (user_id, marketplace))
    for key_name, key_value in keys.items():
        cursor.execute(
            "INSERT INTO api_keys (user_id, marketplace, key_name, key_value) VALUES (?, ?, ?, ?)",
            (user_id, marketplace, key_name, key_value)
        )
    conn.commit()
    conn.close()

def get_api_keys(user_id: int, marketplace: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT key_name, key_value FROM api_keys WHERE user_id = ? AND marketplace = ?",
        (user_id, marketplace)
    )
    keys = {row["key_name"]: row["key_value"] for row in cursor.fetchall()}
    conn.close()
    return keys

def save_product_history(user_id: int, marketplace: str, offer_id: str, product_data: dict, status: str, response: dict = None):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO product_history (user_id, marketplace, offer_id, product_data, status, response) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, marketplace, offer_id, json.dumps(product_data), status, json.dumps(response) if response else None)
    )
    conn.commit()
    conn.close()

def log_api_request(user_id: Optional[int], endpoint: str, method: str, request_data: dict = None, response_data: dict = None, status_code: int = None, error: str = None):
    """Логирование API запросов и ответов"""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Создаем таблицу если её нет
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                request_data TEXT,
                response_data TEXT,
                status_code INTEGER,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        conn.execute(
            "INSERT INTO api_logs (user_id, endpoint, method, request_data, response_data, status_code, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                endpoint,
                method,
                json.dumps(request_data) if request_data else None,
                json.dumps(response_data) if response_data else None,
                status_code,
                error
            )
        )
        conn.commit()
    except Exception as e:
        print(f"Error logging API request: {str(e)}")
    finally:
        conn.close()

def get_product_history(user_id: int, marketplace: str = None, offer_id: str = None):
    """Получение истории товаров"""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        query = "SELECT * FROM product_history WHERE user_id = ?"
        params = [user_id]
        if marketplace:
            query += " AND marketplace = ?"
            params.append(marketplace)
        if offer_id:
            query += " AND offer_id = ?"
            params.append(offer_id)
        query += " ORDER BY created_at DESC LIMIT 100"
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()

def save_product_template(user_id: int, template: ProductTemplate) -> int:
    """Сохранение шаблона товара"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO product_templates 
            (user_id, name, description, marketplace, brand, description_text, price, stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            template.name,
            template.description,
            template.marketplace,
            template.brand,
            template.description_text,
            template.price,
            template.stock
        ))
        conn.commit()
        template_id = cursor.lastrowid
        conn.close()
        return template_id
    except Exception as e:
        conn.close()
        raise Exception(f"Error saving template: {str(e)}")

def get_product_templates(user_id: int, marketplace: str = None) -> List[dict]:
    """Получение шаблонов товаров пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        query = "SELECT * FROM product_templates WHERE user_id = ?"
        params = [user_id]
        if marketplace:
            query += " AND marketplace = ?"
            params.append(marketplace)
        query += " ORDER BY updated_at DESC"
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()

def update_product_template(template_id: int, user_id: int, template: ProductTemplate) -> bool:
    """Обновление шаблона товара"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE product_templates 
            SET name = ?, description = ?, marketplace = ?, brand = ?, 
                description_text = ?, price = ?, stock = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (
            template.name,
            template.description,
            template.marketplace,
            template.brand,
            template.description_text,
            template.price,
            template.stock,
            template_id,
            user_id
        ))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.close()
        raise Exception(f"Error updating template: {str(e)}")

def delete_product_template(template_id: int, user_id: int) -> bool:
    """Удаление шаблона товара"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM product_templates WHERE id = ? AND user_id = ?", (template_id, user_id))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    except Exception as e:
        conn.close()
        raise Exception(f"Error deleting template: {str(e)}")

# ======================== AUTH ENDPOINTS ========================
@app.post("/api/auth/register")
async def register(user: UserRegister):
    try:
        # Нормализация email
        normalized_email = normalize_email(user.email)
        print(f"🔍 Registration attempt for email: {user.email} -> normalized: {normalized_email}")
        
        # Валидация email
        if not validate_email(normalized_email):
            print(f"❌ Invalid email format: {normalized_email}")
            raise HTTPException(status_code=400, detail="Invalid email format")
        
        # Проверка существования email (с нормализацией)
        existing_user = get_user_by_email(normalized_email)
        if existing_user:
            print(f"❌ User already exists with email: {normalized_email}, user_id: {existing_user.get('id')}")
            raise HTTPException(status_code=400, detail="Email already exists")
        
        # Валидация пароля
        is_valid, message = validate_password_strength(user.password)
        if not is_valid:
            print(f"❌ Invalid password: {message}")
            raise HTTPException(status_code=400, detail=message)
        
        password_hash = hash_password(user.password)
        print(f"💾 Attempting to save user: {normalized_email}")
        user_id = save_user(normalized_email, password_hash, username=user.name)
        
        # Получаем токен верификации
        user_data = get_user_by_id(user_id)
        verification_token = user_data.get("email_verification_token")
        
        print(f"✅ User registered successfully: {normalized_email}, user_id: {user_id}, verification token: {verification_token}")
        
        return {
            "user_id": user_id,
            "email": normalized_email,
            "email_verified": False,
            "message": "Registration successful. Please verify your email.",
            "verification_token": verification_token
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error during registration: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/api/auth/verify-email")
async def verify_email(request: VerifyEmail):
    if verify_user_email(request.token):
        return {"status": "ok", "message": "Email verified successfully"}
    raise HTTPException(status_code=400, detail="Invalid or expired verification token")

@app.post("/api/auth/login")
async def login(user: UserLogin):
    db_user = get_user_by_email(user.email)
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Проверка 2FA
    if db_user.get("two_factor_enabled"):
        # Требуем код 2FA (будет проверен в отдельном эндпоинте)
        return {
            "requires_2fa": True,
            "message": "Two-factor authentication required"
        }
    
    # Обновляем активность
    update_user_activity(db_user["id"])
    
    # Очищаем папку пользователя на Яндекс Диске при авторизации
    try:
        username = db_user.get("username") or db_user["email"].split("@")[0]
        clear_user_media_folder(username)
    except Exception as e:
        print(f"Warning: Could not clear user media folder: {str(e)}")
    
    access_token = create_access_token(db_user["id"], db_user["email"], user.remember_me)
    refresh_token = create_refresh_token(db_user["id"], user.remember_me)
    
    print(f"✅ User logged in: {db_user['email']}")
    
    user_theme = db_user.get("theme", "light")
    
    return {
        "user_id": db_user["id"],
        "email": db_user["email"],
        "username": db_user.get("username"),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "theme": user_theme,
        "email_verified": db_user.get("email_verified", False)
    }

@app.post("/api/auth/verify-2fa")
async def verify_2fa_login(request: dict):
    """Верификация 2FA при входе"""
    email = request.get("email")
    password = request.get("password")
    code = request.get("code")
    remember_me = request.get("remember_me", False)
    
    if not all([email, password, code]):
        raise HTTPException(status_code=400, detail="Email, password and 2FA code required")
    
    db_user = get_user_by_email(email)
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_password(password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not verify_2fa(db_user["id"], code):
        raise HTTPException(status_code=401, detail="Invalid 2FA code")
    
    update_user_activity(db_user["id"])
    
    access_token = create_access_token(db_user["id"], db_user["email"], remember_me)
    refresh_token = create_refresh_token(db_user["id"], remember_me)
    
    user_theme = db_user.get("theme", "light")
    
    return {
        "user_id": db_user["id"],
        "email": db_user["email"],
        "username": db_user.get("username"),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "theme": user_theme,
        "email_verified": db_user.get("email_verified", False)
    }

@app.post("/api/auth/refresh")
async def refresh_token(request: dict):
    """Обновление access token через refresh token"""
    refresh_token_value = request.get("refresh_token")
    if not refresh_token_value:
        raise HTTPException(status_code=400, detail="Refresh token required")
    
    try:
        token_data = verify_refresh_token(refresh_token_value)
        user = get_user_by_id(token_data["user_id"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Проверка таймаута сессии
        if not check_session_timeout(user["id"]):
            raise HTTPException(status_code=401, detail="Session expired")
        
        update_user_activity(user["id"])
        
        access_token = create_access_token(user["id"], user["email"])
        
        return {
            "access_token": access_token,
            "token_type": "bearer"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@app.post("/api/auth/logout")
async def logout(request: dict, current_user: dict = Depends(get_current_user)):
    """Выход из системы"""
    refresh_token_value = request.get("refresh_token")
    if refresh_token_value:
        revoke_refresh_token(refresh_token_value)
    return {"status": "ok", "message": "Logged out successfully"}

@app.post("/api/auth/forgot-password")
async def forgot_password(request: PasswordResetRequest):
    """Запрос на сброс пароля"""
    user = get_user_by_email(request.email)
    if not user:
        # Не раскрываем, существует ли пользователь
        return {"status": "ok", "message": "If email exists, password reset link has been sent"}
    
    token = save_password_reset_token(user["id"])
    # Здесь должна быть отправка email с токеном
    print(f"Password reset token for {request.email}: {token}")
    
    return {
        "status": "ok",
        "message": "If email exists, password reset link has been sent",
        "reset_token": token  # В продакшене не возвращать
    }

@app.post("/api/auth/reset-password")
async def reset_password(request: PasswordReset):
    """Сброс пароля по токену"""
    user_id = verify_password_reset_token(request.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    is_valid, message = validate_password_strength(request.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)
    
    new_password_hash = hash_password(request.new_password)
    update_user_password(user_id, new_password_hash)
    use_password_reset_token(request.token)
    
    return {"status": "ok", "message": "Password reset successfully"}

# ======================== USER PROFILE ENDPOINTS ========================
@app.post("/api/user/change-password")
async def change_password(
    request: ChangePassword,
    current_user: dict = Depends(get_current_user)
):
    """Смена пароля пользователя"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Проверяем текущий пароль
    if not verify_password(request.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    
    # Валидация нового пароля
    is_valid, message = validate_password_strength(request.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)
    
    # Обновляем пароль
    new_password_hash = hash_password(request.new_password)
    update_user_password(current_user["user_id"], new_password_hash)
    
    return {"status": "ok", "message": "Password changed successfully"}

@app.post("/api/user/change-username")
async def change_username(
    request: ChangeUsername,
    current_user: dict = Depends(get_current_user)
):
    """Смена имени пользователя"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Валидация имени (опционально, можно добавить проверки)
    new_username = request.new_username.strip() if request.new_username else None
    
    # Обновляем имя
    update_user_username(current_user["user_id"], new_username)
    
    return {
        "status": "ok", 
        "message": "Username changed successfully",
        "username": new_username
    }

@app.post("/api/user/change-email/request")
async def request_email_change(
    request: ChangeEmail,
    current_user: dict = Depends(get_current_user)
):
    """Запрос на смену email (отправка токена верификации)"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Проверяем пароль
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    
    # Нормализация и валидация нового email
    normalized_new_email = normalize_email(request.new_email)
    if not validate_email(normalized_new_email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # Проверяем, не используется ли email другим пользователем
    existing_user = get_user_by_email(normalized_new_email)
    if existing_user and existing_user["id"] != current_user["user_id"]:
        raise HTTPException(status_code=400, detail="Email already in use")
    
    # Генерируем токен для смены email
    token = save_email_change_token(current_user["user_id"], normalized_new_email)
    
    # Отправляем email с токеном (здесь должна быть интеграция с email сервисом)
    # Пока просто возвращаем токен для тестирования
    print(f"✅ Email change requested for user {current_user['user_id']}, token: {token}")
    
    return {
        "status": "ok",
        "message": "Verification email sent. Please check your new email inbox.",
        "verification_token": token  # В продакшене не возвращать токен
    }

@app.post("/api/user/change-email/verify")
async def verify_email_change(request: VerifyEmail):
    """Подтверждение смены email по токену"""
    result = verify_email_change_token(request.token)
    if not result:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    
    user_id, new_email = result
    
    # Обновляем email
    update_user_email(user_id, new_email)
    
    # Помечаем токен как использованный
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE email_verification_tokens SET used = 1 WHERE token = ?
    """, (request.token,))
    conn.commit()
    conn.close()
    
    return {"status": "ok", "message": "Email changed successfully"}

@app.get("/api/user/profile")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """Получить профиль пользователя"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем активный токен верификации, если email не подтвержден
    verification_token = None
    if not user.get("email_verified", 0):
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT token FROM email_verification_tokens
            WHERE user_id = ? AND expires_at > ? AND used = 0 AND new_email IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, (user["id"], datetime.utcnow()))
        result = cursor.fetchone()
        conn.close()
        if result:
            verification_token = result[0]
    
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user.get("username"),
        "email_verified": bool(user.get("email_verified", 0)),
        "two_factor_enabled": bool(user.get("two_factor_enabled", 0)),
        "theme": user.get("theme", "light"),
        "created_at": user.get("created_at"),
        "verification_token": verification_token
    }

# ======================== API KEYS ENDPOINTS ========================
@app.post("/api/keys/save")
async def save_keys(
    marketplace: str,
    keys: dict,
    current_user: dict = Depends(get_current_user)
):
    save_api_keys(current_user["user_id"], marketplace, keys)
    print(f"✅ API keys saved for {marketplace}")
    return {"status": "ok", "message": "API keys saved"}

@app.get("/api/keys/{marketplace}")
async def get_keys(marketplace: str, current_user: dict = Depends(get_current_user)):
    keys = get_api_keys(current_user["user_id"], marketplace)
    safe_keys = {}
    for k, v in keys.items():
        if v:
            safe_keys[k] = v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
    return safe_keys

# ======================== 2FA ENDPOINTS ========================
@app.post("/api/user/2fa/setup")
async def setup_2fa_endpoint(
    request: TwoFactorSetup,
    current_user: dict = Depends(get_current_user)
):
    """Настройка 2FA"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    
    secret, qr_code = setup_2fa(current_user["user_id"])
    
    return {
        "status": "ok",
        "secret": secret,
        "qr_code": qr_code,
        "message": "Scan QR code with authenticator app, then verify to enable 2FA"
    }

@app.post("/api/user/2fa/verify")
async def verify_2fa_setup(
    request: TwoFactorVerify,
    current_user: dict = Depends(get_current_user)
):
    """Верификация и включение 2FA"""
    if not verify_2fa(current_user["user_id"], request.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")
    
    enable_2fa(current_user["user_id"])
    
    return {"status": "ok", "message": "2FA enabled successfully"}

@app.post("/api/user/2fa/disable")
async def disable_2fa_endpoint(
    request: TwoFactorSetup,
    current_user: dict = Depends(get_current_user)
):
    """Отключение 2FA"""
    user = get_user_by_id(current_user["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    
    disable_2fa(current_user["user_id"])
    
    return {"status": "ok", "message": "2FA disabled successfully"}

@app.get("/api/user/theme")
async def get_user_theme(current_user: dict = Depends(get_current_user)):
    """Получить тему пользователя"""
    db_user = get_user_by_id(current_user["user_id"])
    if db_user:
        theme = db_user.get("theme", "light")
        return {"theme": theme}
    return {"theme": "light"}

@app.post("/api/user/theme")
async def save_user_theme(
    request: dict,
    current_user: dict = Depends(get_current_user)
):
    """Сохранить тему пользователя"""
    theme = request.get("theme", "light")
    if theme not in ("light", "dark"):
        raise HTTPException(status_code=400, detail="Theme must be 'light' or 'dark'")
    
    success = update_user_theme(current_user["user_id"], theme)
    if success:
        return {"status": "ok", "theme": theme}
    else:
        raise HTTPException(status_code=500, detail="Failed to save theme")

# ======================== TEMPLATES ENDPOINTS ========================
@app.get("/api/templates")
async def get_templates(
    marketplace: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получить шаблоны пользователя"""
    templates = get_product_templates(current_user["user_id"], marketplace)
    return {"templates": templates}

@app.post("/api/templates")
async def create_template(
    template: ProductTemplate,
    current_user: dict = Depends(get_current_user)
):
    """Создать шаблон"""
    if template.marketplace not in ("ozon", "wildberries"):
        raise HTTPException(status_code=400, detail="Marketplace must be 'ozon' or 'wildberries'")
    
    template_id = save_product_template(current_user["user_id"], template)
    return {"status": "ok", "template_id": template_id, "message": "Template created"}

@app.put("/api/templates/{template_id}")
async def update_template(
    template_id: int,
    template: ProductTemplate,
    current_user: dict = Depends(get_current_user)
):
    """Обновить шаблон"""
    if template.marketplace not in ("ozon", "wildberries"):
        raise HTTPException(status_code=400, detail="Marketplace must be 'ozon' or 'wildberries'")
    
    success = update_product_template(template_id, current_user["user_id"], template)
    if success:
        return {"status": "ok", "message": "Template updated"}
    else:
        raise HTTPException(status_code=404, detail="Template not found")

@app.delete("/api/templates/{template_id}")
async def delete_template(
    template_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Удалить шаблон"""
    success = delete_product_template(template_id, current_user["user_id"])
    if success:
        return {"status": "ok", "message": "Template deleted"}
    else:
        raise HTTPException(status_code=404, detail="Template not found")

# ======================== API KEYS ENDPOINTS ========================

# ======================== CATEGORIES ENDPOINTS ========================

@app.post("/api/categories/ozon/tree")
async def get_ozon_categories(
    current_user: dict = Depends(get_current_user)
):
    """Получить дерево категорий Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    
    payload = {
        "language": "DEFAULT"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/description-category/tree",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon categories loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading Ozon categories: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/categories/ozon/attributes")
async def get_ozon_attributes(
    request: dict,
    current_user: dict = Depends(get_current_user)
):
    """Получить атрибуты (характеристики) категории Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    category_id = request.get("category_id")
    type_id = request.get("type_id")
    
    if not category_id or not type_id:
        raise HTTPException(status_code=400, detail="category_id and type_id are required")
    
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    
    payload = {
        "category_id": category_id,
        "type_id": type_id,
        "language": "DEFAULT"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/description-category/attribute/values",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon attributes loaded for category {category_id}, type {type_id}")
                # Преобразуем атрибуты в формат, который ожидает фронтенд
                attributes = data.get("result", [])
                formatted_attributes = []
                for attr in attributes:
                    # Ozon API возвращает атрибуты с полями: id, name, is_required, type, etc.
                    formatted_attr = {
                        "id": attr.get("id"),
                        "name": attr.get("name", ""),
                        "is_required": attr.get("is_required", False),
                        "type": attr.get("type", ""),
                        "dictionary_id": attr.get("dictionary_id"),
                        "values": attr.get("values", [])  # Список возможных значений для словарных атрибутов
                    }
                    formatted_attributes.append(formatted_attr)
                return {"result": formatted_attributes}
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading Ozon attributes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/tree")
async def get_wildberries_categories(
    current_user: dict = Depends(get_current_user)
):
    """Получить дерево категорий Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://content-api.wildberries.ru/content/v2/object/parent/all",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Wildberries categories loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB categories: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/subjects")
async def get_wildberries_subjects(
    parent_id: Optional[int] = None,
    name: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получить предметы (подкатегории) Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    params = {"limit": min(limit, 1000), "offset": offset}
    if parent_id:
        params["parentID"] = parent_id
    if name:
        params["name"] = name
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://content-api.wildberries.ru/content/v2/object/all",
                headers=headers,
                params=params
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB subjects loaded")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB subjects: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/api/categories/wildberries/characteristics/{subject_id}")
async def get_wildberries_characteristics(
    subject_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Получить характеристики предмета Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://content-api.wildberries.ru/content/v2/object/charcs/{subject_id}",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB characteristics loaded for subject {subject_id}")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error loading WB characteristics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== BARCODE ENDPOINTS ========================
@app.post("/api/ozon/barcode/add")
async def add_ozon_barcode(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Привязать баркод к товару в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "api_key" not in keys or "client_id" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    payload = {"barcodes": request_data.get("barcodes", [])}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/barcode/add",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon barcodes added: {len(payload['barcodes'])} items")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error adding Ozon barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/ozon/barcode/generate")
async def generate_ozon_barcodes(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Сгенерировать баркоды для товаров в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "api_key" not in keys or "client_id" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    headers = {
        "Client-Id": keys["client_id"],
        "Api-Key": keys["api_key"],
        "Content-Type": "application/json"
    }
    payload = {"product_ids": [str(pid) for pid in request_data.get("product_ids", [])]}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api-seller.ozon.ru/v1/barcode/generate",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ozon barcodes generated for {len(payload['product_ids'])} products")
                return data
            else:
                error_detail = response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"Ozon error: {error_detail}")
    except Exception as e:
        print(f"❌ Error generating Ozon barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/wildberries/barcode/generate")
async def generate_wildberries_barcodes(
    request_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Сгенерировать баркоды в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    headers = {"X-API-Key": keys["api_key"], "Content-Type": "application/json"}
    count = request_data.get("count", 1)
    if count < 1 or count > 5000:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 5000")
    payload = {"count": count}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://content-api.wildberries.ru/content/v2/barcodes",
                headers=headers,
                json=payload
            )
            if response.status_code == 200:
                data = response.json()
                print(f"✅ WB barcodes generated: {count} items")
                return data
            else:
                error_detail = response.text
                print(f"❌ WB API error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"WB error: {error_detail}")
    except Exception as e:
        print(f"❌ Error generating WB barcodes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== OPENROUTER AI ENDPOINTS ========================
async def generate_description_openrouter(
    product_name: str,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    key_features: Optional[List[str]] = None,
    marketplace: str = "ozon"
) -> str:
    """Генерация описания товара с помощью OpenRouter AI"""
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter API key not configured. Set OPENROUTER_API_KEY environment variable."
        )
    features_text = ", ".join(key_features) if key_features else "не указаны"
    marketplace_name = "Ozon" if marketplace == "ozon" else "Wildberries"
    prompt = f"""Напиши краткое и привлекательное описание товара для маркетплейса {marketplace_name}.
Название товара: {product_name}
Бренд: {brand or 'не указан'}
Категория: {category or 'не указана'}
Ключевые характеристики: {features_text}
Требования:
- Описание должно быть кратким (100-150 слов)
- Описание должно быть привлекательным и информативным
- Описание должно подчеркивать преимущества товара
- Избегай чрезмерно использовать восклицательные знаки
- Используй профессиональный тон
- Написано для {marketplace_name}
Напиши только описание без дополнительных комментариев."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Marketplace Manager"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 1024,
        "top_p": 0.95
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        description = choice["message"]["content"].strip()
                        if description and description != "":
                            print(f"✅ Description generated for: {product_name}")
                            return description
                        else:
                            raise HTTPException(status_code=500, detail="Empty response from OpenRouter (empty content)")
                    else:
                        raise HTTPException(status_code=500, detail="No choices in response from OpenRouter")
                else:
                    raise HTTPException(status_code=500, detail="No choices in response from OpenRouter")
            else:
                error_detail = response.text
                print(f"❌ OpenRouter error: {response.status_code} - {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=f"OpenRouter error: {error_detail}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenRouter request timeout")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ OpenRouter error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/generate-description")
async def generate_description(
    request: GenerateDescriptionRequest,
    current_user: dict = Depends(get_current_user)
):
    """Endpoint для генерации описания товара с помощью OpenRouter AI"""
    try:
        description = await generate_description_openrouter(
            product_name=request.product_name,
            brand=request.brand,
            category=request.category,
            key_features=request.key_features,
            marketplace=request.marketplace
        )
        return {"success": True, "description": description}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"❌ Error generating description: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== VIDEO COVER GENERATION ========================
from PIL import Image

@app.post("/api/generate-video-cover")
async def generate_video_cover(file_ids: List[str], current_user: dict = Depends(get_current_user)):
    """Генерация видеообложки из временных файлов"""
    if len(file_ids) == 0:
        raise HTTPException(status_code=400, detail="No images provided")
    
    # Получаем пути к временным файлам
    image_files = []
    for file_id in file_ids:
        if file_id in temp_files:
            image_files.append(temp_files[file_id])
        else:
            raise HTTPException(status_code=400, detail=f"File not found: {file_id}")
    
    if len(image_files) == 0:
        raise HTTPException(status_code=400, detail="Failed to load any images")
    
    # Проверка размеров изображений
    sizes = []
    for image_path in image_files:
        with Image.open(image_path) as img:
            sizes.append(img.size)
    if len(set(sizes)) > 1:
        size_info = ", ".join([f"{w}x{h}" for w, h in sizes])
        raise HTTPException(
            status_code=400, 
            detail=f"Фото должны быть одинакового размера. Обнаружены размеры: {size_info}"
        )
    
    # Создаём видеообложку
    # Вычисляем длительность для каждого изображения (равномерно распределяем 10 секунд)
    total_duration = 10.0
    num_images = len(image_files)
    duration_per_image = total_duration / num_images
    
    # Создаем список длительностей для каждого изображения
    durations = [duration_per_image] * num_images
    
    clip = ImageSequenceClip(image_files, durations=durations)
    video_path = tempfile.mktemp(suffix=".mp4")
    # Вычисляем fps так, чтобы видео было плавным
    fps = max(1.0, num_images / total_duration)
    clip.write_videofile(video_path, codec="libx264", fps=fps)
    
    # Загружаем видео на Яндекс Диск
    with open(video_path, "rb") as f:
        file_content = f.read()
    
    video_url = upload_to_yandex_disk(file_content, "cover_video.mp4", current_user["username"], "video/mp4")
    
    # Удаляем временные файлы
    for file_id in file_ids:
        if file_id in temp_files:
            os.remove(temp_files[file_id])
            del temp_files[file_id]
    os.remove(video_path)
    
    return {"video_url": video_url}

# ======================== PRODUCT ENDPOINTS ========================
def validate_image_url(url: str) -> tuple[bool, str]:
    """Валидация URL изображения - возвращает (is_valid, error_message)"""
    if not url or not url.strip():
        return False, "Пустой URL"
    
    # Проверка формата URL
    if not url.startswith(('http://', 'https://', '/')):
        return False, "URL должен начинаться с http://, https:// или /"
    
    # Проверка расширения файла
    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    url_lower = url.lower()
    has_valid_extension = any(url_lower.endswith(ext) for ext in valid_extensions)
    
    # Если это прокси URL, пропускаем проверку расширения
    if '/api/media-proxy' in url:
        return True, ""
    
    if not has_valid_extension and not url.startswith('/'):
        return False, f"Неподдерживаемый формат изображения. Разрешенные: {', '.join(valid_extensions)}"
    
    return True, ""

def validate_ozon_product(product: ProductCreate, required_characteristics: Dict[int, str] = None) -> List[str]:
    """Валидация товара для Ozon API - возвращает список ошибок
    
    Args:
        product: Товар для валидации
        required_characteristics: Словарь {char_id: char_name} обязательных характеристик
    """
    errors = []
    
    # Обязательные поля
    if not product.offer_id or not product.offer_id.strip():
        errors.append("offer_id обязателен")
    if not product.name or not product.name.strip():
        errors.append("name обязателен")
    if not product.category:
        errors.append("category_id обязателен")
    if not product.type:
        errors.append("type_id обязателен")
    
    # Валидация цены
    if product.price <= 0:
        errors.append("price должен быть больше 0")
    if product.price > 10000000:  # 100 млн рублей
        errors.append("price слишком большой (максимум 10000000)")
    
    # Валидация остатков
    if product.stock < 0:
        errors.append("stock не может быть отрицательным")
    
    # Валидация изображений (рекомендуется минимум 1)
    if product.images:
        if len(product.images) > 20:
            errors.append("Максимум 20 изображений для Ozon")
        # Проверяем формат URL и расширения
        for img_url in product.images:
            if img_url:
                is_valid, error_msg = validate_image_url(img_url)
                if not is_valid:
                    errors.append(f"Некорректный URL изображения: {error_msg}")
    
    # Проверка обязательных характеристик
    if required_characteristics:
        provided_chars = set(product.characteristics.keys() if product.characteristics else [])
        for char_id, char_name in required_characteristics.items():
            if str(char_id) not in provided_chars or not product.characteristics.get(str(char_id), "").strip():
                errors.append(f"Обязательная характеристика не заполнена: {char_name}")
    
    return errors

def build_ozon_product(product: ProductCreate, required_characteristics: Dict[int, str] = None) -> dict:
    """Построение продукта для Ozon API"""
    # Валидация перед построением
    validation_errors = validate_ozon_product(product, required_characteristics)
    if validation_errors:
        raise ValueError(f"Ошибки валидации: {', '.join(validation_errors)}")
    
    ozon_product = {
        "offer_id": product.offer_id.strip(),
        "name": product.name.strip(),
        "brand": (product.brand or "").strip(),
        "price": str(int(product.price * 100)),
        "description": (product.description or "").strip(),
    }
    
    # Категория и тип товара (обязательные для Ozon)
    if product.category:
        ozon_product["category_id"] = int(product.category)
    if product.type:
        ozon_product["type_id"] = int(product.type)
    
    # VAT (НДС) - по умолчанию 0 (без НДС)
    ozon_product["vat"] = "0"
    
    # Характеристики товара
    if product.characteristics:
        attributes = []
        for char_id, value in product.characteristics.items():
            if value and str(value).strip():  # Пропускаем пустые значения
                try:
                    attributes.append({
                        "id": int(char_id),
                        "value": str(value).strip()
                    })
                except (ValueError, TypeError):
                    # Пропускаем некорректные значения
                    print(f"Warning: Invalid characteristic value for id {char_id}: {value}")
        if attributes:
            ozon_product["attributes"] = attributes
    
    # Изображения (минимум 1 для Ozon)
    if product.images:
        # Фильтруем пустые URL и ограничиваем количество
        valid_images = [url for url in product.images if url and url.strip()][:20]
        if valid_images:
            ozon_product["images"] = [{"file_name": url} for url in valid_images]
            # Первое изображение по умолчанию - главное
            if product.primary_image and 0 < product.primary_image <= len(valid_images):
                ozon_product["primary_image"] = {"file_name": valid_images[product.primary_image - 1]}
            elif len(valid_images) > 0:
                ozon_product["primary_image"] = {"file_name": valid_images[0]}
    
    # Видео
    if product.video_url:
        ozon_product["complex_attributes"] = [
            {
                "complex_id": 100002,
                "id": 21845,
                "values": [{"value": product.video_url}]
            }
        ]
    
    # Баркод
    if product.barcode:
        ozon_product["barcode"] = product.barcode
    
    return ozon_product

async def ozon_create_product(product: ProductCreate, client_id: str, api_key: str, user_id: int = None) -> dict:
    ozon_product = build_ozon_product(product)
    payload = {"items": [ozon_product]}
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    endpoint = "https://api-seller.ozon.ru/v2/product/import"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=headers
            )
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            # Логирование запроса
            log_api_request(
                user_id=user_id,
                endpoint=endpoint,
                method="POST",
                request_data=payload,
                response_data=response_data,
                status_code=response.status_code,
                error=None if success else str(response_data.get("error", response.text))
            )
            
            if not success:
                error_msg = response_data.get("error", {}).get("message", response.text) if isinstance(response_data, dict) else response.text
                print(f"❌ Ozon API error: {response.status_code} - {error_msg}")
            
            return {
                "status": response.status_code,
                "body": response_data,
                "success": success,
                "error": response_data.get("error") if isinstance(response_data, dict) else None
            }
    except httpx.RequestError as e:
        error_msg = str(e)
        log_api_request(
            user_id=user_id,
            endpoint=endpoint,
            method="POST",
            request_data=payload,
            response_data=None,
            status_code=500,
            error=error_msg
        )
        print(f"❌ Ozon API request error: {error_msg}")
        return {"status": 500, "error": error_msg, "success": False}
    except Exception as e:
        error_msg = str(e)
        log_api_request(
            user_id=user_id,
            endpoint=endpoint,
            method="POST",
            request_data=payload,
            response_data=None,
            status_code=500,
            error=error_msg
        )
        print(f"❌ Ozon API error: {error_msg}")
        return {"status": 500, "error": error_msg, "success": False}

async def ozon_get_products(client_id: str, api_key: str, offer_id: List[str] = None, product_id: List[int] = None, sku: List[int] = None, visibility: str = "ALL", user_id: int = None) -> dict:
    """Получение списка товаров из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v2/product/info/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "filter": {
            "visibility": visibility
        },
        "last_id": "",
        "limit": 100
    }
    
    if offer_id:
        payload["filter"]["offer_id"] = offer_id
    if product_id:
        payload["filter"]["product_id"] = product_id
    if sku:
        payload["filter"]["sku"] = sku
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(
                user_id=user_id,
                endpoint=endpoint,
                method="POST",
                request_data=payload,
                response_data=response_data,
                status_code=response.status_code,
                error=None if success else str(response_data.get("error", response.text))
            )
            
            return {
                "status": response.status_code,
                "body": response_data,
                "success": success
            }
    except Exception as e:
        error_msg = str(e)
        log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, error=error_msg)
        return {"status": 500, "error": error_msg, "success": False}

async def ozon_get_product_info(client_id: str, api_key: str, offer_id: str = None, product_id: int = None, sku: int = None, user_id: int = None) -> dict:
    """Получение информации о товаре из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v2/product/info"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {}
    if offer_id:
        payload["offer_id"] = offer_id
    elif product_id:
        payload["product_id"] = product_id
    elif sku:
        payload["sku"] = sku
    else:
        return {"status": 400, "error": "offer_id, product_id or sku required", "success": False}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_delete_product(client_id: str, api_key: str, product_id: List[int] = None, offer_id: List[str] = None, sku: List[int] = None, user_id: int = None) -> dict:
    """Удаление товара из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/product/delete"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {}
    if product_id:
        payload["product_id"] = product_id
    elif offer_id:
        payload["offer_id"] = offer_id
    elif sku:
        payload["sku"] = sku
    else:
        return {"status": 400, "error": "product_id, offer_id or sku required", "success": False}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_update_prices(client_id: str, api_key: str, prices: List[dict], user_id: int = None) -> dict:
    """Обновление цен товаров в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/product/import/prices"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"prices": prices}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_update_stocks(client_id: str, api_key: str, stocks: List[dict], user_id: int = None) -> dict:
    """Обновление остатков товаров в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/product/import/stocks"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"stocks": stocks}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

def validate_wb_product(product: ProductCreate) -> List[str]:
    """Валидация товара для Wildberries API - возвращает список ошибок"""
    errors = []
    
    # Обязательные поля
    if not product.offer_id or not product.offer_id.strip():
        errors.append("offer_id обязателен")
    if not product.name or not product.name.strip():
        errors.append("name обязателен")
    if not product.category:
        errors.append("object (subject_id) обязателен для Wildberries")
    
    # Валидация цены
    if product.price <= 0:
        errors.append("price должен быть больше 0")
    if product.price > 10000000:  # 100 млн рублей
        errors.append("price слишком большой (максимум 10000000)")
    
    # Валидация остатков
    if product.stock < 0:
        errors.append("stock не может быть отрицательным")
    
    # Валидация изображений (минимум 1 для WB, обязательно)
    images = product.wb_images or product.images or []
    if not images or len(images) == 0:
        errors.append("Необходимо хотя бы одно изображение для Wildberries")
    elif len(images) > 30:
        errors.append("Максимум 30 изображений для Wildberries")
    else:
        # Проверяем формат URL
        for img_url in images:
            if img_url and not img_url.startswith(('http://', 'https://', '/')):
                errors.append(f"Некорректный URL изображения: {img_url}")
    
    # Валидация описания (рекомендуется)
    if not product.description or len(product.description.strip()) < 50:
        errors.append("Описание должно содержать минимум 50 символов (рекомендуется)")
    
    return errors

async def wb_create_product(product: ProductCreate, api_key: str, required_characteristics: Dict[int, str] = None) -> dict:
    # Валидация перед построением
    validation_errors = validate_wb_product(product, required_characteristics)
    if validation_errors:
        raise ValueError(f"Ошибки валидации: {', '.join(validation_errors)}")
    
    wb_product = {
        "vendorCode": (product.wb_sku or product.offer_id).strip(),
        "brand": (product.brand or "").strip(),
        "title": product.name.strip(),
        "description": (product.description or "").strip(),
        "sizes": [
            {
                "skus": [product.offer_id.strip()],
                "price": int(product.price * 100),
                "stocks": [{"warehouseId": 0, "quantity": max(0, product.stock)}]
            }
        ]
    }
    
    # Категория (subject_id для WB) - обязательна
    if product.category:
        wb_product["object"] = str(int(product.category))
    
    # Характеристики товара
    if product.characteristics:
        characteristics = []
        for char_id, value in product.characteristics.items():
            if value and str(value).strip():  # Пропускаем пустые значения
                try:
                    characteristics.append({
                        "id": int(char_id),
                        "value": str(value).strip()
                    })
                except (ValueError, TypeError):
                    # Пропускаем некорректные значения
                    print(f"Warning: Invalid characteristic value for id {char_id}: {value}")
        if characteristics:
            wb_product["characteristics"] = characteristics
    
    # Изображения (обязательны для WB, минимум 1)
    images = product.wb_images or product.images or []
    if images:
        # Фильтруем пустые URL и ограничиваем количество
        valid_images = [url for url in images if url and url.strip()][:30]
        if valid_images:
            wb_product["mediaFiles"] = valid_images
    
    # Видео
    if product.wb_video:
        wb_product["video"] = product.wb_video
    elif product.video_url:
        wb_product["video"] = product.video_url
    
    payload = [wb_product]
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    endpoint = "https://suppliers-api.wildberries.ru/content/v2/cards/upload"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=headers
            )
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            # Логирование будет добавлено в вызывающей функции с user_id
            
            if not success:
                error_msg = response_data.get("error", {}).get("message", response.text) if isinstance(response_data, dict) else response.text
                print(f"❌ Wildberries API error: {response.status_code} - {error_msg}")
            
            return {
                "status": response.status_code,
                "body": response_data,
                "success": success,
                "error": response_data.get("error") if isinstance(response_data, dict) else None
            }
    except httpx.RequestError as e:
        error_msg = str(e)
        print(f"❌ Wildberries API request error: {error_msg}")
        return {"status": 500, "error": error_msg, "success": False}
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Wildberries API error: {error_msg}")
        return {"status": 500, "error": error_msg, "success": False}

async def wb_get_cards(api_key: str, limit: int = 100, offset: int = 0, user_id: int = None) -> dict:
    """Получение списка карточек из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/content/v1/cards/cursor/list"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "sort": {
            "cursor": {
                "limit": limit
            },
            "filter": {
                "withPhoto": -1
            }
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_card(api_key: str, nm_id: int = None, vendor_code: str = None, user_id: int = None) -> dict:
    """Получение информации о карточке из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/content/v1/cards/filter"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {}
    if nm_id:
        payload["find"] = [{"id": nm_id}]
    elif vendor_code:
        payload["find"] = [{"vendorCode": vendor_code}]
    else:
        return {"status": 400, "error": "nm_id or vendor_code required", "success": False}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_delete_card(api_key: str, nm_id: int, user_id: int = None) -> dict:
    """Удаление карточки из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/content/v1/cards/delete"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"nmIDs": [nm_id]}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_update_prices(api_key: str, prices: List[dict], user_id: int = None) -> dict:
    """Обновление цен товаров в Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/public/api/v1/prices"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=prices, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=prices, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_update_stocks(api_key: str, stocks: List[dict], user_id: int = None) -> dict:
    """Обновление остатков товаров в Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v3/stocks"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"stocks": stocks}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PUT", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== OZON ORDERS API ========================
async def ozon_get_orders(client_id: str, api_key: str, dir: str = "ASC", filter: dict = None, limit: int = 100, offset: int = 0, with_: dict = None, user_id: int = None) -> dict:
    """Получение списка заказов из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v3/posting/fbs/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "dir": dir,
        "filter": filter or {},
        "limit": limit,
        "offset": offset,
        "with": with_ or {}
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_get_order(client_id: str, api_key: str, posting_number: str, user_id: int = None) -> dict:
    """Получение информации о заказе из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v3/posting/fbs/get"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"posting_number": posting_number}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_cancel_order(client_id: str, api_key: str, posting_number: str, cancel_reason_id: int, user_id: int = None) -> dict:
    """Отмена заказа в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v3/posting/fbs/cancel"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "posting_number": posting_number,
        "cancel_reason_id": cancel_reason_id
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_ship_order(client_id: str, api_key: str, posting_number: str, packages: List[dict], user_id: int = None) -> dict:
    """Отправка заказа в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v3/posting/fbs/ship"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "posting_number": posting_number,
        "packages": packages
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== OZON ANALYTICS API ========================
async def ozon_get_sales_report(client_id: str, api_key: str, date_from: str, date_to: str, dimension: List[str] = None, metrics: List[str] = None, filters: List[dict] = None, user_id: int = None) -> dict:
    """Получение отчета по продажам из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/analytics/data"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "date_from": date_from,
        "date_to": date_to,
        "dimension": dimension or ["sku"],
        "metrics": metrics or ["ordered_units", "revenue"]
    }
    if filters:
        payload["filters"] = filters
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_get_stocks_report(client_id: str, api_key: str, user_id: int = None) -> dict:
    """Получение отчета по остаткам из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/analytics/stock_on_warehouses"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json={}, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_get_finance_report(client_id: str, api_key: str, filter: dict = None, page: int = 1, page_size: int = 100, user_id: int = None) -> dict:
    """Получение финансового отчета из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v3/finance/transaction/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "filter": filter or {},
        "page": page,
        "page_size": page_size
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== OZON WAREHOUSES API ========================
async def ozon_get_warehouses(client_id: str, api_key: str, user_id: int = None) -> dict:
    """Получение списка складов из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/warehouse/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json={}, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_get_product_stocks(client_id: str, api_key: str, sku: List[int] = None, offer_id: List[str] = None, user_id: int = None) -> dict:
    """Получение остатков товара на складах из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/product/info/stocks"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {}
    if sku:
        payload["sku"] = sku
    if offer_id:
        payload["offer_id"] = offer_id
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== OZON ACTIONS API ========================
async def ozon_create_action(client_id: str, api_key: str, action: dict, user_id: int = None) -> dict:
    """Создание акции в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/actions"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=action, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=action, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_get_actions(client_id: str, api_key: str, filter: dict = None, user_id: int = None) -> dict:
    """Получение списка акций из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/actions"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"filter": filter or {}}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== OZON REVIEWS API ========================
async def ozon_get_reviews(client_id: str, api_key: str, filter: dict = None, page: int = 1, page_size: int = 10, user_id: int = None) -> dict:
    """Получение списка отзывов из Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/review/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "filter": filter or {},
        "page": page,
        "page_size": page_size
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def ozon_answer_review(client_id: str, api_key: str, review_id: int, text: str, user_id: int = None) -> dict:
    """Ответ на отзыв в Ozon"""
    endpoint = "https://api-seller.ozon.ru/v1/review/answer"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "review_id": review_id,
        "text": text
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES ORDERS API ========================
async def wb_get_orders(api_key: str, date_from: str = None, date_to: str = None, status: int = None, take: int = 100, skip: int = 0, user_id: int = None) -> dict:
    """Получение списка заказов из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/orders"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "take": take,
        "skip": skip
    }
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    if status is not None:
        params["status"] = status
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_order(api_key: str, order_id: int, user_id: int = None) -> dict:
    """Получение информации о заказе из Wildberries"""
    endpoint = f"https://suppliers-api.wildberries.ru/api/v1/orders/{order_id}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_confirm_order(api_key: str, order_id: int, user_id: int = None) -> dict:
    """Подтверждение заказа в Wildberries"""
    endpoint = f"https://suppliers-api.wildberries.ru/api/v1/orders/{order_id}/confirm"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PUT", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_cancel_order(api_key: str, order_id: int, user_id: int = None) -> dict:
    """Отмена заказа в Wildberries"""
    endpoint = f"https://suppliers-api.wildberries.ru/api/v1/orders/{order_id}/cancel"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PUT", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES ANALYTICS API ========================
async def wb_get_sales_report(api_key: str, date_from: str, date_to: str, rrdid: int = 0, user_id: int = None) -> dict:
    """Получение отчета по продажам из Wildberries"""
    endpoint = "https://statistics-api.wildberries.ru/api/v1/supplier/reportDetailByPeriod"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "rrdid": rrdid
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_stocks_report(api_key: str, date_from: str = None, user_id: int = None) -> dict:
    """Получение отчета по остаткам из Wildberries"""
    endpoint = "https://statistics-api.wildberries.ru/api/v1/supplier/stocks"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {}
    if date_from:
        params["dateFrom"] = date_from
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_payments_report(api_key: str, date_from: str, date_to: str, user_id: int = None) -> dict:
    """Получение финансового отчета из Wildberries"""
    endpoint = "https://statistics-api.wildberries.ru/api/v1/supplier/payments"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "dateFrom": date_from,
        "dateTo": date_to
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES WAREHOUSES API ========================
async def wb_get_warehouses(api_key: str, user_id: int = None) -> dict:
    """Получение списка складов из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/warehouses"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES DISCOUNTS API ========================
async def wb_create_discount(api_key: str, discount: dict, user_id: int = None) -> dict:
    """Создание скидки в Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/supplier/discounts"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=discount, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=discount, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_discounts(api_key: str, user_id: int = None) -> dict:
    """Получение списка скидок из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/supplier/discounts"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES QUESTIONS & FEEDBACKS API ========================
async def wb_get_questions(api_key: str, date_from: str = None, date_to: str = None, is_answered: bool = None, take: int = 100, skip: int = 0, user_id: int = None) -> dict:
    """Получение списка вопросов из Wildberries"""
    endpoint = "https://feedbacks-api.wildberries.ru/api/v1/questions"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "take": take,
        "skip": skip
    }
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    if is_answered is not None:
        params["isAnswered"] = is_answered
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_answer_question(api_key: str, question_id: int, text: str, user_id: int = None) -> dict:
    """Ответ на вопрос в Wildberries"""
    endpoint = f"https://feedbacks-api.wildberries.ru/api/v1/questions/{question_id}/answer"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"text": text}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PATCH", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_feedbacks(api_key: str, date_from: str = None, date_to: str = None, is_answered: bool = None, take: int = 100, skip: int = 0, user_id: int = None) -> dict:
    """Получение списка отзывов из Wildberries"""
    endpoint = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "take": take,
        "skip": skip
    }
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    if is_answered is not None:
        params["isAnswered"] = is_answered
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_answer_feedback(api_key: str, feedback_id: int, text: str, user_id: int = None) -> dict:
    """Ответ на отзыв в Wildberries"""
    endpoint = f"https://feedbacks-api.wildberries.ru/api/v1/feedbacks/{feedback_id}/answer"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"text": text}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PATCH", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES SUPPLIES API ========================
async def wb_create_supply(api_key: str, supply_name: str, user_id: int = None) -> dict:
    """Создание поставки в Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/supplies"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {"name": supply_name}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=payload, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_supplies(api_key: str, limit: int = 100, next: int = 0, user_id: int = None) -> dict:
    """Получение списка поставок из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/supplies"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {
        "limit": limit,
        "next": next
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data=params, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_close_supply(api_key: str, supply_id: str, user_id: int = None) -> dict:
    """Закрытие поставки в Wildberries"""
    endpoint = f"https://suppliers-api.wildberries.ru/api/v1/supplies/{supply_id}/close"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="PATCH", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

# ======================== WILDBERRIES PROMOCODES API ========================
async def wb_create_promocode(api_key: str, promocode: dict, user_id: int = None) -> dict:
    """Создание промокода в Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/promocodes"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=promocode, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="POST", request_data=promocode, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

async def wb_get_promocodes(api_key: str, user_id: int = None) -> dict:
    """Получение списка промокодов из Wildberries"""
    endpoint = "https://suppliers-api.wildberries.ru/api/v1/promocodes"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, headers=headers)
            response_data = response.json() if response.text else {}
            success = response.status_code == 200
            
            log_api_request(user_id=user_id, endpoint=endpoint, method="GET", request_data={}, response_data=response_data, status_code=response.status_code)
            
            return {"status": response.status_code, "body": response_data, "success": success}
    except Exception as e:
        return {"status": 500, "error": str(e), "success": False}

@app.post("/api/ozon/products/batch")
async def batch_create_ozon(
    batch: BatchProducts,
    current_user: dict = Depends(get_current_user)
):
    if len(batch.products) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 products per batch")
    
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    # Проверка дубликатов offer_id в батче
    offer_ids = {}
    duplicates = []
    for i, product in enumerate(batch.products):
        offer_id = product.offer_id.strip()
        if offer_id in offer_ids:
            duplicates.append(f"Товар #{i+1}: offer_id '{offer_id}' дублируется с товаром #{offer_ids[offer_id]+1}")
        else:
            offer_ids[offer_id] = i
    
    if duplicates:
        return {
            "status": "duplicates_found",
            "total": len(batch.products),
            "duplicates": duplicates,
            "message": f"Найдено {len(duplicates)} дубликатов offer_id"
        }
    
    results = []
    validation_errors = []
    
    # Предварительная валидация всех товаров
    for i, product in enumerate(batch.products):
        try:
            errors = validate_ozon_product(product)
            if errors:
                validation_errors.append({
                    "index": i,
                    "offer_id": product.offer_id,
                    "errors": errors
                })
        except Exception as e:
            validation_errors.append({
                "index": i,
                "offer_id": product.offer_id,
                "errors": [str(e)]
            })
    
    # Если есть ошибки валидации, возвращаем их
    if validation_errors:
        return {
            "status": "validation_failed",
            "total": len(batch.products),
            "validation_errors": validation_errors,
            "message": f"Найдено {len(validation_errors)} товаров с ошибками валидации"
        }
    
    # Создаем товары
    for product in batch.products:
        try:
            result = await ozon_create_product(product, keys["client_id"], keys["api_key"], user_id=current_user["user_id"])
            results.append({
                "offer_id": product.offer_id,
                "result": result
            })
            save_product_history(
                current_user["user_id"],
                "ozon",
                product.offer_id,
                product.dict(),
                "success" if result.get("success") else "failed",
                result
            )
        except ValueError as e:
            # Ошибка валидации
            results.append({
                "offer_id": product.offer_id,
                "result": {
                    "success": False,
                    "error": str(e),
                    "status": 400
                }
            })
        except Exception as e:
            # Другие ошибки
            results.append({
                "offer_id": product.offer_id,
                "result": {
                    "success": False,
                    "error": str(e),
                    "status": 500
                }
            })
    
    success_count = sum(1 for r in results if r.get("result", {}).get("success", False))
    print(f"✅ Batch created {success_count}/{len(batch.products)} products in Ozon")
    return {
        "total": len(batch.products),
        "success": success_count,
        "failed": len(batch.products) - success_count,
        "results": results
    }

@app.post("/api/wb/products/batch")
async def batch_create_wb(
    batch: BatchProducts,
    current_user: dict = Depends(get_current_user)
):
    if len(batch.products) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 products per batch")
    
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    results = []
    validation_errors = []
    
    # Предварительная валидация всех товаров
    for i, product in enumerate(batch.products):
        try:
            errors = validate_wb_product(product)
            if errors:
                validation_errors.append({
                    "index": i,
                    "offer_id": product.offer_id,
                    "errors": errors
                })
        except Exception as e:
            validation_errors.append({
                "index": i,
                "offer_id": product.offer_id,
                "errors": [str(e)]
            })
    
    # Если есть ошибки валидации, возвращаем их
    if validation_errors:
        return {
            "status": "validation_failed",
            "total": len(batch.products),
            "validation_errors": validation_errors,
            "message": f"Найдено {len(validation_errors)} товаров с ошибками валидации"
        }
    
    # Создаем товары
    for product in batch.products:
        try:
            result = await wb_create_product(product, keys["api_key"], user_id=current_user["user_id"])
            results.append({
                "offer_id": product.offer_id,
                "result": result
            })
            save_product_history(
                current_user["user_id"],
                "wildberries",
                product.offer_id,
                product.dict(),
                "success" if result.get("success") else "failed",
                result
            )
        except ValueError as e:
            # Ошибка валидации
            results.append({
                "offer_id": product.offer_id,
                "result": {
                    "success": False,
                    "error": str(e),
                    "status": 400
                }
            })
        except Exception as e:
            # Другие ошибки
            results.append({
                "offer_id": product.offer_id,
                "result": {
                    "success": False,
                    "error": str(e),
                    "status": 500
                }
            })
    
    success_count = sum(1 for r in results if r.get("result", {}).get("success", False))
    print(f"✅ Batch created {success_count}/{len(batch.products)} products in Wildberries")
    return {
        "total": len(batch.products),
        "success": success_count,
        "failed": len(batch.products) - success_count,
        "results": results
    }

# ======================== MEDIA UPLOAD ENDPOINT (Яндекс Диск) ========================
@app.post("/api/upload-media")
async def upload_media(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Загрузка медиа на Яндекс Диск и сохранение временного пути"""
    try:
        # Читаем содержимое файла
        file_content = await file.read()
        print(f"📤 Uploading file: {file.filename}, size: {len(file_content)} bytes")
        
        # Загружаем файл на Яндекс Диск в папку пользователя
        file_url = upload_to_yandex_disk(file_content, file.filename, current_user["username"], file.content_type)
        print(f"✅ File uploaded successfully: {file_url}")
        
        # Сохраняем файл во временное хранилище для генерации видеообложки
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename or "temp_file")
        with open(temp_path, "wb") as f:
            f.write(file_content)
        
        file_id = str(id(file))
        temp_files[file_id] = temp_path
        
        return {"file_id": file_id, "url": file_url}
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Upload error: {error_msg}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error: {error_msg}")

class DeleteMediaRequest(BaseModel):
    file_urls: List[str]

@app.get("/api/media-proxy")
async def media_proxy(path: str):
    """Прокси для загрузки медиа файлов с Яндекс Диска (отдает файлы напрямую, обход CORS)"""
    try:
        client = get_yandex_disk_client()
        
        print(f"📥 Proxying file: {path}")
        
        # Получаем download ссылку для файла
        try:
            download_url = client.get_download_link(path)
            print(f"✅ Download URL obtained: {download_url[:100]}...")
        except Exception as e:
            print(f"❌ Error getting download link for {path}: {str(e)}")
            raise HTTPException(status_code=404, detail=f"File not found: {str(e)}")
        
        # Загружаем файл с Яндекс Диска и отдаем напрямую
        try:
            # Используем httpx для загрузки файла с правильными заголовками
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Referer": "https://disk.yandex.ru/"
            }
            
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http_client:
                response = await http_client.get(download_url, headers=headers)
                print(f"📥 Response status: {response.status_code}, size: {len(response.content)} bytes")
                
                if response.status_code == 200:
                    # Определяем content-type из заголовков или по расширению файла
                    content_type = response.headers.get('content-type', 'application/octet-stream')
                    if content_type == 'application/octet-stream' or 'text/html' in content_type:
                        # Пробуем определить по расширению
                        file_ext = Path(path).suffix.lower()
                        content_type_map = {
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.png': 'image/png',
                            '.gif': 'image/gif',
                            '.webp': 'image/webp',
                            '.mp4': 'video/mp4',
                            '.webm': 'video/webm',
                            '.mov': 'video/quicktime'
                        }
                        content_type = content_type_map.get(file_ext, 'application/octet-stream')
                        print(f"📋 Detected content type: {content_type}")
                    
                    # Отдаем файл напрямую через streaming
                    return StreamingResponse(
                        iter([response.content]),
                        media_type=content_type,
                        headers={
                            "Cache-Control": "public, max-age=3600",
                            "Content-Disposition": f'inline; filename="{Path(path).name}"',
                            "Access-Control-Allow-Origin": "*"
                        }
                    )
                else:
                    error_text = response.text[:200] if response.text else "No error message"
                    print(f"❌ Failed to download: {response.status_code} - {error_text}")
                    raise HTTPException(status_code=response.status_code, detail=f"Failed to download file: {error_text}")
        except httpx.RequestError as e:
            print(f"❌ Error downloading file: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error proxying media: {str(e)}")

@app.post("/api/delete-media")
async def delete_media(request: DeleteMediaRequest):
    """Удаление файлов с Яндекс Диска"""
    try:
        deleted_count = 0
        for file_url in request.file_urls:
            if delete_from_yandex_disk(file_url):
                deleted_count += 1
        
        if deleted_count > 0:
            return {"status": "ok", "message": f"Deleted {deleted_count} file(s)"}
        else:
            return {"status": "warning", "message": "No files were deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== YANDEX DISK OAUTH ========================
@app.get("/api/yandex-disk/auth-url")
async def get_yandex_disk_auth_url():
    """Получение URL для авторизации в Яндекс Диске"""
    redirect_uri = os.getenv("YANDEX_DISK_REDIRECT_URI", "https://oauth.yandex.ru/verification_code")
    # Используем расширенные права для работы с файлами на всем Диске
    # cloud_api:disk:write - для записи файлов в любом месте
    # cloud_api:disk:read - для чтения файлов
    # cloud_api:disk.app_folder - доступ к папке приложения
    scopes = "cloud_api:disk:write cloud_api:disk:read cloud_api:disk.app_folder"
    auth_url = f"https://oauth.yandex.ru/authorize?response_type=code&client_id={YANDEX_DISK_CLIENT_ID}&redirect_uri={redirect_uri}&scope={scopes}"
    return {"auth_url": auth_url, "redirect_uri": redirect_uri, "scopes": scopes}

@app.post("/api/yandex-disk/get-token")
async def get_yandex_disk_token(code: str):
    """Получение OAuth токена по коду авторизации"""
    try:
        redirect_uri = os.getenv("YANDEX_DISK_REDIRECT_URI", "https://oauth.yandex.ru/verification_code")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_DISK_CLIENT_ID,
            "client_secret": YANDEX_DISK_CLIENT_SECRET
        }
        response = requests.post("https://oauth.yandex.ru/token", data=data)
        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get("access_token")
            if access_token:
                # Сохраняем токен в переменную окружения (в продакшене лучше использовать БД или секреты)
                global YANDEX_DISK_TOKEN
                YANDEX_DISK_TOKEN = access_token
                return {
                    "status": "ok",
                    "message": "Token received successfully",
                    "token": access_token,
                    "expires_in": token_data.get("expires_in", 0)
                }
            else:
                raise HTTPException(status_code=400, detail="No access token in response")
        else:
            raise HTTPException(status_code=response.status_code, detail=f"Failed to get token: {response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/api/yandex-disk/set-token")
async def set_yandex_disk_token(token: str, current_user: dict = Depends(get_current_user)):
    """Установка OAuth токена вручную (для тестирования)"""
    try:
        # Проверяем токен
        client = yadisk.Client(token=token)
        if not client.check_token():
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Сохраняем токен
        global YANDEX_DISK_TOKEN
        YANDEX_DISK_TOKEN = token
        
        return {"status": "ok", "message": "Token set successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== STATIC FILES ========================
@app.get("/")
async def root():
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"message": "Welcome to Marketplace Manager v3.5"}

@app.get("/index.html")
async def index_page():
    index_path = FRONTEND_PATH / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "index.html not found"}

@app.get("/dashboard.html")
async def dashboard_page():
    dashboard_path = FRONTEND_PATH / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
    return {"error": "dashboard.html not found"}

@app.get("/verify-email.html")
async def verify_email_page():
    verify_path = FRONTEND_PATH / "verify-email.html"
    if verify_path.exists():
        return FileResponse(str(verify_path), media_type="text/html")
    return {"error": "verify-email.html not found"}

@app.get("/favicon.ico")
async def favicon():
    favicon_path = FRONTEND_PATH / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    return {"error": "favicon not found"}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.5.0"}

@app.get("/api/debug/users")
async def debug_users():
    """Отладочный эндпоинт для проверки пользователей в базе (только для разработки)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, username, email_verified, created_at FROM users ORDER BY id")
    users = cursor.fetchall()
    conn.close()
    
    users_list = []
    for user in users:
        users_list.append({
            "id": user["id"],
            "email": user["email"],
            "email_normalized": normalize_email(user["email"]) if user["email"] else None,
            "username": user["username"],
            "email_verified": bool(user["email_verified"]),
            "created_at": user["created_at"]
        })
    
    return {
        "total_users": len(users_list),
        "users": users_list
    }

# ======================== STARTUP/SHUTDOWN ========================
@app.on_event("startup")
async def startup():
    print(f"🚀 Application started (version 3.5.0)")
    print(f"📍 Database: {DB_PATH}")
    print(f"📁 Frontend: {FRONTEND_PATH}")
    if OPENROUTER_API_KEY:
        print(f"✅ OpenRouter AI API configured")
        print(f"📊 Model: {OPENROUTER_MODEL}")
    else:
        print(f"⚠️  OpenRouter AI API not configured. Set OPENROUTER_API_KEY environment variable.")
    if YANDEX_DISK_TOKEN:
        print(f"✅ Yandex Disk configured")
    else:
        print(f"⚠️  Yandex Disk token not configured. Use /api/yandex-disk/auth-url to get token")
    print(f"📂 Categories endpoints:")
    print(f"   - POST /api/categories/ozon/tree")
    print(f"   - GET /api/categories/wildberries/tree")
    print(f"📦 Ozon Product Management:")
    print(f"   - GET /api/ozon/products/list")
    print(f"   - GET /api/ozon/products/info")
    print(f"   - DELETE /api/ozon/products/delete")
    print(f"   - POST /api/ozon/products/prices")
    print(f"   - POST /api/ozon/products/stocks")
    print(f"📦 Wildberries Product Management:")
    print(f"   - GET /api/wb/products/list")
    print(f"   - GET /api/wb/products/info")
    print(f"   - DELETE /api/wb/products/delete")
    print(f"   - POST /api/wb/products/prices")
    print(f"   - POST /api/wb/products/stocks")
    print(f"   - GET /api/categories/wildberries/subjects")
    print(f"   - GET /api/categories/wildberries/characteristics/{{subject_id}}")

# ======================== PRODUCT STATUS & HISTORY ENDPOINTS ========================
@app.get("/api/products/history")
async def get_products_history(
    marketplace: Optional[str] = None,
    offer_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получить историю создания товаров"""
    history = get_product_history(current_user["user_id"], marketplace, offer_id)
    return {
        "total": len(history),
        "history": history
    }

@app.get("/api/products/status/{marketplace}/{offer_id}")
async def get_product_status(
    marketplace: str,
    offer_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Получить статус товара на маркетплейсе"""
    keys = get_api_keys(current_user["user_id"], marketplace)
    if not keys:
        raise HTTPException(status_code=400, detail=f"{marketplace} API keys not configured")
    
    try:
        if marketplace == "ozon":
            if "client_id" not in keys or "api_key" not in keys:
                raise HTTPException(status_code=400, detail="Ozon API keys not configured")
            headers = {
                "Client-Id": keys["client_id"],
                "Api-Key": keys["api_key"],
                "Content-Type": "application/json"
            }
            payload = {"offer_id": offer_id}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api-seller.ozon.ru/v2/product/info",
                    json=payload,
                    headers=headers
                )
                if response.status_code == 200:
                    return {"status": "ok", "data": response.json()}
                else:
                    return {"status": "error", "error": response.text}
        elif marketplace == "wildberries":
            if "api_key" not in keys:
                raise HTTPException(status_code=400, detail="Wildberries API key not configured")
            headers = {"X-API-Key": keys["api_key"]}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"https://content-api.wildberries.ru/content/v1/cards/filter?vendorCode={offer_id}",
                    headers=headers
                )
                if response.status_code == 200:
                    return {"status": "ok", "data": response.json()}
                else:
                    return {"status": "error", "error": response.text}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown marketplace: {marketplace}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.put("/api/ozon/products/update")
async def update_ozon_product(
    product: ProductCreate,
    current_user: dict = Depends(get_current_user)
):
    """Обновление товара в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    try:
        result = await ozon_create_product(product, keys["client_id"], keys["api_key"], user_id=current_user["user_id"])
        save_product_history(
            current_user["user_id"],
            "ozon",
            product.offer_id,
            product.dict(),
            "updated" if result.get("success") else "update_failed",
            result
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.put("/api/wb/products/update")
async def update_wb_product(
    product: ProductCreate,
    current_user: dict = Depends(get_current_user)
):
    """Обновление товара в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    try:
        result = await wb_create_product(product, keys["api_key"], user_id=current_user["user_id"])
        save_product_history(
            current_user["user_id"],
            "wildberries",
            product.offer_id,
            product.dict(),
            "updated" if result.get("success") else "update_failed",
            result
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# ======================== OZON PRODUCT MANAGEMENT ENDPOINTS ========================
@app.get("/api/ozon/products/list")
async def get_ozon_products(
    offer_id: Optional[str] = None,
    product_id: Optional[int] = None,
    sku: Optional[int] = None,
    visibility: str = "ALL",
    current_user: dict = Depends(get_current_user)
):
    """Получение списка товаров из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    offer_ids = [offer_id] if offer_id else None
    product_ids = [product_id] if product_id else None
    skus = [sku] if sku else None
    
    return await ozon_get_products(
        keys["client_id"],
        keys["api_key"],
        offer_id=offer_ids,
        product_id=product_ids,
        sku=skus,
        visibility=visibility,
        user_id=current_user["user_id"]
    )

@app.get("/api/ozon/products/info")
async def get_ozon_product_info(
    offer_id: Optional[str] = None,
    product_id: Optional[int] = None,
    sku: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение информации о товаре из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_product_info(
        keys["client_id"],
        keys["api_key"],
        offer_id=offer_id,
        product_id=product_id,
        sku=sku,
        user_id=current_user["user_id"]
    )

@app.delete("/api/ozon/products/delete")
async def delete_ozon_product(
    product_id: Optional[List[int]] = None,
    offer_id: Optional[List[str]] = None,
    sku: Optional[List[int]] = None,
    current_user: dict = Depends(get_current_user)
):
    """Удаление товара из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_delete_product(
        keys["client_id"],
        keys["api_key"],
        product_id=product_id,
        offer_id=offer_id,
        sku=sku,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/products/prices")
async def update_ozon_prices(
    prices: List[dict],
    current_user: dict = Depends(get_current_user)
):
    """Обновление цен товаров в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_update_prices(
        keys["client_id"],
        keys["api_key"],
        prices,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/products/stocks")
async def update_ozon_stocks(
    stocks: List[dict],
    current_user: dict = Depends(get_current_user)
):
    """Обновление остатков товаров в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_update_stocks(
        keys["client_id"],
        keys["api_key"],
        stocks,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES PRODUCT MANAGEMENT ENDPOINTS ========================
@app.get("/api/wb/products/list")
async def get_wb_products(
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка карточек из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_cards(
        keys["api_key"],
        limit=limit,
        offset=offset,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/products/info")
async def get_wb_product_info(
    nm_id: Optional[int] = None,
    vendor_code: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение информации о карточке из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_card(
        keys["api_key"],
        nm_id=nm_id,
        vendor_code=vendor_code,
        user_id=current_user["user_id"]
    )

@app.delete("/api/wb/products/delete")
async def delete_wb_product(
    nm_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Удаление карточки из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_delete_card(
        keys["api_key"],
        nm_id,
        user_id=current_user["user_id"]
    )

@app.post("/api/wb/products/prices")
async def update_wb_prices(
    prices: List[dict],
    current_user: dict = Depends(get_current_user)
):
    """Обновление цен товаров в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_update_prices(
        keys["api_key"],
        prices,
        user_id=current_user["user_id"]
    )

@app.post("/api/wb/products/stocks")
async def update_wb_stocks(
    stocks: List[dict],
    current_user: dict = Depends(get_current_user)
):
    """Обновление остатков товаров в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_update_stocks(
        keys["api_key"],
        stocks,
        user_id=current_user["user_id"]
    )

# ======================== OZON ORDERS ENDPOINTS ========================
@app.get("/api/ozon/orders/list")
async def get_ozon_orders(
    dir: str = "ASC",
    limit: int = 100,
    offset: int = 0,
    filter: Optional[dict] = None,
    with_: Optional[dict] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка заказов из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_orders(
        keys["client_id"],
        keys["api_key"],
        dir=dir,
        filter=filter,
        limit=limit,
        offset=offset,
        with_=with_,
        user_id=current_user["user_id"]
    )

@app.get("/api/ozon/orders/info")
async def get_ozon_order(
    posting_number: str,
    current_user: dict = Depends(get_current_user)
):
    """Получение информации о заказе из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_order(
        keys["client_id"],
        keys["api_key"],
        posting_number,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/orders/cancel")
async def cancel_ozon_order(
    posting_number: str,
    cancel_reason_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Отмена заказа в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_cancel_order(
        keys["client_id"],
        keys["api_key"],
        posting_number,
        cancel_reason_id,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/orders/ship")
async def ship_ozon_order(
    posting_number: str,
    packages: List[dict],
    current_user: dict = Depends(get_current_user)
):
    """Отправка заказа в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_ship_order(
        keys["client_id"],
        keys["api_key"],
        posting_number,
        packages,
        user_id=current_user["user_id"]
    )

# ======================== OZON ANALYTICS ENDPOINTS ========================
@app.post("/api/ozon/analytics/sales")
async def get_ozon_sales_report(
    date_from: str,
    date_to: str,
    dimension: Optional[List[str]] = None,
    metrics: Optional[List[str]] = None,
    filters: Optional[List[dict]] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение отчета по продажам из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_sales_report(
        keys["client_id"],
        keys["api_key"],
        date_from,
        date_to,
        dimension=dimension,
        metrics=metrics,
        filters=filters,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/analytics/stocks")
async def get_ozon_stocks_report(
    current_user: dict = Depends(get_current_user)
):
    """Получение отчета по остаткам из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_stocks_report(
        keys["client_id"],
        keys["api_key"],
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/analytics/finance")
async def get_ozon_finance_report(
    filter: Optional[dict] = None,
    page: int = 1,
    page_size: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Получение финансового отчета из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_finance_report(
        keys["client_id"],
        keys["api_key"],
        filter=filter,
        page=page,
        page_size=page_size,
        user_id=current_user["user_id"]
    )

# ======================== OZON WAREHOUSES ENDPOINTS ========================
@app.post("/api/ozon/warehouses/list")
async def get_ozon_warehouses(
    current_user: dict = Depends(get_current_user)
):
    """Получение списка складов из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_warehouses(
        keys["client_id"],
        keys["api_key"],
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/warehouses/stocks")
async def get_ozon_product_stocks(
    sku: Optional[List[int]] = None,
    offer_id: Optional[List[str]] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение остатков товара на складах из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_product_stocks(
        keys["client_id"],
        keys["api_key"],
        sku=sku,
        offer_id=offer_id,
        user_id=current_user["user_id"]
    )

# ======================== OZON ACTIONS ENDPOINTS ========================
@app.post("/api/ozon/actions/create")
async def create_ozon_action(
    action: dict,
    current_user: dict = Depends(get_current_user)
):
    """Создание акции в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_create_action(
        keys["client_id"],
        keys["api_key"],
        action,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/actions/list")
async def get_ozon_actions(
    filter: Optional[dict] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка акций из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_actions(
        keys["client_id"],
        keys["api_key"],
        filter=filter,
        user_id=current_user["user_id"]
    )

# ======================== OZON REVIEWS ENDPOINTS ========================
@app.post("/api/ozon/reviews/list")
async def get_ozon_reviews(
    filter: Optional[dict] = None,
    page: int = 1,
    page_size: int = 10,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка отзывов из Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_get_reviews(
        keys["client_id"],
        keys["api_key"],
        filter=filter,
        page=page,
        page_size=page_size,
        user_id=current_user["user_id"]
    )

@app.post("/api/ozon/reviews/answer")
async def answer_ozon_review(
    review_id: int,
    text: str,
    current_user: dict = Depends(get_current_user)
):
    """Ответ на отзыв в Ozon"""
    keys = get_api_keys(current_user["user_id"], "ozon")
    if not keys or "client_id" not in keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Ozon API keys not configured")
    
    return await ozon_answer_review(
        keys["client_id"],
        keys["api_key"],
        review_id,
        text,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES ORDERS ENDPOINTS ========================
@app.get("/api/wb/orders/list")
async def get_wb_orders(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[int] = None,
    take: int = 100,
    skip: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка заказов из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_orders(
        keys["api_key"],
        date_from=date_from,
        date_to=date_to,
        status=status,
        take=take,
        skip=skip,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/orders/info")
async def get_wb_order(
    order_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Получение информации о заказе из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_order(
        keys["api_key"],
        order_id,
        user_id=current_user["user_id"]
    )

@app.put("/api/wb/orders/confirm")
async def confirm_wb_order(
    order_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Подтверждение заказа в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_confirm_order(
        keys["api_key"],
        order_id,
        user_id=current_user["user_id"]
    )

@app.put("/api/wb/orders/cancel")
async def cancel_wb_order(
    order_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Отмена заказа в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_cancel_order(
        keys["api_key"],
        order_id,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES ANALYTICS ENDPOINTS ========================
@app.get("/api/wb/analytics/sales")
async def get_wb_sales_report(
    date_from: str,
    date_to: str,
    rrdid: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение отчета по продажам из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_sales_report(
        keys["api_key"],
        date_from,
        date_to,
        rrdid=rrdid,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/analytics/stocks")
async def get_wb_stocks_report(
    date_from: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Получение отчета по остаткам из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_stocks_report(
        keys["api_key"],
        date_from=date_from,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/analytics/payments")
async def get_wb_payments_report(
    date_from: str,
    date_to: str,
    current_user: dict = Depends(get_current_user)
):
    """Получение финансового отчета из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_payments_report(
        keys["api_key"],
        date_from,
        date_to,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES WAREHOUSES ENDPOINTS ========================
@app.get("/api/wb/warehouses/list")
async def get_wb_warehouses(
    current_user: dict = Depends(get_current_user)
):
    """Получение списка складов из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_warehouses(
        keys["api_key"],
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES DISCOUNTS ENDPOINTS ========================
@app.post("/api/wb/discounts/create")
async def create_wb_discount(
    discount: dict,
    current_user: dict = Depends(get_current_user)
):
    """Создание скидки в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_create_discount(
        keys["api_key"],
        discount,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/discounts/list")
async def get_wb_discounts(
    current_user: dict = Depends(get_current_user)
):
    """Получение списка скидок из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_discounts(
        keys["api_key"],
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES QUESTIONS & FEEDBACKS ENDPOINTS ========================
@app.get("/api/wb/questions/list")
async def get_wb_questions(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    is_answered: Optional[bool] = None,
    take: int = 100,
    skip: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка вопросов из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_questions(
        keys["api_key"],
        date_from=date_from,
        date_to=date_to,
        is_answered=is_answered,
        take=take,
        skip=skip,
        user_id=current_user["user_id"]
    )

@app.patch("/api/wb/questions/answer")
async def answer_wb_question(
    question_id: int,
    text: str,
    current_user: dict = Depends(get_current_user)
):
    """Ответ на вопрос в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_answer_question(
        keys["api_key"],
        question_id,
        text,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/feedbacks/list")
async def get_wb_feedbacks(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    is_answered: Optional[bool] = None,
    take: int = 100,
    skip: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка отзывов из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_feedbacks(
        keys["api_key"],
        date_from=date_from,
        date_to=date_to,
        is_answered=is_answered,
        take=take,
        skip=skip,
        user_id=current_user["user_id"]
    )

@app.patch("/api/wb/feedbacks/answer")
async def answer_wb_feedback(
    feedback_id: int,
    text: str,
    current_user: dict = Depends(get_current_user)
):
    """Ответ на отзыв в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_answer_feedback(
        keys["api_key"],
        feedback_id,
        text,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES SUPPLIES ENDPOINTS ========================
@app.post("/api/wb/supplies/create")
async def create_wb_supply(
    supply_name: str,
    current_user: dict = Depends(get_current_user)
):
    """Создание поставки в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_create_supply(
        keys["api_key"],
        supply_name,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/supplies/list")
async def get_wb_supplies(
    limit: int = 100,
    next: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Получение списка поставок из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_supplies(
        keys["api_key"],
        limit=limit,
        next=next,
        user_id=current_user["user_id"]
    )

@app.patch("/api/wb/supplies/close")
async def close_wb_supply(
    supply_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Закрытие поставки в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_close_supply(
        keys["api_key"],
        supply_id,
        user_id=current_user["user_id"]
    )

# ======================== WILDBERRIES PROMOCODES ENDPOINTS ========================
@app.post("/api/wb/promocodes/create")
async def create_wb_promocode(
    promocode: dict,
    current_user: dict = Depends(get_current_user)
):
    """Создание промокода в Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_create_promocode(
        keys["api_key"],
        promocode,
        user_id=current_user["user_id"]
    )

@app.get("/api/wb/promocodes/list")
async def get_wb_promocodes(
    current_user: dict = Depends(get_current_user)
):
    """Получение списка промокодов из Wildberries"""
    keys = get_api_keys(current_user["user_id"], "wildberries")
    if not keys or "api_key" not in keys:
        raise HTTPException(status_code=400, detail="Wildberries API key not configured")
    
    return await wb_get_promocodes(
        keys["api_key"],
        user_id=current_user["user_id"]
    )

@app.on_event("shutdown")
async def shutdown():
    print("🛑 Application shutdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
