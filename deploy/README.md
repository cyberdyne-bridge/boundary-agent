# deploy

systemd 单元与虚拟机侧脚本。所有路径用 systemd 的 `%h`（当前用户家目录）展开，
**不含任何写死的用户名**，换机器只需改 `WorkingDirectory` 里的仓库位置。

| 文件 | 作用 |
|---|---|
| `boundary-model.service` | 拉起本地模型（llama-server），默认 8099 |
| `boundary-web.service` | 岸 的网页窗口，默认 8765，只绑 127.0.0.1 |
| `boundary-vm-activity.service` | 虚拟机"每日一次自选活动"调度 |
| `boundary-vm-monitor.service` | 虚拟机观察窗（8766） |
| `vm/vm_activity.py` | 活动调度与记录 |
| `vm/vm_monitor.py` | 只读观察窗页面 |

## 安装

```bash
# 按需改 WorkingDirectory / --data-dir 指向你自己的仓库与数据目录
mkdir -p ~/.config/systemd/user
cp deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now boundary-model boundary-web
```

## 环境变量（覆盖默认路径）

```text
BOUNDARY_DATA        数据目录（默认 <repo>/data）
BOUNDARY_VM_ROOT     虚拟机工作目录（默认 <repo>/data/vm）
BOUNDARY_VM_SCRIPT   虚拟机启动脚本（默认 deploy/vm/an-vm.sh）
```

> `vm/vm_activity.py` 需要一个把 Debian 拉起来的启动脚本（`an-vm.sh`）。
> 该脚本属于实验现场资产，未随仓发布；请按 `docs/vm.md` 自行搭建。

## 注意

- `boundary-web` 只监听回环地址。**不要**把 8765 直接暴露到公网——它没有鉴权。
- 虚拟机活动的设计前提是"无 root QEMU + 隔离 SSH"，见 `docs/vm.md`。
