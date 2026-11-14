import os
import tempfile
import unittest
from pathlib import Path

from subcore import srt

from .helpers import numbered


class ParseTests(unittest.TestCase):
    def test_basic_roundtrip(self):
        text = "1\n00:00:01,000 --> 00:00:02,500\nHello\nworld\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n"
        cues = srt.parse(text)
        self.assertEqual([c.index for c in cues], [1, 2])
        self.assertEqual(cues[0].text, "Hello\nworld")
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (1000, 2500))
        self.assertEqual(srt.serialize(cues), text)

    def test_bom_and_crlf(self):
        text = chr(0xFEFF) + "1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nYo\r\n"
        cues = srt.parse(text)
        self.assertEqual([c.text for c in cues], ["Hi", "Yo"])
        self.assertNotIn("\r", srt.serialize(cues))

    def test_empty_cue_is_kept(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nYo\n"
        cues = srt.parse(text)
        self.assertEqual([c.text for c in cues], ["", "Yo"])
        again = srt.parse(srt.serialize(cues))
        self.assertEqual([c.text for c in again], ["", "Yo"])

    def test_noncontiguous_and_duplicate_indexes_preserved(self):
        text = "7\n00:00:01,000 --> 00:00:02,000\nA\n\n7\n00:00:03,000 --> 00:00:04,000\nB\n\n20\n00:00:05,000 --> 00:00:06,000\nC\n"
        cues = srt.parse(text)
        self.assertEqual([c.index for c in cues], [7, 7, 20])
        self.assertEqual(srt.serialize(cues), text)

    def test_numeric_dialogue_is_not_mistaken_for_an_index(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n1984\n\n2\n00:00:03,000 --> 00:00:04,000\nYo\n"
        cues = srt.parse(text)
        self.assertEqual([c.text for c in cues], ["1984", "Yo"])

    def test_missing_index_gets_ordinal(self):
        cues = srt.parse("00:00:01,000 --> 00:00:02,000\nA\n\n00:00:03,000 --> 00:00:04,000\nB\n")
        self.assertEqual([c.index for c in cues], [1, 2])

    def test_timing_line_kept_verbatim_including_coordinates(self):
        timing = "00:00:01.5 --> 00:00:02.25 X1:10 X2:20"
        cue = srt.parse(f"1\n{timing}\nA\n")[0]
        self.assertEqual(cue.timing, timing)
        self.assertEqual((cue.start_ms, cue.end_ms), (1500, 2250))

    def test_no_cues_raises(self):
        with self.assertRaises(srt.SrtError):
            srt.parse("not a subtitle")

    def test_parse_file_falls_back_to_cp1252(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "x.srt")
            path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\ncafé\n".encode("cp1252"))
            self.assertEqual(srt.parse_file(path)[0].text, "café")


class TagTests(unittest.TestCase):
    def test_matching_tags_ok(self):
        self.assertEqual(srt.tag_problems("<i>Hi</i>", "<i>Chào</i>"), [])

    def test_dropped_tag_is_a_problem(self):
        self.assertTrue(srt.tag_problems("<i>Hi</i>", "Chào"))

    def test_unclosed_tag_is_a_problem(self):
        self.assertTrue(srt.tag_problems("<i>Hi</i>", "<i>Chào"))

    def test_misnested_tags_are_a_problem(self):
        self.assertTrue(srt.tag_problems("<i><b>Hi</b></i>", "<i><b>Chào</i></b>"))

    def test_font_attributes_do_not_matter(self):
        self.assertEqual(srt.tag_problems('<font color="red">A</font>', "<font color='blue'>B</font>"), [])

    def test_line_count_warning_only_above_two(self):
        self.assertIsNone(srt.line_count_warning("a\nb"))
        self.assertIsNotNone(srt.line_count_warning("a\nb\nc"))


class WriteAtomicTests(unittest.TestCase):
    def test_writes_utf8_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "out.vi.srt")
            srt.write_atomic(target, srt.serialize(numbered(2, "Xin chào {n}")))
            self.assertEqual(target.read_bytes().count("Xin chào".encode()), 2)
            self.assertEqual(os.listdir(tmp), ["out.vi.srt"])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_the_file_is_readable_by_other_users_not_the_private_temp_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp, "new.vi.srt")
            srt.write_atomic(fresh, "1\n")
            self.assertEqual(fresh.stat().st_mode & 0o777, 0o644)
            kept = Path(tmp, "kept.vi.srt")
            kept.write_text("old", encoding="utf-8")
            kept.chmod(0o664)
            srt.write_atomic(kept, "new")
            self.assertEqual(kept.stat().st_mode & 0o777, 0o664)

    def test_failed_write_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "out.srt")
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(UnicodeEncodeError):
                srt.write_atomic(target, "bad \ud800 surrogate")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(os.listdir(tmp), ["out.srt"])


if __name__ == "__main__":
    unittest.main()
