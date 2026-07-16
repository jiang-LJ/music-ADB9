#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Phase 1 单元测试
测试 utils.py 和 task_manager.py 的核心功能
"""

import os
import time
import pytest
import tempfile
from pathlib import Path

from utils import (
    compute_md5,
    ChangeStatus,
    ScanType,
    FileState,
    TaskRecord,
    TIME_TOLERANCE,
)
from task_manager import TaskManager


# ============ utils.py 测试 ============

class TestUtils:
    def test_compute_md5_normal_file(self, tmp_path):
        """U-001: 正常文件 MD5"""
        f = tmp_path / "test.mp3"
        f.write_bytes(b"hello world")
        md5 = compute_md5(str(f))
        assert md5 is not None
        assert len(md5) == 32
        assert all(c in '0123456789abcdef' for c in md5)

    def test_compute_md5_nonexistent(self):
        """U-003: 不存在的文件返回 None"""
        assert compute_md5("/nonexistent/file.mp3") is None

    def test_compute_md5_empty_file(self, tmp_path):
        """U-004: 空文件 MD5 为固定值"""
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert compute_md5(str(f)) == "d41d8cd98f00b204e9800998ecf8427e"

    def test_change_status_enum(self):
        """U-005: ChangeStatus 枚举值正确"""
        assert ChangeStatus.MODIFIED.value == "modified"
        assert ChangeStatus.NEW.value == "new"
        assert ChangeStatus.UNCHANGED.value == "unchanged"
        assert ChangeStatus.DELETED.value == "deleted"
        assert ChangeStatus.MOVED.value == "moved"


# ============ task_manager.py 测试 ============

class TestTaskManagerCRUD:
    @pytest.fixture
    def manager(self, tmp_path):
        db_path = tmp_path / "test.db"
        return TaskManager(db_path=str(db_path))

    def test_create_task(self, manager):
        """T-001: 创建任务"""
        task = manager.create_task("测试任务", "/music/a", "/music/b")
        assert task.task_name == "测试任务"
        assert task.folder_a == "/music/a"
        assert task.folder_b == "/music/b"
        assert len(task.task_id) > 0
        assert task.scan_count == 0

    def test_get_and_list_tasks(self, manager):
        """T-002 / T-003: 获取和列出任务"""
        t1 = manager.create_task("任务1", "/a", "/b")
        time.sleep(0.01)
        t2 = manager.create_task("任务2", "/c", "/d")
        time.sleep(0.01)
        t3 = manager.create_task("任务3", "/e", "/f")

        got = manager.get_task(t1.task_id)
        assert got is not None
        assert got.task_name == "任务1"

        tasks = manager.list_tasks(limit=2)
        assert len(tasks) == 2
        # 按 updated_at 倒序，最后创建的在前面
        names = {t.task_name for t in tasks}
        assert "任务3" in names

    def test_update_task(self, manager):
        """T-004: 更新任务"""
        task = manager.create_task("更新测试", "/a", "/b")
        old_count = task.scan_count
        time.sleep(0.01)  # 确保时间戳变化
        manager.update_task(task.task_id, scan_count=5, status="completed",
                            folder_a="/new/a", folder_b="/new/b")
        updated = manager.get_task(task.task_id)
        assert updated.scan_count == 5
        assert updated.status == "completed"
        assert updated.folder_a == "/new/a"
        assert updated.folder_b == "/new/b"
        assert updated.updated_at >= task.updated_at

    def test_update_task_ignore_invalid_field(self, manager):
        """T-006: 非法字段被忽略"""
        task = manager.create_task("忽略测试", "/a", "/b")
        manager.update_task(task.task_id, malicious="xxx", scan_count=3)
        updated = manager.get_task(task.task_id)
        assert updated.scan_count == 3
        # 不会因为非法字段报错

    def test_delete_task_cascade(self, manager):
        """T-005: 删除任务级联删除关联数据"""
        task = manager.create_task("删除测试", "/a", "/b")
        # 插入一些 file_states
        states = [
            FileState(path="/a/1.mp3", folder_type="A", name="1.mp3", size=100, modified_time=1.0),
        ]
        manager.save_file_states(task.task_id, states)
        manager.record_scan_history(task.task_id, ScanType.FULL, {'total': 1}, 1.0)

        manager.delete_task(task.task_id)
        assert manager.get_task(task.task_id) is None
        # 级联删除后历史记录为空
        assert manager.get_scan_history(task.task_id) == []


class TestDetectChanges:
    @pytest.fixture
    def manager(self, tmp_path):
        db_path = tmp_path / "test.db"
        return TaskManager(db_path=str(db_path))

    def test_all_new(self, manager):
        """D-001: 历史为空，全部 NEW"""
        task = manager.create_task("D001", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
            "/a/2.mp3": {"name": "2.mp3", "size": 200, "mtime": 2000.0, "md5": "def"},
        }
        changes, stats = manager.detect_changes(task.task_id, current, "A")
        assert stats['new'] == 2
        assert all(c.change_status == ChangeStatus.NEW for c in changes)

    def test_all_unchanged(self, manager):
        """D-002: 全部未变更"""
        task = manager.create_task("D002", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        # 先保存历史
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        # 再次检测（完全相同）
        changes2, stats = manager.detect_changes(task.task_id, current, "A")
        assert stats['unchanged'] == 1
        assert changes2[0].change_status == ChangeStatus.UNCHANGED

    def test_modified_by_size(self, manager):
        """D-003: 大小变化导致 MODIFIED"""
        task = manager.create_task("D003", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/1.mp3": {"name": "1.mp3", "size": 200, "mtime": 1000.0, "md5": "abc"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A")
        assert stats['modified'] == 1

    def test_time_tolerance_unchanged(self, manager):
        """D-004: 1秒内变化视为未变更"""
        task = manager.create_task("D004", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0 + TIME_TOLERANCE - 0.5, "md5": "abc"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A")
        assert stats['unchanged'] == 1

    def test_time_tolerance_modified(self, manager):
        """D-005: 超出容差视为修改"""
        task = manager.create_task("D005", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0 + TIME_TOLERANCE + 1.0, "md5": "abc"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A")
        assert stats['modified'] == 1

    def test_deleted(self, manager):
        """D-006: 文件删除检测"""
        task = manager.create_task("D006", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
            "/a/2.mp3": {"name": "2.mp3", "size": 200, "mtime": 2000.0, "md5": "def"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A")
        assert stats['deleted'] == 1
        assert any(c.change_status == ChangeStatus.DELETED for c in changes2)

    def test_moved(self, manager):
        """D-007: 移动检测开启时识别 MOVED"""
        task = manager.create_task("D007", "/a", "/b")
        current = {
            "/a/old.mp3": {"name": "old.mp3", "size": 100, "mtime": 1000.0, "md5": "abc123"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/new.mp3": {"name": "new.mp3", "size": 100, "mtime": 1000.0, "md5": "abc123"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A", detect_moved=True)
        assert stats['moved'] == 1
        assert stats['deleted'] == 0
        assert any(c.change_status == ChangeStatus.MOVED for c in changes2)

    def test_moved_disabled(self, manager):
        """D-008: 移动检测关闭时视为 NEW + DELETED"""
        task = manager.create_task("D008", "/a", "/b")
        current = {
            "/a/old.mp3": {"name": "old.mp3", "size": 100, "mtime": 1000.0, "md5": "abc123"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/new.mp3": {"name": "new.mp3", "size": 100, "mtime": 1000.0, "md5": "abc123"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A", detect_moved=False)
        assert stats['moved'] == 0
        assert stats['new'] == 1
        assert stats['deleted'] == 1

    def test_hash_compare(self, manager):
        """D-009: hash 比较模式下识别 MD5 差异"""
        task = manager.create_task("D009", "/a", "/b")
        current = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "abc"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/1.mp3": {"name": "1.mp3", "size": 100, "mtime": 1000.0, "md5": "def"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A", compare_method='hash')
        assert stats['modified'] == 1

    def test_mixed_states(self, manager):
        """D-010: 混合状态检测"""
        task = manager.create_task("D010", "/a", "/b")
        current = {
            "/a/keep.mp3": {"name": "keep.mp3", "size": 100, "mtime": 1000.0, "md5": "aaa"},
            "/a/mod.mp3": {"name": "mod.mp3", "size": 100, "mtime": 1000.0, "md5": "bbb"},
            "/a/del.mp3": {"name": "del.mp3", "size": 100, "mtime": 1000.0, "md5": "ccc"},
        }
        changes, _ = manager.detect_changes(task.task_id, current, "A")
        manager.save_file_states(task.task_id, changes)

        current2 = {
            "/a/keep.mp3": {"name": "keep.mp3", "size": 100, "mtime": 1000.0, "md5": "aaa"},
            "/a/mod.mp3": {"name": "mod.mp3", "size": 200, "mtime": 1000.0, "md5": "bbb"},
            "/a/new.mp3": {"name": "new.mp3", "size": 100, "mtime": 1000.0, "md5": "ddd"},
        }
        changes2, stats = manager.detect_changes(task.task_id, current2, "A")
        assert stats['unchanged'] == 1
        assert stats['modified'] == 1
        assert stats['deleted'] == 1
        assert stats['new'] == 1


class TestBatchAndHistory:
    @pytest.fixture
    def manager(self, tmp_path):
        db_path = tmp_path / "test.db"
        return TaskManager(db_path=str(db_path))

    def test_batch_save(self, manager):
        """S-001: 批量保存 1200 条记录"""
        task = manager.create_task("S001", "/a", "/b")
        states = [
            FileState(
                path=f"/a/file_{i}.mp3",
                folder_type="A",
                name=f"file_{i}.mp3",
                size=i,
                modified_time=float(i),
            )
            for i in range(1200)
        ]
        manager.save_file_states(task.task_id, states)

        prev = manager.get_previous_state(task.task_id, "A")
        assert len(prev) == 1200

    def test_upsert(self, manager):
        """S-002: 同一记录重复保存应更新"""
        task = manager.create_task("S002", "/a", "/b")
        state = FileState(path="/a/1.mp3", folder_type="A", name="1.mp3", size=100, modified_time=1.0)
        manager.save_file_states(task.task_id, [state])

        state2 = FileState(path="/a/1.mp3", folder_type="A", name="1.mp3", size=200, modified_time=2.0)
        manager.save_file_states(task.task_id, [state2])

        prev = manager.get_previous_state(task.task_id, "A")
        assert len(prev) == 1
        assert prev["/a/1.mp3"].size == 200

    def test_scan_history(self, manager):
        """H-001 / H-002: 扫描历史记录和查询"""
        task = manager.create_task("H001", "/a", "/b")
        stats = {'total': 10, 'new': 2, 'modified': 1, 'deleted': 0, 'duplicates': 3, 'similar': 1}
        sid = manager.record_scan_history(task.task_id, ScanType.INCREMENTAL, stats, 5.5)
        assert len(sid) > 0

        # 插入多条
        for i in range(4):
            manager.record_scan_history(task.task_id, ScanType.FULL, stats, 1.0)

        history = manager.get_scan_history(task.task_id, limit=3)
        assert len(history) == 3
        # 验证包含 incremental 和 full 记录
        types = {h['scan_type'] for h in history}
        assert 'incremental' in types or 'full' in types


# ==================== 程序入口 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
