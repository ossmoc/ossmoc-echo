from ludiglot.infrastructure.proxy_setup import setup_system_proxy
setup_system_proxy()

try:
    from .__main__ import main
except ImportError:
    from __main__ import main

if __name__ == "__main__":
    main()
