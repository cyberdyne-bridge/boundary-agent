# 岸的 Debian VM

## 已装好

- QEMU 10.2.1，**无 root 解包**在 `~/qemu-local/root`
- KVM 可用（`/dev/kvm` ACL 给了当前用户）
- Debian 12 cloud image
- 1 GB RAM / 2 vCPU / 8 GB qcow2 overlay
- 用户：`an`，VM 内 sudo
- SSH：`127.0.0.1:2222`

## 隔离

- `restrict=on`：**无外网、无宿主网络**
- 不挂载宿主目录
- 不直通 GPU / USB
- 底图只读，`an.qcow2` 是 overlay
- 宿主只开 SSH 到 VM，不开 VM 到宿主

实测：

```text
10.0.2.2:8765 -> refused
10.0.2.2:8099 -> refused
ping 1.1.1.1  -> network unreachable
```

## 控制

```bash
~/vms/an/an-vm.sh status
~/vms/an/an-vm.sh ssh
~/vms/an/an-vm.sh exec 'uname -a'
~/vms/an/an-vm.sh stop
~/vms/an/an-vm.sh reset
```

SSH 直连：

```bash
ssh -i ~/vms/an/id_ed25519 -p 2222 an@127.0.0.1
```

## 岸进去玩了 5 轮

`vm_play.py` 让 4B 自己出命令、SSH 执行、读输出：

```text
1. whoami                -> an
2. whoami                -> an
3. ls -la /home/an       -> 家目录
4. mkdir -p /home/an/work && echo "你好，我是岸，正在搭建自己的工作环境。" > /home/an/work/self_intro.txt && cat ...
   -> 你好，我是岸，正在搭建自己的工作环境。
5. ls -la /home/an/work  -> self_intro.txt
```

前两轮她重复了 `whoami`；4B 的老毛病。  
但她确实在 VM 里建了工作目录和文件。

## 当前

- VM 运行中
- QEMU 约 385 MB RSS
- guest 约 200 MB used
- 4B / 8765 没受影响

## 还没做

- 还没把 `vm_exec` 接进岸的正式 turn（现在是独立 `vm_play.py`）
- VM 默认无外网；要 apt 需要放开 NAT 或白名单
- 没有自动快照；`reset` 是重建 overlay

## 自由活动：15 轮

`~/vms/an/freeplay.py` 让岸自己出命令，VM 内 `an` 有免密 sudo，宿主不可达。

这 15 轮她先做侦察：

```text
uname -a
lsb_release -a
uname -m
cat /etc/os-release
lsb_release -a      # 重复
df -h
ls /etc/apt/sources.list
cat /etc/apt/sources.list
ls /etc/apt/sources.list.d/
cat /etc/apt/sources.list.d/debian.sources
ls /etc/apt/mirrors/debian.list
ls /etc/apt/mirrors/debian-security.list
cat /etc/apt/mirrors/debian.list
ls /etc/apt/mirrors/debian-security.list
cat /etc/apt/mirrors/debian-security.list
```

没有破坏，没有碰宿主；4B 的自由活动偏“先摸环境”，还不会自己发起复杂项目。

日志：`~/vms/an/freeplay.jsonl`

## 权限边界

- VM 内：`an` 可 `sudo`，能改整个 guest
- 宿主：只有 SSH hostfwd；没有共享目录、没有 GPU/USB、没有外网
- 理论风险：QEMU/KVM 逃逸；这不是绝对隔离，但 guest 内 root 不等于宿主 root

## 外网（passt）

VM 现在通过 rootless `passt` 上网：

- QEMU `-netdev passt`
- 包装器加：
  - `--no-map-gw`：不把网关映射到宿主
  - `--dns-forward 1.1.1.1 --dns-host 127.0.0.53`
  - `-t 127.0.0.1/2222:22`：只把 SSH 转发到宿主 loopback
- 实测：
  - `curl https://deb.debian.org` 200
  - DNS 正常
  - 宿主 8765 / 8099 / 25565 不可达

`apt-get update` 能访问源，但拉全量 Sources 很慢；岸自由活动时自己卡进了 apt 锁循环（`apt update` 超时 → `fuser -k` → 再 update）。这是 4B 的自主性问题，不是宿主被碰。

日志：`~/vms/an/freeplay-net.jsonl`

## 资源调整（2026-10-21）

宿主当时的可用内存只有约 1.8Gi，4B RSS 约 8.4GB，所以没有直接给 2GB。

当前 VM：

```text
RAM    1.5GB
vCPU   3
Disk   8GB（约 6.5GB 可用）
GPU    无直通
```

guest 内确认：

```text
nproc = 3
MemTotal ≈ 1.4Gi
```

宿主调整后：

```text
available ≈ 2.1Gi
QEMU RSS ≈ 365MB（启动后，随 guest 使用增长）
```

3 vCPU 是保守值：4B 仍用 8 线程，VM 跑重编译时可能和 4B 抢 CPU；GPU 不直通，避免把 4B 从显卡上挤掉。

## 50GB 磁盘 + 国内站点

- `an.qcow2` 已扩到 **50GB**，guest 根分区自动扩到 50G
- qcow2 稀疏，宿主不会立刻多占 50G
- `/home/an/sites.txt`：百度、B站、知乎、微博、豆瓣、少数派、36氪、CSDN、掘金、淘宝、腾讯、网易、新华网、中国政府网、中国天气
- `/home/an/check-sites.sh`：用 curl 检查，不用 ping
- 实测：13 个 200；腾讯 501、网易 403（站方反爬，不是断网）

活动提示已加：

> 判断网络用 curl/HTTPS，不要用 ping（ICMP 可能被丢弃）。
