"""Inspect assistant configuration/reservations without returning keys or evidence."""
import json
import os
from pathlib import Path
import sqlite3
from dotenv import dotenv_values
from applypilot.ai_settings import environment, workspace_context
from applypilot.ai_routing import route_info

root = Path('/data/multi/workspaces/kobby18')
env = environment(root)
file = root/'.ai-settings.json'
stored = json.loads(file.read_text()) if file.exists() else {}
local = dotenv_values(root/'.env') if (root/'.env').exists() else {}
with sqlite3.connect((root/'applypilot.db').as_uri()+'?mode=ro', uri=True) as conn:
    rows = conn.execute('SELECT task,provider,model,input_tokens,output_tokens,created_at FROM cc_ai_usage ORDER BY id DESC LIMIT 5').fetchall()
with workspace_context(root):
    route = route_info('score')
print(json.dumps({'explain_route':route,'gemini_workspace_encrypted_override':bool(stored.get('credentials',{}).get('GEMINI_API_KEY')),
                  'gemini_workspace_env_override':bool(local.get('GEMINI_API_KEY')),'effective_gemini_matches_service':env.get('GEMINI_API_KEY')==os.environ.get('GEMINI_API_KEY'),
                  'last_assistant_requests':rows},indent=2))
