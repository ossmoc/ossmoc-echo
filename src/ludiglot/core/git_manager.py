from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, Dict


def get_git_env() -> Dict[str, str]:
    """获取包含代理检测的 Git 执行环境"""
    env = os.environ.copy()
    
    # 1. 检查环境变量中是否已有代理
    system_env_proxy = env.get('HTTP_PROXY') or env.get('http_proxy') or \
                       env.get('HTTPS_PROXY') or env.get('https_proxy')
    if system_env_proxy:
        return env

    # 2. 检查 Git 全局配置
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", "http.proxy"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return env # Git 自己会读全局配置，不需要我们注入
    except Exception:
        pass

    # 3. 检查 Windows 系统代理
    windows_proxy = _get_windows_system_proxy()
    if windows_proxy:
        env['HTTP_PROXY'] = windows_proxy
        env['HTTPS_PROXY'] = windows_proxy
        
    return env


def _get_windows_system_proxy() -> str | None:
    """从 Windows 注册表读取系统代理设置"""
    try:
        import winreg
        proxy_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, proxy_path) as key:
            try:
                proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if not proxy_enable:
                    return None
                proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                if proxy_server:
                    if '=' in proxy_server:
                        for item in proxy_server.split(';'):
                            if item.startswith('http='):
                                return 'http://' + item.split('=', 1)[1]
                    return 'http://' + proxy_server if not proxy_server.startswith('http') else proxy_server
            except FileNotFoundError:
                pass
    except Exception:
        pass
    return None


class GitManager:
    """管理数据仓库的同步与更新"""
    
    @staticmethod
    def pull(repo_path: Path) -> subprocess.CompletedProcess:
        """安全地在指定路径执行 git pull"""
        env = get_git_env()
        return subprocess.run(
            ["git", "-C", str(repo_path), "pull"],
            capture_output=True,
            text=True,
            timeout=300,
            env=env
        )

    @staticmethod
    def fast_clone_wuthering_data(target_path: Path, progress_callback=None) -> bool:
        """执行针对 WutheringData 的精简克隆 (Sparse-checkout + Shallow)"""
        env = get_git_env()
        repo_url = "https://github.com/Dimbreath/WutheringData.git"
        
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            
            # 1. Init
            subprocess.run(["git", "init", str(target_path)], check=True, env=env, capture_output=True)
            
            # 2. Remote
            subprocess.run(["git", "-C", str(target_path), "remote", "add", "origin", repo_url], 
                          check=True, env=env, capture_output=True)
            
            # 3. Sparse-checkout
            subprocess.run(["git", "-C", str(target_path), "sparse-checkout", "init", "--cone"], 
                          check=True, env=env, capture_output=True)
            subprocess.run(["git", "-C", str(target_path), "sparse-checkout", "set", "TextMap", "ConfigDB"], 
                          check=True, env=env, capture_output=True)
            
            # 4. Fetch
            cmd = ["git", "-C", str(target_path), "fetch", "--depth", "1", "--progress", "origin"]
            process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, env=env, universal_newlines=True)
            
            if process.stderr:
                for line in process.stderr:
                    if progress_callback:
                        progress_callback(line.strip())
            
            process.wait()
            if process.returncode != 0:
                return False
                
            # 5. Checkout
            for branch in ["master", "main"]:
                res = subprocess.run(["git", "-C", str(target_path), "checkout", f"origin/{branch}"], 
                                    env=env, capture_output=True)
                if res.returncode == 0:
                    return True
            return False
            
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
