import os
import sqlite3
import hashlib
import secrets
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Depends, status
from pydantic import BaseModel
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY")
if not DATA_ENCRYPTION_KEY:
    raise RuntimeError("Не задан DATA_ENCRYPTION_KEY в .env")

CIPHER_SUITE = Fernet(DATA_ENCRYPTION_KEY.encode())
DB_NAME = "messenger.db"

api_router = APIRouter(prefix="/api/v1")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            token TEXT
        )
    """)
    # Таблица сообщений
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            encrypted_payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Хеширование паролей ---
def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return pwd_hash, salt

# --- Проверка сессии ---
def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    token = authorization.split(" ")[1]
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE token = ?", (token,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    return user[0]

# --- Модели ---
class AuthRequest(BaseModel):
    username: str
    password: str

class SendMessageRequest(BaseModel):
    chat_id: str
    text: str

class MessageResponse(BaseModel):
    id: int
    sender_id: str
    chat_id: str
    text: str
    created_at: str

# --- Эндпоинты аутентификации ---
@api_router.post("/auth/register")
def register(data: AuthRequest):
    username = data.username.strip().lower()
    if len(username) < 3 or len(data.password) < 4:
        raise HTTPException(status_code=400, detail="Логин от 3 знаков, пароль от 4")
    
    pwd_hash, salt = hash_password(data.password)
    token = secrets.token_hex(32)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, token) VALUES (?, ?, ?, ?)",
            (username, pwd_hash, salt, token)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    conn.close()
    return {"status": "ok", "token": token, "username": username}

@api_router.post("/auth/login")
def login(data: AuthRequest):
    username = data.username.strip().lower()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    
    pwd_hash, salt = row
    test_hash, _ = hash_password(data.password, salt)
    if test_hash != pwd_hash:
        conn.close()
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    
    token = secrets.token_hex(32)
    cursor.execute("UPDATE users SET token = ? WHERE username = ?", (token, username))
    conn.commit()
    conn.close()
    return {"status": "ok", "token": token, "username": username}

# --- Эндпоинты сообщений ---
@api_router.post("/messages/send", status_code=status.HTTP_201_CREATED)
def send_message(payload: SendMessageRequest, current_user: str = Depends(get_current_user)):
    encrypted_text = CIPHER_SUITE.encrypt(payload.text.encode("utf-8")).decode("utf-8")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (sender_id, chat_id, encrypted_payload) VALUES (?, ?, ?)",
        (current_user, payload.chat_id, encrypted_text)
    )
    conn.commit()
    msg_id = cursor.lastrowid
    conn.close()
    return {"status": "success", "message_id": msg_id}

@api_router.get("/messages/{chat_id}", response_model=List[MessageResponse])
def get_chat_messages(chat_id: str, limit: int = 50, current_user: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, sender_id, chat_id, encrypted_payload, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
        (chat_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    
    decrypted = []
    for row in rows:
        msg_id, sender, c_id, enc_payload, created_at = row
        try:
            raw_text = CIPHER_SUITE.decrypt(enc_payload.encode("utf-8")).decode("utf-8")
        except Exception:
            raw_text = "[Ошибка расшифровки]"
        decrypted.append(MessageResponse(id=msg_id, sender_id=sender, chat_id=c_id, text=raw_text, created_at=str(created_at)))
    return decrypted
