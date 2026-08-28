#!/usr/bin/env python3
"""
ComfyUI 多工作流 API 代理服务器
基于配置文件驱动，支持动态加载多个工作流
"""

import base64
import copy
import io
import json
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="ComfyUI Multi-Workflow API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COMFYUI_URL = "http://localhost:8188"
WORKFLOW_CONFIGS_DIR = Path(__file__).parent / "workflow_configs"

# ── 性能优化：HTTP 连接池，复用 TCP 连接 ──
_retry_strategy = Retry(total=2, backoff_factor=0.3, status_forcelist=[502, 503, 504])
_http_adapter = HTTPAdapter(max_retries=_retry_strategy, pool_connections=10, pool_maxsize=20)
HTTP_SESSION = requests.Session()
HTTP_SESSION.mount("http://", _http_adapter)
HTTP_SESSION.mount("https://", _http_adapter)


class WorkflowExecutor:
    """通用工作流执行引擎"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_name = config["model_name"]
        self.workflow_path = config["workflow_path"]
        self.inputs_config = config["inputs"]
        self.output_config = config["output"]
        # ── 性能优化：workflow 缓存 ──
        self._cached_workflow: Optional[Dict] = None
        self._cache_mtime: float = 0

    def load_workflow(self) -> Dict:
        """加载工作流 JSON（带文件修改时间缓存，避免重复读盘）"""
        try:
            mtime = os.path.getmtime(self.workflow_path)
        except OSError:
            mtime = 0
        if self._cached_workflow is None or mtime != self._cache_mtime:
            with open(self.workflow_path, "r", encoding="utf-8") as f:
                self._cached_workflow = json.load(f)
            self._cache_mtime = mtime
        return self._cached_workflow
    
    def detect_format(self, data: bytes) -> tuple:
        """检测文件类型"""
        if data[:3] == b"\xff\xd8\xff":
            return ".jpg", "image/jpeg"
        elif data[:4] == b"\x89PNG":
            return ".png", "image/png"
        elif data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
            return ".webp", "image/webp"
        elif data[:4] == b"RIFF":
            return ".wav", "audio/wav"
        elif data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
            return ".mp3", "audio/mpeg"
        elif data[4:8] == b"ftyp":
            return ".mp4", "video/mp4"
        return ".bin", "application/octet-stream"
    
    def upload_file(self, data: bytes, filename_hint: str) -> str:
        """上传文件到 ComfyUI（overwrite=true，相同文件名会覆盖而非新增）"""
        ext, mime = self.detect_format(data)
        name = f"{filename_hint}{ext}"

        response = HTTP_SESSION.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (name, io.BytesIO(data), mime)},
            data={"type": "input", "overwrite": "true"},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"上传失败: {response.text}")

        return response.json().get("name", name)

    def _content_hash(self, data: bytes) -> str:
        """计算文件内容的短哈希，用于去重（相同内容 → 相同文件名）"""
        import hashlib
        return hashlib.md5(data).hexdigest()[:12]

    def process_inputs(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理输入参数，上传文件并返回映射值"""
        processed = {}

        for input_name, input_config in self.inputs_config.items():
            if input_name not in request_data:
                if input_config.get("required", False):
                    raise ValueError(f"缺少必需参数: {input_name}")
                continue

            value = request_data[input_name]
            input_type = input_config.get("type")

            # 处理文件上传
            if input_type in ["image", "file"]:
                file_data = None

                if isinstance(value, str):
                    if value.startswith("data:"):
                        # Base64 数据带前缀
                        header, encoded = value.split(",", 1)
                        file_data = base64.b64decode(encoded)
                    else:
                        # 尝试解码为 base64
                        try:
                            file_data = base64.b64decode(value)
                        except Exception:
                            # 如果解码失败，当作文件名
                            processed[input_name] = value
                            continue

                if file_data:
                    # 使用内容哈希作为文件名，相同内容复用同一文件（避免重复）
                    content_hash = self._content_hash(file_data)
                    filename = self.upload_file(file_data, f"api_{input_name}_{content_hash}")
                    processed[input_name] = filename

            elif input_type == "list":
                # 列表类型（如多张图片）
                if not isinstance(value, list):
                    raise ValueError(f"{input_name} 必须是列表")

                max_count = input_config.get("max_count")
                if max_count and len(value) > max_count:
                    value = value[:max_count]

                filenames = []
                for i, item in enumerate(value):
                    file_data = None

                    if isinstance(item, str):
                        if item.startswith("data:"):
                            header, encoded = item.split(",", 1)
                            file_data = base64.b64decode(encoded)
                        else:
                            try:
                                file_data = base64.b64decode(item)
                            except Exception:
                                filenames.append(item)
                                continue

                    if file_data:
                        # 使用内容哈希作为文件名，相同内容复用同一文件（避免重复）
                        content_hash = self._content_hash(file_data)
                        filename = self.upload_file(file_data, f"api_{input_name}_{i}_{content_hash}")
                        filenames.append(filename)
                    elif isinstance(item, str):
                        filenames.append(item)
                
                processed[input_name] = filenames
            
            else:
                # 普通类型（string, number, boolean）
                processed[input_name] = value
        
        return processed
    
    def modify_workflow(self, workflow: Dict, processed_inputs: Dict[str, Any]) -> Dict:
        """根据配置修改工作流参数"""
        modified = copy.deepcopy(workflow)  # 性能优化：用 copy.deepcopy 替代 json 序列化
        nodes = modified.get("nodes", [])

        # 性能优化：预先构建 node_id → node 字典，避免 O(n*m) 嵌套循环
        node_map = {node.get("id"): node for node in nodes}

        for input_name, input_config in self.inputs_config.items():
            if input_name not in processed_inputs:
                continue

            value = processed_inputs[input_name]
            mappings = input_config.get("mappings", [])

            for mapping in mappings:
                node_id = mapping["node_id"]
                input_field = mapping["input_name"]
                value_type = mapping.get("value_type", "direct")

                # O(1) 字典查找替代线性扫描
                target_node = node_map.get(node_id)
                if not target_node:
                    continue

                # 计算正确的 widget 索引：只计算有 widget 的 inputs，跳过无 widget 的输入（如 AUDIO/MODEL 等连接类型）
                widgets_values = target_node.get("widgets_values", [])
                widget_idx = 0
                node_inputs = target_node.get("inputs", [])
                widget_count = 0
                for i, inp in enumerate(node_inputs):
                    if inp.get("widget") is not None:
                        if inp.get("name") == input_field:
                            widget_idx = widget_count
                            break
                        widget_count += 1

                # 根据类型设置值
                if value_type == "filename":
                    # 文件名类型（用于 LoadImage/LoadAudio）
                    if isinstance(value, list):
                        # 列表类型，需要找到对应的索引
                        # 这里简化处理：假设 mapping 按顺序对应列表项
                        # 实际使用时需要更精确的映射
                        idx = mappings.index(mapping)
                        if idx < len(value):
                            if widget_idx < len(widgets_values):
                                widgets_values[widget_idx] = value[idx]
                    else:
                        # 单个文件
                        if widget_idx < len(widgets_values):
                            widgets_values[widget_idx] = value

                elif value_type == "text":
                    # 文本类型
                    if widget_idx < len(widgets_values):
                        widgets_values[widget_idx] = value

                elif value_type == "number":
                    # 数字类型
                    if widget_idx < len(widgets_values):
                        widgets_values[widget_idx] = float(value)

        # 强制输出到 Haimi/ 子目录：修改 SaveVideo 节点 (id=92) 的 filename_prefix
        save_node = node_map.get(92)
        if save_node and save_node.get("type") == "SaveVideo":
            wv = save_node.get("widgets_values", [])
            if wv:
                current_prefix = str(wv[0]).strip() if wv[0] else ""
                # 确保在 Haimi/ 子目录下
                if current_prefix.startswith("Haimi/"):
                    pass  # 已有 Haimi/ 前缀
                elif current_prefix:
                    wv[0] = f"Haimi/{current_prefix}"
                else:
                    wv[0] = "Haimi/MiniMax_H3"

        return modified
    
    def frontend_to_api(self, workflow: Dict) -> Dict:
        """将前端格式转换为 ComfyUI API 格式
        
        关键修复：widgets_values 按顺序对应所有带 widget 字段的 input
        无论该 input 是否有 link，widget_idx 都要递增
        """
        api = {}
        nodes = workflow.get("nodes", [])
        links = workflow.get("links", [])
        
        # 构建 link 映射: link_id -> (from_node_id, from_slot)
        link_source = {}
        for link in links:
            if len(link) >= 5:
                link_id, from_node, from_slot, to_node, to_slot = link[:5]
                link_source[link_id] = (from_node, from_slot)
        
        # 跳过的节点类型
        SKIP_TYPES = {"MarkdownNote", "Note", "Reroute", "PrimitiveBoolean"}
        
        for node in nodes:
            node_type = node.get("type", "")
            
            # 跳过注释和非处理节点
            if node_type in SKIP_TYPES:
                continue
            
            node_id = str(node.get("id"))
            
            # 跳过被禁用的节点
            if node.get("mode") == 2:
                continue
            
            api_node = {
                "class_type": node_type,
                "_meta": {"title": node.get("title", "")}
            }
            
            inputs = {}
            widgets_values = node.get("widgets_values", [])
            widget_idx = 0
            
            # 遍历节点的所有 inputs
            for inp in node.get("inputs", []):
                name = inp.get("name")
                if not name:
                    continue
                
                link_id = inp.get("link")
                has_widget = inp.get("widget") is not None
                
                # 关键修复：带 widget 字段的 input 无论有无 link 都递增 widget_idx
                if has_widget:
                    if widget_idx < len(widgets_values):
                        widget_val = widgets_values[widget_idx]
                        widget_idx += 1
                    else:
                        widget_val = None
                else:
                    widget_val = None
                
                if link_id is not None and link_id in link_source:
                    # Link 输入优先：引用其他节点
                    from_node, from_slot = link_source[link_id]
                    inputs[name] = [str(from_node), from_slot]
                elif widget_val is not None:
                    # 没有 link，使用 widget 值
                    inputs[name] = widget_val
            
            api_node["inputs"] = inputs
            api[node_id] = api_node
        
        return api
    
    def queue_prompt(self, api_prompt: Dict) -> str:
        """提交工作流到 ComfyUI"""
        response = HTTP_SESSION.post(
            f"{COMFYUI_URL}/prompt",
            json={"prompt": api_prompt},
            timeout=30
        )
        if response.status_code != 200:
            raise Exception(f"提交失败: {response.text}")

        return response.json().get("prompt_id")

    def save_comfyui_progress(self, task_id: str, prompt_id: str, total_nodes: int):
        """保存 ComfyUI 任务进度信息，供 WebUI 查询"""
        progress_dir = Path("/tmp/comfyui_progress")
        progress_dir.mkdir(exist_ok=True)
        progress_file = progress_dir / f"{task_id}.json"
        data = {
            "task_id": task_id,
            "prompt_id": prompt_id,
            "total_nodes": total_nodes,
            "comfyui_status": "queued",
            "started_at": time.time()
        }
        with open(progress_file, "w") as f:
            json.dump(data, f)

    def update_comfyui_progress(self, task_id: str, **kwargs):
        """更新 ComfyUI 任务进度"""
        progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
        if not progress_file.exists():
            return
        try:
            with open(progress_file, "r") as f:
                data = json.load(f)
            data.update(kwargs)
            with open(progress_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def cleanup_comfyui_progress(self, task_id: str):
        """清理 ComfyUI 任务进度文件"""
        progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
        try:
            if progress_file.exists():
                progress_file.unlink()
        except Exception:
            pass
    
    def detect_oom_error(self, error_text: str) -> bool:
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
        ]
        error_lower = error_text.lower()
        return any(ind.lower() in error_lower for ind in oom_indicators)

    def format_oom_hint(self, error_text: str = "") -> str:
        """生成 OOM 错误的用户友好提示，从原始错误中提取显存信息"""
        import re as _re
        # 提取显存数据
        allocated = _re.search(r'Currently allocated\s*:\s*([\d.]+\s*\w+)', error_text)
        requested = _re.search(r'Requested\s*:\s*([\d.]+\s*\w+)', error_text)
        device_limit = _re.search(r'Device limit\s*:\s*([\d.]+\s*\w+)', error_text)

        mem_info = ""
        if allocated and device_limit:
            mem_info = f"\n当前占用 {allocated.group(1)} / {device_limit.group(1)}"
            if requested:
                mem_info += f"，还需 {requested.group(1)}"

        return (
            "💥 显存不足！请尝试以下操作：\n"
            "① 降低「分辨率质量」（建议 0.3~0.5）\n"
            "② 缩短「视频时长」（建议 ≤ 10 秒）\n"
            "③ 切换更小的画面比例（如 1:1 或 4:3）\n"
            "④ 减少参考图片数量"
            f"{mem_info}"
        )

    def wait_for_completion(self, prompt_id: str, timeout: int = 900, task_id: str = "") -> Dict:
        """等待任务完成，同时更新 ComfyUI 队列状态"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = HTTP_SESSION.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
                if resp.status_code == 200:
                    history = resp.json()
                    if prompt_id in history:
                        result = history[prompt_id]
                        status = result.get("status", {})
                        if status.get("completed") or status.get("status_str") == "success":
                            return result
                        elif status.get("status_str") == "error":
                            error_msgs = status.get("messages", [])
                            error_text = str(error_msgs) if error_msgs else str(status)
                            if self.detect_oom_error(error_text):
                                raise Exception(f"OOM_ERROR: {self.format_oom_hint(error_text)}")
                            raise Exception(f"任务失败: {error_text}")
                # 查询 ComfyUI 队列，更新任务在队列中的状态
                if task_id:
                    self._update_comfyui_queue_status(task_id, prompt_id)
            except requests.exceptions.RequestException:
                pass

            time.sleep(3)

        raise Exception(f"超时（{timeout}秒）")

    def _update_comfyui_queue_status(self, task_id: str, prompt_id: str):
        """查询 ComfyUI /queue 和 /progress 更新任务状态和进度百分比"""
        try:
            resp = HTTP_SESSION.get(f"{COMFYUI_URL}/queue", timeout=5)
            if resp.status_code != 200:
                return
            queue_data = resp.json()
            running = queue_data.get("queue_running", [])
            pending = queue_data.get("queue_pending", [])

            is_running = False
            is_queued = False
            queue_position = 0

            for idx, item in enumerate(running):
                if isinstance(item, list) and len(item) > 0:
                    item_prompt = item[0] if isinstance(item[0], str) else (item[1] if len(item) > 1 and isinstance(item[1], str) else None)
                    if item_prompt == prompt_id:
                        is_running = True
                        break
            if not is_running:
                for idx, item in enumerate(pending):
                    if isinstance(item, list) and len(item) > 0:
                        item_prompt = item[0] if isinstance(item[0], str) else (item[1] if len(item) > 1 and isinstance(item[1], str) else None)
                        if item_prompt == prompt_id:
                            is_queued = True
                            queue_position = idx + 1
                            break

            if is_running:
                # 查询节点级进度百分比
                try:
                    prog_resp = HTTP_SESSION.get(f"{COMFYUI_URL}/progress/{prompt_id}", timeout=5)
                    if prog_resp.status_code == 200:
                        prog_data = prog_resp.json()
                        max_nodes = prog_data.get("max", 0)
                        cur_nodes = prog_data.get("value", 0)
                        if max_nodes > 0:
                            percent = round(cur_nodes / max_nodes * 100, 1)
                            self.update_comfyui_progress(
                                task_id,
                                comfyui_status="running",
                                comfyui_percent=percent,
                                comfyui_current=cur_nodes,
                                comfyui_total=max_nodes
                            )
                            return
                except Exception:
                    pass
                self.update_comfyui_progress(task_id, comfyui_status="running")
            elif is_queued:
                self.update_comfyui_progress(task_id, comfyui_status="queued", comfyui_position=queue_position)
        except Exception:
            pass
    
    def download_output(self, filename: str, subfolder: str = "") -> bytes:
        """下载输出文件"""
        params = {"filename": filename, "type": "output"}
        if subfolder:
            params["subfolder"] = subfolder
        
        response = HTTP_SESSION.get(f"{COMFYUI_URL}/view", params=params, timeout=120)
        if response.status_code != 200:
            raise Exception(f"下载失败: {response.text}")
        
        return response.content
    
    def extract_output(self, result: Dict) -> tuple:
        """从结果中提取输出文件
        
        修复：同时支持 gifs/images/videos 字段，按文件扩展名判断视频
        """
        outputs = result.get("outputs", {})
        node_id = str(self.output_config.get("node_id"))
        
        if node_id not in outputs:
            raise Exception(f"未找到输出节点 {node_id}")
        
        node_output = outputs[node_id]
        
        # 尝试多个可能的字段（按优先级）
        possible_fields = self.output_config.get("possible_fields", ["videos", "gifs", "images"])
        valid_extensions = self.output_config.get("file_extensions", [".mp4", ".webm", ".mov", ".gif"])
        
        for field in possible_fields:
            if field not in node_output:
                continue
            
            items = node_output[field]
            for item in items:
                filename = item.get("filename", "")
                
                # 检查文件扩展名
                if any(filename.lower().endswith(ext) for ext in valid_extensions):
                    subfolder = item.get("subfolder", "")
                    return filename, subfolder
        
        # 如果找不到符合扩展名的文件，尝试返回第一个文件
        for field in possible_fields:
            if field in node_output and node_output[field]:
                item = node_output[field][0]
                filename = item.get("filename", "")
                if filename:
                    subfolder = item.get("subfolder", "")
                    return filename, subfolder
        
        raise Exception(f"未找到有效的输出文件（尝试了字段: {possible_fields}）")
    
    async def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行完整流程（同步等待完成）"""
        task_id = request_data.get("_task_id", "")
        try:
            # 1. 加载工作流
            workflow = self.load_workflow()

            # 2. 处理输入
            processed_inputs = self.process_inputs(request_data)

            # 3. 修改工作流
            modified_workflow = self.modify_workflow(workflow, processed_inputs)

            # 4. 转换为 API 格式
            api_prompt = self.frontend_to_api(modified_workflow)

            # 5. 提交到 ComfyUI
            prompt_id = self.queue_prompt(api_prompt)

            # 保存 ComfyUI 进度信息（供 WebUI 查询）
            total_nodes = len(api_prompt)
            if task_id:
                self.save_comfyui_progress(task_id, prompt_id, total_nodes)

            try:
                # 6. 等待完成
                result = self.wait_for_completion(prompt_id, task_id=task_id)
            finally:
                # 清理进度文件
                if task_id:
                    self.cleanup_comfyui_progress(task_id)

            # 7. 提取输出
            filename, subfolder = self.extract_output(result)

            # 8. 计算目标保存路径
            base_dir = Path("/mnt/storage/MMX_ComfyUI-output/Haimi")

            # 解析 filename_prefix：支持 / 或 \ 作为目录分隔符
            filename_prefix = request_data.get("filename_prefix", "").strip()
            prefix_subdir = ""
            prefix_name = ""
            if filename_prefix:
                normalized = filename_prefix.replace("\\", "/")
                parts = normalized.split("/")
                non_empty = [p for p in parts if p]
                if non_empty:
                    prefix_name = non_empty[-1]
                    prefix_subdir = "/".join(non_empty[:-1])

            output_dir = base_dir
            if prefix_subdir:
                output_dir = output_dir / prefix_subdir
            output_dir.mkdir(parents=True, exist_ok=True)

            # ComfyUI 实际输出路径（SaveVideo 节点已直接保存到这里）
            comfyui_output_path = Path("/mnt/storage/MMX_ComfyUI-output") / subfolder / filename if subfolder else Path("/mnt/storage/MMX_ComfyUI-output") / filename

            # 9. 获取文件数据：如果 ComfyUI 已保存到目标目录，直接读取，避免重复保存
            ext = Path(filename).suffix or ".mp4"
            if comfyui_output_path.exists() and comfyui_output_path.parent == output_dir:
                # ComfyUI 已经保存到目标目录，直接使用
                with open(comfyui_output_path, 'rb') as f:
                    file_data = f.read()
                output_filename = comfyui_output_path.name
                output_path = comfyui_output_path
                print(f"✅ 使用 ComfyUI 已保存的文件: {output_path}")
            else:
                # ComfyUI 保存到了其他位置，需要下载并另存
                file_data = self.download_output(filename, subfolder)

                if prefix_name:
                    existing = [f.name for f in output_dir.iterdir() if f.is_file() and f.name.startswith(prefix_name)]
                    max_num = 0
                    _num_pattern = re.compile(r'(\d+)' + re.escape(ext) + '$')
                    for f_name in existing:
                        m = _num_pattern.search(f_name)
                        if m:
                            max_num = max(max_num, int(m.group(1)))
                    output_filename = f"{prefix_name}{max_num + 1:03d}{ext}"
                else:
                    custom_filename = request_data.get("output_filename", "")
                    if custom_filename:
                        if not custom_filename.endswith(('.mp4', '.webm', '.mov', '.gif')):
                            custom_filename = f"{custom_filename}{ext}"
                        output_filename = custom_filename
                    else:
                        output_filename = filename

                output_path = output_dir / output_filename
                with open(output_path, 'wb') as f:
                    f.write(file_data)
                print(f"✅ 视频已保存到: {output_path}")

            # 10. 返回 base64
            file_b64 = base64.b64encode(file_data).decode()
            
            return {
                "model": self.model_name,
                "video": file_b64,
                "filename": output_filename,
                "output_path": str(output_path),
                "size": len(file_data),
                "success": True,
                "message": "ok"
            }
        
        except Exception as e:
            traceback.print_exc()
            raise Exception(str(e))

    async def submit_only(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """只提交任务到 ComfyUI 队列，不等待完成，立即返回 prompt_id"""
        task_id = request_data.get("_task_id", "")
        try:
            workflow = self.load_workflow()
            processed_inputs = self.process_inputs(request_data)
            modified_workflow = self.modify_workflow(workflow, processed_inputs)
            api_prompt = self.frontend_to_api(modified_workflow)
            prompt_id = self.queue_prompt(api_prompt)

            total_nodes = len(api_prompt)
            if task_id:
                self.save_comfyui_progress(task_id, prompt_id, total_nodes)
                # 保存请求数据到进度文件，供 collect_result 使用（剥离 base64 大字段）
                progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
                with open(progress_file, "r") as f:
                    pdata = json.load(f)
                # 只保留小字段（prompt、duration 等），剥离 base64 数据（images/audio/video）
                slim_request_data = {k: v for k, v in request_data.items()
                                     if k not in ("images", "audio", "video")}
                pdata["request_data"] = slim_request_data
                pdata["model_name"] = self.model_name
                with open(progress_file, "w") as f:
                    json.dump(pdata, f)

            return {
                "prompt_id": prompt_id,
                "task_id": task_id,
                "total_nodes": total_nodes,
                "success": True,
                "message": "已提交到 ComfyUI 队列"
            }
        except Exception as e:
            traceback.print_exc()
            raise Exception(str(e))

    def collect_result(self, task_id: str) -> Dict[str, Any]:
        """检查任务是否完成（快速返回）。完成时启动后台下载。"""
        progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
        if not progress_file.exists():
            return {"status": "not_found", "task_id": task_id}

        try:
            with open(progress_file, "r") as f:
                pdata = json.load(f)

            # 如果已经标记为 completed（下载中/已完成），直接返回
            if pdata.get("comfyui_status") == "completed":
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "filename": pdata.get("output_filename", ""),
                    "output_path": pdata.get("output_path", ""),
                    "size": pdata.get("output_size", 0),
                    "downloading": pdata.get("downloading", False),
                }
            if pdata.get("comfyui_status") == "failed":
                return {
                    "status": "failed",
                    "task_id": task_id,
                    "error": pdata.get("error", "未知错误"),
                }

            prompt_id = pdata.get("prompt_id")
            request_data = pdata.get("request_data", {})
            model_name = pdata.get("model_name", self.model_name)

            if not prompt_id:
                return {"status": "error", "task_id": task_id, "error": "缺少 prompt_id"}

            # 更新队列状态
            self._update_comfyui_queue_status(task_id, prompt_id)

            # 检查 ComfyUI history
            try:
                resp = HTTP_SESSION.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
                if resp.status_code == 200:
                    history = resp.json()
                    if prompt_id in history:
                        result = history[prompt_id]
                        status = result.get("status", {})
                        if status.get("completed") or status.get("status_str") == "success":
                            # 立即标记完成，后台下载
                            return self._mark_completed_and_download(task_id, result, request_data, model_name)
                        elif status.get("status_str") == "error":
                            error_msgs = status.get("messages", [])
                            error_text = str(error_msgs) if error_msgs else str(status)
                            # 标记失败到进度文件
                            with open(progress_file, "r") as f:
                                pdata = json.load(f)
                            pdata["comfyui_status"] = "failed"
                            if self.detect_oom_error(error_text):
                                pdata["error"] = f"OOM_ERROR: {self.format_oom_hint(error_text)}"
                            else:
                                pdata["error"] = f"任务失败: {error_text}"
                            with open(progress_file, "w") as f:
                                json.dump(pdata, f)
                            return {"status": "failed", "task_id": task_id, "error": pdata["error"]}
            except requests.exceptions.RequestException:
                pass

            # 任务还在进行中
            with open(progress_file, "r") as f:
                pdata = json.load(f)
            return {
                "status": pdata.get("comfyui_status", "running"),
                "task_id": task_id,
                "prompt_id": prompt_id,
                "comfyui_progress": {
                    "comfyui_status": pdata.get("comfyui_status"),
                    "comfyui_percent": pdata.get("comfyui_percent"),
                    "comfyui_current": pdata.get("comfyui_current"),
                    "comfyui_total": pdata.get("comfyui_total"),
                    "comfyui_position": pdata.get("comfyui_position"),
                }
            }
        except Exception as e:
            return {"status": "error", "task_id": task_id, "error": str(e)}

    def _mark_completed_and_download(self, task_id, result, request_data, model_name):
        """标记任务完成（快速），然后在后台线程中下载视频"""

        progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"

        # 先提取输出文件名（快速操作）
        try:
            filename, subfolder = self.extract_output(result)
        except Exception as e:
            with open(progress_file, "r") as f:
                pdata = json.load(f)
            pdata["comfyui_status"] = "failed"
            pdata["error"] = f"提取输出失败: {str(e)}"
            with open(progress_file, "w") as f:
                json.dump(pdata, f)
            return {"status": "failed", "task_id": task_id, "error": pdata["error"]}

        # 计算输出路径
        base_dir = Path("/mnt/storage/MMX_ComfyUI-output/Haimi")
        filename_prefix = request_data.get("filename_prefix", "").strip()
        prefix_subdir = ""
        prefix_name = ""
        if filename_prefix:
            normalized = filename_prefix.replace("\\", "/")
            parts = normalized.split("/")
            non_empty = [p for p in parts if p]
            if non_empty:
                prefix_name = non_empty[-1]
                prefix_subdir = "/".join(non_empty[:-1])

        output_dir = base_dir
        if prefix_subdir:
            output_dir = output_dir / prefix_subdir
        output_dir.mkdir(parents=True, exist_ok=True)

        # ComfyUI 实际输出路径（SaveVideo 节点已直接保存到这里）
        comfyui_output_path = Path("/mnt/storage/MMX_ComfyUI-output") / subfolder / filename if subfolder else Path("/mnt/storage/MMX_ComfyUI-output") / filename

        ext = Path(filename).suffix or ".mp4"
        # 如果 ComfyUI 已保存到目标目录，直接使用，不再重复下载
        if comfyui_output_path.exists() and comfyui_output_path.parent == output_dir:
            output_filename = comfyui_output_path.name
            output_path = comfyui_output_path
            file_size = comfyui_output_path.stat().st_size
            need_download = False
        else:
            if prefix_name:
                existing = [f.name for f in output_dir.iterdir() if f.is_file() and f.name.startswith(prefix_name)]
                max_num = 0
                _num_pattern = re.compile(r'(\d+)' + re.escape(ext) + '$')
                for f_name in existing:
                    m = _num_pattern.search(f_name)
                    if m:
                        max_num = max(max_num, int(m.group(1)))
                output_filename = f"{prefix_name}{max_num + 1:03d}{ext}"
            else:
                custom_filename = request_data.get("output_filename", "")
                if custom_filename:
                    if not custom_filename.endswith(('.mp4', '.webm', '.mov', '.gif')):
                        custom_filename = f"{custom_filename}{ext}"
                    output_filename = custom_filename
                else:
                    output_filename = filename
            output_path = output_dir / output_filename
            file_size = 0
            need_download = True

        relative_path = str(output_path.relative_to(Path("/mnt/storage/MMX_ComfyUI-output")))

        # 立即标记完成，启动后台下载（仅在需要时）
        with open(progress_file, "r") as f:
            pdata = json.load(f)
        pdata["comfyui_status"] = "completed"
        pdata["output_filename"] = output_filename
        pdata["output_path"] = relative_path
        pdata["downloading"] = need_download
        with open(progress_file, "w") as f:
            json.dump(pdata, f)

        if not need_download:
            # ComfyUI 已保存，无需下载
            with open(progress_file, "r") as f:
                pdata = json.load(f)
            pdata["downloading"] = False
            pdata["output_size"] = file_size
            with open(progress_file, "w") as f:
                json.dump(pdata, f)
            print(f"✅ 使用 ComfyUI 已保存的文件: {output_path}")
        else:
            # 后台线程下载
            def _download_in_background():
                try:
                    file_data = self.download_output(filename, subfolder)
                    with open(output_path, 'wb') as f:
                        f.write(file_data)
                    print(f"✅ 视频已保存到: {output_path}")

                    # 更新进度文件：下载完成
                    with open(progress_file, "r") as f:
                        pdata = json.load(f)
                    pdata["downloading"] = False
                    pdata["output_size"] = len(file_data)
                    with open(progress_file, "w") as f:
                        json.dump(pdata, f)
                except Exception as e:
                    print(f"❌ 后台下载失败: {task_id} - {e}")
                    with open(progress_file, "r") as f:
                        pdata = json.load(f)
                    pdata["downloading"] = False
                    pdata["download_error"] = str(e)
                    with open(progress_file, "w") as f:
                        json.dump(pdata, f)

            t = threading.Thread(target=_download_in_background, daemon=True)
            t.start()

        return {
            "status": "completed",
            "task_id": task_id,
            "filename": output_filename,
            "output_path": relative_path,
            "downloading": need_download,
            "size": file_size if not need_download else 0,
        }

    def _save_completed_result(self, task_id, result, request_data, model_name):
        """提取输出、下载、保存到磁盘、返回 base64"""
        try:
            filename, subfolder = self.extract_output(result)
            file_data = self.download_output(filename, subfolder)

            base_dir = Path("/mnt/storage/MMX_ComfyUI-output/Haimi")
            filename_prefix = request_data.get("filename_prefix", "").strip()
            prefix_subdir = ""
            prefix_name = ""
            if filename_prefix:
                normalized = filename_prefix.replace("\\", "/")
                parts = normalized.split("/")
                non_empty = [p for p in parts if p]
                if non_empty:
                    prefix_name = non_empty[-1]
                    prefix_subdir = "/".join(non_empty[:-1])

            output_dir = base_dir
            if prefix_subdir:
                output_dir = output_dir / prefix_subdir
            output_dir.mkdir(parents=True, exist_ok=True)

            ext = Path(filename).suffix or ".mp4"
            if prefix_name:
                existing = [f.name for f in output_dir.iterdir() if f.is_file() and f.name.startswith(prefix_name)]
                max_num = 0
                _num_pattern = re.compile(r'(\d+)' + re.escape(ext) + '$')
                for f_name in existing:
                    m = _num_pattern.search(f_name)
                    if m:
                        max_num = max(max_num, int(m.group(1)))
                output_filename = f"{prefix_name}{max_num + 1:03d}{ext}"
            else:
                custom_filename = request_data.get("output_filename", "")
                if custom_filename:
                    if not custom_filename.endswith(('.mp4', '.webm', '.mov', '.gif')):
                        custom_filename = f"{custom_filename}{ext}"
                    output_filename = custom_filename
                else:
                    output_filename = filename

            output_path = output_dir / output_filename
            with open(output_path, 'wb') as f:
                f.write(file_data)
            print(f"✅ 视频已保存到: {output_path}")

            file_b64 = base64.b64encode(file_data).decode()
            self.cleanup_comfyui_progress(task_id)

            return {
                "status": "completed",
                "task_id": task_id,
                "model": model_name,
                "video": file_b64,
                "filename": output_filename,
                "output_path": str(output_path),
                "size": len(file_data),
                "success": True,
            }
        except Exception as e:
            self.cleanup_comfyui_progress(task_id)
            return {"status": "failed", "task_id": task_id, "error": f"下载/保存失败: {str(e)}"}


# 全局工作流执行器注册表
workflow_executors: Dict[str, WorkflowExecutor] = {}


def load_workflow_configs():
    """加载所有工作流配置"""
    global workflow_executors
    
    if not WORKFLOW_CONFIGS_DIR.exists():
        print(f"⚠️ 配置目录不存在: {WORKFLOW_CONFIGS_DIR}")
        return
    
    for config_file in WORKFLOW_CONFIGS_DIR.glob("*.json"):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            model_name = config["model_name"]
            executor = WorkflowExecutor(config)
            workflow_executors[model_name] = executor
            print(f"✅ 加载工作流: {model_name} -> {config_file.name}")
        
        except Exception as e:
            print(f"❌ 加载配置失败 {config_file.name}: {e}")


@app.on_event("startup")
async def startup():
    """启动时加载所有工作流配置"""
    load_workflow_configs()


@app.get("/")
async def health():
    """健康检查，列出所有可用模型"""
    return {
        "status": "ok",
        "version": "2.1.0",
        "available_models": list(workflow_executors.keys())
    }


# 动态路由：为每个工作流生成 /{model_name}/generate 路由
# 由于 FastAPI 不支持完全动态路由，我们使用一个通用端点，通过 model 参数分发

class GenerateRequest(BaseModel):
    model: str
    data: Dict[str, Any]


@app.post("/generate")
async def generate(req: GenerateRequest):
    """通用生成接口（同步等待完成）"""
    model_name = req.model

    if model_name not in workflow_executors:
        raise HTTPException(
            status_code=404,
            detail=f"模型 {model_name} 不存在。可用模型: {list(workflow_executors.keys())}"
        )

    executor = workflow_executors[model_name]

    try:
        result = await executor.execute(req.data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/submit")
async def submit(req: GenerateRequest):
    """提交任务到 ComfyUI 队列（不等待完成，立即返回 prompt_id）"""
    model_name = req.model

    if model_name not in workflow_executors:
        raise HTTPException(
            status_code=404,
            detail=f"模型 {model_name} 不存在。可用模型: {list(workflow_executors.keys())}"
        )

    executor = workflow_executors[model_name]

    try:
        result = await executor.submit_only(req.data)
        return result
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="COMFYUI_NOT_AVAILABLE: ComfyUI 服务未启动，请等待 ComfyUI 启动后任务会自动提交"
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail="COMFYUI_TIMEOUT: ComfyUI 响应超时，可能正在启动中，请稍后重试"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """检查 ComfyUI 是否可用"""
    try:
        resp = HTTP_SESSION.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "comfyui": "ok",
                "version": data.get("system", {}).get("comfyui_version", "unknown"),
            }
    except requests.exceptions.ConnectionError:
        pass
    except Exception:
        pass
    return JSONResponse(
        status_code=503,
        content={"comfyui": "unavailable", "message": "ComfyUI 服务未启动"}
    )


@app.get("/result/{task_id}")
async def get_result(task_id: str):
    """轮询任务结果：检查任务是否完成，完成则返回视频数据"""
    # 遍历所有 executor 找到对应的（因为进度文件里记录了 model_name）
    progress_file = Path("/tmp/comfyui_progress") / f"{task_id}.json"
    if not progress_file.exists():
        raise HTTPException(status_code=404, detail="任务进度文件不存在")

    try:
        with open(progress_file, "r") as f:
            pdata = json.load(f)
        model_name = pdata.get("model_name", "")
    except Exception:
        model_name = ""

    if model_name and model_name in workflow_executors:
        executor = workflow_executors[model_name]
    elif workflow_executors:
        # 默认使用第一个 executor
        executor = list(workflow_executors.values())[0]
    else:
        raise HTTPException(status_code=500, detail="没有可用的工作流执行器")

    result = executor.collect_result(task_id)
    return result


# 为常用模型创建快捷路由
@app.post("/MiniMax_H3/generate")
async def generate_minimax_h3(data: Dict[str, Any]):
    """MiniMax_H3 快捷路由"""
    if "MiniMax_H3" not in workflow_executors:
        raise HTTPException(status_code=404, detail="MiniMax_H3 模型未加载")
    
    executor = workflow_executors["MiniMax_H3"]
    
    try:
        result = await executor.execute(data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    print("🚀 启动 ComfyUI 多工作流 API 服务")
    uvicorn.run(app, host="0.0.0.0", port=8026)
