#!/usr/bin/env python3
"""Video Studio CLI: small, predictable ffmpeg wrappers for shorts, edits and ads.

Every command builds one ffmpeg invocation; pass --dry-run to print it instead of
running it. Only the Python standard library and ffmpeg/ffprobe are required.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = json.loads((ROOT / "presets" / "platforms.json").read_text(encoding="utf-8"))
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
HIGGSFIELD_PROJECT_ID = "dd95e70a-f153-46de-b405-469c34535404"
CAPTION_FONT = "DejaVu Sans"  # has Hebrew glyphs; libass handles right-to-left text
ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]


# ---------------------------------------------------------------- helpers

def run(cmd: list[str], dry_run: bool) -> None:
    if dry_run:
        print(shlex.join(cmd))
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed with exit code {result.returncode}")


def ffmpeg(*args: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]


def probe(path: str) -> dict:
    if not Path(path).is_file():
        sys.exit(f"file not found: {path}")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"path": path, "duration": float(data["format"].get("duration", 0.0)),
            "has_video": video is not None, "has_audio": audio is not None}
    if video:
        num, den = (video.get("r_frame_rate") or "0/1").split("/")
        info.update(width=video["width"], height=video["height"],
                    fps=round(float(num) / float(den), 3) if float(den) else 0.0,
                    video_codec=video["codec_name"])
    if audio:
        info.update(audio_codec=audio["codec_name"], sample_rate=int(audio["sample_rate"]))
    return info


def frame_filter(width: int, height: int, mode: str) -> str:
    """Fit any input into width x height: crop (fill), blur (blurred fill) or pad (letterbox)."""
    if mode == "crop":
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1")
    if mode == "pad":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if mode == "blur":
        return (f"split=2[bg][fg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=20:2[bgb];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    raise SystemExit(f"unknown fit mode: {mode}")


def filter_path(path: str) -> str:
    """Escape a file path for use inside an ffmpeg filter argument."""
    return str(Path(path).resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def keep_segments(silences: list[tuple[float, float]], duration: float, pad: float) -> list[tuple[float, float]]:
    """Invert detected silences into the speech segments to keep, with padding."""
    segments, cursor = [], 0.0
    for start, end in silences:
        seg_end = min(duration, start + pad)
        if seg_end - cursor > 0.05:
            segments.append((round(cursor, 3), round(seg_end, 3)))
        cursor = max(cursor, end - pad)
    if duration - cursor > 0.05:
        segments.append((round(cursor, 3), round(duration, 3)))
    return segments


def parse_silences(log: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends))


# ---------------------------------------------------------------- commands

def cmd_new(a):
    target = ROOT / "projects" / a.name
    if target.exists():
        sys.exit(f"project already exists: {target}")
    shutil.copytree(ROOT / "projects" / "_template", target)
    meta = {"name": a.name, "created": dt.date.today().isoformat(), "type": a.type,
            "targets": a.targets.split(","), "higgsfield_project_id": HIGGSFIELD_PROJECT_ID,
            "notes": ""}
    (target / "project.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    print(target)


def cmd_probe(a):
    print(json.dumps(probe(a.input), indent=2))


def cmd_trim(a):
    args = ["-ss", str(a.start), "-i", a.input]
    if a.end is not None:
        args += ["-t", str(float(a.end) - float(a.start))]
    run(ffmpeg(*args, *ENCODE, a.output), a.dry_run)


def cmd_concat(a):
    width, height = ASPECTS[a.aspect]
    inputs, chains, labels = [], [], []
    for i, path in enumerate(a.inputs):
        info = probe(path)
        inputs += ["-i", path]
        chains.append(f"[{i}:v]{frame_filter(width, height, a.fit)},fps={a.fps},format=yuv420p[v{i}]")
        if info["has_audio"]:
            chains.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={info['duration']}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + f";{''.join(labels)}concat=n={len(a.inputs)}:v=1:a=1[v][a]"
    run(ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", *ENCODE, a.output),
        a.dry_run)


def cmd_reframe(a):
    width, height = ASPECTS[a.aspect]
    run(ffmpeg("-i", a.input, "-filter_complex", f"[0:v]{frame_filter(width, height, a.fit)}[v]",
               "-map", "[v]", "-map", "0:a?", *ENCODE, a.output), a.dry_run)


def cmd_silence_cut(a):
    info = probe(a.input)
    if not info["has_audio"]:
        sys.exit("input has no audio track; nothing to detect")
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", a.input, "-af",
         f"silencedetect=noise={a.threshold}:d={a.min_silence}", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    segments = keep_segments(parse_silences(log), info["duration"], a.pad)
    if not segments:
        sys.exit("everything is silent at this threshold; try a lower --threshold")
    expr = "+".join(f"between(t,{s},{e})" for s, e in segments)
    kept = sum(e - s for s, e in segments)
    print(f"keeping {len(segments)} segments, {kept:.1f}s of {info['duration']:.1f}s", file=sys.stderr)
    vf = f"select='{expr}',setpts=N/FRAME_RATE/TB"
    af = f"aselect='{expr}',asetpts=N/SR/TB"
    run(ffmpeg("-i", a.input, "-vf", vf, "-af", af, *ENCODE, a.output), a.dry_run)


def cmd_subs(a):
    style = {"shorts": "Fontsize=16,Bold=1,Outline=3,Shadow=0,MarginV=70,Alignment=2",
             "clean": "Fontsize=12,Bold=0,Outline=1,Shadow=1,MarginV=25,Alignment=2"}[a.style]
    vf = (f"subtitles='{filter_path(a.subtitles)}':charenc=UTF-8:"
          f"force_style='Fontname={CAPTION_FONT},PrimaryColour=&H00FFFFFF,"
          f"OutlineColour=&H00000000,BorderStyle=1,{style}'")
    run(ffmpeg("-i", a.input, "-vf", vf, "-map", "0:v", "-map", "0:a?", *ENCODE, a.output), a.dry_run)


def cmd_text(a):
    """Overlay a title / call-to-action (Hebrew-safe, rendered through libass)."""
    info = probe(a.input)
    width, height = info["width"], info["height"]
    end = a.end if a.end is not None else info["duration"]
    align = {"top": 8, "center": 5, "bottom": 2}[a.position]
    size = round(height * a.size)
    ass = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
        f"Style: T,{CAPTION_FONT},{size},&H00FFFFFF,&H00000000,&H80000000,1,"
        f"{3 if a.box else 1},{max(2, size // 12)},0,{align},60,60,{round(height * 0.08)}", "",
        "[Events]", "Format: Layer, Start, End, Style, Text",
        f"Dialogue: 0,{ass_time(a.start)},{ass_time(end)},T,{{\\fad(200,200)}}{a.text}", ""])
    ass_path = Path(a.output).with_suffix(".overlay.ass")
    ass_path.write_text(ass, encoding="utf-8")
    run(ffmpeg("-i", a.input, "-vf", f"ass='{filter_path(str(ass_path))}'",
               "-map", "0:v", "-map", "0:a?", *ENCODE, a.output), a.dry_run)


def cmd_music(a):
    info = probe(a.input)
    inputs = ["-i", a.input, "-stream_loop", "-1", "-i", a.music]
    fade_out = max(0.0, info["duration"] - a.fade)
    bed = (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={a.volume},"
           f"atrim=duration={info['duration']},afade=t=in:d={a.fade},"
           f"afade=t=out:st={fade_out}:d={a.fade}")
    if not info["has_audio"]:
        graph = f"{bed}[a]"
    elif a.no_duck:
        graph = (f"{bed}[m];[0:a]aformat=sample_rates=48000:channel_layouts=stereo[v];"
                 f"[v][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:  # duck the music under the voice track
        graph = (f"{bed}[m];[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[v][sc];"
                 f"[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[md];"
                 f"[v][md]amix=inputs=2:duration=first:normalize=0[a]")
    run(ffmpeg(*inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", a.output), a.dry_run)


def cmd_export(a):
    p = PRESETS[a.preset]
    info = probe(a.input)
    if p["max_seconds"] and info["duration"] > p["max_seconds"]:
        print(f"warning: {info['duration']:.0f}s exceeds {a.preset} limit of {p['max_seconds']}s",
              file=sys.stderr)
    graph = f"[0:v]{frame_filter(p['width'], p['height'], a.fit)},fps={p['fps']},format=yuv420p[v]"
    args = ["-i", a.input, "-filter_complex", graph, "-map", "[v]"]
    if info["has_audio"]:
        args += ["-map", "0:a", "-af", f"loudnorm=I={p['lufs']}:TP=-1.0:LRA=11"]
    args += ["-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-b:v", p["video_bitrate"],
             "-maxrate", p["video_bitrate"], "-bufsize", p["video_bitrate"].replace("M", "") + "M",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", a.output]
    run(ffmpeg(*args), a.dry_run)


def cmd_thumb(a):
    run(ffmpeg("-ss", str(a.at), "-i", a.input, "-frames:v", "1", "-q:v", "2", a.output), a.dry_run)


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vs", description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the ffmpeg command only")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("new", help="create a project folder from the template")
    s.add_argument("name")
    s.add_argument("--type", choices=["short", "edit", "ad"], default="short")
    s.add_argument("--targets", default="tiktok,reels,shorts")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("probe", help="show duration, size, fps and tracks")
    s.add_argument("input")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("trim", help="cut a range (seconds)")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float)
    s.set_defaults(func=cmd_trim)

    s = sub.add_parser("concat", help="join clips into one, normalising size/fps/audio")
    s.add_argument("output"); s.add_argument("inputs", nargs="+")
    s.add_argument("--aspect", choices=ASPECTS, default="9:16")
    s.add_argument("--fit", choices=["crop", "blur", "pad"], default="crop")
    s.add_argument("--fps", type=int, default=30)
    s.set_defaults(func=cmd_concat)

    s = sub.add_parser("reframe", help="change aspect ratio (e.g. landscape to 9:16)")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--aspect", choices=ASPECTS, default="9:16")
    s.add_argument("--fit", choices=["crop", "blur", "pad"], default="blur")
    s.set_defaults(func=cmd_reframe)

    s = sub.add_parser("silence-cut", help="remove pauses from talking footage")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--threshold", default="-35dB")
    s.add_argument("--min-silence", type=float, default=0.6)
    s.add_argument("--pad", type=float, default=0.12)
    s.set_defaults(func=cmd_silence_cut)

    s = sub.add_parser("subs", help="burn an .srt/.ass caption file into the video")
    s.add_argument("input"); s.add_argument("subtitles"); s.add_argument("output")
    s.add_argument("--style", choices=["shorts", "clean"], default="shorts")
    s.set_defaults(func=cmd_subs)

    s = sub.add_parser("text", help="add a title or call-to-action overlay")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--text", required=True)
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float)
    s.add_argument("--position", choices=["top", "center", "bottom"], default="top")
    s.add_argument("--size", type=float, default=0.045, help="font size as a fraction of height")
    s.add_argument("--box", action="store_true", help="draw a dark box behind the text")
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("music", help="add background music, ducked under speech")
    s.add_argument("input"); s.add_argument("music"); s.add_argument("output")
    s.add_argument("--volume", type=float, default=0.18)
    s.add_argument("--fade", type=float, default=1.0)
    s.add_argument("--no-duck", action="store_true")
    s.set_defaults(func=cmd_music)

    s = sub.add_parser("export", help="final render for a platform preset")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--preset", choices=PRESETS, required=True)
    s.add_argument("--fit", choices=["crop", "blur", "pad"], default="crop")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("thumb", help="grab a thumbnail frame")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--at", type=float, default=1.0)
    s.set_defaults(func=cmd_thumb)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
