"""Verify enhanced status endpoint output matches the CLI format."""
import asyncio
import json

from general_ludd.daemon import _daemon_state, create_daemon_app
from httpx import ASGITransport, AsyncClient


async def main():
    _daemon_state["todos"] = []
    _daemon_state["tick_metrics"] = {}
    app = create_daemon_app(tick_interval=0.01)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
        data = resp.json()
        print(json.dumps(data, indent=2))
        print()
        print("--- Formatted CLI output ---")
        print(f'General Ludd Agent v{data.get("version", "unknown")}')
        print("\u2500" * 45)
        print(f'Config dir:  {data.get("config_dir", "not set")}')
        for cf in data.get("config_files", []):
            print(f"  \u251c\u2500 {cf}")
        print(f'Filestore:   {data.get("filestore_root", "")}')
        bins = data.get("filestore_binaries", [])
        if bins:
            print(f"  Binaries:  {', '.join(bins)}")
        print(f'DB engine:   {data.get("db_engine", "sqlite")}')
        print(f'DB URL:      {data.get("db_url", "")}')
        print(f'Uptime:      {data.get("uptime_ticks", 0)} ticks')
        print(f'Todos:       {data.get("todos_total", 0)} total')
        print("Queue depths:")
        for q, d in sorted(data.get("queue_depths", {}).items()):
            print(f"  {q:<20} {d}")
        metrics = data.get("tick_metrics", {})
        if metrics:
            print(f'Dispatch:    {metrics.get("todos_dispatched", 0)} dispatched')
            print(f'Leases:      {metrics.get("leases_reclaimed", 0)} reclaimed')


if __name__ == "__main__":
    asyncio.run(main())
