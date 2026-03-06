import cv2
import mediapipe as mp
import time
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from mediapipe.framework.formats import landmark_pb2

mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
mp_pose = mp.solutions.pose

latest_canvas = None #儲存最新的偵測結果
is_processing = False
current_timestamp = 0

def pose_result(result, output_image, timestamp):
    global latest_canvas, is_processing
    try:
        canvas = cv2.cvtColor(output_image.numpy_view(), cv2.COLOR_RGB2BGR) #轉換為BGR格式以便OpenCV顯示
        
        if result.pose_landmarks:
            for landmark_list in result.pose_landmarks: 
                pose_landmarks_proto = landmark_pb2.NormalizedLandmarkList() #創建一個新的NormalizedLandmarkList物件
                pose_landmarks_proto.landmark.extend([ 
                    landmark_pb2.NormalizedLandmark(x=lm.x, y=lm.y, z=lm.z) 
                    for lm in landmark_list
                ])         
                mp_drawing.draw_landmarks(
                    canvas,
                    pose_landmarks_proto, # 使用轉換後的格式
                    mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style()
                )
        
        latest_canvas = canvas
        
    except Exception as e:
        print(f"Error in callback: {e}")
    finally:
        is_processing = False

base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
#參數
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM, #即時串流
    result_callback= pose_result, #回傳
    num_poses = 1, #偵測人體數量
    min_pose_detection_confidence=0.5
)
with vision.PoseLandmarker.create_from_options(options) as pose_landmarks:
    #開啟攝影機
    cap = cv2.VideoCapture(0)
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            print("無法讀取影像")
            break
        frame = cv2.resize(frame, (640, 480))
     
        if not is_processing:
            is_processing = True
            current_timestamp += 1
            ai_frame = cv2.resize(frame, (320, 240))
            img = cv2.cvtColor(ai_frame, cv2.COLOR_BGR2RGB)
            img2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=img)
            timestamp_ms = int(time.time() * 1000)
            pose_landmarks.detect_async(img2, timestamp_ms)
        if latest_canvas is not None:
            final_show = cv2.resize(latest_canvas, (640, 480))
            cv2.imshow("Rehab System - AI Detection", final_show)
        else:
            cv2.imshow("Rehab System - AI Detection", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
