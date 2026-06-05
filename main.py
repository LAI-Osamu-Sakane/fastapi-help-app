import sqlite3
from datetime import datetime
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse

app = FastAPI()

# 別の画面（HTMLファイル）からこのPythonに通信できるようにする設定（CORS解除）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "help_system.db"


def init_db():
    """データベースとテーブルを初期化する関数"""
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
                       timestamp
                       TEXT,
                       room
                       INTEGER,
                       name
                       TEXT,
                       status
                       TEXT,
                       completed_at
                       TEXT
                   )
                   """)
    conn.commit()
    conn.close()


# サーバー起動時にデータベースを自動作成
init_db()


# ==========================================
# 1. 待機件数の取得 (受講生用 action=status)
# ==========================================
@app.get("/status")
def get_status():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 現在「待機中」の件数だけを数えるSQL
    cursor.execute("SELECT COUNT(*) FROM help_requests WHERE status = '待機中'")
    count = cursor.fetchone()[0]
    conn.close()
    return {"waitCount": count}


# ==========================================
# 2. 一覧の取得 (講師・受講生用 action=list)
# ==========================================
@app.get("/list")
def get_list():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 結果を辞書形式で取得できるようにする
    cursor = conn.cursor()

    # ステータスの優先度（対応中 ＞ 待機中 ＞ 対応済み）を設定し、その中でID順にする
    # 「対応中」「待機中」のデータ、または「対応済みになってから5分（300秒）以内」のデータだけを取得する
    cursor.execute("""
                   SELECT *
                   FROM help_requests
                   WHERE status != '対応済み' 
                OR (
                    status = '対応済み' 
                    -- completed_at（HH:mm:ss）の前に今日の日付（yyyy-mm-dd）を自動でくっつけて、正しい秒数に変換します
                    AND (
                        strftime('%s', 'now', 'localtime') - 
                        strftime('%s', strftime('%Y-%m-%d ', 'now', 'localtime') || completed_at)
                    ) < 300
                )
                   ORDER BY
                       CASE status
                       WHEN '対応中' THEN 1
                       WHEN '待機中' THEN 2
                       WHEN '対応済み' THEN 3
                   END
                   ASC,
                id ASC
                   """)
    rows = cursor.fetchall()
    conn.close()

    result_list = []
    for row in rows:
        result_list.append({
            "rowIndex": row["id"],  # HTML側が識別する行番号としてIDを渡す
            "timestamp": row["timestamp"],
            "room": row["room"],
            "name": row["name"],
            "status": row["status"],
            "completedAt": row["completed_at"]
        })

    return {"list": result_list}


# ==========================================
# 3. ヘルプの登録 (受講生用 action=help)
# ==========================================
@app.get("/help")
def do_help(room: int = Query(...), name: str = Query("")):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 【重複チェック】同じルームで既に「待機中」または「対応中」がないか確認
    cursor.execute(
        "SELECT COUNT(*) FROM help_requests WHERE room = ? AND (status = '待機中' OR status = '対応中')",
        (room,)
    )
    already_exists = cursor.fetchone()[0] > 0

    if already_exists:
        conn.close()
        # 重複している場合は登録させず、エラー理由を返す
        return {"ok": False, "reason": "already_waiting"}

    # 新規登録処理 (現在の時刻を取得)
    now_str = datetime.now().strftime("%H:%M:%S")
    cursor.execute(
        "INSERT INTO help_requests (timestamp, room, name, status, completed_at) VALUES (?, ?, ?, '待機中', '')",
        (now_str, room, name)
    )
    conn.commit()
    conn.close()

    return {"ok": True}


# ==========================================
# 4. ヘルプのキャンセル (受講生用 action=cancel)
# ==========================================
@app.get("/cancel")
def do_cancel(room: int = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 該当ルームの「待機中」のデータを削除（またはステータス変更でも可、今回は削除）
    cursor.execute("DELETE FROM help_requests WHERE room = ? AND status = '待機中'", (room,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ==========================================
# 5. 対応中にする (講師用 action=progress)
# ==========================================
@app.get("/progress")
def do_progress(rowIndex: int = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 指定されたIDのステータスを「対応中」に更新
    cursor.execute("UPDATE help_requests SET status = '対応中' WHERE id = ?", (rowIndex,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ==========================================
# 6. 対応済みにする (講師用 action=done)
# ==========================================
@app.get("/done")
def do_done(rowIndex: int = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 現在の完了時刻を取得
    now_str = datetime.now().strftime("%H:%M:%S")

    # ステータスを「対応済み」にし、完了時刻を記録
    cursor.execute(
        "UPDATE help_requests SET status = '対応済み', completed_at = ? WHERE id = ?",
        (now_str, rowIndex)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

# http://localhost:8000/student にアクセスしたら「student.html」を返す
@app.get("/student")
def get_student_page():
    return FileResponse("student.html")

# http://localhost:8000/teacher にアクセスしたら「teacher.html」を返す
@app.get("/teacher")
def get_teacher_page():
    return FileResponse("teacher.html")