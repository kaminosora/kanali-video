"""Stop only this project's verified localhost service, cancelling its jobs."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
try:
    url = json.loads((root / ".cache" / "server.json").read_text(encoding="utf-8"))["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("Unexpected local service URL")
    with urlopen(url + "/api/state", timeout=2) as response:
        state = json.load(response)
    if Path(state["output_dir"]).resolve() != (root / "outputs").resolve():
        raise ValueError("This service belongs to another project")
    request = Request(url + "/api/shutdown", data=b"{}", headers={
        "Content-Type": "application/json", "X-Session-Token": state["session_token"]})
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    print("Stopping PANORAMA Mapper; active work is being cancelled.")
except OSError:
    print("PANORAMA Mapper is not running.")
