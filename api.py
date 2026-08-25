import os
import sqlite3
import hashlib
import secrets
import shutil
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Depends, UploadFile, File, Form, status
from pydantic import BaseModel
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = str(BASE_DIR / "messenger.db")
AVATARS_DIR = BASE_DIR / "static" / "avatars"
AVATARS_DIR.mkdir(parents=True, exist_ok=True)

DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY")
if not DATA_ENCRYPTION_KEY:
    raise RuntimeError("Не задан DATA_ENCRYPTION_KEY в .env")

CIPHER_SUITE = Fernet(DATA_ENCRYPTION_KEY.encode())

api_router = APIRouter(prefix="/api/v1")

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                token TEXT,
                display_name TEXT,
                avatar_url TEXT DEFAULT '',
                name_color TEXT DEFAULT '#5288c1'
            )
        """)
        # Миграция колонок на случай, если таблица уже создана
        for col, col_type, default in [
            ("display_name", "TEXT", "''"),
            ("avatar_url", "TEXT", "''"),
            ("name_color", "TEXT", "'#5288c1'")
        ]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

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

init_db()

def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return pwd_hash, salt

def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    token = authorization.split(" ")[1]
    
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE token = ?", (token,))
        user = cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    return user[0]

# --- Pydantic модели ---
class AuthRequest(BaseModel):
    username: str
    password: str

class SendMessageRequest(BaseModel):
    chat_id: str
    text: str

class UserProfileResponse(BaseModel):
    username: str
    display_name: str
    avatar_url: str
    name_color: str

class MessageResponse(BaseModel):
    id: int
    sender_id: str
    sender_display_name: str
    sender_avatar_url: str
    sender_name_color: str
    chat_id: str
    text: str
    created_at: str

# --- Auth ---
@api_router.post("/auth/register")
def register(data: AuthRequest):
    username = data.username.strip().lower()
    if len(username) < 3 or len(data.password) < 4:
        raise HTTPException(status_code=400, detail="Логин от 3 знаков, пароль от 4")
    
    pwd_hash, salt = hash_password(data.password)
    token = secrets.token_hex(32)
    display_name = data.username.strip()
    
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, salt, token, display_name) VALUES (?, ?, ?, ?, ?)",
                (username, pwd_hash, salt, token, display_name)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Пользователь уже существует")
            
    return {"status": "ok", "token": token, "username": username}

@api_router.post("/auth/login")
def login(data: AuthRequest):
    username = data.username.strip().lower()
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Неверный логин или пароль")
        
        pwd_hash, salt = row
        test_hash, _ = hash_password(data.password, salt)
        if test_hash != pwd_hash:
            raise HTTPException(status_code=400, detail="Неверный логин или пароль")
        
        token = secrets.token_hex(32)
        cursor.execute("UPDATE users SET token = ? WHERE username = ?", (token, username))
        conn.commit()
        
    return {"status": "ok", "token": token, "username": username}

# --- Профиль пользователя ---
@api_router.get("/users/me", response_model=UserProfileResponse)
def get_my_profile(current_user: str = Depends(get_current_user)):
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, display_name, avatar_url, name_color FROM users WHERE username = ?", (current_user,))
        row = cursor.fetchone()
    return UserProfileResponse(username=row[0], display_name=row[1] or row[0], avatar_url=row[2] or "", name_color=row[3] or "#5288c1")

@api_router.get("/users/{username}", response_model=UserProfileResponse)
def get_user_profile(username: str, current_user: str = Depends(get_current_user)):
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, display_name, avatar_url, name_color FROM users WHERE username = ?", (username.lower(),))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return UserProfileResponse(username=row[0], display_name=row[1] or row[0], avatar_url=row[2] or "", name_color=row[3] or "#5288c1")

@api_router.post("/users/update_profile")
def update_profile(
    display_name: str = Form(...),
    name_color: str = Form(...),
    avatar: Optional[UploadFile] = File(None),
    current_user: str = Depends(get_current_user)
):
    avatar_url = None
    if avatar and avatar.filename:
        ext = Path(avatar.filename).suffix.lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            raise HTTPException(status_code=400, detail="Разрешены только изображения")
        
        filename = f"{current_user}_{secrets.token_hex(6)}{ext}"
        file_path = AVATARS_DIR / filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(avatar.file, buffer)
        avatar_url = f"/static/avatars/{filename}"

    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        if avatar_url:
            cursor.execute(
                "UPDATE users SET display_name = ?, name_color = ?, avatar_url = ? WHERE username = ?",
                (display_name.strip(), name_color, avatar_url, current_user)
            )
        else:
            cursor.execute(
                "UPDATE users SET display_name = ?, name_color = ? WHERE username = ?",
                (display_name.strip(), name_color, current_user)
            )
        conn.commit()

    return {"status": "success", "avatar_url": avatar_url}

# --- Сообщения ---
@api_router.post("/messages/send", status_code=status.HTTP_201_CREATED)
def send_message(payload: SendMessageRequest, current_user: str = Depends(get_current_user)):
    encrypted_text = CIPHER_SUITE.encrypt(payload.text.encode("utf-8")).decode("utf-8")
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (sender_id, chat_id, encrypted_payload) VALUES (?, ?, ?)",
            (current_user, payload.chat_id, encrypted_text)
        )
        conn.commit()
        msg_id = cursor.lastrowid
    return {"status": "success", "message_id": msg_id}

@api_router.get("/messages/{chat_id}", response_model=List[MessageResponse])
def get_chat_messages(
    chat_id: str, 
    limit: int = 30, 
    before_id: Optional[int] = None, 
    after_id: Optional[int] = None,
    current_user: str = Depends(get_current_user)
):
    with sqlite3.connect(DB_NAME, timeout=10) as conn:
        cursor = conn.cursor()
        
        base_query = """
            SELECT m.id, m.sender_id, u.display_name, u.avatar_url, u.name_color, m.chat_id, m.encrypted_payload, m.created_at
            FROM messages m
            LEFT JOIN users u ON m.sender_id = u.username
        """
        
        if after_id is not None:
            cursor.execute(
                f"{base_query} WHERE m.chat_id = ? AND m.id > ? ORDER BY m.id ASC",
                (chat_id, after_id)
            )
        elif before_id is not None:
            cursor.execute(
                f"""
                SELECT * FROM (
                    {base_query} WHERE m.chat_id = ? AND m.id < ? ORDER BY m.id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (chat_id, before_id, limit)
            )
        else:
            cursor.execute(
                f"""
                SELECT * FROM (
                    {base_query} WHERE m.chat_id = ? ORDER BY m.id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (chat_id, limit)
            )
            
        rows = cursor.fetchall()

    decrypted = []
    for row in rows:
        msg_id, sender, d_name, av_url, color, c_id, enc_payload, created_at = row
        try:
            raw_text = CIPHER_SUITE.decrypt(enc_payload.encode("utf-8")).decode("utf-8")
        except Exception:
            raw_text = "[Ошибка расшифровки]"
            
        decrypted.append(
            MessageResponse(
                id=msg_id,
                sender_id=sender,
                sender_display_name=d_name or sender,
                sender_avatar_url=av_url or "",
                sender_name_color=color or "#5288c1",
                chat_id=c_id,
                text=raw_text,
                created_at=str(created_at)
            )
        )
        
    return decrypted
