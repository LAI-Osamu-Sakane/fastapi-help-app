import sqlite3
import csv
import io
import os
import glob
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import socketio

# ==========================================
# 🛠️ サーバー & Socket.IO の初期設定
# ==========================================
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
asgi_app = socketio.ASGIApp(sio, app)

DB_FILE = "help_system.db"
CSV_DIR = "csv_logs"

# CORS（クロスオリジンリソース共有）の設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 💾 データベース & ログ保存関連のロジック
# ==========================================
def init_db():
    """データベースの初期化（初回起動時にテーブルを作成）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS help_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id TEXT DEFAULT 'default',
            timestamp TEXT,
            room INTEGER,
            period INTEGER DEFAULT 1,
            name TEXT,
            status TEXT,
            completed_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def export_to_csv():
    """溜まったデータをCSVファイルとして外部フォルダに自動保存する"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM help_requests ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    os.makedirs(CSV_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    csv_filename = f"{CSV_DIR}/help_log_{date_str}.csv"

    with open(csv_filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "クラスID", "受付時刻", "ルーム番号", "時限（限目）", "お名前", "ステータス", "完了時刻"])
        for row in rows:
            writer.writerow([
                row["id"], row["class_id"], row["timestamp"], row["room"],
                row["period"], row["name"], row["status"], row["completed_at"]
            ])


def cleanup_old_data():
    """夜0時〜0時5分の間にアクセスがあった場合、前日のデータをCSVに退避してDBを空にする"""
    current_time_str = datetime.now().strftime("%H:%M")
    if "00:00" <= current_time_str <= "00:05":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        export_to_csv()
        cursor.execute("DELETE FROM help_requests")
        conn.commit()
        conn.close()


def get_current_list(class_id: str):
    """指定されたクラスID（部屋）の有効なヘルプ一覧を取得する（完了後5分以内のデータを含む）"""
    cleanup_old_data()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT *
                   FROM help_requests
                   WHERE class_id = ?
                     AND (
                       status != '対応済み' 
           OR (status = '対応済み' AND (strftime('%s', 'now', 'localtime') - strftime('%s', strftime('%Y-%m-%d ', 'now', 'localtime') || completed_at)) < 300)
                       )
                   ORDER BY CASE status WHEN '対応中' THEN 1 WHEN '待機中' THEN 2 WHEN '対応済み' THEN 3 END ASC, id ASC
                   """, (class_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "rowIndex": row["id"],
        "classId": row["class_id"],
        "timestamp": row["timestamp"],
        "room": row["room"],
        "period": row["period"],
        "name": row["name"],
        "status": row["status"],
        "completedAt": row["completed_at"]
    } for row in rows]


# 💡 【重要】プログラム読み込み時に強制的にテーブルを作成・チェックする安全装置
init_db()


# ==========================================
# 🌐 画面配信ルーティング (HTMLの表示)
# ==========================================
@app.get('/student', response_class=HTMLResponse)
def get_student_page():
    try:
        with open("student.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTTPException(status_code=404, detail="「student.html」ファイルが見つかりません。")


@app.get('/teacher', response_class=HTMLResponse)
def get_teacher_page():
    try:
        with open("teacher.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTTPException(status_code=404, detail="「teacher.html」ファイルが見つかりません。")


@app.get('/teacher/csv-history', response_class=HTMLResponse)
def get_csv_history_page():
    try:
        with open("csv_history.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTTPException(status_code=404, detail="「csv_history.html」ファイルが見つかりません。")


# ==========================================
# 📊 データ管理・CSV操作用 APIエンドポイント
# ==========================================
@app.get('/download_csv')
def download_current_csv():
    """現在のデータベース内にあるすべてのデータをその場でCSVダウンロードする"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM help_requests ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "クラスID", "受付時刻", "ルーム番号", "時限（限目）", "お名前", "ステータス", "完了時刻"])
    for row in rows:
        cw.writerow(
            [row["id"], row["class_id"], row["timestamp"], row["room"], row["period"], row["name"], row["status"],
             row["completed_at"]])

    output = io.BytesIO(si.getvalue().encode('utf-8-sig'))
    time_str = datetime.now().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=help_log_{time_str}.csv"}
    )


@app.get('/api/csv-files')
def list_csv_files():
    """csv_logs フォルダ内にある過去のCSVファイル一覧を返すAPI"""
    if not os.path.exists(CSV_DIR):
        return []
    files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
    file_list = []
    for f in sorted(files, reverse=True):  # 新しい日付順
        filename = os.path.basename(f)
        size = os.path.getsize(f)
        size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} Bytes"
        file_list.append({"filename": filename, "size": size_str})
    return file_list


@app.get('/api/download-csv/{filename}')
def download_past_csv(filename: str):
    """過去の特定のCSVログファイルをダウンロードさせるAPI"""
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(CSV_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="text/csv", filename=safe_filename)


# ==========================================
# ⚡ Socket.IO リアルタイム双方向通信イベント
# ==========================================
@sio.event
async def connect(sid, environ):
    """最初の接続時は何もしない（画面からルーム入室要求が来るのを待つ）"""
    pass


@sio.event
async def join_class(sid, data):
    """受講生や講師を、URLで指定された独立した部屋（クラスグループ）に所属させる"""
    class_id = data.get('classId', 'default')
    await sio.enter_room(sid, class_id)  # Socket.IOのルーム機能を利用
    # 接続した人に対して、そのクラスの最新データだけを返す
    await sio.emit('update_data', {'list': get_current_list(class_id)}, to=sid)


@sio.event
async def request_help(sid, data):
    """受講生からのヘルプ送信要求"""
    class_id = data.get('classId', 'default')
    room = int(data.get('room'))
    period = int(data.get('period', 1))
    name = data.get('name', '').strip()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 同じクラス・同じルームで既に「待機中」「対応中」のヘルプが無いか重複チェック
    cursor.execute(
        "SELECT COUNT(*) FROM help_requests WHERE class_id = ? AND room = ? AND (status = '待機中' OR status = '対応中')",
        (class_id, room))
    if cursor.fetchone()[0] > 0:
        conn.close()
        await sio.emit('help_response', {'ok': False, 'reason': 'already_waiting'}, to=sid)
        return

    now_str = datetime.now().strftime("%H:%M:%S")
    cursor.execute(
        "INSERT INTO help_requests (class_id, timestamp, room, period, name, status, completed_at) VALUES (?, ?, ?, ?, ?, '待機中', '')",
        (class_id, now_str, room, period, name))
    conn.commit()
    conn.close()

    # 同じクラスグループの全員に最新リストを配信
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)
    await sio.emit('help_response', {'ok': True}, to=sid)


@sio.event
async def request_cancel(sid, data):
    """受講生からのヘルプ取り消し（キャンセル）要求"""
    class_id = data.get('classId', 'default')
    room = int(data.get('room'))
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM help_requests WHERE class_id = ? AND room = ? AND status = '待機中'", (class_id, room))
    conn.commit()
    conn.close()
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)


@sio.event
async def request_progress(sid, data):
    """講師による「▶ 対応中にする」操作"""
    class_id = data.get('classId', 'default')
    row_index = int(data.get('rowIndex'))
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE help_requests SET status = '対応中' WHERE id = ?", (row_index,))
    conn.commit()
    conn.close()
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)


@sio.event
async def request_done(sid, data):
    """講師による「✅ 対応済みにする」操作"""
    class_id = data.get('classId', 'default')
    row_index = int(data.get('rowIndex'))
    now_str = datetime.now().strftime("%H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE help_requests SET status = '対応済み', completed_at = ? WHERE id = ?", (now_str, row_index))
    conn.commit()
    conn.close()
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)


@sio.event
async def check_timeout(sid, data=None):
    """画面側のカウントダウンタイマーと同調して定期実行される生存確認"""
    if data is None or not isinstance(data, dict):
        class_id = 'default'
    else:
        class_id = data.get('classId', 'default')
    await sio.emit('update_data', {'list': get_current_list(class_id)}, to=sid)


# ==========================================
# 🚀 サーバーの起動
# ==========================================
if __name__ == '__main__':
    # 起動時の古いデータクリーンアップ（もし前日のデータが残っていればCSVに保存して空にする）
    # ※init_db()は上に移動したため、ここでは生存チェックとクリーンアップのみを行います
    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM help_requests")
            if cursor.fetchone()[0] > 0:
                export_to_csv()
                cursor.execute("DELETE FROM help_requests")
                conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    import uvicorn

    print("🚀 ローカル環境でヘルプシステムを起動します。")
    uvicorn.run("app:asgi_app", host="0.0.0.0", port=8000, factory=False)