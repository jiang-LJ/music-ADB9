#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jwx音乐文件筛查工具 - 公共工具模块
存放常量、枚举、数据类和与业务无关的通用函数
"""

import hashlib
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

# ============ 常量定义 ============

# 时间容差：2秒内的时间差异视为相同（解决文件系统精度问题）
TIME_TOLERANCE = 2.0

# MD5计算分块大小（8MB，优化大文件性能）
MD5_CHUNK_SIZE = 8 * 1024 * 1024

# 批量插入批次大小
BATCH_SIZE = 500


# ============ 枚举类型 ============

class ChangeStatus(Enum):
    """文件变更状态"""
    NEW = "new"           # 新增
    MODIFIED = "modified" # 修改(大小/时间变化)
    UNCHANGED = "unchanged" # 未变更
    DELETED = "deleted"   # 删除
    MOVED = "moved"       # 移动(路径变化但哈希相同)


class CompareMethod(Enum):
    """文件比较方法"""
    SIZE_TIME = "size_time"  # 通过大小+修改时间比较
    HASH = "hash"            # 通过 MD5 哈希比较


class ScanType(Enum):
    """扫描类型"""
    FULL = "full"         # 全新扫描
    INCREMENTAL = "incremental" # 增量扫描
    RESUME = "resume"     # 继续任务
    COMPARE = "compare"   # 对比模式


# ============ 数据类 ============

@dataclass
class FileState:
    """文件状态记录"""
    path: str
    folder_type: str  # 'A' 或 'B'
    name: str
    size: int
    modified_time: float
    md5_hash: Optional[str] = None
    duration: Optional[float] = None
    change_status: ChangeStatus = ChangeStatus.NEW
    first_seen: Optional[str] = None
    last_scan: Optional[str] = None


@dataclass
class TaskRecord:
    """任务记录"""
    task_id: str
    task_name: str
    folder_a: str
    folder_b: str
    created_at: str
    updated_at: str
    scan_count: int = 0
    total_files_a: int = 0
    total_files_b: int = 0
    status: str = "active"


def get_app_dir() -> Path:
    """
    获取应用程序目录。
    - 开发环境：返回当前脚本所在目录
    - PyInstaller 打包后：返回 exe 所在目录（确保数据库在 exe 同级）
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 等打包环境
        return Path(sys.executable).parent.resolve()
    else:
        try:
            return Path(__file__).parent.resolve()
        except NameError:
            return Path.cwd()


# ============ 模块级工具函数 ============

def compute_md5(filepath: str) -> Optional[str]:
    """
    分块计算文件MD5（模块级函数，可被扫描流程直接调用）

    Args:
        filepath: 文件路径

    Returns:
        MD5哈希值（十六进制字符串），失败返回None
    """
    if not os.path.isfile(filepath):
        return None

    try:
        hash_md5 = hashlib.md5(usedforsecurity=False)
        with open(filepath, 'rb') as f:
            # 使用传统while循环（兼容Python 3.7+）
            while True:
                chunk = f.read(MD5_CHUNK_SIZE)
                if not chunk:
                    break
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except (PermissionError, OSError, IOError) as e:
        print(f"计算MD5失败 {filepath}: {e}")
        return None


def get_audio_duration(filepath: str) -> Optional[float]:
    """获取音频文件时长（秒），失败返回 None。优先 tinytag，fallback mutagen。"""
    try:
        from tinytag import TinyTag
        tag = TinyTag.get(filepath)
        return tag.duration
    except Exception:
        pass
    try:
        from mutagen import File
        audio = File(filepath)
        if audio is not None and audio.info is not None:
            return audio.info.length
    except Exception:
        pass
    return None


def get_audio_tags(filepath: str) -> dict:
    """
    读取音频文件的 title 和 artist 标签。
    优先 tinytag，fallback mutagen。

    Returns:
        {'title': str or None, 'artist': str or None}
    """
    try:
        from tinytag import TinyTag
        tag = TinyTag.get(filepath)
        return {'title': tag.title, 'artist': tag.artist}
    except Exception:
        pass
    try:
        from mutagen import File
        from mutagen.mp3 import MP3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4
        audio = File(filepath)
        if audio is None:
            return {'title': None, 'artist': None}

        title = None
        artist = None

        if isinstance(audio, MP3):
            title = audio.get('TIT2', [None])[0] if 'TIT2' in audio else None
            artist = audio.get('TPE1', [None])[0] if 'TPE1' in audio else None
        elif isinstance(audio, FLAC) or isinstance(audio, OggVorbis):
            title = audio.get('title', [None])[0] if audio.get('title') else None
            artist = audio.get('artist', [None])[0] if audio.get('artist') else None
        elif isinstance(audio, MP4):
            title = audio.get('\xa9nam', [None])[0] if '\xa9nam' in audio else None
            artist = audio.get('\xa9ART', [None])[0] if '\xa9ART' in audio else None
        else:
            # 通用 fallback
            title = audio.get('title', None)
            artist = audio.get('artist', None)

        return {'title': title, 'artist': artist}
    except Exception:
        pass
    return {'title': None, 'artist': None}


def format_duration(seconds: Optional[float]) -> str:
    """将秒数格式化为 M:SS，None 返回 '--'"""
    if seconds is None or seconds < 0:
        return '--'
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"
