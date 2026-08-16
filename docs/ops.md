---
title: "运维部署"
nav_order: 5
has_children: true
description: 服务器部署、systemd 服务与日常运维
---

# 运维部署

本章节面向部署与运维人员：如何在服务器上安装、启动、升级本系统，以及常见故障的排查方法。

核心内容见**部署手册**：systemd 一键安装、公网 / SSH 隧道两种访问方式、日志查看、数据库备份与故障排查。

日常运维最常用的三个入口：

- 服务管理：`sudo systemctl status/restart ifind-monitor`
- 交易日定时任务：`crontab -l`（daily 同步与归因推送，见[快速开始](getting-started.md)）
- 运行数据：`data/` 目录（SQLite 数据库、交易日历缓存、推送日志）
