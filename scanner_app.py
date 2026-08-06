#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jwx音乐文件筛查工具 - Tkinter 主界面
"""

import os
import sys
import json
import random
import re
import shutil
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List

# Windows 回收站支持
import ctypes
from ctypes import wintypes

from utils import ChangeStatus, ScanType, FileState, compute_md5, get_app_dir, get_audio_duration, get_audio_tags, format_duration
from task_manager import TaskManager
from scanner_core import find_duplicates, find_similar, find_approximate
import ai_analyzer


def _generate_blank_ico() -> str:
    """生成一个 1x1 透明 ICO 到系统临时目录，返回路径"""
    import struct
    import tempfile

    # ICO header: Reserved(2), Type(1=icon), Count(1)
    header = struct.pack('<HHH', 0, 1, 1)
    # Entry: Width, Height, Colors, Reserved, Planes, BitCount, SizeInBytes, Offset
    entry = struct.pack('<BBBBHHII', 1, 1, 0, 0, 1, 32, 40 + 4 + 4, 22)
    # BITMAPINFOHEADER
    bmp_header = struct.pack('<IIIHHIIIIII', 40, 1, 2, 1, 32, 0, 0, 0, 0, 0, 0)
    # XOR mask (1 pixel, transparent) + AND mask
    xor_data = b'\x00\x00\x00\x00'
    and_data = b'\x80\x00\x00\x00'

    fd, path = tempfile.mkstemp(suffix='.ico')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(header + entry + bmp_header + xor_data + and_data)
    except Exception:
        os.close(fd)
        raise
    return path


class Tooltip:
    """通用的鼠标悬停提示组件"""
    def __init__(self, widget, text, delay=1000):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tipwindow = None
        self.id = None
        self.widget.bind("<Enter>", self.on_enter)
        self.widget.bind("<Leave>", self.on_leave)

    def on_enter(self, event=None):
        self.schedule()

    def on_leave(self, event=None):
        self.unschedule()
        self.hide()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.delay, self.show)

    def unschedule(self):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def show(self):
        if self.tipwindow:
            return
        x = self.widget.winfo_pointerx() + 10
        y = self.widget.winfo_pointery() + 10
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#1f2937", foreground="#f3f4f6",
                         relief=tk.SOLID, borderwidth=1,
                         font=("Segoe UI", 9), padx=6, pady=4)
        label.pack(ipadx=1)

    def hide(self):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


class SHFILEOPSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_trash(paths: List[str]) -> bool:
    """将文件移动到 Windows 回收站"""
    if not paths:
        return True
    # pFrom 需要以双空字符结尾的字符串
    joined = '\0'.join(paths) + '\0\0'
    struct = SHFILEOPSTRUCT()
    struct.hwnd = None
    struct.wFunc = 3  # FO_DELETE
    struct.pFrom = joined
    struct.pTo = None
    struct.fFlags = 0x40 | 0x10  # FOF_ALLOWUNDO | FOF_NOCONFIRMATION
    struct.fAnyOperationsAborted = False
    struct.hNameMappings = None
    struct.lpszProgressTitle = None

    SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
    SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCT)]
    SHFileOperationW.restype = wintypes.INT
    ret = SHFileOperationW(ctypes.byref(struct))
    return ret == 0


HELP_CONTENT = """
平台限制
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 仅支持 64 位 Windows 10 / Windows 11 系统
• 本软件为绿色便携版，无需安装 Python 或任何额外依赖
• 将整个 ABD9 文件夹复制到任意位置（包括 U 盘或其它电脑），直接运行 ABD9.exe 即可使用
• 退出前自动保存当前任务和扫描设置，下次打开自动恢复

软件的作用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 用于快速筛查两个音乐文件夹（文件夹 A 和文件夹 B）中的重复文件、相似文件、近似文件以及文件变更情况
• 适合整理个人曲库、清理冗余备份、比对主库与备份盘差异等场景

四种文件定义
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【重复文件】内容完全相同的文件
  • 优先按 MD5 哈希值匹配（开启「计算 MD5」时）
  • 未开启 MD5 时，按「文件名（不区分大小写）+ 文件大小」匹配
  • 同一组中的文件可以安全删除其中任意一份

【相似文件】文件名相同但内容可能不同的文件
  • 同名同后缀但大小不同（如两个大小不同的 song.mp3）
  • 同名不同后缀（如 song.mp3 与 song.flac）
  • 通常是同一首歌的不同音质版本或不同来源的转码文件

【近似文件】文件名很相似且时长接近的文件
  • 比较文件名去掉后缀后的主干部分（如 "Love Story" 与 "Love_Story"）
  • 相似度达到设定阈值（默认 80%）即初步归为一组
  • 同时会对比两个文件的音频时长，若时长差异超过设定值（默认 98%）则排除
    ※ 仅当两个文件都能读取到有效时长时才进行时长过滤，任一无法读取则跳过
  • 不会与重复/相似文件重叠，三者互斥

【聚合去重】重复、相似、近似三类文件的总览（可统一去重处理）
  • 重复文件 + 相似文件 + 近似文件 = 聚合去重覆盖的文件总数
  • 三类互斥，不会重叠

功能与用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【任务管理】
  • 保存任务：创建一个新的扫描任务，保存文件夹路径和扫描历史
  • 加载任务：弹窗中以 Checkbutton 列表展示所有历史任务，可勾选多项；点击「加载选中」加载单个任务（多选会提示仅允许一项），点击「删除选中」可批量删除选中的历史任务
  • 导入导出：将任务列表及扫描历史备份为 JSON 文件，方便迁移或恢复
     ※ 建议导出到本软件目录以外的文件夹，避免执行「恢复默认」时丢失已导出的备份
  • 恢复默认（需验证作者微信号）：
     1) 清空任务列表 — 仅删除所有已保存的任务记录，不清空当前路径和结果
     2) 软件恢复初始设置 — 清空所有任务、路径、结果和回收站记录，将软件完全恢复到初始状态

【扫描选项】
  • 扫描文件夹 A / B：可单独或同时扫描两个文件夹
    ※ 若取消勾选最后一个文件夹，会自动恢复为默认勾选状态（A和B都勾选）
  • 全新扫描 / 增量扫描（二选一，互斥）：
    - 全新扫描：扫描所有文件并建立新的历史基准（首次扫描必选）
    - 增量扫描：只扫描自上次以来发生变化的文件，速度更快
    ※ 勾选其一会自动取消另一个；若都取消，自动恢复为全新扫描
  • 快速模式 / 计算 MD5（二选一，互斥）：
    - 快速模式：通过文件大小和修改时间判断变化（推荐开启，速度最快）
    - 计算 MD5：通过文件内容哈希判断变化，更精确但大文件稍慢
    ※ 勾选其一会自动取消另一个；若都取消，自动恢复为快速模式
  • 检测移动：检测文件名或路径改变但内容完全相同的文件
    ※ 开启后会自动关闭快速模式并勾选计算 MD5
  • 相似度：设置近似文件匹配的宽松程度（80%～100%，默认 80%），值越低匹配越宽松
  • 时长值：设置近似检测中两个文件时长的最小重合比例（80%～100%，默认 98%）
    ※ 仅当两个文件都能读取到有效时长时才生效，用于排除时长差异过大的近似匹配

【开始扫描】
  1. 配置好文件夹路径和扫描选项后，点击「开始扫描」
  2. 弹出扫描预估窗口，显示预计扫描的文件数量和耗时
  3. 确认后进入进度窗口，扫描在后台线程执行，主界面不会卡死
  4. 扫描完成后弹出结果汇总弹窗，底部结果区自动展示重复/相似/近似/变更文件列表

【结果查看】
  • 底部双栏分别展示文件夹 A 和文件夹 B 的结果，滚动条位于两栏之间并自动同步
  • 每行显示：选择 ☑ / 序号 / 文件名 / 时长 / 大小 / 修改时间
    ※ 「时长」显示为 M:SS 格式（如 3:45），若无法读取则显示 --
  • 点击「选择」列（☐ / ☑）可勾选文件，勾选后显示为红色
  • 点击任意行，对侧相同位置的行会同步高亮为黄色，方便左右对照
  • 切换选中其它行或点击空白处，黄色高亮会自动清除，不会残留
  • 右键点击行可打开上下文菜单：打开文件位置、移动到回收站

【文件操作】
  • 移动到回收站：将勾选的文件（支持同时勾选 A 和 B 文件夹）移入软件目录下的 AppTrash 文件夹（应用内部回收站，并非 Windows 系统回收站），操作前需验证作者微信号；移入后可随时撤销
  • 撤销移动：将最近一次移入回收站的文件恢复至原来的路径

【结果视图切换】
  • 点击扫描结果概览区的彩色数字可切换视图：重复文件 / 相似文件 / 近似文件 / 聚合去重
  • 概览区各数字含义：
    - 待重命名：文件名不符合命名规范的文件数（点击打开重命名管理）
    - 重复文件：内容完全相同的文件总数
    - 相似文件：文件名相同但内容可能不同的文件总数
    - 近似文件：文件名相似且时长接近的文件总数
    - 聚合去重：重复/相似/近似三类覆盖的文件总数（点击切换聚合去重视图）
    - 扫描耗时：本次扫描所花费的时间
  • 文件夹统计区（🟢 新增 / 🟡 修改）：增量扫描下，与上次扫描基准相比的新增和修改文件数量；全新扫描时此处为 0

【聚合去重】
  • 将重复、相似、近似三类文件统一展示，每组前有类型分隔行
  • 在聚合去重视图下，智选去重、一键选A/B 对三类组全部生效
  • AI 分析：只分析相似/近似组（重复组内容相同无需 AI），分析后重复组自动按规则勾选冗余文件

【手动分析】
  • 一键选A / 一键选B：在当前视图（重复/相似/近似）中一键选中所有对应文件夹的文件
  • 智选去重：根据下方勾选的筛选条件，在每组中智能保留最优文件（权重：最大时长 > 最大文件 > 最新文件），其余勾选
  • 最大时长 / 最大文件 / 最新文件：智选去重的筛选条件，可单选或多选
  • 取消选择：清空所有已勾选的文件
  • 统计：实时显示当前已勾选文件的总数，以及 A 侧和 B 侧各勾选了多少个

【作者联系】
  • 微信：a_better_day_9
  • 点击界面上的微信号即可复制到剪贴板
"""


class MusicScannerWithTasks(tk.Tk):
    """支持任务记录的音乐文件扫描器"""

    def __init__(self):
        super().__init__()
        self.base_title = "ABD9音乐文件筛查器"
        self.title(self.base_title)
        self._blank_ico_path = _generate_blank_ico()
        # 打包后 music.ico 在 _internal 目录下，需要绝对路径
        if getattr(sys, 'frozen', False):
            icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
        else:
            icon_path = 'icon.ico'
        self.iconbitmap(icon_path)
        # 自动适配屏幕高度，避免被任务栏遮挡
        screen_h = self.winfo_screenheight()
        win_h = min(screen_h - 80, 980)
        self.geometry(f"1600x{win_h}")
        self.after_idle(lambda: self._remove_window_icon(self))
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 初始化任务管理器
        self.task_manager = TaskManager()
        self.current_task: Optional[object] = None
        self.scan_type = ScanType.FULL

        # 内部回收站（用于撤销删除）
        self.trash_dir = Path(get_app_dir()) / "AppTrash"
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.last_deleted_records: List[Dict[str, str]] = []

        # 颜色配置
        self.colors = {
            'bg': '#374151',      # 中灰背景
            'card': '#4b5563',     # 中深灰卡片
            'text': '#f3f4f6',     # 浅灰文字
            'accent': '#60a5fa',   # 柔和蓝色强调
            'accent2': '#93c5fd',  # 浅蓝强调
            'border': '#6b7280',   # 灰边框
        }

        # 路径变量
        self.path_a_var = tk.StringVar()
        self.path_b_var = tk.StringVar()

        # 并发线程数（用于读取时长/计算MD5，默认32，范围1~32）
        self.worker_count_var = tk.IntVar(value=32)

        # 智选去重筛选条件（默认全选）
        self.smart_use_duration = tk.BooleanVar(value=True)
        self.smart_use_size = tk.BooleanVar(value=True)
        self.smart_use_mtime = tk.BooleanVar(value=True)
        # 单侧去重：默认关闭，仅处理两侧都有的组
        self.smart_single_side = tk.BooleanVar(value=False)

        # 音频指纹验证（默认关闭）
        self.use_fingerprint = tk.BooleanVar(value=True)

        # AI 分析配置
        self.ai_config_path = get_app_dir() / "ai_config.json"
        self.ai_config: dict = {}
        self.file_tags: Dict[str, dict] = {}  # path -> {title, artist}
        self.ai_judgments: Optional[List[dict]] = None  # AI 分析结果（最新）
        self.ai_history: List[dict] = []  # AI 分析历史记录
        # 指纹缓存
        self.fingerprint_cache_path = get_app_dir() / "fingerprint_cache.json"
        self.fingerprint_cache: dict = {}
        self._load_fingerprint_cache()
        # 用户反馈学习（记录手动保留的文件）
        self.feedback_path = get_app_dir() / "user_feedback.json"
        self.user_feedback: set = set()
        self._load_feedback()
        # 读取学习：默认关闭，勾选后 AI 分析才应用学习记录
        self.use_learning = tk.BooleanVar(value=False)
        # 选live版：默认勾选，优先勾选 Live 版（保留原版）
        self.use_live_priority = tk.BooleanVar(value=True)
        # 重命名日志
        self.rename_log_path = get_app_dir() / "rename_log.json"
        self.rename_log: List[dict] = []
        self._load_rename_log()
        self._load_ai_config()

        # 扫描结果数据
        self.duplicate_groups = []
        self.similar_groups = []
        self.approximate_groups = []
        self.all_files_a = {}
        self.all_files_b = {}
        self.change_results = []
        self.checked_items = {}  # id(tree) -> set(iid)
        self._scan_diagnosis = {}  # folder_type -> 诊断信息（路径存在但扫描为空时用）

        # 线程锁和进度事件
        self._scan_state_lock = threading.Lock()
        self._scan_progress_event = threading.Event()
        self._scan_thread_abort = False

        self.setup_ui()
        self.state('zoomed')
        self._load_session()

    # ==================== UI 搭建 ====================

    def setup_ui(self):
        """设置UI界面 - V9截图版三栏布局 + 底部通栏结果区"""
        self.configure(bg=self.colors['bg'])

        # 主容器
        main_frame = tk.Frame(self, bg=self.colors['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 上半部分：横向三栏（左窄、中宽、右中）
        top_frame = tk.Frame(main_frame, bg=self.colors['bg'])
        top_frame.pack(fill=tk.X, expand=False, pady=(0, 5))

        top_frame.columnconfigure(0, weight=0)   # 左：任务管理
        top_frame.columnconfigure(1, weight=1)   # 中：当前任务+文件夹A+文件夹B
        top_frame.columnconfigure(2, weight=0, minsize=225)   # 操作（加宽50%）
        top_frame.columnconfigure(3, weight=0, minsize=300)   # 扫描结果概览
        top_frame.columnconfigure(4, weight=0, minsize=300)   # 手动分析（加宽）
        top_frame.columnconfigure(5, weight=0, minsize=170)   # AI 分析（与操作栏同宽）

        # 左栏：任务管理
        task_frame = tk.LabelFrame(top_frame, text="📋 任务管理",
                                   bg=self.colors['card'],
                                   fg=self.colors['text'],
                                   font=('Segoe UI', 11, 'bold'))
        task_frame.grid(row=0, column=0, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_task_panel(task_frame)

        # 中栏上：当前任务横条
        task_info_frame = tk.Frame(top_frame, bg=self.colors['card'])
        task_info_frame.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        self._fill_task_info_bar(task_info_frame)

        # 中栏中：文件夹 A
        folder_a_frame = tk.LabelFrame(top_frame, text="📁 文件夹 A",
                                       bg=self.colors['card'],
                                       fg=self.colors['text'],
                                       font=('Segoe UI', 11, 'bold'))
        folder_a_frame.grid(row=1, column=1, sticky='nsew', padx=5, pady=5)
        self._fill_folder_a_panel(folder_a_frame)

        # 中栏下：文件夹 B
        folder_b_frame = tk.LabelFrame(top_frame, text="📁 文件夹 B",
                                       bg=self.colors['card'],
                                       fg=self.colors['text'],
                                       font=('Segoe UI', 11, 'bold'))
        folder_b_frame.grid(row=2, column=1, sticky='nsew', padx=5, pady=5)
        self._fill_folder_b_panel(folder_b_frame)

        # 右栏第1列：操作（主操作）
        action_frame = tk.LabelFrame(top_frame, text="⚡ 操作",
                                     bg=self.colors['card'],
                                     fg=self.colors['text'],
                                     font=('Segoe UI', 11, 'bold'))
        action_frame.grid(row=0, column=2, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_operation_panel(action_frame)

        # 右栏第2列：扫描结果概览（独立）
        overview_frame = tk.LabelFrame(top_frame, text="📊 扫描结果(可点击切换视图）",
                                       bg=self.colors['card'],
                                       fg=self.colors['text'],
                                       font=('Segoe UI', 11, 'bold'))
        overview_frame.grid(row=0, column=3, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_overview_panel(overview_frame)

        # 右栏第3列：快速操作
        quick_frame = tk.LabelFrame(top_frame, text="⚡ 手动分析",
                                    bg=self.colors['card'],
                                    fg=self.colors['text'],
                                    font=('Segoe UI', 11, 'bold'))
        quick_frame.grid(row=0, column=4, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_quick_action_panel(quick_frame)

        # 最右栏：软件使用说明
        help_frame = tk.LabelFrame(top_frame, text="🤖 AI 分析",
                                   bg=self.colors['card'],
                                   fg=self.colors['text'],
                                   font=('Segoe UI', 11, 'bold'))
        help_frame.grid(row=0, column=5, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_help_panel(help_frame)

        # 底部：统一结果展示区（左右 A/B 分栏）
        self.setup_bottom_result_panel(main_frame)

    def _fill_task_panel(self, task_frame):
        """填充任务面板内容（V8：大按钮垂直排列）"""
        # 任务选择/创建（2x2紧凑网格）
        btn_frame = tk.Frame(task_frame, bg=self.colors['card'])
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        btn_new = tk.Button(btn_frame, text="➕ 保存任务",
                 command=self.create_new_task,
                 bg='#10b981', fg='white',
                 font=('Segoe UI', 11, 'bold'), cursor='hand2')
        btn_new.grid(row=0, column=0, padx=2, pady=2, sticky='ew')
        Tooltip(btn_new, "创建一个新的扫描任务，保存文件夹路径和扫描历史")

        btn_load = tk.Button(btn_frame, text="📂 加载任务",
                 command=self.load_existing_task,
                 bg='#3b82f6', fg='white',
                 font=('Segoe UI', 11, 'bold'), cursor='hand2')
        btn_load.grid(row=1, column=0, padx=2, pady=2, sticky='ew')
        Tooltip(btn_load, "加载已保存的任务，自动恢复文件夹路径")

        btn_io = tk.Button(btn_frame, text="导入导出",
                 command=self.import_export_tasks,
                 bg='#f59e0b', fg='white',
                 font=('Segoe UI', 11, 'bold'), cursor='hand2')
        btn_io.grid(row=0, column=1, padx=2, pady=2, sticky='ew')
        Tooltip(btn_io, "导出或导入任务列表及其扫描历史")

        btn_restore = tk.Button(btn_frame, text="恢复默认",
                 command=self.restore_defaults,
                 bg='#8b5cf6', fg='white',
                 font=('Segoe UI', 11, 'bold'), cursor='hand2')
        btn_restore.grid(row=1, column=1, padx=2, pady=2, sticky='ew')
        Tooltip(btn_restore, "将软件恢复到初始状态，清空所有任务、路径和结果")

        # 扫描选项
        options_frame = tk.Frame(task_frame, bg=self.colors['card'])
        options_frame.pack(fill=tk.X, padx=10, pady=1)

        # 扫描选项变量
        self.scan_options = {
            'scan_folder_a': tk.BooleanVar(value=True),
            'scan_folder_b': tk.BooleanVar(value=True),
            'full_scan': tk.BooleanVar(value=True),
            'incremental': tk.BooleanVar(value=False),
            'fast_mode': tk.BooleanVar(value=True),
            'compute_md5': tk.BooleanVar(value=False),
            'detect_moved': tk.BooleanVar(value=False),
            'use_duration': tk.BooleanVar(value=True),
        }

        option_configs = [
            ('scan_folder_a', '📁', '扫描文件夹A'),
            ('scan_folder_b', '📁', '扫描文件夹B'),
            ('full_scan', '🔍', '全新扫描'),
            ('incremental', '⚡', '增量扫描'),
            ('fast_mode', '🚀', '快速模式'),
            ('compute_md5', '🔐', '计算MD5'),
            ('detect_moved', '📂', '检测移动'),
        ]

        option_tooltips = {
            'scan_folder_a': "扫描左侧指定的文件夹A中的音乐文件",
            'scan_folder_b': "扫描右侧指定的文件夹B中的音乐文件",
            'full_scan': "扫描所有文件并建立新的历史基准（首次扫描必选）",
            'incremental': "只扫描自上次以来发生变化的文件，速度更快",
            'fast_mode': "通过文件大小和修改时间判断文件是否变化（推荐开启）",
            'compute_md5': "通过文件内容哈希判断变化，更精确但大文件稍慢",
            'detect_moved': "检测文件名或路径改变但内容完全相同的文件（需要开启计算MD5并关闭快速模式）",
        }

        # 互斥关系：勾选 key 时自动取消 mutex_keys 中的项
        self._mutex_map = {
            'full_scan': ['incremental'],
            'incremental': ['full_scan'],
            'fast_mode': ['compute_md5', 'detect_moved'],
            'compute_md5': ['fast_mode'],
            'detect_moved': ['fast_mode'],
        }
        # 互斥组定义：用于空组自动恢复默认
        self._mutex_groups = {
            'scan_mode': {
                'keys': ['full_scan', 'incremental'],
                'default': 'full_scan',
                'name': '全新扫描',
            },
            'compare_method': {
                'keys': ['fast_mode', 'compute_md5'],
                'default': 'fast_mode',
                'name': '快速模式',
            },
        }
        # 必须至少选一个的组：空组时恢复所有默认值
        self._min_one_groups = {
            'scan_folders': {
                'keys': ['scan_folder_a', 'scan_folder_b'],
                'defaults': ['scan_folder_a', 'scan_folder_b'],
                'name': '扫描文件夹',
            },
        }
        # 依赖关系：勾选 key 时自动勾选 requires_keys 中的项
        self._requires_map = {
            'detect_moved': ['compute_md5'],
        }

        for i, (key, icon, label) in enumerate(option_configs):
            cb = tk.Checkbutton(
                options_frame, text=f"{icon} {label}",
                variable=self.scan_options[key],
                bg=self.colors['card'], fg=self.colors['text'],
                selectcolor=self.colors['card'],
                activebackground=self.colors['card'],
                font=('Segoe UI', 10),
            )
            if key in self._mutex_map or any(key in g['keys'] for g in self._min_one_groups.values()):
                cb.config(command=lambda k=key: self._on_option_toggled(k))
            else:
                cb.config(command=self.on_scan_option_changed)
            cb.grid(row=i // 2, column=i % 2, sticky=tk.W, padx=2, pady=1)
            Tooltip(cb, option_tooltips.get(key, ""))

        # 相似度阈值
        sim_frame = tk.Frame(options_frame, bg=self.colors['card'])
        sim_frame.grid(row=3, column=1, sticky=tk.W, padx=2, pady=1)
        tk.Label(sim_frame, text="相似度:", bg=self.colors['card'], fg=self.colors['text'], font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.similarity_var = tk.IntVar(value=80)
        sim_spin = tk.Spinbox(sim_frame, from_=50, to=100, increment=5,
                              textvariable=self.similarity_var, width=5,
                              font=('Segoe UI', 10), bg='#d1d5db', fg='#1f2937',
                              insertbackground='#1f2937', buttonbackground='#9ca3af')
        sim_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(sim_frame, text="%", bg=self.colors['card'], fg=self.colors['text'], font=('Segoe UI', 10)).pack(side=tk.LEFT)
        Tooltip(sim_spin, "设置相似文件判断的阈值百分比，值越低匹配越宽松")

        # 使用时长值（勾选 + 阈值）
        dur_frame = tk.Frame(options_frame, bg=self.colors['card'])
        dur_frame.grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=2, pady=1)
        self.use_duration_cb = tk.Checkbutton(dur_frame, text="使用时长值过滤",
                     variable=self.scan_options['use_duration'],
                     bg=self.colors['card'], fg=self.colors['text'],
                     selectcolor=self.colors['card'],
                     activebackground=self.colors['card'],
                     font=('Segoe UI', 10),
                     command=self.on_scan_option_changed)
        self.use_duration_cb.pack(side=tk.LEFT)
        Tooltip(self.use_duration_cb, "勾选后根据音频时长过滤近似匹配，需要先读取每个文件的音频时长（可能降低扫描速度）")
        tk.Label(dur_frame, text="阈值:", bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(5, 0))
        self.duration_threshold_var = tk.IntVar(value=98)
        dur_spin = tk.Spinbox(dur_frame, from_=50, to=100, increment=5,
                              textvariable=self.duration_threshold_var, width=5,
                              font=('Segoe UI', 10), bg='#d1d5db', fg='#1f2937',
                              insertbackground='#1f2937', buttonbackground='#9ca3af')
        dur_spin.pack(side=tk.LEFT, padx=2)
        tk.Label(dur_frame, text="%", bg=self.colors['card'], fg=self.colors['text'], font=('Segoe UI', 10)).pack(side=tk.LEFT)
        Tooltip(dur_spin, "仅当两个文件都有有效时长时才生效，不勾选时跳过时长过滤")

        # 选项冲突提示
        self.option_warning_var = tk.StringVar(value="")
        self.warning_label = tk.Label(options_frame, textvariable=self.option_warning_var,
                bg=self.colors['card'], fg='#fbbf24',
                font=('Segoe UI', 9)
                )
        self.warning_label.grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=2)

    def _fill_task_info_bar(self, parent):
        """当前任务横条"""
        self.task_info_var = tk.StringVar(value="当前任务: 无")
        tk.Label(parent, textvariable=self.task_info_var,
                bg=self.colors['card'], fg=self.colors['accent2'],
                font=('Segoe UI', 12, 'bold')).pack(anchor=tk.W, padx=10, pady=5)

    def _fill_folder_a_panel(self, folder_frame):
        """填充文件夹A面板"""
        a_frame = tk.Frame(folder_frame, bg=self.colors['card'])
        a_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=3)

        path_a_frame = tk.Frame(a_frame, bg=self.colors['card'])
        path_a_frame.pack(fill=tk.X, pady=1)
        tk.Entry(path_a_frame, textvariable=self.path_a_var,
                font=('Segoe UI', 10), bg='#d1d5db', fg='#1f2937',
                insertbackground='#1f2937').pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_browse_a = tk.Button(path_a_frame, text="浏览...", command=lambda: self.browse_folder('A'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9))
        btn_browse_a.pack(side=tk.LEFT, padx=3)
        Tooltip(btn_browse_a, "选择文件夹A的路径")

        stats_frame_a = tk.Frame(a_frame, bg=self.colors['card'])
        stats_frame_a.pack(fill=tk.X, pady=1)
        self.a_stats_var = tk.StringVar(value="📀 0 个文件")
        tk.Label(stats_frame_a, textvariable=self.a_stats_var,
                bg=self.colors['card'], fg=self.colors['accent2'],
                font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.a_change_var = tk.StringVar(value="")
        a_change_lbl = tk.Label(stats_frame_a, textvariable=self.a_change_var,
                bg=self.colors['card'], fg='#86efac',
                font=('Segoe UI', 9))
        a_change_lbl.pack(side=tk.LEFT, padx=10)
        Tooltip(a_change_lbl, "增量扫描下，与上次扫描基准相比的新增和修改文件数量。全新扫描时此处为 0。")

    def _fill_folder_b_panel(self, folder_frame):
        """填充文件夹B面板"""
        b_frame = tk.Frame(folder_frame, bg=self.colors['card'])
        b_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=3)

        path_b_frame = tk.Frame(b_frame, bg=self.colors['card'])
        path_b_frame.pack(fill=tk.X, pady=1)
        tk.Entry(path_b_frame, textvariable=self.path_b_var,
                font=('Segoe UI', 10), bg='#d1d5db', fg='#1f2937',
                insertbackground='#1f2937').pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn_browse_b = tk.Button(path_b_frame, text="浏览...", command=lambda: self.browse_folder('B'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9))
        btn_browse_b.pack(side=tk.LEFT, padx=3)
        Tooltip(btn_browse_b, "选择文件夹B的路径")

        stats_frame_b = tk.Frame(b_frame, bg=self.colors['card'])
        stats_frame_b.pack(fill=tk.X, pady=1)
        self.b_stats_var = tk.StringVar(value="📀 0 个文件")
        tk.Label(stats_frame_b, textvariable=self.b_stats_var,
                bg=self.colors['card'], fg=self.colors['accent2'],
                font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.b_change_var = tk.StringVar(value="")
        b_change_lbl = tk.Label(stats_frame_b, textvariable=self.b_change_var,
                bg=self.colors['card'], fg='#86efac',
                font=('Segoe UI', 9))
        b_change_lbl.pack(side=tk.LEFT, padx=10)
        Tooltip(b_change_lbl, "增量扫描下，与上次扫描基准相比的新增和修改文件数量。全新扫描时此处为 0。")

    def _fill_overview_panel(self, overview_frame):
        """填充扫描结果概览（V12：6项垂直堆叠，文字左数字右，可点击切换视图）"""
        self.overview_vars = {}
        items = [
            ('rename_pending', '待重命名', '#0ea5e9', 'rename'),
            ('duplicate_groups', '重复文件', '#ef4444', 'dup'),
            ('similar_groups', '相似文件', '#f59e0b', 'sim'),
            ('approximate_groups', '近似文件', '#a855f7', 'approx'),
            ('agg_files', '聚合去重', '#22c55e', 'agg'),
            ('duration', '扫描耗时', '#94a3b8', None),
        ]

        # 内部容器用于垂直居中
        inner = tk.Frame(overview_frame, bg=overview_frame.cget('bg'))
        inner.pack(fill=tk.X, expand=True)

        tooltip_texts = {
            'rename_pending': '文件名不符合命名规范的文件数（点击打开重命名管理）',
            'duplicate_groups': '内容完全相同的文件总数（点击仅显示重复文件）',
            'similar_groups': '文件名相同但内容可能不同的文件总数（点击仅显示相似文件）',
            'approximate_groups': '文件名相似且时长接近的文件总数（点击仅显示近似文件）',
            'agg_files': '三类（重复/相似/近似）覆盖的文件总数，可统一去重处理（点击切换聚合去重视图）',
            'duration': '本次扫描所花费的时间',
        }

        for key, label, color, view_type in items:
            row = tk.Frame(inner, bg=inner.cget('bg'), padx=5, pady=3)
            row.pack(fill=tk.X)
            row.columnconfigure(2, weight=1)

            var = tk.StringVar(value="-")
            self.overview_vars[key] = var

            dot_color = color if view_type else '#6b7280'

            dot_lbl = tk.Label(row, text='●', bg=inner.cget('bg'), fg=dot_color,
                               font=('Segoe UI', 8))
            dot_lbl.grid(row=0, column=0, padx=(5, 0))

            text_lbl = tk.Label(row, text=label, bg=inner.cget('bg'), fg=self.colors['text'],
                                font=('Segoe UI', 10))
            text_lbl.grid(row=0, column=1, sticky='w', padx=(0, 5))

            num_lbl = tk.Label(row, textvariable=var,
                               bg=inner.cget('bg'), fg=color,
                               font=('Segoe UI', 14, 'bold'))
            num_lbl.grid(row=0, column=2)

            # Tooltip
            tip = tooltip_texts.get(key, '')
            if tip:
                for lbl in (text_lbl, num_lbl):
                    Tooltip(lbl, tip)

            # 绑定点击（rename 特殊：切换待重命名视图 + 打开重命名管理弹窗）
            if view_type:
                for lbl in (dot_lbl, text_lbl, num_lbl):
                    lbl.config(cursor='hand2')
                    if view_type == 'rename':
                        def _open_rename(e):
                            self.switch_result_view('rename')
                            self._open_long_name_analyzer()
                        lbl.bind('<Button-1>', _open_rename)
                    else:
                        lbl.bind('<Button-1>', lambda e, v=view_type: self.switch_result_view(v))

        # 底部居中导出按钮（导出当前视图列表为 CSV）
        btn_frame = tk.Frame(overview_frame, bg=overview_frame.cget('bg'))
        btn_frame.pack(side=tk.BOTTOM, pady=(0, 4))
        btn_export = tk.Button(btn_frame, text="📄 导出",
                               command=self._export_list_to_txt,
                               bg=self.colors['border'], fg=self.colors['text'],
                               font=('Segoe UI', 9), cursor='hand2', width=10)
        btn_export.pack()
        Tooltip(btn_export, "导出当前视图（重复/相似/近似/聚合去重）列表为 CSV 文件")

    def _fill_view_switch_panel(self, view_frame):
        """填充视图切换面板（V12：独立成一栏）"""
        container = tk.Frame(view_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        inner = tk.Frame(container, bg=self.colors['card'])
        inner.pack(expand=True)

        btn_view_dup = tk.Button(inner, text="仅显示重复",
                 command=lambda: self.switch_result_view('dup'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9), cursor='hand2', width=12)
        btn_view_dup.pack(pady=3)
        Tooltip(btn_view_dup, "仅显示重复文件")

        btn_view_sim = tk.Button(inner, text="仅显示相似",
                 command=lambda: self.switch_result_view('sim'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9), cursor='hand2', width=12)
        btn_view_sim.pack(pady=3)
        Tooltip(btn_view_sim, "仅显示相似文件")

        btn_view_approx = tk.Button(inner, text="仅显示近似",
                 command=lambda: self.switch_result_view('approx'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9), cursor='hand2', width=12)
        btn_view_approx.pack(pady=3)
        Tooltip(btn_view_approx, "仅显示近似文件")

        btn_view_all = tk.Button(inner, text="显示全部",
                 command=lambda: self.switch_result_view('all'),
                 bg=self.colors['border'], fg=self.colors['text'],
                 font=('Segoe UI', 9), cursor='hand2', width=12)
        btn_view_all.pack(pady=3)
        Tooltip(btn_view_all, "显示全部文件")

    def _fill_operation_panel(self, action_frame):
        """填充操作按钮区（V12：主操作）"""
        container = tk.Frame(action_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        btn_inner = tk.Frame(container, bg=self.colors['card'])
        btn_inner.pack(expand=True, fill=tk.X)

        def _center_btn(parent, text, bg, fg, cmd, tooltip, width=14):
            f = tk.Frame(parent, bg=parent.cget('bg'))
            f.pack(fill=tk.X, pady=10)
            btn = tk.Button(f, text=text, command=cmd,
                           bg=bg, fg=fg, font=('Segoe UI', 10), cursor='hand2',
                           width=width)
            btn.pack(anchor='center')
            Tooltip(btn, tooltip)
            return btn

        _center_btn(btn_inner, "开始扫描", '#22c55e', 'white',
                   self.start_scan_with_task,
                   "按照当前配置开始扫描选中的文件夹", width=14)

        _center_btn(btn_inner, "移动到回收站", '#f59e0b', 'white',
                   self.send_selected_to_trash,
                   "将选中的文件移动到软件目录下的 AppTrash 文件夹（非 Windows 回收站，可撤销）", width=14)

        _center_btn(btn_inner, "撤销移动", self.colors['border'], self.colors['text'],
                   self.undo_delete,
                   "将最近一次移动到回收站的文件恢复至原位置", width=14)

        _center_btn(btn_inner, "清空回收站", '#ef4444', 'white',
                   self.clear_trash,
                   "永久删除 AppTrash 回收站中的所有文件（不可恢复）", width=14)

        # 并发线程数控制（读取时长/计算MD5用）
        worker_frame = tk.Frame(container, bg=self.colors['card'])
        worker_frame.pack(pady=(5, 0), anchor='center')
        inner = tk.Frame(worker_frame, bg=self.colors['card'])
        inner.pack(anchor='center')
        tk.Label(inner, text="⚙️ 并发线程数:", bg=self.colors['card'],
                fg=self.colors['text'], font=('Segoe UI', 9)).pack(side=tk.LEFT)
        worker_spin = tk.Spinbox(inner, from_=1, to=32, width=5,
                                 textvariable=self.worker_count_var,
                                 font=('Segoe UI', 9), justify='center')
        worker_spin.pack(side=tk.LEFT, padx=(5, 0))
        Tooltip(worker_spin, "控制读取音频时长和计算MD5时的并行线程数（1~32，默认32）。SSD建议16~32，机械硬盘建议2~4。")

    def _fill_quick_action_panel(self, quick_frame):
        """填充手动分析面板（V17：智选去重+三条件勾选）"""
        container = tk.Frame(quick_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        # 上半区3行 : 下半区2行 = 3:2，使5行等高
        container.grid_rowconfigure(0, weight=3)
        container.grid_rowconfigure(1, weight=2)
        container.grid_columnconfigure(0, weight=1)

        btn_cfg = dict(
            bg=self.colors['border'], fg=self.colors['text'],
            font=('Segoe UI', 9), cursor='hand2',
            relief='raised', bd=1
        )

        cb_cfg = dict(
            bg=self.colors['card'], fg=self.colors['text'],
            font=('Segoe UI', 9), cursor='hand2',
            selectcolor=self.colors['card'],
            activebackground=self.colors['card'],
            activeforeground=self.colors['text']
        )

        # ===== 上半区：筛选条件 + 单侧/指纹 + 统计（3行等高）=====
        top_frame = tk.Frame(container, bg=self.colors['card'])
        top_frame.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)
        top_frame.grid_rowconfigure(0, weight=1)
        top_frame.grid_rowconfigure(1, weight=1)
        top_frame.grid_rowconfigure(2, weight=1)
        top_frame.grid_columnconfigure(0, weight=1)
        top_frame.grid_columnconfigure(1, weight=1)

        cond_frame = tk.Frame(top_frame, bg=self.colors['card'])
        cond_frame.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=10, pady=5)
        cond_frame.grid_columnconfigure(0, weight=1)
        cond_frame.grid_columnconfigure(1, weight=1)
        cond_frame.grid_columnconfigure(2, weight=1)

        tk.Checkbutton(cond_frame, text="最大时长", variable=self.smart_use_duration,
                       **cb_cfg).grid(row=0, column=0, sticky='w')
        tk.Checkbutton(cond_frame, text="最大文件", variable=self.smart_use_size,
                       **cb_cfg).grid(row=0, column=1, sticky='w')
        tk.Checkbutton(cond_frame, text="最新文件", variable=self.smart_use_mtime,
                       **cb_cfg).grid(row=0, column=2, sticky='w')

        cb_single = tk.Checkbutton(top_frame, text="单侧去重",
                                   variable=self.smart_single_side, **cb_cfg)
        cb_single.grid(row=1, column=0, padx=(10, 2), pady=(5, 2), sticky='w')
        cb_single.configure(command=self._on_single_side_toggled)
        Tooltip(cb_single, "不勾选→仅显示两侧数量相等的组；勾选→仅显示数量不等的组")

        cb_fp = tk.Checkbutton(top_frame, text="音频指纹",
                               variable=self.use_fingerprint, **cb_cfg)
        cb_fp.grid(row=2, column=0, padx=(10, 2), pady=(2, 10), sticky='w')
        Tooltip(cb_fp, "勾选后 AI 分析时用 Chromaprint 音频指纹验证聚类结果")

        stat_frame = tk.Frame(top_frame, bg=self.colors['card'])
        stat_frame.grid(row=1, column=1, rowspan=2, padx=10, pady=10, sticky='nsew')
        self.selection_var = tk.StringVar(value="已选择: 0 个文件")
        self.selection_detail_var = tk.StringVar(value="(A: 0, B: 0)")
        tk.Label(stat_frame, textvariable=self.selection_var,
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(anchor=tk.CENTER)
        tk.Label(stat_frame, textvariable=self.selection_detail_var,
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 9)).pack(anchor=tk.CENTER, pady=(2, 0))

        # ===== 下半区：四个功能按键 =====
        bottom_frame = tk.Frame(container, bg=self.colors['card'])
        bottom_frame.grid(row=1, column=0, sticky='nsew', padx=8, pady=8)
        bottom_frame.grid_rowconfigure(0, weight=1)
        bottom_frame.grid_rowconfigure(1, weight=1)
        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure(1, weight=1)

        btn_qa = tk.Button(bottom_frame, text="一键选A",
                           command=lambda: self.quick_select(self.result_view_type, 'A'), **btn_cfg)
        btn_qa.grid(row=0, column=0, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_qa, "在当前视图中一键选中所有文件夹A的文件")

        btn_qb = tk.Button(bottom_frame, text="一键选B",
                           command=lambda: self.quick_select(self.result_view_type, 'B'), **btn_cfg)
        btn_qb.grid(row=0, column=1, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_qb, "在当前视图中一键选中所有文件夹B的文件")

        btn_smart = tk.Button(bottom_frame, text="智选去重",
                              command=self.smart_select, **btn_cfg)
        btn_smart.grid(row=1, column=0, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_smart, "根据勾选的条件在每组中智能保留最优文件，其余勾选")

        cancel_btn = tk.Button(bottom_frame, text="取消选择",
                               command=self.clear_selection, **btn_cfg)
        cancel_btn.grid(row=1, column=1, padx=10, pady=10, sticky='nsew')
        Tooltip(cancel_btn, "取消所有已选中的文件")

    def _fill_help_panel(self, help_frame):
        """填充 AI 分析面板"""
        container = tk.Frame(help_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        btn_ai = dict(
            bg=self.colors['border'], fg=self.colors['text'],
            font=('Segoe UI', 9), cursor='hand2',
            relief='raised', bd=1
        )

        # 按钮居中区域
        btn_wrap = tk.Frame(container, bg=self.colors['card'])
        btn_wrap.pack(expand=True)

        tk.Button(btn_wrap, text="API 配置", command=self._configure_api,
                  **btn_ai).pack(pady=(8, 2), ipadx=20, fill=tk.X)
        tk.Button(btn_wrap, text="AI 分析", command=self._run_ai_analysis,
                  **btn_ai).pack(pady=2, ipadx=20, fill=tk.X)
        cb_live = tk.Checkbutton(btn_wrap, text="选live版",
                                 variable=self.use_live_priority,
                                 bg=self.colors['card'], fg=self.colors['text'],
                                 font=('Segoe UI', 9), cursor='hand2',
                                 selectcolor=self.colors['card'],
                                 activebackground=self.colors['card'],
                                 activeforeground=self.colors['text'])
        cb_live.pack(pady=(0, 6))
        Tooltip(cb_live, "勾选后 AI 分析/智选去重时优先勾选 Live 版（保留原版）\n"
                         "仅当 Live 版与原版时长差≤5秒时生效")

        tk.Label(btn_wrap, text="学习",
                bg=self.colors['card'], fg=self.colors['accent'],
                font=('Segoe UI', 9, 'bold')).pack(pady=(10, 4))
        cb_learn = tk.Checkbutton(btn_wrap, text="读取学习",
                                  variable=self.use_learning,
                                  bg=self.colors['card'], fg=self.colors['text'],
                                  font=('Segoe UI', 9), cursor='hand2',
                                  selectcolor=self.colors['card'],
                                  activebackground=self.colors['card'],
                                  activeforeground=self.colors['text'])
        cb_learn.pack(pady=(0, 4))
        Tooltip(cb_learn, "勾选后 AI 分析时应用学习记录（记录中标记为保留的文件不会被勾选）")
        tk.Button(btn_wrap, text="记住调整",
                  command=lambda: messagebox.showinfo(
                      "反馈已记录",
                      f"已记录 {self._record_feedback()} 个手动保留的文件"),
                  **btn_ai).pack(pady=2, ipadx=20, fill=tk.X)
        tk.Button(btn_wrap, text="导出规则", command=self._export_ai_rules,
                  **btn_ai).pack(pady=2, ipadx=20, fill=tk.X)
        tk.Button(btn_wrap, text="清除记录",
                  command=lambda: [self.clear_feedback(),
                                   messagebox.showinfo("已清除", "已清除所有反馈记录")],
                  **btn_ai).pack(pady=2, ipadx=20, fill=tk.X)

    def setup_bottom_result_panel(self, parent):
        """底部通栏结果区：统一左右 A/B 分栏"""
        # Treeview 样式：协调的浅灰背景，避免纯白刺眼
        tree_style = ttk.Style()
        tree_style.theme_use("clam")
        tree_style.configure("Treeview",
                             background='#d1d5db',
                             foreground='#1f2937',
                             fieldbackground='#d1d5db',
                             rowheight=24,
                             font=('Segoe UI', 10))
        tree_style.configure("Treeview.Heading",
                             background='#4b5563',
                             foreground='#ffffff',
                             font=('Segoe UI', 10, 'bold'))
        tree_style.map("Treeview",
                       background=[('selected', '#93c5fd')],
                       foreground=[('selected', '#1f2937')])
        tree_style.map("Treeview.Heading",
                       background=[('active', '#374151')])

        # 滚动条样式：橙黄色滑动块
        tree_style.configure("Vertical.TScrollbar",
                             background="#f59e0b",
                             troughcolor="#374151",
                             bordercolor="#374151",
                             arrowcolor="#ffffff",
                             width=12,
                             gripcount=0)
        tree_style.map("Vertical.TScrollbar",
                       background=[('active', '#fbbf24'),
                                   ('pressed', '#d97706'),
                                   ('disabled', '#9ca3af')])

        result_frame = tk.LabelFrame(parent, text="",
                                     bg=self.colors['card'],
                                     fg=self.colors['text'],
                                     font=('Segoe UI', 11, 'bold'))
        result_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        container = tk.Frame(result_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        def make_tree(title):
            frame = tk.LabelFrame(container, text=title, bg=self.colors['card'],
                                  fg=self.colors['text'], font=('Segoe UI', 10, 'bold'))
            frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)
            tree = ttk.Treeview(frame, columns=('no', 'name', 'play', 'duration', 'size', 'mtime'),
                                show='tree headings', selectmode='browse')
            tree.heading('#0', text='选择')
            tree.column('#0', width=12, anchor='center')
            tree.heading('no', text='序号')
            tree.column('no', width=30, anchor='center')
            tree.heading('name', text='文件名')
            tree.column('name', width=280, anchor='center')
            tree.heading('play', text='🎵')
            tree.column('play', width=28, anchor='center')
            tree.heading('duration', text='时长')
            tree.column('duration', width=55, anchor='center')
            tree.heading('size', text='大小')
            tree.column('size', width=35, anchor='center')
            tree.heading('mtime', text='修改时间')
            tree.column('mtime', width=105, anchor='center')
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            return tree, frame

        tree_a, _ = make_tree("文件夹 A")

        # 共用滚动条（放在A和B之间）
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        tree_b, _ = make_tree("文件夹 B")

        def sync_scroll(cmd=None, *args):
            if cmd:
                tree_a.yview(cmd, *args)
                tree_b.yview(cmd, *args)

        def on_a_scroll(first, last):
            tree_b.yview_moveto(first)
            scrollbar.set(first, last)

        def on_b_scroll(first, last):
            tree_a.yview_moveto(first)
            scrollbar.set(first, last)

        scrollbar.config(command=sync_scroll)
        tree_a.config(yscrollcommand=on_a_scroll)
        tree_b.config(yscrollcommand=on_b_scroll)

        for tree in (tree_a, tree_b):
            tree.bind('<Button-1>', self._on_tree_background_click)
            tree.bind('<ButtonRelease-1>', self.on_tree_click)
            tree.bind('<Button-3>', self.on_tree_right_click)
            tree.bind('<<TreeviewSelect>>', self._on_tree_select)
            tree.tag_configure('peer_highlight', background='#fef08a')
            tree.tag_configure('checked', foreground='#ef4444')

        self.result_tree_a = tree_a
        self.result_tree_b = tree_b
        self.result_view_type = 'dup'  # 默认显示重复文件

    def switch_result_view(self, view_type: str):
        """切换底部结果区显示内容"""
        self.result_view_type = view_type
        self.refresh_bottom_trees()

    # ==================== 通用方法 ====================

    def _create_dialog(self, title: str, w: int, h: int,
                       transient: bool = True, grab: bool = True) -> tk.Toplevel:
        """创建标准弹窗（统一样板代码）"""
        d = tk.Toplevel(self)
        d.title(title)
        d.iconbitmap(self._blank_ico_path)
        d.configure(bg=self.colors['card'])
        if transient:
            d.transient(self)
        if grab:
            d.grab_set()
        self._center_window(d, w, h)
        d.after_idle(lambda w2=d: self._remove_window_icon(w2))
        return d

    def _make_button_frame(self, parent) -> tk.Frame:
        """创建按钮容器（底部居中）"""
        f = tk.Frame(parent, bg=self.colors['card'])
        f.pack(pady=10)
        return f

    def _add_dialog_button(self, parent, text: str, command,
                           bg: str = None, **kwargs):
        """在按钮容器中添加按钮"""
        bg = bg or self.colors['border']
        btn = tk.Button(parent, text=text, command=command,
                        bg=bg, fg='white', font=('Segoe UI', 10, 'bold'),
                        cursor='hand2', **kwargs)
        btn.pack(side=tk.LEFT, padx=5)
        return btn

    def show_help(self):
        """显示软件使用说明窗口"""
        dialog = self._create_dialog("软件使用说明", 640, 520)

        # 标题
        tk.Label(dialog, text="软件使用说明", bg=self.colors['card'],
                 fg=self.colors['accent2'], font=('Segoe UI', 14, 'bold')).pack(pady=(15, 5))

        # 文本框 + 滚动条
        text_frame = tk.Frame(dialog, bg=self.colors['card'])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        text = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set,
                       bg=self.colors['card'], fg=self.colors['text'],
                       font=('Segoe UI', 11), padx=10, pady=10,
                       spacing1=2, spacing2=2, spacing3=4,
                       relief=tk.FLAT, highlightthickness=0)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=text.yview)

        # 插入内容并配置标签
        text.insert(tk.END, HELP_CONTENT)
        text.tag_config('heading', font=('Segoe UI', 13, 'bold'), foreground=self.colors['accent2'], spacing1=10, spacing3=6)
        text.tag_config('body', font=('Segoe UI', 11), foreground=self.colors['text'])
        text.tag_add('heading', '1.0', '1.end')
        text.tag_add('heading', '7.0', '7.end')
        text.tag_add('heading', '12.0', '12.end')

        text.config(state=tk.DISABLED)

        # 关闭按钮
        tk.Button(dialog, text="关闭", command=dialog.destroy,
                  bg=self.colors['border'], fg=self.colors['text'],
                  font=('Segoe UI', 10), cursor='hand2', width=10).pack(pady=(0, 15))

    def browse_folder(self, folder_type: str):
        """浏览文件夹"""
        path = filedialog.askdirectory()
        if path:
            if folder_type == 'A':
                self.path_a_var.set(path)
            else:
                self.path_b_var.set(path)

    def copy_wechat_id(self):
        """复制微信号到剪贴板"""
        self.clipboard_clear()
        self.clipboard_append("a_better_day_9")
        self.show_toast("✅ 微信号已复制到剪贴板")

    def _on_closing(self):
        """关闭窗口前确认并保存会话"""
        if messagebox.askyesno("确认退出", "确定要退出 ABD9音乐文件筛查器吗？"):
            self._save_session()
            self.destroy()

    def _save_session(self):
        """保存当前会话状态到 JSON"""
        session = {
            'last_task_id': self.current_task.task_id if self.current_task else None,
            'folder_a': self.path_a_var.get().strip(),
            'folder_b': self.path_b_var.get().strip(),
            'scan_options': {k: v.get() for k, v in self.scan_options.items()},
            'worker_count': self.worker_count_var.get(),
        }
        session_path = get_app_dir() / "last_session.json"
        try:
            with open(session_path, 'w', encoding='utf-8') as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_session(self):
        """从 JSON 恢复上次会话状态"""
        session_path = get_app_dir() / "last_session.json"
        if not session_path.exists():
            return
        try:
            with open(session_path, 'r', encoding='utf-8') as f:
                session = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        # 恢复扫描选项
        for key, val in session.get('scan_options', {}).items():
            if key in self.scan_options:
                self.scan_options[key].set(val)

        # 恢复路径
        self.path_a_var.set(session.get('folder_a', '').strip())
        self.path_b_var.set(session.get('folder_b', '').strip())

        # 恢复并发线程数（校验范围 1~32）
        wc = session.get('worker_count', 32)
        try:
            wc = int(wc)
        except (ValueError, TypeError):
            wc = 32
        wc = max(1, min(32, wc))
        self.worker_count_var.set(wc)

        # 恢复当前任务
        last_task_id = session.get('last_task_id')
        if last_task_id:
            task = self.task_manager.get_task(last_task_id)
            if task:
                self.current_task = task
                self.update_task_display()
            else:
                self.show_toast("⚠️ 上次任务已删除，已恢复路径和扫描设置")

        # 同步互斥选项状态
        self._sync_mutex_options()
        self.on_scan_option_changed()

    def show_toast(self, message: str, duration: int = 3000):
        """显示临时提示"""
        toast = tk.Toplevel(self)
        toast.overrideredirect(True)
        toast.configure(bg='#1e293b')

        label = tk.Label(toast, text=message, bg='#1e293b', fg='#38bdf8',
                        font=('Segoe UI', 11), padx=20, pady=10)
        label.pack()

        # 居中显示
        toast.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - toast.winfo_width()) // 2
        y = self.winfo_y() + 50
        toast.geometry(f"+{x}+{y}")

        # 自动关闭
        toast.after(duration, toast.destroy)

    def show_scan_result_dialog(self, message: str, extra_text: str = ''):
        """显示扫描结果弹窗（统一白色字体；extra_text 为附加诊断提示）"""
        h = 480 if extra_text else 330
        dialog = self._create_dialog("扫描结果", 460, h)
        dialog.resizable(False, False)

        # 主内容区（纵向：上方文本 + 底部按钮）
        main_frame = tk.Frame(dialog, bg=self.colors['card'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)

        # 上方：图标 + 数据 + 诊断
        top = tk.Frame(main_frame, bg=self.colors['card'])
        top.pack(fill=tk.BOTH, expand=True)

        tk.Label(top, text="ℹ", bg=self.colors['card'], fg='white',
                 font=('Segoe UI', 20)).pack(anchor='w')

        tk.Label(top, text=message, bg=self.colors['card'], fg='white',
                 font=('Segoe UI', 11), justify=tk.LEFT, anchor='nw').pack(
                     fill=tk.BOTH, expand=True, pady=(10, 0))

        if extra_text:
            tk.Label(top, text=extra_text, bg=self.colors['card'], fg='white',
                     font=('Segoe UI', 11), justify=tk.LEFT, anchor='nw',
                     wraplength=420).pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        # 底部：确定按钮（居中，完整显示）
        tk.Button(main_frame, text="确定", command=dialog.destroy,
                  bg=self.colors['border'], fg='white',
                  font=('Segoe UI', 11), cursor='hand2', width=12).pack(
                      pady=(14, 0))

    # ==================== 扫描选项处理 ====================

    def _on_option_toggled(self, key):
        """处理扫描选项勾选（双向互斥 + 必选恢复 + 依赖自动勾选 + 空组自动恢复默认）"""
        auto_recovery_msg = ""

        if not self.scan_options[key].get():
            # 取消勾选：检查所属互斥组是否全空，若是则恢复默认
            for group in self._mutex_groups.values():
                if key in group['keys']:
                    any_checked = any(self.scan_options[k].get() for k in group['keys'])
                    if not any_checked:
                        default_key = group['default']
                        self.scan_options[default_key].set(True)
                        # 取消默认选项的互斥项
                        for mkey in self._mutex_map.get(default_key, []):
                            self.scan_options[mkey].set(False)
                        auto_recovery_msg = f"ℹ️ 已自动恢复为{group['name']}"
                    break

            # 取消勾选：检查所属必选组是否全空，若是则恢复所有默认值
            if not auto_recovery_msg:
                for group in self._min_one_groups.values():
                    if key in group['keys']:
                        any_checked = any(self.scan_options[k].get() for k in group['keys'])
                        if not any_checked:
                            for default_key in group['defaults']:
                                self.scan_options[default_key].set(True)
                            auto_recovery_msg = f"ℹ️ 已自动恢复为默认{group['name']}"
                        break
        else:
            # 勾选：取消互斥项
            for mkey in self._mutex_map.get(key, []):
                self.scan_options[mkey].set(False)

            # 自动勾选依赖项
            for rkey in self._requires_map.get(key, []):
                if not self.scan_options[rkey].get():
                    self.scan_options[rkey].set(True)
                    # 依赖项被勾选后，处理其互斥项（排除当前项）
                    for mmkey in self._mutex_map.get(rkey, []):
                        if mmkey != key:
                            self.scan_options[mmkey].set(False)

        self.on_scan_option_changed(auto_recovery_msg)

    def on_scan_option_changed(self, extra_msg=""):
        """扫描选项变更通用回调"""
        warnings = []

        if extra_msg:
            warnings.append(extra_msg)

        # 检查文件夹选择
        if not self.scan_options['scan_folder_a'].get() and not self.scan_options['scan_folder_b'].get():
            warnings.append("⚠️ 请至少选择一个要扫描的文件夹")
            self.option_warning_var.set("; ".join(warnings))
            return

        self.option_warning_var.set("; ".join(warnings))
        self._sync_mutex_options()

    def _sync_mutex_options(self):
        """同步互斥选项状态（基于声明式 _mutex_map / _mutex_groups）"""
        # 基于 _mutex_map 强制执行互斥：按优先级顺序保留
        priority = ['full_scan', 'incremental', 'fast_mode', 'compute_md5', 'detect_moved']
        seen = set()
        for k in priority:
            if k in seen or not self.scan_options[k].get():
                continue
            seen.add(k)
            for mk in self._mutex_map.get(k, []):
                self.scan_options[mk].set(False)
                seen.add(mk)

        # 基于 _requires_map 强制执行依赖
        for k in priority:
            if self.scan_options[k].get():
                for rk in self._requires_map.get(k, []):
                    if not self.scan_options[rk].get():
                        self.scan_options[rk].set(True)

        # 空组恢复默认
        for group in self._mutex_groups.values():
            if not any(self.scan_options[k].get() for k in group['keys']):
                self.scan_options[group['default']].set(True)

        # 必选组恢复默认
        for group in self._min_one_groups.values():
            if not any(self.scan_options[k].get() for k in group['keys']):
                for dk in group['defaults']:
                    self.scan_options[dk].set(True)

    def validate_scan_options(self) -> Tuple[bool, str]:
        """验证扫描选项（强制拦截无效配置）"""
        if not self.scan_options['scan_folder_a'].get() and not self.scan_options['scan_folder_b'].get():
            return False, "请至少选择一个要扫描的文件夹（A或B）"

        if self.scan_options['scan_folder_a'].get():
            path_a = self.path_a_var.get().strip()
            if not path_a:
                return False, "已选择扫描文件夹A，但未指定路径"
            if not os.path.exists(path_a):
                return False, f"文件夹A路径不存在：\n{path_a}\n\n请检查路径或点击「浏览...」重新选择。"
            if not os.path.isdir(path_a):
                return False, f"文件夹A路径不是目录：\n{path_a}\n\n请选择有效的文件夹。"

        if self.scan_options['scan_folder_b'].get():
            path_b = self.path_b_var.get().strip()
            if not path_b:
                return False, "已选择扫描文件夹B，但未指定路径"
            if not os.path.exists(path_b):
                return False, f"文件夹B路径不存在：\n{path_b}\n\n请检查路径或点击「浏览...」重新选择。"
            if not os.path.isdir(path_b):
                return False, f"文件夹B路径不是目录：\n{path_b}\n\n请选择有效的文件夹。"

        # 防御性检查：扫描模式不能为空
        if not self.scan_options['full_scan'].get() and not self.scan_options['incremental'].get():
            return False, "请至少选择一种扫描模式（全新扫描或增量扫描）"

        # 防御性检查：比较方式不能为空
        if not self.scan_options['fast_mode'].get() and not self.scan_options['compute_md5'].get():
            return False, "请至少选择一种比较方式（快速模式或计算MD5）"

        return True, ""

    def get_effective_scan_config(self) -> dict:
        """获取有效的扫描配置（处理选项优先级）"""
        is_full = self.scan_options['full_scan'].get()
        is_incremental = self.scan_options['incremental'].get() and not is_full

        # 首次使用默认全新扫描
        is_first_scan = self.current_task is None or self.current_task.scan_count == 0
        if is_first_scan and not is_full:
            is_incremental = False

        return {
            'scan_folder_a': self.scan_options['scan_folder_a'].get(),
            'scan_folder_b': self.scan_options['scan_folder_b'].get(),
            'scan_mode': 'full' if is_full else ('incremental' if is_incremental else 'full'),
            'compare_method': 'size_time' if self.scan_options['fast_mode'].get() else 'hash',
            'compute_md5': self.scan_options['compute_md5'].get(),
            'detect_moved': self.scan_options['detect_moved'].get(),
            'similarity_threshold': self.similarity_var.get() / 100.0,
            'duration_threshold': self.duration_threshold_var.get() / 100.0,
        }

    def _on_single_side_toggled(self):
        """勾选/取消单侧去重时自动调整阈值"""
        if self.smart_single_side.get():
            self.similarity_var.set(75)
            self.duration_threshold_var.set(99)
        else:
            self.similarity_var.set(80)
            self.duration_threshold_var.set(98)

    # ==================== 任务管理 ====================

    def _center_window(self, window, width: int, height: int):
        """将弹窗居中显示在主窗口上"""
        x = self.winfo_x() + (self.winfo_width() - width) // 2
        y = self.winfo_y() + (self.winfo_height() - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _remove_window_icon(self, window):
        """通过 Windows API 移除窗口标题栏图标（解决 iconbitmap('') 在打包后无效的问题）
        注意：只清除 ICON_BIG，不清除 ICON_SMALL，否则会导致任务栏图标无法显示"""
        try:
            hwnd = window.winfo_id()
            if hwnd:
                # WM_SETICON = 0x80, ICON_BIG = 0（仅清除大图标，保留 ICON_SMALL 供任务栏使用）
                ctypes.windll.user32.SendMessageW(hwnd, 0x80, 0, 0)
        except Exception:
            pass

    def create_new_task(self):
        """创建新任务对话框"""
        dialog = self._create_dialog("保存任务", 400, 200)
        dialog.grab_set()
        self._center_window(dialog, 400, 200)
        dialog.after_idle(lambda w=dialog: self._remove_window_icon(w))

        tk.Label(dialog, text="任务名称:", bg=self.colors['card'],
                fg=self.colors['text'], font=('Segoe UI', 10)).pack(pady=5)
        name_var = tk.StringVar()
        tk.Entry(dialog, textvariable=name_var, width=40,
                font=('Segoe UI', 10)).pack()

        def confirm():
            name = name_var.get().strip()
            if not name:
                return

            folder_a = self.path_a_var.get().strip()
            folder_b = self.path_b_var.get().strip()

            if not folder_a and not folder_b:
                messagebox.showwarning("提示", "请至少选择一个文件夹")
                return

            self.current_task = self.task_manager.create_task(
                name, folder_a or "", folder_b or ""
            )
            self.update_task_display()
            dialog.destroy()
            self.show_toast(f"✅ 任务创建成功: {name}")

        btn_create = tk.Button(dialog, text="创建", command=confirm,
                 bg=self.colors['accent'], fg='white',
                 font=('Segoe UI', 10))
        btn_create.pack(pady=20)
        Tooltip(btn_create, "确认创建新任务")

    def update_task_display(self):
        """更新任务信息（居中显示在UI中）"""
        if self.current_task:
            self.task_info_var.set(
                f"当前任务: {self.current_task.task_name} (扫描{self.current_task.scan_count}次)"
            )
        else:
            self.task_info_var.set("当前任务: 无")

    def _sync_task_paths(self):
        """将 UI 中当前路径同步到 current_task 并持久化（用户在路径框编辑或浏览后调用）"""
        if not self.current_task:
            return
        folder_a = self.path_a_var.get().strip()
        folder_b = self.path_b_var.get().strip()
        # 去除路径两端空白后写回 UI（防止隐藏空格导致扫描失败）
        self.path_a_var.set(folder_a)
        self.path_b_var.set(folder_b)
        if (self.current_task.folder_a != folder_a or
                self.current_task.folder_b != folder_b):
            self.task_manager.update_task(
                self.current_task.task_id,
                folder_a=folder_a,
                folder_b=folder_b
            )
            self.current_task = self.task_manager.get_task(self.current_task.task_id)

    def load_existing_task(self):
        """加载已有任务"""
        tasks = self.task_manager.list_tasks()

        if not tasks:
            messagebox.showinfo("提示", "没有保存的任务")
            return

        dialog = self._create_dialog("选择任务", 600, 400)
        dialog.grab_set()
        self._center_window(dialog, 600, 400)
        dialog.after_idle(lambda w=dialog: self._remove_window_icon(w))

        # 任务列表（Checkbutton 多选）
        list_frame = tk.Frame(dialog, bg=self.colors['card'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        task_vars = {}  # task_id -> BooleanVar
        for task in tasks:
            display = f"{task.task_name} ({task.task_id}) - 扫描{task.scan_count}次 - {task.updated_at[:10]}"
            var = tk.BooleanVar(value=False)
            task_vars[task.task_id] = var
            cb = tk.Checkbutton(list_frame, text=display, variable=var,
                                bg=self.colors['card'], fg=self.colors['text'],
                                selectcolor=self.colors['card'],
                                activebackground=self.colors['card'],
                                font=('Segoe UI', 10), anchor=tk.W)
            cb.pack(fill=tk.X, pady=1)

        def get_selected_tasks():
            return [tid for tid, var in task_vars.items() if var.get()]

        def load():
            selected = get_selected_tasks()
            if not selected:
                return
            if len(selected) > 1:
                messagebox.showinfo("提示", "只能加载一个任务，请只勾选一项", parent=dialog)
                return
            task = self.task_manager.get_task(selected[0])
            if not task:
                return
            self.current_task = task
            self.path_a_var.set(task.folder_a)
            self.path_b_var.set(task.folder_b)
            self.update_task_display()
            dialog.destroy()
            self.show_toast(f"✅ 已加载任务: {task.task_name}")

        def delete():
            selected = get_selected_tasks()
            if not selected:
                return
            if not messagebox.askyesno("确认删除", f"确定要删除选中的 {len(selected)} 个任务吗？", parent=dialog):
                return
            deleted_names = []
            for tid in selected:
                task = self.task_manager.get_task(tid)
                if task:
                    deleted_names.append(task.task_name)
                try:
                    self.task_manager.delete_task(tid)
                except Exception:
                    pass
            self.show_toast(f"✅ 已删除任务: {', '.join(deleted_names)}")
            # 刷新列表：重新读取任务
            remaining = self.task_manager.list_tasks()
            if not remaining:
                dialog.destroy()
                return
            # 清空现有列表并重建
            for widget in list_frame.winfo_children():
                widget.destroy()
            task_vars.clear()
            for task in remaining:
                display = f"{task.task_name} ({task.task_id}) - 扫描{task.scan_count}次 - {task.updated_at[:10]}"
                var = tk.BooleanVar(value=False)
                task_vars[task.task_id] = var
                cb = tk.Checkbutton(list_frame, text=display, variable=var,
                                    bg=self.colors['card'], fg=self.colors['text'],
                                    selectcolor=self.colors['card'],
                                    activebackground=self.colors['card'],
                                    font=('Segoe UI', 10), anchor=tk.W)
                cb.pack(fill=tk.X, pady=1)

        btn_frame = tk.Frame(dialog, bg=self.colors['card'])
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="加载选中", command=load,
                 bg=self.colors['accent'], fg='white',
                 font=('Segoe UI', 10), cursor='hand2').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="删除选中", command=delete,
                 bg='#ef4444', fg='white',
                 font=('Segoe UI', 10), cursor='hand2').pack(side=tk.LEFT, padx=5)

    def import_export_tasks(self):
        """导入/导出任务列表"""
        dialog = self._create_dialog("导入导出", 350, 350)
        dialog.grab_set()
        self._center_window(dialog, 350, 350)
        dialog.after_idle(lambda w=dialog: self._remove_window_icon(w))

        tk.Label(dialog, text="导入导出任务列表",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 12, 'bold')).pack(pady=(15, 10))

        tk.Label(dialog, text="备份或恢复您的任务数据",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack(pady=2)

        tk.Label(dialog, text="※ 建议导出到软件目录以外\n避免重置时丢失备份文件",
                bg=self.colors['card'], fg='#fbbf24',
                font=('Segoe UI', 16, 'bold')).pack(pady=2)

        btn_frame = tk.Frame(dialog, bg=self.colors['card'])
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="导出",
                 command=lambda: (dialog.destroy(), self.export_tasks()),
                 bg=self.colors['accent'], fg='white',
                 font=('Segoe UI', 10, 'bold'), cursor='hand2').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="导入",
                 command=lambda: (dialog.destroy(), self.import_tasks()),
                 bg='#10b981', fg='white',
                 font=('Segoe UI', 10, 'bold'), cursor='hand2').pack(side=tk.LEFT, padx=5)

    def export_tasks(self):
        """导出所有任务数据到JSON文件"""
        from datetime import datetime
        default_name = f"music_scanner_tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=default_name,
            title="导出任务列表"
        )
        if not path:
            return
        try:
            data = self.task_manager.export_all_data()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.show_toast(f"✅ 任务列表已导出到: {Path(path).name}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def import_tasks(self):
        """从JSON文件导入任务数据"""
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="导入任务列表"
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            imported, skipped = self.task_manager.import_all_data(data)
            msg = f"成功导入 {imported} 个任务"
            if skipped:
                msg += f"，跳过 {skipped} 个重复任务"
            self.show_toast(f"✅ {msg}")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def restore_defaults(self):
        """恢复默认：提供两个选项，均需输入作者微信号确认"""
        dialog = self._create_dialog("恢复默认", 340, 260)
        dialog.grab_set()
        self._center_window(dialog, 340, 260)
        dialog.after_idle(lambda w=dialog: self._remove_window_icon(w))

        tk.Label(dialog, text="恢复默认",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 12, 'bold')).pack(pady=(15, 5))

        tk.Label(dialog, text="请选择要执行的操作",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack()

        wechat_var = tk.StringVar()
        tk.Entry(dialog, textvariable=wechat_var, width=30,
                font=('Segoe UI', 10)).pack(pady=5)
        tk.Label(dialog, text="请输入作者微信号以确认操作",
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 8)).pack()

        wechat_lbl = tk.Label(dialog, text="点击复制微信号: a_better_day_9",
                bg=self.colors['card'], fg='#60a5fa',
                font=('Segoe UI', 9), cursor='hand2')
        wechat_lbl.pack()
        wechat_lbl.bind("<Button-1>", lambda e: (self.copy_wechat_id(), wechat_var.set("a_better_day_9")))

        def verify_and_run(action):
            if wechat_var.get().strip() != "a_better_day_9":
                messagebox.showwarning("验证失败", "微信号不正确，操作已取消", parent=dialog)
                return
            dialog.destroy()
            action()

        btn_frame = tk.Frame(dialog, bg=self.colors['card'])
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="1. 清空任务列表",
                 command=lambda: verify_and_run(self._do_clear_tasks),
                 bg='#f59e0b', fg='white', font=('Segoe UI', 10, 'bold'), cursor='hand2',
                 width=18).pack(pady=3)
        tk.Button(btn_frame, text="2. 软件恢复初始设置",
                 command=lambda: verify_and_run(self._do_restore_defaults),
                 bg='#ef4444', fg='white', font=('Segoe UI', 10, 'bold'), cursor='hand2',
                 width=18).pack(pady=3)
        tk.Button(btn_frame, text="取消", command=dialog.destroy,
                 bg=self.colors['border'], fg=self.colors['text'], font=('Segoe UI', 10), cursor='hand2',
                 width=18).pack(pady=6)

    def _do_clear_tasks(self):
        """执行清空任务列表"""
        try:
            tasks = self.task_manager.list_tasks()
            for task in tasks:
                try:
                    self.task_manager.delete_task(task.task_id)
                except Exception:
                    pass
        except Exception:
            pass
        self.current_task = None
        self.update_task_display()
        self.show_toast("✅ 任务列表已清空")

    def _do_restore_defaults(self):
        """执行恢复默认的实际逻辑"""
        # 如果当前有任务，清空其历史记录
        if self.current_task:
            try:
                self.task_manager.clear_history(self.current_task.task_id)
            except Exception:
                pass

        # 重置核心状态
        self.current_task = None
        self.path_a_var.set("")
        self.path_b_var.set("")

        # 恢复扫描选项默认值
        self.scan_options['scan_folder_a'].set(True)
        self.scan_options['scan_folder_b'].set(True)
        self.scan_options['full_scan'].set(True)
        self.scan_options['incremental'].set(False)
        self.scan_options['fast_mode'].set(True)
        self.scan_options['compute_md5'].set(False)
        self.scan_options['detect_moved'].set(False)
        self.similarity_var.set(80)

        # 清空结果数据
        self.change_results = []
        self.duplicate_groups = []
        self.similar_groups = []
        self.approximate_groups = []
        self.all_files_a = {}
        self.all_files_b = {}
        self.checked_items = {}

        # 清空回收站记录
        self.last_deleted_records = []
        try:
            if self.trash_dir.exists():
                for item in self.trash_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
        except Exception:
            pass

        # 刷新UI
        self.refresh_bottom_trees()
        self.update_selection_stats()
        self.update_task_display()
        self.a_stats_var.set("📀 0 个文件")
        self.b_stats_var.set("📀 0 个文件")
        self.a_change_var.set("")
        self.b_change_var.set("")
        self.option_warning_var.set("")
        for v in self.overview_vars.values():
            v.set("-")

        self.show_toast("✅ 已恢复到初始状态")

    # ==================== 扫描执行 ====================

    def start_scan_with_task(self):
        """带任务的扫描 - 使用可勾选选项"""
        is_valid, error_msg = self.validate_scan_options()
        if not is_valid:
            messagebox.showwarning("配置错误", error_msg)
            return

        if not self.current_task:
            folder_a = self.path_a_var.get()
            folder_b = self.path_b_var.get()

            # 创建临时任务
            self.current_task = self.task_manager.create_task(
                f"临时任务_{datetime.now().strftime('%m%d_%H%M')}",
                folder_a or "", folder_b or ""
            )
            self.update_task_display()

        # 同步 UI 路径到当前任务（用户在路径框编辑或浏览后，确保扫描使用最新路径）
        self._sync_task_paths()

        # 获取扫描配置
        scan_config = self.get_effective_scan_config()
        scan_type = ScanType(scan_config['scan_mode'])

        # 估算扫描时间
        estimated_files, estimated_seconds = self.estimate_scan_time(scan_config)

        if estimated_files == 0:
            messagebox.showinfo("提示", "未在选中的文件夹中找到音频文件")
            return

        # 直接开始扫描（跳过预估确认弹窗）
        self.show_scan_progress_dialog(scan_type, scan_config)

    def estimate_scan_time(self, scan_config: dict) -> Tuple[int, float]:
        """快速估算扫描文件数量和耗时（仅计数，不读音频时长，不阻塞 UI）"""
        total_files = 0
        extensions = ('.mp3', '.flac', '.wav', '.aac', '.ogg', '.m4a', '.wma')

        for folder_key in ('scan_folder_a', 'scan_folder_b'):
            if scan_config.get(folder_key):
                path = getattr(self.current_task, 'folder_a' if folder_key == 'scan_folder_a' else 'folder_b', '')
                if path and os.path.exists(path):
                    for root, _, filenames in os.walk(path):
                        total_files += sum(1 for f in filenames if f.lower().endswith(extensions))

        base_time = total_files * 0.001
        if scan_config.get('compute_md5'):
            base_time += total_files * 0.03
        if scan_config.get('scan_mode') == 'incremental':
            base_time += 1.0
        else:
            base_time += 2.0

        return total_files, max(base_time, 1.0)

    def show_estimate_dialog(self, estimated_files: int, estimated_seconds: float, on_confirm):
        """显示扫描预估确认弹窗"""
        dialog = self._create_dialog("扫描预估", 320, 160)
        tk.Label(dialog, text="扫描预估",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 12, 'bold')).pack(pady=(15, 10))
        tk.Label(dialog, text=f"预计扫描 {estimated_files} 个音频文件\n预估耗时约 {estimated_seconds:.1f} 秒",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(pady=5)
        btn_frame = self._make_button_frame(dialog)
        self._add_dialog_button(btn_frame, "开始扫描",
                 lambda: (dialog.destroy(), on_confirm()), bg='#22c55e')
        self._add_dialog_button(btn_frame, "取消", dialog.destroy)

    def show_scan_progress_dialog(self, scan_type: ScanType, scan_config: dict):
        """显示扫描进度弹窗并启动后台线程"""
        self.scan_progress_state = {'percent': 0, 'message': '准备扫描...', 'done': False, 'error': None}

        dialog = self._create_dialog("扫描进度", 400, 120)
        self.scan_progress_dialog = dialog

        tk.Label(dialog, text="正在扫描，请稍候...",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 11, 'bold')).pack(pady=(10, 5))

        self.scan_progress_var = tk.DoubleVar(value=0)
        bar = ttk.Progressbar(dialog, variable=self.scan_progress_var, maximum=100, length=350)
        bar.pack(pady=5)

        self.scan_progress_msg = tk.StringVar(value="准备扫描...")
        tk.Label(dialog, textvariable=self.scan_progress_msg,
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack()

        thread = threading.Thread(target=self._run_scan_in_thread, args=(scan_type, scan_config))
        thread.daemon = True
        thread.start()

        self._poll_scan_progress()

    def _run_scan_in_thread(self, scan_type: ScanType, scan_config: dict):
        """在后台线程执行扫描（线程安全写状态）"""
        try:
            def progress_callback(percent: int, message: str):
                with self._scan_state_lock:
                    self.scan_progress_state['percent'] = percent
                    self.scan_progress_state['message'] = message
                self._scan_progress_event.set()

            self.perform_scan(scan_type, scan_config, progress_callback=progress_callback)
            with self._scan_state_lock:
                self.scan_progress_state['done'] = True
        except Exception as e:
            with self._scan_state_lock:
                self.scan_progress_state['error'] = str(e)
                self.scan_progress_state['done'] = True
        finally:
            self._scan_progress_event.set()

    def _build_diagnosis_text(self) -> str:
        """从扫描诊断信息生成提示文本并清空诊断；无诊断返回空串"""
        diag = getattr(self, '_scan_diagnosis', {})
        if not diag:
            return ''
        lines = []
        for ft in ('A', 'B'):
            info = diag.get(ft)
            if not info:
                continue
            lines.append(f"📁 文件夹 {ft}: {info['path']}")
            lines.append(f"   存在: {'是' if info['isdir'] else '否（不是目录）'}")
            lines.append(f"   遍历目录数: {info['walked_dirs']}")
            lines.append(f"   匹配音频文件: 0")
            lines.append(f"   非音频文件数: {info['skipped_ext']}")
            if info['top_skipped']:
                ext_text = ', '.join(f"{ext}({cnt})" for ext, cnt in info['top_skipped'])
                lines.append(f"   最常见扩展名: {ext_text}")
            if info['stat_failed']:
                lines.append(f"   无法读取文件数: {info['stat_failed']}")
        self._scan_diagnosis.clear()
        return (
            "以下文件夹路径存在，但没有找到支持的音频文件（.mp3/.flac/.wav/.aac/.ogg/.m4a/.wma）：\n\n"
            + '\n'.join(lines)
        )

    def _show_scan_diagnosis_if_any(self):
        """独立弹窗模式：若有诊断信息则弹窗提示"""
        text = self._build_diagnosis_text()
        if text:
            messagebox.showinfo("扫描诊断", text)

    def _poll_scan_progress(self):
        """主线程轮询更新进度弹窗（事件驱动 + 100ms fallback）"""
        if not hasattr(self, 'scan_progress_dialog') or not self.scan_progress_dialog.winfo_exists():
            return

        state = self.scan_progress_state
        self.scan_progress_var.set(state['percent'])
        self.scan_progress_msg.set(state['message'])

        if state['done']:
            self.scan_progress_dialog.destroy()
            try:
                delattr(self, 'scan_progress_dialog')
            except AttributeError:
                pass
            if state['error']:
                messagebox.showerror("扫描失败", state['error'])
            else:
                self.update_task_display()
                self.refresh_results(self._last_scan_config, self._last_stats_a,
                                     self._last_stats_b, self._last_duration)
                self.show_toast("✅ 扫描完成")
                rename = self.overview_vars['rename_pending'].get()
                dup = self.overview_vars['duplicate_groups'].get()
                sim = self.overview_vars['similar_groups'].get()
                approx = self.overview_vars['approximate_groups'].get()
                agg = self.overview_vars['agg_files'].get()
                dur = self.overview_vars['duration'].get()
                result_msg = (
                    f"待重命名: {rename}\n"
                    f"重复文件: {dup}\n"
                    f"相似文件: {sim}\n"
                    f"近似文件: {approx}\n"
                    f"聚合去重: {agg}\n"
                    f"扫描耗时: {dur}"
                )
                diag_text = self._build_diagnosis_text()
                extra_parts = []
                if diag_text:
                    extra_parts.append(diag_text)
                mismatches = self._find_tag_filename_mismatches()
                if mismatches:
                    extra_parts.append(self._format_mismatch_text(mismatches))
                self.show_scan_result_dialog(result_msg, extra_text="\n\n".join(extra_parts))
            return

        self._scan_progress_event.wait(0.1)
        self.after(100, self._poll_scan_progress)

    def scan_directory(self, path: str, folder_type: str) -> Dict[str, dict]:
        """扫描目录获取文件列表（不立即读取音频时长，延迟到检测变更后按需读取）"""
        files = {}
        path = path.strip()

        # 调试日志：帮助定位文件夹 B 扫描为 0 的问题
        debug_log = os.path.join(get_app_dir(), 'scan_debug.log')
        def _log(msg):
            try:
                with open(debug_log, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().isoformat()}] [{folder_type}] {msg}\n")
            except Exception:
                pass

        _log(f"scan_directory called path={repr(path)}")
        if not path:
            _log("empty path, returning 0")
            return files
        exists = os.path.exists(path)
        isdir = os.path.isdir(path)
        _log(f"exists={exists} isdir={isdir}")
        if not exists:
            _log("path not exists, returning 0")
            return files

        matched = 0
        skipped_ext = 0
        stat_failed = 0
        walked_dirs = 0
        skipped_ext_samples = {}
        for root, _, filenames in os.walk(path):
            walked_dirs += 1
            for filename in filenames:
                if filename.lower().endswith(('.mp3', '.flac', '.wav', '.aac', '.ogg', '.m4a', '.wma')):
                    full_path = os.path.join(root, filename)
                    try:
                        stat = os.stat(full_path)
                        info = {
                            'name': filename,
                            'size': stat.st_size,
                            'mtime': stat.st_mtime,
                            'ctime': stat.st_ctime,
                            'md5': None,
                            'duration': None,
                        }
                        files[full_path] = info
                        matched += 1
                    except (OSError, IOError) as e:
                        stat_failed += 1
                        _log(f"stat failed: {repr(full_path)} error={e}")
                else:
                    skipped_ext += 1
                    ext = os.path.splitext(filename)[1].lower() or '(no ext)'
                    skipped_ext_samples[ext] = skipped_ext_samples.get(ext, 0) + 1
            if walked_dirs <= 5:
                _log(f"walked dir {walked_dirs}: {repr(root)} files={len(filenames)}")
        top_skipped = sorted(skipped_ext_samples.items(), key=lambda x: -x[1])[:10]
        _log(f"done matched={matched} skipped_ext={skipped_ext} stat_failed={stat_failed} walked_dirs={walked_dirs}")
        _log(f"top skipped extensions: {top_skipped}")

        # 保存诊断信息：路径存在但扫描到 0 个音频文件时，帮助用户定位原因
        if exists and matched == 0:
            self._scan_diagnosis[folder_type] = {
                'path': path,
                'isdir': isdir,
                'walked_dirs': walked_dirs,
                'skipped_ext': skipped_ext,
                'stat_failed': stat_failed,
                'top_skipped': top_skipped,
            }
        else:
            self._scan_diagnosis.pop(folder_type, None)

        return files

    def _apply_cached_durations(self, files: Dict[str, dict], changes: List[FileState]):
        """将历史缓存的音频时长透传到当前文件字典（未变更/移动文件）"""
        for c in changes:
            if c.path in files and c.duration is not None:
                files[c.path]['duration'] = c.duration

    def _read_audio_metadata(self, files: Dict[str, dict], progress_callback=None):
        """
        读取音频文件的时长和标签。
        时长：仅在启用时长过滤时读取
        标签（title/artist）：始终读取（预聚类和 AI 分析需要）
        """
        read_duration = self.scan_options['use_duration'].get()

        # 确定需要读取的文件
        paths = []
        for p, info in files.items():
            need_dur = read_duration and info.get('duration') is None
            need_tags = p not in self.file_tags or not self.file_tags.get(p)
            if need_dur or need_tags:
                paths.append((p, need_dur, need_tags))

        if not paths:
            return

        total = len(paths)
        workers = max(1, min(32, self.worker_count_var.get()))

        def _worker(item):
            path, need_dur, need_tags = item
            duration = None
            try:
                if need_dur and need_tags:
                    # 需要时长和标签 → 同时读取
                    duration = get_audio_duration(path)
                    tags = get_audio_tags(path)
                    self.file_tags[path] = tags
                elif need_tags:
                    # 只需要标签
                    tags = get_audio_tags(path)
                    self.file_tags[path] = tags
                elif need_dur:
                    # 只需要时长
                    duration = get_audio_duration(path)
            except Exception:
                pass
            if progress_callback and total > 0:
                pct = 35 + int(15 * (paths.index((path, need_dur, need_tags)) + 1) / total)
                progress_callback(pct, f"正在读取音频信息 ({paths.index((path, need_dur, need_tags)) + 1}/{total})...")
            return path, duration

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_worker, paths))

        for path, duration in results:
            if path in files and duration is not None:
                files[path]['duration'] = duration

    def _apply_cached_md5(self, files: Dict[str, dict], changes: List[FileState]):
        """将历史缓存的 MD5 透传到当前文件字典（未变更/移动文件）"""
        for c in changes:
            if c.path in files and c.md5_hash:
                files[c.path]['md5'] = c.md5_hash

    def _compute_md5_parallel(self, files: Dict[str, dict], progress_callback=None):
        """多线程并行计算 MD5，并同步回写到 all_files_a/all_files_b"""
        if not files:
            return
        paths = list(files.keys())
        total = len(paths)

        def _worker(i_path):
            idx, path = i_path
            md5 = compute_md5(path)
            if progress_callback and total > 0:
                progress_callback(50 + int(30 * (idx + 1) / total),
                                  f"正在计算 MD5 ({idx + 1}/{total})...")
            return path, md5

        workers = max(1, min(32, self.worker_count_var.get()))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_worker, enumerate(paths)))

        for path, md5 in results:
            if md5:
                files[path]['md5'] = md5
                if path in self.all_files_a:
                    self.all_files_a[path]['md5'] = md5
                if path in self.all_files_b:
                    self.all_files_b[path]['md5'] = md5

    def perform_scan(self, scan_type: ScanType, scan_config: dict = None, progress_callback=None):
        """执行扫描 - 支持只扫描单一文件夹和进度回调"""
        if scan_config is None:
            scan_config = self.get_effective_scan_config()

        # 确保 current_task 路径与 UI 一致（外部直接调用 perform_scan 时也要同步）
        self._sync_task_paths()

        # 调试日志：记录本次扫描配置与路径
        debug_log = os.path.join(get_app_dir(), 'scan_debug.log')
        def _debug(msg):
            try:
                with open(debug_log, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now().isoformat()}] [SCAN] {msg}\n")
            except Exception:
                pass
        task_id = self.current_task.task_id if self.current_task else None
        _debug(f"perform_scan task_id={task_id} scan_config={scan_config}")
        if self.current_task:
            _debug(f"current_task.folder_a={repr(self.current_task.folder_a)} folder_b={repr(self.current_task.folder_b)}")

        def _report(pct: int, msg: str):
            if progress_callback:
                progress_callback(pct, msg)

        start_time = time.time()

        # 初始化统计
        self.duplicate_groups = []
        self.similar_groups = []
        self.approximate_groups = []
        self.change_results = []

        # 1. 扫描文件系统（根据配置选择）
        _report(0, "正在遍历文件夹...")
        self.all_files_a = {}
        self.all_files_b = {}

        if scan_config['scan_folder_a']:
            self.all_files_a = self.scan_directory(self.current_task.folder_a, 'A')
        _report(15, "正在遍历文件夹 B..." if scan_config['scan_folder_b'] else "正在分析文件...")
        if scan_config['scan_folder_b']:
            self.all_files_b = self.scan_directory(self.current_task.folder_b, 'B')

        # 2. 检测文件变化（FULL 也调用，用于复用缓存时长）
        _report(30, "正在检测文件变化...")
        changes_a, stats_a = [], {'new': 0, 'modified': 0, 'unchanged': 0, 'deleted': 0, 'moved': 0}
        changes_b, stats_b = [], {'new': 0, 'modified': 0, 'unchanged': 0, 'deleted': 0, 'moved': 0}

        if scan_config['scan_folder_a'] and self.all_files_a:
            changes_a, stats_a = self.task_manager.detect_changes(
                self.current_task.task_id, self.all_files_a, 'A',
                compare_method=scan_config['compare_method'],
                detect_moved=scan_config['detect_moved']
            )

        if scan_config['scan_folder_b'] and self.all_files_b:
            changes_b, stats_b = self.task_manager.detect_changes(
                self.current_task.task_id, self.all_files_b, 'B',
                compare_method=scan_config['compare_method'],
                detect_moved=scan_config['detect_moved']
            )

        # 仅在增量扫描且非后台线程模式显示变更统计 toast
        if scan_type == ScanType.INCREMENTAL and not progress_callback:
            total_new = stats_a['new'] + stats_b['new']
            total_modified = stats_a['modified'] + stats_b['modified']
            total_deleted = stats_a['deleted'] + stats_b['deleted']
            total_moved = stats_a['moved'] + stats_b['moved']
            folders = []
            if scan_config['scan_folder_a']: folders.append('A')
            if scan_config['scan_folder_b']: folders.append('B')
            self.show_toast(
                f"📊 文件夹{'+'.join(folders)}: 新增{total_new}, 修改{total_modified}, 删除{total_deleted}, 移动{total_moved}",
                duration=5000
            )

        # 3. 应用缓存时长并按需读取新/修改文件的时长
        _report(35, "正在读取音频时长...")
        self._apply_cached_durations(self.all_files_a, changes_a)
        self._apply_cached_durations(self.all_files_b, changes_b)

        # 只有新增/修改文件才需要重新读取时长或计算 MD5
        files_to_process_a = {c.path: self.all_files_a[c.path] for c in changes_a
                              if c.change_status in [ChangeStatus.NEW, ChangeStatus.MODIFIED]}
        files_to_process_b = {c.path: self.all_files_b[c.path] for c in changes_b
                              if c.change_status in [ChangeStatus.NEW, ChangeStatus.MODIFIED]}

        if scan_type == ScanType.FULL:
            # 全新扫描时，对未变更但之前没有缓存时长的文件也尝试读取
            files_to_read_duration_a = self.all_files_a
            files_to_read_duration_b = self.all_files_b
        else:
            files_to_read_duration_a = files_to_process_a
            files_to_read_duration_b = files_to_process_b

        self._read_audio_metadata({**files_to_read_duration_a, **files_to_read_duration_b}, progress_callback=_report)

        # 4. 计算 MD5：应用缓存 + 多线程仅计算新增/修改文件
        _report(50, "正在计算 MD5...")
        self._apply_cached_md5(self.all_files_a, changes_a)
        self._apply_cached_md5(self.all_files_b, changes_b)

        if scan_config['compute_md5']:
            self._compute_md5_parallel({**files_to_process_a, **files_to_process_b}, progress_callback=_report)
        else:
            _report(80, "跳过 MD5 计算...")

        # 5. 查找重复/相似/近似
        _report(80, "正在查找重复/相似/近似文件...")
        self.duplicate_groups = find_duplicates(self.all_files_a, self.all_files_b)

        # 统一计算重复排除路径（避免 find_similar / find_approximate 重复计算）
        dup_paths: set = set()
        for group in self.duplicate_groups:
            for path, _ in group:
                dup_paths.add(path)

        def _similar_progress(current: int, total: int):
            pct = 80 + int(5 * current / total) if total > 0 else 80
            _report(pct, f"正在查找相似文件 ({current}/{total})...")

        self.similar_groups = find_similar(
            self.all_files_a, self.all_files_b,
            progress_callback=_similar_progress,
            excluded_paths=dup_paths
        )

        # 统一计算相似排除路径（避免 find_approximate 内部重复计算）
        sim_paths: set = set()
        for group in self.similar_groups:
            for path, _ in group:
                sim_paths.add(path)

        approx_threshold = self.similarity_var.get() / 100.0
        dur_threshold = (self.duration_threshold_var.get() / 100.0
                         if self.scan_options['use_duration'].get() else None)

        def _approx_progress(current: int, total: int):
            pct = 85 + int(10 * current / total) if total > 0 else 85
            _report(pct, f"正在查找近似文件 ({current}/{total})...")

        self.approximate_groups = find_approximate(
            self.all_files_a, self.all_files_b,
            threshold=approx_threshold,
            duration_threshold=dur_threshold,
            progress_callback=_approx_progress,
            excluded_paths=dup_paths,
            similar_paths=sim_paths
        )

        # 5. 保存结果
        _report(95, "正在保存扫描结果...")
        all_changes = changes_a + changes_b
        # 将读取/缓存的时长和 MD5 回写到变更记录，确保数据库存储最新值
        for c in all_changes:
            if c.path in self.all_files_a:
                c.duration = self.all_files_a[c.path].get('duration')
                c.md5_hash = self.all_files_a[c.path].get('md5')
            elif c.path in self.all_files_b:
                c.duration = self.all_files_b[c.path].get('duration')
                c.md5_hash = self.all_files_b[c.path].get('md5')
        if all_changes:
            self.task_manager.save_file_states(self.current_task.task_id, all_changes)
            self.change_results = all_changes

        # 记录扫描历史（统一存储文件个数而非组数）
        duration = time.time() - start_time
        dup_files = sum(len(g) for g in self.duplicate_groups)
        sim_files = sum(len(g) for g in self.similar_groups)
        approx_files = sum(len(g) for g in self.approximate_groups)
        stats = {
            'total': len(self.all_files_a) + len(self.all_files_b),
            'new': stats_a['new'] + stats_b['new'],
            'modified': stats_a['modified'] + stats_b['modified'],
            'deleted': stats_a['deleted'] + stats_b['deleted'],
            'duplicates': dup_files,
            'similar': sim_files,
            'approximate': approx_files
        }

        self.task_manager.record_scan_history(
            self.current_task.task_id, scan_type, stats, duration
        )

        # 更新任务统计
        self.task_manager.update_task(
            self.current_task.task_id,
            scan_count=self.current_task.scan_count + 1,
            total_files_a=len(self.all_files_a) if scan_config['scan_folder_a'] else self.current_task.total_files_a,
            total_files_b=len(self.all_files_b) if scan_config['scan_folder_b'] else self.current_task.total_files_b
        )
        # 刷新内存中的 task 对象
        self.current_task = self.task_manager.get_task(self.current_task.task_id)

        _report(100, "扫描完成")
        if progress_callback:
            # 后台线程模式：暂存数据，由主线程刷新 UI
            self._last_scan_config = scan_config
            self._last_stats_a = stats_a
            self._last_stats_b = stats_b
            self._last_duration = duration
        else:
            self.update_task_display()
            self.refresh_results(scan_config, stats_a, stats_b, duration)
            folders = []
            if scan_config['scan_folder_a']: folders.append('A')
            if scan_config['scan_folder_b']: folders.append('B')
            self.show_toast(f"✅ 文件夹{'+'.join(folders)}扫描完成! 耗时{duration:.1f}秒")

    def refresh_results(self, scan_config, stats_a, stats_b, duration):
        """刷新结果展示（V12修正：统一显示文件个数而非组数）"""
        dup_files = sum(len(g) for g in self.duplicate_groups)
        sim_files = sum(len(g) for g in self.similar_groups)
        approx_files = sum(len(g) for g in self.approximate_groups)
        agg = dup_files + sim_files + approx_files

        self.overview_vars['rename_pending'].set(str(self._count_rename_pending()))
        self.overview_vars['duplicate_groups'].set(str(dup_files))
        self.overview_vars['similar_groups'].set(str(sim_files))
        self.overview_vars['approximate_groups'].set(str(approx_files))
        self.overview_vars['agg_files'].set(str(agg))
        self.overview_vars['duration'].set(f"{duration:.1f}s")

        # 文件夹统计
        a_new = stats_a.get('new', 0) if stats_a else 0
        a_mod = stats_a.get('modified', 0) if stats_a else 0
        b_new = stats_b.get('new', 0) if stats_b else 0
        b_mod = stats_b.get('modified', 0) if stats_b else 0
        if scan_config.get('scan_folder_a'):
            self.a_stats_var.set(f"📀 {len(self.all_files_a)} 个文件")
        else:
            self.a_stats_var.set("📀 未选择扫描")
        if scan_config.get('scan_folder_b'):
            self.b_stats_var.set(f"📀 {len(self.all_files_b)} 个文件")
        else:
            self.b_stats_var.set("📀 未选择扫描")
        self.a_change_var.set(f"🟢 新增: {a_new}  🟡 修改: {a_mod}")
        self.b_change_var.set(f"🟢 新增: {b_new}  🟡 修改: {b_mod}")

        # 刷新底部双树（保持当前视图）
        self.refresh_bottom_trees()
        self.selection_var.set("已选择: 0 个文件")
        self.selection_detail_var.set("(A: 0, B: 0)")

    def refresh_bottom_trees(self):
        """刷新底部统一双树（含序号列）"""
        left = self.result_tree_a
        right = self.result_tree_b

        # 配置组交替底色 tag
        GROUP_TAGS = ('group_even', 'group_odd')
        for tree in (left, right):
            tree.tag_configure('group_even', background='#dbeafe')    # 淡蓝
            tree.tag_configure('group_odd', background='#fef9c3')     # 淡黄
            tree.tag_configure('group_header', background='#e2e8f0',
                               foreground='#475569', font=('Segoe UI', 9, 'bold'))

        for item in left.get_children(): left.delete(item)
        for item in right.get_children(): right.delete(item)
        self.checked_items[id(left)] = set()
        self.checked_items[id(right)] = set()

        vt = self.result_view_type
        left_counter = [0]
        right_counter = [0]

        def _fmt_no(counter_list):
            counter_list[0] += 1
            return f"{counter_list[0]:05d}"

        def _insert(tree, counter_list, path, info_dict, checkable=True, tags=()):
            if info_dict is None:
                tree.insert('', tk.END, text='', values=(_fmt_no(counter_list), '', '', '', '', ''), tags=tags)
            else:
                sel = '☐' if checkable else ''
                tree.insert('', tk.END, iid=path, text=sel,
                            values=(_fmt_no(counter_list), info_dict['name'], '▶', format_duration(info_dict.get('duration')), self._fmt_size(info_dict['size']), self._fmt_ctime(info_dict['mtime'])),
                            tags=tags)

        if vt == 'dup':
            for gi, group in enumerate(self.duplicate_groups):
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                max_len = max(len(a_items), len(b_items))
                gtag = GROUP_TAGS[gi % 2]
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('dup', gtag))
                    else:
                        _insert(left, left_counter, None, None, tags=('dup', gtag))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('dup', gtag))
                    else:
                        _insert(right, right_counter, None, None, tags=('dup', gtag))
        elif vt == 'sim':
            for gi, group in enumerate(self.similar_groups):
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                # 单侧去重：不勾选→仅显示两侧数量相等的组；勾选→仅显示数量不等的组
                if not self.smart_single_side.get():
                    if not a_items or not b_items or len(a_items) != len(b_items):
                        continue
                else:
                    if a_items and b_items and len(a_items) == len(b_items):
                        continue
                max_len = max(len(a_items), len(b_items))
                gtag = GROUP_TAGS[gi % 2]
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('sim', gtag))
                    else:
                        _insert(left, left_counter, None, None, tags=('sim', gtag))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('sim', gtag))
                    else:
                        _insert(right, right_counter, None, None, tags=('sim', gtag))
        elif vt == 'approx':
            for gi, group in enumerate(self.approximate_groups):
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                # 单侧去重：不勾选→仅显示两侧数量相等的组；勾选→仅显示数量不等的组
                if not self.smart_single_side.get():
                    if not a_items or not b_items or len(a_items) != len(b_items):
                        continue
                else:
                    if a_items and b_items and len(a_items) == len(b_items):
                        continue
                max_len = max(len(a_items), len(b_items))
                gtag = GROUP_TAGS[gi % 2]
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('approx', gtag))
                    else:
                        _insert(left, left_counter, None, None, tags=('approx', gtag))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('approx', gtag))
                    else:
                        _insert(right, right_counter, None, None, tags=('approx', gtag))
        elif vt == 'agg':
            # 聚合去重视图：重复 + 相似 + 近似 依次显示，每组前插入类型分隔行
            sections = (
                ('dup', '── 重复文件 ──', self.duplicate_groups),
                ('sim', '── 相似文件 ──', self.similar_groups),
                ('approx', '── 近似文件 ──', self.approximate_groups),
            )
            for tag_name, header_text, group_list in sections:
                if not group_list:
                    continue
                # 类型分隔行（左右两侧同步插入，不可勾选）
                for tree in (left, right):
                    counter = left_counter if tree is left else right_counter
                    tree.insert('', tk.END, text='',
                                values=(_fmt_no(counter), header_text, '', '', '', ''),
                                tags=('group_header',))
                for gi, group in enumerate(group_list):
                    a_items = [(p, i) for p, i in group if p in self.all_files_a]
                    b_items = [(p, i) for p, i in group if p in self.all_files_b]
                    # 单侧去重过滤仅对相似/近似组生效（重复组不受影响）
                    if tag_name != 'dup':
                        if not self.smart_single_side.get():
                            if not a_items or not b_items or len(a_items) != len(b_items):
                                continue
                        else:
                            if a_items and b_items and len(a_items) == len(b_items):
                                continue
                    max_len = max(len(a_items), len(b_items))
                    gtag = GROUP_TAGS[gi % 2]
                    for i in range(max_len):
                        if i < len(a_items):
                            _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=(tag_name, gtag))
                        else:
                            _insert(left, left_counter, None, None, tags=(tag_name, gtag))
                        if i < len(b_items):
                            _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=(tag_name, gtag))
                        else:
                            _insert(right, right_counter, None, None, tags=(tag_name, gtag))
        elif vt == 'rename':
            # 待重命名视图：显示不符合命名规范的文件（旧名 → 新名）
            import rename_utils
            for d, tree, counter in ((self.all_files_a, left, left_counter),
                                     (self.all_files_b, right, right_counter)):
                for path, info in d.items():
                    name = info.get('name', '')
                    if not name:
                        continue
                    new_name = rename_utils.build_new_filename(name)
                    if new_name == name:
                        continue
                    info2 = dict(info)
                    info2['name'] = f"{name} → {new_name}"
                    _insert(tree, counter, path, info2, tags=('rename',))
        elif vt == 'chg':
            a_changes = [c for c in self.change_results if c.folder_type == 'A']
            b_changes = [c for c in self.change_results if c.folder_type == 'B']
            for c in a_changes:
                info = self.all_files_a.get(c.path, {'name': c.name, 'size': c.size, 'mtime': c.modified_time})
                _insert(left, left_counter, c.path, info, tags=(c.change_status.value,))
            for c in b_changes:
                info = self.all_files_b.get(c.path, {'name': c.name, 'size': c.size, 'mtime': c.modified_time})
                _insert(right, right_counter, c.path, info, tags=(c.change_status.value,))
        elif vt == 'all':
            for path, info in self.all_files_a.items():
                _insert(left, left_counter, path, info, tags=('all',))
            for path, info in self.all_files_b.items():
                _insert(right, right_counter, path, info, tags=('all',))

    def _fmt_size(self, size: int) -> str:
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if abs(size) < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    def _fmt_ctime(self, ts: float) -> str:
        """格式化创建日期"""
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

    def _update_item_tags(self, tree, item, add_tags=(), remove_tags=()):
        tags = list(tree.item(item, 'tags') or ())
        for t in add_tags:
            if t not in tags:
                tags.append(t)
        for t in remove_tags:
            tags = [x for x in tags if x != t]
        tree.item(item, tags=tags)

    def _clear_all_peer_highlights(self):
        for tree in (self.result_tree_a, self.result_tree_b):
            for item in tree.get_children():
                self._update_item_tags(tree, item, remove_tags=('peer_highlight',))

    def _on_tree_select(self, event):
        """Treeview 选中时同步高亮对侧对应行"""
        tree = event.widget
        other = self.result_tree_b if tree == self.result_tree_a else self.result_tree_a

        self._clear_all_peer_highlights()

        selection = tree.selection()
        if not selection:
            return

        idx = tree.index(selection[0])
        children = other.get_children()
        if 0 <= idx < len(children):
            self._update_item_tags(other, children[idx], add_tags=('peer_highlight',))

    def _on_tree_background_click(self, event):
        tree = event.widget
        row = tree.identify_row(event.y)
        if not row:
            for item in tree.selection():
                tree.selection_remove(item)
            self._clear_all_peer_highlights()

    def _set_checked_tag(self, tree, row, checked: bool):
        """设置或移除 checked tag，保留其它 tags"""
        if checked:
            self._update_item_tags(tree, row, add_tags=('checked',))
        else:
            self._update_item_tags(tree, row, remove_tags=('checked',))

    def on_tree_click(self, event):
        """点击 Treeview 切换复选框状态（#0 列）或播放文件（🎵 列）"""
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        if region not in ('cell', 'tree'):
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        col = tree.identify_column(event.x)

        # #3 列 = 播放列
        if col == '#3':
            if row and os.path.isfile(row):
                try:
                    os.startfile(row)
                except Exception as e:
                    messagebox.showerror("播放失败", f"无法打开文件: {e}")
            return

        # #0 列 = 选择列
        if col != '#0':
            return
        current = tree.item(row, 'text')
        if current not in ('☐', '☑'):
            return
        new_text = '☑' if current == '☐' else '☐'
        tree.item(row, text=new_text)
        s = self.checked_items.setdefault(id(tree), set())
        if new_text == '☑':
            s.add(row)
            self._set_checked_tag(tree, row, True)
        else:
            s.discard(row)
            self._set_checked_tag(tree, row, False)
        self.update_selection_stats()

    def update_selection_stats(self):
        """根据复选框状态更新统计"""
        total = 0
        a_count = 0
        b_count = 0
        for idx, tree in enumerate((self.result_tree_a, self.result_tree_b)):
            checked = self.checked_items.get(id(tree), set())
            total += len(checked)
            if idx == 0:
                a_count += len(checked)
            else:
                b_count += len(checked)
        self.selection_var.set(f"已选择: {total} 个文件")
        self.selection_detail_var.set(f"(A: {a_count}, B: {b_count})")

    def on_tree_right_click(self, event):
        """右键菜单"""
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        if region == "cell":
            iid = tree.identify_row(event.y)
            if iid:
                # 右键时自动勾选当前行
                cur = tree.item(iid, 'text')
                if cur in ('☐', '☑'):
                    tree.item(iid, text='☑')
                    self._set_checked_tag(tree, iid, True)
                    self.checked_items.setdefault(id(tree), set()).add(iid)
                    self.update_selection_stats()

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开文件位置", command=lambda: self.open_file_location(tree))
        menu.add_separator()
        menu.add_command(label="移动到回收站", command=self.send_selected_to_trash)
        menu.post(event.x_root, event.y_root)

    def open_file_location(self, tree: ttk.Treeview):
        """在资源管理器中打开选中文件的位置"""
        checked = self.checked_items.get(id(tree), set())
        if not checked:
            return
        path = list(checked)[0]
        if os.path.exists(path):
            import subprocess
            subprocess.run(['explorer', '/select,', os.path.normpath(path)])

    def clear_selection(self):
        """取消所有选择"""
        for tree in (self.result_tree_a, self.result_tree_b):
            self.checked_items[id(tree)] = set()
            for item in tree.get_children():
                cur = tree.item(item, 'text')
                if cur in ('☐', '☑'):
                    tree.item(item, text='☐')
                    self._set_checked_tag(tree, item, False)
        self.update_selection_stats()

    def quick_select(self, result_type: str, folder: str, status: str = None):
        """手动分析功能"""
        if result_type == 'dup':
            self.switch_result_view('dup')
            target = self.result_tree_a if folder == 'A' else self.result_tree_b
        elif result_type == 'chg':
            self.switch_result_view('chg')
            target = self.result_tree_a if folder == 'A' else self.result_tree_b
        elif result_type == 'sim':
            self.switch_result_view('sim')
            target = self.result_tree_a if folder == 'A' else self.result_tree_b
        elif result_type == 'approx':
            self.switch_result_view('approx')
            target = self.result_tree_a if folder == 'A' else self.result_tree_b
        elif result_type == 'agg':
            self.switch_result_view('agg')
            target = self.result_tree_a if folder == 'A' else self.result_tree_b
        else:
            return

        trees = [self.result_tree_a, self.result_tree_b]
        # 先清空所有复选框
        for tree in trees:
            self.checked_items[id(tree)] = set()
            for item in tree.get_children():
                cur = tree.item(item, 'text')
                if cur in ('☐', '☑'):
                    tree.item(item, text='☐')
                    self._set_checked_tag(tree, item, False)

        # 勾选目标（单树模式）
        for item in target.get_children():
            vals = target.item(item, 'values')
            if len(vals) >= 2 and vals[1]:
                if result_type == 'chg' and status:
                    tags = target.item(item, 'tags')
                    if tags and status in str(tags).lower():
                        target.item(item, text='☑')
                        self._set_checked_tag(target, item, True)
                        self.checked_items[id(target)].add(item)
                else:
                    target.item(item, text='☑')
                    self._set_checked_tag(target, item, True)
                    self.checked_items[id(target)].add(item)
        self.update_selection_stats()


    def smart_select(self):
        """智选去重：根据勾选的筛选条件（时长最大>文件最大>最新文件）决定每组保留哪个文件"""
        vt = self.result_view_type
        if vt == 'sim':
            groups = self.similar_groups
            dup_len = 0
        elif vt == 'approx':
            groups = self.approximate_groups
            dup_len = 0
        elif vt == 'agg':
            dup_len = len(self.duplicate_groups)
            groups = self.duplicate_groups + self.similar_groups + self.approximate_groups
        else:
            return  # 'all' 或 'dup' 时不操作

        if not groups:
            return

        use_duration = self.smart_use_duration.get()
        use_size = self.smart_use_size.get()
        use_mtime = self.smart_use_mtime.get()

        if not any([use_duration, use_size, use_mtime]):
            return

        self.switch_result_view(vt)
        trees = [self.result_tree_a, self.result_tree_b]

        # 清空所有勾选
        for tree in trees:
            self.checked_items[id(tree)] = set()
            for item in tree.get_children():
                cur = tree.item(item, 'text')
                if cur in ('☐', '☑'):
                    tree.item(item, text='☐')
                    self._set_checked_tag(tree, item, False)

        checked_paths = set()
        import ai_analyzer as _ai_mod
        for gi, group in enumerate(groups):
            a_items = [(p, i) for p, i in group if p in self.all_files_a]
            b_items = [(p, i) for p, i in group if p in self.all_files_b]
            all_items = a_items + b_items
            if not all_items:
                continue
            # 单侧去重：仅对相似/近似组生效（聚合视图下重复组不受影响）
            if gi >= dup_len:
                if not self.smart_single_side.get():
                    if not a_items or not b_items or len(a_items) != len(b_items):
                        continue
                else:
                    if a_items and b_items and len(a_items) == len(b_items):
                        continue

            # 按歌名+版本 key 子分组：原版/DJ版/伴奏版 各保留一个最优
            sub_groups = {}
            for p, i in all_items:
                k = _ai_mod.get_file_title_key(p, i, self.file_tags)
                if k is None:
                    k = f"__nokey_{p}"
                sub_groups.setdefault(k, []).append((p, i))

            for items in sub_groups.values():
                # 用共享规则选出最优文件
                keep = self._pick_best_file(items)
                if not keep:
                    continue
                # 子组内其余全部勾选
                for path, _ in items:
                    if path != keep:
                        checked_paths.add(path)

        # 在 Treeview 中勾选
        for tree in trees:
            for item in tree.get_children():
                if item in checked_paths:
                    tree.item(item, text='☑')
                    self._set_checked_tag(tree, item, True)
                    self.checked_items[id(tree)].add(item)

        self.update_selection_stats()

    def _get_selected_paths(self) -> List[str]:
        """获取所有选中树中的文件路径"""
        paths = []
        for tree in (self.result_tree_a, self.result_tree_b):
            checked = self.checked_items.get(id(tree), set())
            for item in checked:
                path = item
                if path and os.path.exists(path):
                    paths.append(path)
        return list(set(paths))

    def send_selected_to_trash(self):
        """移动到回收站前需验证作者微信号"""
        paths = self._get_selected_paths()
        if not paths:
            messagebox.showinfo("提示", "请先选择要删除的文件")
            return

        dialog = self._create_dialog("移动到回收站", 340, 180)

        tk.Label(dialog, text="确认移动",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 12, 'bold')).pack(pady=(15, 5))

        tk.Label(dialog, text="请输入作者微信号以确认操作",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack()

        wechat_lbl = tk.Label(dialog, text="点击复制微信号: a_better_day_9",
                bg=self.colors['card'], fg='#60a5fa',
                font=('Segoe UI', 9), cursor='hand2')
        wechat_lbl.pack()
        wechat_lbl.bind("<Button-1>", lambda e: (self.copy_wechat_id(), wechat_var.set("a_better_day_9")))

        wechat_var = tk.StringVar()
        tk.Entry(dialog, textvariable=wechat_var, width=30,
                font=('Segoe UI', 10)).pack(pady=5)

        def confirm():
            if wechat_var.get().strip() != "a_better_day_9":
                messagebox.showwarning("验证失败", "微信号不正确，操作已取消", parent=dialog)
                return
            dialog.destroy()
            self._do_send_selected_to_trash(paths)

        btn_frame = tk.Frame(dialog, bg=self.colors['card'])
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="确认移动",
                 command=confirm,
                 bg='#f59e0b', fg='white', font=('Segoe UI', 10, 'bold'), cursor='hand2').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消",
                 command=dialog.destroy,
                 bg=self.colors['border'], fg=self.colors['text'], font=('Segoe UI', 10), cursor='hand2').pack(side=tk.LEFT, padx=5)

    def _do_send_selected_to_trash(self, paths: List[str]):
        """将选中文件移动到应用内部回收站，并记录原始路径用于撤销"""
        if not messagebox.askyesno("确认删除", f"确定将 {len(paths)} 个文件移动到回收站？"):
            return

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_dir = self.trash_dir / timestamp
        batch_dir.mkdir(parents=True, exist_ok=True)

        records = []
        failed = []
        for src in paths:
            src_path = Path(src)
            dest = batch_dir / src_path.name
            # 处理重名
            counter = 1
            stem = dest.stem
            suffix = dest.suffix
            while dest.exists():
                dest = batch_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            try:
                shutil.move(str(src_path), str(dest))
                records.append({'original': str(src_path), 'trashed': str(dest)})
            except Exception:
                failed.append(src_path.name)

        if records:
            self.last_deleted_records = records
        if failed:
            messagebox.showerror("失败", f"以下文件未能移动：{', '.join(failed)}")
        self.refresh_current_tab()
        if records:
            messagebox.showinfo("提示", "重复文件已经清除，文件列表已刷新")

    def undo_delete(self):
        """撤销最近一次移动到回收站的操作"""
        if not self.last_deleted_records:
            messagebox.showinfo("提示", "没有可撤销的删除操作")
            return

        restored = []
        failed = []
        for rec in self.last_deleted_records:
            original = Path(rec['original'])
            trashed = Path(rec['trashed'])
            if not trashed.exists():
                failed.append(original.name)
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trashed), str(original))
                restored.append(original.name)
                # 将恢复的文件重新加入内存字典
                try:
                    st = os.stat(original)
                    info = {
                        'name': original.name, 'size': st.st_size,
                        'mtime': st.st_mtime, 'ctime': st.st_ctime,
                        'md5': None,
                        'duration': get_audio_duration(str(original)),
                    }
                    sp = str(original).replace('\\', '/')
                    if self.current_task and sp.startswith(self.current_task.folder_a.replace('\\', '/')):
                        self.all_files_a[str(original)] = info
                    elif self.current_task and sp.startswith(self.current_task.folder_b.replace('\\', '/')):
                        self.all_files_b[str(original)] = info
                except Exception:
                    pass
            except Exception:
                failed.append(original.name)

        # 清理已空的批次目录
        if self.last_deleted_records:
            batch_dir = Path(self.last_deleted_records[0]['trashed']).parent
            try:
                if batch_dir.exists() and not any(batch_dir.iterdir()):
                    batch_dir.rmdir()
            except Exception:
                pass

        self.last_deleted_records = []

        if restored:
            self.show_toast(f"↩️ 已恢复 {len(restored)} 个文件")
        if failed:
            messagebox.showerror("恢复失败", f"以下文件未能恢复：{', '.join(failed)}")
        self.refresh_current_tab()

    def clear_trash(self):
        """清空应用内部回收站 AppTrash 目录"""
        if not self.trash_dir.exists() or not any(self.trash_dir.iterdir()):
            messagebox.showinfo("提示", "回收站中没有任何文件")
            return

        if not messagebox.askyesno("确认清空", "确定要永久删除回收站中的所有文件吗？\n此操作不可恢复！"):
            return

        count = 0
        for item in list(self.trash_dir.iterdir()):
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                count += 1
            except Exception:
                pass

        self.last_deleted_records = []
        self.show_toast(f"已清空回收站，删除了 {count} 个文件/文件夹")

    def refresh_current_tab(self):
        """刷新当前标签页（后台线程执行，带进度弹窗）"""
        if not self.current_task:
            return
        config = self.get_effective_scan_config()
        scan_type = ScanType(config['scan_mode'])

        # 进度弹窗
        self.scan_progress_state = {'percent': 0, 'message': '正在刷新列表...', 'done': False, 'error': None}
        dialog = self._create_dialog("刷新列表", 360, 110, grab=False)
        tk.Label(dialog, text="正在刷新列表，请稍候...",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 11, 'bold')).pack(pady=(8, 3))
        self.scan_progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(dialog, variable=self.scan_progress_var, maximum=100, length=300).pack(pady=3)
        self.scan_progress_msg = tk.StringVar(value="正在刷新列表...")
        tk.Label(dialog, textvariable=self.scan_progress_msg,
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack()
        self.scan_progress_dialog = dialog

        thread = threading.Thread(target=self._run_refresh_scan, args=(scan_type, config))
        thread.daemon = True
        thread.start()
        self._poll_refresh_progress()

    def _run_refresh_scan(self, scan_type: ScanType, config: dict):
        """后台线程执行刷新扫描"""
        try:
            def cb(pct, msg):
                with self._scan_state_lock:
                    self.scan_progress_state['percent'] = pct
                    self.scan_progress_state['message'] = msg
                self._scan_progress_event.set()
            self.perform_scan(scan_type, config, progress_callback=cb)
            with self._scan_state_lock:
                self.scan_progress_state['done'] = True
        except Exception as e:
            with self._scan_state_lock:
                self.scan_progress_state['error'] = str(e)
                self.scan_progress_state['done'] = True
        finally:
            self._scan_progress_event.set()

    def _poll_refresh_progress(self):
        """轮询刷新进度"""
        if not hasattr(self, 'scan_progress_dialog') or not self.scan_progress_dialog.winfo_exists():
            return
        state = self.scan_progress_state
        self.scan_progress_var.set(state['percent'])
        self.scan_progress_msg.set(state['message'])
        if state['done']:
            self.scan_progress_dialog.destroy()
            try:
                delattr(self, 'scan_progress_dialog')
            except AttributeError:
                pass
            if state['error']:
                messagebox.showerror("刷新失败", state['error'])
            else:
                self.update_task_display()
                self.refresh_results(self._last_scan_config, self._last_stats_a,
                                     self._last_stats_b, self._last_duration)
                self._show_scan_diagnosis_if_any()
            return
        self._scan_progress_event.wait(0.1)
        self.after(100, self._poll_refresh_progress)

    # ==================== AI 分析 ====================

    def _load_ai_config(self):
        """加载 AI 配置并检查主机名绑定"""
        cfg_path = self.ai_config_path
        if not cfg_path.exists():
            self.ai_config = {}
            return

        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            saved_host = cfg.get('hostname', '')
            current_host = os.environ.get('COMPUTERNAME', '')
            if saved_host and saved_host != current_host:
                # 主机名不匹配，删配置防泄露
                cfg_path.unlink(missing_ok=True)
                self.ai_config = {}
                return
            self.ai_config = cfg
        except Exception:
            self.ai_config = {}

    def _load_fingerprint_cache(self):
        """加载指纹缓存文件"""
        try:
            if self.fingerprint_cache_path.exists():
                with open(self.fingerprint_cache_path, 'r', encoding='utf-8') as f:
                    self.fingerprint_cache = json.load(f)
        except Exception:
            self.fingerprint_cache = {}

    def _load_feedback(self):
        """加载用户反馈记录"""
        try:
            if self.feedback_path.exists():
                with open(self.feedback_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.user_feedback = set(data.get('keep', []))
        except Exception:
            self.user_feedback = set()

    def _save_feedback(self):
        """保存用户反馈记录"""
        try:
            with open(self.feedback_path, 'w', encoding='utf-8') as f:
                json.dump({'keep': list(self.user_feedback)}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _record_feedback(self):
        """
        记录用户当前的手动调整：扫描当前 Treeview，
        将用户手动保留的文件（应为☑但用户改为☐的文件）加入反馈。
        """
        recorded = 0
        for tree in (self.result_tree_a, self.result_tree_b):
            for item in tree.get_children():
                text = tree.item(item, 'text')
                iid = tree.item(item, 'iid') if hasattr(tree, 'item') else ''
                # 如果 AI 之前勾选了但用户取消了 → 记录为保留
                if text == '☐' and item in self.checked_items.get(id(tree), set()):
                    self.user_feedback.add(item)
                    recorded += 1
        if recorded:
            self._save_feedback()
        return recorded

    def clear_feedback(self):
        """清除所有用户反馈记录"""
        self.user_feedback.clear()
        self._save_feedback()

    def _save_ai_config(self, endpoint: str, api_key: str, model: str):
        """保存 AI 配置（含主机名绑定）"""
        cfg = {
            'hostname': os.environ.get('COMPUTERNAME', ''),
            'endpoint': endpoint.rstrip('/'),
            'api_key': api_key,
            'model': model
        }
        try:
            with open(self.ai_config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.ai_config = cfg
        except Exception as e:
            messagebox.showerror("保存失败", f"无法保存 AI 配置: {e}")

    def _configure_api(self):
        """弹出 API 配置对话框"""
        dialog = self._create_dialog("AI 服务配置", 420, 260)
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="API Endpoint",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack(pady=(15, 2))

        endpoint_var = tk.StringVar(value=self.ai_config.get('endpoint', 'https://api.openai.com/v1'))
        tk.Entry(dialog, textvariable=endpoint_var, width=50,
                font=('Segoe UI', 10)).pack(pady=(0, 8))

        tk.Label(dialog, text="API Key",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack(pady=(0, 2))

        key_var = tk.StringVar(value=self.ai_config.get('api_key', ''))
        tk.Entry(dialog, textvariable=key_var, width=50,
                font=('Segoe UI', 10), show='*').pack(pady=(0, 8))

        tk.Label(dialog, text="模型名称",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack(pady=(0, 2))

        model_var = tk.StringVar(value=self.ai_config.get('model', 'gpt-4o-mini'))
        tk.Entry(dialog, textvariable=model_var, width=50,
                font=('Segoe UI', 10)).pack(pady=(0, 12))

        def do_save():
            ep = endpoint_var.get().strip()
            key = key_var.get().strip()
            mdl = model_var.get().strip()
            if not ep or not key or not mdl:
                messagebox.showwarning("配置不完整", "请填写所有字段", parent=dialog)
                return
            self._save_ai_config(ep, key, mdl)
            dialog.destroy()
            messagebox.showinfo("配置已保存", "AI 配置已保存，点击「AI 分析」即可使用")

        btn_frame = tk.Frame(dialog, bg=self.colors['card'])
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="保存",
                 command=do_save,
                 bg='#f59e0b', fg='white', font=('Segoe UI', 10, 'bold'), cursor='hand2').pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消",
                 command=dialog.destroy,
                 bg=self.colors['border'], fg=self.colors['text'], font=('Segoe UI', 10), cursor='hand2').pack(side=tk.LEFT, padx=5)

    def _run_ai_analysis(self):
        """运行 AI 分析：将当前视图分组发送到 AI 判断是否为同一首歌"""
        vt = self.result_view_type
        if vt not in ('sim', 'approx', 'agg'):
            messagebox.showinfo("提示", "请在相似文件、近似文件或聚合去重视图下使用 AI 分析")
            return

        if not self.ai_config.get('endpoint') or not self.ai_config.get('api_key'):
            ret = messagebox.askyesno("未配置 API", "尚未配置 AI 服务，是否前往配置？")
            if ret:
                self._configure_api()
            return

        # 聚合视图：只分析相似+近似组（重复文件内容相同无需 AI）
        if vt == 'agg':
            groups = self.similar_groups + self.approximate_groups
        else:
            groups = self.similar_groups if vt == 'sim' else self.approximate_groups
        if not groups:
            messagebox.showinfo("提示", "当前视图中没有分组数据")
            return

        # 构建 file_tags 字典（仅包含当前分组涉及的文件）
        all_paths = set()
        for group in groups:
            for path, _ in group:
                all_paths.add(path)
        tags_subset = {p: self.file_tags.get(p, {}) for p in all_paths}

        # 进度窗口
        total_groups = len(groups)
        progress_dialog = self._create_dialog("AI 分析", 380, 130)
        tk.Label(progress_dialog, text=f"正在分析 {total_groups} 组...",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(pady=(15, 5))
        progress_var = tk.StringVar(value="准备中...")
        tk.Label(progress_dialog, textvariable=progress_var,
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 9)).pack()
        progress_dialog.update()

        def _on_progress(current, total, msg):
            progress_var.set(msg)
            progress_dialog.update()

        try:
            judgments = ai_analyzer.analyze_groups(
                groups, tags_subset, self.ai_config,
                progress_callback=_on_progress
            )
            self.ai_judgments = judgments  # 保存 AI 结果供导出使用
            self.ai_history.append({
                'time': datetime.now().isoformat(),
                'view': vt,
                'judgments': judgments,
                'total_groups': total_groups,
            })
        except Exception as e:
            progress_dialog.destroy()
            messagebox.showerror("AI 分析失败", str(e))
            return

        progress_dialog.destroy()

        # 音频指纹验证（勾选后对 AI 聚类结果做硬件指纹比对）
        if self.use_fingerprint.get() and judgments:
            self._verify_fingerprints(judgments, groups)

        if not judgments:
            messagebox.showinfo("AI 分析", "AI 未返回有效结果")
            return

        # 应用聚类结果（勾选/保留逻辑）
        same_count, diff_count, error_count, checked_paths = self._apply_ai_clusters(
            judgments, groups, vt)

        # 聚合视图：重复组内容完全相同，无需 AI，直接按规则勾选保留最优
        dup_checked = 0
        if vt == 'agg' and self.duplicate_groups:
            for group in self.duplicate_groups:
                all_items = [(p, i) for p, i in group]
                if not all_items:
                    continue
                keep = self._pick_best_file(all_items)
                if not keep:
                    continue
                for path, _ in all_items:
                    if path != keep and (not self.use_learning.get() or path not in self.user_feedback):
                        checked_paths.add(path)
                        dup_checked += 1

        if not checked_paths:
            msg = f"AI 分析了 {total_groups} 组"
            if same_count:
                msg += f"，{same_count} 个同歌组已处理但无需勾选"
            else:
                msg += "，未发现需要去重的文件"
            messagebox.showinfo("AI 分析", msg)
            return

        self.switch_result_view(vt)
        trees = [self.result_tree_a, self.result_tree_b]

        for tree in trees:
            self.checked_items[id(tree)] = set()
            for item in tree.get_children():
                cur = tree.item(item, 'text')
                if cur in ('☐', '☑'):
                    tree.item(item, text='☐')
                    self._set_checked_tag(tree, item, False)

        for tree in trees:
            for item in tree.get_children():
                if item in checked_paths:
                    tree.item(item, text='☑')
                    self._set_checked_tag(tree, item, True)
                    self.checked_items[id(tree)].add(item)

        self.update_selection_stats()

        summary = (f"AI 分析了 {total_groups} 组\n"
                   f"• {same_count} 个同歌组已自动勾选，保留最优文件\n"
                   f"• {diff_count} 个不同歌曲已保留")
        if vt == 'agg' and dup_checked:
            summary += f"\n• 重复文件组已按规则勾选 {dup_checked} 个冗余文件"
        if error_count:
            summary += f"\n⚠ {error_count} 批分析失败（可重新运行 AI 分析重试）"
        summary += "\n\n请检查勾选结果后移入回收站"
        messagebox.showinfo("AI 分析完成", summary)

    def _apply_ai_clusters(self, judgments: list, groups: list, vt: str) -> tuple:
        """
        根据 AI 聚类结果计算应勾选的文件路径。

        Returns:
            (same_count, diff_count, error_count, checked_paths)
        """
        same_count = 0
        diff_count = 0
        error_count = 0
        checked_paths = set()

        for j in judgments:
            gi = j.get('group_index')
            if gi == -1:
                error_count += 1
                continue
            if gi is None or gi >= len(groups):
                continue

            clusters = j.get('clusters', [])
            if not clusters:
                continue

            group = groups[gi]
            total_in_group = len(group)

            for cluster in clusters:
                if len(cluster) < 2:
                    diff_count += 1
                    continue

                same_count += 1
                cluster_idx = [idx for idx in cluster if idx < total_in_group]
                if len(cluster_idx) < 2:
                    continue

                # 版本子分组：原版/DJ版/伴奏版 各保留一个最优（key 不同 → 分别处理）
                import ai_analyzer as _ai_mod
                sub = {}
                for idx in cluster_idx:
                    path, info = group[idx]
                    k = _ai_mod.get_file_title_key(path, info, self.file_tags)
                    if k is None:
                        k = f"__nokey_{path}"
                    sub.setdefault(k, []).append(idx)

                for idxs in sub.values():
                    if len(idxs) < 2:
                        continue  # 单文件版本：单独保留，不勾选
                    cluster_paths = [group[i][0] for i in idxs]
                    cluster_a = [(p, info) for p, info in group if p in cluster_paths and p in self.all_files_a]
                    cluster_b = [(p, info) for p, info in group if p in cluster_paths and p in self.all_files_b]
                    all_items = cluster_a + cluster_b
                    if not all_items:
                        continue

                    # 选最优文件
                    keep = self._pick_best_file(all_items)
                    if not keep:
                        continue

                    for path, _ in all_items:
                        if path != keep and (not self.use_learning.get() or path not in self.user_feedback):
                            checked_paths.add(path)

        return same_count, diff_count, error_count, checked_paths

    def _pick_best_file(self, candidates: list) -> Optional[str]:
        """
        从候选文件列表中按规则选出应保留的最优文件。
        规则：伴奏版优先 → Live版删除(≤5s) → 时长最大 → 文件最大 → 最新文件 → A侧优先
        """
        if not candidates:
            return None

        cands = candidates[:]
        use_duration = self.smart_use_duration.get()
        use_size = self.smart_use_size.get()
        use_mtime = self.smart_use_mtime.get()

        # (1)/(2) 数字后缀副本排最后：优先保留不带副本后缀的原版
        import rename_utils as _ru
        non_copy = [(p, i) for p, i in cands if not _ru.is_copy_suffix(i.get('name', ''))]
        if non_copy:
            cands = non_copy

        # 伴奏版优先
        acc = [(p, i) for p, i in cands if '伴奏' in i.get('name', '')]
        if acc:
            cands = acc

        # Live 版优先删除（「选live版」勾选时生效）
        live = [(p, i) for p, i in cands if 'live' in i.get('name', '').lower()]
        non_live = [(p, i) for p, i in cands if 'live' not in i.get('name', '').lower()]
        if self.use_live_priority.get() and live and non_live:
            for _, li in live:
                for _, ni in non_live:
                    ld = li.get('duration')
                    nd = ni.get('duration')
                    if ld is not None and nd is not None and abs(ld - nd) <= 5:
                        cands = non_live
                        break
                else:
                    continue
                break

        if use_duration:
            wd = [(p, i) for p, i in cands if i.get('duration') is not None]
            if wd:
                md = max(i['duration'] for _, i in wd)
                cands = [(p, i) for p, i in wd if i['duration'] == md]

        if use_size and len(cands) > 1:
            ms = max(i['size'] for _, i in cands)
            cands = [(p, i) for p, i in cands if i['size'] == ms]

        if use_mtime and len(cands) > 1:
            mm = max(i['mtime'] for _, i in cands)
            cands = [(p, i) for p, i in cands if i['mtime'] == mm]

        a_cand = [(p, i) for p, i in cands if p in self.all_files_a]
        return a_cand[0][0] if a_cand else cands[0][0]

    def _verify_fingerprints(self, judgments: list, groups: list):
        """
        对 AI 聚类结果做音频指纹验证。
        同一 cluster 内指纹不一致的文件拆分为独立 cluster。
        指纹结果缓存到本地文件，避免重复计算。
        """
        fp_dialog = self._create_dialog("音频指纹验证", 350, 100)
        tk.Label(fp_dialog, text="正在进行音频指纹验证...",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(pady=(20, 5))
        fp_var = tk.StringVar(value="计算中...")
        tk.Label(fp_dialog, textvariable=fp_var,
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 9)).pack()
        fp_dialog.update()

        import fingerprint as fp_mod
        import ai_analyzer as ai_mod
        new_cache = {}  # 存本次新计算的指纹
        verified_count = 0

        def _get_fp(path):
            """从缓存或计算获取指纹"""
            if path in self.fingerprint_cache:
                return self.fingerprint_cache[path]
            if path in new_cache:
                return new_cache[path]
            fp = fp_mod.compute_fingerprint(path)
            if fp:
                new_cache[path] = fp
            return fp

        def _cluster_all_same_title(cluster, group):
            """cluster 内所有文件歌名 key 是否相同（预聚类同歌则信任歌名，不拆）"""
            keys = set()
            for idx in cluster:
                if idx >= len(group):
                    return False
                path, info = group[idx]
                k = ai_mod.get_file_title_key(path, info, self.file_tags)
                if k is None:
                    return False
                keys.add(k)
            return len(keys) == 1

        for j in judgments:
            gi = j.get('group_index')
            clusters = j.get('clusters', [])
            if gi is None or gi >= len(groups):
                continue
            group = groups[gi]
            new_clusters = []
            for cluster in clusters:
                if len(cluster) < 2:
                    new_clusters.append(cluster)
                    continue
                # 歌名 key 全部相同 → 信任歌名判定（同歌不同版本/格式），跳过指纹拆分
                if _cluster_all_same_title(cluster, group):
                    new_clusters.append(cluster)
                    continue
                cluster_paths = [group[idx][0] for idx in cluster if idx < len(group)]
                if len(cluster_paths) < 2:
                    new_clusters.append(cluster)
                    continue
                fp0 = _get_fp(cluster_paths[0])
                if fp0 is None:
                    new_clusters.append(cluster)
                    continue
                match_groups = [[cluster[0]]]
                for idx in cluster[1:]:
                    path = group[idx][0]
                    fp = _get_fp(path)
                    if fp is None or fp == fp0:
                        match_groups[-1].append(idx)
                    else:
                        match_groups.append([idx])
                new_clusters.extend(match_groups)
                verified_count += 1
                fp_var.set(f"已验证 {verified_count} 个聚类...")
                fp_dialog.update()
            j['clusters'] = new_clusters
        fp_dialog.destroy()

        # 保存新计算的指纹到缓存
        if new_cache:
            self.fingerprint_cache.update(new_cache)
            try:
                with open(self.fingerprint_cache_path, 'w', encoding='utf-8') as f:
                    json.dump(self.fingerprint_cache, f, ensure_ascii=False)
            except Exception:
                pass

    # ==================== 文件重命名（待重命名） ====================

    def _count_rename_pending(self) -> int:
        """统计当前任务中文件名不符合命名规范的文件数"""
        import rename_utils
        count = 0
        for d in (self.all_files_a, self.all_files_b):
            for info in d.values():
                name = info.get('name', '')
                if name and rename_utils.should_rename(name):
                    count += 1
        return count

    def _find_tag_filename_mismatches(self) -> list:
        """检测文件名与标签 title 不一致的文件，返回 [(name, tag_title), ...]"""
        import ai_analyzer as _ai
        mismatches = []
        for d in (self.all_files_a, self.all_files_b):
            for path, info in d.items():
                name = info.get('name', '')
                if not name:
                    continue
                tags = self.file_tags.get(path, {})
                tag_title = tags.get('title')
                if not tag_title or not tag_title.strip():
                    continue  # 无标签不比较
                file_key = _ai._extract_title_key(name)
                if file_key is None:
                    continue
                tag_key = _ai.normalize_tag_title(tag_title, name)
                if tag_key is None:
                    continue  # 标签不可用不比较
                if file_key != tag_key:
                    mismatches.append((name, tag_title.strip()))
        return mismatches

    def _format_mismatch_text(self, mismatches: list) -> str:
        """生成标签不一致提示文本（数量 + 前 5 个示例）"""
        total = len(mismatches)
        lines = [f"⚠ {total} 个文件文件名与标签不一致（可能影响 AI 去重判断）："]
        for name, tag in mismatches[:5]:
            lines.append(f"  • {name}  →  标签: {tag}")
        if total > 5:
            lines.append(f"  …等 {total} 个，请用 Mp3tag 修正标签")
        return "\n".join(lines)

    def _load_rename_log(self):
        """加载重命名日志（用于恢复功能）"""
        try:
            if self.rename_log_path.exists():
                with open(self.rename_log_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.rename_log = data.get('entries', [])
        except Exception:
            self.rename_log = []

    def _save_rename_log(self):
        """保存重命名日志"""
        try:
            with open(self.rename_log_path, 'w', encoding='utf-8') as f:
                json.dump({'entries': self.rename_log}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _build_rename_plan(self) -> list:
        """构建待重命名计划 [(old_path, old_name, new_name, reason), ...]
        reason 非空 = 无法自动修复，需弹窗人工确认"""
        import rename_utils
        plan = []
        for d in (self.all_files_a, self.all_files_b):
            for path, info in d.items():
                name = info.get('name', '')
                if not name:
                    continue
                reason = rename_utils.detect_manual_review(name)
                new_name = rename_utils.build_new_filename(name)
                if new_name != name or reason:
                    plan.append((path, name, new_name, reason))
        return plan

    def _unique_target_path(self, folder: str, new_name: str, old_path: str) -> str:
        """目标文件名冲突时自动加后缀 (1)、(2)…"""
        target = os.path.join(folder, new_name)
        n = 1
        while os.path.exists(target) and target != old_path:
            base, ext = os.path.splitext(new_name)
            target = os.path.join(folder, f"{base}({n}){ext}")
            n += 1
        return target

    def _open_long_name_analyzer(self):
        """超长文件分析窗：阈值默认 120 可调，即时刷新列出超长文件，可批量删除或进入下一步"""
        dlg = self._create_dialog("超长文件分析", 780, 540)
        frame = tk.Frame(dlg, bg=self.colors['card'])
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # 顶部：阈值输入 + 说明
        top = tk.Frame(frame, bg=self.colors['card'])
        top.pack(fill=tk.X, pady=(0, 6))
        tk.Label(top, text="超长阈值（文件名 ≥）", bg=self.colors['card'],
                 fg=self.colors['text'], font=('Segoe UI', 10)).pack(side=tk.LEFT)
        threshold_var = tk.StringVar(value='120')
        thr_entry = tk.Entry(top, textvariable=threshold_var, width=8,
                             font=('Consolas', 10), bg='#d1d5db', fg='#1f2937',
                             highlightthickness=0, justify='center')
        thr_entry.pack(side=tk.LEFT, padx=(6, 2))
        tk.Label(top, text="字符", bg=self.colors['card'], fg=self.colors['text'],
                 font=('Segoe UI', 10)).pack(side=tk.LEFT)
        tk.Label(top, text="超长文件可能影响路径上限与显示，建议处理",
                 bg=self.colors['card'], fg=self.colors['accent2'],
                 font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=(12, 0))

        count_label = tk.Label(frame, text="超长文件: 0 个",
                               bg=self.colors['card'], fg=self.colors['text'],
                               font=('Segoe UI', 10, 'bold'))
        count_label.pack(anchor='w', pady=(0, 4))

        list_frame = tk.Frame(frame, bg=self.colors['card'])
        list_frame.pack(fill=tk.BOTH, expand=True)
        lb = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                        font=('Consolas', 9), bg='#d1d5db', fg='#1f2937',
                        highlightthickness=0, activestyle='none')
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 当前命中列表 [(path, name)]，与列表行对应
        hits = []
        deleted = {'n': 0}

        def _all_long_paths(thr):
            out = []
            for d in (self.all_files_a, self.all_files_b):
                for path, info in d.items():
                    name = info.get('name', '')
                    if name and len(name) >= thr:
                        out.append((path, name))
            out.sort(key=lambda x: len(x[1]), reverse=True)
            return out

        def _refresh():
            try:
                thr = int(threshold_var.get())
                if thr < 1:
                    raise ValueError
            except (ValueError, TypeError):
                count_label.config(text="超长文件: 无效阈值")
                return
            hits.clear()
            for path, name in _all_long_paths(thr):
                hits.append((path, name))
            lb.delete(0, tk.END)
            for path, name in hits:
                lb.insert(tk.END, f"{len(name):>4}  {name}")
            count_label.config(text=f"超长文件: {len(hits)} 个（阈值 ≥ {thr} 字符）")
            if hits:
                lb.selection_set(0, tk.END)  # 默认全选

        def _on_key(e):
            # 只允许数字
            if not threshold_var.get().isdigit():
                threshold_var.set(re.sub(r'\D', '', threshold_var.get()))
            _refresh()

        thr_entry.bind('<KeyRelease>', _on_key)
        _refresh()

        btn_bar = tk.Frame(frame, bg=self.colors['card'])
        btn_bar.pack(fill=tk.X, pady=(10, 0))

        def _batch_delete():
            if not hits:
                return
            sel = lb.curselection()
            if not sel:
                return
            targets = [hits[i][0] for i in sel]
            ok = messagebox.askyesno(
                "批量删除确认",
                f"确定将 {len(targets)} 个超长文件移入回收站？\n\n（可在回收站中还原，删除后不参与后续重命名）",
                parent=dlg)
            if not ok:
                return
            try:
                if send_to_trash(targets):
                    for i in sorted(sel, reverse=True):
                        p = hits[i][0]
                        if p in self.all_files_a:
                            del self.all_files_a[p]
                        if p in self.all_files_b:
                            del self.all_files_b[p]
                    deleted['n'] += len(targets)
                    # 同步刷新概览统计
                    self.overview_vars['rename_pending'].set(str(self._count_rename_pending()))
                    if hasattr(self, 'a_stats_var'):
                        self.a_stats_var.set(f"📀 {len(self.all_files_a)} 个文件")
                    if hasattr(self, 'b_stats_var'):
                        self.b_stats_var.set(f"💿 {len(self.all_files_b)} 个文件")
                    messagebox.showinfo("删除完成",
                                        f"已移入回收站 {len(targets)} 个文件",
                                        parent=dlg)
                    _refresh()
                else:
                    messagebox.showwarning("删除失败", "部分文件未能移入回收站", parent=dlg)
            except Exception as e:
                messagebox.showerror("删除失败", str(e), parent=dlg)

        def _next():
            dlg.destroy()
            self._open_rename_manager()

        btn_cfg = dict(bg=self.colors['border'], fg='white', font=('Segoe UI', 10, 'bold'),
                       cursor='hand2')
        tk.Button(btn_bar, text="批量删除（移入回收站）", command=_batch_delete,
                  bg='#ef4444', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="下一步（重命名管理）", command=_next,
                  bg='#22c55e', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.RIGHT, padx=4)
        tk.Button(btn_bar, text="关闭", command=dlg.destroy, **btn_cfg).pack(side=tk.RIGHT, padx=4)

        dlg.protocol('WM_DELETE_WINDOW', dlg.destroy)

    def _open_rename_manager(self):
        """打开重命名管理弹窗（重命名 + 恢复）"""
        # 打开时重新从磁盘加载重命名日志，保证恢复页与磁盘一致
        self._load_rename_log()
        self._rename_dirty = 0  # 本次会话执行的重命名/恢复数（关闭时提示重扫）

        dlg = self._create_dialog("重命名管理", 780, 560)
        dlg.transient(self)
        dlg.grab_set()

        btn_cfg = dict(
            bg=self.colors['border'], fg=self.colors['text'],
            font=('Segoe UI', 9), cursor='hand2',
            relief='raised', bd=1
        )

        # 顶部：页面切换
        top_bar = tk.Frame(dlg, bg=self.colors['card'])
        top_bar.pack(fill=tk.X, padx=10, pady=(10, 4))
        tab_rename = tk.Button(top_bar, text="重命名", **btn_cfg)
        tab_rename.pack(side=tk.LEFT, padx=2)
        tab_restore = tk.Button(top_bar, text="恢复", **btn_cfg)
        tab_restore.pack(side=tk.LEFT, padx=2)

        # ── 重命名页 ──
        rename_frame = tk.Frame(dlg, bg=self.colors['card'])
        plan = self._build_rename_plan()

        info_label = tk.Label(rename_frame, text=f"待重命名: {len(plan)} 个文件（勾选后执行）",
                              bg=self.colors['card'], fg=self.colors['text'],
                              font=('Segoe UI', 10))
        info_label.pack(anchor='w', padx=10, pady=(2, 4))
        self._rename_info_label = info_label

        list_frame = tk.Frame(rename_frame, bg=self.colors['card'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        lb = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                        font=('Consolas', 9), bg='#d1d5db', fg='#1f2937',
                        highlightthickness=0, activestyle='none')
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for old_path, old_name, new_name, reason in plan:
            if reason:
                lb.insert(tk.END, f"⚠ {old_name}  →  (需手动输入)  [{reason}]")
            else:
                lb.insert(tk.END, f"{old_name}  →  {new_name}")
        if plan:
            lb.selection_set(0, tk.END)  # 默认全选

        btn_bar = tk.Frame(rename_frame, bg=self.colors['card'])
        btn_bar.pack(fill=tk.X, padx=10, pady=8)

        def _select_all():
            lb.selection_set(0, tk.END)

        def _invert_sel():
            all_idx = list(range(lb.size()))
            cur = set(lb.curselection())
            lb.selection_clear(0, tk.END)
            for i in all_idx:
                if i not in cur:
                    lb.selection_set(i)

        tk.Button(btn_bar, text="全选", command=_select_all, **btn_cfg).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="反选", command=_invert_sel, **btn_cfg).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="重命名", command=self._do_rename,
                  bg='#f59e0b', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.RIGHT, padx=2)

        def _show_rename():
            restore_frame.pack_forget()
            rename_frame.pack(fill=tk.BOTH, expand=True)

        def _show_restore():
            rename_frame.pack_forget()
            restore_frame.pack(fill=tk.BOTH, expand=True)
            self._refresh_restore_list()

        # ── 恢复页 ──
        restore_frame = tk.Frame(dlg, bg=self.colors['card'])

        restore_info = tk.Label(restore_frame, text=f"已重命名: {len(self.rename_log)} 个文件（勾选后恢复原名）",
                                bg=self.colors['card'], fg=self.colors['text'],
                                font=('Segoe UI', 10))
        restore_info.pack(anchor='w', padx=10, pady=(2, 4))
        self._restore_info_label = restore_info

        rlist_frame = tk.Frame(restore_frame, bg=self.colors['card'])
        rlist_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        rlb = tk.Listbox(rlist_frame, selectmode=tk.EXTENDED,
                         font=('Consolas', 9), bg='#d1d5db', fg='#1f2937',
                         highlightthickness=0, activestyle='none')
        rsb = tk.Scrollbar(rlist_frame, orient=tk.VERTICAL, command=rlb.yview)
        rlb.config(yscrollcommand=rsb.set)
        rsb.pack(side=tk.RIGHT, fill=tk.Y)
        rlb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for entry in self.rename_log:
            old = os.path.basename(entry.get('old_path', ''))
            new = os.path.basename(entry.get('new_path', ''))
            rlb.insert(tk.END, f"{new}  →  {old}")
        if self.rename_log:
            rlb.selection_set(0, tk.END)

        rbtn_bar = tk.Frame(restore_frame, bg=self.colors['card'])
        rbtn_bar.pack(fill=tk.X, padx=10, pady=8)

        def _rselect_all():
            rlb.selection_set(0, tk.END)

        tk.Button(rbtn_bar, text="全选", command=_rselect_all, **btn_cfg).pack(side=tk.LEFT, padx=2)
        tk.Button(rbtn_bar, text="恢复原名", command=self._do_restore,
                  bg='#22c55e', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.RIGHT, padx=2)

        tab_rename.config(command=_show_rename)
        tab_restore.config(command=_show_restore)

        _show_rename()
        self._rename_dialog = dlg
        self._rename_plan = plan
        self._rename_listbox = lb
        self._restore_listbox = rlb
        dlg.protocol('WM_DELETE_WINDOW', self._on_rename_dialog_close)

    def _on_rename_dialog_close(self):
        """弹窗关闭：若本次执行过重命名/恢复，提示是否重新扫描"""
        dlg = getattr(self, '_rename_dialog', None)
        if dlg is None:
            return
        dirty = getattr(self, '_rename_dirty', 0)
        if dirty > 0:
            ret = messagebox.askyesno("重新扫描",
                                      f"已重命名/恢复 {dirty} 个文件，\n"
                                      "是否立即重新扫描以刷新下方列表？",
                                      parent=dlg)
            if ret:
                dlg.destroy()
                self._rename_dialog = None
                self.start_scan_with_task()
                return
        dlg.destroy()
        self._rename_dialog = None

    def _ask_manual_review(self, old_name: str, reason: str, parent):
        """人工确认单窗：加宽输入框（预填原名）+ 四键（是/否/删除/全部否）
        返回 (action, new_name)：action ∈ {'yes','no','delete','abort'}"""
        width = max(80, len(old_name) + 10)
        w = min(920, max(620, width * 7 + 140))
        dlg = self._create_dialog("需手动修改", w, 300)
        frame = tk.Frame(dlg, bg=self.colors['card'])
        frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)

        tk.Label(frame, text="该文件无法自动修复，请手动处理：",
                 bg=self.colors['card'], fg=self.colors['text'],
                 font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        tk.Label(frame, text=f"原因：{reason}",
                 bg=self.colors['card'], fg=self.colors['accent2'],
                 font=('Segoe UI', 9)).pack(anchor='w', pady=(2, 8))

        tk.Label(frame, text="文件名（可修改）：",
                 bg=self.colors['card'], fg=self.colors['text'],
                 font=('Segoe UI', 9)).pack(anchor='w')
        entry = tk.Entry(frame, font=('Consolas', 10), width=width,
                         bg='#d1d5db', fg='#1f2937', highlightthickness=0)
        entry.insert(0, old_name)
        entry.pack(fill=tk.X, pady=(2, 4))
        entry.focus_set()
        entry.selection_range(0, tk.END)

        btn_bar = tk.Frame(frame, bg=self.colors['card'])
        btn_bar.pack(fill=tk.X, pady=(12, 0))
        result = {'action': 'abort', 'name': old_name}

        def _pick(action):
            if action == 'yes':
                v = entry.get().strip()
                if not v:
                    return  # 空名无效，保持弹窗
                result['name'] = v
            result['action'] = action
            dlg.destroy()

        tk.Button(btn_bar, text="是（应用修改）", command=lambda: _pick('yes'),
                  bg='#22c55e', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="否（跳过）", command=lambda: _pick('no'),
                  bg=self.colors['border'], fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="删除（移入回收站）", command=lambda: _pick('delete'),
                  bg='#ef4444', fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="全部否（退出）", command=lambda: _pick('abort'),
                  bg=self.colors['border'], fg='white', font=('Segoe UI', 10, 'bold'),
                  cursor='hand2').pack(side=tk.LEFT, padx=4)

        entry.bind('<Return>', lambda e: _pick('yes'))
        dlg.protocol('WM_DELETE_WINDOW', lambda: _pick('abort'))
        dlg.wait_window()
        return result['action'], result['name']

    def _do_rename(self):
        """执行勾选的重命名"""
        dlg = getattr(self, '_rename_dialog', None)
        lb = getattr(self, '_rename_listbox', None)
        plan = getattr(self, '_rename_plan', [])
        if lb is None or plan is None:
            return
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先勾选要重命名的文件", parent=dlg)
            return

        done = 0
        failed = []
        done_idx = []
        # 第一阶段：批量执行能自动修复的项
        for idx in sel:
            if idx >= len(plan):
                continue
            old_path, old_name, new_name, reason = plan[idx]
            if reason:
                continue  # 需人工的留到第二阶段
            folder = os.path.dirname(old_path)
            target = self._unique_target_path(folder, new_name, old_path)
            if target == old_path:
                continue
            try:
                os.rename(old_path, target)
                self.rename_log.append({
                    'old_path': old_path,
                    'new_path': target,
                    'renamed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
                done += 1
                done_idx.append(idx)
            except Exception as e:
                failed.append((old_name, str(e)))
        # 第二阶段：需人工的文件逐个弹窗确认（单窗四键 + 加宽输入框）
        import rename_utils
        for idx in sel:
            if idx >= len(plan):
                continue
            old_path, old_name, new_name, reason = plan[idx]
            if not reason:
                continue
            action, new_input = self._ask_manual_review(old_name, reason, dlg)
            if action == 'abort':
                break  # 全部否，退出后续人工处理
            if action == 'no':
                continue  # 跳过该文件
            if action == 'delete':
                # 删除（移入回收站，不确认直接删）
                try:
                    if send_to_trash([old_path]):
                        done += 1
                        done_idx.append(idx)
                    else:
                        failed.append((old_name, '移入回收站失败'))
                except Exception as e:
                    failed.append((old_name, str(e)))
                continue
            # action == 'yes'：应用输入框新名
            new_input = new_input.strip()
            # 无扩展名时自动补原扩展名
            if '.' not in os.path.basename(new_input) and '.' in old_name:
                new_input += os.path.splitext(old_name)[1]
            # 非法字符校验
            if rename_utils._ILLEGAL_CHARS & set(new_input):
                messagebox.showwarning("无效文件名",
                                       "文件名含非法字符：\\ / : * ? \" < > |",
                                       parent=dlg)
                continue
            folder = os.path.dirname(old_path)
            target = self._unique_target_path(folder, new_input, old_path)
            if target == old_path:
                continue
            try:
                os.rename(old_path, target)
                self.rename_log.append({
                    'old_path': old_path,
                    'new_path': target,
                    'renamed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
                done += 1
                done_idx.append(idx)
            except Exception as e:
                failed.append((old_name, str(e)))
        self._save_rename_log()

        # 弹窗内即时刷新：从 plan 和列表移除已处理项（倒序删除保持索引）
        for idx in sorted(done_idx, reverse=True):
            del plan[idx]
            lb.delete(idx)
        il = getattr(self, '_rename_info_label', None)
        if il is not None:
            il.config(text=f"待重命名: {len(plan)} 个文件（勾选后执行）")
        self._rename_dirty = getattr(self, '_rename_dirty', 0) + done
        self._refresh_restore_list()

        if failed:
            detail = "\n".join(f"• {n}: {err}" for n, err in failed[:10])
            messagebox.showwarning("部分失败",
                                   f"成功 {done} 个，失败 {len(failed)} 个：\n{detail}",
                                   parent=dlg)
        if done:
            messagebox.showinfo("重命名完成",
                                f"已重命名 {done} 个文件（可继续操作，关闭弹窗后重新扫描刷新）",
                                parent=dlg)

    def _do_restore(self):
        """执行勾选的恢复（新名 → 旧名）"""
        dlg = getattr(self, '_rename_dialog', None)
        rlb = getattr(self, '_restore_listbox', None)
        if rlb is None:
            return
        sel = rlb.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先勾选要恢复的文件", parent=dlg)
            return

        done = 0
        failed = []
        keep_log = []
        for idx, entry in enumerate(self.rename_log):
            if idx in sel:
                old_path = entry.get('old_path', '')
                new_path = entry.get('new_path', '')
                if not old_path or not new_path:
                    continue
                if os.path.exists(old_path):
                    failed.append((os.path.basename(new_path), "原文件名已存在，跳过"))
                    keep_log.append(entry)
                    continue
                try:
                    os.rename(new_path, old_path)
                    done += 1
                except Exception as e:
                    failed.append((os.path.basename(new_path), str(e)))
                    keep_log.append(entry)
            else:
                keep_log.append(entry)
        self.rename_log = keep_log
        self._save_rename_log()
        self._rename_dirty = getattr(self, '_rename_dirty', 0) + done

        if failed:
            detail = "\n".join(f"• {n}: {err}" for n, err in failed[:10])
            messagebox.showwarning("部分失败",
                                   f"成功恢复 {done} 个，失败 {len(failed)} 个：\n{detail}",
                                   parent=dlg)
        if done:
            messagebox.showinfo("恢复完成", f"已恢复 {done} 个文件的原名", parent=dlg)
            self._refresh_restore_list()

    def _refresh_restore_list(self):
        """刷新恢复页列表与计数"""
        rlb = getattr(self, '_restore_listbox', None)
        if rlb is None:
            return
        rlb.delete(0, tk.END)
        for e in self.rename_log:
            old = os.path.basename(e.get('old_path', ''))
            new = os.path.basename(e.get('new_path', ''))
            rlb.insert(tk.END, f"{new}  →  {old}")
        if self.rename_log:
            rlb.selection_set(0, tk.END)
        il = getattr(self, '_restore_info_label', None)
        if il is not None:
            il.config(text=f"已重命名: {len(self.rename_log)} 个文件（勾选后恢复原名）")

    def _export_ai_rules(self):
        """导出 AI 分析规则文本到文件"""
        import ai_analyzer
        prompt = ai_analyzer.get_system_prompt()
        rules_text = (
            "# ABD9 AI 分析规则\n"
            f"# 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "# 将此文件交给开发者，可在下次更新时预设进软件\n"
            "#" + "=" * 58 + "\n\n"
            f"{prompt}\n\n"
            "#" + "=" * 60 + "\n"
            "# ABD9音乐文件筛查器"
        )
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"ai_rules_{datetime.now().strftime('%Y%m%d')}.txt",
            filetypes=[("文本文件", "*.txt")],
            title="导出 AI 规则"
        )
        if not file_path:
            return
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(rules_text)
            messagebox.showinfo("导出成功", f"AI 分析规则已导出到:\n{file_path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _export_list_to_txt(self):
        """将 AI 分析结果导出为 CSV（无 AI 结果时提示先运行）"""
        vt = self.result_view_type

        # 分组视图必须要有 AI 分析结果
        if vt in ('sim', 'approx', 'agg') and not self.ai_judgments:
            ret = messagebox.askyesno("无 AI 分析结果",
                "当前视图尚未运行 AI 分析，导出的数据不会包含 AI 判断。\n\n是否先运行 AI 分析？")
            if ret:
                self._run_ai_analysis()
            return

        # 选择保存路径（自动生成文件名：视图类型-月-日-两位随机字母）
        now = datetime.now()
        letters = f"{random.choice('abcdefghijklmnopqrstuvwxyz')}{random.choice('abcdefghijklmnopqrstuvwxyz')}"
        export_view_names = {'dup': '重复文件', 'sim': '相似文件', 'approx': '近似文件',
                             'agg': '聚合去重', 'chg': '变更文件', 'all': '全部文件'}
        default_name = f"{export_view_names.get(vt, '文件列表')}-{now.month:02d}-{now.day:02d}-{letters}.csv"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv")],
            title=f"导出 {vt} 视图"
        )
        if not file_path:
            return

        # 获取视图名称和分组数据
        view_names = {'dup': '重复文件', 'sim': '相似文件', 'approx': '近似文件',
                      'agg': '聚合去重', 'chg': '变更文件', 'all': '全部文件'}
        view_name = view_names.get(vt, '文件列表')
        groups = []
        if vt == 'dup':
            groups = self.duplicate_groups
        elif vt == 'sim':
            groups = self.similar_groups
        elif vt == 'approx':
            groups = self.approximate_groups
        elif vt == 'agg':
            groups = self.duplicate_groups + self.similar_groups + self.approximate_groups

        # 智选规则文本
        rules_text = (
            "选中规则: 时长最大 → 文件最大 → 最新文件 → A侧优先, "
            "勾选的条件以工具栏设置为准"
        )

        def _csv_quote(val):
            """CSV 字段转义：含逗号/引号时加双引号"""
            s = str(val)
            if ',' in s or '"' in s or '\n' in s:
                s = '"' + s.replace('"', '""') + '"'
            return s


        lines = []
        # 文件头注释
        lines.append(f"# 文件列表导出 - {view_name}")
        lines.append(f"# 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"# AI 分析结果（聚类标识：同歌cluster内去重，跨cluster不同歌全部保留）")
        lines.append(f"# {rules_text}")
        lines.append("# ★保留 = 按规则应保留的文件, 去重 = 建议删除的冗余文件")
        lines.append("")

        if groups:
            # CSV 列头
            lines.append("组号,位置,文件名,时长,大小,修改时间,选中状态")

            for gi, group in enumerate(groups):
                gno = f"G{gi + 1}"

                # 查找 AI 聚类结果（聚合视图下 AI 只分析了相似+近似组，需偏移索引）
                ai_clusters = None
                if self.ai_judgments:
                    if vt == 'agg':
                        j_gi = gi - len(self.duplicate_groups)
                        if j_gi < 0:
                            ai_clusters = None
                        else:
                            for j in self.ai_judgments:
                                if j.get('group_index') == j_gi:
                                    ai_clusters = j.get('clusters')
                                    break
                    else:
                        for j in self.ai_judgments:
                            if j.get('group_index') == gi:
                                ai_clusters = j.get('clusters')
                                break

                def _pick_keep(group):
                    all_items = [(p, i) for p, i in group]
                    return self._pick_best_file(all_items)

                if ai_clusters and vt in ('sim', 'approx', 'agg'):
                    # 用 AI 聚类：每个 cluster 内同歌，跨 cluster 不同歌
                    import ai_analyzer as _ai_mod
                    cluster_keep_map = {}
                    for cluster in ai_clusters:
                        cluster_idx = [idx for idx in cluster if idx < len(group)]
                        if len(cluster_idx) < 2:
                            # 单文件 cluster：不同歌，全部保留
                            for idx in cluster_idx:
                                cluster_keep_map[group[idx][0]] = True
                            continue
                        # 版本子分组：原版/DJ版/伴奏版 各保留一个
                        sub = {}
                        for idx in cluster_idx:
                            path, info = group[idx]
                            k = _ai_mod.get_file_title_key(path, info, self.file_tags)
                            if k is None:
                                k = f"__nokey_{path}"
                            sub.setdefault(k, []).append(idx)
                        for idxs in sub.values():
                            if len(idxs) < 2:
                                for i in idxs:
                                    cluster_keep_map[group[i][0]] = True
                                continue
                            cluster_paths = [group[i][0] for i in idxs]
                            # 同歌同版本：选一个保留
                            keep = _pick_keep([(p, dict(info)) for p, info in group if p in cluster_paths])
                            for p in cluster_paths:
                                cluster_keep_map[p] = (p == keep)

                    for path, info in group:
                        name = info.get('name', '?')
                        dur = format_duration(info.get('duration'))
                        size = self._fmt_size(info['size'])
                        mtime = self._fmt_ctime(info['mtime'])
                        side = 'A' if path in self.all_files_a else 'B'
                        is_keep = cluster_keep_map.get(path, True)
                        status = '★保留' if is_keep else '去重'
                        row = f"{gno},{side},{_csv_quote(name)},{dur},{_csv_quote(size)},{_csv_quote(mtime)},{status}"
                        lines.append(row)
                else:
                    # 无 AI 结果：整组用智能规则
                    keep_path = _pick_keep(group)
                    for path, info in group:
                        name = info.get('name', '?')
                        dur = format_duration(info.get('duration'))
                        size = self._fmt_size(info['size'])
                        mtime = self._fmt_ctime(info['mtime'])
                        side = 'A' if path in self.all_files_a else 'B'
                        status = '★保留' if path == keep_path else '去重'
                        row = f"{gno},{side},{_csv_quote(name)},{dur},{_csv_quote(size)},{_csv_quote(mtime)},{status}"
                        lines.append(row)

                # 组之间空行
                lines.append("")
        else:
            # 非分组视图
            if vt == 'all':
                lines.append("文件名,时长,大小,修改时间")
                items = list(self.all_files_a.items()) + list(self.all_files_b.items())
                for path, info in items:
                    name = info.get('name', '?')
                    dur = format_duration(info.get('duration'))
                    size = self._fmt_size(info['size'])
                    mtime = self._fmt_ctime(info['mtime'])
                    lines.append(f"{_csv_quote(name)},{dur},{_csv_quote(size)},{_csv_quote(mtime)}")
            elif vt == 'chg':
                lines.append("状态,文件名,时长,大小,修改时间")
                for c in self.change_results:
                    info = self.all_files_a.get(c.path) or self.all_files_b.get(c.path) or {}
                    name = info.get('name', c.name)
                    dur = format_duration(info.get('duration'))
                    size = self._fmt_size(info.get('size', c.size))
                    mtime = self._fmt_ctime(info.get('mtime', c.modified_time))
                    lines.append(f"{c.change_status.value},{_csv_quote(name)},{dur},{_csv_quote(size)},{_csv_quote(mtime)}")
            else:
                lines.append("当前视图无分组数据")

        lines.append("")
        lines.append("# ABD9音乐文件筛查器")

        try:
            with open(file_path, 'w', encoding='utf-8-sig') as f:
                f.write('\n'.join(lines))
            messagebox.showinfo("导出成功", f"已导出到:\n{file_path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
