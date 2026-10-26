"""Production deployment smoke: hashes/counts and read-only previews; no inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True, type=Path)
parser.add_argument("--phase", choices=("before", "after"), required=True)
args = parser.parse_args()
root = args.workspace
with sqlite3.connect((root/"applypilot.db").resolve().as_uri()+"?mode=ro", uri=True) as conn:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("jobs", "cc_documents", "cc_messages") if table in tables}
paths = [root/"searches.yaml", root/"resume.txt", root/"profile.json", *sorted((root/"resume_variants").glob("*.txt"))]
protected = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
out = {"phase": args.phase, "counts": counts, "protected_inputs": protected}
if args.phase == "after":
    from applypilot.database import init_db
    from applypilot.ai_settings import workspace_context
    from applypilot.ai_routing import route_table
    from applypilot.pipeline_workspace import state
    from applypilot.role_routing import route_resume_for_job
    conn = init_db(root/"applypilot.db")
    with workspace_context(root):
        workspace = state(conn, root)
        out["ui_services"] = {"sources": len(workspace["sources"]), "variants": len(workspace["variants"]), "campaigns": len(workspace["campaigns"]), "queues": workspace["queue"]}
        out["resume_choices"] = {title: route_resume_for_job({"title": title}, app_dir=root).public()
                                 for title in ("Data Analyst", "Application Support Analyst", "IT Support Analyst")}
        out["writing_routes"] = [r for r in route_table() if r["task"] in {"tailor", "statement", "cover"}]
    out["http"] = {}
    for path in ("/health", "/assets/pipeline.js", "/assets/studios.js", "/assets/studios.css"):
        with urllib.request.urlopen("http://127.0.0.1:8765"+path, timeout=15) as response:
            out["http"][path] = response.status
    out["post_counts"] = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in counts}
print(json.dumps(out, indent=2))
