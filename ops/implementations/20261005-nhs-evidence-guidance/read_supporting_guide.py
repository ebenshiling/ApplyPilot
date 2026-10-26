"""Extract an official PDF into readable research text without saving files."""
import io
import urllib.request
from pypdf import PdfReader

url = 'https://www.nhsbsa.nhs.uk/sites/default/files/2026-08/How%20to%20apply%20for%20a%20job%20-%20Add%20your%20supporting%20evidence%20in%20NHS%20Jobs%20user%20guide.pdf'
request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/pdf'})
with urllib.request.urlopen(request, timeout=30) as response:
    data = response.read(5_000_001)
if len(data) > 5_000_000:
    raise ValueError('Research PDF exceeds 5 MB')
reader = PdfReader(io.BytesIO(data))
for index, page in enumerate(reader.pages):
    print(f'PAGE {index+1}\n{page.extract_text()}\n')
