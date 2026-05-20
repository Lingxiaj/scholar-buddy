"""Launcher — starts the server and opens the browser."""
import os
import sys
import webbrowser
import threading
import time

# Set default port
PORT = int(os.environ.get("PORT", "8000"))


def open_browser():
    """Open browser after a short delay so the server is ready."""
    time.sleep(2)
    webbrowser.open(f"http://localhost:{PORT}")


def main():
    # Open browser in a background thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        "server.main:app",
        host="127.0.0.1",
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
