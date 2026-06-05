import sqlite3
import csv
import io
import os
import glob
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import socketio

# ==========================================
# 🛠️ サーバー & Socket.IO の初期設定
# ==========================================
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    max_http_packets_per_request=1000  # 💡 パケットの許容量を増やす設定を追加
)
app = FastAPI()
asgi_app = socketio.ASGIApp(sio, app)

DB_FILE = "help_system.db"
CSV_DIR = "csv_logs"

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
    """データベースの初期化（初回起動時にテーブルを作成、および既存DBへのカラム追加）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS help_requests
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       class_id
                       TEXT
                       DEFAULT
                       'default',
                       timestamp
                       TEXT,
                       room
                       INTEGER,
                       period
                       INTEGER
                       DEFAULT
                       1,
                       name
                       TEXT,
                       status
                       TEXT,
                       completed_at
                       TEXT
                   )
                   """)

    # 💡 【自動拡張】過去のDBファイルが存在する場合に、新設する started_at（対応開始時刻）カラムを追加する
    try:
        cursor.execute("ALTER TABLE help_requests ADD COLUMN started_at TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        # 既にカラムが存在する場合は何もしない
        pass

    conn.commit()
    conn.close()


def export_to_csv():
    """溜まったデータをCSVファイルとして外部フォルダに自動保存する（分析用カラム含む）"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM help_requests ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    os.makedirs(CSV_DIR, exist_ok=True)
    jst = timezone(timedelta(hours=9))
    date_str = datetime.now(jst).strftime("%Y-%m-%d")
    csv_filename = f"{CSV_DIR}/help_log_{date_str}.csv"

    with open(csv_filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["ID", "クラスID", "受付時刻", "対応開始時刻", "完了時刻", "ルーム番号", "時限", "お名前", "ステータス"])
        for row in rows:
            # カラムが古い可能性を考慮して辞書型安全取得
            started_at = row["started_at"] if "started_at" in row.keys() else ""
            writer.writerow([
                row["id"], row["class_id"], row["timestamp"], started_at,
                row["completed_at"], row["room"], row["period"], row["name"], row["status"]
            ])


def cleanup_old_data():
    """夜0時〜0時5分の間にアクセスがあった場合、前日のデータをCSVに退避してDBを空にする"""
    jst = timezone(timedelta(hours=9))
    current_time_str = datetime.now(jst).strftime("%H:%M")
    if "00:00" <= current_time_str <= "00:05":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        export_to_csv()
        cursor.execute("DELETE FROM help_requests")
        conn.commit()
        conn.close()


def get_current_list(class_id: str):
    """指定されたクラスID（部屋）の有効なヘルプ一覧を取得する"""
    cleanup_old_data()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT *
                   FROM help_requests
                   WHERE class_id = ?
                   ORDER BY CASE status WHEN '対応中' THEN 1 WHEN '待機中' THEN 2 WHEN '対応済み' THEN 3 END ASC, id ASC
                   """, (class_id,))
    rows = cursor.fetchall()
    conn.close()

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    valid_rows = []

    for row in rows:
        if row["status"] in ["待機中", "対応中"]:
            valid_rows.append(row)
            continue

        if row["status"] == "対応済み":
            if not row["completed_at"]:
                continue
            try:
                comp_time = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {row['completed_at']}",
                                              "%Y-%m-%d %H:%M:%S").replace(tzinfo=jst)
                diff_seconds = (now - comp_time).total_seconds()
                if 0 <= diff_seconds < 300:
                    valid_rows.append(row)
            except ValueError:
                pass

    return [{
        "rowIndex": row["id"],
        "classId": row["class_id"],
        "timestamp": row["timestamp"],
        "startedAt": row["started_at"] if "started_at" in row.keys() else "",
        "room": row["room"],
        "period": row["period"],
        "name": row["name"],
        "status": row["status"],
        "completedAt": row["completed_at"]
    } for row in valid_rows]


init_db()


# ==========================================
# 🌐 画面配信ルーティング (HTMLの表示)
# ==========================================
@app.get('/student', response_class=HTMLResponse)
def get_student_page():
    with open("student.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.get('/teacher', response_class=HTMLResponse)
def get_teacher_page():
    with open("teacher.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.get('/teacher/csv-history', response_class=HTMLResponse)
def get_csv_history_page():
    with open("csv_history.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


# 💡 【新設】分析・統計画面への配信ルーティング
@app.get('/teacher/analytics', response_class=HTMLResponse)
def get_analytics_page():
    try:
        with open("analytics.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h3>「analytics.html」ファイルが見つかりません。</h3>", status_code=404)


# ==========================================
# 📊 データ管理・分析用 APIエンドポイント
# ==========================================
# 💡 【新設】特定のクラス・指定時限のすべての生データを取得する集計用API
@app.get('/api/analytics-data')
def get_analytics_data(classId: str = "default", period: str = "all"):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if period == "all":
        cursor.execute("SELECT * FROM help_requests WHERE class_id = ? ORDER BY id ASC", (classId,))
    else:
        cursor.execute("SELECT * FROM help_requests WHERE class_id = ? AND period = ? ORDER BY id ASC",
                       (classId, int(period)))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "timestamp": row["timestamp"],
            "started_at": row["started_at"] if "started_at" in row.keys() else "",
            "completed_at": row["completed_at"],
            "room": row["room"],
            "period": row["period"],
            "name": row["name"],
            "status": row["status"]
        })
    return result


@app.get('/download_csv')
def download_current_csv():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM help_requests ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(
        ["ID", "クラスID", "受付時刻", "対応開始時刻", "完了時刻", "ルーム番号", "時限（限目）", "お名前", "ステータス"])
    for row in rows:
        started_at = row["started_at"] if "started_at" in row.keys() else ""
        cw.writerow(
            [row["id"], row["class_id"], row["timestamp"], started_at, row["completed_at"], row["room"], row["period"],
             row["name"], row["status"]])

    output = io.BytesIO(si.getvalue().encode('utf-8-sig'))
    jst = timezone(timedelta(hours=9))
    time_str = datetime.now(jst).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=help_log_{time_str}.csv"}
    )


@app.get('/api/csv-files')
def list_csv_files():
    if not os.path.exists(CSV_DIR):
        return []
    files = glob.glob(os.path.join(CSV_DIR, "*.csv"))
    file_list = []
    for f in sorted(files, reverse=True):
        filename = os.path.basename(f)
        size = os.path.getsize(f)
        size_str = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} Bytes"
        file_list.append({"filename": filename, "size": size_str})
    return file_list


@app.get('/api/download-csv/{filename}')
def download_past_csv(filename: str):
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
    pass


@sio.event
async def join_class(sid, data):
    class_id = data.get('classId', 'default')
    await sio.enter_room(sid, class_id)
    await sio.emit('update_data', {'list': get_current_list(class_id)}, to=sid)


@sio.event
async def request_help(sid, data):
    class_id = data.get('classId', 'default')
    room = int(data.get('room'))
    period = int(data.get('period', 1))
    name = data.get('name', '').strip()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM help_requests WHERE class_id = ? AND room = ? AND (status = '待機中' OR status = '対応中')",
            (class_id, room))
        if cursor.fetchone()[0] > 0:
            await sio.emit('help_response', {'ok': False, 'reason': 'already_waiting'}, to=sid)
            return

        jst = timezone(timedelta(hours=9))
        now_str = datetime.now(jst).strftime("%H:%M:%S")

        # started_at の初期値は空文字
        cursor.execute(
            "INSERT INTO help_requests (class_id, timestamp, room, period, name, status, completed_at, started_at) VALUES (?, ?, ?, ?, ?, '待機中', '', '')",
            (class_id, now_str, room, period, name))
        conn.commit()

    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)
    await sio.emit('help_response', {'ok': True}, to=sid)


@sio.event
async def request_cancel(sid, data):
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
    class_id = data.get('classId', 'default')
    row_index = int(data.get('rowIndex'))

    # 💡 【機能拡張】対応中にした瞬間の「日本時間」を started_at カラムに記録する
    jst = timezone(timedelta(hours=9))
    now_str = datetime.now(jst).strftime("%H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE help_requests SET status = '対応中', started_at = ? WHERE id = ?", (now_str, row_index))
    conn.commit()
    conn.close()
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)


@sio.event
async def request_done(sid, data):
    class_id = data.get('classId', 'default')
    row_index = int(data.get('rowIndex'))
    jst = timezone(timedelta(hours=9))
    now_str = datetime.now(jst).strftime("%H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 💡 対応開始（started_at）が未記録だった場合の安全ガード処理
    cursor.execute("SELECT started_at FROM help_requests WHERE id = ?", (row_index,))
    current_start = cursor.fetchone()[0]
    if not current_start:
        cursor.execute("UPDATE help_requests SET status = '対応済み', started_at = ?, completed_at = ? WHERE id = ?",
                       (now_str, now_str, row_index))
    else:
        cursor.execute("UPDATE help_requests SET status = '対応済み', completed_at = ? WHERE id = ?",
                       (now_str, row_index))

    conn.commit()
    conn.close()
    await sio.emit('update_data', {'list': get_current_list(class_id)}, room=class_id)


@sio.event
async def check_timeout(sid, data=None):
    if data is None or not isinstance(data, dict):
        class_id = 'default'
    else:
        class_id = data.get('classId', 'default')
    await sio.emit('update_data', {'list': get_current_list(class_id)}, to=sid)


if __name__ == '__main__':
    import uvicorn

    print("🚀 ローカル環境でヘルプ分析対応版システムを起動します。")
    uvicorn.run("app:asgi_app", host="0.0.0.0", port=8000, factory=False)