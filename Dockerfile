# Scorer builds this on linux/amd64 and runs it with no network. The digest is
# the image the organizers labeled every commit in (see the starter kit).
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# sqlglot's own repository at the pinned commit (allowed download at build time).
# Kept pristine at /opt/repo for the selector's harmlessness (AST) checks,
# fixture/text lookups and any decision-time test runs.
RUN python - <<'PY'
import tarfile, urllib.request, io, os, shutil
url = "https://github.com/tobymao/sqlglot/archive/eb11aec4be8cb2122afe713a4816662c3f0fb2fa.tar.gz"
data = urllib.request.urlopen(url, timeout=120).read()
tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
tf.extractall("/opt")
shutil.move("/opt/sqlglot-eb11aec4be8cb2122afe713a4816662c3f0fb2fa", "/opt/repo")
os.chmod("/opt/repo", 0o755)
PY
RUN chmod -R a+rX /opt/repo

COPY coverage_selector/selector.py coverage_selector/measured.json.gz.b64 coverage_selector/values.json.gz.b64 coverage_selector/catalog.sha256 coverage_selector/registry.json coverage_selector/native.json.gz.b64 linemap.json.gz /app/
RUN python - <<'PYMAP'
import base64
from pathlib import Path
for encoded in Path("/app").glob("*.gz.b64"):
    encoded.with_suffix("").write_bytes(base64.b64decode(encoded.read_bytes()))
    encoded.unlink()
PYMAP

CMD ["python", "/app/selector.py"]
