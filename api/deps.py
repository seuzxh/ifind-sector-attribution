# -*- coding: utf-8 -*-
"""接口层共享依赖：db 单例（与拆包前 api_server.db 同语义）"""

from database import Database

db = Database()
