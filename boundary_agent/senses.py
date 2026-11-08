"""它自己的身体：GPU 温度、负载、跑了多久。

只读本机指标，不碰用户数据。每 60 秒缓存一次，避免每轮都去问 nvidia-smi。
"""

from __future__ import annotations

import os
import subprocess
import time


class Body:
    def __init__(self) -> None:
        self._start = time.time()
        self._cached_at = 0.0
        self._cached = ""
        self._metrics: dict = {}

    def metrics(self) -> dict:
        now = time.time()
        if now - self._cached_at < 60 and self._metrics:
            return self._metrics
        m: dict = {
            "gpu_temp": None, "gpu_util": None, "vram_mb": None,
            "load1": None, "uptime_h": round((now - self._start) / 3600.0, 2),
        }
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=temperature.gpu,utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits"],
                timeout=2, stderr=subprocess.DEVNULL,
            ).decode("utf-8", "ignore").strip().splitlines()[0]
            temp, util, mem = [x.strip() for x in out.split(",")]
            m["gpu_temp"] = float(temp)
            m["gpu_util"] = float(util)
            m["vram_mb"] = float(mem)
        except Exception:
            pass
        try:
            m["load1"] = float(os.getloadavg()[0])
        except Exception:
            pass
        self._metrics = m
        self._cached = self._format(m)
        self._cached_at = now
        return m

    @staticmethod
    def _format(m: dict) -> str:
        parts = []
        if m.get("gpu_temp") is not None:
            parts.append(f"GPU {m['gpu_temp']:.0f}°C，负载 {m.get('gpu_util', 0):.0f}%，"
                         f"显存 {m.get('vram_mb', 0):.0f}MB")
        if m.get("load1") is not None:
            parts.append(f"系统负载 {m['load1']:.1f}")
        parts.append(f"你已经连续运行 {m.get('uptime_h', 0):.1f} 小时")
        return "；".join(parts)

    def snapshot(self) -> str:
        self.metrics()
        return self._cached
