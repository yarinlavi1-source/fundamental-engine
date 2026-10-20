"""End-to-end checks for the Video Studio CLI using synthetic ffmpeg media."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vs  # noqa: E402


def make_clip(path, seconds=4, size="1920x1080", tone=True, gap=None):
    """Test pattern video; with gap=(a, b) the tone is silent between a and b."""
    args = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if tone:
        vol = f",volume='if(between(t,{gap[0]},{gap[1]}),0,1)':eval=frame" if gap else ""
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-af", f"anull{vol}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True)


class PureHelpers(unittest.TestCase):
    def test_keep_segments_inverts_silence(self):
        self.assertEqual(vs.keep_segments([(1.0, 2.0)], 4.0, 0.1), [(0.0, 1.1), (1.9, 4.0)])

    def test_ass_time(self):
        self.assertEqual(vs.ass_time(83.456), "0:01:23.46")

    def test_presets_have_required_fields(self):
        for name, p in vs.PRESETS.items():
            self.assertTrue({"width", "height", "fps", "video_bitrate", "lufs"} <= p.keys(), name)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.land = cls.dir / "land.mp4"
        make_clip(cls.land, gap=(1.5, 2.8))
        cls.mute = cls.dir / "mute.mp4"
        make_clip(cls.mute, seconds=2, size="1080x1920", tone=False)
        cls.music = cls.dir / "music.m4a"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=2", str(cls.music)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return str(self.dir / name)

    def test_reframe_blur_to_vertical(self):
        vs.main(["reframe", str(self.land), self.out("v.mp4"), "--aspect", "9:16"])
        info = vs.probe(self.out("v.mp4"))
        self.assertEqual((info["width"], info["height"]), (1080, 1920))
        self.assertTrue(info["has_audio"])

    def test_trim(self):
        vs.main(["trim", str(self.land), self.out("t.mp4"), "--start", "1", "--end", "3"])
        self.assertAlmostEqual(vs.probe(self.out("t.mp4"))["duration"], 2.0, delta=0.15)

    def test_silence_cut_shortens(self):
        vs.main(["silence-cut", str(self.land), self.out("s.mp4"), "--min-silence", "0.5"])
        self.assertLess(vs.probe(self.out("s.mp4"))["duration"], 3.5)

    def test_concat_mixed_audio(self):
        vs.main(["concat", self.out("c.mp4"), str(self.land), str(self.mute)])
        info = vs.probe(self.out("c.mp4"))
        self.assertAlmostEqual(info["duration"], 6.0, delta=0.2)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))

    def test_hebrew_subs_and_text(self):
        srt = self.dir / "c.srt"
        srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nשלום עולם\n", encoding="utf-8")
        vs.main(["subs", str(self.land), str(srt), self.out("sub.mp4")])
        vs.main(["text", self.out("sub.mp4"), self.out("cta.mp4"), "--text", "קנו עכשיו", "--box"])
        self.assertTrue(vs.probe(self.out("cta.mp4"))["has_video"])

    def test_music_ducked_and_on_silent_clip(self):
        vs.main(["music", str(self.land), str(self.music), self.out("m.mp4")])
        vs.main(["music", str(self.mute), str(self.music), self.out("m2.mp4")])
        self.assertTrue(vs.probe(self.out("m2.mp4"))["has_audio"])

    def test_export_preset(self):
        vs.main(["export", str(self.land), self.out("ad.mp4"), "--preset", "ad-feed"])
        info = vs.probe(self.out("ad.mp4"))
        self.assertEqual((info["width"], info["height"]), (1080, 1350))

    def test_thumb(self):
        vs.main(["thumb", str(self.land), self.out("th.jpg")])
        self.assertTrue(Path(self.out("th.jpg")).stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
