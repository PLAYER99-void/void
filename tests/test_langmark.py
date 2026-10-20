"""Tests for app/langmark.py: the {fr:…} marks Claude puts on foreign names, and how server.py speaks and shows them.
No audio and no network: the phonemizer and Kokoro are fakes, except one test that uses the real espeak-ng phonemizer
when it's installed (skipped otherwise).

    python3 -m unittest discover -s tests
"""
import io
import sys
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import langmark  # noqa: E402


def fake_phonemize(text, lang):
    return f"<{lang}:{text.strip()}>"


class Markup(unittest.TestCase):
    def test_unmark(self):
        self.assertEqual(langmark.unmark("I loved {fr:Les Jardins de Minuit}, really."), "I loved Les Jardins de Minuit, really.")
        self.assertEqual(langmark.unmark("{es:Muchas gracias} and {pt-br:São Paulo}"), "Muchas gracias and São Paulo")
        self.assertEqual(langmark.unmark("{xx:Glorb} stays a word"), "Glorb stays a word")      # unknown code: just the words
        self.assertEqual(langmark.unmark("cut short: {fr:Les Jar"), "cut short: Les Jar")       # unclosed at the very end
        self.assertEqual(langmark.unmark(""), "")
        self.assertEqual(langmark.unmark(None), "")

    def test_leaves_everything_else_alone(self):
        for t in ("[happy] Fixed it.", "a [link](http://x.y) and **bold** _it_", "`{id:5}` in code",
                  "```js\nconst o = {id:5}\n```", "{ fr: spaced }", "{french:no}", "{f:no}", "{fr:}", "{\"fr\": 1}",
                  "two\n{fr:lines\n}"):
            self.assertEqual(langmark.unmark(t), t, t)

    def test_spans(self):
        self.assertEqual(langmark.spans("I loved {fr:Les Jardins de Minuit}, by {ja:Hayao Miyazaki}."),
                         [(None, "I loved "), ("fr-fr", "Les Jardins de Minuit"), (None, ", by "), ("ja", "Hayao Miyazaki"),
                          (None, ".")])
        self.assertEqual(langmark.spans("{de:Die Zauberflöte}"), [("de", "Die Zauberflöte")])
        self.assertEqual(langmark.spans("{IT:La dolce vita} {es-mx:Hola}"), [("it", "La dolce vita"), ("es", "Hola")])
        self.assertEqual(langmark.spans("{xx:Glorb} is {fr:bien}"), [(None, "Glorb is "), ("fr-fr", "bien")])
        self.assertEqual(langmark.spans("plain English"), [(None, "plain English")])
        self.assertEqual(langmark.spans("`{fr:code}` here"), [(None, "`{fr:code}` here")])
        for code in ("fr", "es", "de", "it", "pt", "ja"):
            self.assertTrue(langmark.lang_of(code), code)

    def test_phonemes_one_string_in_order(self):
        ph = langmark.phonemes("I loved {fr:Les Jardins de Minuit}, really.", fake_phonemize, "en-gb")
        self.assertEqual(ph, "<en-gb:I loved> <fr-fr:Les Jardins de Minuit> <en-gb:, really.>")

    def test_phonemes_none_when_nothing_to_do(self):
        self.assertIsNone(langmark.phonemes("Just English.", fake_phonemize))
        self.assertIsNone(langmark.phonemes("An {xx:unknown} code.", fake_phonemize))     # → plain English, as before

    def test_espeak_language_flags_dropped(self):
        ph = langmark.phonemes("{fr:Le weekend}", lambda t, lang: "lə (en)wiːkˈɛnd(fr)")
        self.assertEqual(ph, "lə wiːkˈɛnd")

    def test_romaji(self):
        self.assertEqual(langmark.romaji("Shinjuku"), "ɕiɴʥɯkɯ")
        self.assertEqual(langmark.romaji("konnichiwa"), "koɴniʨiwa")
        self.assertEqual(langmark.romaji("Tōkyō"), "toːkjoː")
        self.assertEqual(langmark.romaji("Fuji-san"), "ɸɯʥi-saɴ")
        ph = langmark.phonemes("{ja:Hayao Miyazaki}", lambda t, lang: self.fail("espeak can't read rōmaji"))
        self.assertEqual(ph, "hajao mijazaki")


class Guess(unittest.TestCase):
    """The fallback for a title Claude forgot to mark: only what's clearly foreign, never English."""

    def test_marks_clear_foreign_titles(self):
        for t, want in (("Le Toit du Monde", "{fr:Le Toit du Monde}"),           # the real miss
                        ("Le Toit Du Monde", "{fr:Le Toit Du Monde}"),
                        ("Les Jardins de Minuit", "{fr:Les Jardins de Minuit}"),
                        ("Écailles de lune", "{fr:Écailles de lune}"),
                        ("Der Himmel über Berlin", "{de:Der Himmel über Berlin}"),
                        ("La Vie Est Prelude", "{fr:La Vie Est Prelude}"),
                        ("Les Misérables", "{fr:Les Misérables}"),
                        ("El Laberinto del Fauno", "{es:El Laberinto del Fauno}"),
                        ("Die Zauberflöte", "{de:Die Zauberflöte}")):
            self.assertEqual(langmark.guess(t), want, t)

    def test_in_a_sentence(self):
        self.assertEqual(langmark.guess("[happy] Tonight I played Le Toit Du Monde by Alcest."),
                         "[happy] Tonight I played {fr:Le Toit Du Monde} by Alcest.")
        self.assertEqual(langmark.guess("Then Les Jardins de Minuit, and Alcest's Le Toit du Monde."),
                         "Then {fr:Les Jardins de Minuit}, and Alcest's {fr:Le Toit du Monde}.")
        self.assertEqual(langmark.guess("I loved “écailles de lune”."), "I loved “{fr:écailles de lune}”.")
        self.assertEqual(langmark.guess("Le Toit du Monde die hard fans"), "{fr:Le Toit du Monde} die hard fans")

    def test_english_stays_english(self):
        for t in ("Die Hard", "The Art of Sombre Ecstacy", "Death Atlas", "Dead End Residents", "Bring Back the Plague",
                  "La Jolla", "We decided the design was dead, despite the delay.", "De La Soul", "La La Land",
                  "Die Die My Darling", "Van Der Graaf Generator", "Robert De Niro", "Leonardo da Vinci",
                  "Los Angeles", "Mötley Crüe", "Pokémon", "Then De La Soul played Le Freak", "Van Halen et Le Tigre",
                  "I watched Die Hard and Das Boot", "Do You Know Da Way", "Lana Del Rey", "LE TOIT DU MONDE",
                  "Sen to Chihiro", "Sen to Chihiro no Kamikakushi"):                  # rōmaji: out of scope
            self.assertEqual(langmark.guess(t), t, t)

    def test_leaves_code_marks_and_tags_alone(self):
        for t in ("`{id:5}` in code", "see `Le Toit du Monde` here", "```\nLe Toit du Monde\n```",
                  "{fr:Le Toit du Monde}", "{es:Le Toit du Monde}", "{xx:Le Toit du Monde}", "[Le Toit du Monde]", ""):
            self.assertEqual(langmark.guess(t), t, t)
        self.assertEqual(langmark.guess("{fr:Le Toit du Monde} and Les Jardins de Minuit"),
                         "{fr:Le Toit du Monde} and {fr:Les Jardins de Minuit}")
        self.assertEqual(langmark.guess(None), "")

    def test_phonemes_say_the_unmarked_title_in_french(self):
        self.assertEqual(langmark.phonemes("I loved Le Toit du Monde.", fake_phonemize),
                         "<en-us:I loved> <fr-fr:Le Toit du Monde> <en-us:.>")
        self.assertIsNone(langmark.phonemes("I loved Die Hard.", fake_phonemize))
        self.assertEqual(langmark.unmark("I loved Le Toit du Monde."), "I loved Le Toit du Monde.")   # captions


try:
    from kokoro_onnx.tokenizer import Tokenizer
    REAL = Tokenizer().phonemize
except Exception:  # noqa: BLE001
    REAL = None


@unittest.skipUnless(REAL, "Kokoro's phonemizer (espeak-ng) isn't installed")
class RealPhonemes(unittest.TestCase):
    def test_french_is_french(self):
        fr = langmark.phonemes("{fr:Les Jardins de Minuit}", REAL)
        self.assertNotEqual(fr, REAL("Les Jardins de Minuit", "en-us"))
        self.assertIn("ʒaʁd", fr)


def tiny_wav():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * 240)
    return buf.getvalue()


class Server(unittest.TestCase):
    """How server.py uses it: Kokoro gets phonemes, everything else (captions, history, piper, espeak) the words."""

    @classmethod
    def setUpClass(cls):
        try:
            import server
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"server.py doesn't import here: {e}")
        cls.s = server

    def test_shown_and_saved_without_marks(self):
        self.assertEqual(self.s.untag("[proud] I loved {fr:Les Jardins}. [curious] You?"), "I loved Les Jardins. You?")

    def test_spoken_keeps_marks_but_not_in_code(self):
        t = self.s.speakable("[happy] I loved {fr:Les Jardins}, see `{id:5}`.")
        self.assertIn("{fr:Les Jardins}", t)
        self.assertNotIn("{id", t)
        self.assertNotIn("[happy]", t)

    def test_kokoro_one_call_with_phonemes(self):
        calls = []

        class FakeKokoro:
            tokenizer = types.SimpleNamespace(phonemize=fake_phonemize)

            def create(self, text, **kw):
                import numpy as np
                calls.append((text, kw))
                return np.zeros(240, dtype="float32"), 24000

        fake = types.ModuleType("kokoro_onnx")
        fake.Kokoro = FakeKokoro
        with mock.patch.dict(sys.modules, {"kokoro_onnx": fake}), mock.patch.dict(self.s._KOKORO, {"k": FakeKokoro()}):
            self.s.kokoro_synth("I loved {fr:Les Jardins}.", "af_heart", 1.0)
            self.assertEqual(calls[-1], ("<en-us:I loved> <fr-fr:Les Jardins> <en-us:.>",
                                         {"voice": "af_heart", "speed": 1.0, "is_phonemes": True}))
            self.s.kokoro_synth("Just {xx:English}.", "bm_george", 1.0)          # nothing to phonemize: as before
            self.assertEqual(calls[-1], ("Just English.", {"voice": "bm_george", "speed": 1.0, "lang": "en-gb"}))
            FakeKokoro.tokenizer = types.SimpleNamespace(phonemize=mock.Mock(side_effect=RuntimeError("no fr")))
            with mock.patch("sys.stderr", io.StringIO()):
                self.s.kokoro_synth("I loved {fr:Les Jardins}.", "af_heart", 1.0)   # phonemizer broke: plain English
            self.assertEqual(calls[-1][0], "I loved Les Jardins.")
            self.assertEqual(len(calls), 3)

    def test_espeak_gets_the_words(self):
        seen = []

        def run(args, **kw):
            seen.append(args[-1])
            Path(args[args.index("-w") + 1]).write_bytes(tiny_wav())

        with mock.patch.object(self.s, "tts_engine", return_value="espeak"), \
                mock.patch.object(self.s, "voice_preset", return_value={"pitch": 1.0}), \
                mock.patch.object(self.s.shutil, "which", return_value="/usr/bin/espeak-ng"), \
                mock.patch.object(self.s.subprocess, "run", side_effect=run):
            self.s.synth("I loved {fr:Les Jardins}.")
        self.assertEqual(seen, ["I loved Les Jardins."])

    def test_piper_gets_the_words(self):
        seen = []
        with mock.patch.object(self.s, "tts_engine", return_value="piper"), \
                mock.patch.object(self.s, "voice_preset", return_value={"piper": "x", "pitch": 1.0}), \
                mock.patch.dict(self.s.CFG, {"piper_voice": "/v/x.onnx"}), \
                mock.patch.object(self.s, "piper_voice", return_value="/v/x.onnx"), \
                mock.patch.object(self.s, "piper_inproc", side_effect=lambda m, t, s: seen.append(t) or tiny_wav()):
            self.s.synth("{de:Die Zauberflöte} tonight.")
        self.assertEqual(seen, ["Die Zauberflöte tonight."])


if __name__ == "__main__":
    unittest.main()
