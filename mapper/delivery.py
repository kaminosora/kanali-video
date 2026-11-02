"""Strict MP4 delivery gate; stdlib-only so Blender can use the same contract.

Bitrate is a planning input, never a size guarantee. Only an actual stat below
the decimal byte limit permits publication. FFV1 archival jobs bypass this module.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

MAX_MP4_BYTES = 500_000_000
TARGET_MP4_BYTES = 480_000_000


class DeliveryBudgetError(RuntimeError):
    """The finite encoding attempts could not satisfy the strict delivery limit."""


def _duration(options):
    if all(key in options for key in ('frames', 'fps_num', 'fps_den')):
        parts = [options[key] for key in ('frames', 'fps_num', 'fps_den')]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value <= 0 for value in parts):
            raise ValueError('Frame count and rational frame rate must be finite and positive')
        duration = parts[0] * parts[2] / parts[1]
    else:
        duration = options.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('A finite positive encoded duration is required')
    return duration


def _target(limit, target_bytes):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= MAX_MP4_BYTES:
        raise ValueError('MP4 limit must be an integer between 2 and 500000000 decimal bytes')
    target = min(TARGET_MP4_BYTES, int(limit * .96), limit-1) if target_bytes is None else target_bytes
    if isinstance(target, bool) or not isinstance(target, int) or not 0 < target < limit:
        raise ValueError('Target byte budget must be positive and strictly below the delivery limit')
    return target


def budget_bitrate(options, limit=MAX_MP4_BYTES, *, target_bytes=None):
    """Return estimated video bits/second after reserving MP4 muxing overhead.

    Encoded duration is frames * fps_den / fps_num when supplied, including the
    final rounded frame. Otherwise options['duration'] is required. The default
    target is 480 MB (96% for a smaller test limit). Actual size still MUST pass
    finalize_mp4's strict stat gate, because rate control and mux overhead vary.
    """
    target = _target(limit, target_bytes)
    duration = _duration(options)
    overhead = max(2048, math.ceil(target * .002), math.ceil(options.get('frames', 0) * 12))
    if target <= overhead:
        raise DeliveryBudgetError('Byte budget is too small for MP4 container overhead')
    return max(1, math.floor((target-overhead) * 8 / duration))


def two_pass_command(ffmpeg_path, candidate, output, bitrate, pass_number, passlog, progress_file):
    """CPU fallback with x264 stats paths valid in deep Windows directories."""
    if pass_number not in (1, 2):
        raise ValueError('pass_number must be 1 or 2')
    candidate, output = Path(candidate).resolve(), Path(output).resolve()
    passlog, progress_file = Path(passlog).resolve(), Path(progress_file).resolve()
    stats = str(passlog)
    # x264 opens its own statistics with the Windows CRT, bypassing FFmpeg's
    # long-path handling. Supply the extended absolute path to both passes.
    # A deep cwd is not a workaround: CreateProcess rejects it with WinError 267.
    if os.name == 'nt' and not stats.startswith('\\\\?\\'):
        stats = ('\\\\?\\UNC\\' + stats[2:]) if stats.startswith('\\\\') else ('\\\\?\\' + stats)
    command = [str(ffmpeg_path), '-hide_banner', '-nostdin', '-y', '-nostats',
               '-stats_period', '0.5', '-progress', str(progress_file), '-i', str(candidate),
               '-map', '0:v:0', '-an', '-sn', '-dn', '-map_metadata', '-1',
               '-vf', 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
               '-c:v', 'libx264', '-preset', 'slow', '-b:v', str(bitrate),
               '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-fps_mode', 'passthrough',
               '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709',
               '-color_trc', 'bt709', '-pass', str(pass_number), '-passlogfile', stats]
    if pass_number == 1:
        command += ['-f', 'null', os.devnull]
    else:
        command += ['-movflags', '+faststart', '-f', 'mp4', str(output)]
    return command


def _progress_frame(path):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell()-16384))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        latest = [line[6:].strip() for line in lines if line.startswith('frame=')]
        return max(0, int(latest[-1])) if latest else 0
    except (OSError, ValueError):
        return 0


def finalize_mp4(candidate, final, options, *, ffmpeg_path, runner=None, cancelled=None,
                  progress=None, limit=MAX_MP4_BYTES, target_bytes=None, max_attempts=3):
    """Copy or recompress a completed compatible candidate and publish atomically.

    runner(command: list[str]) -> integer exit code may replace the built-in
    cancellable subprocess runner; a custom runner must support cancellation
    while it runs and must return only after its process has exited. Both
    runners are checked for cancellation before/after every pass and publish.
    cancelled() -> bool; progress(event: dict) reports phases checking, encoding,
    retrying, publishing, completed. Encoding events contain attempt, pass_number,
    bitrate, progress_file, log_path; the default runner also emits frame updates.

    Returns output_path, size_bytes, candidate_bytes, reencoded, attempts,
    bitrate, duration, limit_bytes, target_bytes, log_path. The caller must only
    mark completed after successful return (and its own final cancellation gate).
    Candidate is preserved; every retry reads it, never the preceding compressed
    attempt. Diagnostic logs/pass statistics survive failure; partial MP4s do not.
    Final must not already exist. This validates size, not arbitrary input media:
    candidate must be a successfully encoded H.264/yuv420p/BT.709 CFR MP4.
    """
    target = _target(limit, target_bytes)
    duration = _duration(options)
    if options.get('quality') == 'master':
        raise ValueError('FFV1 archival outputs must bypass the compatible MP4 delivery gate')
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
        raise ValueError('max_attempts must be an integer between 1 and 10')
    candidate, final = Path(candidate).resolve(), Path(final).resolve()
    if candidate == final:
        raise ValueError('Candidate and delivery paths must differ')
    if final.exists():
        raise FileExistsError(f'Will not overwrite existing delivery: {final}')
    if final.suffix.lower() != '.mp4':
        raise ValueError('Compatible delivery requires an .mp4 filename')
    original_size = candidate.stat().st_size
    if not candidate.is_file() or original_size <= 0:
        raise ValueError('Candidate must be a nonempty, completed video file')
    final.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    work = final.parent / ('.delivery-' + token)
    work.mkdir()
    log_path = work / 'delivery.log'
    partial = final.parent / ('.' + final.stem + '.' + token + '.partial.mp4')
    published = False
    attempts, bitrate, size = 0, None, original_size

    def check_cancel():
        if cancelled and cancelled():
            raise InterruptedError('MP4 delivery cancelled')

    def emit(phase, **extra):
        event = dict(phase=phase, limit_bytes=limit, target_bytes=target,
                     log_path=str(log_path), **extra)
        if progress:
            progress(event)

    def run(command, event):
        check_cancel()
        with log_path.open('a', encoding='utf-8') as log:
            log.write('COMMAND: ' + json.dumps(command, ensure_ascii=False) + '\n')
            log.flush()
            if runner is not None:
                code = runner(command)
            else:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                last_report = 0
                try:
                    while proc.poll() is None:
                        check_cancel()
                        try:
                            proc.wait(timeout=.1)
                        except subprocess.TimeoutExpired:
                            pass
                        if time.monotonic()-last_report >= .5:
                            emit('encoding', **event, frame=_progress_frame(event['progress_file']),
                                 total_frames=options.get('frames'))
                            last_report = time.monotonic()
                    code = proc.returncode
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
            log.write(f'EXIT: {code}\n')
        check_cancel()
        if not isinstance(code, int) or code != 0:
            raise RuntimeError(f'MP4 encoding failed (exit {code}); see {log_path}')

    try:
        check_cancel()
        with log_path.open('a', encoding='utf-8') as log:
            log.write(f'Candidate: {candidate}\nBytes: {original_size}\nStrict limit: {limit}\nTarget: {target}\n')
        emit('checking', size_bytes=original_size)
        if original_size < limit:
            # Copy in bounded chunks so even a compliant large candidate can be cancelled.
            with candidate.open('rb') as source, partial.open('xb') as output:
                while chunk := source.read(1024*1024):
                    check_cancel()
                    output.write(chunk)
            size = partial.stat().st_size
        else:
            bitrate = budget_bitrate(options, limit, target_bytes=target)
            for attempt in range(1, max_attempts+1):
                attempts = attempt
                passlog = work / f'attempt-{attempt}'
                for pass_number in (1, 2):
                    progress_file = work / f'attempt-{attempt}-pass-{pass_number}.progress'
                    event = dict(attempt=attempt, pass_number=pass_number, bitrate=bitrate,
                                 progress_file=str(progress_file))
                    check_cancel()
                    emit('encoding', **event)
                    command = two_pass_command(ffmpeg_path, candidate, partial, bitrate,
                                               pass_number, passlog, progress_file)
                    run(command, event)
                size = partial.stat().st_size
                emit('checking', attempt=attempt, size_bytes=size, bitrate=bitrate)
                if 0 < size < limit:
                    break
                if size <= 0:
                    raise RuntimeError(f'Encoder produced an empty MP4; see {log_path}')
                partial.unlink()
                if attempt < max_attempts:
                    # Use measured error, preserve extra room, and strictly lower the rate.
                    bitrate = max(1, min(bitrate-1, math.floor(bitrate * target / size * .95)))
                    emit('retrying', attempt=attempt+1, size_bytes=size, bitrate=bitrate)
        check_cancel()
        if not 0 < size < limit:
            raise DeliveryBudgetError(f'MP4 size {size} bytes is not strictly below {limit} after {attempts} attempts; see {log_path}')
        emit('publishing', size_bytes=size, attempt=attempts)
        check_cancel()
        if final.exists():
            raise FileExistsError(f'Will not overwrite existing delivery: {final}')
        # Windows rename refuses an existing destination; POSIX link is likewise
        # atomic and exclusive. Both operate on sibling files on one filesystem.
        if os.name == 'nt':
            os.rename(partial, final)
        else:
            os.link(partial, final)
        published = True
        check_cancel()
        if final.stat().st_size >= limit:
            raise DeliveryBudgetError('Published file failed the final strict size check')
        emit('completed', size_bytes=size, attempt=attempts)
        check_cancel()
        return dict(output_path=str(final), size_bytes=size, candidate_bytes=original_size,
                    reencoded=bool(attempts), attempts=attempts, bitrate=bitrate,
                    duration=duration, limit_bytes=limit, target_bytes=target, log_path=str(log_path))
    except BaseException as exc:
        if published:
            final.unlink(missing_ok=True)
        try:
            with log_path.open('a', encoding='utf-8') as log:
                log.write(f'{type(exc).__name__}: {exc}\n')
        except OSError:
            pass  # Keep the original failure, especially ENOSPC.
        raise
    finally:
        original_error = sys.exc_info()[1]
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:
            if original_error is None:
                raise
            original_error.add_note(f'Could not remove partial MP4 {partial}: {exc}')
