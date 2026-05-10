import cv2
import mediapipe as mp
import time
import numpy as np
import os
import sqlite3
from flask import Flask, Response, render_template, redirect, url_for, request, session, flash

# 1. 初始化 Flask
app = Flask(__name__)
app.secret_key = "rehab_secret_key_123"  # 用於加密 session

# --- 2. 資料庫初始化 ---
def init_db():
    # 這裡會建立 rehab.db 檔案，如果之前報錯 no such table，請先刪除舊的 db 檔案再執行
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    # 建立使用者表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    # 建立運動紀錄表 (存放歷史紀錄)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            count INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 預設建立一個測試帳號
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('sally', '1234'))
    except sqlite3.IntegrityError:
        pass 
    conn.commit()
    conn.close()

init_db()

# --- 3. 深蹲分析模組 ---
class SquatAnalyzer:
    def __init__(self):
        self.counter = 0
        self.stage = "UP"
        self.min_angle = 180
        self.last_rom = 0

    def calculate_angle(self, a, b, c):
        a, b, c = np.array(a), np.array(b), np.array(c)
        v1, v2 = a - b, c - b
        cosine_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        return np.degrees(np.arccos(np.clip(cosine_angle, -1.0, 1.0)))

    def update(self, landmark_list):
        # 取得髖部(24)、膝蓋(26)、腳踝(28) 座標
        hip = [landmark_list[24].x, landmark_list[24].y]
        knee = [landmark_list[26].x, landmark_list[26].y]
        ankle = [landmark_list[28].x, landmark_list[28].y]
        angle = self.calculate_angle(hip, knee, ankle)
        
        if angle > 160:
            if self.stage == "DOWN":
                self.last_rom = 180 - self.min_angle
                self.counter += 1
                self.min_angle = 180
            self.stage = "UP"
        if angle < 120:
            self.stage = "DOWN"
            if angle < self.min_angle:
                self.min_angle = angle
        return int(angle), self.counter, self.stage, int(self.last_rom)

# 全域變數
analyzer = SquatAnalyzer()
latest_canvas = None
is_processing = False
latest_info = (180, 0, "UP", 0)

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login_page'))
    
    username = session['user']
    
    # 從資料庫抓取該使用者的歷史總運動次數
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(count) FROM records WHERE username=?", (username,))
    row = cursor.fetchone()
    total_count = row[0] if row[0] is not None else 0
    conn.close()
    
    # 將數據傳給前端 index.html
    return render_template('index.html', 
                           username=username, 
                           total_count=total_count, 
                           stage=latest_info[2])

@app.route('/login_page')
def login_page():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    #先檢查帳號是否存在
    cursor.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cursor.fetchone()
    conn.close()
    if user is None:
        flash("您還沒有註冊帳號喔！請先點擊下方註冊。", "danger")
        return redirect(url_for('login_page'))
    
    if user[2] != password: # 假設密碼在第三欄
        # 情況 B：帳號存在但密碼錯了
        flash("密碼輸入錯誤，請再試一次。", "warning")
        return redirect(url_for('login_page'))
    
    # 登入成功
    session['user'] = username
    return redirect(url_for('index'))

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
    
    try:
        conn = sqlite3.connect('rehab.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        conn.commit()
        conn.close()
        
        # 這裡就是關鍵：顯示成功訊息，但導向「註冊頁面」
        flash("註冊成功！現在您可以點擊下方返回登入了。", "success")
        return redirect(url_for('register_page'))
        
    except sqlite3.IntegrityError:
        flash("這個帳號已經有人用了，換一個試試看？", "danger")
        return redirect(url_for('register_page'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))

@app.route('/save_exercise', methods=['POST'])
def save_exercise():
    if 'user' in session:
        username = session['user']
        global analyzer
        final_count = analyzer.counter # 取得當前偵測到的次數
        
        if final_count > 0:
            conn = sqlite3.connect('rehab.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO records (username, count) VALUES (?, ?)", (username, final_count))
            conn.commit()
            conn.close()
            analyzer.counter = 0 # 存入資料庫後重置
    return redirect(url_for('index'))

# --- 5. MediaPipe 影像處理 ---
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

def pose_result(result, output_image, timestamp):
    global latest_canvas, is_processing, latest_info
    try:
        canvas = cv2.cvtColor(output_image.numpy_view(), cv2.COLOR_RGB2BGR)
        if result.pose_landmarks:
            for landmark_list in result.pose_landmarks: 
                latest_info = analyzer.update(landmark_list)
        
        angle, count, stage, rom = latest_info
        # 在畫面上即時繪製資訊
        cv2.putText(canvas, f"Count: {count}", (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
        cv2.putText(canvas, f"Stage: {stage}", (10, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
        latest_canvas = canvas
    except Exception as e:
        print(f"Error in pose_result: {e}")
    finally:
        is_processing = False

def gen_frames():
    global is_processing, latest_canvas
    cap = cv2.VideoCapture(0)
    
    # 請確保 pose_landmarker.task 在同一個資料夾
    base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.LIVE_STREAM,
        result_callback=pose_result,
        num_poses=1
    )

    with vision.PoseLandmarker.create_from_options(options) as pose_landmarks:
        while cap.isOpened():
            success, frame = cap.read()
            if not success: break
            
            if not is_processing:
                is_processing = True
                img_mp = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                pose_landmarks.detect_async(img_mp, int(time.time() * 1000))

            display_frame = latest_canvas if latest_canvas is not None else frame
            _, buffer = cv2.imencode('.jpg', display_frame)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/mjpeg')
def mjpeg():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # 啟動系統
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)