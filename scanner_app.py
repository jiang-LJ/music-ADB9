#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jwx音乐文件筛查工具 - Tkinter 主界面
"""

import os
import sys
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

from utils import ChangeStatus, ScanType, FileState, compute_md5, get_app_dir, get_audio_duration, format_duration
from task_manager import TaskManager
from scanner_core import find_duplicates, find_similar, find_approximate


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

【唯一文件】不属于重复、相似、近似任何一类的文件
  • 计算方式：总文件数 − 重复文件 − 相似文件 − 近似文件
  • 四者严格互斥，相加等于总文件数

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
  • 点击扫描结果概览区的彩色数字可切换视图：重复文件 / 相似文件 / 近似文件 / 唯一文件 / 全部
  • 概览区各数字含义：
    - 总文件数：文件夹 A 和文件夹 B 中的文件总数（点击切换为全部视图）
    - 重复文件：内容完全相同的文件总数
    - 相似文件：文件名相同但内容可能不同的文件总数
    - 近似文件：文件名相似且时长接近的文件总数
    - 唯一文件：不属于重复/相似/近似的独立文件数
    - 扫描耗时：本次扫描所花费的时间
  • 文件夹统计区（🟢 新增 / 🟡 修改）：增量扫描下，与上次扫描基准相比的新增和修改文件数量；全新扫描时此处为 0

【唯一文件】
  • 指既不属于重复文件、也不属于相似文件、更不属于近似文件的文件
  • 计算方式：总文件数 − 重复文件 − 相似文件 − 近似文件
    ※ 四者严格互斥，相加等于总文件数

【快速选择】
  • 选 A 重复：在重复文件结果中一键选中所有位于文件夹 A 的文件
  • 选 B 重复：在重复文件结果中一键选中所有位于文件夹 B 的文件
  • 相似去重：保留每组最大/最新的文件，其余全部勾选（A+B 都有时优先保留 A 侧）
  • 近似去重：保留每组最大/最新的文件，其余全部勾选（A+B 都有时优先保留 A 侧）
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

        # 扫描结果数据
        self.duplicate_groups = []
        self.similar_groups = []
        self.approximate_groups = []
        self.all_files_a = {}
        self.all_files_b = {}
        self.change_results = []
        self.checked_items = {}  # id(tree) -> set(iid)

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
        top_frame.columnconfigure(4, weight=0, minsize=300)   # 快速选择（加宽）
        top_frame.columnconfigure(5, weight=0)   # 最右：软件使用说明

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
        quick_frame = tk.LabelFrame(top_frame, text="⚡ 快速选择",
                                    bg=self.colors['card'],
                                    fg=self.colors['text'],
                                    font=('Segoe UI', 11, 'bold'))
        quick_frame.grid(row=0, column=4, rowspan=3, sticky='nsew', padx=5, pady=5)
        self._fill_quick_action_panel(quick_frame)

        # 最右栏：软件使用说明
        help_frame = tk.LabelFrame(top_frame, text="📖 软件使用说明",
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
            ('total_files', '总文件数', '#0ea5e9', 'all'),
            ('duplicate_groups', '重复文件', '#ef4444', 'dup'),
            ('similar_groups', '相似文件', '#f59e0b', 'sim'),
            ('approximate_groups', '近似文件', '#a855f7', 'approx'),
            ('unique_files', '唯一文件', '#22c55e', None),
            ('duration', '扫描耗时', '#94a3b8', None),
        ]

        # 内部容器用于垂直居中
        inner = tk.Frame(overview_frame, bg=overview_frame.cget('bg'))
        inner.pack(fill=tk.X, expand=True)

        tooltip_texts = {
            'total_files': '文件夹 A 和文件夹 B 中的文件总数（点击可切换为全部视图）',
            'duplicate_groups': '内容完全相同的文件总数（点击仅显示重复文件）',
            'similar_groups': '文件名相同但内容可能不同的文件总数（点击仅显示相似文件）',
            'approximate_groups': '文件名相似且时长接近的文件总数（点击仅显示近似文件）',
            'unique_files': '不属于重复/相似/近似的独立文件数（计算公式：总文件数 − 重复 − 相似 − 近似）',
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

            # 绑定点击
            if view_type:
                for lbl in (dot_lbl, text_lbl, num_lbl):
                    lbl.config(cursor='hand2')
                    lbl.bind('<Button-1>', lambda e, v=view_type: self.switch_result_view(v))

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

    def _fill_quick_action_panel(self, quick_frame):
        """填充快速选择面板（V14：上4按钮+下并排）"""
        container = tk.Frame(quick_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        # 上下两区均分垂直空间
        container.grid_rowconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        btn_cfg = dict(
            bg=self.colors['border'], fg=self.colors['text'],
            font=('Segoe UI', 9), cursor='hand2',
            relief='raised', bd=1
        )

        # ===== 上半区：4个按钮 2×2 =====
        top_frame = tk.Frame(container, bg=self.colors['card'])
        top_frame.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)
        for i in range(2):
            top_frame.grid_rowconfigure(i, weight=1)
            top_frame.grid_columnconfigure(i, weight=1)

        btn_qa = tk.Button(top_frame, text="选A重复",
                           command=lambda: self.quick_select('dup', 'A'), **btn_cfg)
        btn_qa.grid(row=0, column=0, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_qa, "在重复文件结果中一键选中所有文件夹A的文件")

        btn_qb = tk.Button(top_frame, text="选B重复",
                           command=lambda: self.quick_select('dup', 'B'), **btn_cfg)
        btn_qb.grid(row=0, column=1, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_qb, "在重复文件结果中一键选中所有文件夹B的文件")

        btn_sim = tk.Button(top_frame, text="相似去重",
                            command=self.select_smallest_in_similar, **btn_cfg)
        btn_sim.grid(row=1, column=0, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_sim, "保留每组最大/最新的文件，其余全部勾选")

        btn_approx = tk.Button(top_frame, text="近似去重",
                               command=self.select_smallest_in_approximate, **btn_cfg)
        btn_approx.grid(row=1, column=1, padx=10, pady=10, sticky='nsew')
        Tooltip(btn_approx, "保留每组最大/最新的文件，其余全部勾选")

        # ===== 下半区：取消选择 | 统计 =====
        bottom_frame = tk.Frame(container, bg=self.colors['card'])
        bottom_frame.grid(row=1, column=0, sticky='nsew', padx=8, pady=8)
        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure(1, weight=1)

        cancel_btn = tk.Button(bottom_frame, text="取消选择",
                               command=self.clear_selection, **btn_cfg)
        cancel_btn.grid(row=0, column=0, padx=10, pady=10, sticky='nsew')
        Tooltip(cancel_btn, "取消所有已选中的文件")

        stat_frame = tk.Frame(bottom_frame, bg=self.colors['card'])
        stat_frame.grid(row=0, column=1, padx=10, pady=10, sticky='nsew')
        self.selection_var = tk.StringVar(value="已选择: 0 个文件")
        self.selection_detail_var = tk.StringVar(value="(A: 0, B: 0)")
        tk.Label(stat_frame, textvariable=self.selection_var,
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 10)).pack(anchor=tk.CENTER)
        tk.Label(stat_frame, textvariable=self.selection_detail_var,
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 9)).pack(anchor=tk.CENTER, pady=(2, 0))

    def _fill_help_panel(self, help_frame):
        """填充软件使用说明面板（V12：独立成一栏）"""
        container = tk.Frame(help_frame, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        inner = tk.Frame(container, bg=self.colors['card'])
        inner.pack(expand=True)

        btn_help = tk.Button(inner, text="软件使用说明",
                             command=self.show_help,
                             bg=self.colors['border'], fg=self.colors['text'],
                             font=('Segoe UI', 9), cursor='hand2',
                             width=12)
        btn_help.pack(pady=(0, 25))
        Tooltip(btn_help, "查看软件使用说明")

        tk.Label(inner, text="联络作者微信",
                bg=self.colors['card'], fg=self.colors['text'],
                font=('Segoe UI', 9)).pack()
        wechat_lbl = tk.Label(inner, text="a_better_day_9",
                bg=self.colors['card'], fg='#86efac',
                font=('Segoe UI', 9, 'bold'), cursor='hand2')
        wechat_lbl.pack()
        wechat_lbl.bind('<Button-1>', lambda e: self.copy_wechat_id())
        Tooltip(wechat_lbl, "点击复制微信号到剪贴板")

        tk.Label(inner, text="上方点击复制",
                bg=self.colors['card'], fg='#94a3b8',
                font=('Segoe UI', 8)).pack()

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
            tree = ttk.Treeview(frame, columns=('no', 'name', 'duration', 'size', 'mtime'),
                                show='tree headings', selectmode='browse')
            tree.heading('#0', text='选择')
            tree.column('#0', width=12, anchor='center')
            tree.heading('no', text='序号')
            tree.column('no', width=30, anchor='center')
            tree.heading('name', text='文件名')
            tree.column('name', width=300, anchor='center')
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
        import json
        session = {
            'last_task_id': self.current_task.task_id if self.current_task else None,
            'folder_a': self.path_a_var.get(),
            'folder_b': self.path_b_var.get(),
            'scan_options': {k: v.get() for k, v in self.scan_options.items()},
        }
        session_path = get_app_dir() / "last_session.json"
        try:
            with open(session_path, 'w', encoding='utf-8') as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_session(self):
        """从 JSON 恢复上次会话状态"""
        import json
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
        self.path_a_var.set(session.get('folder_a', ''))
        self.path_b_var.set(session.get('folder_b', ''))

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

    def show_scan_result_dialog(self, message: str):
        """显示扫描结果弹窗（固定宽高比，避免正方形）"""
        dialog = self._create_dialog("扫描结果", 350, 240)
        dialog.resizable(False, False)

        # 主内容区（横向：左侧数据 + 右侧按钮）
        main_frame = tk.Frame(dialog, bg=self.colors['card'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        # 左侧：图标 + 数据
        left = tk.Frame(main_frame, bg=self.colors['card'])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        icon_lbl = tk.Label(left, text="ℹ", bg=self.colors['card'], fg='#0ea5e9',
                            font=('Segoe UI', 32))
        icon_lbl.pack(anchor='w')

        tk.Label(left, text=message, bg=self.colors['card'], fg=self.colors['text'],
                 font=('Segoe UI', 11), justify=tk.LEFT, anchor='nw').pack(
                     fill=tk.BOTH, expand=True, pady=(8, 0))

        # 右侧：确定按钮（偏下）
        right = tk.Frame(main_frame, bg=self.colors['card'])
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        tk.Button(right, text="确定", command=dialog.destroy,
                  bg=self.colors['border'], fg=self.colors['text'],
                  font=('Segoe UI', 10), cursor='hand2', width=10).pack(
                      side=tk.BOTTOM, pady=(0, 10))

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

        if self.scan_options['scan_folder_a'].get() and not self.path_a_var.get():
            return False, "已选择扫描文件夹A，但未指定路径"

        if self.scan_options['scan_folder_b'].get() and not self.path_b_var.get():
            return False, "已选择扫描文件夹B，但未指定路径"

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

            folder_a = self.path_a_var.get()
            folder_b = self.path_b_var.get()

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
            import json
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
            import json
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

        # 获取扫描配置
        scan_config = self.get_effective_scan_config()
        scan_type = ScanType(scan_config['scan_mode'])

        # 估算扫描时间
        estimated_files, estimated_seconds = self.estimate_scan_time(scan_config)

        if estimated_files == 0:
            messagebox.showinfo("提示", "未在选中的文件夹中找到音频文件")
            return

        self.show_estimate_dialog(
            estimated_files, estimated_seconds,
            lambda: self.show_scan_progress_dialog(scan_type, scan_config)
        )

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
                total = self.overview_vars['total_files'].get()
                dup = self.overview_vars['duplicate_groups'].get()
                sim = self.overview_vars['similar_groups'].get()
                approx = self.overview_vars['approximate_groups'].get()
                unique = self.overview_vars['unique_files'].get()
                dur = self.overview_vars['duration'].get()
                result_msg = (
                    f"总文件数: {total}\n"
                    f"重复文件: {dup}\n"
                    f"相似文件: {sim}\n"
                    f"近似文件: {approx}\n"
                    f"唯一文件: {unique}\n"
                    f"扫描耗时: {dur}"
                )
                self.show_scan_result_dialog(result_msg)
            return

        self._scan_progress_event.wait(0.1)
        self.after(100, self._poll_scan_progress)

    def scan_directory(self, path: str, folder_type: str) -> Dict[str, dict]:
        """扫描目录获取文件列表（不立即读取音频时长，延迟到检测变更后按需读取）"""
        files = {}
        if not path or not os.path.exists(path):
            return files

        for root, _, filenames in os.walk(path):
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
                    except (OSError, IOError):
                        continue
        return files

    def _apply_cached_durations(self, files: Dict[str, dict], changes: List[FileState]):
        """将历史缓存的音频时长透传到当前文件字典（未变更/移动文件）"""
        for c in changes:
            if c.path in files and c.duration is not None:
                files[c.path]['duration'] = c.duration

    def _read_durations(self, files: Dict[str, dict]):
        """按需读取音频时长，仅当启用时长过滤时执行"""
        if not self.scan_options['use_duration'].get():
            return
        for path, info in files.items():
            if info.get('duration') is None:
                try:
                    info['duration'] = get_audio_duration(path)
                except Exception:
                    info['duration'] = None

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

        with ThreadPoolExecutor(max_workers=8) as ex:
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

        self._read_durations({**files_to_read_duration_a, **files_to_read_duration_b})

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
        total = len(self.all_files_a) + len(self.all_files_b)
        dup_files = sum(len(g) for g in self.duplicate_groups)
        sim_files = sum(len(g) for g in self.similar_groups)
        approx_files = sum(len(g) for g in self.approximate_groups)
        unique = total - dup_files - sim_files - approx_files

        self.overview_vars['total_files'].set(str(total))
        self.overview_vars['duplicate_groups'].set(str(dup_files))
        self.overview_vars['similar_groups'].set(str(sim_files))
        self.overview_vars['approximate_groups'].set(str(approx_files))
        self.overview_vars['unique_files'].set(str(unique))
        self.overview_vars['duration'].set(f"{duration:.1f}s")

        # 文件夹统计
        a_new = stats_a.get('new', 0) if stats_a else 0
        a_mod = stats_a.get('modified', 0) if stats_a else 0
        b_new = stats_b.get('new', 0) if stats_b else 0
        b_mod = stats_b.get('modified', 0) if stats_b else 0
        self.a_stats_var.set(f"📀 {len(self.all_files_a)} 个文件")
        self.b_stats_var.set(f"📀 {len(self.all_files_b)} 个文件")
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
                tree.insert('', tk.END, text='', values=(_fmt_no(counter_list), '', '', '', ''), tags=tags)
            else:
                sel = '☐' if checkable else ''
                tree.insert('', tk.END, iid=path, text=sel,
                            values=(_fmt_no(counter_list), info_dict['name'], format_duration(info_dict.get('duration')), self._fmt_size(info_dict['size']), self._fmt_ctime(info_dict['mtime'])),
                            tags=tags)

        if vt == 'dup':
            for group in self.duplicate_groups:
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                max_len = max(len(a_items), len(b_items))
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('dup',))
                    else:
                        _insert(left, left_counter, None, None, tags=('dup',))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('dup',))
                    else:
                        _insert(right, right_counter, None, None, tags=('dup',))
        elif vt == 'sim':
            for group in self.similar_groups:
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                max_len = max(len(a_items), len(b_items))
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('sim',))
                    else:
                        _insert(left, left_counter, None, None, tags=('sim',))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('sim',))
                    else:
                        _insert(right, right_counter, None, None, tags=('sim',))
        elif vt == 'approx':
            for group in self.approximate_groups:
                a_items = [(p, i) for p, i in group if p in self.all_files_a]
                b_items = [(p, i) for p, i in group if p in self.all_files_b]
                max_len = max(len(a_items), len(b_items))
                for i in range(max_len):
                    if i < len(a_items):
                        _insert(left, left_counter, a_items[i][0], a_items[i][1], tags=('approx',))
                    else:
                        _insert(left, left_counter, None, None, tags=('approx',))
                    if i < len(b_items):
                        _insert(right, right_counter, b_items[i][0], b_items[i][1], tags=('approx',))
                    else:
                        _insert(right, right_counter, None, None, tags=('approx',))
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
        """点击 Treeview 切换复选框状态（#0 text 为选择列）"""
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        if region not in ('cell', 'tree'):
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        col = tree.identify_column(event.x)
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
        """快速选择功能"""
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

    def _select_smallest_in_groups(self, groups: list, view_type: str):
        """每组保留最大/最新的文件不选，其余全部勾选"""
        self.switch_result_view(view_type)
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
        for group in groups:
            a_items = [(p, i) for p, i in group if p in self.all_files_a]
            b_items = [(p, i) for p, i in group if p in self.all_files_b]

            if a_items and b_items:
                # A+B都有
                all_items = a_items + b_items
                sizes = {info['size'] for _, info in all_items}
                if len(sizes) > 1:
                    # 大小不同：保留全局最大的（A侧优先）
                    max_size = max(info['size'] for _, info in all_items)
                    max_items = [(p, i) for p, i in all_items if i['size'] == max_size]
                    a_max = [(p, i) for p, i in max_items if p in self.all_files_a]
                    if a_max:
                        keep = max(a_max, key=lambda x: x[1]['mtime'])[0]
                    else:
                        keep = max(max_items, key=lambda x: x[1]['mtime'])[0]
                else:
                    # 大小相同：保留A侧最新的
                    keep = max(a_items, key=lambda x: x[1]['mtime'])[0]
                for path, _ in all_items:
                    if path != keep:
                        checked_paths.add(path)
            elif a_items:
                # 只有A侧
                sizes = {info['size'] for _, info in a_items}
                if len(sizes) > 1:
                    keep = max(a_items, key=lambda x: x[1]['size'])[0]
                else:
                    keep = max(a_items, key=lambda x: x[1]['mtime'])[0]
                for path, _ in a_items:
                    if path != keep:
                        checked_paths.add(path)
            elif b_items:
                # 只有B侧
                sizes = {info['size'] for _, info in b_items}
                if len(sizes) > 1:
                    keep = max(b_items, key=lambda x: x[1]['size'])[0]
                else:
                    keep = max(b_items, key=lambda x: x[1]['mtime'])[0]
                for path, _ in b_items:
                    if path != keep:
                        checked_paths.add(path)

        # 在 Treeview 中勾选
        for tree in trees:
            for item in tree.get_children():
                # item 本身就是 iid（即完整文件路径）
                if item in checked_paths:
                    tree.item(item, text='☑')
                    self._set_checked_tag(tree, item, True)
                    self.checked_items[id(tree)].add(item)

        self.update_selection_stats()

    def select_smallest_in_similar(self):
        """在相似文件中，每组勾选文件大小最小的（A/B同size则选B侧）"""
        if not self.similar_groups:
            return
        self._select_smallest_in_groups(self.similar_groups, 'sim')

    def select_smallest_in_approximate(self):
        """在近似文件中，每组勾选文件大小最小的（A/B同size则选B侧）"""
        if not self.approximate_groups:
            return
        self._select_smallest_in_groups(self.approximate_groups, 'approx')

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
            return
        self._scan_progress_event.wait(0.1)
        self.after(100, self._poll_refresh_progress)
