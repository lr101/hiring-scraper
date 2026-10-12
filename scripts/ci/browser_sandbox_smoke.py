"""Verify the production Chromium build runs with its sandbox at the app UID."""
from __future__ import annotations

import os
import ctypes
import errno
from pathlib import Path

os.environ.setdefault("DEBUG", "pw:browser")
if not os.access(os.environ.get("HOME", "/"), os.W_OK):
    os.environ["HOME"] = "/tmp"
os.environ.setdefault("XDG_CACHE_HOME", str(Path(os.environ["HOME"]) / ".cache"))
Path(os.environ["XDG_CACHE_HOME"], "fontconfig").mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright


def user_namespace_error() -> int | None:
    """Return the host's errno when this container cannot create a user namespace."""
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.unshare(ctypes.c_int(0x10000000))  # CLONE_NEWUSER
        error = ctypes.get_errno() if result != 0 else 0
        os.write(write_fd, str(error).encode("ascii"))
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    try:
        result = os.read(read_fd, 16)
    finally:
        os.close(read_fd)
        os.waitpid(child_pid, 0)
    if not result:
        return errno.EIO
    error = int(result)
    return error or None


if os.geteuid() != 10001:
    raise RuntimeError(f"Expected the application runtime UID 10001, got {os.geteuid()}")

namespace_error = user_namespace_error()
if namespace_error is not None:
    print(
        "Skipped Chromium sandbox smoke: the current runtime blocks unprivileged "
        f"user namespaces ({os.strerror(namespace_error)})."
    )
    raise SystemExit(0)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                                          args=["--disable-dev-shm-usage", "--disable-gpu"],
                                          timeout=15_000)
    try:
        page = browser.new_page()
        page.set_content("<main id='sandbox-check'>sandboxed</main>")
        if page.locator("#sandbox-check").inner_text() != "sandboxed":
            raise RuntimeError("Chromium rendered an unexpected sandbox smoke page")
        print(f"Sandboxed Chromium launched as UID {os.geteuid()} ({browser.version})")
    finally:
        browser.close()
