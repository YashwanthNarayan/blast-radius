# Scorer builds this on linux/amd64 and runs it with no network. The digest is
# the image the organizers labeled every commit in (see the starter kit).
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# Test dependencies for the selector's optional self-checks (PyPI is an allowed
# build-time source). sqlglot itself is installed from the pinned checkout below.
# Versions pinned to the starter kit's requirements.lock (the labeling environment).
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir pytest==9.2.0 pytest-xdist==3.8.0 pytest-timeout==2.4.0 duckdb==2.0.0 numpy==2.5.5 pandas==3.1.1 pytz==2026.6 python-dateutil==4.0.0 PyYAML==6.0.3

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

COPY selector.py /app/selector.py
COPY maps.json /app/maps.json
COPY linemap.json.gz /app/linemap.json.gz
RUN chmod a+r /app/selector.py /app/maps.json /app/linemap.json.gz

CMD ["python", "/app/selector.py"]
