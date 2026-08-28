"""
ComfyUI WebUI 版本信息管理

版本号规则（语义化版本 Semantic Versioning）:
- MAJOR（主版本）: 重大功能变更或不兼容更改
- MINOR（次版本）: 向后兼容的新功能
- PATCH（修订版）: bug修复和小改进

版本历史:
- 1.0.0: 初始版本（基础工作流管理）
- 1.1.0: 添加任务队列、进度显示、历史记录持久化
- 1.2.0: 添加文件浏览器、预置资源管理、连续任务提交、配置保存
- 1.3.0: UI重构，视频缩略图，历史管理，保存路径自定义
- 1.4.0: 性能优化版 — HTTP连接池、SQLite WAL、视频流式传输、缩略图缓存、动态轮询
- 1.5.0: 素材管理升级 — 文件管理（预览/添加到槽位/添加到提示词）、音频裁剪、
         素材去重、视频槽位缩略图、默认预置素材、优雅 OOM 提示、输出路径修复
"""

# 当前版本号
__version__ = "1.5.0"

# 版本构建时间
__build_time__ = "2026-08-28"

# Git提交哈希
__git_commit__ = None

# 版本别名
__release_stage__ = "release"


def get_version_info() -> dict:
    """获取完整的版本信息字典"""
    return {
        "version": __version__,
        "build_time": __build_time__,
        "git_commit": __git_commit__,
        "release_stage": __release_stage__,
        "display_version": _format_display_version()
    }


def _format_display_version() -> str:
    """格式化显示的版本字符串"""
    version_str = f"v{__version__}"
    if __release_stage__ and __release_stage__ != "release":
        version_str += f"-{__release_stage__}"
    if __git_commit__:
        version_str += f" (commit: {__git_commit__[:8]})"
    elif __build_time__:
        version_str += f" (build: {__build_time__})"
    return version_str
