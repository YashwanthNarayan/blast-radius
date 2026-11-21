# Scorer builds this on linux/amd64 and runs it with no network. The digest is
# the image the organizers labeled every commit in (see the starter kit).
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# sqlglot's own repository at the pinned commit (allowed download at build time).
# Kept pristine at /opt/repo for the selector's harmlessness (AST) checks and
# fixture/text lookups at run time.
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
RUN chmod a+r /app/selector.py /app/maps.json

CMD ["python", "/app/selector.py"]
