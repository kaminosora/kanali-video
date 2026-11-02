"""Real localhost export smoke suite. Run while app.py is running.

Creates known synthetic media and exercises native Blender output, not mocks.
Artifacts remain in examples/ and outputs/ for inspection.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
import av
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Running panel URL; defaults to this checkout's .cache/server.json")
    parser.add_argument("--mode", choices=["cover", "standee", "motion", "master", "cancel", "all"], default="all")
    args = parser.parse_args()
    if not args.url:
        args.url = json.loads((ROOT / '.cache' / 'server.json').read_text(encoding='utf-8'))['url']
    def api(path, data=None):
        request = Request(args.url + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Content-Type": "application/json", "X-Session-Token": token})
        with urlopen(request, timeout=300) as response:
            return json.load(response)
    token = ""
    state = api("/api/state"); token = state["session_token"]
    examples = ROOT / "examples"; examples.mkdir(exist_ok=True)
    report_path = ROOT / "docs" / "pipeline-results.json"
    results = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    def wait(job):
        last = None
        while True:
            current = next(j for j in api("/api/jobs")["jobs"] if j["id"] == job["id"])
            progress = (current["status"], current["frame"])
            if progress != last:
                print(current["id"], current["status"], current["frame"], "/", current["total_frames"], flush=True)
                last = progress
            if current["status"] not in {"queued", "preparing", "rendering", "encoding"}:
                return current
            time.sleep(1)
    def inspect(job, label):
        assert job["status"] == "completed", job
        output = Path(job["output_path"])
        with av.open(str(output)) as container:
            stream = container.streams.video[0]
            count, first, last, times = 0, None, None, []
            for frame in container.decode(stream):
                array = frame.to_ndarray(format="rgb24")
                if first is None:
                    first = array
                last = array; times.append(float(frame.time)); count += 1
            assert (stream.width, stream.height) == (2240, 1080)
            assert count == job["total_frames"], (count, job)
            rate = float(stream.average_rate)
            assert 0 < rate <= 60
            assert all(b > a for a, b in zip(times, times[1:]))
            if output.suffix == ".mp4":
                assert stream.codec_context.format.name == "yuv420p"
                assert (stream.codec_context.colorspace, stream.codec_context.color_primaries, stream.codec_context.color_trc) == (1, 1, 1)
            difference = float(np.mean(np.abs(first.astype(float)-last.astype(float))))
            if label == "motion":
                assert difference > 0.2, "Video texture appears frozen"
                assert str(stream.average_rate) == "30000/1001"
            if label == "master":
                assert stream.codec_context.name == "ffv1"
            result = {"job_id": job["id"], "output_path": str(output), "blend_path": job["blend_path"],
                      "dimensions": [stream.width, stream.height], "fps": str(stream.average_rate), "frames": count,
                      "codec": stream.codec_context.name, "format": stream.codec_context.format.name,
                      "first_last_mean_difference": difference, "size_bytes": output.stat().st_size,
                      "time_last": times[-1]}
        target_name = {"cover": "calibration-cover.mp4", "standee": "calibration-standee.mp4",
                       "motion": "motion-demo.mp4", "master": "master-demo.mkv"}[label]
        shutil.copy2(output, examples / target_name)
        results[label] = result
        report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    modes = ["cover", "standee", "motion", "master", "cancel"] if args.mode == "all" else [args.mode]
    for mode in modes:
        source = api("/api/calibration", {})
        config = dict(state["defaults"], fit="cover", presentation="plain")
        request = {"source_id": source["id"], "config": config, "start": 0, "end": 3,
                   "quality": "high", "supersample": 2, "name": "calibration-"+mode, "fps": 30}
        if mode == "standee":
            config.update(fit="contain", presentation="standee")
        elif mode == "motion":
            path = examples / "motion-source.mp4"
            subprocess.run([state["ffmpeg_path"], "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                            "-i", "testsrc2=size=1920x1080:rate=30000/1001:duration=1.2", "-c:v", "libx264", "-crf", "10",
                            "-pix_fmt", "yuv420p", str(path)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            source = api("/api/import", {"path": str(path)})
            request.update(source_id=source["id"], start=.1, end=.7, fps=0, name="motion-demo")
        elif mode == "master":
            request.update(quality="master", end=.1, fps=60, supersample=1, name="master-demo")
        elif mode == "cancel":
            request.update(end=60, name="cancel-test")
        job = api("/api/render", request)
        if mode == "cancel":
            api("/api/cancel", {"job_id": job["id"]})
            outcome = wait(job)
            assert outcome["status"] == "cancelled", outcome
            assert outcome["output_path"] is None
            results["cancel"] = {"job_id": job["id"], "status": outcome["status"], "output_path": None}
            report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            inspect(wait(job), mode)


if __name__ == "__main__":
    main()
