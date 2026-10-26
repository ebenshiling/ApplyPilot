"""Compare configured key origins without printing credential values."""
import json
from pathlib import Path
import subprocess
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[3]
host = dotenv_values(root/'.env') if (root/'.env').exists() else {}
container = json.loads(subprocess.check_output(['docker.exe', 'inspect', 'applypilot-dashboard'], text=True))[0]
env = dict(item.split('=', 1) for item in container['Config']['Env'] if '=' in item)
print(json.dumps({'repo_gemini_key_present': bool(host.get('GEMINI_API_KEY')),
                  'service_gemini_key_matches_repo': env.get('GEMINI_API_KEY') == host.get('GEMINI_API_KEY')}, indent=2))
