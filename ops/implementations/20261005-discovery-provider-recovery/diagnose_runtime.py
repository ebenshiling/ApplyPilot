"""No-inference dependency and sanitized workspace configuration diagnostic."""
import importlib.metadata as metadata
import json
import os
import sys
from pathlib import Path

from applypilot.ai_settings import environment, workspace_context
from applypilot.ai_routing import route_table

root = Path('/data/multi/workspaces/kobby18')
env = environment(root)
out = {'dependencies': {}}
for name in ('python-jobspy', 'curl-cffi', 'numpy', 'pandas', 'beautifulsoup4', 'pydantic'):
    try:
        out['dependencies'][name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        out['dependencies'][name] = 'missing'
out['jobspy_requirements'] = metadata.requires('python-jobspy')
with workspace_context(root):
    out['routes'] = [r for r in route_table() if r['task'] in {'score', 'extract', 'summary', 'tailor', 'cover', 'statement'}]
out['gemini_key_present'] = bool(env.get('GEMINI_API_KEY'))
out['gemini_key_looks_like_google_key'] = str(env.get('GEMINI_API_KEY') or '').startswith('AIza')
out['gemini_key_matches_service_key'] = env.get('GEMINI_API_KEY') == os.environ.get('GEMINI_API_KEY')
out['proxy_variables_present'] = [k for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY') if os.environ.get(k)]
if '--catalog' in sys.argv and env.get('GEMINI_API_KEY'):
    import httpx
    # Metadata request only: never invoke generateContent or print credentials/body.
    response = httpx.get('https://generativelanguage.googleapis.com/v1beta/models', headers={'x-goog-api-key': env['GEMINI_API_KEY']}, timeout=20)
    out['gemini_model_catalog_http_status'] = response.status_code
    out['gemini_catalog_key_accepted'] = response.status_code == 200
print(json.dumps(out, indent=2))
