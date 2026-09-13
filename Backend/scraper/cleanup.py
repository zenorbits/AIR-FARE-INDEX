import logging

logger = logging.getLogger(__name__)


def _is_playwright_chromium(p):
    exe = (p.info.get("exe") or "").lower()
    cmd = " ".join(p.info.get("cmdline") or []).lower()
    return "ms-playwright" in exe or "browser_profile" in cmd


def _parent_alive(p, psutil):
    ppid = p.info.get("ppid")
    if not ppid or not psutil.pid_exists(ppid):
        return False
    try:
        parent = psutil.Process(ppid)
        # PID reuse guard: a "parent" newer than the child is not the real parent
        return parent.create_time() <= p.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def cleanup_orphaned_browsers():
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not installed; skipping orphan cleanup")
        return 0

    victims = []
    for p in psutil.process_iter(["pid", "ppid", "exe", "cmdline", "name"]):
        try:
            if _is_playwright_chromium(p) and not _parent_alive(p, psutil):
                victims.append(p)
                victims.extend(p.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    unique = list({v.pid: v for v in victims}.values())
    for v in unique:
        try:
            logger.info(f"Killing orphan chromium pid={v.pid} name={v.name()}")
            v.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    psutil.wait_procs(unique, timeout=5)
    if unique:
        logger.info(f"Orphan cleanup: killed {len(unique)} process(es)")
    return len(unique)
