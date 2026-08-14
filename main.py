import os
import json
import base64
import asyncio
import bcrypt
import jwt
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import uvicorn

# ==================== КОНФИГУРАЦИЯ ====================
SECRET_KEY = "your-super-secret-jwt-key-change-it-in-production"
DB_PATH = "chat.db"
MAX_HISTORY = 100
PING_INTERVAL = 30  # секунд

# ==================== МОДЕЛИ ДАННЫХ ====================
class RegisterRequest(BaseModel):
    username: str
    password: str
    public_key: str

class LoginRequest(BaseModel):
    username: str
    password: str

class MessageRequest(BaseModel):
    to: str
    encrypted_message: str
    nonce: str

# ==================== КРИПТОГРАФИЯ ====================
class CryptoManager:
    @staticmethod
    def generate_rsa_keys():
        private = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        return private, private.public_key()
    
    @staticmethod
    def rsa_encrypt(public_key, data: bytes) -> bytes:
        return public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    
    @staticmethod
    def rsa_decrypt(private_key, data: bytes) -> bytes:
        return private_key.decrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    
    @staticmethod
    def generate_session_key() -> bytes:
        return os.urandom(32)
    
    @staticmethod
    def aes_encrypt(key: bytes, plaintext: str) -> dict:
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
        return {
            'ciphertext': base64.b64encode(ciphertext).decode(),
            'nonce': base64.b64encode(nonce).decode()
        }
    
    @staticmethod
    def aes_decrypt(key: bytes, ciphertext_b64: str, nonce_b64: str) -> str:
        aesgcm = AESGCM(key)
        ciphertext = base64.b64decode(ciphertext_b64)
        nonce = base64.b64decode(nonce_b64)
        return aesgcm.decrypt(nonce, ciphertext, None).decode()

crypto = CryptoManager()

# ==================== БАЗА ДАННЫХ (SQLite) ====================
class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_lock = asyncio.Lock()
    
    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Включаем WAL для производительности
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            
            # Таблица пользователей
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица сессий
            await db.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    session_key BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
                )
            ''')
            
            # Таблица сообщений (с индексами для скорости)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user TEXT NOT NULL,
                    to_user TEXT NOT NULL,
                    encrypted_message TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_read BOOLEAN DEFAULT 0,
                    delivered BOOLEAN DEFAULT 0,
                    FOREIGN KEY (from_user) REFERENCES users(username),
                    FOREIGN KEY (to_user) REFERENCES users(username)
                )
            ''')
            
            # Индексы для быстрых запросов
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_messages_from_to 
                ON messages(from_user, to_user, timestamp DESC)
            ''')
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_messages_to_unread 
                ON messages(to_user, is_read, timestamp DESC)
            ''')
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp 
                ON messages(timestamp DESC)
            ''')
            
            # Таблица непрочитанных (кэш для быстрых ответов)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS unread_counts (
                    username TEXT,
                    from_user TEXT,
                    count INTEGER DEFAULT 0,
                    last_message_timestamp TIMESTAMP,
                    PRIMARY KEY (username, from_user),
                    FOREIGN KEY (username) REFERENCES users(username),
                    FOREIGN KEY (from_user) REFERENCES users(username)
                )
            ''')
            
            await db.commit()
    
    # ---------- ПОЛЬЗОВАТЕЛИ ----------
    async def create_user(self, username: str, password_hash: str, public_key: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, public_key) VALUES (?, ?, ?)",
                (username, password_hash, public_key)
            )
            await db.commit()
    
    async def get_user(self, username: str):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT username, password_hash, public_key, last_seen FROM users WHERE username = ?",
                (username,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'username': row[0],
                        'password_hash': row[1],
                        'public_key': row[2],
                        'last_seen': row[3]
                    }
                return None
    
    async def user_exists(self, username: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (username,)
            ) as cursor:
                return await cursor.fetchone() is not None
    
    async def update_last_seen(self, username: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = ?",
                (username,)
            )
            await db.commit()
    
    # ---------- СЕССИИ ----------
    async def save_session(self, session_id: str, username: str, session_key: bytes, expires_at: datetime):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO sessions (session_id, username, session_key, expires_at) VALUES (?, ?, ?, ?)",
                (session_id, username, session_key, expires_at)
            )
            await db.commit()
    
    async def get_session(self, session_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT username, session_key FROM sessions WHERE session_id = ? AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
                (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {'username': row[0], 'session_key': row[1]}
                return None
    
    async def delete_session(self, session_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            await db.commit()
    
    # ---------- СООБЩЕНИЯ ----------
    async def save_message(self, from_user: str, to_user: str, encrypted_message: str, nonce: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''INSERT INTO messages 
                   (from_user, to_user, encrypted_message, nonce, delivered) 
                   VALUES (?, ?, ?, ?, 1)''',
                (from_user, to_user, encrypted_message, nonce)
            )
            await db.commit()
            
            # Обновляем счётчик непрочитанных
            await db.execute('''
                INSERT INTO unread_counts (username, from_user, count, last_message_timestamp)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(username, from_user) DO UPDATE SET
                    count = count + 1,
                    last_message_timestamp = CURRENT_TIMESTAMP
            ''', (to_user, from_user))
            await db.commit()
            
            return cursor.lastrowid
    
    async def get_messages(self, username: str, other_user: Optional[str] = None, limit: int = MAX_HISTORY, before_id: Optional[int] = None):
        """Получить историю сообщений с пагинацией"""
        async with aiosqlite.connect(self.db_path) as db:
            if other_user:
                query = '''
                    SELECT id, from_user, to_user, encrypted_message, nonce, timestamp, is_read
                    FROM messages 
                    WHERE ((from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?))
                '''
                params = [username, other_user, other_user, username]
                
                if before_id:
                    query += " AND id < ?"
                    params.append(before_id)
                
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        {
                            'id': row[0],
                            'from': row[1],
                            'to': row[2],
                            'encrypted_message': row[3],
                            'nonce': row[4],
                            'timestamp': row[5],
                            'is_read': bool(row[6])
                        }
                        for row in rows
                    ]
            else:
                # Все сообщения пользователя (последние)
                query = '''
                    SELECT id, from_user, to_user, encrypted_message, nonce, timestamp, is_read
                    FROM messages 
                    WHERE from_user = ? OR to_user = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                '''
                params = [username, username, limit]
                
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    return [
                        {
                            'id': row[0],
                            'from': row[1],
                            'to': row[2],
                            'encrypted_message': row[3],
                            'nonce': row[4],
                            'timestamp': row[5],
                            'is_read': bool(row[6])
                        }
                        for row in rows
                    ]
    
    async def get_new_messages(self, username: str, after_id: int):
        """Получить новые сообщения после указанного ID (для Long Polling)"""
        async with aiosqlite.connect(self.db_path) as db:
            query = '''
                SELECT id, from_user, to_user, encrypted_message, nonce, timestamp, is_read
                FROM messages 
                WHERE (to_user = ? OR from_user = ?) AND id > ?
                ORDER BY id ASC
            '''
            async with db.execute(query, (username, username, after_id)) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        'id': row[0],
                        'from': row[1],
                        'to': row[2],
                        'encrypted_message': row[3],
                        'nonce': row[4],
                        'timestamp': row[5],
                        'is_read': bool(row[6])
                    }
                    for row in rows
                ]
    
    async def mark_messages_as_read(self, username: str, from_user: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE messages SET is_read = 1 WHERE to_user = ? AND from_user = ? AND is_read = 0",
                (username, from_user)
            )
            await db.commit()
            
            await db.execute(
                "DELETE FROM unread_counts WHERE username = ? AND from_user = ?",
                (username, from_user)
            )
            await db.commit()
    
    async def get_unread_counts(self, username: str):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT from_user, count FROM unread_counts WHERE username = ?",
                (username,)
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
    
    async def get_last_message_id(self, username: str):
        """Получить ID последнего сообщения пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT MAX(id) FROM messages WHERE from_user = ? OR to_user = ?",
                (username, username)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row[0] else 0

# ==================== ОСНОВНОЙ СЕРВЕР ====================
app = FastAPI(title="Real-time Chat API", version="1.0")

# CORS для веб-клиентов
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
db = Database()

# Хранилище активных WebSocket соединений
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_sessions: Dict[str, str] = {}  # websocket_id -> username
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        async with self._lock:
            if username not in self.active_connections:
                self.active_connections[username] = set()
            self.active_connections[username].add(websocket)
            self.user_sessions[id(websocket)] = username
    
    async def disconnect(self, websocket: WebSocket):
        username = self.user_sessions.pop(id(websocket), None)
        if username and username in self.active_connections:
            self.active_connections[username].discard(websocket)
            if not self.active_connections[username]:
                del self.active_connections[username]
    
    async def send_to_user(self, username: str, data: dict):
        """Отправить сообщение пользователю (если онлайн)"""
        if username in self.active_connections:
            for connection in self.active_connections[username]:
                try:
                    await connection.send_json(data)
                except:
                    pass
    
    async def broadcast_to_all(self, data: dict):
        """Отправить всем (для системных сообщений)"""
        for username in list(self.active_connections.keys()):
            await self.send_to_user(username, data)
    
    def is_online(self, username: str) -> bool:
        return username in self.active_connections and bool(self.active_connections[username])

manager = ConnectionManager()

# ---------- ИНИЦИАЛИЗАЦИЯ ----------
@app.on_event("startup")
async def startup():
    await db.init()
    print("✅ Database initialized")
    # Запускаем фоновую задачу для очистки старых сессий
    asyncio.create_task(cleanup_sessions())

async def cleanup_sessions():
    """Фоновая очистка старых сессий каждые 6 часов"""
    while True:
        await asyncio.sleep(21600)  # 6 часов
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP"
            )
            await conn.commit()
            print("🧹 Old sessions cleaned")

# ---------- REST API ----------
@app.post("/register")
async def register(req: RegisterRequest):
    if await db.user_exists(req.username):
        raise HTTPException(status_code=400, detail="User already exists")
    
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
    await db.create_user(req.username, password_hash.decode(), req.public_key)
    return {"status": "ok", "message": "User registered"}

@app.post("/login")
async def login(req: LoginRequest):
    user = await db.get_user(req.username)
    if not user or not bcrypt.checkpw(req.password.encode(), user['password_hash'].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = jwt.encode({
        'user': req.username,
        'exp': datetime.utcnow() + timedelta(days=7)
    }, SECRET_KEY, algorithm='HS256')
    
    return {"token": token, "username": req.username}

@app.post("/init_session")
async def init_session(username: str, token_data: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(token_data.credentials, SECRET_KEY, algorithms=['HS256'])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if payload['user'] != username:
        raise HTTPException(status_code=403, detail="Invalid user")
    
    user = await db.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Генерируем сессионный ключ
    session_key = crypto.generate_session_key()
    session_id = base64.b64encode(os.urandom(16)).decode()
    expires_at = datetime.utcnow() + timedelta(days=1)
    
    await db.save_session(session_id, username, session_key, expires_at)
    
    # Шифруем ключ RSA
    public_key = serialization.load_pem_public_key(
        user['public_key'].encode()
    )
    encrypted_key = crypto.rsa_encrypt(public_key, session_key)
    
    return {
        "session_id": session_id,
        "encrypted_session_key": base64.b64encode(encrypted_key).decode()
    }

@app.get("/users")
async def get_users(search: Optional[str] = None, token_data: HTTPAuthorizationCredentials = Depends(security)):
    """Получить список пользователей (для поиска)"""
    try:
        payload = jwt.decode(token_data.credentials, SECRET_KEY, algorithms=['HS256'])
    except:
        raise HTTPException(status_code=401)
    
    async with aiosqlite.connect(DB_PATH) as conn:
        if search:
            query = "SELECT username FROM users WHERE username LIKE ? LIMIT 20"
            async with conn.execute(query, (f"%{search}%",)) as cursor:
                rows = await cursor.fetchall()
        else:
            query = "SELECT username FROM users LIMIT 50"
            async with conn.execute(query) as cursor:
                rows = await cursor.fetchall()
        
        users = []
        for row in rows:
            users.append({
                'username': row[0],
                'online': manager.is_online(row[0])
            })
        return users

@app.get("/unread")
async def get_unread(username: str, token_data: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(token_data.credentials, SECRET_KEY, algorithms=['HS256'])
        if payload['user'] != username:
            raise HTTPException(status_code=403)
    except:
        raise HTTPException(status_code=401)
    
    return await db.get_unread_counts(username)

@app.get("/history")
async def get_history(
    username: str,
    with_user: Optional[str] = None,
    limit: int = 50,
    before_id: Optional[int] = None,
    token_data: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        payload = jwt.decode(token_data.credentials, SECRET_KEY, algorithms=['HS256'])
        if payload['user'] != username:
            raise HTTPException(status_code=403)
    except:
        raise HTTPException(status_code=401)
    
    messages = await db.get_messages(username, with_user, limit, before_id)
    return {"messages": messages}

# ==================== LONG POLLING (Fallback для WebSocket) ====================
@app.get("/poll")
async def long_poll(
    username: str,
    last_id: int = 0,
    timeout: int = 30,
    token_data: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Long polling endpoint для получения новых сообщений в реальном времени.
    Блокирует запрос до появления нового сообщения или таймаута.
    """
    try:
        payload = jwt.decode(token_data.credentials, SECRET_KEY, algorithms=['HS256'])
        if payload['user'] != username:
            raise HTTPException(status_code=403)
    except:
        raise HTTPException(status_code=401)
    
    # Ждём новое сообщение
    start_time = datetime.utcnow()
    check_interval = 1  # секунд
    
    while (datetime.utcnow() - start_time).total_seconds() < timeout:
        # Проверяем новые сообщения
        messages = await db.get_new_messages(username, last_id)
        if messages:
            return {
                "messages": messages,
                "last_id": messages[-1]['id'] if messages else last_id
            }
        
        # Проверяем онлайн статус собеседников (для обновления)
        await asyncio.sleep(check_interval)
    
    return {"messages": [], "last_id": last_id, "timeout": True}

# ==================== WEBSOCKET (Реальное время) ====================
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    
    try:
        # Инициализация сессии
        init_data = await websocket.receive_json()
        session_id = init_data.get('session_id')
        
        session = await db.get_session(session_id)
        if not session or session['username'] != username:
            await websocket.close(code=4001, reason="Invalid session")
            return
        
        session_key = session['session_key']
        
        # Отправляем подтверждение
        await websocket.send_json({
            "type": "connected",
            "status": "ok",
            "username": username,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Обновляем last_seen
        await db.update_last_seen(username)
        
        # Сообщаем всем, что пользователь онлайн
        await manager.broadcast_to_all({
            "type": "user_status",
            "username": username,
            "status": "online"
        })
        
        # Основной цикл обработки
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                # Отправляем ping для поддержания соединения
                await websocket.send_json({"type": "ping"})
                continue
            
            # Обработка сообщений
            if data['type'] == 'message':
                to_user = data.get('to')
                if not await db.user_exists(to_user):
                    await websocket.send_json({
                        "type": "error",
                        "error": "User not found"
                    })
                    continue
                
                try:
                    # Проверяем что сообщение можно расшифровать
                    decrypted = crypto.aes_decrypt(
                        session_key,
                        data['encrypted_message'],
                        data['nonce']
                    )
                    
                    # Сохраняем в БД
                    msg_id = await db.save_message(
                        username,
                        to_user,
                        data['encrypted_message'],
                        data['nonce']
                    )
                    
                    # Создаём объект сообщения
                    message_obj = {
                        "type": "message",
                        "id": msg_id,
                        "from": username,
                        "to": to_user,
                        "encrypted_message": data['encrypted_message'],
                        "nonce": data['nonce'],
                        "timestamp": datetime.utcnow().isoformat(),
                        "is_read": False
                    }
                    
                    # Отправляем получателю (если онлайн)
                    if manager.is_online(to_user):
                        await manager.send_to_user(to_user, message_obj)
                        # Помечаем как доставленное
                        async with aiosqlite.connect(DB_PATH) as conn:
                            await conn.execute(
                                "UPDATE messages SET delivered = 1 WHERE id = ?",
                                (msg_id,)
                            )
                            await conn.commit()
                    
                    # Отправляем подтверждение отправителю
                    await websocket.send_json({
                        "type": "delivered",
                        "message_id": msg_id,
                        "to": to_user,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Decryption failed: {str(e)}"
                    })
            
            elif data['type'] == 'mark_read':
                from_user = data.get('from_user')
                if from_user:
                    await db.mark_messages_as_read(username, from_user)
                    # Уведомляем отправителя
                    await manager.send_to_user(from_user, {
                        "type": "messages_read",
                        "by": username,
                        "from": from_user
                    })
                    await websocket.send_json({
                        "type": "marked_read",
                        "status": "ok"
                    })
            
            elif data['type'] == 'get_history':
                other_user = data.get('with_user')
                limit = data.get('limit', 50)
                before_id = data.get('before_id')
                messages = await db.get_messages(username, other_user, limit, before_id)
                await websocket.send_json({
                    "type": "history",
                    "messages": messages,
                    "has_more": len(messages) == limit
                })
            
            elif data['type'] == 'typing':
                # Уведомление о наборе текста
                to_user = data.get('to')
                if to_user and manager.is_online(to_user):
                    await manager.send_to_user(to_user, {
                        "type": "typing",
                        "from": username,
                        "is_typing": data.get('is_typing', True)
                    })
            
            elif data['type'] == 'ping':
                await websocket.send_json({"type": "pong"})
    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await manager.disconnect(websocket)
        await db.update_last_seen(username)
        
        # Сообщаем всем о выходе
        await manager.broadcast_to_all({
            "type": "user_status",
            "username": username,
            "status": "offline"
        })

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("🚀 Starting Real-time Chat Server...")
    print(f"📡 WebSocket: ws://localhost:8000/ws/{{username}}")
    print(f"🌐 API: http://localhost:8000")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        # Для продакшена раскомментируйте:
        # ssl_keyfile="key.pem",
        # ssl_certfile="cert.pem"
    )