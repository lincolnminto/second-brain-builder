"""Transcript rendering rules that the Canonical Documents of media Sources rely on."""

from second_brain_builder.conversion.transcript import (
    Frame,
    render_video_markdown,
    render_vtt,
)
from second_brain_builder.domain import Segment


def test_vtt_sorts_cues_drops_empty_ones_and_never_has_zero_length_cues():
    vtt = render_vtt(
        [
            Segment(start=3725.25, end=3730.0, text="Second   hour,\nnew line."),
            Segment(start=1.0, end=1.0, text="Instant."),
            Segment(start=0.5, end=0.9, text="   "),
        ]
    )

    assert vtt == (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:01.001\nInstant.\n\n"
        "01:02:05.250 --> 01:02:10.000\nSecond hour, new line.\n"
    )


def test_video_transcript_goes_under_the_last_frame_before_it():
    markdown = render_video_markdown(
        segments=[
            Segment(start=0.0, end=1.0, text="Before any frame."),
            Segment(start=6.0, end=7.0, text="During the second scene."),
            Segment(start=2.5, end=3.0, text="During the first scene."),
        ],
        frames=[
            Frame(timestamp=5.5, image_name="frame-0002.png", text_lines=[]),
            Frame(timestamp=2.0, image_name="frame-0001.png", text_lines=["run ``` code"]),
        ],
        vtt_name="talk.vtt",
    )

    assert markdown == (
        "Transcript with timings: [talk.vtt](assets/talk.vtt)\n\n"
        "**00:00:00** Before any frame.\n\n"
        "## 00:00:02\n\n"
        "![Frame at 00:00:02](assets/frame-0001.png)\n\n"
        "Text in the frame:\n\n"
        "````text\nrun ``` code\n````\n\n"
        "**00:00:02** During the first scene.\n\n"
        "## 00:00:05\n\n"
        "![Frame at 00:00:05](assets/frame-0002.png)\n\n"
        "**00:00:06** During the second scene.\n"
    )
