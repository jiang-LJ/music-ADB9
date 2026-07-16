#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
集成测试 - perform_scan 完整流程
"""

import os
import time
import pytest

from utils import ScanType
from task_manager import TaskManager
from scanner_app import MusicScannerWithTasks


class DummyTaskManager(TaskManager):
    """隔离数据库的测试用 TaskManager"""
    pass


class TestIntegration:
    @pytest.fixture
    def app(self, tmp_path):
        """创建应用实例并隔离数据库"""
        app = MusicScannerWithTasks()
        app.withdraw()  # 隐藏窗口，避免测试时弹出
        db_path = tmp_path / "test_tasks.db"
        app.task_manager = TaskManager(db_path=str(db_path))
        return app, tmp_path

    def _make_music_files(self, tmp_path, folder_name, files):
        """辅助：创建音乐测试文件"""
        folder = tmp_path / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (folder / name).write_bytes(content)
        return str(folder)

    def test_full_scan_dual_folders(self, app):
        """I-001: 首次全新扫描双文件夹"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {
            "song1.mp3": b"dup_content",
            "song2.mp3": b"a_only",
        })
        folder_b = self._make_music_files(tmp_path, "B", {
            "song1.mp3": b"dup_content",
            "song3.mp3": b"b_only",
        })

        app_obj.path_a_var.set(folder_a)
        app_obj.path_b_var.set(folder_b)
        app_obj.current_task = app_obj.task_manager.create_task("I001", folder_a, folder_b)

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True,
            'scan_folder_b': True,
            'scan_mode': 'full',
            'compare_method': 'size_time',
            'compute_md5': False,
            'detect_moved': False,
        })

        assert len(app_obj.all_files_a) == 2
        assert len(app_obj.all_files_b) == 2
        assert len(app_obj.duplicate_groups) == 1  # song1 重复
        app_obj.current_task = app_obj.task_manager.get_task(app_obj.current_task.task_id)
        assert app_obj.current_task.scan_count == 1

    def test_incremental_no_change(self, app):
        """I-002: 增量扫描无变化"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I002", folder_a, "")

        # 首次全新扫描
        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        # 再次增量扫描
        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        # 统计未变更
        assert len(app_obj.all_files_a) == 1
        app_obj.current_task = app_obj.task_manager.get_task(app_obj.current_task.task_id)
        assert app_obj.current_task.scan_count == 2

    def test_incremental_new_file(self, app):
        """I-003: 增量扫描有新增"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I003", folder_a, "")

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        # 新增文件
        time.sleep(0.1)
        (tmp_path / "A" / "song2.mp3").write_bytes(b"y")

        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        chg = [c for c in app_obj.change_results if c.change_status.value == 'new']
        assert len(chg) == 1
        assert chg[0].name == "song2.mp3"

    def test_incremental_modified(self, app):
        """I-004: 增量扫描有修改"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I004", folder_a, "")

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        time.sleep(0.1)
        (tmp_path / "A" / "song1.mp3").write_bytes(b"xxxxx")

        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        chg = [c for c in app_obj.change_results if c.change_status.value == 'modified']
        assert len(chg) == 1

    def test_incremental_deleted(self, app):
        """I-005: 增量扫描有删除"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {
            "song1.mp3": b"x",
            "song2.mp3": b"y",
        })
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I005", folder_a, "")

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        os.remove(tmp_path / "A" / "song2.mp3")

        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        chg = [c for c in app_obj.change_results if c.change_status.value == 'deleted']
        assert len(chg) == 1
        assert chg[0].name == "song2.mp3"

    def test_scan_only_folder_a(self, app):
        """I-006: 仅扫描文件夹 A"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        folder_b = self._make_music_files(tmp_path, "B", {"song2.mp3": b"y"})
        app_obj.path_a_var.set(folder_a)
        app_obj.path_b_var.set(folder_b)
        app_obj.current_task = app_obj.task_manager.create_task("I006", folder_a, folder_b)

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        assert len(app_obj.all_files_a) == 1
        assert len(app_obj.all_files_b) == 0
        app_obj.current_task = app_obj.task_manager.get_task(app_obj.current_task.task_id)
        assert app_obj.current_task.total_files_a == 1
        # B 未扫描，保持 0
        assert app_obj.current_task.total_files_b == 0

    def test_scan_only_folder_b(self, app):
        """I-007: 仅扫描文件夹 B"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        folder_b = self._make_music_files(tmp_path, "B", {"song2.mp3": b"y"})
        app_obj.path_a_var.set(folder_a)
        app_obj.path_b_var.set(folder_b)
        app_obj.current_task = app_obj.task_manager.create_task("I007", folder_a, folder_b)

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': False, 'scan_folder_b': True,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        assert len(app_obj.all_files_a) == 0
        assert len(app_obj.all_files_b) == 1
        app_obj.current_task = app_obj.task_manager.get_task(app_obj.current_task.task_id)
        assert app_obj.current_task.total_files_a == 0
        assert app_obj.current_task.total_files_b == 1

    def test_first_scan_force_full(self, app):
        """I-008: 首次扫描强制全新"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I008", folder_a, "")
        # scan_count == 0

        config = app_obj.get_effective_scan_config()
        # 用户未勾选 full_scan（默认 False）
        assert config['scan_mode'] == 'full'

    def test_compute_md5_enabled(self, app):
        """I-009: MD5 计算集成"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I009", folder_a, "")

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'hash',
            'compute_md5': True, 'detect_moved': False,
        })

        assert len(app_obj.all_files_a) == 1
        assert app_obj.all_files_a[list(app_obj.all_files_a.keys())[0]]['md5'] is not None

    def test_fast_mode_disables_md5(self, app):
        """I-010: 快速模式自动关闭 MD5"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I010", folder_a, "")

        # 模拟用户同时勾选 fast_mode 和 compute_md5
        app_obj.scan_options['fast_mode'].set(True)
        app_obj.scan_options['compute_md5'].set(True)
        app_obj.on_scan_option_changed()

        config = app_obj.get_effective_scan_config()
        assert config['compute_md5'] is False

    def test_duration_cached_incremental(self, app, monkeypatch):
        """I-011: 增量扫描时未变更文件的时长从缓存读取，不重新调用 get_audio_duration"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I011", folder_a, "")
        app_obj.scan_options['use_duration'].set(True)

        call_count = {'n': 0}

        def fake_duration(path):
            call_count['n'] += 1
            return 123.45

        monkeypatch.setattr('scanner_app.get_audio_duration', fake_duration)

        # 首次全新扫描会读取时长
        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })
        assert call_count['n'] == 1
        assert app_obj.all_files_a[list(app_obj.all_files_a.keys())[0]]['duration'] == 123.45

        # 再次增量扫描，未变更文件不应重新读取时长
        call_count['n'] = 0
        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })
        assert call_count['n'] == 0
        assert app_obj.all_files_a[list(app_obj.all_files_a.keys())[0]]['duration'] == 123.45

    def test_duration_read_for_new_file(self, app, monkeypatch):
        """I-012: 增量扫描新增文件时会读取时长"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I012", folder_a, "")
        app_obj.scan_options['use_duration'].set(True)

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        call_count = {'n': 0}

        def fake_duration(path):
            call_count['n'] += 1
            return 200.0

        monkeypatch.setattr('scanner_app.get_audio_duration', fake_duration)

        # 新增文件
        time.sleep(0.1)
        (tmp_path / "A" / "song2.mp3").write_bytes(b"y")

        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': False, 'detect_moved': False,
        })

        # 只应对新增文件读取一次时长
        assert call_count['n'] == 1
        paths = list(app_obj.all_files_a.keys())
        assert any(app_obj.all_files_a[p]['duration'] == 200.0 for p in paths)

    def test_md5_cached_incremental(self, app, monkeypatch):
        """I-013: 增量扫描无变化时复用缓存 MD5，不重新计算"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I013", folder_a, "")
        app_obj.scan_options['compute_md5'].set(True)

        call_count = {'n': 0}

        def fake_md5(path):
            call_count['n'] += 1
            return "abc123"

        monkeypatch.setattr('scanner_app.compute_md5', fake_md5)

        # 首次全新扫描会计算 MD5
        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': True, 'detect_moved': False,
        })
        assert call_count['n'] == 1
        assert app_obj.all_files_a[list(app_obj.all_files_a.keys())[0]]['md5'] == "abc123"

        # 再次增量扫描，未变更文件不应重新计算 MD5
        call_count['n'] = 0
        app_obj.perform_scan(ScanType.INCREMENTAL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'incremental', 'compare_method': 'size_time',
            'compute_md5': True, 'detect_moved': False,
        })
        assert call_count['n'] == 0
        assert app_obj.all_files_a[list(app_obj.all_files_a.keys())[0]]['md5'] == "abc123"

    def test_md5_cached_full_after_modify(self, app, monkeypatch):
        """I-014: 全新扫描时只有修改过的文件重新计算 MD5"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {
            "song1.mp3": b"x",
            "song2.mp3": b"y",
        })
        app_obj.path_a_var.set(folder_a)
        app_obj.current_task = app_obj.task_manager.create_task("I014", folder_a, "")
        app_obj.scan_options['compute_md5'].set(True)

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': True, 'detect_moved': False,
        })

        call_count = {'n': 0}

        def fake_md5(path):
            call_count['n'] += 1
            return "newmd5"

        monkeypatch.setattr('scanner_app.compute_md5', fake_md5)

        # 修改其中一个文件
        time.sleep(0.1)
        (tmp_path / "A" / "song1.mp3").write_bytes(b"xx")

        app_obj.perform_scan(ScanType.FULL, {
            'scan_folder_a': True, 'scan_folder_b': False,
            'scan_mode': 'full', 'compare_method': 'size_time',
            'compute_md5': True, 'detect_moved': False,
        })

        # 只有修改过的文件需要重新计算 MD5
        assert call_count['n'] == 1

    def test_task_path_sync(self, app):
        """I-015: 用户在 UI 修改路径后，同步更新 current_task 并持久化"""
        app_obj, tmp_path = app
        folder_a = self._make_music_files(tmp_path, "A", {"song1.mp3": b"x"})
        folder_b = self._make_music_files(tmp_path, "B", {"song2.mp3": b"y"})
        app_obj.path_a_var.set(folder_a)
        app_obj.path_b_var.set(folder_b)
        app_obj.current_task = app_obj.task_manager.create_task("I015", folder_a, folder_b)

        # 修改 UI 中的 B 路径
        new_b = self._make_music_files(tmp_path, "B2", {"song3.mp3": b"z"})
        app_obj.path_b_var.set(new_b)

        # 调用同步方法
        app_obj._sync_task_paths()

        assert app_obj.current_task.folder_b == new_b
        # 验证数据库也更新了
        task = app_obj.task_manager.get_task(app_obj.current_task.task_id)
        assert task.folder_b == new_b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
