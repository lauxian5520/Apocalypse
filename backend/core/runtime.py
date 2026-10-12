"""Runtime readiness and the cooperative maintenance lock (single host)."""
import fcntl
import os
import tempfile
from pathlib import Path
from sqlalchemy import text


def check_storage(settings, engine):
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
    for directory in settings.runtime_dirs:
        with tempfile.TemporaryFile(dir=directory) as probe:
            probe.write(b'ready')
            probe.flush()


def acquire_runtime_lock(var_dir):
    Path(var_dir).mkdir(parents=True, exist_ok=True)
    handle = open(Path(var_dir) / '.app.lock', 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('备份或恢复正在进行，不能启动服务')
    return handle
