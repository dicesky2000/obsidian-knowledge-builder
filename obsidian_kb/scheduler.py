# -*- coding: utf-8 -*-
"""定时自动同步：两种运行模式。

- watch（内置循环）：程序常驻后台，按 scheduler.interval_minutes 间隔轮询执行
  sync（导入 + 打标 + 双链 + MOC + 报告）；
- schedule（Windows 计划任务）：生成一个 run_sync.bat（chcp 65001 + 调用 sync），
  并通过 schtasks 注册每日定时任务；--uninstall 可卸载。
"""
import logging
import os
import platform
import subprocess
import sys
import time
from typing import Any, Dict, Optional

BAT_TEMPLATE = """@echo off
chcp 65001 >nul
rem ===== 由 Obsidian 知识库自动化工具生成，请勿手动编辑 =====
"{python}" "{run_py}" sync --config "{config}"
"""


def _bat_path(vault_root: str) -> str:
    return os.path.join(vault_root, "处理日志", "run_sync.bat")


def install_task(cfg: Dict[str, Any], vault_root: str,
                 logger: logging.Logger, config_path: Optional[str] = None,
                 task_time: Optional[str] = None) -> Dict[str, str]:
    """注册 Windows 计划任务（每日）。返回 {'bat': ..., 'cmd': ..., 'output': ...}。"""
    sched = cfg.get("scheduler", {})
    task_name = sched.get("task_name", "ObsidianKB-Sync")
    t_time = task_time or sched.get("task_time", "09:00")

    run_py = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py"))
    config = config_path or _find_config()

    bat = _bat_path(vault_root)
    os.makedirs(os.path.dirname(bat), exist_ok=True)
    with open(bat, "w", encoding="utf-8") as f:
        f.write(BAT_TEMPLATE.format(
            python=sys.executable.replace('"', '""'),
            run_py=run_py.replace('"', '""'),
            config=(config or "").replace('"', '""')))

    if platform.system() != "Windows":
        msg = ("当前系统不是 Windows，无法注册计划任务。"
               "可手动添加 crontab：\n"
               "%s * * * * %s %s sync --config %s"
               % (t_time.split(":")[1], sys.executable, run_py, config or ""))
        logger.warning(msg)
        return {"bat": bat, "cmd": "", "output": msg}

    cmd = ('schtasks /Create /F /TN "%s" /SC DAILY /ST %s /TR "%s"'
           % (task_name, t_time, bat))
    logger.info("[定时] 注册计划任务：%s", cmd)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding="gbk", errors="replace", timeout=30)
        out = (proc.stdout or "") + (proc.stderr or "")
        logger.info("[定时] %s", out.strip() or "（无输出）")
        return {"bat": bat, "cmd": cmd, "output": out.strip()}
    except Exception as e:
        logger.error("[定时] 注册失败：%s", e)
        return {"bat": bat, "cmd": cmd, "output": "注册失败: %s" % e}


def uninstall_task(cfg: Dict[str, Any], logger: logging.Logger) -> str:
    """卸载 Windows 计划任务。"""
    sched = cfg.get("scheduler", {})
    task_name = sched.get("task_name", "ObsidianKB-Sync")
    if platform.system() != "Windows":
        return "当前系统不是 Windows，无需卸载计划任务"
    cmd = 'schtasks /Delete /F /TN "%s"' % task_name
    logger.info("[定时] 卸载计划任务：%s", cmd)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding="gbk", errors="replace", timeout=30)
        out = (proc.stdout or "") + (proc.stderr or "")
        logger.info("[定时] %s", out.strip() or "（无输出）")
        return out.strip() or "已卸载"
    except Exception as e:
        return "卸载失败: %s" % e


def watch_loop(cfg: Dict[str, Any], vault_root: str, logger: logging.Logger,
               sync_fn: Any, interval_minutes: Optional[int] = None) -> None:
    """内置循环模式：每 interval 分钟执行一次 sync_fn。"""
    sched = cfg.get("scheduler", {})
    minutes = interval_minutes or int(sched.get("interval_minutes", 30))
    logger.info("[定时] 进入 watch 模式，每 %d 分钟同步一次（Ctrl+C 退出）", minutes)
    try:
        while True:
            logger.info("[定时] ===== 开始一轮自动同步 =====")
            sync_fn()
            logger.info("[定时] ===== 本轮完成，%d 分钟后下一轮 =====", minutes)
            time.sleep(minutes * 60)
    except KeyboardInterrupt:
        logger.info("[定时] 已手动退出 watch 模式")


def _find_config() -> Optional[str]:
    from .config import find_default_config
    return find_default_config(os.getcwd())
