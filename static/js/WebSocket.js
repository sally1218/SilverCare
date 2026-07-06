var socket = io();

// 宣告全域變數以供 WebRTC 與鏡頭串流使用
let localStream = null;
let peerConnection = null;
let currentRoom = null;

const rtcConfig = {
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
};

socket.on('connect', function () {
    console.log('WebSocket connected!');
    initLocalCamera();
});

//WebRTC 信令牽線
socket.on('sdp_signal', async function (data) {
    try {
        if (!peerConnection) makePeerConnection();

        if (data.sdp) {
            console.log("收到對手的 SDP 描述文件");
            await peerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));

            if (data.sdp.type === 'offer') {
                const answer = await peerConnection.createAnswer();
                await peerConnection.setLocalDescription(answer);
                socket.emit('sdp_signal', { sdp: peerConnection.localDescription });
            }
        } else if (data.candidate) {
            console.log("收到對手的 ICE 網路候選人節點");
            await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
        }
    } catch (e) {
        console.error("WebRTC 牽線過程發生錯誤: ", e);
    }
});

function makePeerConnection() {
    peerConnection = new RTCPeerConnection(rtcConfig);

    if (localStream) {
        localStream.getTracks().forEach(track => {
            peerConnection.addTrack(track, localStream);
        });
    }

    peerConnection.ontrack = (event) => {
        console.log("成功接到對手的 WebRTC 視訊串流！");
        var p2Video = document.getElementById('p2-video');
        var p2Placeholder = document.getElementById('p2-placeholder');

        if (p2Placeholder) p2Placeholder.style.display = 'none';
        if (p2Video) {
            p2Video.style.display = 'block';
            p2Video.srcObject = event.streams[0];
        }
    };

    peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
            socket.emit('sdp_signal', { candidate: event.candidate });
        }
    };
}

async function startWebRTCInvite() {
    if (!peerConnection) makePeerConnection();
    console.log("發起 WebRTC 連線請求 (Create Offer)...");
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    socket.emit('sdp_signal', { sdp: peerConnection.localDescription });
}

// 接收對手的分數與狀態更新
socket.on('opponent_data_sync', function (data) {
    var p2Score = document.getElementById('p2-score');
    var p2Stage = document.getElementById('p2-stage');

    if (p2Score && data.score !== undefined) p2Score.innerText = data.score;
    if (p2Stage && data.stage) {
        p2Stage.innerText = (data.stage === "UP") ? "抬腿" : "放下";
        p2Stage.style.color = (data.stage === "UP") ? "#e67e22" : "#3498db";
    }
});

socket.on('server_response', function (data) {
    console.log('伺服器連線成功通知: ' + data.msg);
    var dot = document.getElementById('p2-dot');
    var stage = document.getElementById('p2-stage');
    var placeholder = document.getElementById('p2-placeholder');

    if (dot) dot.className = "status-dot online";
    if (stage) {
        stage.innerText = "已連線";
        stage.style.color = "#2ecc71";
    }
    if (placeholder) placeholder.innerText = "對手已連線，接通畫面中...";

    alert('【房間動態通知】\n' + data.msg);
    startWebRTCInvite();
});

socket.on('room_created_success', function (data) {
    alert('成功創建房間！房間號: ' + data.room);
    currentRoom = data.room;
    var displayNum = document.getElementById('displayRoomNumber');
    if (displayNum) {
        displayNum.innerText = data.room;
    }
});

// 當後端儲存成功後的回覆
socket.on('response_save_success', function (data) {
    if (data.saved === true) {
        const toast = document.getElementById('save-toast');
        if (toast) {
            toast.style.display = 'block';
            setTimeout(() => {
                toast.style.display = 'none';
                location.reload();
            }, 2500);
        }
    }
});

function initLocalCamera() {
    const video = document.getElementById('p1-local-video');
    if (!video) return;

    navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false })
        .then(function (stream) {
            localStream = stream;
            video.srcObject = stream;
            video.play();
            console.log("本地端硬體鏡頭啟動成功！");
            if (typeof startFrontendDetection === "function") {
                startFrontendDetection(video);
            }
        })
        .catch(function (err) {
            console.error("無法開啟攝影機鏡頭: ", err);
            alert("系統提示：請允許瀏覽器載入視訊鏡頭權限以進行復健偵測。");
        });
}

function enterRoom() {
    var roomName = document.getElementById('roomName').value;
    console.log('正在嘗試加入房間: ' + roomName);
    currentRoom = roomName;
    socket.emit('joinroom', roomName);
}

function createRoom() {
    console.log('正在創建房間...');
    socket.emit('createroom');
}

function clearNum() {
    document.getElementById('roomName').value = '';
}

function pressNum(num) {
    const input = document.getElementById('roomName');
    if (input.value.length < 3) {
        input.value += num;
    }
    if (input.value.length === 3) {
        console.log('確認滿三位數，自動觸發連線：' + input.value);
        currentRoom = input.value; // ✨ 修正：補上綁定全域變數
        socket.emit('joinroom', input.value);
    }
}

function initPage() {
    var p2Placeholder = document.getElementById('p2-placeholder');
    var p2Video = document.getElementById('p2-video');
    var p2Dot = document.getElementById('p2-dot');
    var p2Stage = document.getElementById('p2-stage');
    if (p2Placeholder) p2Placeholder.style.display = 'block';
    if (p2Video) p2Video.style.display = 'none';
    if (p2Dot) p2Dot.className = "status-dot offline";
    if (p2Stage) {
        p2Stage.innerText = "OFFLINE";
        p2Stage.style.color = "#7f8c8d";
    }
}

function clickCreateMode() {
    document.getElementById('host-display-area').style.display = 'block';
    document.getElementById('p2-stage').innerText = "連線中...";
    document.getElementById('p2-stage').style.color = "#f39c12";
    if (typeof socket !== 'undefined') {
        socket.emit('createroom');
    }
}

function clickJoinMode() {
    document.getElementById('initial-choice-area').style.display = 'none';
    document.getElementById('guest-keyboard-area').style.display = 'block';
}