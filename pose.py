import cv2
import mediapipe as mp
import time
import numpy as np
from flask import Flask, Response
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from mediapipe.framework.formats import landmark_pb2

app = Flask(__name__)

#深蹲模組
class SquatAnalyzer:
    def __init__(self):
        self.counter = 0
        self.stage = "UP"
        self.min_angle = 180
        self.last_rom = 0

    def calculate_angle(self, a, b, c):
        a, b, c = np.array(a), np.array(b), np.array(c)
        v1 = a - b
        v2 = c - b
        cosine_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        return np.degrees(np.arccos(np.clip(cosine_angle, -1.0, 1.0)))

    def update(self, landmark_list):
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

analyzer = SquatAnalyzer()
latest_canvas = None
is_processing = False
latest_info = (180, 0, "UP", 0)

mp_drawing = mp.solutions.drawing_utils
mp_pose = mp.solutions.pose
custom_dot_style = mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=3)
custom_link_style = mp_drawing.DrawingSpec(color=(0, 255, 255), thickness=2)

def pose_result(result, output_image, timestamp):
    global latest_canvas, is_processing, latest_info
    try:
        canvas = cv2.cvtColor(output_image.numpy_view(), cv2.COLOR_RGB2BGR)
        if result.pose_landmarks:
            for landmark_list in result.pose_landmarks: 
                latest_info = analyzer.update(landmark_list)
                pose_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
                pose_landmarks_proto.landmark.extend([
                    landmark_pb2.NormalizedLandmark(x=lm.x, y=lm.y, z=lm.z) for lm in landmark_list
                ])
                mp_drawing.draw_landmarks(
                    canvas, pose_landmarks_proto, mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=custom_dot_style,
                    connection_drawing_spec=custom_link_style
                )
        angle, count, stage, rom = latest_info
        cv2.putText(canvas, f"Angle: {angle}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(canvas, f"Count: {count}", (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(canvas, f"ROM: {rom}", (10, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
        latest_canvas = canvas
    except Exception as e:
        print(f"Error: {e}")
    finally:
        is_processing = False

def gen_frames():
    global is_processing, latest_canvas
    cap = cv2.VideoCapture(0)
    
    # 建立 MediaPipe 偵測器
    base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.LIVE_STREAM,
        result_callback=pose_result,
        num_poses=1,
        min_pose_detection_confidence=0.5
    )

    with vision.PoseLandmarker.create_from_options(options) as pose_landmarks:
        while cap.isOpened():
            success, frame = cap.read()
            if not success: break
            
            frame = cv2.resize(frame, (640, 480))
            
            # 驅動 AI 偵測
            if not is_processing:
                is_processing = True
                img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img_mp = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
                timestamp_ms = int(time.time() * 1000)
                pose_landmarks.detect_async(img_mp, timestamp_ms)

            # 決定要顯示的畫面 (優先顯示有骨架的)
            display_frame = latest_canvas if latest_canvas is not None else frame
            
            # 編碼為 JPG 並串流
            ret, buffer = cv2.imencode('.jpg', display_frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/mjpeg')
def mjpeg():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return '<html><body><h1>Rehab System - Web</h1><img src="/mjpeg" width="640"></body></html>'

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, threaded=True)