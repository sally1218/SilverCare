import os
import sqlite3
import json
import psycopg2
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, session, flash
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

sid_to_room = {}   # { socket_id: room_name }
sid_to_user = {}   # { socket_id: username }

Database_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """ 自動判斷：有雲端網址就連 Supabase，沒有就連本地 SQLite """
    if Database_URL:
        return psycopg2.connect(Database_URL, sslmode='require')
    else:
        return sqlite3.connect('rehab.db')

# 資料庫初始化
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    #建立使用者資料表
    if isinstance(conn, sqlite3.Connection):
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            count INTEGER,
            duration_data TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    else:
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS records (
            id SERIAL PRIMARY KEY,
            username TEXT,
            action TEXT,
            count INTEGER,
            duration_data TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    try:
        p_mark = "?" if isinstance(conn, sqlite3.Connection) else "%s"
        cursor.execute(f"INSERT INTO users (username, password) VALUES ({p_mark}, {p_mark})", ('sally', '1234'))
        conn.commit()
    except Exception:
        if not isinstance(conn, sqlite3.Connection):
            conn.rollback() 
            
    conn.close()

init_db()
@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login_page'))
    username = session['user']

    conn = get_db_connection()
    cursor = conn.cursor()
    # 讀取復健紀錄
    p_mark = "?" if isinstance(conn, sqlite3.Connection) else "%s"
    cursor.execute(f"""
        SELECT strftime('%m-%d %H:%M', date,'localtime'), action, count, duration_data
        FROM records
        WHERE username={p_mark}
        ORDER BY date ASC
    """ if isinstance(conn, sqlite3.Connection) else f"""
        SELECT to_char(date, 'MM-DD HH24:MI'), action, count, duration_data
        FROM records
        WHERE username={p_mark}
        ORDER BY date ASC
    """, (username,))
    
    rows = cursor.fetchall()
    conn.close()

    time_labels = []
    count_values = []
    last_fatigue_data = []

    for r in rows:
        time_str, act, cnt, dur_str = r
        time_labels.append(time_str)
        count_values.append(cnt)
        if dur_str:
            try:
                last_fatigue_data = json.loads(dur_str)
            except Exception:
                pass

    return render_template('index.html',
                            username=username,
                            time_labels=time_labels,
                            count_values=count_values,
                            duration_values=last_fatigue_data,
                            last_fatigue_data=last_fatigue_data)

@app.route('/login_page')
def login_page():
    return render_template('login.html')


@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    conn = get_db_connection()
    cursor = conn.cursor()
    p_mark = "?" if isinstance(conn, sqlite3.Connection) else "%s"
    cursor.execute(f"SELECT * FROM users WHERE username={p_mark}", (username,))
    user = cursor.fetchone()
    conn.close()

    if user and user[2] == password:
        session['user'] = username
        return redirect(url_for('index'))
    else:
        flash('帳號或密碼錯誤，請再確認一次!', 'error')
        return redirect(url_for('login_page'))


@app.route('/register_page')
def register_page():
    return render_template('register.html')


@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    if not username or not password:
        flash("帳號與密碼不能為空喔！", "warning")
        return redirect(url_for('register_page'))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        p_mark = "?" if isinstance(conn, sqlite3.Connection) else "%s"
        cursor.execute(f"INSERT INTO users (username, password) VALUES ({p_mark}, {p_mark})", (username, password))
        conn.commit()
        conn.close()
        flash("註冊成功！現在您可以點擊下方返回登入了。", "success")
        return redirect(url_for('register_page'))
    except Exception as e:
        if not isinstance(conn, sqlite3.Connection):
            conn.rollback()
        conn.close()
        flash("這個帳號已經有人用了，換一個試試看？", "danger")
        return redirect(url_for('register_page'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))

@socketio.on('connect')
def handle_connect():
    print(f"【系統】新連線建立: sid={request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    room = sid_to_room.pop(sid, None)
    username = sid_to_user.pop(sid, None)
    if room:
        # 通知房間內其他人，這個玩家已離線
        emit('opponent_left', room=room, include_self=False)
        print(f"【系統】使用者 [{username}] (sid={sid}) 已斷線，離開房間: {room}")


# 創建房間
@socketio.on('create_room')
def handle_create_room(data):
    room = str(data.get('room', '')).strip()
    if not room:
        return

    sid = request.sid
    join_room(room)
    sid_to_room[sid] = room
    sid_to_user[sid] = session.get('user')

    print(f"【系統】[{session.get('user')}] 創建並進入房間: {room} (sid={sid})")
    emit('room_created', {'room': room}, to=sid)


@socketio.on('join_room')
def handle_join_room(data):
    room = str(data.get('room', '')).strip()
    if not room:
        return

    sid = request.sid
    join_room(room)
    sid_to_room[sid] = room
    sid_to_user[sid] = session.get('user')

    print(f"【系統】[{session.get('user')}] 加入房間: {room} (sid={sid})")

    # 通知自己加入成功
    emit('room_joined', {'room': room}, to=sid)
    # 通知房間內其他人（房主），有對手加入了，可以開始 WebRTC 連線
    emit('opponent_joined', {'room': room}, room=room, include_self=False)


@socketio.on('signal')
def handle_signal(data):
    room = data.get('room')
    if not room:
        return
    # data 內容預期: { room, type: 'offer'|'answer'|'candidate', sdp / candidate }
    emit('signal', data, room=room, include_self=False)


# 接收前端算好的分數與狀態，並同步給房間內的對手
@socketio.on('sync_score_status')
def handle_sync_score_status(data):
    room = data.get('room')
    if not room:
        return
    emit('update_opponent_view', {
        'score': data.get('score'),
        'stage': data.get('stage')
    }, room=room, include_self=False)


# 儲存復健紀錄
@socketio.on('save_rehab_record')
def handle_save_record(data):
    username = session.get('user')
    if not username or not data:
        return

    count = data.get('count', 0)
    durations = data.get('durations', [])

    if count > 0:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            json_durations = json.dumps(durations)
            # 使用伺服器本地時間（避免 SQLite CURRENT_TIMESTAMP 使用 UTC 時區）
            now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            p_mark = "?" if isinstance(conn, sqlite3.Connection) else "%s"
            cursor.execute(
                f"INSERT INTO records (username, action, count, duration_data, date) VALUES ({p_mark}, {p_mark}, {p_mark}, {p_mark}, {p_mark})",
                (username, 'leg_raise', count, json_durations, now_local)
            )
            conn.commit()
            print(f"--- 雲端/本地資料庫存檔成功： [{username}] 完成直抬腿 {count} 次 ---")
            emit('response_save_success', {'saved': True})
        except Exception as e:
            if not isinstance(conn, sqlite3.Connection):
                conn.rollback()
            print(f"❌ 資料庫寫入失敗: {e}")
        finally:
            conn.close()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)