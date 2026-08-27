#!/usr/bin/env python3
"""
ComfyUI WebUI 管理服务
端口：8025（用户界面）
调用：8026（API 服务）
"""

import os
import json
import shutil
import sqlite3
import base64
import subprocess
import tempfile
import requests
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from version import __version__, get_version_info

app = FastAPI(title="ComfyUI WebUI Manager", version=__version__)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置
API_BASE_URL = "http://localhost:8026"
WORKFLOW_CONFIGS_DIR = Path("/root/workflow_configs")
UPLOAD_DIR = Path("/tmp/webui_uploads")
DB_PATH = Path("/tmp/webui_tasks.db")
COMFYUI_INPUT_DIR = Path("/mnt/storage/MMX_ComfyUI-input")
COMFYUI_OUTPUT_DIR = Path("/mnt/storage/MMX_ComfyUI-output")
HAIMI_OUTPUT_DIR = Path("/mnt/storage/MMX_ComfyUI-output/Haimi")

# 确保目录存在
WORKFLOW_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
COMFYUI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
COMFYUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HAIMI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 静态文件
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# ========== 数据库工具 ==========

def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """初始化数据库表"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        # 创建任务表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)

        # 创建历史记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                aspect_ratio TEXT,
                resolution REAL,
                video_path TEXT,
                thumbnail TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.commit()
        conn.close()
        print(f"✅ 数据库初始化成功: {DB_PATH}")
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        raise

# 启动时初始化数据库
init_database()

# 启动时重置卡住的任务（服务器重启时可能遗留的 processing 任务）
def reset_stuck_tasks():
    """重置卡住的任务：
    - 有进度文件的 queued/processing 任务 → 保持原状态（已在 ComfyUI 中，重启后继续轮询）
    - 无进度文件的 queued/processing 任务 → 重置为 pending（从未提交，重新排队）
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM tasks WHERE status IN ('queued', 'processing')")
        active_rows = cursor.fetchall()

        progress_dir = Path("/tmp/comfyui_progress")
        to_reset = []
        to_keep = []
        for row in active_rows:
            tid = row['id']
            progress_file = progress_dir / f"{tid}.json"
            if progress_file.exists():
                to_keep.append(tid)
            else:
                to_reset.append(tid)

        if to_reset:
            placeholders = ','.join('?' * len(to_reset))
            cursor.execute(f"""
                UPDATE tasks SET status = 'pending', started_at = NULL
                WHERE id IN ({placeholders})
            """, to_reset)

        conn.commit()
        conn.close()

        if to_reset:
            print(f"🔄 已重置 {len(to_reset)} 个未提交的任务（processing → pending）")
        if to_keep:
            print(f"🔄 保留 {len(to_keep)} 个已提交的任务（继续轮询 ComfyUI）")
    except Exception as e:
        print(f"⚠️ 重置卡住任务失败: {e}")

reset_stuck_tasks()

# ========== 路径安全工具 ==========

def detect_oom_error(error_text: str) -> bool:
    """检测是否为 OOM（显存不足）错误"""
    oom_indicators = [
        "OutOfMemoryError",
        "CUDA out of memory",
        "cudaErrorMemoryAllocation",
        "OutOfMemory",
        "torch.OutOfMemoryError",
        "GPU out of memory",
        "not enough memory",
        "_ALLOC_FAILED",
        "out of memory",
        "OOM_ERROR",
    ]
    error_lower = error_text.lower()
    return any(ind.lower() in error_lower for ind in oom_indicators)

def enrich_oom_error(error_msg: str) -> str:
    """如果错误是 OOM，追加用户友好的操作建议"""
    if detect_oom_error(error_msg):
        oom_hint = (
            "\n\n💥 显存不足（OOM）！建议：\n"
            "1. 降低「分辨率质量」（建议 0.3~0.5）\n"
            "2. 缩短「视频时长」（建议 ≤10 秒）\n"
            "3. 减少参考图片数量\n"
            "4. 使用更小的画面比例（如 1:1 或 4:3）"
        )
        if "OOM_ERROR" not in error_msg:
            return error_msg + oom_hint
    return error_msg

def extract_key_error(error_msg: str) -> str:
    """从大段错误信息中提取关键部分，避免 DB 膨胀"""
    if len(error_msg) <= 2000:
        return error_msg

    # 尝试提取 JSON detail
    try:
        # 格式: API 返回错误: 500 - {"detail": "..."}
        if '{"detail":' in error_msg or '{"detail":' in error_msg:
            import re
            m = re.search(r'\{"detail":\s*"(.+?)"\s*\}', error_msg, re.DOTALL)
            if m:
                detail = m.group(1)
                # 解码转义
                detail = detail.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                if len(detail) > 1500:
                    detail = detail[:1500] + '...'
                return f"API 错误详情:\n{detail}"
    except Exception:
        pass

    return error_msg[:2000] + '... (已截断)'

def make_content_disposition(filename: str, disposition: str = "inline") -> str:
    """生成符合 RFC 5987 的 Content-Disposition 头，支持中文等非 ASCII 字符"""
    ascii_name = filename.encode('ascii', 'replace').decode('ascii')
    encoded_name = quote(filename, safe='')
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"

# 进度文件中需要剥离的大字段（base64 数据）
_PROGRESS_STRIP_FIELDS = {"video_b64", "request_data"}

def sanitize_progress_data(pdata: dict) -> dict:
    """剥离进度文件中的大字段（base64 数据），只返回用于前端展示的小字段"""
    if not pdata:
        return pdata
    return {k: v for k, v in pdata.items() if k not in _PROGRESS_STRIP_FIELDS}

def resolve_safe_path(relative_path: str) -> Path:
    """解析路径并确保在允许的目录内"""
    base = COMFYUI_INPUT_DIR.resolve()
    if relative_path:
        full_path = (COMFYUI_INPUT_DIR / relative_path).resolve()
    else:
        full_path = base

    # 确保路径在允许的目录内（使用字符串比较，处理 symlink）
    if not str(full_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="访问被拒绝：路径超出允许范围")

    return full_path

# ========== 任务队列处理（两阶段：提交 + 轮询） ==========

def submit_pending_tasks():
    """阶段1：将所有 pending 任务立即提交到 ComfyUI 队列"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, model, data FROM tasks
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        pending_rows = cursor.fetchall()
        conn.close()

        for task_row in pending_rows:
            task_id = task_row['id']
            model = task_row['model']
            data = json.loads(task_row['data'])

            # 跳过从 ComfyUI 同步的任务（没有原始请求数据，无法重新提交）
            if data.get("source") == "comfyui_sync":
                print(f"⏭️ 跳过同步任务（已在 ComfyUI 中）: {task_id[:8]}...")
                continue

            # 原子操作：只有 pending 才更新为 queued（已提交到 ComfyUI 等待执行）
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tasks
                SET status = 'queued', started_at = ?
                WHERE id = ? AND status = 'pending'
            """, (datetime.now().isoformat(), task_id))
            updated = cursor.rowcount
            conn.commit()
            conn.close()

            if updated == 0:
                continue

            print(f"📤 提交任务到 ComfyUI: {task_id[:8]}...")
            data["_task_id"] = task_id

            try:
                response = requests.post(
                    f"{API_BASE_URL}/submit",
                    json={"model": model, "data": data},
                    timeout=30
                )
                if response.status_code == 200:
                    result = response.json()
                    prompt_id = result.get("prompt_id", "")
                    print(f"✅ 已提交到 ComfyUI 队列: {task_id[:8]}... (prompt={prompt_id[:8]}...)")
                    # 注意：不在此处剥离 base64，因为任务可能需要重新提交
                    # base64 会在任务完成后由 poll_processing_tasks() 剥离
                elif response.status_code in (503, 504) and "COMFYUI_NOT_AVAILABLE" in response.text:
                    # ComfyUI 未启动，保持 pending 状态，等下次循环自动重试
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks SET status = 'pending', started_at = NULL
                        WHERE id = ?
                    """, (task_id,))
                    conn.commit()
                    conn.close()
                    print(f"⏳ ComfyUI 未启动，任务 {task_id[:8]}... 等待下次重试")
                elif response.status_code == 504 and "COMFYUI_TIMEOUT" in response.text:
                    # ComfyUI 响应超时（可能正在启动），保持 pending 重试
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks SET status = 'pending', started_at = NULL
                        WHERE id = ?
                    """, (task_id,))
                    conn.commit()
                    conn.close()
                    print(f"⏳ ComfyUI 响应超时，任务 {task_id[:8]}... 等待下次重试")
                else:
                    error_msg = f"提交失败: {response.status_code} - {response.text}"
                    error_msg = enrich_oom_error(error_msg)
                    error_msg = extract_key_error(error_msg)
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks
                        SET status = 'failed', error = ?, completed_at = ?
                        WHERE id = ?
                    """, (error_msg, datetime.now().isoformat(), task_id))
                    conn.commit()
                    conn.close()
                    print(f"❌ 提交失败: {task_id[:8]}... - {error_msg}")
            except Exception as e:
                error_msg = f"提交异常: {str(e)}"
                error_msg = extract_key_error(error_msg)
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks
                        SET status = 'failed', error = ?, completed_at = ?
                        WHERE id = ?
                    """, (error_msg, datetime.now().isoformat(), task_id))
                    conn.commit()
                    conn.close()
                except Exception as db_err:
                    print(f"❌ 更新任务状态失败: {db_err}")
                print(f"❌ 提交异常: {task_id[:8]}... - {error_msg}")

    except Exception as e:
        print(f"提交阶段异常: {e}")


def poll_processing_tasks():
    """阶段2：轮询 queued/processing 状态的 tasks，检查 ComfyUI 状态"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, status FROM tasks
            WHERE status IN ('queued', 'processing')
            ORDER BY created_at ASC
        """)
        active_rows = cursor.fetchall()
        conn.close()

        for task_row in active_rows:
            task_id = task_row['id']
            current_db_status = task_row['status']
            try:
                response = requests.get(
                    f"{API_BASE_URL}/result/{task_id}",
                    timeout=30
                )
                if response.status_code != 200:
                    continue

                result = response.json()
                status = result.get("status")

                if status == "completed":
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks
                        SET status = 'completed', progress = 100,
                            result = ?, completed_at = ?
                        WHERE id = ?
                    """, (json.dumps(result), datetime.now().isoformat(), task_id))
                    conn.commit()
                    conn.close()
                    print(f"✅ 任务完成: {task_id[:8]}...")

                    # 任务完成后，剥离 data 中的 base64 大字段，减小 DB 体积
                    try:
                        conn3 = get_db()
                        cur3 = conn3.cursor()
                        cur3.execute("SELECT data FROM tasks WHERE id = ?", (task_id,))
                        row = cur3.fetchone()
                        if row and row["data"]:
                            task_data = json.loads(row["data"])
                            slim_data = {k: v for k, v in task_data.items()
                                         if k not in ("images", "audio", "video")}
                            cur3.execute("UPDATE tasks SET data = ? WHERE id = ?",
                                         (json.dumps(slim_data, ensure_ascii=False), task_id))
                            conn3.commit()
                        conn3.close()
                    except Exception as strip_err:
                        print(f"⚠️ 剥离 base64 失败（不影响任务）: {strip_err}")

                elif status == "failed":
                    error_msg = result.get("error", "未知错误")
                    error_msg = enrich_oom_error(error_msg)
                    error_msg = extract_key_error(error_msg)
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks
                        SET status = 'failed', error = ?, completed_at = ?
                        WHERE id = ?
                    """, (error_msg, datetime.now().isoformat(), task_id))
                    conn.commit()
                    conn.close()
                    print(f"❌ 任务失败: {task_id[:8]}... - {error_msg[:200]}")

                elif status == "running" and current_db_status in ("queued", "processing"):
                    # ComfyUI 开始执行了，更新状态为 processing
                    if current_db_status != "processing":
                        conn = get_db()
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE tasks SET status = 'processing' WHERE id = ?
                        """, (task_id,))
                        conn.commit()
                        conn.close()
                        print(f"🔄 任务开始执行: {task_id[:8]}...")

                elif status == "queued" and current_db_status == "processing":
                    # DB 显示 processing 但 ComfyUI 实际在排队（旧任务修正）
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tasks SET status = 'queued' WHERE id = ?
                    """, (task_id,))
                    conn.commit()
                    conn.close()
                    print(f"⏳ 任务修正为排队中: {task_id[:8]}...")

                # 其他状态（queued/not_found/running with processing）→ 继续等待

            except Exception as e:
                print(f"轮询任务 {task_id[:8]}... 异常: {e}")

    except Exception as e:
        print(f"轮询阶段异常: {e}")


def process_task_worker():
    """后台任务处理工作线程（两阶段：提交 pending + 轮询 processing）"""
    print("✅ 任务处理工作线程已启动（两阶段模式：提交+轮询）")

    while True:
        try:
            submit_pending_tasks()
            poll_processing_tasks()
        except Exception as e:
            print(f"任务处理线程异常: {e}")

        time.sleep(2)

# 启动后台任务处理线程（只有一个）
task_worker_thread = threading.Thread(target=process_task_worker, daemon=True)
task_worker_thread.start()

# Pydantic 模型
class WorkflowConfig(BaseModel):
    model_name: str
    workflow_path: str
    description: str
    inputs: Dict[str, Any]
    output: Dict[str, Any]
    route: Optional[Dict[str, Any]] = None

class GenerateRequest(BaseModel):
    model: str
    data: Dict[str, Any]

# 静态文件服务
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ========== API 接口 ==========

@app.get("/", response_class=HTMLResponse)
async def index():
    """主页面 - 用户使用界面"""
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="页面未找到")
    content = html_path.read_text(encoding="utf-8")
    response = HTMLResponse(content=content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """管理界面"""
    html_path = STATIC_DIR / "admin.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="页面未找到")
    return html_path.read_text(encoding="utf-8")

@app.get("/api/version")
async def get_version():
    """获取版本信息"""
    return get_version_info()

@app.get("/api/workflows")
async def list_workflows():
    """获取所有工作流配置"""
    workflows = []
    for config_file in WORKFLOW_CONFIGS_DIR.glob("*.json"):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
                workflows.append({
                    "filename": config_file.name,
                    "model_name": config.get("model_name"),
                    "description": config.get("description"),
                    "config": config
                })
        except Exception as e:
            print(f"加载配置失败 {config_file.name}: {e}")

    return {"workflows": workflows}

@app.get("/api/workflows/{model_name}")
async def get_workflow(model_name: str):
    """获取指定工作流配置"""
    config_file = WORKFLOW_CONFIGS_DIR / f"{model_name}.json"
    if not config_file.exists():
        raise HTTPException(status_code=404, detail="工作流配置不存在")

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    return {"config": config}

@app.get("/api/presets/prompts")
async def get_prompt_presets():
    """获取提示词模板列表"""
    presets_file = STATIC_DIR / "presets" / "prompts" / "templates.json"
    if not presets_file.exists():
        return {"prompts": []}

    with open(presets_file, "r", encoding="utf-8") as f:
        prompts = json.load(f)

    return {"prompts": prompts}

@app.get("/api/presets/images")
async def get_image_presets():
    """获取示例图片列表"""
    index_file = STATIC_DIR / "presets" / "images" / "index.json"
    if not index_file.exists():
        return {"images": []}

    with open(index_file, "r", encoding="utf-8") as f:
        images = json.load(f)

    return {"images": images}

@app.get("/api/presets/audios")
async def get_audio_presets():
    """获取示例音频列表"""
    index_file = STATIC_DIR / "presets" / "audios" / "index.json"
    if not index_file.exists():
        return {"audios": []}

    with open(index_file, "r", encoding="utf-8") as f:
        audios = json.load(f)

    return {"audios": audios}

@app.get("/api/presets/audio/{filename}")
async def get_preset_audio(filename: str):
    """获取预置音频文件（返回base64）"""
    audio_file = STATIC_DIR / "presets" / "audios" / filename
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")

    with open(audio_file, "rb") as f:
        audio_data = f.read()

    audio_b64 = base64.b64encode(audio_data).decode()
    return {"filename": filename, "base64": audio_b64}

@app.post("/api/workflows")
async def create_workflow(config: WorkflowConfig):
    """创建新工作流配置"""
    config_file = WORKFLOW_CONFIGS_DIR / f"{config.model_name}.json"

    if config_file.exists():
        raise HTTPException(status_code=400, detail="工作流配置已存在")

    config_dict = config.model_dump()
    if config.route is None:
        config_dict["route"] = {
            "path": f"/{config.model_name}/generate",
            "methods": ["POST"]
        }

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)

    return {"message": "工作流配置创建成功", "filename": config_file.name}

@app.put("/api/workflows/{model_name}")
async def update_workflow(model_name: str, config: WorkflowConfig):
    """更新工作流配置"""
    config_file = WORKFLOW_CONFIGS_DIR / f"{model_name}.json"

    if not config_file.exists():
        raise HTTPException(status_code=404, detail="工作流配置不存在")

    config_dict = config.model_dump()
    if config.route is None:
        config_dict["route"] = {
            "path": f"/{config.model_name}/generate",
            "methods": ["POST"]
        }

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)

    return {"message": "工作流配置更新成功"}

@app.delete("/api/workflows/{model_name}")
async def delete_workflow(model_name: str):
    """删除工作流配置"""
    config_file = WORKFLOW_CONFIGS_DIR / f"{model_name}.json"

    if not config_file.exists():
        raise HTTPException(status_code=404, detail="工作流配置不存在")

    config_file.unlink()

    return {"message": "工作流配置删除成功"}

@app.post("/api/generate")
async def generate_video(request: GenerateRequest):
    """生成视频 - 加入任务队列"""
    task_id = str(uuid.uuid4())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tasks (id, model, data, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
    """, (task_id, request.model, json.dumps(request.data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

    return {
        "task_id": task_id,
        "status": "queued",
        "message": "任务已加入队列，请通过任务ID查询进度"
    }

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, model, status, progress, result, error, created_at, started_at, completed_at
        FROM tasks WHERE id = ?
    """, (task_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 读取 ComfyUI 实时进度（如果有）
    comfyui_progress = None
    progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
    if progress_file.exists():
        try:
            with open(progress_file, "r") as f:
                comfyui_progress = sanitize_progress_data(json.load(f))
        except Exception:
            pass

    result_data = {
        "task_id": row["id"],
        "model": row["model"],
        "status": row["status"],
        "progress": row["progress"],
        "result": json.loads(row["result"]) if row["result"] else None,
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"]
    }

    # 如果有 ComfyUI 实时进度，合并到返回数据
    if comfyui_progress:
        result_data["comfyui_progress"] = comfyui_progress

    return result_data

@app.get("/api/tasks")
async def list_tasks(limit: int = Query(default=50, le=200)):
    """获取任务队列"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, model, data, status, progress, error, created_at
        FROM tasks
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

    tasks = []
    progress_dir = Path("/tmp/comfyui_progress")
    for row in rows:
        # 从 data 中提取 prompt 作为摘要
        prompt_summary = ''
        try:
            task_data = json.loads(row["data"]) if row["data"] else {}
            prompt_summary = (task_data.get("prompt", "") or "")[:100]
        except Exception:
            pass

        task_item = {
            "task_id": row["id"],
            "model": row["model"],
            "prompt": prompt_summary,
            "status": row["status"],
            "progress": row["progress"],
            "error": (row["error"] or "")[:500],  # 截断错误信息，避免响应过大
            "created_at": row["created_at"]
        }

        # 如果有 ComfyUI 实时进度，合并到返回数据
        progress_file = progress_dir / f"{row['id']}.json"
        if progress_file.exists():
            try:
                with open(progress_file, "r") as f:
                    comfyui_progress = sanitize_progress_data(json.load(f))
                    task_item["comfyui_progress"] = comfyui_progress
            except Exception:
                pass

        tasks.append(task_item)

    return {"tasks": tasks}

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务"""
    conn = get_db()
    cursor = conn.cursor()

    # 先查询任务状态
    cursor.execute("""
        SELECT status FROM tasks WHERE id = ?
    """, (task_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")

    current_status = row["status"]

    # 只允许取消 pending/queued/processing 状态的任务
    if current_status not in ['pending', 'queued', 'processing']:
        conn.close()
        raise HTTPException(status_code=400, detail=f"无法取消状态为 {current_status} 的任务")

    # 更新任务状态为 cancelled
    cursor.execute("""
        UPDATE tasks
        SET status = 'cancelled', completed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()

    return {"message": "任务已取消", "task_id": task_id}


@app.post("/api/tasks/sync")
async def sync_from_comfyui():
    """从 ComfyUI 队列同步任务到 WebUI"""
    imported = 0
    updated = 0

    # 1. 查询 ComfyUI 队列
    try:
        resp = requests.get(f"{API_BASE_URL}/../queue", timeout=10)
        # 直接查 ComfyUI 而非 proxy
        resp = requests.get("http://localhost:8188/queue", timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"无法连接 ComfyUI: {resp.status_code}")
        queue_data = resp.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"无法连接 ComfyUI: {str(e)}")

    running = queue_data.get("queue_running", [])
    pending = queue_data.get("queue_pending", [])

    # 2. 读取所有进度文件，建立 prompt_id → task_id 映射
    progress_dir = Path("/tmp/comfyui_progress")
    prompt_to_task = {}
    if progress_dir.exists():
        for pf in progress_dir.glob("*.json"):
            try:
                with open(pf, "r") as f:
                    pdata = sanitize_progress_data(json.load(f))
                pid = pdata.get("prompt_id")
                tid = pdata.get("task_id")
                if pid and tid:
                    prompt_to_task[pid] = tid
            except Exception:
                pass

    # 3. 也扫描 DB 中所有任务，找 prompt_id 映射（从 result 中）
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, result, status FROM tasks")
    db_tasks = {row["id"]: row for row in cursor.fetchall()}

    # 4. 遍历 ComfyUI 队列，导入/更新任务
    all_comfyui_prompts = {}
    for item in running:
        if isinstance(item, list) and len(item) > 0:
            # ComfyUI queue format: [prompt_uuid, index, workflow_dict, ...]
            prompt_uuid = item[0] if isinstance(item[0], str) else (item[1] if len(item) > 1 and isinstance(item[1], str) else None)
            if prompt_uuid:
                all_comfyui_prompts[prompt_uuid] = "processing"
    for item in pending:
        if isinstance(item, list) and len(item) > 0:
            prompt_uuid = item[0] if isinstance(item[0], str) else (item[1] if len(item) > 1 and isinstance(item[1], str) else None)
            if prompt_uuid:
                all_comfyui_prompts[prompt_uuid] = "pending"

    for prompt_id, comfyui_status in all_comfyui_prompts.items():
        task_id = prompt_to_task.get(prompt_id)

        if task_id and task_id in db_tasks:
            # 已有对应任务，更新状态
            db_status = db_tasks[task_id]["status"]
            if db_status in ("pending", "queued", "processing"):
                new_status = "processing" if comfyui_status == "processing" else "queued"
                if db_status != new_status:
                    cursor.execute("""
                        UPDATE tasks SET status = ? WHERE id = ?
                    """, (new_status, task_id))
                    updated += 1
        else:
            # 新任务（从 ComfyUI 原生界面提交的），导入为外部任务
            new_task_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            status = "processing" if comfyui_status == "processing" else "queued"
            # 检测实际模型（从 proxy 获取可用模型列表）
            actual_model = "MiniMax_H3"
            try:
                proxy_resp = requests.get(f"{API_BASE_URL}/", timeout=5)
                if proxy_resp.status_code == 200:
                    models = proxy_resp.json().get("available_models", [])
                    if models:
                        actual_model = models[0]
            except Exception:
                pass
            cursor.execute("""
                INSERT INTO tasks (id, model, data, status, progress, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
            """, (new_task_id, actual_model, json.dumps({"prompt_id": prompt_id, "source": "comfyui_sync"}), status, now))

            # 创建进度文件，以便后续轮询
            if progress_dir.exists() or True:
                progress_dir.mkdir(exist_ok=True)
                with open(progress_dir / f"{new_task_id}.json", "w") as f:
                    json.dump({
                        "task_id": new_task_id,
                        "prompt_id": prompt_id,
                        "total_nodes": 0,
                        "status": comfyui_status,
                        "started_at": time.time(),
                        "model_name": "MiniMax_H3",
                        "request_data": {},
                        "comfyui_status": comfyui_status,
                    }, f)

            imported += 1

    conn.commit()
    conn.close()

    return {"imported": imported, "updated": updated, "total_in_queue": len(all_comfyui_prompts)}


@app.get("/api/comfyui-queue")
async def comfyui_queue():
    """直接查询 ComfyUI 的实时队列，并匹配我们的任务信息"""
    try:
        resp = requests.get("http://localhost:8188/queue", timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"ComfyUI 返回 {resp.status_code}")
        queue_data = resp.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"无法连接 ComfyUI: {str(e)}")

    running_raw = queue_data.get("queue_running", [])
    pending_raw = queue_data.get("queue_pending", [])

    # 构建 prompt_id → task 信息映射（从进度文件 + DB）
    prompt_to_info = {}

    # 从进度文件获取映射
    progress_dir = Path("/tmp/comfyui_progress")
    if progress_dir.exists():
        for pf in progress_dir.glob("*.json"):
            try:
                with open(pf, "r") as f:
                    pdata = sanitize_progress_data(json.load(f))
                pid = pdata.get("prompt_id")
                if pid and isinstance(pid, str):
                    prompt_to_info[pid] = {
                        "task_id": pdata.get("task_id"),
                        "model_name": pdata.get("model_name"),
                        "comfyui_percent": pdata.get("comfyui_percent"),
                        "comfyui_current": pdata.get("comfyui_current"),
                        "comfyui_total": pdata.get("comfyui_total"),
                    }
            except Exception:
                pass

    # 从 DB 补充（prompt 文本等）
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, model, data, status FROM tasks")
        for row in cursor.fetchall():
            task_data = json.loads(row["data"]) if row["data"] else {}
            # 尝试从 data 中找 prompt_id
            pid = task_data.get("prompt_id")
            if pid:
                if pid in prompt_to_info:
                    prompt_to_info[pid]["prompt_text"] = (task_data.get("prompt", "") or "")[:100]
                else:
                    prompt_to_info[pid] = {
                        "task_id": row["id"],
                        "model_name": row["model"],
                        "prompt_text": (task_data.get("prompt", "") or "")[:100],
                    }
        conn.close()
    except Exception:
        pass

    def extract_prompt_uuid(item):
        """从 ComfyUI 队列项中提取 prompt UUID"""
        if not isinstance(item, list) or len(item) == 0:
            return None
        # 格式可能是 [uuid, index, ...] 或 [index, uuid, ...]
        for elem in item[:3]:
            if isinstance(elem, str) and len(elem) > 20 and '-' in elem:
                return elem
        return None

    def format_item(item, status, position=0):
        prompt_uuid = extract_prompt_uuid(item)
        info = prompt_to_info.get(prompt_uuid, {}) if prompt_uuid else {}

        result = {
            "prompt_id": prompt_uuid,
            "comfyui_status": status,
            "position": position,
            "task_id": info.get("task_id"),
            "model": info.get("model_name", "Unknown"),
            "prompt": info.get("prompt_text", ""),
            "comfyui_percent": info.get("comfyui_percent"),
            "comfyui_current": info.get("comfyui_current"),
            "comfyui_total": info.get("comfyui_total"),
        }
        return result

    running = []
    for idx, item in enumerate(running_raw):
        running.append(format_item(item, "running", idx + 1))

    pending = []
    for idx, item in enumerate(pending_raw):
        pending.append(format_item(item, "queued", idx + 1))

    return {
        "running": running,
        "pending": pending,
        "total": len(running) + len(pending),
    }


@app.get("/api/status")
async def api_status():
    """检查 API 服务状态"""
    try:
        response = requests.get(f"{API_BASE_URL}/", timeout=5)
        if response.status_code == 200:
            return {
                "api_status": "ok",
                "api_url": API_BASE_URL,
                "available_models": response.json().get("available_models", [])
            }
        else:
            return {
                "api_status": "error",
                "api_url": API_BASE_URL,
                "message": f"API 服务返回 {response.status_code}"
            }
    except Exception as e:
        return {
            "api_status": "error",
            "api_url": API_BASE_URL,
            "message": str(e)
        }

@app.get("/api/comfyui-health")
async def comfyui_health():
    """检查 ComfyUI 是否可用（用于前端显示友好提示）"""
    try:
        resp = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "available": True,
                "version": data.get("version", "unknown"),
                "message": "ComfyUI 运行正常"
            }
        else:
            return {
                "available": False,
                "version": None,
                "message": "ComfyUI 正在启动中，请稍候..."
            }
    except requests.exceptions.ConnectionError:
        return {
            "available": False,
            "version": None,
            "message": "ComfyUI 代理服务未启动"
        }
    except Exception as e:
        return {
            "available": False,
            "version": None,
            "message": f"检查失败: {str(e)}"
        }

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件（图片/音频）并保存到 MMX_ComfyUI-input 目录"""
    content = await file.read()
    b64_content = base64.b64encode(content).decode()

    # 生成唯一文件名避免冲突
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{timestamp}_{file.filename}"

    # 保存到 ComfyUI input 目录
    target_file = COMFYUI_INPUT_DIR / filename
    with open(target_file, "wb") as f:
        f.write(content)

    # 同时保存到临时目录（兼容旧逻辑）
    temp_file = UPLOAD_DIR / filename
    with open(temp_file, "wb") as f:
        f.write(content)

    print(f"✅ 文件已保存: {target_file}")

    return {
        "filename": filename,
        "filepath": str(target_file),
        "content_type": file.content_type,
        "size": len(content),
        "base64": b64_content
    }

# ========== 文件浏览器 API ==========

@app.get("/api/browser/list")
async def browser_list(path: str = "", offset: int = 0, limit: int = 100):
    """浏览文件目录，列出文件和子目录（支持分页）"""
    try:
        full_path = resolve_safe_path(path)

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="目录不存在")

        if not full_path.is_dir():
            raise HTTPException(status_code=400, detail="路径不是目录")

        # 读取目录内容
        all_items = []
        for item in full_path.iterdir():
            try:
                item_info = {
                    "name": item.name,
                    "path": str(item.relative_to(COMFYUI_INPUT_DIR)),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                    "modified": item.stat().st_mtime
                }

                # 判断文件类型
                if item.is_file():
                    suffix = item.suffix.lower()
                    if suffix in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                        item_info['type'] = 'image'
                    elif suffix in ['.mp3', '.wav', '.ogg', '.flac', '.aac']:
                        item_info['type'] = 'audio'
                    elif suffix in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
                        item_info['type'] = 'video'
                    else:
                        item_info['type'] = 'file'

                all_items.append(item_info)
            except OSError:
                # 跳过无法访问的文件
                continue

        # 排序：目录在前，然后按名称排序
        all_items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

        # 分页
        total = len(all_items)
        items = all_items[offset:offset + limit]

        return {
            "current_path": path,
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取目录失败: {str(e)}")

@app.get("/api/browser/file")
async def browser_file(path: str):
    """获取文件内容（直接返回二进制，用于预览）"""
    try:
        full_path = resolve_safe_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        # 判断 MIME 类型
        suffix = full_path.suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.bmp': 'image/bmp',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.ogg': 'audio/ogg',
            '.flac': 'audio/flac',
            '.aac': 'audio/aac',
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska',
            '.webm': 'video/webm'
        }
        mime_type = mime_map.get(suffix, 'application/octet-stream')

        # 直接返回二进制内容，不用 base64 编码（节省 33% 带宽）
        with open(full_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type=mime_type,
            headers={
                'Content-Disposition': make_content_disposition(full_path.name),
                'Cache-Control': 'public, max-age=300'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")

@app.get("/api/browser/file-base64")
async def browser_file_base64(path: str):
    """获取文件内容（返回 base64，用于提交到 ComfyUI）"""
    try:
        full_path = resolve_safe_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        suffix = full_path.suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp',
            '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
            '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.aac': 'audio/aac'
        }
        mime_type = mime_map.get(suffix, 'application/octet-stream')

        with open(full_path, "rb") as f:
            content = f.read()

        return {
            "filename": full_path.name,
            "path": path,
            "mime_type": mime_type,
            "size": len(content),
            "base64": base64.b64encode(content).decode()
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")

# ========== 视频缩略图 API ==========

@app.get("/api/browser/video-thumbnail/{path:path}")
async def get_video_thumbnail(path: str):
    """生成视频缩略图（提取第一帧）"""
    try:
        full_path = resolve_safe_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        suffix = full_path.suffix.lower()
        if suffix not in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
            raise HTTPException(status_code=400, detail="不是视频文件")

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run([
                'ffmpeg', '-i', str(full_path),
                '-vframes', '1',
                '-q:v', '2',
                tmp_path,
                '-y'
            ], capture_output=True, check=True, timeout=10)

            with open(tmp_path, 'rb') as f:
                thumbnail_data = f.read()

            return Response(
                content=thumbnail_data,
                media_type='image/jpeg',
                headers={'Cache-Control': 'public, max-age=3600'}
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="视频处理失败")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="视频处理超时")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成缩略图失败: {str(e)}")

# ========== 图片缩略图 API ==========

@app.get("/api/browser/image-thumbnail/{path:path}")
async def get_image_thumbnail(path: str, size: int = Query(default=300, le=800)):
    """生成图片缩略图"""
    try:
        full_path = resolve_safe_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        suffix = full_path.suffix.lower()
        if suffix not in ['.png', '.jpg', '.jpeg', '.webp', '.bmp']:
            raise HTTPException(status_code=400, detail="不是图片文件")

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run([
                'ffmpeg', '-i', str(full_path),
                '-vf', f'scale={size}:-1',
                '-q:v', '3',
                tmp_path,
                '-y'
            ], capture_output=True, check=True, timeout=10)

            with open(tmp_path, 'rb') as f:
                thumbnail_data = f.read()

            return Response(
                content=thumbnail_data,
                media_type='image/jpeg',
                headers={'Cache-Control': 'public, max-age=3600'}
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="图片处理失败")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="视频处理超时")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成缩略图失败: {str(e)}")

# ========== 历史记录 API ==========

def resolve_output_path(relative_path: str) -> Path:
    """解析输出目录下的路径并确保安全（允许访问整个输出目录）"""
    base = COMFYUI_OUTPUT_DIR.resolve()
    if relative_path:
        full_path = (COMFYUI_OUTPUT_DIR / relative_path).resolve()
    else:
        full_path = base

    if not str(full_path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="访问被拒绝：路径超出允许范围")

    return full_path

@app.get("/api/history/list")
async def history_list(path: str = "", offset: int = 0, limit: int = 50):
    """浏览历史生成记录目录（支持分页）"""
    try:
        full_path = resolve_output_path(path)

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="目录不存在")

        if not full_path.is_dir():
            raise HTTPException(status_code=400, detail="路径不是目录")

        all_items = []
        for item in full_path.iterdir():
            try:
                stat = item.stat()
                item_info = {
                    "name": item.name,
                    "path": str(item.relative_to(COMFYUI_OUTPUT_DIR)),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else 0,
                    "modified": stat.st_mtime
                }

                if item.is_file():
                    suffix = item.suffix.lower()
                    if suffix in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
                        item_info['type'] = 'video'
                    elif suffix in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                        item_info['type'] = 'image'
                    elif suffix in ['.json']:
                        item_info['type'] = 'metadata'
                    else:
                        item_info['type'] = 'file'

                all_items.append(item_info)
            except OSError:
                continue

        # 排序：目录在前，文件按修改时间倒序（最新的在前）
        all_items.sort(key=lambda x: (not x['is_dir'], -x.get('modified', 0)))

        total = len(all_items)
        items = all_items[offset:offset + limit]

        return {
            "current_path": path,
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "base_dir": str(COMFYUI_OUTPUT_DIR)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取历史目录失败: {str(e)}")

@app.get("/api/history/video/{path:path}")
async def history_video(path: str):
    """直接提供输出目录下的视频文件"""
    try:
        full_path = resolve_output_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        suffix = full_path.suffix.lower()
        mime_map = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska',
            '.webm': 'video/webm'
        }
        mime_type = mime_map.get(suffix, 'application/octet-stream')

        return Response(
            content=full_path.read_bytes(),
            media_type=mime_type,
            headers={
                'Content-Disposition': make_content_disposition(full_path.name),
                'Cache-Control': 'public, max-age=300'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取视频失败: {str(e)}")

@app.get("/api/history/video-thumbnail/{path:path}")
async def history_video_thumbnail(path: str):
    """为输出目录下的视频生成缩略图"""
    try:
        full_path = resolve_output_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run([
                'ffmpeg', '-i', str(full_path),
                '-vframes', '1',
                '-q:v', '2',
                tmp_path,
                '-y'
            ], capture_output=True, check=True, timeout=10)

            with open(tmp_path, 'rb') as f:
                thumbnail_data = f.read()

            return Response(
                content=thumbnail_data,
                media_type='image/jpeg',
                headers={'Cache-Control': 'public, max-age=3600'}
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="视频处理失败")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="视频处理超时")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成缩略图失败: {str(e)}")

@app.get("/api/history/image/{path:path}")
async def history_image(path: str):
    """直接提供输出目录下的图片文件"""
    try:
        full_path = resolve_output_path(path)

        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        suffix = full_path.suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp'
        }
        mime_type = mime_map.get(suffix, 'application/octet-stream')

        return Response(
            content=full_path.read_bytes(),
            media_type=mime_type,
            headers={
                'Content-Disposition': make_content_disposition(full_path.name),
                'Cache-Control': 'public, max-age=300'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取图片失败: {str(e)}")

# ========== 历史记录删除 ==========

@app.delete("/api/history/file")
async def delete_history_file(path: str):
    """删除输出目录下的文件"""
    try:
        full_path = resolve_output_path(path)

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")

        if full_path.is_dir():
            # 递归删除目录
            shutil.rmtree(full_path)
            return {"message": f"目录已删除: {path}"}
        else:
            full_path.unlink()
            return {"message": f"文件已删除: {path}"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

# ========== 保存路径设置 ==========

SETTINGS_FILE = Path("/root/webui/settings.json")

def load_settings() -> dict:
    """加载设置"""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"output_subdir": ""}

def save_settings(settings: dict):
    """保存设置"""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

@app.get("/api/settings/output-path")
async def get_output_path():
    """获取当前输出子目录（相对于 Haimi 目录）"""
    settings = load_settings()
    subdir = settings.get("output_subdir", "")
    full_path = str(HAIMI_OUTPUT_DIR / subdir) if subdir else str(HAIMI_OUTPUT_DIR)
    return {
        "output_subdir": subdir,
        "output_path": full_path,
        "base_dir": str(HAIMI_OUTPUT_DIR)
    }

@app.put("/api/settings/output-path")
async def set_output_path(data: dict):
    """设置输出子目录（相对于 HAIMI_OUTPUT_DIR，只能设置 Haimi 下的子目录）"""
    subdir = data.get("output_subdir", "")

    # 验证路径安全性 - 必须在 HAIMI_OUTPUT_DIR 范围内
    if subdir:
        test_path = (HAIMI_OUTPUT_DIR / subdir).resolve()
        if not str(test_path).startswith(str(HAIMI_OUTPUT_DIR.resolve())):
            raise HTTPException(status_code=403, detail="路径超出允许范围（只能在 Haimi 目录下）")
        if not test_path.exists():
            test_path.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    settings["output_subdir"] = subdir
    save_settings(settings)

    full_path = str(HAIMI_OUTPUT_DIR / subdir) if subdir else str(HAIMI_OUTPUT_DIR)
    return {
        "message": "保存路径已更新",
        "output_subdir": subdir,
        "output_path": full_path
    }

# ========== 配置预设管理 ==========

PRESETS_FILE = Path("/root/webui/config_presets.json")

def load_presets() -> dict:
    """加载配置预设"""
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_presets(presets: dict):
    """保存配置预设"""
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2, ensure_ascii=False)

@app.get("/api/presets")
async def get_presets():
    """获取所有配置预设"""
    presets = load_presets()
    return {
        "presets": [
            {"name": name, "config": config, "created_at": config.get("created_at", "")}
            for name, config in presets.items()
        ]
    }

@app.post("/api/presets")
async def save_preset(data: dict):
    """保存配置预设"""
    name = data.get("name", "").strip()
    config = data.get("config", {})

    if not name:
        raise HTTPException(status_code=400, detail="预设名称不能为空")

    # 添加创建时间
    config["created_at"] = datetime.now().isoformat()

    presets = load_presets()
    presets[name] = config
    save_presets(presets)

    return {"message": f"预设 '{name}' 已保存", "name": name}

@app.delete("/api/presets/{name}")
async def delete_preset(name: str):
    """删除配置预设"""
    presets = load_presets()

    if name not in presets:
        raise HTTPException(status_code=404, detail="预设不存在")

    del presets[name]
    save_presets(presets)

    return {"message": f"预设 '{name}' 已删除"}

import logging

# 配置日志过滤器，减少重复的任务状态查询日志
class TaskPollingFilter(logging.Filter):
    def filter(self, record):
        # 过滤掉频繁的任务状态查询日志
        if hasattr(record, 'getMessage'):
            msg = record.getMessage()
            if 'GET /api/tasks/' in msg and 'HTTP/1.1' in msg:
                return False
        return True

# 应用日志过滤器
logging.getLogger("uvicorn.access").addFilter(TaskPollingFilter())

if __name__ == "__main__":
    print("=" * 60)
    print(f"🚀 ComfyUI WebUI v{__version__} 启动中...")
    print(f"📡 WebUI 地址: http://0.0.0.0:8025")
    print(f"🔌 API 服务: {API_BASE_URL}")
    print(f"📁 工作流配置: {WORKFLOW_CONFIGS_DIR}")
    print(f"📁 输出目录: {COMFYUI_OUTPUT_DIR}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8025, log_config=None)
