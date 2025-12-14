"""
Simple HTTP server for serving dashboard static files.
"""

import asyncio
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

async def start_http_server(static_dir: Path, host: str = "0.0.0.0", port: int = 8080):
    """Start HTTP server to serve static dashboard files."""
    
    app = web.Application()
    
    # Serve static files
    app.router.add_static('/', static_dir, name='static', show_index=True)
    
    # Redirect root to dashboard.html
    async def redirect_to_dashboard(request):
        raise web.HTTPFound('/dashboard.html')
    
    app.router.add_get('/', redirect_to_dashboard)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"[DASHBOARD] HTTP server started at http://{host}:{port}")
    
    # Keep server running
    while True:
        await asyncio.sleep(3600)
