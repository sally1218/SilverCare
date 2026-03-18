import cv2
import mediapipe as mp
import time
import numpy as np 
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from mediapipe.framework.formats import landmark_pb2

#深蹲模組
class SquatAnalyzer:
    def __init__(self): #初始化
        self.counter = 0        # 深蹲次數
        self.stage = "UP"       # 狀態：UP(預設)
        self.min_angle = 180    # 紀錄單次深蹲中最深的夾角
        self.last_rom = 0       # 論文核心：活動度 (Range of Motion)

    def calculate_angle(self, a, b, c): #角度計算
        a, b, c = np.array(a), np.array(b), np.array(c)
        v1 = a - b # 膝蓋到髖部向量
        v2 = c - b # 膝蓋到腳踝向量
        cosine_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        return np.degrees(np.arccos(np.clip(cosine_angle, -1.0, 1.0)))

    def update(self, landmark_list):
        # 取得右半身關鍵點 
        hip = [landmark_list[24].x, landmark_list[24].y]
        knee = [landmark_list[26].x, landmark_list[26].y]
        ankle = [landmark_list[28].x, landmark_list[28].y]
        
        angle = self.calculate_angle(hip, knee, ankle)
        if angle > 160: 
            if self.stage == "DOWN": # 從蹲下回到站立
                self.last_rom = 180 - self.min_angle # 計算 ROM
                self.counter += 1
                self.min_angle = 180 # 重置，準備下一次
            self.stage = "UP"
        
        if angle < 120: # 進入下蹲狀態
            self.stage = "DOWN"
            if angle < self.min_angle:
                self.min_angle = angle # 持續記錄最低點
                
        return int(angle), self.counter, self.stage, int(self.last_rom)

analyzer = SquatAnalyzer() # 開啟分析器
latest_canvas = None
is_processing = False
latest_info = (180, 0, "UP", 0) # 暫存 (角度, 次數, 狀態, ROM)

mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
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
                # 繪製骨架
                pose_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
                pose_landmarks_proto.landmark.extend([ 
                    landmark_pb2.NormalizedLandmark(x=lm.x, y=lm.y, z=lm.z) 
                    for lm in landmark_list
                ])         
                mp_drawing.draw_landmarks(
                    canvas, pose_landmarks_proto, mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec = custom_dot_style, 
                    connection_drawing_spec = custom_link_style
                )       
        # 把分析結果畫在畫面上
        angle, count, stage, rom = latest_info
        cv2.putText(canvas, f"Angle: {angle}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(canvas, f"Count: {count}", (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(canvas, f"ROM: {rom}", (10, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)

        latest_canvas = canvas
        
    except Exception as e:
        print(f"Error in callback: {e}")
    finally:
        is_processing = False

base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM,
    result_callback= pose_result,
    num_poses = 1,
    min_pose_detection_confidence=0.5
)

with vision.PoseLandmarker.create_from_options(options) as pose_landmarks:
    cap = cv2.VideoCapture(0)
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        frame = cv2.resize(frame, (640, 480))
     
        if not is_processing:
            is_processing = True
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=img)
            timestamp_ms = int(time.time() * 1000)
            pose_landmarks.detect_async(img2, timestamp_ms)

        if latest_canvas is not None:
            cv2.imshow("Camera", latest_canvas)
        else:
            cv2.imshow("Camera", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()