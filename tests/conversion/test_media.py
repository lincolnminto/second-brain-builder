"""Audio and video: transcripts, WebVTT Assets, scene frames, and OCR."""

import shutil
import struct
import wave
from pathlib import Path

import pytest

from second_brain_builder.conversion import ConversionError, ConversionOptions, convert_source
from second_brain_builder.conversion.media import transcribe_with_provider
from second_brain_builder.domain import Segment

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed",
)


def cue(start: float, end: float, text: str) -> Segment:
    return Segment(start=start, end=end, text=text)


class ScriptedTranscriber:
    """A Provider stand-in for ``domain.Transcriber``: returns fixed segments and records
    the audio it received."""

    def __init__(self, cues: list[Segment]) -> None:
        self.cues = cues
        self.received: list[tuple[int, int, float]] = []

    def transcribe(self, audio: Path, language: str | None) -> list[Segment]:
        with wave.open(str(audio), "rb") as reader:
            seconds = reader.getnframes() / reader.getframerate()
            self.received.append((reader.getframerate(), reader.getnchannels(), seconds))
        return self.cues


def _sine_mp3(synthetic, name: str, seconds: int) -> Path:
    path = synthetic.directory / name
    synthetic.ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:a", "libmp3lame", str(path),
    )  # fmt: skip
    return path


def _set_mp3_frame_count(path: Path, frames: int) -> None:
    """Overwrite the frame count in the Xing or Info header, which gives the duration."""
    data = bytearray(path.read_bytes())
    position = max(data.find(b"Xing"), data.find(b"Info"))
    assert position > 0
    (flags,) = struct.unpack(">I", data[position + 4 : position + 8])
    assert flags & 0x1, "the header has no frame count"
    data[position + 8 : position + 12] = struct.pack(">I", frames)
    path.write_bytes(bytes(data))


def _slide_video(synthetic, path, slides, *, with_audio=True):
    """A video with one still slide of large text per scene, four seconds each."""
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")
    font = font_module.load_default(size=56)
    inputs = []
    for index, (text, background, foreground) in enumerate(slides):
        image = image_module.new("RGB", (640, 360), background)
        draw_module.Draw(image).text((60, 150), text, fill=foreground, font=font)
        slide = synthetic.directory / f"slide{index}.png"
        image.save(slide)
        inputs += ["-loop", "1", "-t", "4", "-framerate", "10", "-i", str(slide)]
    seconds = 4 * len(slides)
    streams = "".join(f"[{index}:v]" for index in range(len(slides)))
    args = [*inputs]
    if with_audio:
        args += ["-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=300:sample_rate=16000"]
    args += [
        "-filter_complex",
        f"{streams}concat=n={len(slides)}:v=1:a=0,format=yuv420p[v]",
        "-map",
        "[v]",
        "-c:v",
        "mpeg4",
    ]
    if with_audio:
        args += ["-map", f"{len(slides)}:a", "-c:a", "aac", "-shortest"]
    synthetic.ffmpeg(*args, str(path))
    return path


@needs_ffmpeg
def test_audio_with_a_provider_transcriber(synthetic, tmp_path):
    source = synthetic.silent_wav("river-talk.wav", seconds=3.0, rate=44_100)
    transcriber = ScriptedTranscriber(
        [
            cue(0.4, 1.9, "Rivers carry silt."),
            cue(2.0, 2.6, "Floods spread it --> <wide> & far."),
            cue(5.5, 6.0, "After a pause, a new paragraph."),
        ]
    )
    assets_dir = tmp_path / "assets"
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=transcriber)

    result = convert_source(source, assets_dir, options)

    assert transcriber.received == [(16_000, 1, pytest.approx(3.0, abs=0.01))]
    assert result.assets == [assets_dir / "river-talk.vtt"]
    assert (assets_dir / "river-talk.vtt").read_text() == (
        "WEBVTT\n\n"
        "00:00:00.400 --> 00:00:01.900\nRivers carry silt.\n\n"
        "00:00:02.000 --> 00:00:02.600\nFloods spread it --&gt; &lt;wide&gt; &amp; far.\n\n"
        "00:00:05.500 --> 00:00:06.000\nAfter a pause, a new paragraph.\n"
    )
    assert result.markdown == (
        "Transcript with timings: [river-talk.vtt](assets/river-talk.vtt)\n\n"
        "**00:00:00** Rivers carry silt. Floods spread it --&gt; &lt;wide&gt; &amp; far.\n\n"
        "**00:00:05** After a pause, a new paragraph.\n"
    )
    assert result.metadata.duration_seconds == pytest.approx(3.0, abs=0.01)
    assert result.converter == "provider transcriber"
    assert result.warnings == []


@needs_ffmpeg
def test_mp3_tags_become_metadata(synthetic, tmp_path):
    source = synthetic.directory / "episode.mp3"
    synthetic.ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-metadata", "title=Tide Talk", "-metadata", "artist=Ada Example",
        "-c:a", "libmp3lame", str(source),
    )  # fmt: skip
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=ScriptedTranscriber([]))

    result = convert_source(source, tmp_path / "assets", options)

    assert result.metadata.title == "Tide Talk"
    assert result.metadata.author == "Ada Example"
    assert result.metadata.duration_seconds == pytest.approx(2.0, abs=0.1)
    assert "Speech recognition found no speech." in result.warnings
    assert (tmp_path / "assets" / "episode.vtt").read_text() == "WEBVTT\n"


@needs_ffmpeg
def test_header_duration_past_the_limit_is_refused_before_decoding(synthetic, tmp_path):
    # The header of this 5-second file says it lasts 56 million seconds (649 days).
    source = _sine_mp3(synthetic, "endless.mp3", seconds=5)
    _set_mp3_frame_count(source, 0x7FFFFFFF)
    transcriber = ScriptedTranscriber([cue(0.0, 1.0, "Never used.")])
    assets_dir = tmp_path / "assets"
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=transcriber)

    with pytest.raises(ConversionError, match=r"at most 24\.0 hours"):
        convert_source(source, assets_dir, options)
    assert transcriber.received == []
    assert not assets_dir.exists()


@needs_ffmpeg
def test_audio_past_the_limit_is_never_decoded(synthetic, tmp_path):
    # The header says 0.5 seconds, but the file holds 5 seconds of audio.
    source = _sine_mp3(synthetic, "longer.mp3", seconds=5)
    _set_mp3_frame_count(source, 20)
    transcriber = ScriptedTranscriber([cue(0.0, 1.0, "Tone.")])
    options = ConversionOptions(
        models_dir=tmp_path / "models", transcriber=transcriber, max_media_seconds=2
    )

    result = convert_source(source, tmp_path / "assets", options)

    assert transcriber.received == [(16_000, 1, pytest.approx(2.0, abs=0.05))]
    assert "Only the first 2 seconds were transcribed." in result.warnings[0]


@needs_ffmpeg
@pytest.mark.parametrize(
    ("name", "codec_args"),
    [
        ("tagged.aac", ["-c:a", "aac", "-f", "adts", "-write_id3v2", "1"]),
        ("tagged.mp3", ["-c:a", "libmp3lame"]),
    ],
)
def test_id3_tagged_audio_converts(synthetic, tmp_path, name, codec_args):
    source = synthetic.directory / name
    synthetic.ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-metadata", "title=Tide Talk", *codec_args, str(source),
    )  # fmt: skip
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=ScriptedTranscriber([]))

    result = convert_source(source, tmp_path / "assets", options)

    assert result.metadata.title == "Tide Talk"
    assert result.metadata.duration_seconds == pytest.approx(2.0, abs=0.1)


def test_provider_chunks_are_shifted_to_source_time(synthetic, tmp_path):
    wav = synthetic.silent_wav("long.wav", seconds=25.0)
    transcriber = ScriptedTranscriber([cue(1.0, 2.0, "Chunk speech.")])

    segments = transcribe_with_provider(wav, transcriber, tmp_path / "work", chunk_seconds=10)

    assert [seconds for _rate, _channels, seconds in transcriber.received] == [10.0, 10.0, 5.0]
    assert [(segment.start, segment.end) for segment in segments] == [
        (1.0, 2.0),
        (11.0, 12.0),
        (21.0, 22.0),
    ]


@pytest.mark.slow
@needs_ffmpeg
def test_video_frames_carry_ocr_text_under_timestamp_headings(synthetic, tmp_path):
    pytest.importorskip("docling", reason="Docling comes with the worker dependencies")
    pytest.importorskip("rapidocr", reason="RapidOCR comes with the worker dependencies")
    video = _slide_video(
        synthetic,
        synthetic.directory / "delta-lecture.mp4",
        [
            ("RIVER DELTA", (250, 250, 250), (0, 0, 0)),
            ("FLOOD PLAIN", (40, 40, 120), (255, 255, 255)),
            ("HARVEST TIME", (20, 120, 40), (255, 255, 255)),
        ],
    )
    transcriber = ScriptedTranscriber([cue(0.2, 1.0, "Welcome."), cue(9.0, 10.5, "Harvest.")])
    assets_dir = tmp_path / "assets"
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=transcriber)

    result = convert_source(video, assets_dir, options)

    names = sorted(asset.name for asset in result.assets)
    assert names == ["delta-lecture.vtt", "frame-0001.png", "frame-0002.png", "frame-0003.png"]
    sections = result.markdown.split("\n## ")
    assert len(sections) == 4
    assert "Welcome." in sections[0]
    for section, (text, frame) in zip(
        sections[1:],
        [("RIVER DELTA", 1), ("FLOOD PLAIN", 2), ("HARVEST TIME", 3)],
        strict=True,
    ):
        assert f"(assets/frame-{frame:04d}.png)" in section
        assert f"```text\n{text}\n```" in section
    assert "Harvest." in sections[3]
    assert result.metadata.duration_seconds == pytest.approx(12.0, abs=0.2)
    assert result.converter.startswith("provider transcriber, docling ")
    assert "rapidocr" in result.converter


@pytest.mark.slow
@needs_ffmpeg
def test_video_without_audio_still_gets_frames(synthetic, tmp_path):
    pytest.importorskip("docling", reason="Docling comes with the worker dependencies")
    pytest.importorskip("rapidocr", reason="RapidOCR comes with the worker dependencies")
    video = _slide_video(
        synthetic,
        synthetic.directory / "silent.mkv",
        [("NORTH", (250, 250, 250), (0, 0, 0)), ("SOUTH", (0, 0, 0), (250, 250, 250))],
        with_audio=False,
    )
    transcriber = ScriptedTranscriber([cue(0.0, 1.0, "Never used.")])
    options = ConversionOptions(models_dir=tmp_path / "models", transcriber=transcriber)

    result = convert_source(video, tmp_path / "assets", options)

    assert transcriber.received == []
    assert "The Source has no audio track." in result.warnings
    assert "```text\nNORTH\n```" in result.markdown
    assert "```text\nSOUTH\n```" in result.markdown


@pytest.mark.slow
@pytest.mark.timeout(1800)  # the first run downloads the Whisper checkpoint
@needs_ffmpeg
def test_local_whisper_downloads_into_the_models_dir(
    synthetic, tmp_path, model_cache, isolated_conversion
):
    pytest.importorskip("docling", reason="Docling comes with the worker dependencies")
    pytest.importorskip("whisper", reason="Whisper comes with the worker dependencies")
    source = synthetic.directory / "tone.flac"
    synthetic.ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "flac", str(source)
    )  # fmt: skip
    options = ConversionOptions(models_dir=model_cache, asr_model="tiny")

    result = isolated_conversion.convert(source, tmp_path / "assets", options)

    assert isolated_conversion.model_files_in_home() == []
    assert (model_cache / "whisper" / "tiny.pt").is_file()
    assert result.converter.endswith(", whisper tiny")
    assert result.converter.startswith("docling ")
    assert (tmp_path / "assets" / "tone.vtt").read_text().startswith("WEBVTT\n")
    assert result.markdown.startswith("Transcript with timings: [tone.vtt](assets/tone.vtt)")
    assert result.metadata.duration_seconds == pytest.approx(3.0, abs=0.1)
