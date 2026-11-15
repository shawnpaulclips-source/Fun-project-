"""Checkpointing. Saving a .blend costs ~9 ms and ~260 KB, so there is no
reason ever to lose work or to be cautious about trying something.

Rule: checkpoint before every destructive operation. Booleans and remeshes can
segfault bpy with no Python exception, and a segfault takes the process with it.
"""
import bpy, os, time, json, shutil

_LOG = []


def checkpoint(directory, label):
    """Save a named snapshot. Returns its id."""
    os.makedirs(directory, exist_ok=True)
    cid = f"{len(_LOG):03d}_{label}"
    path = os.path.join(directory, f"{cid}.blend")
    t = time.time()
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    entry = {"id": cid, "label": label, "path": path,
             "seconds": round(time.time() - t, 4),
             "bytes": os.path.getsize(path),
             "objects": len(bpy.context.scene.objects)}
    _LOG.append(entry)
    return entry


def rollback(cid_or_path):
    """Reopen a snapshot. Everything since is gone - that is the point."""
    path = cid_or_path
    if not os.path.exists(path):
        match = [e for e in _LOG if e["id"] == cid_or_path]
        if not match:
            raise KeyError(f"no checkpoint {cid_or_path}")
        path = match[0]["path"]
    bpy.ops.wm.open_mainfile(filepath=path)
    return path


def history():
    return list(_LOG)


def write_manifest(directory):
    p = os.path.join(directory, "checkpoints.json")
    with open(p, "w") as f:
        json.dump(_LOG, f, indent=2)
    return p
