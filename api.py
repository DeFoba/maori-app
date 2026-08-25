import os
import sqlite3
from typing import List
from fastapi import APIRouter, Header, HTTPException, Depends, status
from pydantic import BaseModel
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# Ключи и настройки
DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY")
if not DATA_ENCRYPTION_KEY:
    raise RuntimeError("Не задан DATA_ENCRYPTION_KEY в .env")

CIPHER_SUITE = Fernet(DATA_ENCRYPTION_KEY.encode())
AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "tg-secret-access-token-2026")
DB_NAME = "messenger.db"

# Создаем роутер вместо app
api_router = APIRouter(prefix="/api/v1")

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
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

# Проверка токена
def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует или некорректен заголовок Authorization"
        )
    token = authorization.split(" ")[1]
    if token != AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный токен доступа"
        )
    return token

# Схемы
class SendMessageRequest(BaseModel):
    sender_id: str
    chat_id: str
    text: str

class MessageResponse(BaseModel):
    id: int
    sender_id: str
    chat_id: str
    text: str
    created_at: str

# Эндпоинты
@api_router.post("/messages/send", status_code=status.HTTP_201_CREATED)
def send_message(payload: SendMessageRequest, token: str = Depends(verify_token)):
    encrypted_text = CIPHER_SUITE.encrypt(payload.text.encode("utf-8")).decode("utf-8")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (sender_id, chat_id, encrypted_payload) VALUES (?, ?, ?)",
        (payload.sender_id, payload.chat_id, encrypted_text)
    )
    conn.commit()
    msg_id = cursor.lastrowid
    conn.close()
    
    return {"status": "success", "message_id": msg_id}

@api_router.get("/messages/{chat_id}", response_model=List[MessageResponse])
def get_chat_messages(chat_id: str, limit: int = 50, token: str = Depends(verify_token)):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, sender_id, chat_id, encrypted_payload, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
        (chat_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    
    decrypted_messages = []
    for row in rows:
        msg_id, sender, c_id, enc_payload, created_at = row
        try:
            raw_text = CIPHER_SUITE.decrypt(enc_payload.encode("utf-8")).decode("utf-8")
        except Exception:
            raw_text = "[Ошибка расшифровки]"
            
        decrypted_messages.append(
            MessageResponse(
                id=msg_id,
                sender_id=sender,
                chat_id=c_id,
                text=raw_text,
                created_at=str(created_at)
            )
        )
        
    return decrypted_messages
