# -*- coding: utf-8 -*-
"""monitor 的 ifind-sector-hub 接入点：进程级单例 + token 文件持久化。

token 解析（spec 决策 #6）：config_local.py / 环境变量仅作首次 bootstrap；
运行期刷新与轮换落 data/ifind_sector_hub_token.json（FileTokenStore + flock）。
"""

import os
import threading

import config
from ifind_sector_hub import FileTokenStore, HubConfig, SectorHub

_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "ifind_sector_hub_token.json")

_hub = None
_hub_lock = threading.Lock()


def get_hub() -> SectorHub:
    """进程级单例（同进程共享 token 刷新与 FileTokenStore 锁）。"""
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                _hub = SectorHub(HubConfig(
                    db_path=config.DB_PATH,
                    access_token=config.ACCESS_TOKEN,
                    refresh_token=config.REFRESH_TOKEN,
                    token_store=FileTokenStore(_TOKEN_FILE),
                ))
    return _hub


def refresh_token_now() -> str:
    """手动刷新 access_token（调试用，等价旧 `from ifind_client import refresh_access_token`）。"""
    hub = get_hub()
    return hub.client.tokens.refresh_access_token(hub.client.headers["access_token"])
