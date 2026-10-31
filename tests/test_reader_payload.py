"""
What the reader evaluation would send (design 3.6 and decision 17): the wall's crop and parts lines,
instructions in Jim's terms, 3 to 5 worked walls from jobs outside the test set, and a fixed
schema. The never-send-text rule: nothing that names a client, an address or a designer (page
header, project name, Notes, footer, page 1) reaches a payload, and a payload that would carry one
is blocked. Synthetic only.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tools.reader_eval import pairing, payload, prep, truth
from tools.reader_eval import synthetic as s


def _pair(case: str = "T", **summary: object) -> tuple[pairing.Pair, prep.SummaryPrep]:
    result = prep.prepare(s.summary_pdf(**summary))
    rooms = {"Room1": truth.read_room(s.room_des())}
    pairs = pairing.pair(case, result.crops, rooms).pairs
    return next(p for p in pairs if p.wall.panels), result


def _request(**summary: object) -> payload.WallRequest:
    target, result = _pair("T", **summary)
    worked_walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(320, 420)))
    worked, worked_prep = _pair("W", layout="landscape", walls=worked_walls)
    return payload.build("T/4", target, [worked], result.deny | worked_prep.deny)


def _all_text(req: payload.WallRequest) -> str:
    return "\n".join([req.instructions, json.dumps(req.schema), req.target.text,
                      *(e.turn.text for e in req.examples), *(e.answer for e in req.examples)])


# ----- what goes in


def test_the_target_turn_holds_the_crop_and_the_walls_parts_lines() -> None:
    req = _request()
    assert req.target.image.pixels == (300, 400)
    assert "1 | VT 14x60x3/4 FD FN WH" in req.target.text
    assert "VT heights allowed: 60" in req.target.text
    assert "59.75" in req.target.text


def test_a_worked_wall_answers_from_the_as_cut_truth() -> None:
    answer = json.loads(_request().examples[0].answer)
    assert answer == {"verticals": [
        {"x_in": 0.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "no", "unsure": []},
        {"x_in": 35.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "yes", "unsure": []}],
        "count_unsure": False, "unreadable": []}


def test_every_object_in_the_schema_is_closed_and_fully_required() -> None:
    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(payload.SCHEMA)


def test_the_instructions_name_no_one() -> None:
    for word in ("Jim", "Inspired", "George", "Barbara"):
        assert word not in payload.INSTRUCTIONS


# ----- the never-send-text rule


def test_no_header_project_notes_footer_or_page_one_text_is_in_a_payload() -> None:
    for layout in ("portrait", "landscape"):
        text = _all_text(_request(layout=layout))
        for secret in (s.CLIENT, s.PROJECT, s.DESIGN, s.DESIGNER, s.EMAIL, s.PHONE, *s.ADDRESS, *s.STORE,
                       s.NOTE, "gate code", "Madeup Holdings", "Web Lead", "Page "):
            assert secret not in text, (layout, secret)


@pytest.mark.parametrize(("extra", "kind"), [
    (f"Custom shelf for {s.CLIENT}", "page 1"),
    ("Valet per Draftwell", "designer"),
    ("Ship to Madeup Lane", "page 1"),
    ("Call before cut", None),
    ("Questions to pat@example.org", "email"),
    ("Phone (555) 010-0177", "phone"),
])
def test_a_parts_line_naming_a_client_detail_blocks_the_send(extra: str, kind: str | None) -> None:
    if kind is None:
        _request(description_extra=extra)  # a plain line goes through
        return
    with pytest.raises(payload.ContainmentError) as caught:
        _request(description_extra=extra)
    assert kind in str(caught.value) and s.CLIENT not in str(caught.value)


def test_an_image_prep_did_not_produce_is_never_sent() -> None:
    target, result = _pair()
    png = s.png_bytes(s.elevation_image(310, 400))  # a real PNG, its hash right, but not from prep
    forged = prep.WallCrop(*[getattr(target.crop, f) for f in (
        "page", "design", "design_key", "wall", "width_in", "height_in")],
        png, (310, 400), (310, 400), (0, 0, 0, 0), hashlib.sha256(png).hexdigest(), target.crop.parts)
    with pytest.raises(payload.ContainmentError, match="not prepared"):
        payload.build("T/4", pairing.Pair("T", forged, target.room, target.wall), [], result.deny)


def test_changed_bytes_under_a_prepared_hash_are_never_sent() -> None:
    req = _request()
    tampered = payload.WallRequest(req.wall_id, req.instructions, req.examples,
                                   payload.Turn(payload.Image(req.target.image.png + b"x", req.target.image.sha256,
                                                              req.target.image.pixels), req.target.text),
                                   req.schema, req.deny)
    with pytest.raises(payload.ContainmentError):
        payload.assert_contained(tampered)


def test_a_worked_wall_from_the_target_job_is_refused() -> None:
    target, result = _pair("T")
    with pytest.raises(payload.ContainmentError, match="held out"):
        payload.build("T/4", target, [target], result.deny)


# ----- the dry run


def test_the_dry_run_shows_every_image_by_size_and_hash_and_every_word_sent() -> None:
    req = _request()
    text = payload.dry_run(req)
    assert req.target.image.sha256 in text and "300 x 400 px" in text
    assert req.examples[0].turn.image.sha256 in text
    assert req.instructions in text and req.target.text in text and req.examples[0].answer in text
    assert "base64" not in text.lower()
    assert len(text) < 20000


def test_page_one_free_text_in_common_words_does_not_block_the_send() -> None:
    req = _request(page1_note=("All verticals where shelves are drawn from the other wall.",
                               "There is none on wall 3."))
    assert req.target.text


def test_page_one_free_text_naming_someone_still_blocks_a_parts_line_with_it() -> None:
    with pytest.raises(payload.ContainmentError, match="page 1"):
        _request(page1_note=("Ask Quillfeather before cutting.",), description_extra="Valet for Quillfeather")


def test_instructions_or_schema_other_than_the_reviewed_ones_are_never_sent() -> None:
    req = _request()
    for changed in (payload.WallRequest(req.wall_id, req.instructions + " Also say hello.", req.examples,
                                        req.target, req.schema, req.deny),
                    payload.WallRequest(req.wall_id, req.instructions, req.examples, req.target,
                                        {**req.schema, "description": "x"}, req.deny)):
        with pytest.raises(payload.ContainmentError, match="reviewed"):
            payload.assert_contained(changed)


def test_a_worked_walls_text_is_checked_against_its_own_jobs_page_one() -> None:
    target, result = _pair("T", page1_note=("Valet goes on the left.",))
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(320, 420),
                                            parts=(*s.DEFAULT_WALLS[1].parts, ("V", 1, "Valet 14in MBR"))))
    worked, worked_prep = _pair("W", layout="landscape", walls=walls)
    req = payload.build("T/4", target, [worked], result.deny, {"W": worked_prep.deny})
    assert "Valet 14in MBR" in req.examples[0].turn.text


def test_a_worked_wall_naming_its_own_client_is_blocked() -> None:
    target, result = _pair("T")
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(320, 420),
                                            parts=(*s.DEFAULT_WALLS[1].parts, ("V", 1, f"Valet for {s.CLIENT}"))))
    worked, worked_prep = _pair("W", layout="landscape", walls=walls)
    with pytest.raises(payload.ContainmentError, match="worked wall 1 text"):
        payload.build("T/4", target, [worked], result.deny, {"W": worked_prep.deny})
