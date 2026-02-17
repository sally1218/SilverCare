import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

timestamp_ms = 0

def pose_result(result, output_image, timestamp):
    if result.pose_landmarks:
        print("有偵測到人體")

base_options = python.BaseOptions(model_asset_path='pose_landmarker.task')
#參數
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM, #即時串流
    result_callback= pose_result
)
with vision.PoseLandmarker.create_from_options(options) as pose_landmarker:
    #開啟攝影機
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("無法開啟攝影機")
        exit()
    while True:
        ret, frame = cap.read()
        if not ret:
            print("無法讀取影像")
            break
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) #BGR轉RGB
        img2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=img) #轉mediapipe影像格式
        timestamp_ms += 1
        pose_landmarker.detect_async(img2, timestamp_ms) 
        cv2.imshow("Camera", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
