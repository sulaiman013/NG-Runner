"""
The reader evaluation's preparation (docs/p2/build-spec.md 5.7): each elevation page's raster at
native pixels, the wall's parts-list lines as text, and containment (design 3.6): no text span may
overlap a crop, and nothing that names a client, an address or a designer may reach what is sent.
Synthetic summaries only (tools/reader_eval/synthetic.py); the real ones are in
test_reader_real.py, marked fixtures.
"""
from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from tools.reader_eval import prep
from tools.reader_eval import synthetic as s


def _crops(**kwargs: object) -> prep.SummaryPrep:
    return prep.prepare(s.summary_pdf(**kwargs))


# ----- crops and parts lines


@pytest.mark.parametrize("layout", ["portrait", "landscape"])
def test_every_elevation_page_gives_its_raster_at_native_pixels(layout: str) -> None:
    result = _crops(layout=layout)
    assert [c.wall for c in result.crops] == ["1", "2"]
    assert result.refused == ()
    for crop in result.crops:
        img = Image.open(io.BytesIO(crop.png))
        assert img.size == crop.pixels == crop.native == (300, 400)
        assert crop.sha256 == hashlib.sha256(crop.png).hexdigest()
        assert crop.trim == (0, 0, 0, 0)


@pytest.mark.parametrize("layout", ["portrait", "landscape"])
def test_each_wall_gets_its_own_parts_lines(layout: str) -> None:
    result = _crops(layout=layout)
    wall1, wall2 = result.crops
    assert [p.as_text() for p in wall1.parts] == ["1 | DW Hook 4.9x12.2x3/4 FL WH"]
    assert sorted(p.as_text() for p in wall2.parts) == sorted([
        "1 | VT 14x60x3/4 FD FN WH", "1 | VT RAD 14x60x3/4 RH LN WH", "2 | SH 14x34.3x3/4 ADJ WH"])


def test_the_wall_size_comes_from_the_designs_wall_area_table() -> None:
    wall1, wall2 = _crops().crops
    assert (wall1.width_in, wall1.height_in) == (42.375, 108.0)
    assert (wall2.width_in, wall2.height_in) == (59.75, 108.0)


def test_a_wrapped_description_joins_the_line_above() -> None:
    walls = (s.WallSpec("2", '59 3/4"', parts=(("VT-WH", 1, "VT 14x60x3/4 FD"),)),)
    crop, = _crops(walls=walls, wrap_last_description="FN WH").crops
    assert [p.as_text() for p in crop.parts] == ["1 | VT 14x60x3/4 FD FN WH"]


def test_a_table_carried_onto_the_next_page_keeps_every_line() -> None:
    walls = (s.WallSpec("2", '59 3/4"'),)
    crop, = _crops(walls=walls, split_table=True).crops
    assert len(crop.parts) == 3


def test_vertical_lines_give_the_allowed_heights_and_the_count() -> None:
    wall1, wall2 = _crops().crops
    assert wall1.vt_count == 0 and wall1.vt_heights == ()
    assert wall2.vt_count == 2 and wall2.vt_heights == ("60",)
    assert wall2.vt_rad_count == 1


def test_the_design_is_kept_as_a_key_hash_never_its_name() -> None:
    crop = _crops().crops[1]
    assert crop.design_key == prep.name_key_hash(s.DESIGN)
    assert crop.design_key == prep.name_key_hash("testerson-PRIMARY")  # case, spaces, punctuation
    assert s.CLIENT.casefold() not in crop.design_key


def test_a_crop_repr_holds_no_pixels_and_no_names() -> None:
    text = repr(_crops().crops[1])
    assert len(text) < 600
    for secret in (s.CLIENT, s.PROJECT, s.DESIGNER, s.EMAIL):
        assert secret not in text


# ----- containment: no text span over a crop (design 3.6)


def test_text_inside_the_drawing_refuses_that_page() -> None:
    result = _crops(text_over_drawing="Outlet")
    assert [c.wall for c in result.crops] == ["1"]
    assert [r.code for r in result.refused] == ["text_over_drawing"]


def test_text_rotated_into_the_drawing_refuses_that_page() -> None:
    result = _crops(text_over_drawing="Window", rotated_text_over_drawing=True)
    assert [r.code for r in result.refused] == ["text_over_drawing"]


def test_an_annotation_over_the_drawing_refuses_that_page() -> None:
    result = _crops(annotation_over_drawing=True)
    assert [r.code for r in result.refused] == ["annotation_over_drawing"]


def test_a_second_drawing_on_the_page_refuses_it() -> None:
    result = _crops(second_drawing=True)
    assert [r.code for r in result.refused] == ["several_drawings"]


def test_a_form_on_the_page_refuses_it() -> None:
    result = _crops(form_over_drawing=True)
    assert [r.code for r in result.refused] == ["form_on_page"]


def test_text_just_outside_the_drawing_is_not_an_overlap() -> None:
    drawing = prep.Box(208, 403, 404, 691)
    above = prep.text_box("Wall 2", 18, [1, 0, 0, 1, 60, 705], [1, 0, 0, 1, 0, 0])
    assert not above.overlaps(drawing)
    inside = prep.text_box("x", 8, [1, 0, 0, 1, 300, 500], [1, 0, 0, 1, 0, 0])
    assert inside.overlaps(drawing)


def test_a_text_box_is_wide_enough_for_any_glyph() -> None:
    box = prep.text_box("WWW", 10, [1, 0, 0, 1, 100, 100], [1, 0, 0, 1, 0, 0])
    assert box.x0 <= 100 and box.x1 >= 130 and box.y0 <= 97 and box.y1 >= 110


def test_a_rotated_text_box_turns_with_its_matrix() -> None:
    box = prep.text_box("ABCD", 10, [0, 1, -1, 0, 100, 100], [1, 0, 0, 1, 0, 0])
    assert box.y1 >= 140 and box.x0 <= 90


# ----- containment: nothing that names a client, an address or a designer


def test_no_header_project_name_notes_or_footer_reaches_the_parts_text() -> None:
    for layout in ("portrait", "landscape"):
        for crop in _crops(layout=layout).crops:
            text = "\n".join(p.as_text() for p in crop.parts)
            for secret in (s.CLIENT, s.PROJECT, "Design:", "Project:", s.NOTE, "gate code", "Notes",
                           *s.STORE, s.DESIGNER, s.EMAIL, s.PHONE, *s.ADDRESS, "Page "):
                assert secret not in text, (layout, crop.wall, secret)


def test_the_deny_list_holds_the_client_designer_address_and_footer() -> None:
    deny = _crops().deny
    for kind, text in (("page 1", s.CLIENT), ("page 1", "Madeup Lane"), ("page 1", "Sampletown"),
                       ("designer", "Draftwell"), ("project", s.PROJECT), ("footer", "Sampleville"),
                       ("footer", "Fakery Blvd")):
        assert kind in deny.hits(f"a line with {text} in it"), (kind, text)
    assert "email" in deny.hits("write to someone@example.net")
    assert "phone" in deny.hits("call (555) 010-0123")


def test_the_deny_list_leaves_a_parts_line_alone() -> None:
    result = _crops()
    for crop in result.crops:
        for part in crop.parts:
            assert result.deny.hits(part.as_text()) == ()


def test_the_deny_list_never_shows_its_values() -> None:
    deny = _crops().deny
    assert s.CLIENT not in repr(deny) and s.CLIENT not in str(deny)


# ----- fitting Claude's image budget by cropping, never by scaling


def test_visual_tokens_follow_the_28_pixel_patch_rule() -> None:
    assert prep.visual_tokens(1000, 1000) == 36 * 36
    assert prep.visual_tokens(2000, 2000) == 72 * 72


def test_an_image_within_budget_is_left_alone() -> None:
    img = s.elevation_image(1683, 2000)
    fitted, trim = prep.fit_to_budget(img)
    assert fitted.size == (1683, 2000) and trim == (0, 0, 0, 0)


def test_an_image_over_budget_loses_only_its_right_ruler() -> None:
    img = s.elevation_image(2000, 2000, ruler=220)
    fitted, trim = prep.fit_to_budget(img)
    assert prep.visual_tokens(*fitted.size) <= prep.MAX_VISUAL_TOKENS
    assert fitted.size[1] == 2000 and trim[0] == 0 and trim[2] > 0
    box_right = 2000 - 220 - 1
    assert fitted.size[0] > box_right  # the wall box is whole


def test_an_image_that_cannot_fit_without_cutting_the_wall_is_refused() -> None:
    img = s.elevation_image(2000, 2000, ruler=20)
    with pytest.raises(prep.TooLarge):
        prep.fit_to_budget(img)


def test_a_crop_carries_its_designs_whole_wall_area_table() -> None:
    wall1, wall2 = _crops().crops
    assert wall1.table == wall2.table == (("1", 42.375), ("2", 59.75))


def test_a_company_field_naming_the_store_does_not_make_page_one_a_footer() -> None:
    deny = _crops(company="Inspired Closets LA").deny
    assert deny.hits("Web Lead") == ("page 1",)  # LEAD SOURCE, printed below COMPANY


def test_words_in_the_ignore_set_never_hit() -> None:
    deny = _crops(page1_note=("All verticals where shelves are", f"Ask {s.CLIENT} first")).deny
    assert deny.hits("verticals", ignore=frozenset({"verticals"})) == ()
    assert "page 1" in deny.hits(s.CLIENT, ignore=frozenset({"verticals"}))
