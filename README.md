# proxy-minging

从 `config.yaml` 中配置的 Telegram 公开频道收集代理订阅链接，检查可识别的订阅内容，并生成可直接使用的列表。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `config.yaml` | Telegram 频道列表，格式为 `tgchannel` 字符串列表 |
| `latest.yaml` | 按机场、Clash、V2Ray 订阅分类的有效链接 |
| `clash.yaml` | 自动合并转换后的 Clash/Mihomo 完整配置 |
| `url.txt` | 本轮发现的候选订阅链接 |
| `v2ray.txt` | 本轮发现的代理节点链接 |

## 本地运行

需要 Python 3.11。

```bash
python -m pip install -r requirements.txt
python main.py
```

抓取请求有连接和读取超时，订阅检查最多并发 64 个请求。带流量信息的订阅仅在未过期且剩余流量超过 1 GiB 时写入 `latest.yaml`。

GitHub Actions 每小时自动运行，也可以在 Actions 页面手动触发。成功运行后会提交有变化的输出文件。

Clash/Mihomo 可直接添加以下配置订阅，更新由 GitHub Actions 生成并提交：

```text
https://raw.githubusercontent.com/yitong2333/proxy-minging/main/clash.yaml
```

转换由 [SubConverter-Extended 公共 API](https://api.asailor.org) 完成。转换服务会接收本轮通过筛选的订阅 URL；请留意订阅 URL 中可能包含访问令牌。
