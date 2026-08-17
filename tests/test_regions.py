"""scale_box scaling math -- pure, no images/OCR."""
from maple_analyzer.regions import FIELD_BOXES, REFERENCE_CLIENT_SIZE, STAT_PANEL_BOX, scale_box


def test_identity_scale_at_reference_size():
    for box in [STAT_PANEL_BOX, *FIELD_BOXES.values()]:
        assert scale_box(box, REFERENCE_CLIENT_SIZE).as_tuple() == box


def test_scales_proportionally():
    ref_w, ref_h = REFERENCE_CLIENT_SIZE
    box = scale_box(STAT_PANEL_BOX, (ref_w * 2, ref_h * 2))
    assert box.as_tuple() == tuple(c * 2 for c in STAT_PANEL_BOX)


def test_field_boxes_stay_within_panel_at_reference_size():
    panel = scale_box(STAT_PANEL_BOX, REFERENCE_CLIENT_SIZE)
    for name, box in FIELD_BOXES.items():
        b = scale_box(box, REFERENCE_CLIENT_SIZE)
        assert panel.left <= b.left and b.right <= panel.right, name
        assert panel.top <= b.top and b.bottom <= panel.bottom, name


def test_scale_at_known_working_resolutions():
    # Confirmed working live per handover notes: 1366x768 and 1920x1080.
    for client_size in [(1366, 768), (1920, 1080)]:
        panel = scale_box(STAT_PANEL_BOX, client_size)
        assert panel.left < panel.right and panel.top < panel.bottom
        for box in FIELD_BOXES.values():
            b = scale_box(box, client_size)
            assert b.left < b.right and b.top < b.bottom
