import os
import sys

def setup_system_proxy():
    """检测并应用 Windows 系统代理设置到环境变量。"""
    if sys.platform != "win32":
        return

    try:
        import winreg
        
        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                # 检查代理是否开启
                try:
                    enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                    if not enabled:
                        return
                except FileNotFoundError:
                    return

                # 获取代理服务器地址
                try:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                    if not server:
                        return
                except FileNotFoundError:
                    return

                # 解析代理设置
                # 格式可能是 "127.0.0.1:7897" 或 "http=127.0.0.1:7897;https=127.0.0.1:7897"
                proxies = {}
                if ";" in server:
                    for part in server.split(";"):
                        if "=" in part:
                            proto, addr = part.split("=", 1)
                            proxies[proto.lower()] = addr
                        else:
                            addr = part.strip()
                            if addr:
                                proxies["http"] = addr
                                proxies["https"] = addr
                else:
                    proxies["http"] = server
                    proxies["https"] = server

                # 应用到环境变量
                for proto, addr in proxies.items():
                    env_key = f"{proto.upper()}_PROXY"
                    if env_key not in os.environ:
                        # 确保有协议头
                        full_addr = addr
                        if "://" not in addr:
                            full_addr = f"http://{addr}"
                        os.environ[env_key] = full_addr
                        # print(f"[PROXY] {env_key} set to {full_addr}")

                # 处理忽略列表 (Bypass)
                try:
                    override, _ = winreg.QueryValueEx(key, "ProxyOverride")
                    if override and "NO_PROXY" not in os.environ:
                        # Windows 用分号，环境变量通常用逗号
                        no_proxy = override.replace(";", ",")
                        # 去掉可能存在的 <local>
                        no_proxy = no_proxy.replace("<local>", "localhost,127.0.0.1")
                        os.environ["NO_PROXY"] = no_proxy
                except FileNotFoundError:
                    # Proxy override list is not configured; no NO_PROXY value to apply.
                    pass
        except FileNotFoundError:
            # Proxy settings registry key does not exist; assume no system proxy is configured.
            # 找不到 Internet Settings 注册表项时，视为未配置代理，直接跳过
            pass
    except Exception as e:
        # 读取系统代理配置出错时静默失败，不影响程序主逻辑
        pass

if __name__ == "__main__":
    setup_system_proxy()
    print(f"HTTP_PROXY: {os.environ.get('HTTP_PROXY')}")
    print(f"HTTPS_PROXY: {os.environ.get('HTTPS_PROXY')}")
    print(f"NO_PROXY: {os.environ.get('NO_PROXY')}")
