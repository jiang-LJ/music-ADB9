#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jwx音乐文件筛查工具 - 任务管理器
处理任务持久化、增量扫描检测、扫描历史记录
"""

import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

from utils import (
    ChangeStatus,
    CompareMethod,
    ScanType,
    FileState,
    TaskRecord,
    BATCH_SIZE,
    TIME_TOLERANCE,
    get_app_dir,
)


class TaskManager:
    """
    任务管理器 - 处理任务持久化和增量扫描

    线程安全：按线程ID维护独立连接，避免跨线程使用SQLite连接报错
    """

    def __init__(self, db_path: Optional[str] = None):
        """初始化任务管理器"""
        if db_path is None:
            # 便携版：数据库存储在程序/exe所在目录
            db_path = get_app_dir() / "tasks.db"

        self.db_path = str(db_path)
        self._conn_map: Dict[int, sqlite3.Connection] = {}
        self._init_database()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（按线程隔离）"""
        tid = threading.current_thread().ident
        if tid not in self._conn_map or self._conn_map[tid] is None:
            conn = sqlite3.connect(self.db_path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA busy_timeout=5000')  # 5秒超时
            conn.execute('PRAGMA foreign_keys=ON')
            self._conn_map[tid] = conn
        return self._conn_map[tid]

    def close(self):
        """关闭所有数据库连接（资源释放）"""
        for conn in list(self._conn_map.values()):
            if conn:
                conn.close()
        self._conn_map.clear()

    def __del__(self):
        """析构时关闭连接"""
        self.close()

    def _init_database(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('PRAGMA user_version')
        version = cursor.fetchone()[0] or 0

        # 任务表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                folder_a TEXT NOT NULL,
                folder_b TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                total_files_a INTEGER DEFAULT 0,
                total_files_b INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        ''')

        # 文件状态表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                folder_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_time REAL NOT NULL,
                md5_hash TEXT,
                duration REAL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_scan TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                change_status TEXT DEFAULT 'new',
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                UNIQUE(task_id, file_path)
            )
        ''')

        # 扫描历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_history (
                scan_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                scan_type TEXT NOT NULL,
                scan_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                scan_end TIMESTAMP,
                duration_seconds REAL,
                files_scanned INTEGER DEFAULT 0,
                files_new INTEGER DEFAULT 0,
                files_modified INTEGER DEFAULT 0,
                files_deleted INTEGER DEFAULT 0,
                duplicate_groups INTEGER DEFAULT 0,
                similar_groups INTEGER DEFAULT 0,
                approximate_groups INTEGER DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            )
        ''')

        # 兼容旧数据库：添加 approximate_groups 字段（仅首次迁移）
        if version < 1:
            try:
                cursor.execute('ALTER TABLE scan_history ADD COLUMN approximate_groups INTEGER DEFAULT 0')
                cursor.execute('PRAGMA user_version = 1')
            except sqlite3.OperationalError:
                pass  # 字段已存在

        # 兼容旧数据库：添加 duration 字段（V15 时长缓存）
        if version < 2:
            try:
                cursor.execute('ALTER TABLE file_states ADD COLUMN duration REAL')
                cursor.execute('PRAGMA user_version = 2')
            except sqlite3.OperationalError:
                pass  # 字段已存在

        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_task ON file_states(task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_path ON file_states(file_path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_status ON file_states(change_status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_task ON scan_history(task_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_md5 ON file_states(md5_hash)')

        conn.commit()

    # ==================== 任务CRUD操作 ====================

    def create_task(self, task_name: str, folder_a: str, folder_b: str) -> TaskRecord:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]  # 短ID便于显示
        now = datetime.now().isoformat()

        task = TaskRecord(
            task_id=task_id,
            task_name=task_name,
            folder_a=folder_a,
            folder_b=folder_b,
            created_at=now,
            updated_at=now
        )

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tasks (task_id, task_name, folder_a, folder_b, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (task.task_id, task.task_name, task.folder_a,
               task.folder_b, task.created_at, task.updated_at))
        conn.commit()

        return task

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """获取任务信息"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT task_id, task_name, folder_a, folder_b, created_at,
                   updated_at, scan_count, total_files_a, total_files_b, status
            FROM tasks WHERE task_id = ?
        ''', (task_id,))

        row = cursor.fetchone()
        if row:
            return TaskRecord(*row)
        return None

    def list_tasks(self, limit: int = 50) -> List[TaskRecord]:
        """列出所有任务"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT task_id, task_name, folder_a, folder_b, created_at,
                   updated_at, scan_count, total_files_a, total_files_b, status
            FROM tasks ORDER BY updated_at DESC LIMIT ?
        ''', (limit,))

        return [TaskRecord(*row) for row in cursor.fetchall()]

    def update_task(self, task_id: str, **kwargs):
        """
        更新任务信息（单条 UPDATE 合并多个字段）
        """
        allowed_fields = {'task_name', 'folder_a', 'folder_b', 'total_files_a', 'total_files_b', 'status', 'scan_count'}
        present = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not present:
            return

        now = datetime.now().isoformat()
        present['updated_at'] = now

        set_clause = ', '.join(f'{k} = ?' for k in present)
        values = list(present.values()) + [task_id]

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(f'UPDATE tasks SET {set_clause} WHERE task_id = ?', values)
        conn.commit()

    def delete_task(self, task_id: str):
        """删除任务及关联数据（PRAGMA foreign_keys=ON使级联生效）"""
        conn = self._get_conn()
        cursor = conn.cursor()
        # 外键设置了ON DELETE CASCADE，只需删除任务
        cursor.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
        conn.commit()

    # ==================== 增量扫描核心 ====================

    def get_previous_state(self, task_id: str, folder_type: str) -> Dict[str, FileState]:
        """获取上次的文件状态"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT file_path, folder_type, file_name, file_size,
                   modified_time, md5_hash, duration, first_seen, last_scan, change_status
            FROM file_states
            WHERE task_id = ? AND folder_type = ?
        ''', (task_id, folder_type))

        states = {}
        for row in cursor.fetchall():
            state = FileState(
                path=row[0],
                folder_type=row[1],
                name=row[2],
                size=row[3],
                modified_time=row[4],
                md5_hash=row[5],
                duration=row[6],
                first_seen=row[7],
                last_scan=row[8],
                change_status=ChangeStatus(row[9]) if row[9] else ChangeStatus.NEW
            )
            states[row[0]] = state

        return states

    def detect_changes(self, task_id: str, current_files: Dict[str, dict],
                       folder_type: str,
                       compare_method: object = CompareMethod.SIZE_TIME,
                       detect_moved: bool = True) -> Tuple[List[FileState], Dict[str, int]]:
        """
        检测文件变更

        Args:
            task_id: 任务ID
            current_files: 当前扫描到的文件字典 {path: {name, size, mtime, md5}}
            folder_type: 文件夹类型 'A' 或 'B'
            compare_method: CompareMethod 枚举或字符串 'size_time'/'hash'
            detect_moved: 是否检测移动的文件

        Returns:
            (变更文件列表, 统计信息)
        """
        if isinstance(compare_method, str):
            compare_method = CompareMethod(compare_method)
        previous_states = self.get_previous_state(task_id, folder_type)
        current_paths = set(current_files.keys())
        previous_paths = set(previous_states.keys())

        changes = []
        stats = {'new': 0, 'modified': 0, 'unchanged': 0, 'deleted': 0, 'moved': 0}

        now = datetime.now().isoformat()

        # 构建MD5倒排索引用于O(1)移动检测
        md5_index: Dict[str, List[str]] = {}
        claimed_paths: Set[str] = set()

        if detect_moved:
            for prev_path, prev_state in previous_states.items():
                if prev_state.md5_hash and prev_path not in current_paths:
                    md5_index.setdefault(prev_state.md5_hash, []).append(prev_path)

        # 1. 检查新增和修改
        for path, info in current_files.items():
            if path not in previous_states:
                # 新增文件 - 使用预构建索引检测移动
                moved = False
                file_md5 = info.get('md5')

                if detect_moved and file_md5 and file_md5 in md5_index:
                    for prev_path in md5_index[file_md5]:
                        if prev_path not in claimed_paths:
                            prev_state = previous_states[prev_path]
                            changes.append(FileState(
                                path=path, folder_type=folder_type,
                                name=info['name'], size=info['size'],
                                modified_time=info['mtime'],
                                md5_hash=prev_state.md5_hash,
                                duration=prev_state.duration,
                                change_status=ChangeStatus.MOVED,
                                first_seen=prev_state.first_seen,
                                last_scan=now
                            ))
                            stats['moved'] += 1
                            claimed_paths.add(prev_path)
                            moved = True
                            break

                if not moved:
                    changes.append(FileState(
                        path=path,
                        folder_type=folder_type,
                        name=info['name'],
                        size=info['size'],
                        modified_time=info['mtime'],
                        md5_hash=info.get('md5'),
                        change_status=ChangeStatus.NEW,
                        first_seen=now,
                        last_scan=now
                    ))
                    stats['new'] += 1

            else:
                # 已存在的文件，检查是否修改
                prev = previous_states[path]

                # 添加时间容差比较
                time_diff = abs(prev.modified_time - info['mtime'])
                is_modified = False

                if compare_method == CompareMethod.HASH and info.get('md5') and prev.md5_hash:
                    is_modified = prev.md5_hash != info['md5']
                else:
                    # 默认使用大小+时间（带容差）
                    is_modified = (prev.size != info['size'] or
                                   time_diff > TIME_TOLERANCE)

                if is_modified:
                    changes.append(FileState(
                        path=path,
                        folder_type=folder_type,
                        name=info['name'],
                        size=info['size'],
                        modified_time=info['mtime'],
                        md5_hash=info.get('md5'),
                        change_status=ChangeStatus.MODIFIED,
                        first_seen=prev.first_seen,
                        last_scan=now
                    ))
                    stats['modified'] += 1
                else:
                    # 未变更，更新扫描时间，透传缓存时长
                    changes.append(FileState(
                        path=path,
                        folder_type=folder_type,
                        name=info['name'],
                        size=info['size'],
                        modified_time=info['mtime'],
                        md5_hash=prev.md5_hash,
                        duration=prev.duration,
                        change_status=ChangeStatus.UNCHANGED,
                        first_seen=prev.first_seen,
                        last_scan=now
                    ))
                    stats['unchanged'] += 1

        # 2. 检查删除（排除已被移动认领的路径）
        for path in previous_paths - current_paths - claimed_paths:
            prev = previous_states[path]
            changes.append(FileState(
                path=path, folder_type=folder_type,
                name=prev.name, size=prev.size,
                modified_time=prev.modified_time,
                md5_hash=prev.md5_hash,
                duration=prev.duration,
                change_status=ChangeStatus.DELETED,
                first_seen=prev.first_seen, last_scan=now
            ))
            stats['deleted'] += 1

        return changes, stats

    # ==================== 批量事务优化 ====================

    def save_file_states(self, task_id: str, states: List[FileState]):
        """
        批量保存文件状态（使用BATCH_SIZE分批插入）
        """
        if not states:
            return

        conn = self._get_conn()
        cursor = conn.cursor()

        # 批量准备数据
        batch_data = [
            (
                task_id, state.path, state.folder_type, state.name,
                state.size, state.modified_time, state.md5_hash,
                state.duration, state.first_seen, state.last_scan, state.change_status.value
            )
            for state in states
        ]

        sql = '''
            INSERT OR REPLACE INTO file_states
            (task_id, file_path, folder_type, file_name, file_size,
             modified_time, md5_hash, duration, first_seen, last_scan, change_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''

        try:
            # 使用BATCH_SIZE分批插入
            for i in range(0, len(batch_data), BATCH_SIZE):
                batch = batch_data[i:i + BATCH_SIZE]
                cursor.executemany(sql, batch)

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(f"批量保存文件状态失败: {e}") from e

    def record_scan_history(self, task_id: str, scan_type: ScanType,
                           stats: Dict, duration: float) -> str:
        """记录扫描历史"""
        scan_id = str(uuid.uuid4())[:12]

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scan_history
            (scan_id, task_id, scan_type, scan_end, duration_seconds,
             files_scanned, files_new, files_modified, files_deleted,
             duplicate_groups, similar_groups, approximate_groups)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            scan_id, task_id, scan_type.value,
            datetime.now().isoformat(), duration,
            stats.get('total', 0), stats.get('new', 0),
            stats.get('modified', 0), stats.get('deleted', 0),
            stats.get('duplicates', 0), stats.get('similar', 0),
            stats.get('approximate', 0)
        ))
        conn.commit()

        return scan_id

    def get_scan_history(self, task_id: str, limit: int = 10) -> List[Dict]:
        """获取扫描历史"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT scan_id, scan_type, scan_start, scan_end, duration_seconds,
                   files_scanned, files_new, files_modified, files_deleted,
                   duplicate_groups, similar_groups, approximate_groups
            FROM scan_history
            WHERE task_id = ?
            ORDER BY scan_start DESC LIMIT ?
        ''', (task_id, limit))

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def clear_history(self, task_id: str):
        """清空指定任务的所有扫描历史和文件状态记录"""
        conn = self._get_conn()
        with conn:
            conn.execute("DELETE FROM scan_history WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM file_states WHERE task_id = ?", (task_id,))

    def export_all_data(self) -> dict:
        """导出所有任务数据（含文件状态和扫描历史）"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM tasks")
        tasks = [dict(zip([desc[0] for desc in cursor.description], row))
                 for row in cursor.fetchall()]

        cursor.execute("SELECT * FROM file_states")
        file_states = [dict(zip([desc[0] for desc in cursor.description], row))
                       for row in cursor.fetchall()]

        cursor.execute("SELECT * FROM scan_history")
        scan_history = [dict(zip([desc[0] for desc in cursor.description], row))
                        for row in cursor.fetchall()]

        return {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "tasks": tasks,
            "file_states": file_states,
            "scan_history": scan_history,
        }

    def import_all_data(self, data: dict) -> Tuple[int, int]:
        """
        导入任务数据。
        为每个任务重新生成 task_id，避免与现有数据冲突。
        返回 (导入任务数, 跳过任务数)
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        tasks = data.get("tasks", [])
        file_states = data.get("file_states", [])
        scan_history = data.get("scan_history", [])

        imported_count = 0
        skipped_count = 0
        id_mapping: Dict[str, str] = {}

        for task in tasks:
            old_id = task["task_id"]

            # 跳过已存在的同名任务（路径和名称都相同）
            cursor.execute(
                "SELECT 1 FROM tasks WHERE task_name = ? AND folder_a = ? AND folder_b = ?",
                (task.get("task_name"), task.get("folder_a"), task.get("folder_b"))
            )
            if cursor.fetchone():
                skipped_count += 1
                continue

            new_id = str(uuid.uuid4())[:8]
            id_mapping[old_id] = new_id
            task["task_id"] = new_id

            cursor.execute('''
                INSERT INTO tasks (task_id, task_name, folder_a, folder_b, created_at,
                                   updated_at, scan_count, total_files_a, total_files_b, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task.get("task_id"), task.get("task_name"), task.get("folder_a"),
                task.get("folder_b"), task.get("created_at"), task.get("updated_at"),
                task.get("scan_count", 0), task.get("total_files_a", 0),
                task.get("total_files_b", 0), task.get("status", "active")
            ))
            imported_count += 1

        # 只导入已成功插入 tasks 表的任务关联数据
        valid_new_ids = set(id_mapping.values())

        fs_batch_data = []
        for fs in file_states:
            old_task_id = fs.get("task_id")
            new_task_id = id_mapping.get(old_task_id)
            if not new_task_id or new_task_id not in valid_new_ids:
                continue
            fs_batch_data.append((
                new_task_id, fs.get("file_path"), fs.get("folder_type"),
                fs.get("file_name"), fs.get("file_size"), fs.get("modified_time"),
                fs.get("md5_hash"), fs.get("duration"), fs.get("first_seen"), fs.get("last_scan"),
                fs.get("change_status", "new")
            ))

        fs_sql = '''
            INSERT INTO file_states (task_id, file_path, folder_type, file_name,
                                     file_size, modified_time, md5_hash, duration,
                                     first_seen, last_scan, change_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        for i in range(0, len(fs_batch_data), BATCH_SIZE):
            cursor.executemany(fs_sql, fs_batch_data[i:i + BATCH_SIZE])

        sh_batch_data = []
        for sh in scan_history:
            old_task_id = sh.get("task_id")
            new_task_id = id_mapping.get(old_task_id)
            if not new_task_id or new_task_id not in valid_new_ids:
                continue
            sh_batch_data.append((
                str(uuid.uuid4()), new_task_id, sh.get("scan_type"),
                sh.get("scan_start"), sh.get("scan_end"), sh.get("duration_seconds"),
                sh.get("files_scanned", 0), sh.get("files_new", 0),
                sh.get("files_modified", 0), sh.get("files_deleted", 0),
                sh.get("duplicate_groups", 0), sh.get("similar_groups", 0),
                sh.get("approximate_groups", 0)
            ))

        sh_sql = '''
            INSERT INTO scan_history (scan_id, task_id, scan_type, scan_start,
                                      scan_end, duration_seconds, files_scanned,
                                      files_new, files_modified, files_deleted,
                                      duplicate_groups, similar_groups, approximate_groups)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        for i in range(0, len(sh_batch_data), BATCH_SIZE):
            cursor.executemany(sh_sql, sh_batch_data[i:i + BATCH_SIZE])

        conn.commit()
        return imported_count, skipped_count
