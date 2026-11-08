# 岸 · VM 观察窗（8766）

## 地址

```text
http://127.0.0.1:8766/
```

和 8765 分开，不挤在一起；只绑本机。

## 界面里有什么

每一轮一张卡：

- **她想**：岸用第一人称写的中文意图
- **翻译**：4B 把命令和输出翻成普通中文
- **原始命令**：展开可见
- **原始输出**：展开可见

顶部：

- `VM running` / `VM stopped`
- `她在活动` / `活动停止`
- 轮数、最新时间

每 1.5 秒轮询一次；新轮次自动插到最上面。

## 数据

`~/vms/an/activity.jsonl`

每行：

```json
{"round":1,"ts":...,"thought":"...","cmd":"...","explain":"...","out":"...","status":"ok","duration":1.07}
```

## 服务

```bash
systemctl --user status boundary-vm-monitor.service
systemctl --user status boundary-vm-activity.service

systemctl --user start boundary-vm-activity
systemctl --user stop boundary-vm-activity
journalctl --user -u boundary-vm-activity -f
```

- `boundary-vm-monitor`：已 enable，登录自启
- `boundary-vm-activity`：当前手动启动，跑 30 轮后退出

## 注意

- 翻译是 4B 写的，不是人工校对；可能把 SSH 警告、ping 失败解释得不准
- `ping` 在 passt 下可能 100% 丢包，但 HTTP/HTTPS 是通的
- `apt update` 可能超过 25 秒，卡片会标 `timeout`
- 8766 没有鉴权，只绑 `127.0.0.1`，别往公网放

## apt 锁修复（2026-10-21）

之前 8766 里连续出现：

```text
E: Could not get lock /var/lib/dpkg/lock-frontend
E: Unable to lock directory /var/lib/apt/lists/
```

原因不是宿主锁，是 guest 里的 `apt update`：

- 活动外层 25 秒超时只杀了 SSH 客户端
- VM 里的 apt 继续跑
- 下一轮又起 apt，互相抢 `/var/lib/dpkg/lock-frontend`
- 后台 `unattended-upgrades` 也在掺和

已处理：

- 杀掉残留 apt/dpkg，删 stale locks
- `dpkg --configure -a`
- 禁用 `apt-daily.timer` / `apt-daily-upgrade.timer` / `unattended-upgrades`
- Debian 源去掉 `deb-src`
- 镜像换 `mirrors.ustc.edu.cn`
- 活动命令改成 VM 内 `timeout --kill-after=3 20`，超时后清理 apt

结果：

```text
apt-get update   4s，exit 0
apt-get upgrade  成功（ca-certificates）
```

## apt 镜像测速（VM 内）

在 VM 里直接下 `dists/bookworm/main/binary-amd64/Packages.xz`：

```text
mirrors.bfsu.edu.cn      11.1 MB/s（复测 7.2）
mirrors.aliyun.com        8.8–9.0 MB/s
mirror.sjtu.edu.cn        8.5–8.9 MB/s
repo.huaweicloud.com      5.1 MB/s
mirrors.tuna...           4.7 MB/s
mirrors.ustc...           3.4 MB/s
mirrors.cloud.tencent...  2.0 MB/s
mirrors.nju...            3.7 MB/s
```

选阿里云：速度稳定，安全源也通。

```text
/etc/apt/mirrors/debian.list
  https://mirrors.aliyun.com/debian
/etc/apt/mirrors/debian-security.list
  https://mirrors.aliyun.com/debian-security
```

结果：

```text
apt-get update  1.3s，exit 0
```

## 只准用国内站（2026-10-21）

活动提示已写死：

- 只使用国内站点
- 不访问 google / stackoverflow / cloudflare 等海外站
- 网络检查只运行绝对路径 `/home/an/check-sites.sh`
- 不运行 `./home/an/check-sites.sh`
- 不用 ping
- 不用 `grep 'HTTP/1.1'`，看 `%{http_code}` 或 exit code

## 自由发挥（2026-10-21）

之前提示词写成了“检查国内站点/完成任务”，4B 会把它当任务清单。已改成自由活动：

- 没有目标、没有验收、没有必须完成的事
- 可以探索、写东西、装包、改配置、整理房间、发呆
- 不要求反复 `echo`“任务完成”
- 只保留边界：国内站、不碰宿主
- `sites.txt` / `check-sites.sh` 只是工具，不是任务

当前活动自己跑去看本地镜像和清华源，不再按任务表走。

## 持续活动（2026-10-21）

`vm_activity.py` 支持 `--rounds 0`：无限轮次。

活动服务已改成：

```text
--rounds 0 --delay 2
```

不再每 30 轮自动结束；要停：

```bash
systemctl --user stop boundary-vm-activity
```

注意：持续活动会每轮调用 4B，GPU 会周期性尖峰。

## 架构事实（2026-10-21）

岸最近把 VM 当成 ARM64，连续 curl 阿里云/清华/USTC 的 `dists/.../arm64/` 包索引。
实际：

```text
uname -m = x86_64
dpkg --print-architecture = amd64
```

原因：活动提示词没有写架构；她在阿里云镜像页看到 `armbian-releases` 后被带偏，4B 没有持久系统记忆。

已加进提示词：

> 这台 VM 是 x86_64 / amd64，不是 ARM、ARM64 或 aarch64。  
> 不要下载、检查或安装 ARM64/aarch64 包；apt 用的是 amd64。

## 源循环（2026-10-21）

岸连续 20+ 轮检查“隐藏的国内源”：

```text
find /etc/apt/sources.list.d ...
find /etc -name sources.list ...
apt list --installed | grep tuna/aliyun/ustc ...
curl mirrors.tuna.../help/apt
```

原因：空结果也返回 0，她把 `ok` 当成“还有东西没找到”；4B 又没有持久记忆。

已加事实层：

> apt 源已经配置为阿里云，不需要检查、修改或寻找隐藏的 apt/镜像源；sources.list 不用管。

## 活动结束自动归档（2026-10-22）

`vm_activity.py` 启动时记录 `session_start`；在 `finally` 里把本次新增轮次压成一条 fact：

- subject: `虚拟机`
- topic: `记忆`
- predicate: `活动归档`
- source: `vm_session_auto`
- 写入 `~/boundary-live/facts.sqlite3`
- 正常跑完 / Ctrl-C / `systemctl --user stop` 都会触发
- 没有新增轮次则跳过

这样 8765 的 facts 检索能命中“昨晚虚拟机 / 心树 / 雾”这类问题。
