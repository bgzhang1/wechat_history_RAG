"""计时运行一次 ingest。

用法:
  python bench/run_case.py --name s1_baseline --pkg bench/baseline_pkg --db bench/dbs/s1.db \
      [--env KEY=VALUE ...] [--mock-port 8765] -- <targets...> [ingest 参数...]

--pkg 指定作为 cwd 的目录（决定导入 baseline 还是优化后的包）。
--mock-port 设置后自动把 CHAT/EMBED/SUMMARY 指到本地 mock。
输出: bench/logs/<name>.log（带相对时间戳），stdout 最后打印 WALL_SECONDS。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--pkg", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--mock-port", type=int)
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    targets = [t for t in args.rest if t != "--"]
    db_path = os.path.abspath(args.db)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "CHAT_DB": db_path}
    if args.mock_port:
        base = f"http://127.0.0.1:{args.mock_port}/v1"
        env.update({
            "CHAT_BASE_URL": base, "CHAT_API_KEY": "mock", "CHAT_MODEL": "mock-chat",
            "SUMMARY_MODEL": "mock-chat",
            "EMBED_BASE_URL": base, "EMBED_API_KEY": "mock", "EMBED_MODEL": "mock-embed",
            # Windows 注册表系统代理会拦截 httpx 的回环请求，必须显式绕过
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        })
    for pair in args.env:
        key, _, value = pair.partition("=")
        env[key] = value

    cmd = [sys.executable, "-u", "-m", "wechat_rag_agent.ingest", *targets]
    log_path = os.path.join(ROOT, "bench", "logs", f"{args.name}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd, cwd=os.path.abspath(args.pkg), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"# cmd: {cmd}\n# pkg: {args.pkg}\n# db: {db_path}\n")
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            stamp = time.perf_counter() - t0
            log.write(f"[{stamp:9.2f}s] {line}\n")
            log.flush()
    code = proc.wait()
    wall = time.perf_counter() - t0
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"# exit={code} wall={wall:.2f}s\n")
    print(f"{args.name}: exit={code} WALL_SECONDS={wall:.2f}")
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
