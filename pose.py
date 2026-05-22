import cv2
import mediapipe as mp
import time
import numpy as np
import sqlite3
import json
from flask import Flask, Response, render_template, redirect, url_for, request, session, flash

app = Flask(__name__)
app.secret_key = "1234"

#全域變數設定
latest_canvas = None #儲存最新的畫面
is_processing = False
latest_info = (180, 0, "DOWN") #記錄最新狀態
last_seen_time = time.time() #記錄最後一次偵測到人體的時間
save_notification = False 
current_active_user = None #記錄目前登入的使用者帳號

#資料庫初始化
def init_db():
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    #建立使用者資料表
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    username TEXT UNIQUE, 
    password TEXT)''')
    #建立復健紀錄資料表
    cursor.execute('''CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        username TEXT, 
        action TEXT, 
        count INTEGER, 
        duration_data TEXT, 
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    try:
        #測試帳號
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('sally', '1234'))
    except sqlite3.IntegrityError:
        pass 
    conn.commit()
    conn.close()
init_db()

#動作分析模組
class RehabAnalyzer:
    def __init__(self):
        self.counter = 0 #紀錄次數
        self.stage = "DOWN" #紀錄狀態            
        self.current_action = "leg_raise" 
        self.action_start_time = None  # 記錄開始抬腿的時間點
        self.durations = [] #儲存當次運動中，每一下抬腿分別維持了幾秒            
    #計算角度
    def calculate_angle(self, a, b, c):
        a, b, c = np.array(a), np.array(b), np.array(c)
        v1, v2 = a - b, c - b
        cosine_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        return np.degrees(np.arccos(np.clip(cosine_angle, -1.0, 1.0)))

    def update(self, landmark_list):
        hip = [landmark_list[24].x, landmark_list[24].y] #臀部
        knee = [landmark_list[26].x, landmark_list[26].y] #膝蓋
        ankle = [landmark_list[28].x, landmark_list[28].y] #腳踝
        
        angle = self.calculate_angle(hip, knee, ankle) #計算當前膝蓋角度
        
        if angle > 160 and self.stage == "DOWN":
            self.stage = "UP"
            self.action_start_time = time.time() #啟動計時器
            
        elif angle < 130 and self.stage == "UP":
            self.stage = "DOWN"
            self.counter += 1
            if self.action_start_time is not None:
                hold_duration = round(time.time() - self.action_start_time, 2) #計算這一下撐了幾秒
                self.durations.append(hold_duration)  
        return int(angle), self.counter, self.stage 

analyzer = RehabAnalyzer()

#自動儲存
def auto_save(username):
    global analyzer, save_notification
    #只有當次做超過0次，且使用者有登入時才儲存
    if analyzer.counter > 0 and username:
        try:
            json_durations = json.dumps(analyzer.durations) 
            
            conn = sqlite3.connect('rehab.db')
            cursor = conn.cursor()
            # 將本次復健數據寫入資料庫
            cursor.execute("INSERT INTO records (username, action, count, duration_data) VALUES (?, ?, ?, ?)", 
                           (username, analyzer.current_action, analyzer.counter, json_durations))
            conn.commit()
            conn.close()
            
            print(f"--- 儲存成功: {analyzer.counter} 次，疲勞時間數據: {json_durations} ---")
            save_notification = True 
            
            analyzer.counter = 0 #歸零次數
            analyzer.durations = [] #清空秒數
        except Exception as e:
            print(f"資料庫錯誤: {e}")

@app.route('/get_status')
def get_status():
    global latest_info, save_notification
    res = {"count": latest_info[1], "stage": latest_info[2], "saved": save_notification}
    if save_notification: save_notification = False 
    return res

@app.route('/')
def index():
    if 'user' not in session: return redirect(url_for('login_page'))
    username = session['user']
    global current_active_user
    current_active_user = username 
    
    conn = sqlite3.connect('rehab.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%m-%d %H:%M', date,'localtime'), action, count, duration_data 
        FROM records 
        WHERE username=? 
        ORDER BY date ASC
    """, (username,))
    rows = cursor.fetchall() 
    conn.close()
    
    last_fatigue_data = [] 
    time_labels = []
    count_values = []

    for r in rows:
        time_str, act, cnt, dur_str = r
        time_labels.append(time_str) #X軸顯示時間
        count_values.append(cnt) #Y軸顯示次數
        
        if dur_str:
            try: last_fatigue_data = json.loads(dur_str)
            except: pass               
    return render_template('index.html', 
                           username=username, 
                           time_labels=time_labels, 
                           count_values=count_values, 
                           last_fatigue_data=last_fatigue_data)

@app.route('/login_page')
def login_page(): return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username, password = request.form.get('username'), request.form.get('password')
    conn = sqlite3.connect('rehab.db'); cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user and user[2] == password:
        session['user'] = username
        return redirect(url_for('index'))
    return redirect(url_for('login_page'))

@app.route('/logout')
def logout(): session.pop('user', None); return redirect(url_for('login_page'))

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
        latest_canvas = canvas
    finally:
        is_processing = False

def gen_frames():
    global is_processing, latest_canvas
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
            
            display_frame = latest_canvas if latest_canvas is not None else frame
            _, buffer = cv2.imencode('.jpg', display_frame)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
    
    cap.release()

@app.route('/mjpeg')
def mjpeg():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)