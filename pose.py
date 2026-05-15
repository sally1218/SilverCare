import cv2
import mediapipe as mp
import time
import numpy as np
import sqlite3
from flask import Flask, Response, render_template, redirect, url_for, request, session, flash

app = Flask(__name__)
app.secret_key = "rehab_secret_key_123"

# 全域變數
latest_canvas = None
is_processing = False
latest_info = (180, 0, "UP")
last_seen_time = time.time()
save_notification = False 
current_active_user = None  # 關鍵：用全域變數替代 session 存取

# --- 2. 資料庫初始化 ---
def init_db():
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, count INTEGER, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('sally', '1234'))
    except sqlite3.IntegrityError:
        pass 
    conn.commit()
    conn.close()

init_db()

# --- 3. 分析模組 ---
class RehabAnalyzer:
    def __init__(self):
        self.counter = 0
        self.stage = "UP"
        self.current_action = "squat"

    def calculate_angle(self, a, b, c):
        a, b, c = np.array(a), np.array(b), np.array(c)
        v1, v2 = a - b, c - b
        cosine_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        return np.degrees(np.arccos(np.clip(cosine_angle, -1.0, 1.0)))

    def update(self, landmark_list):
        hip = [landmark_list[24].x, landmark_list[24].y]
        knee = [landmark_list[26].x, landmark_list[26].y]
        ankle = [landmark_list[28].x, landmark_list[28].y]
        angle = self.calculate_angle(hip, knee, ankle)
        if angle > 160:
            if self.stage == "DOWN": self.counter += 1
            self.stage = "UP"
        elif angle < 120:
            self.stage = "DOWN"
        return int(angle), self.counter, self.stage

analyzer = RehabAnalyzer()

# --- 4. 自動儲存函數 (不使用 session) ---
def auto_save(username):
    global analyzer, save_notification
    if analyzer.counter > 0 and username:
        try:
            conn = sqlite3.connect('rehab.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO records (username, action, count) VALUES (?, ?, ?)", 
                           (username, analyzer.current_action, analyzer.counter))
            conn.commit()
            conn.close()
            save_notification = True 
            print(f"--- 儲存成功: {analyzer.counter} 次 ---")
            analyzer.counter = 0 
        except Exception as e:
            print(f"資料庫錯誤: {e}")

@app.route('/get_status')
def get_status():
    global latest_info, save_notification
    res = {"count": latest_info[1], "stage": latest_info[2], "saved": save_notification}
    if save_notification: save_notification = False 
    return res

# --- 5. 路由 ---
@app.route('/')
def index():
    if 'user' not in session: return redirect(url_for('login_page'))
    username = session['user']
    # 每次進入 index 就刷新全域使用者變數，防止 Context 錯誤
    global current_active_user
    current_active_user = username
    
    conn = sqlite3.connect('rehab.db'); cursor = conn.cursor()
    cursor.execute("SELECT date(date), action, SUM(count) FROM records WHERE username=? GROUP BY date(date), action", (username,))
    rows = cursor.fetchall(); conn.close()
    
    chart_data = {}
    for r in rows:
        day, act, cnt = r
        if day not in chart_data: chart_data[day] = {'squat': 0, 'sit_to_stand': 0}
        chart_data[day][act] = cnt
    return render_template('index.html', username=username, chart_data=chart_data, stage=latest_info[2])

@app.route('/login_page')
def login_page(): return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username, password = request.form.get('username'), request.form.get('password')
    conn = sqlite3.connect('rehab.db'); cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (username,)); user = cursor.fetchone(); conn.close()
    if user and user[2] == password:
        session['user'] = username
        return redirect(url_for('index'))
    return redirect(url_for('login_page'))

@app.route('/logout')
def logout(): session.pop('user', None); return redirect(url_for('login_page'))

# --- 6. MediaPipe 核心 ---
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

def pose_result(result, output_image, timestamp):
    global latest_canvas, is_processing, latest_info, last_seen_time, current_active_user
    try:
        canvas = cv2.cvtColor(output_image.numpy_view(), cv2.COLOR_RGB2BGR)
        if result.pose_landmarks:
            last_seen_time = time.time()
            for landmark_list in result.pose_landmarks:
                latest_info = analyzer.update(landmark_list)
        else:
            if analyzer.counter > 0 and (time.time() - last_seen_time > 3):
                auto_save(current_active_user)
        angle, count, stage = latest_info
        cv2.putText(canvas, f"Count: {count}", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 3)
        cv2.putText(canvas, f"Stage: {stage}", (10, 130), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,0,0), 2)
        latest_canvas = canvas
    finally:
        is_processing = False

def gen_frames():
    global is_processing, latest_canvas
    # 獲取攝影機
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
    options = vision.PoseLandmarkerOptions(
        base_options=base_options, 
        running_mode=vision.RunningMode.LIVE_STREAM, 
        result_callback=pose_result
    )
    
    with vision.PoseLandmarker.create_from_options(options) as pose_landmarks:
        while cap.isOpened():
            success, frame = cap.read()
            if not success: break
            
            if not is_processing:
                try:
                    is_processing = True
                    img_mp = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    pose_landmarks.detect_async(img_mp, int(time.time() * 1000))
                except:
                    is_processing = False
            
            # 優先顯示最新偵測畫面，若無則顯示原始畫面
            display_frame = latest_canvas if latest_canvas is not None else frame
            _, buffer = cv2.imencode('.jpg', display_frame)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
    
    cap.release()

@app.route('/mjpeg')
def mjpeg():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)