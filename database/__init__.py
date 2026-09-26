# -*- coding: utf-8 -*-
"""
SQLite 数据库封装（按领域拆分为包）

对外接口不变：`from database import Database`。
- 连接管理 / 建表 DDL / Mixin 组装：database.core
- 各领域读写方法：database.<领域模块>（Mixin，方法与拆包前逐一致）

schema 权威来源：monitor 私有表看 core._init_db()（DDL 在 schema.py）；
板块三表（ths_concept_dict/stock_concept_map/concept_members）看组件
ifind_sector_hub/repositories/storage.py（同一库文件）。
"""

from .core import Database

__all__ = ["Database"]
